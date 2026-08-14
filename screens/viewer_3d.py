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

        # FIX: the camera is now driven entirely through signal_bus /
        # hand_rotation (gesture control), so VTK's own built-in
        # mouse-driven camera style (click-drag to orbit, scroll to zoom)
        # is redundant. Worse, it stays active and can capture the real OS
        # mouse when a synthetic air-mouse click lands on this viewport,
        # which can "steal" the cursor from the rest of the app -- that's
        # what was freezing the cursor and blocking clicks on other
        # screens/buttons. Swapping in the no-op base interactor style
        # disables VTK's own mouse handling while leaving rendering and
        # our camera.azimuth/elevation calls completely untouched.
        # FIXED: vtkInteractorStyle() (base class) still leaks some mouse
        # events to VTK internals on certain VTK builds, so synthetic
        # air-mouse clicks (from pyautogui) could interrupt the camera state
        # or steal focus from the rest of the app.
        # vtkInteractorStyleUser() is the correct "null" style — it blocks
        # ALL default VTK mouse/keyboard handling while leaving our own
        # camera.azimuth / camera.elevation calls completely untouched.
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
        """Clamped camera rotation — ignored when viewer is not the active screen.
        Accepts 3 floats to match pyqtSignal(float, float, float) in signal_bus."""
        if not self.isVisible():
            return
        max_delta = 0.05
        delta_x = max(-max_delta, min(max_delta, delta_x))
        delta_y = max(-max_delta, min(max_delta, delta_y))

        azimuth_step = -delta_x * 250.0
        elevation_step = -delta_y * 250.0

        elevation_limit = 80.0
        proposed = self.elevation_angle + elevation_step
        if proposed > elevation_limit:
            elevation_step = elevation_limit - self.elevation_angle
        elif proposed < -elevation_limit:
            elevation_step = -elevation_limit - self.elevation_angle
        self.elevation_angle += elevation_step

        self.plotter.camera.azimuth += azimuth_step
        self.plotter.camera.elevation += elevation_step
        self.plotter.camera.up = (0.0, 0.0, 1.0)
        self.plotter.update()

    def zoom_camera(self, zoom_direction):
        if not self.isVisible():
            return
        if zoom_direction > 0:
            self.plotter.camera.zoom(1.03)
        elif zoom_direction < 0:
            self.plotter.camera.zoom(0.97)
        self.plotter.update()

    def set_tissue_melt(self, melt_factor):
        if not self.isVisible():
            return
        if abs(melt_factor - self.current_melt) < 0.05:
            return
        self.current_melt = melt_factor
        if self.skin_actor:
            new_opacity = max(0.0, min(1.0, 1.0 - melt_factor))
            self.skin_actor.GetProperty().SetOpacity(new_opacity)
            self.plotter.update()