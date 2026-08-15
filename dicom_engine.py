# dicom_engine.py
"""
DICOM -> 3D mesh pipeline for Aegis-Touch.

Owns exactly one job: given a folder of .dcm slices, produce a bone-only
renderable PyVista mesh.  No skin surface is generated — it obscures anatomy.

Jetson Nano optimisation
────────────────────────
JETSON_OPTIMIZED = True  activates:
  1. Volume pre-downsampled to 50 % (0.5×) — ~8× fewer voxels, preserves
     thin skull structures better than 0.4×.
  2. gaussian_smooth skipped (saves 10-20 s, not visible after decimation).
  3. Decimation raised to 0.88 (keep 12 % of triangles, vs 10 % on desktop).
     12 % gives clean continuous surfaces without holes on thin bone.
  4. clean() + extract_largest() called after decimation to remove floating
     fragments and degenerate triangles that cause the "broken" look.
"""

import os

import pydicom
import numpy as np
import pyvista as pv
from PyQt6.QtCore import QThread, pyqtSignal


# ── Jetson Nano flag ──────────────────────────────────────────────────────────
# Set False on a workstation for full-resolution rendering.
JETSON_OPTIMIZED: bool = True

# ── Volume pre-downsample factors ─────────────────────────────────────────────
# 0.50 on Jetson keeps thin parietal / orbital bone intact.
# 0.40 was too aggressive — thin structures vanished completely.
_VOL_RESAMPLE   = 0.50 if JETSON_OPTIMIZED else 1.0

# ── Decimation: fraction of triangles to REMOVE ───────────────────────────────
# 0.88 → keep 12 % → ~50–80k triangles for a skull volume, no visible holes.
# 0.82 → keep 18 % → desktop quality (more detail, same structure).
_DECIMATE       = 0.88 if JETSON_OPTIMIZED else 0.82

# ── HU thresholds per scan type ───────────────────────────────────────────────
# skull: 250 HU catches the complete calvarium including thinner parietal and
#        sphenoid wings that disappear at 300+ HU.
# body:  400 HU for dense cortical bone throughout the torso.
PRESETS: dict = {
    "skull": {"bone": 250.0},
    "body":  {"bone": 400.0},
}

# ── Natural bone colour ───────────────────────────────────────────────────────
# #e8c87a  — warm golden-ivory, close to real dried cortical bone.
# ambient=0.45 prevents the shadowed hemisphere going pitch-black on Jetson
# where we only use two lights.  diffuse=0.70 gives gentle depth shading.
# specular=0.12 / specular_power=8  → very mild sheen (bone is matte, not shiny).
_BONE_COLOUR        = "#e8c87a"
_BONE_AMBIENT       = 0.45
_BONE_DIFFUSE       = 0.70
_BONE_SPECULAR      = 0.12
_BONE_SPECULAR_PWR  = 8


class DicomVolume:
    """Loads a folder of DICOM slices into a spacing-correct 3-D volume."""

    def __init__(self, folder_path: str):
        self.folder_path = folder_path
        self.volume_data: pv.ImageData = self._load(folder_path)

    def _load(self, folder_path: str) -> pv.ImageData:
        dcm_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".dcm")]
        if not dcm_files:
            raise FileNotFoundError(f"No .dcm files found in: {folder_path}")

        files, skipped = [], []
        for f in dcm_files:
            try:
                dcm = pydicom.dcmread(os.path.join(folder_path, f))
                _ = dcm.ImagePositionPatient
                _ = dcm.pixel_array
                files.append(dcm)
            except Exception as exc:
                skipped.append((f, str(exc)))

        if skipped:
            print(f"[DicomVolume] Skipped {len(skipped)} unreadable file(s):")
            for fname, reason in skipped:
                print(f"    - {fname}: {reason}")

        if not files:
            raise ValueError(
                f"No readable CT slices found in '{folder_path}'.  "
                f"({len(dcm_files)} .dcm files present but none had pixel data + position tags.)"
            )

        # Sort by physical Z — filename order is NOT reliable.
        files.sort(key=lambda x: float(x.ImagePositionPatient[2]))

        slice_shape = list(files[0].pixel_array.shape) + [len(files)]
        volume3d    = np.zeros(slice_shape, dtype=np.float32)

        for i, dcm in enumerate(files):
            if dcm.pixel_array.shape != tuple(slice_shape[:2]):
                raise ValueError(
                    f"Slice size mismatch at index {i}: "
                    f"expected {slice_shape[:2]}, got {dcm.pixel_array.shape}."
                )
            slope     = float(getattr(dcm, "RescaleSlope",     1.0))
            intercept = float(getattr(dcm, "RescaleIntercept", 0.0))
            # Hounsfield Unit conversion — must happen BEFORE thresholding.
            volume3d[:, :, i] = dcm.pixel_array * slope + intercept

        vol = pv.wrap(volume3d)

        spacing = getattr(files[0], "PixelSpacing", [1.0, 1.0])
        z_space = (
            abs(float(files[1].ImagePositionPatient[2]) - float(files[0].ImagePositionPatient[2]))
            if len(files) > 1 else 1.0
        )
        vol.spacing = (float(spacing[0]), float(spacing[1]), z_space)

        # Pre-downsample the volume before contouring.
        # 0.50× reduces voxel count by ~8× while keeping thin bone intact.
        # We do this BEFORE gaussian_smooth — smoothing a small volume is cheap
        # but on Jetson we skip smoothing entirely (see below).
        if JETSON_OPTIMIZED:
            vol = vol.resample(_VOL_RESAMPLE)

        # Gaussian smooth reduces stair-step artefacts.
        # Skipped on Jetson: costs 10-20 s and the benefit is invisible after
        # 88 % decimation.  Enabled on desktop for best visual quality.
        if not JETSON_OPTIMIZED:
            vol = vol.gaussian_smooth(radius_factor=1.0)

        return vol

    def scalar_range(self) -> tuple:
        return self.volume_data.get_data_range()


class MeshSet:
    """
    Bone-only isosurface generated from a DicomVolume.

    No skin mesh is generated — it obscures anatomy and is expensive to compute.
    The tissue_melt gesture signal is still connected in the viewer but becomes
    a no-op.
    """

    def _build_bone(self, isovalue: float) -> pv.PolyData:
        mesh = self.volume.volume_data.contour(isosurfaces=[isovalue])

        if mesh.n_cells == 0:
            raise ValueError(
                f"Bone isovalue {isovalue} HU produced an empty mesh.  "
                f"Volume HU range is {self.volume.scalar_range()} — "
                f"try a lower isovalue."
            )

        # Decimate: removes the given fraction of triangles while preserving shape.
        # _DECIMATE=0.88 keeps 12 % → ~50-80k triangles, no visible surface holes.
        mesh = mesh.decimate(_DECIMATE)

        # clean() merges coincident vertices and removes degenerate faces.
        # Without this, decimation can leave disconnected edge-case triangles
        # that show up as bright spikes or dark pits in the render.
        mesh = mesh.clean()

        # extract_largest() keeps only the single largest connected component.
        # This removes floating bone fragments (e.g. stray noise voxels that
        # survived the contour step) that appear as distracting specks.
        mesh = mesh.extract_largest()

        return mesh

    def __init__(
        self,
        volume: "DicomVolume",
        preset: str = "body",
    ):
        self.volume        = volume
        p                  = PRESETS.get(preset, PRESETS["body"])
        self.bone_isovalue = p["bone"]
        self.bone_mesh: pv.PolyData = self._build_bone(self.bone_isovalue)

    def add_to_plotter(self, plotter: pv.Plotter):
        """
        Adds the bone mesh to an existing plotter.

        Colour rationale
        ────────────────
        #e8c87a  — warm golden-ivory, close to real dried cortical bone.
        Not pure white: VTK renders white geometry as flat mid-grey under
        default lighting because the diffuse component washes out the hue.
        A warm hue makes features (sutures, foramina, orbital rims) pop
        against the dark background without needing post-processing.

        ambient=0.45 ensures the unlit hemisphere stays warm and visible
        rather than going pitch-black, which makes the skull look hollow.
        """
        bone_actor = plotter.add_mesh(
            self.bone_mesh,
            color=_BONE_COLOUR,
            smooth_shading=True,
            ambient=_BONE_AMBIENT,
            diffuse=_BONE_DIFFUSE,
            specular=_BONE_SPECULAR,
            specular_power=_BONE_SPECULAR_PWR,
            opacity=1.0,
        )
        return bone_actor, None   # (bone_actor, skin_actor) — skin always None now


def build_meshes_from_folder(
    folder_path: str,
    preset: str = "body",
) -> "MeshSet":
    """Convenience one-shot: DICOM folder -> ready-to-render MeshSet."""
    volume = DicomVolume(folder_path)
    return MeshSet(volume, preset=preset)


# ── Non-blocking background loader ────────────────────────────────────────────
class DicomLoader(QThread):
    progress = pyqtSignal(str)    # step description shown in the status label
    finished = pyqtSignal(object) # emits completed MeshSet
    failed   = pyqtSignal(str)    # emits error string on failure

    def __init__(self, folder_path: str, preset: str = "body"):
        super().__init__()
        self.folder_path = folder_path
        self.preset      = preset

    def run(self):
        try:
            mode = "Jetson-optimised" if JETSON_OPTIMIZED else "full-quality"
            self.progress.emit(f"📂  Reading DICOM slices  [{mode}]…")

            volume = DicomVolume(self.folder_path)
            lo, hi = volume.scalar_range()
            dims   = volume.volume_data.dimensions
            self.progress.emit(
                f"✅  Volume ready\n"
                f"    {dims[0]}×{dims[1]}×{dims[2]} voxels  ·  "
                f"HU {lo:.0f} → {hi:.0f}\n\n"
                f"🦴  Generating bone mesh…"
            )

            meshset = MeshSet.__new__(MeshSet)
            meshset.volume        = volume
            meshset.bone_isovalue = PRESETS.get(self.preset, PRESETS["body"])["bone"]

            meshset.bone_mesh = meshset._build_bone(meshset.bone_isovalue)
            self.progress.emit(
                f"✅  Bone mesh ready\n"
                f"    {meshset.bone_mesh.n_points:,} vertices  ·  "
                f"    {meshset.bone_mesh.n_cells:,} triangles\n\n"
                f"🖥  Uploading to GPU…"
            )

            self.finished.emit(meshset)

        except Exception as exc:
            import traceback
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")


if __name__ == "__main__":
    import sys, time
    folder = sys.argv[1] if len(sys.argv) > 1 else "DICOM"
    preset = sys.argv[2] if len(sys.argv) > 2 else "body"
    print(f"Loading '{folder}'  preset={preset}  JETSON_OPTIMIZED={JETSON_OPTIMIZED}")
    t0     = time.time()
    meshes = build_meshes_from_folder(folder, preset=preset)
    print(f"Done in {time.time() - t0:.1f}s")
    print(f"Bone: {meshes.bone_mesh.n_points:,} pts  {meshes.bone_mesh.n_cells:,} cells")
    print(f"HU range: {meshes.volume.scalar_range()}")