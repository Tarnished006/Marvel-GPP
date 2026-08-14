import os
import pydicom
import numpy as np
import pyvista as pv
import vtk
from pyvistaqt import QtInteractor
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from signal_bus import signal_bus

class Viewer3D(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)

        # Embed PyVista Interactive Interactor into PyQt layout
        self.plotter = QtInteractor(self)
        self.layout.addWidget(self.plotter.interactor)

        # vtkInteractorStyleUser() is the "null" style — it blocks ALL
        # default VTK mouse/keyboard handling while leaving our own
        # camera.Azimuth / camera.Elevation calls completely untouched.
        # This keeps a synthetic air-mouse click from ever being captured
        # by VTK's own camera controls.
        self.plotter.iren.interactor.SetInteractorStyle(vtk.vtkInteractorStyleUser())

        self.elevation_angle = 0.0
        self.current_melt = 0.0
        self.bone_actor = None
        self.skin_actor = None
        self.bone_mesh = None
        self.skin_mesh = None

        # Bind to Signal Bus events
        signal_bus.hand_rotation.connect(self.rotate_camera)
        signal_bus.zoom_command.connect(self.zoom_camera)
        signal_bus.tissue_melt.connect(self.set_tissue_melt)

        # Default path fallback
        default_dir = r"C:\Users\Tharun R Gowda\Desktop\GPP\DICOM"
        if os.path.exists(default_dir):
            self.load_dicom(default_dir)

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

    def load_scan(self, patient: dict, scan: dict):
        """Triggered from ScanGallery to load a custom patient scan."""
        scan_path = scan.get("path", r"C:\Users\Tharun R Gowda\Desktop\GPP\DICOM")
        if os.path.exists(scan_path):
            self.load_dicom(scan_path)

    def rotate_camera(self, delta_x: float, delta_y: float, delta_z: float = 0.0):
        """
        Fluid, gimbal-safe 3D camera orbit using VTK's native camera
        Azimuth/Elevation methods — the only reliable way to orbit in VTK.

        camera.Azimuth(deg) rotates the camera around the focal point
        about the view-up axis.  camera.Elevation(deg) rotates up/down.
        OrthogonalizeViewUp() prevents gimbal lock by re-orthogonalising
        the view-up vector after each rotation.
        """
        if not self.isVisible():
            return

        az_deg = -delta_x * 180.0
        el_deg = -delta_y * 180.0

        # Clamp per-frame delta so a hand jerk never spins 180°
        az_deg = float(np.clip(az_deg, -12.0, 12.0))
        el_deg = float(np.clip(el_deg, -12.0, 12.0))

        # Elevation safety: keep camera between -80° and +80° from equator
        try:
            pos   = np.array(self.plotter.camera.position)
            focal = np.array(self.plotter.camera.focal_point)
            d     = pos - focal
            dist  = np.linalg.norm(d)
            if dist > 0:
                cur_el = float(np.degrees(np.arcsin(np.clip(d[2] / dist, -1.0, 1.0))))
                if cur_el + el_deg >  80.0:
                    el_deg = max(0.0,  80.0 - cur_el)
                elif cur_el + el_deg < -80.0:
                    el_deg = min(0.0, -80.0 - cur_el)
        except Exception:
            pass

        # VTK native orbit — these are DELTA methods, not absolute setters
        cam = self.plotter.camera
        cam.Azimuth(az_deg)
        cam.Elevation(el_deg)
        cam.OrthogonalizeViewUp()   # prevents gimbal lock accumulation
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