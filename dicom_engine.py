# dicom_engine.py
"""
DICOM -> 3D mesh pipeline for Aegis-Touch.

Owns exactly one job: given a folder of .dcm slices, load them into a
volume and produce renderable PyVista meshes (bone + skin isosurfaces).
No camera, no gestures, no GUI -- those consume this module, they don't
live inside it. That split is what lets this get unit-tested on its own
and reused by both the Qt 3D viewer screen and (later) the gesture engine.
"""

import os
from pathlib import Path

import pydicom
import numpy as np
import pyvista as pv


class DicomVolume:
    """Loads a folder of DICOM slices into a spacing-correct 3D volume."""

    def __init__(self, folder_path: str):
        self.folder_path = folder_path
        self.volume_data: pv.ImageData = self._load(folder_path)

    def _load(self, folder_path: str) -> pv.ImageData:
        dcm_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".dcm")]
        if not dcm_files:
            raise FileNotFoundError(f"No .dcm files found in folder: {folder_path}")

        files = []
        skipped = []
        for f in dcm_files:
            try:
                dcm = pydicom.dcmread(os.path.join(folder_path, f))
                # These two tags are load-bearing for everything below --
                # sorting needs ImagePositionPatient, and the pixel math
                # needs pixel_array to actually exist. Fail loudly on a
                # specific bad file now instead of crashing later on a
                # confusing AttributeError with no filename attached.
                _ = dcm.ImagePositionPatient
                _ = dcm.pixel_array
                files.append(dcm)
            except Exception as e:
                skipped.append((f, str(e)))

        if skipped:
            print(f"[DicomVolume] Skipped {len(skipped)} unreadable/incomplete file(s):")
            for fname, reason in skipped:
                print(f"    - {fname}: {reason}")

        if not files:
            raise ValueError(
                f"Found {len(dcm_files)} .dcm file(s) in '{folder_path}', but none were "
                f"readable as valid CT/MRI slices (missing pixel data or position tags)."
            )

        # Slices can land on disk in any order (alphabetical filename order
        # is not guaranteed to match physical position) -- always sort by
        # the actual physical Z position from the DICOM header.
        files.sort(key=lambda x: float(x.ImagePositionPatient[2]))

        slice_shape = list(files[0].pixel_array.shape)
        slice_shape.append(len(files))
        volume3d = np.zeros(slice_shape, dtype=np.float32)

        for i, dcm in enumerate(files):
            if dcm.pixel_array.shape != tuple(slice_shape[:2]):
                raise ValueError(
                    f"Slice size mismatch: expected {slice_shape[:2]}, got "
                    f"{dcm.pixel_array.shape} in a file near index {i}. "
                    f"Slices from different series/resolutions may have been mixed together."
                )
            slope = getattr(dcm, "RescaleSlope", 1.0)
            intercept = getattr(dcm, "RescaleIntercept", 0.0)
            # Convert raw pixel values to real Hounsfield Units (CT) --
            # without this, isosurface thresholds like 400.0 for bone
            # are meaningless, since they're HU-calibrated constants.
            volume3d[:, :, i] = (dcm.pixel_array * slope) + intercept

        volume_data = pv.wrap(volume3d)

        spacing = getattr(files[0], "PixelSpacing", [1.0, 1.0])
        z_spacing = (
            abs(float(files[1].ImagePositionPatient[2]) - float(files[0].ImagePositionPatient[2]))
            if len(files) > 1 else 1.0
        )
        volume_data.spacing = (spacing[0], spacing[1], z_spacing)

        # Smooths slice-to-slice quantization noise before contouring,
        # otherwise isosurfaces come out visibly stair-stepped.
        volume_data = volume_data.gaussian_smooth(radius_factor=1.0)
        return volume_data

    def scalar_range(self) -> tuple[float, float]:
        return self.volume_data.get_data_range()


class MeshSet:
    """Bone + skin isosurfaces generated from a DicomVolume."""

    # Standard Hounsfield Unit thresholds. Bone is dense (high HU);
    # soft tissue/skin sits just above the air/background floor.
    BONE_ISOVALUE = 400.0
    SKIN_ISOVALUE = -100.0

    def _contour(self, isovalue: float, downsample: bool = False) -> pv.PolyData:
        source = self.volume.volume_data
        if downsample:
            # The skin/tissue isosurface catches almost the entire body
            # volume (unlike bone, which is sparse), so it generates far
            # more triangles before decimation ever gets to run -- that
            # was the ~30-45s bottleneck we measured. Shrinking the volume
            # first (via pv.ImageData.resample, roughly halving each
            # dimension = ~8x fewer voxels) cuts that cost dramatically,
            # and the resolution loss isn't visible on the skin surface,
            # which was already going to be decimated by 90% anyway.
            source = source.resample(0.5)

        # decimate() drops the given fraction of triangles while
        # preserving shape -- essential on Jetson-class hardware, where
        # a full-resolution isosurface can run into the tens of millions
        # of triangles and blow the GPU/VRAM budget.
        return source.contour(isosurfaces=[isovalue]).decimate(self.decimate_factor)

    def __init__(self, volume: DicomVolume, decimate_factor: float = 0.90):
        self.volume = volume
        self.decimate_factor = decimate_factor
        self.bone_mesh: pv.PolyData = self._contour(self.BONE_ISOVALUE)
        self.skin_mesh: pv.PolyData = self._contour(self.SKIN_ISOVALUE, downsample=True)

    def add_to_plotter(self, plotter: pv.Plotter):
        """Adds both meshes to an existing PyVista plotter and returns their actors."""
        bone_actor = plotter.add_mesh(self.bone_mesh, color="ivory", smooth_shading=True, specular=0.3)
        skin_actor = plotter.add_mesh(self.skin_mesh, color="pink", smooth_shading=True, opacity=1.0)
        return bone_actor, skin_actor


def build_meshes_from_folder(folder_path: str, decimate_factor: float = 0.90) -> MeshSet:
    """Convenience one-shot: DICOM folder -> ready-to-render MeshSet."""
    volume = DicomVolume(folder_path)
    return MeshSet(volume, decimate_factor=decimate_factor)


if __name__ == "__main__":
    # Manual smoke test: point this at the DICOM/ folder in this repo
    # and confirm it loads + contours without errors, off-screen (no
    # display needed, so this also works over SSH / in CI).
    import sys
    import time

    folder = sys.argv[1] if len(sys.argv) > 1 else "DICOM"
    print(f"Loading DICOM volume from '{folder}'...")
    t0 = time.time()
    meshes = build_meshes_from_folder(folder)
    print(f"Done in {time.time() - t0:.2f}s")
    print(f"Bone mesh:  {meshes.bone_mesh.n_points} points, {meshes.bone_mesh.n_cells} cells")
    print(f"Skin mesh:  {meshes.skin_mesh.n_points} points, {meshes.skin_mesh.n_cells} cells")
    print(f"Volume scalar range: {meshes.volume.scalar_range()}")