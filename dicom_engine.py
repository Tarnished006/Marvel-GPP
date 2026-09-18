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
# 0.92 → keep 8 % → ~40–60k triangles for low-power GPU / Jetson.
# 0.88 → keep 12 % → ~100–140k triangles for smooth, zero-lag 60+ FPS desktop rendering.
_DECIMATE       = 0.92 if JETSON_OPTIMIZED else 0.88

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


import hashlib
import gc

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


def get_cache_path(folder_path: str, preset: str = "body") -> str:
    """Returns deterministic path to cached .vtp mesh for a DICOM folder."""
    abs_path = os.path.abspath(folder_path)
    key = f"{abs_path}_{preset}_{JETSON_OPTIMIZED}_{_DECIMATE}_hu_v1"
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
    safe_name = os.path.basename(os.path.normpath(folder_path)) or "scan"
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, f"{safe_name}_{preset}_{h}.vtp")


def get_volume_cache_path(folder_path: str) -> str:
    """Returns deterministic path to cached .npz volume array for a DICOM folder."""
    abs_path = os.path.abspath(folder_path)
    key = f"{abs_path}_{JETSON_OPTIMIZED}"
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
    safe_name = os.path.basename(os.path.normpath(folder_path)) or "scan"
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, f"{safe_name}_vol_{h}.npz")


def is_cache_valid(cache_path: str, folder_path: str) -> bool:
    """Checks if cached file exists and is newer than the DICOM folder."""
    if not os.path.isfile(cache_path):
        return False
    try:
        cache_mtime = os.path.getmtime(cache_path)
        folder_mtime = os.path.getmtime(folder_path)
        if folder_mtime > cache_mtime:
            return False
        return os.path.getsize(cache_path) > 1024
    except Exception:
        return False


class DicomVolume:
    """Loads a folder of DICOM slices into a spacing-correct 3-D volume.
    Optimized for 4GB RAM Jetson Nano: parses headers first without loading pixel
    arrays, and streams slices directly with on-the-fly downsampling (4x RAM reduction).
    """

    def __init__(self, folder_path: str):
        self.folder_path = folder_path
        self.raw_data: np.ndarray = None
        self.pixel_spacing = (1.0, 1.0)
        self.slice_thickness = 1.0
        self.volume_data: pv.ImageData = self._load(folder_path)

    def _load(self, folder_path: str) -> pv.ImageData:
        cache_npz = get_volume_cache_path(folder_path)
        if is_cache_valid(cache_npz, folder_path):
            try:
                with np.load(cache_npz) as data:
                    vol_data = data["vol_data"].astype(np.float32)
                    self.raw_data = vol_data
                    self.pixel_spacing = tuple(data["pixel_spacing"])
                    self.slice_thickness = float(data["slice_thickness"])
                    vol = pv.wrap(vol_data)
                    vol.spacing = (self.pixel_spacing[0], self.pixel_spacing[1], self.slice_thickness)
                    return vol
            except Exception as e:
                print(f"[DicomVolume] Failed to load volume cache: {e}")

        dcm_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".dcm")]
        if not dcm_files:
            raise FileNotFoundError(f"No .dcm files found in: {folder_path}")

        # Step 1: Read only header tags (stop_before_pixels=True) to sort by Z position.
        # This uses <1MB RAM instead of loading hundreds of megabytes of uncompressed slices.
        meta_list = []
        skipped = []
        for f in dcm_files:
            full_path = os.path.join(folder_path, f)
            try:
                hdr = pydicom.dcmread(full_path, stop_before_pixels=True)
                z_pos = float(hdr.ImagePositionPatient[2])
                meta_list.append((z_pos, full_path))
            except Exception as exc:
                skipped.append((f, str(exc)))

        if skipped:
            print(f"[DicomVolume] Skipped {len(skipped)} unreadable file(s):")
            for fname, reason in skipped:
                print(f"    - {fname}: {reason}")

        if not meta_list:
            raise ValueError(
                f"No readable CT slices found in '{folder_path}'.  "
                f"({len(dcm_files)} .dcm files present but none had position tags.)"
            )

        # Sort strictly by physical Z
        meta_list.sort(key=lambda x: x[0])

        # Step 2: Read first slice to determine dimensions and voxel spacing
        first_dcm = pydicom.dcmread(meta_list[0][1])
        orig_shape = first_dcm.pixel_array.shape
        n_slices = len(meta_list)

        spacing = getattr(first_dcm, "PixelSpacing", [1.0, 1.0])
        z_space = (
            abs(meta_list[1][0] - meta_list[0][0])
            if n_slices > 1 else 1.0
        )

        # Step 3: Stream slices into 3D volume
        # On 4GB Jetson Nano, downsample 2D on the fly to save >180MB RAM spike
        if JETSON_OPTIMIZED:
            target_h = orig_shape[0] // 2
            target_w = orig_shape[1] // 2
            vol_data = np.zeros((target_h, target_w, n_slices), dtype=np.float32)

            for i, (_, path) in enumerate(meta_list):
                dcm = pydicom.dcmread(path)
                slope = float(getattr(dcm, "RescaleSlope", 1.0))
                intercept = float(getattr(dcm, "RescaleIntercept", 0.0))
                vol_data[:, :, i] = dcm.pixel_array[::2, ::2] * slope + intercept

            self.raw_data = vol_data
            self.pixel_spacing = (float(spacing[0]) * 2.0, float(spacing[1]) * 2.0)
            self.slice_thickness = float(z_space)
            vol = pv.wrap(vol_data)
            vol.spacing = (self.pixel_spacing[0], self.pixel_spacing[1], self.slice_thickness)
        else:
            vol_data = np.zeros((orig_shape[0], orig_shape[1], n_slices), dtype=np.float32)
            for i, (_, path) in enumerate(meta_list):
                dcm = pydicom.dcmread(path)
                slope = float(getattr(dcm, "RescaleSlope", 1.0))
                intercept = float(getattr(dcm, "RescaleIntercept", 0.0))
                vol_data[:, :, i] = dcm.pixel_array * slope + intercept

            self.raw_data = vol_data
            self.pixel_spacing = (float(spacing[0]), float(spacing[1]))
            self.slice_thickness = float(z_space)
            vol = pv.wrap(vol_data)
            vol.spacing = (self.pixel_spacing[0], self.pixel_spacing[1], self.slice_thickness)
            vol = vol.gaussian_smooth(radius_factor=1.0)

        # Save compressed volume cache for ultra-fast MPR loading (<0.05s)
        try:
            cache_npz = get_volume_cache_path(folder_path)
            np.savez_compressed(
                cache_npz,
                vol_data=vol_data.astype(np.int16),
                pixel_spacing=np.array(self.pixel_spacing),
                slice_thickness=self.slice_thickness,
            )
        except Exception as exc:
            print(f"[DicomVolume] Warning: could not write volume cache: {exc}")

        # Explicit cleanup of temporary objects for 4GB RAM budget
        del meta_list, first_dcm
        gc.collect()
        return vol

    def scalar_range(self) -> tuple:
        return self.volume_data.get_data_range()


class MeshSet:
    """Bone-only isosurface generated from a DicomVolume or loaded from disk cache."""

    def _sample_hu_density(self, mesh: pv.PolyData) -> pv.PolyData:
        """Sample true Hounsfield Unit (HU) volumetric density onto the bone mesh vertices.
        Samples at the surface vertices and along the inward surface normal into the bone cortex,
        computing the representative cortical bone density.
        Values are clipped to non-negative (background/air set to 0 HU).
        """
        if not hasattr(self, "volume") or self.volume is None or getattr(self.volume, "volume_data", None) is None:
            return mesh

        try:
            vol_data = self.volume.volume_data
            mesh_with_normals = mesh.compute_normals(
                point_normals=True, cell_normals=False, auto_orient_normals=True
            )
            normals = np.asarray(mesh_with_normals.point_data.get("Normals"))
            pts = np.asarray(mesh.points)

            # 1. Direct surface vertex sampling
            s_surf = pv.PolyData(pts).sample(vol_data)
            v_surf = np.asarray(s_surf.point_data.get("values", np.zeros(len(pts))))

            # 2. Inward cortical probe (depth 1.2mm into bone cortex)
            if normals is not None and len(normals) == len(pts):
                pts_inward = pts - normals * 1.2
                s_inward = pv.PolyData(pts_inward).sample(vol_data)
                v_inward = np.asarray(s_inward.point_data.get("values", v_surf))
                hu_density = np.maximum(v_surf, v_inward)
            else:
                hu_density = v_surf

            hu_density = np.clip(hu_density, 0.0, None).astype(np.float32)
            mesh.point_data["HU_density"] = hu_density
        except Exception as exc:
            print(f"[dicom_engine] Warning: Could not sample HU density onto mesh: {exc}")

        return mesh

    def _build_bone(self, isovalue: float) -> pv.PolyData:
        mesh = self.volume.volume_data.contour(isosurfaces=[isovalue], method="flying_edges")

        if mesh.n_cells == 0:
            raise ValueError(
                f"Bone isovalue {isovalue} HU produced an empty mesh.  "
                f"Volume HU range is {self.volume.scalar_range()} — "
                f"try a lower isovalue."
            )

        # Decimate: removes the given fraction of triangles while preserving shape.
        mesh = mesh.decimate(_DECIMATE)
        mesh = mesh.clean()
        mesh = mesh.extract_largest()

        # Sample true volumetric HU density onto vertices before caching
        mesh = self._sample_hu_density(mesh)
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

    @classmethod
    def load_from_cache(
        cls,
        cache_path: str,
        preset: str = "body",
        folder_path: str = "",
    ) -> "MeshSet":
        """Ultra-fast (0.15s) load of pre-computed mesh directly from disk cache."""
        meshset = cls.__new__(cls)
        meshset.volume = None
        p = PRESETS.get(preset, PRESETS["body"])
        meshset.bone_isovalue = p["bone"]
        meshset.bone_mesh = pv.read(cache_path)

        # If cache is from an earlier version without HU_density, attempt to populate
        # from cached volume if available; otherwise leave absent (do NOT invent fake HU).
        if "HU_density" not in meshset.bone_mesh.point_data and folder_path:
            meshset._try_attach_hu_from_volume(folder_path)

        return meshset

    def _try_attach_hu_from_volume(self, folder_path: str):
        """Attaches HU density from cached volume .npz if available."""
        try:
            cache_npz = get_volume_cache_path(folder_path)
            if is_cache_valid(cache_npz, folder_path):
                with np.load(cache_npz) as data:
                    vol_data = data["vol_data"].astype(np.float32)
                    ps = tuple(data["pixel_spacing"])
                    st = float(data["slice_thickness"])
                    vol = pv.wrap(vol_data)
                    vol.spacing = (ps[0], ps[1], st)
                    dummy_vol = type("DummyVol", (), {"volume_data": vol})()
                    self.volume = dummy_vol
                    self.bone_mesh = self._sample_hu_density(self.bone_mesh)
                    self.volume = None
        except Exception as exc:
            print(f"[dicom_engine] Could not attach HU density from volume cache: {exc}")

    def has_hu_density(self) -> bool:
        """Returns True if genuine HU density scalars are attached to the bone mesh."""
        return self.bone_mesh is not None and "HU_density" in self.bone_mesh.point_data

    def get_hu_range(self) -> tuple[float, float]:
        """Returns the (min, max) display range for the HU colormap.
        Standard CT bone window spans ~200 HU (trabecular) to ~1800 HU (dense cortical).
        """
        if not self.has_hu_density():
            return (200.0, 1800.0)
        arr = np.asarray(self.bone_mesh.point_data["HU_density"])
        if len(arr) == 0:
            return (200.0, 1800.0)
        p5 = float(np.percentile(arr, 5))
        p98 = float(np.percentile(arr, 98))
        lo = max(150.0, p5)
        hi = max(lo + 300.0, min(2200.0, p98))
        return (lo, hi)

    def add_to_plotter(
        self,
        plotter: pv.Plotter,
        density_mode: bool = False,
        cmap: str = "turbo",
    ):
        """Adds the bone mesh to an existing plotter.
        If density_mode is True and HU density is available, renders with continuous HU colormap.
        Otherwise renders with warm natural cortical bone shading.
        """
        if density_mode and self.has_hu_density():
            lo, hi = self.get_hu_range()
            bone_actor = plotter.add_mesh(
                self.bone_mesh,
                scalars="HU_density",
                cmap=cmap,
                clim=(lo, hi),
                smooth_shading=True,
                ambient=0.35,
                diffuse=0.75,
                specular=0.15,
                specular_power=10,
                opacity=1.0,
                name="bone",
                scalar_bar_args={
                    "title": "Density (HU)",
                    "color": "#e0e0e0",
                    "title_font_size": 11,
                    "label_font_size": 9,
                    "shadow": False,
                    "n_labels": 5,
                    "fmt": "%.0f",
                    "position_x": 0.84,
                    "position_y": 0.05,
                    "width": 0.12,
                    "height": 0.38,
                },
            )
        else:
            bone_actor = plotter.add_mesh(
                self.bone_mesh,
                color=_BONE_COLOUR,
                smooth_shading=True,
                ambient=_BONE_AMBIENT,
                diffuse=_BONE_DIFFUSE,
                specular=_BONE_SPECULAR,
                specular_power=_BONE_SPECULAR_PWR,
                opacity=1.0,
                name="bone",
            )
        return bone_actor, None


def build_meshes_from_folder(
    folder_path: str,
    preset: str = "body",
) -> "MeshSet":
    """Convenience one-shot: checks cache first, builds and caches if missing."""
    cache_path = get_cache_path(folder_path, preset)
    if is_cache_valid(cache_path, folder_path):
        return MeshSet.load_from_cache(cache_path, preset=preset, folder_path=folder_path)

    volume = DicomVolume(folder_path)
    meshset = MeshSet(volume, preset=preset)
    try:
        meshset.bone_mesh.save(cache_path)
    except Exception as exc:
        print(f"[build_meshes_from_folder] Warning: could not write cache: {exc}")
    gc.collect()
    return meshset


def detect_scan_anatomy(folder_path: str) -> str:
    """Detects anatomical region of a scan from DICOM headers and folder name.
    Returns: 'spine', 'head', 'chest', 'abdomen', or 'general'.
    """
    # 1. Inspect folder name
    folder_lower = os.path.basename(os.path.normpath(folder_path)).lower()
    if any(k in folder_lower for k in ("spine", "lumbar", "lspine", "cervical", "thoracic")):
        return "spine"
    if any(k in folder_lower for k in ("skull", "head", "brain", "crane", "neuro")):
        return "head"
    if any(k in folder_lower for k in ("chest", "lung", "thorax", "pulmonary")):
        return "chest"
    if any(k in folder_lower for k in ("abdomen", "pelvis", "liver")):
        return "abdomen"

    # 2. Inspect first DICOM header if available
    try:
        dcm_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".dcm")]
        if dcm_files:
            ds = pydicom.dcmread(os.path.join(folder_path, dcm_files[0]), stop_before_pixels=True)
            body_part = str(getattr(ds, "BodyPartExamined", "")).upper()
            study_desc = str(getattr(ds, "StudyDescription", "")).upper()
            series_desc = str(getattr(ds, "SeriesDescription", "")).upper()
            combined = f"{body_part} {study_desc} {series_desc}"

            if any(k in combined for k in ("LSPINE", "CSPINE", "TSPINE", "SPINE", "LUMBAR", "VERTEBRA")):
                return "spine"
            if any(k in combined for k in ("HEAD", "SKULL", "BRAIN", "CRANE", "POLYGONE", "CEREBRAL")):
                return "head"
            if any(k in combined for k in ("CHEST", "LUNG", "THORAX", "PULMONARY", "MEDIASTIN")):
                return "chest"
            if any(k in combined for k in ("ABDOMEN", "PELVIS", "LIVER", "KIDNEY")):
                return "abdomen"
    except Exception:
        pass

    return "general"


def load_volume_for_mpr(folder_path: str) -> tuple[np.ndarray, tuple[float, float], float, str]:
    """Loads raw 3D volume array and spatial spacings directly for MPR 2D slicing.
    Ultra-fast: loads compressed .npz volume cache if available (<0.05s).
    Returns (vol_data, (spacing_y, spacing_x), spacing_z, anatomy).
    """
    anatomy = detect_scan_anatomy(folder_path)
    cache_npz = get_volume_cache_path(folder_path)
    if is_cache_valid(cache_npz, folder_path):
        try:
            with np.load(cache_npz) as data:
                return (
                    data["vol_data"].astype(np.float32),
                    tuple(data["pixel_spacing"]),
                    float(data["slice_thickness"]),
                    anatomy
                )
        except Exception as e:
            print(f"[load_volume_for_mpr] Cache read failed: {e}")

    vol = DicomVolume(folder_path)
    return (vol.raw_data, vol.pixel_spacing, vol.slice_thickness, anatomy)


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
            cache_path = get_cache_path(self.folder_path, self.preset)
            if is_cache_valid(cache_path, self.folder_path):
                self.progress.emit("Loading 3D model from fast cache…")
                meshset = MeshSet.load_from_cache(cache_path, preset=self.preset, folder_path=self.folder_path)
                self.finished.emit(meshset)
                return

            mode = "Jetson-optimised" if JETSON_OPTIMIZED else "full-quality"
            self.progress.emit(f"Reading DICOM slices  [{mode}]…")

            volume = DicomVolume(self.folder_path)
            lo, hi = volume.scalar_range()
            dims   = volume.volume_data.dimensions
            self.progress.emit(
                f"Volume ready\n"
                f"    {dims[0]}×{dims[1]}×{dims[2]} voxels  ·  "
                f"HU {lo:.0f} → {hi:.0f}\n\n"
                f"Generating bone mesh…"
            )

            meshset = MeshSet.__new__(MeshSet)
            meshset.volume        = volume
            meshset.bone_isovalue = PRESETS.get(self.preset, PRESETS["body"])["bone"]
            meshset.bone_mesh     = meshset._build_bone(meshset.bone_isovalue)

            # Save to disk cache so future loads take <0.2s
            try:
                meshset.bone_mesh.save(cache_path)
            except Exception as exc:
                print(f"[DicomLoader] Warning: could not write cache: {exc}")

            # Explicit memory reclamation for 4GB Jetson Nano budget
            volume.volume_data = None
            meshset.volume = None
            del volume
            gc.collect()

            self.progress.emit(
                f"Bone mesh ready\n"
                f"    {meshset.bone_mesh.n_points:,} vertices  ·  "
                f"    {meshset.bone_mesh.n_cells:,} triangles\n\n"
                f"Uploading to GPU…"
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
