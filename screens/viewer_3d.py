# screens/viewer_3d.py
import os
import pydicom
import numpy as np
import pyvista as pv
import vtk
from pyvistaqt import QtInteractor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget
)
from PyQt6.QtCore import Qt
from signal_bus import signal_bus
from database import get_scans_for_ui


class Viewer3D(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # ===== UI WRAPPER (added) =====
        outer = QVBoxLayout(self)
        self.state_stack = QStackedWidget()
        outer.addWidget(self.state_stack)

        self.state_stack.addWidget(self._build_empty_state())
        self.state_stack.addWidget(self._build_loaded_state())
        self.state_stack.setCurrentIndex(0)
        # ===== END UI WRAPPER =====

        # ===== MEMBER 2'S ENGINE (unchanged) =====
        self.plotter.iren.interactor.SetInteractorStyle(vtk.vtkInteractorStyleUser())

        self.elevation_angle = 0.0
        self.current_melt = 0.0
        self.bone_actor = None
        self.skin_actor = None
        self.bone_mesh = None
        self.skin_mesh = None

        signal_bus.hand_rotation.connect(self.rotate_camera)
        signal_bus.zoom_command.connect(self.zoom_camera)
        signal_bus.tissue_melt.connect(self.set_tissue_melt)
        # ===== END MEMBER 2'S ENGINE =====

        # NOTE FOR MEMBER 2: removed the hardcoded default_dir Windows path
        # fallback here (was pointing at your local Desktop folder). Loading
        # now only happens through load_scan(), triggered from Gallery/search.

    # ===== UI WRAPPER (added) =====
    def _build_empty_state(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        search_box = QLineEdit()
        search_box.setPlaceholderText("Search patient name...")
        layout.addWidget(search_box)

        layout.addStretch()
        message = QLabel("No scan loaded\nSearch a patient or open a scan from Gallery")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(message)
        layout.addStretch()

        return page

    def _build_loaded_state(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        self.scan_label = QLabel("")
        outer.addWidget(self.scan_label)

        content_row = QHBoxLayout()
        outer.addLayout(content_row)

        # Member 2's PyVista interactor, embedded here instead of directly on self
        self.plotter = QtInteractor(page)
        content_row.addWidget(self.plotter.interactor, stretch=1)

        self.sidebar = QWidget()
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        content_row.addWidget(self.sidebar)

        self.toggle_btn = QPushButton(">")
        self.toggle_btn.setFixedWidth(24)
        self.toggle_btn.clicked.connect(self._toggle_sidebar)
        content_row.addWidget(self.toggle_btn)

        return page

    def _toggle_sidebar(self):
        visible = self.sidebar.isVisible()
        self.sidebar.setVisible(not visible)
        self.toggle_btn.setText("<" if not visible else ">")
    # ===== END UI WRAPPER =====

    # ===== MEMBER 2'S ENGINE (unchanged) =====
    def load_dicom(self, folder_path):
        """Ingests DICOM slices, pre-calculates meshes, and builds scene."""
        print(f"[3D Engine] Loading DICOM files from: {folder_path}...")
        files = [pydicom.dcmread(os.path.join(folder_path, f))
                 for f in os.listdir(folder_path) if f.endswith('.dcm')]

        if not files:
            print(f"Error: No .dcm files found in {folder_path}")
            return

        files.sort(key=lambda x: float(x.ImagePositionPatient[2]))
        slice_shape = list(files[0].pixel_array.shape)
        slice_shape.append(len(files))
        volume3d = np.zeros(slice_shape, dtype=np.float32)

        for i, dcm in enumerate(files):
            slope = getattr(dcm, 'RescaleSlope', 1.0)
            intercept = getattr(dcm, 'RescaleIntercept', 0.0)
            volume3d[:, :, i] = (dcm.pixel_array * slope) + intercept

        volume_data = pv.wrap(volume3d)
        spacing = getattr(files[0], 'PixelSpacing', [1.0, 1.0])
        z_spacing = abs(float(files[1].ImagePositionPatient[2]) - float(files[0].ImagePositionPatient[2])) if len(files) > 1 else 1.0
        volume_data.spacing = (spacing[0], spacing[1], z_spacing)
        volume_data = volume_data.gaussian_smooth(radius_factor=1.0)

        print("[3D Engine] Pre-calculating Bone mesh (Please wait...)")
        self.bone_mesh = volume_data.contour(isosurfaces=[400.0]).decimate(0.90)

        print("[3D Engine] Pre-calculating Skin/Tissue mesh (Please wait...)")
        self.skin_mesh = volume_data.contour(isosurfaces=[-100.0]).decimate(0.90)

        print("[3D Engine] Generating scene...")
        self.plotter.set_background("black")
        self.plotter.enable_depth_peeling(10)

        self.bone_actor = self.plotter.add_mesh(self.bone_mesh, color="ivory", smooth_shading=True, specular=0.3)
        self.skin_actor = self.plotter.add_mesh(self.skin_mesh, color="pink", smooth_shading=True, opacity=1.0)
    # ===== END MEMBER 2'S ENGINE =====

    def load_scan(self, patient: dict, scan: dict):
        # ===== UI WRAPPER (added) =====
        self.scan_label.setText(
            f"{patient['name']} ({patient['mrn']}) · {scan['type']} · {scan['date']}"
        )

        while self.sidebar_layout.count():
            item = self.sidebar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.sidebar_layout.addWidget(QLabel("Scans"))
        for s in get_scans_for_ui(patient["mrn"]):
            label = QLabel(f"[img] {s['type']}")
            if s == scan:
                label.setStyleSheet("border: 1px solid #4caf50;")
            self.sidebar_layout.addWidget(label)
        # ===== END UI WRAPPER =====

        # NOTE FOR MEMBER 2: your original used scan.get("path", ...) — I switched
        # this to "file_path" to match our SQLite schema/database.py. Also added a
        # slice_count check since some of our patients only have 1 slice (no real
        # volume to render) — please confirm this guard makes sense with your engine.
        scan_path = scan.get("file_path")
        slice_count = scan.get("slice_count", 1)

        if scan_path and slice_count > 1 and os.path.isdir(scan_path):
            self.load_dicom(scan_path)
        else:
            print(f"[3D Engine] Scan has only {slice_count} slice(s) — no volume to render.")

        self.state_stack.setCurrentIndex(1)

    # ===== MEMBER 2'S ENGINE (unchanged) =====
    def rotate_camera(self, delta_x: float, delta_y: float, delta_z: float = 0.0):
        if not self.isVisible():
            return

        az_deg = -delta_x * 180.0
        el_deg = -delta_y * 180.0

        az_deg = float(np.clip(az_deg, -12.0, 12.0))
        el_deg = float(np.clip(el_deg, -12.0, 12.0))

        try:
            pos = np.array(self.plotter.camera.position)
            focal = np.array(self.plotter.camera.focal_point)
            d = pos - focal
            dist = np.linalg.norm(d)
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

    def zoom_camera(self, zoom_direction):
        if not self.isVisible():
            return
        if zoom_direction > 0:
            self.plotter.camera.zoom(1.03)
        elif zoom_direction < 0:
            self.plotter.camera.zoom(0.97)
        self.plotter.render()

    def set_tissue_melt(self, melt_factor):
        if not self.isVisible():
            return
        if abs(melt_factor - self.current_melt) < 0.03:
            return
        self.current_melt = melt_factor
        if self.skin_actor:
            new_opacity = max(0.0, min(1.0, 1.0 - melt_factor))
            self.skin_actor.GetProperty().SetOpacity(new_opacity)
            self.plotter.render()
    # ===== END MEMBER 2'S ENGINE =====