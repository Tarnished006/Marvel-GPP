# screens/viewer_3d.py
"""
3D DICOM viewer screen for Aegis-Touch.

Architecture:
  - DicomLoader (QThread from dicom_engine) runs all heavy meshing off the
    UI thread. The viewer shows a status page while it loads, then switches
    to the live scene.
  - Multi-Modal View Modes:
      1. 🧊 3D Surface Model: Realistic bone isosurface with 90° discrete snap turns.
      2. 📐 MPR Slices: Multi-Planar Reconstruction (Axial, Coronal, Sagittal) with
         synchronized crosshairs, clinical Window/Level presets, and calibrated calipers.
  - A LOCAL DATASETS sidebar lists every DICOM folder found next to the
    project root so you can switch scans without needing a patient in the DB.
  - Hand Gesture & Voice 3D Control:
      1. Open palm left/right movement → Azimuth orbit
      2. Open palm up/down movement → Elevation orbit
      3. Thumb+Index pinch ratio → Zoom In / Out
      4. Voice / Button 90° Anatomical Snap Turns (Anterior, Posterior, Lateral, Superior)
"""

import os
import time
import numpy as np
import pyvista as pv
import vtk
from pyvistaqt import QtInteractor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QSizePolicy, QScrollArea,
)
from PyQt6.QtCore import Qt
from signal_bus import signal_bus
from dicom_engine import DicomLoader, MeshSet
from screens.mpr_view import MPRView

try:
    from database import get_scans_for_ui
except Exception:
    def get_scans_for_ui(_mrn):
        return []

# ── Locate project-root DICOM folders ────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _discover_local_datasets():
    candidates = []
    for name in ("skull", "DICOM"):
        path = os.path.join(_ROOT, name)
        if os.path.isdir(path):
            dcm_count = sum(1 for f in os.listdir(path) if f.lower().endswith(".dcm"))
            if dcm_count > 1:
                preset = "skull" if "skull" in name.lower() else "body"
                label  = f"{name}  ({dcm_count} slices)"
                candidates.append((label, path, preset))
    return candidates

LOCAL_DATASETS = _discover_local_datasets()


class Viewer3D(QWidget):
    # Page indices inside self.state_stack
    _PAGE_SELECT  = 0   # scan picker (shown when no scan is loaded)
    _PAGE_LOADING = 1   # progress text while DicomLoader runs
    _PAGE_SCENE   = 2   # live viewport & MPR stack

    def __init__(self, parent=None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.state_stack = QStackedWidget()
        outer.addWidget(self.state_stack)

        self.state_stack.addWidget(self._build_select_page())   # 0
        self.state_stack.addWidget(self._build_loading_page())  # 1
        self.state_stack.addWidget(self._build_scene_page())    # 2
        self.state_stack.setCurrentIndex(self._PAGE_SELECT)

        # Null VTK style: keeps our camera controls working while blocking
        # VTK's own mouse-drag-orbit so air-mouse clicks can't grab the camera.
        self.plotter.iren.interactor.SetInteractorStyle(vtk.vtkInteractorStyleUser())

        # Viewer state
        self.bone_actor         = None
        self._loader            = None
        self._initial_load_done = False   # lazy-load guard
        self._ghost_active      = False
        self.mesh_bounds        = None

        # Wire gesture & voice signals
        signal_bus.voice_command.connect(self.handle_voice_command)
        signal_bus.hand_rotation.connect(self.rotate_camera)
        signal_bus.zoom_command.connect(self.zoom_camera)
        signal_bus.tissue_melt.connect(self.set_tissue_melt)

    # ── Page builders ─────────────────────────────────────────────────────────

    def _build_select_page(self) -> QWidget:
        """Scan picker shown while no scan is loaded (or if loading fails)."""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(16)

        title = QLabel("Select a Scan to Render")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "color: #ccc; font-size: 16px; font-weight: 600; margin-bottom: 8px;"
        )
        v.addWidget(title)

        if not LOCAL_DATASETS:
            msg = QLabel("No local DICOM folders found.\nAdd a skull/ or DICOM/ folder to the project root.")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            msg.setStyleSheet("color: #555; font-size: 12px;")
            v.addWidget(msg)
            return page

        for display_name, folder_path, preset in LOCAL_DATASETS:
            btn = QPushButton(f"  🗂  {display_name}")
            btn.setFixedWidth(320)
            btn.setFixedHeight(48)
            btn.setStyleSheet(
                "QPushButton {"
                "  background: #1a1a1a; color: #ccc; border: 1px solid #333;"
                "  border-radius: 6px; font-size: 13px; text-align: left; padding-left: 14px;"
                "}"
                "QPushButton:hover { background: #252525; border-color: #555; color: #fff; }"
                "QPushButton:pressed { background: #111; }"
            )
            btn.clicked.connect(
                lambda _=False, p=folder_path, pr=preset, n=display_name:
                    self._start_load(p, preset=pr, label=n)
            )
            v.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("Or open a scan from the patient Gallery →")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #444; font-size: 10px; margin-top: 12px;")
        v.addWidget(hint)
        return page

    def _build_loading_page(self) -> QWidget:
        """Status/progress screen while DicomLoader thread runs."""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(12)

        self.scan_label = QLabel("")
        self.scan_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_label.setStyleSheet(
            "color: #bbb; font-size: 14px; font-weight: 700;"
        )
        v.addWidget(self.scan_label)

        self.status_label = QLabel("Initialising…")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "color: #666; font-size: 11px; padding: 0 60px; line-height: 1.6;"
        )
        v.addWidget(self.status_label)

        back = QPushButton("← Choose a different scan")
        back.setFixedWidth(220)
        back.setStyleSheet(
            "QPushButton { background: transparent; color: #444; border: none;"
            " font-size: 10px; margin-top: 20px; }"
            "QPushButton:hover { color: #888; }"
        )
        back.clicked.connect(lambda: self.state_stack.setCurrentIndex(self._PAGE_SELECT))
        v.addWidget(back, alignment=Qt.AlignmentFlag.AlignCenter)
        return page

    def _build_scene_page(self) -> QWidget:
        """Live 3D viewport and 2D MPR slices driven by hand gestures and voice."""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top info bar with View Mode Switcher ──────────────────────────────
        top_row = QHBoxLayout()
        top_row.setContentsMargins(8, 4, 8, 4)
        top_row.setSpacing(6)

        # View Mode switcher: [ 🧊 3D Volume ] [ 📐 MPR Slices ]
        self.btn_3d_mode = QPushButton("🧊 3D Volume")
        self.btn_3d_mode.setCheckable(True)
        self.btn_3d_mode.setChecked(True)
        self.btn_3d_mode.setFixedHeight(24)
        self.btn_3d_mode.setStyleSheet(
            "QPushButton { background: #1a1a1a; color: #888; border: 1px solid #333; "
            "border-radius: 4px; font-size: 10px; font-weight: 600; padding: 0 10px; }"
            "QPushButton:checked { background: #1a2a1a; color: #7cfc00; border-color: #3a5a3a; }"
        )
        self.btn_3d_mode.clicked.connect(lambda: self._switch_view_mode(0))

        self.btn_mpr_mode = QPushButton("📐 MPR Slices")
        self.btn_mpr_mode.setCheckable(True)
        self.btn_mpr_mode.setChecked(False)
        self.btn_mpr_mode.setFixedHeight(24)
        self.btn_mpr_mode.setStyleSheet(
            "QPushButton { background: #1a1a1a; color: #888; border: 1px solid #333; "
            "border-radius: 4px; font-size: 10px; font-weight: 600; padding: 0 10px; }"
            "QPushButton:checked { background: #1a2a1a; color: #7cfc00; border-color: #3a5a3a; }"
        )
        self.btn_mpr_mode.clicked.connect(lambda: self._switch_view_mode(1))

        top_row.addWidget(self.btn_3d_mode)
        top_row.addWidget(self.btn_mpr_mode)
        top_row.addSpacing(10)

        self.info_bar = QLabel("")
        self.info_bar.setStyleSheet("color: #777; font-size: 10px; font-weight: 500;")
        top_row.addWidget(self.info_bar, stretch=1)

        # Switch scan button
        switch_btn = QPushButton("↩ Switch Scan")
        switch_btn.setFixedHeight(24)
        switch_btn.setStyleSheet(
            "QPushButton { background: #1a1a1a; color: #888; border: 1px solid #333;"
            " border-radius: 4px; font-size: 10px; padding: 0 8px; }"
            "QPushButton:hover { color: #ccc; border-color: #555; }"
        )
        switch_btn.clicked.connect(
            lambda: self.state_stack.setCurrentIndex(self._PAGE_SELECT)
        )
        top_row.addWidget(switch_btn)

        info_bar_widget = QWidget()
        info_bar_widget.setStyleSheet("background: #0f0f0f; border-bottom: 1px solid #1a1a1a;")
        info_bar_widget.setLayout(top_row)
        outer.addWidget(info_bar_widget)

        # ── Content row: view stack (3D or MPR) + sidebar ────────────────────
        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(0)
        outer.addLayout(content_row)

        self.view_mode_stack = QStackedWidget()

        # 1. 3D View Container
        container_3d = QWidget()
        layout_3d = QVBoxLayout(container_3d)
        layout_3d.setContentsMargins(0, 0, 0, 0)
        layout_3d.setSpacing(0)

        # Snap views toolbar (discrete 90° anatomical turns)
        snap_bar = QHBoxLayout()
        snap_bar.setContentsMargins(8, 4, 8, 4)
        snap_bar.setSpacing(4)
        snap_lbl = QLabel("90° Snap:")
        snap_lbl.setStyleSheet("color: #555; font-size: 9px; font-weight: 600; text-transform: uppercase;")
        snap_bar.addWidget(snap_lbl)

        for name, cmd in [
            ("Anterior", "anterior"),
            ("Posterior", "posterior"),
            ("Left Lat", "left_lateral"),
            ("Right Lat", "right_lateral"),
            ("Superior", "superior"),
            ("Inferior", "inferior"),
            ("Reset", "reset"),
        ]:
            s_btn = QPushButton(name)
            s_btn.setFixedHeight(22)
            s_btn.setStyleSheet(
                "QPushButton { background: #141414; color: #888; border: 1px solid #282828; "
                "border-radius: 3px; font-size: 9px; padding: 0 6px; }"
                "QPushButton:hover { background: #222; color: #eee; border-color: #444; }"
                "QPushButton:pressed { background: #00e5ff; color: #000; font-weight: bold; border-color: #00e5ff; }"
            )
            s_btn.clicked.connect(lambda checked, c=cmd: self.snap_to_view(c))
            snap_bar.addWidget(s_btn)

        snap_bar.addSpacing(14)
        self.btn_ghost_plane = QPushButton("👁 3D Ghost Slice: OFF")
        self.btn_ghost_plane.setCheckable(True)
        self.btn_ghost_plane.setFixedHeight(22)
        self.btn_ghost_plane.setStyleSheet(
            "QPushButton { background: #141414; color: #888; border: 1px solid #282828; "
            "border-radius: 3px; font-size: 9px; padding: 0 8px; font-weight: 600; }"
            "QPushButton:checked { background: #00222a; color: #00e5ff; border: 1px solid #00b4d8; }"
            "QPushButton:hover { background: #222; color: #eee; }"
        )
        self.btn_ghost_plane.clicked.connect(self._toggle_ghost_plane)
        snap_bar.addWidget(self.btn_ghost_plane)

        snap_bar.addStretch()

        snap_bar_widget = QWidget()
        snap_bar_widget.setStyleSheet("background: #0b0b0b; border-bottom: 1px solid #181818;")
        snap_bar_widget.setLayout(snap_bar)
        layout_3d.addWidget(snap_bar_widget)

        self.plotter = QtInteractor(container_3d, auto_update=False)
        self.plotter.set_background("#090909")
        layout_3d.addWidget(self.plotter.interactor, stretch=1)

        self.view_mode_stack.addWidget(container_3d)  # Index 0: 3D

        # 2. 2D Multi-Planar Reconstruction (MPR) View
        self.mpr_view = MPRView()
        self.mpr_view.axial_changed.connect(self._on_mpr_axial_changed)
        self.view_mode_stack.addWidget(self.mpr_view)  # Index 1: MPR

        content_row.addWidget(self.view_mode_stack, stretch=1)

        # Sidebar: local dataset switcher
        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(168)
        self.sidebar.setStyleSheet("background: #0f0f0f; border-left: 1px solid #1e1e1e;")
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        self.sidebar_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.sidebar_layout.setContentsMargins(8, 10, 8, 10)
        self.sidebar_layout.setSpacing(6)
        self._rebuild_sidebar()
        content_row.addWidget(self.sidebar)

        self.toggle_btn = QPushButton("›")
        self.toggle_btn.setFixedWidth(18)
        self.toggle_btn.setStyleSheet(
            "QPushButton { background: #131313; color: #555; border: none; }"
            "QPushButton:hover { color: #aaa; }"
        )
        self.toggle_btn.clicked.connect(self._toggle_sidebar)
        content_row.addWidget(self.toggle_btn)

        return page

    def _switch_view_mode(self, idx: int):
        self.view_mode_stack.setCurrentIndex(idx)
        self.btn_3d_mode.setChecked(idx == 0)
        self.btn_mpr_mode.setChecked(idx == 1)

    def _toggle_sidebar(self):
        v = not self.sidebar.isVisible()
        self.sidebar.setVisible(v)
        self.toggle_btn.setText("‹" if v else "›")

    def _rebuild_sidebar(self, active_path: str = ""):
        """Populate sidebar with one button per local dataset."""
        while self.sidebar_layout.count():
            item = self.sidebar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        hdr = QLabel("LOCAL SCANS")
        hdr.setStyleSheet(
            "color: #444; font-size: 9px; font-weight: 700; letter-spacing: 1px;"
        )
        self.sidebar_layout.addWidget(hdr)

        for display_name, folder_path, preset in LOCAL_DATASETS:
            is_active = (folder_path == active_path)
            btn = QPushButton(display_name.split("  ")[0])
            btn.setFixedHeight(32)
            btn.setStyleSheet(
                f"QPushButton {{ background: {'#1e2e1e' if is_active else '#181818'};"
                f" color: {'#7cfc00' if is_active else '#888'};"
                " border-radius: 4px; font-size: 11px; text-align: left; padding-left: 8px; }}"
                "QPushButton:hover { background: #202020; color: #ccc; }"
            )
            btn.clicked.connect(
                lambda _=False, p=folder_path, pr=preset, n=display_name:
                    self._start_load(p, preset=pr, label=n)
            )
            self.sidebar_layout.addWidget(btn)

        self.sidebar_layout.addStretch()

    def showEvent(self, event):
        """Lazy-load the first local dataset the first time the viewer is shown."""
        super().showEvent(event)
        if not self._initial_load_done and LOCAL_DATASETS:
            self._initial_load_done = True
            first_name, first_path, first_preset = LOCAL_DATASETS[0]
            self._start_load(first_path, preset=first_preset, label=first_name)

    # ── Load pipeline ─────────────────────────────────────────────────────────

    def _start_load(self, folder_path: str, preset: str = "body", label: str = ""):
        """Cancel any running load, show progress screen, start background thread."""
        if self._loader and self._loader.isRunning():
            try:
                self._loader.finished.disconnect()
                self._loader.progress.disconnect()
                self._loader.failed.disconnect()
            except Exception:
                pass
            self._loader.requestInterruption()
            self._loader.wait(2000)

        self._active_path = folder_path
        self.scan_label.setText(label or os.path.basename(folder_path))
        self.status_label.setText("Reading DICOM slices…")
        self.state_stack.setCurrentIndex(self._PAGE_LOADING)

        self._loader = DicomLoader(folder_path, preset=preset)
        self._loader.progress.connect(self.status_label.setText)
        self._loader.finished.connect(self._on_meshes_ready)
        self._loader.failed.connect(self._on_load_failed)
        self._loader.start()

    def _on_meshes_ready(self, meshset: MeshSet):
        """Slot: called on UI thread when DicomLoader completes."""
        self.plotter.clear()

        # Warm key + cool fill lighting setup
        self.plotter.remove_all_lights()
        self.plotter.add_light(pv.Light(
            position=(1.0, -1.0, 1.5),
            focal_point=(0.0, 0.0, 0.0),
            intensity=0.90,
            color=(1.00, 0.97, 0.88),
            light_type="scene light",
        ))
        self.plotter.add_light(pv.Light(
            position=(-1.2, 1.0, -0.6),
            focal_point=(0.0, 0.0, 0.0),
            intensity=0.35,
            color=(0.75, 0.85, 1.00),
            light_type="scene light",
        ))

        self.bone_actor, _skin = meshset.add_to_plotter(self.plotter)
        self.mesh_bounds = meshset.bone_mesh.bounds
        self.plotter.reset_camera()
        self.plotter.render()

        # Enable targeted 3D-to-2D raycast snapping
        try:
            self.plotter.enable_point_picking(
                callback=self._on_3d_point_picked,
                show_message=False,
                show_point=True,
                color="#00e5ff",
                point_size=10,
                left_clicking=True
            )
        except Exception as exc:
            print(f"[Viewer3D] Point picking setup error: {exc}")

        self.info_bar.setText(
            f"  {self.scan_label.text()}  ·  "
            f"Bone {meshset.bone_mesh.n_points:,} pts  "
            f"{meshset.bone_mesh.n_cells:,} tris  ·  "
            f"HU={meshset.bone_isovalue:.0f}"
        )

        self._rebuild_sidebar(active_path=getattr(self, "_active_path", ""))
        self.state_stack.setCurrentIndex(self._PAGE_SCENE)

        # Initialize 2D MPR view from cached volume
        try:
            if hasattr(self, "_active_path") and self._active_path:
                self.mpr_view.load_scan_folder(self._active_path)
                if self._ghost_active:
                    self._update_ghost_plane(self.mpr_view.idx_z * self.mpr_view.slice_thickness)
        except Exception as exc:
            print(f"[Viewer3D] Warning: could not load MPR slices: {exc}")

    def _toggle_ghost_plane(self):
        self._ghost_active = self.btn_ghost_plane.isChecked()
        self.btn_ghost_plane.setText("👁 3D Ghost Slice: ON" if self._ghost_active else "👁 3D Ghost Slice: OFF")
        if self._ghost_active:
            z_mm = self.mpr_view.idx_z * self.mpr_view.slice_thickness
            self._update_ghost_plane(z_mm)
        else:
            try:
                self.plotter.remove_actor("ghost_plane")
                self.plotter.render()
            except Exception:
                pass

    def _update_ghost_plane(self, z_mm: float):
        """Renders single-quad transparent MPR cut plane floating inside 3D bone space."""
        if not getattr(self, "_ghost_active", False) or self.mesh_bounds is None:
            return
        try:
            xmin, xmax, ymin, ymax, zmin, zmax = self.mesh_bounds
            cx = (xmin + xmax) / 2.0
            cy = (ymin + ymax) / 2.0
            w = (xmax - xmin) * 1.15
            h = (ymax - ymin) * 1.15
            plane = pv.Plane(
                center=(cx, cy, float(z_mm)),
                direction=(0, 0, 1),
                i_size=max(10.0, w),
                j_size=max(10.0, h),
                i_resolution=1,
                j_resolution=1
            )
            self.plotter.add_mesh(
                plane,
                name="ghost_plane",
                color="#00e5ff",
                opacity=0.35,
                show_edges=True,
                edge_color="#00e5ff",
                line_width=2
            )
            self.plotter.render()
        except Exception as exc:
            print(f"[Viewer3D] Ghost plane error: {exc}")

    def _on_3d_point_picked(self, point):
        """Raycasts 3D surface click directly into MPR slices and synchronizes ghost plane."""
        if point is None:
            return
        x_mm, y_mm, z_mm = float(point[0]), float(point[1]), float(point[2])
        if hasattr(self, "mpr_view") and self.mpr_view:
            self.mpr_view.snap_to_volume_point(x_mm, y_mm, z_mm)
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(
                    f"  🎯 Snapped MPR to ({x_mm:.1f}, {y_mm:.1f}, {z_mm:.1f}) mm  ·  "
                    f"Slice {self.mpr_view.idx_z + 1}"
                )
        if getattr(self, "_ghost_active", False):
            self._update_ghost_plane(z_mm)

    def _on_mpr_axial_changed(self, z_mm: float):
        if getattr(self, "_ghost_active", False):
            self._update_ghost_plane(z_mm)

    def _on_load_failed(self, error_msg: str):
        self.status_label.setText(f"Load failed:\n{error_msg}")
        print(f"[Viewer3D] DICOM load error: {error_msg}")

    # ── Public entry point ───────────────────────────────────────────────────

    def load_scan(self, patient: dict, scan: dict):
        """Load a specific patient scan from the DB-backed gallery."""
        label = (
            f"{patient.get('name', 'Patient')} — "
            f"{scan.get('type', 'CT')} ({scan.get('slice_count', 1)} slices)"
        )
        folder = scan.get("file_path", "")
        preset = "skull" if "skull" in folder.lower() else "body"

        if folder and os.path.isdir(folder) and scan.get("slice_count", 1) > 1:
            self._start_load(folder, preset=preset, label=label)
        else:
            self.scan_label.setText(label)
            self.status_label.setText(
                f"No renderable volume found at:\n{folder or '(no path)'}"
            )
            self.state_stack.setCurrentIndex(self._PAGE_LOADING)

    # ── Discrete 90° Anatomical Snap Turns & Voice Routing ───────────────────

    def snap_to_view(self, command: str):
        """Discrete 90-degree anatomical camera snap turns."""
        cmd = command.lower().strip()
        cam = self.plotter.renderer.GetActiveCamera()
        focal = np.array(cam.GetFocalPoint(), dtype=float)
        pos = np.array(cam.GetPosition(), dtype=float)
        distance = float(np.linalg.norm(pos - focal))
        if distance <= 0:
            distance = 1.0

        positions = {
            "anterior":      (0, -distance, 0),
            "posterior":     (0, distance, 0),
            "left_lateral":  (distance, 0, 0),
            "lateral":       (distance, 0, 0),
            "right_lateral": (-distance, 0, 0),
            "top":           (0, 0, distance),
            "superior":      (0, 0, distance),
            "bottom":        (0, 0, -distance),
            "inferior":      (0, 0, -distance),
        }

        if cmd in ("reset", "reset view"):
            self.plotter.reset_camera()
            self.plotter.renderer.ResetCameraClippingRange()
            self.plotter.render()
            return

        if cmd in positions:
            new_pos = focal + np.array(positions[cmd], dtype=float)
            cam.SetPosition(*new_pos)
            cam.SetFocalPoint(*focal)

            if cmd in ("top", "superior"):
                cam.SetViewUp(0, 1, 0)
            elif cmd in ("bottom", "inferior"):
                cam.SetViewUp(0, -1, 0)
            else:
                cam.SetViewUp(0, 0, 1)

            cam.OrthogonalizeViewUp()
            self.plotter.renderer.ResetCameraClippingRange()
            self.plotter.render()

    def handle_voice_command(self, phrase: str):
        """Execute a recognized voice command on the active view."""
        command = " ".join(phrase.lower().strip().split())
        print(f"[Viewer3D] Received voice command: {command!r}", flush=True)

        if self.state_stack.currentIndex() != self._PAGE_SCENE:
            return

        # Discrete anatomical snap views
        self.snap_to_view(command)

    # ── Pure Gesture Camera Control ──────────────────────────────────────────

    def rotate_camera(self, delta_x: float, delta_y: float, delta_z: float = 0.0):
        """
        Fluid, gimbal-safe 3D camera orbit using VTK's native camera
        Azimuth/Elevation methods driven by open-palm gesture movement.
        """
        if not self.isVisible() or self.state_stack.currentIndex() != self._PAGE_SCENE:
            return

        # Only apply gesture rotation when in 3D mode
        if hasattr(self, "view_mode_stack") and self.view_mode_stack.currentIndex() != 0:
            return

        # Throttle continuous rotation renders to 40 FPS (~25ms) to prevent UI event queue starvation
        now = time.time()
        if hasattr(self, "_last_rot_time") and (now - self._last_rot_time < 0.025):
            return
        self._last_rot_time = now

        az_deg = float(np.clip(-delta_x * 180.0, -12.0, 12.0))
        el_deg = float(np.clip(-delta_y * 180.0, -12.0, 12.0))

        # Elevation safety: keep camera between -80° and +80° from equator
        try:
            pos   = np.array(self.plotter.camera.position)
            focal = np.array(self.plotter.camera.focal_point)
            vec   = pos - focal
            r     = np.linalg.norm(vec)
            if r > 0:
                current_el = np.degrees(np.arcsin(np.clip(vec[2] / r, -1.0, 1.0)))
                new_el     = current_el + el_deg
                if not (-80.0 <= new_el <= 80.0):
                    el_deg = 0.0
        except Exception:
            pass

        cam = self.plotter.renderer.GetActiveCamera()
        cam.Azimuth(az_deg)
        cam.Elevation(el_deg)
        cam.OrthogonalizeViewUp()
        self.plotter.renderer.ResetCameraClippingRange()
        self.plotter.render()

    def zoom_camera(self, direction: int):
        """
        Smooth, clamped Dolly zoom.
        direction = +1 (zoom in) | -1 (zoom out)
        """
        if not self.isVisible() or self.state_stack.currentIndex() != self._PAGE_SCENE:
            return

        if hasattr(self, "view_mode_stack") and self.view_mode_stack.currentIndex() != 0:
            return

        cam    = self.plotter.renderer.GetActiveCamera()
        factor = 1.05 if direction > 0 else (1.0 / 1.05)

        focal = np.array(cam.GetFocalPoint())
        pos   = np.array(cam.GetPosition())
        dist  = np.linalg.norm(pos - focal)

        if direction > 0 and dist < 15.0:
            return
        if direction < 0 and dist > 3000.0:
            return

        cam.Dolly(factor)
        self.plotter.render()

    def set_tissue_melt(self, melt_factor: float):
        """No-op: skin mesh removed. Signal still connected to avoid errors."""
        pass
