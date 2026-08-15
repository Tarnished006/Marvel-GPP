# screens/viewer_3d.py
"""
3D DICOM viewer screen for Aegis-Touch.

Architecture:
  - DicomLoader (QThread from dicom_engine) runs all heavy meshing off the
    UI thread. The viewer shows a status page while it loads, then switches
    to the live PyVista scene.
  - A LOCAL DATASETS sidebar lists every DICOM folder found next to the
    project root so you can switch scans without needing a patient in the DB.
  - Hand Gesture 3D Control:
      1. Open palm left/right movement → Azimuth orbit
      2. Open palm up/down movement → Elevation orbit
      3. Clockwise / Counter-clockwise wrist rotation & tilt → Camera Roll / Orbit
      4. Thumb+Index pinch ratio → Zoom In / Out
"""

import os
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
    _PAGE_SCENE   = 2   # live PyVista viewport

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

        # Wire gesture signals
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
        """Live 3D viewport driven purely by hand gestures, with info bar and scan sidebar."""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top info bar ──────────────────────────────────────────────────────
        top_row = QHBoxLayout()
        top_row.setContentsMargins(8, 4, 8, 4)
        top_row.setSpacing(6)

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

        # ── Content row: viewport + sidebar ──────────────────────────────────
        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(0)
        outer.addLayout(content_row)

        self.plotter = QtInteractor(page)
        self.plotter.set_background("#090909")
        content_row.addWidget(self.plotter.interactor, stretch=1)

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
            label, path, preset = LOCAL_DATASETS[0]
            self._start_load(path, preset=preset, label=label)

    # ── Loading pipeline ──────────────────────────────────────────────────────

    def _start_load(self, folder_path: str, preset: str = "skull", label: str = ""):
        """Cancel any running load, show progress page, start DicomLoader."""
        if self._loader and self._loader.isRunning():
            try:
                self._loader.finished.disconnect()
                self._loader.progress.disconnect()
                self._loader.failed.disconnect()
            except Exception:
                pass
            self._loader.terminate()
            self._loader.wait()

        self._active_path = folder_path
        self.scan_label.setText(label or os.path.basename(folder_path))
        self.status_label.setText("📂  Reading DICOM slices…")
        self.state_stack.setCurrentIndex(self._PAGE_LOADING)

        self._loader = DicomLoader(folder_path, preset=preset)
        self._loader.progress.connect(self.status_label.setText)
        self._loader.finished.connect(self._on_meshes_ready)
        self._loader.failed.connect(self._on_load_failed)
        self._loader.start()

    def _on_meshes_ready(self, meshset: MeshSet):
        """Runs on UI thread via Qt signal delivery."""
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
        self.plotter.reset_camera()
        self.plotter.render()

        self.info_bar.setText(
            f"  {self.scan_label.text()}  ·  "
            f"Bone {meshset.bone_mesh.n_points:,} pts  "
            f"{meshset.bone_mesh.n_cells:,} tris  ·  "
            f"HU={meshset.bone_isovalue:.0f}"
        )

        self._rebuild_sidebar(active_path=getattr(self, "_active_path", ""))
        self.state_stack.setCurrentIndex(self._PAGE_SCENE)

    def _on_load_failed(self, error_msg: str):
        self.status_label.setText(f"❌  Load failed:\n{error_msg}")
        print(f"[Viewer3D] DICOM load error: {error_msg}")

    # ── Public entry point ───────────────────────────────────────────────────

    def load_scan(self, patient: dict, scan: dict):
        """Load a specific patient scan from the DB-backed gallery."""
        label = (
            f"{patient.get('name', '')} ({patient.get('mrn', '')})"
            f" · {scan.get('type', 'CT')} · {scan.get('date', '')}"
        )

        folder = scan.get("file_path", "")
        preset = "skull" if "skull" in folder.lower() else "body"

        if folder and os.path.isdir(folder) and scan.get("slice_count", 1) > 1:
            self._start_load(folder, preset=preset, label=label)
        else:
            self.scan_label.setText(label)
            self.status_label.setText(
                f"⚠️  No renderable volume found at:\n{folder or '(no path)'}"
            )
            self.state_stack.setCurrentIndex(self._PAGE_LOADING)

    # ── Pure Gesture Camera Control ──────────────────────────────────────────

    def rotate_camera(self, delta_x: float, delta_y: float, delta_z: float = 0.0):
        """
        Fluid, gimbal-safe 3D camera orbit using VTK's native camera
        Azimuth/Elevation methods driven by open-palm gesture movement.
        """
        if not self.isVisible() or self.state_stack.currentIndex() != self._PAGE_SCENE:
            return

        az_deg = float(np.clip(-delta_x * 180.0, -12.0, 12.0))
        el_deg = float(np.clip(-delta_y * 180.0, -12.0, 12.0))

        # Elevation safety: keep camera between -80° and +80° from equator
        try:
            pos   = np.array(self.plotter.camera.position)
            focal = np.array(self.plotter.camera.focal_point)
            d     = pos - focal
            dist  = np.linalg.norm(d)
            if dist > 0:
                cur_el = float(np.degrees(np.arcsin(np.clip(d[2] / dist, -1.0, 1.0))))
                if cur_el + el_deg > 80.0:
                    el_deg = max(0.0, 80.0 - cur_el)
                elif cur_el + el_deg < -80.0:
                    el_deg = min(0.0, -80.0 - cur_el)
        except Exception:
            pass

        cam = self.plotter.camera
        cam.Azimuth(az_deg)
        cam.Elevation(el_deg)
        cam.OrthogonalizeViewUp()
        self.plotter.render()

    def zoom_camera(self, zoom_direction: int):
        if not self.isVisible() or self.state_stack.currentIndex() != self._PAGE_SCENE:
            return
        self.plotter.camera.zoom(1.04 if zoom_direction > 0 else 0.96)
        self.plotter.render()

    def set_tissue_melt(self, melt_factor: float):
        """No-op: skin mesh removed. Signal still connected to avoid errors."""
        pass