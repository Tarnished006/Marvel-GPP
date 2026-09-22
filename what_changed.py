"""
what_changed.py - Core domain model and difference calculation for ICU Feature 5: WHAT CHANGED?

Provides an objective, neutral visual review tool that helps the clinician identify where
the CURRENT and PREVIOUS scans differ visually for the same patient.

In strict accordance with medical safety specifications:
- Zero clinical interpretation, no diagnostic claims, no automatic lesion/disease detection.
- No claim of improvement, deterioration, progression, or regression.
- Does not classify changes as clinically important or pathological.
- Neutral terminology only: "Difference Review", "Current", "Previous", "Difference",
  "Difference Threshold", "Difference map available", "Difference map unavailable".
- Only calculates voxel-level difference maps when Current and Previous volumes are
  demonstrably compatible for direct comparison (dimensions, spacing, origin).
- Never resamples, registers, guesses alignment, or fabricates difference maps.
- Completely isolated from SameLocationReview, MeasurementTracker, AnnotationCarryForwardManager,
  and SurgicalPlanSession.
"""

from typing import Optional, Tuple, Dict, Any
import numpy as np


class ChangeReview:
    """Manages objective visual difference review between Current and Previous scans for a patient."""

    def __init__(self):
        self.current_scan_id: Optional[str] = None
        self.previous_scan_id: Optional[str] = None
        self.current_patient_mrn: Optional[str] = None
        self.previous_patient_mrn: Optional[str] = None

        self.threshold: float = 50.0  # Default visualization parameter in HU
        self.enabled: bool = False
        self.difference_available: bool = False
        self.status_message: str = "No difference review active"
        self.rejection_reason: Optional[str] = None

        # Cached volumes & geometries
        self._current_volume: Optional[np.ndarray] = None
        self._previous_volume: Optional[np.ndarray] = None
        self._current_geom: Optional[dict] = None
        self._previous_geom: Optional[dict] = None

        # Cached base difference volume: |current - previous|
        self._cached_diff_volume: Optional[np.ndarray] = None
        self._cache_key: Optional[Tuple[str, str, str]] = None  # (mrn, curr_id, prev_id)
        self._cached_changed_voxel_count: int = 0
        self._cached_threshold_for_count: Optional[float] = None

    def set_patient(self, mrn: Optional[str]):
        """Sets active patient MRN. Clears state if patient changes."""
        mrn_str = str(mrn) if mrn is not None else None
        if self.current_patient_mrn != mrn_str or self.previous_patient_mrn != mrn_str:
            self.clear()
            self.current_patient_mrn = mrn_str
            self.previous_patient_mrn = mrn_str

    def set_scans(
        self,
        current_scan_id: Optional[str],
        previous_scan_id: Optional[str],
        current_volume: Optional[np.ndarray] = None,
        previous_volume: Optional[np.ndarray] = None,
        current_geom: Optional[dict] = None,
        previous_geom: Optional[dict] = None,
        patient_mrn: Optional[str] = None
    ):
        """Configures scan IDs, volumes, and geometries, then validates grid compatibility."""
        if patient_mrn is not None:
            self.set_patient(patient_mrn)

        c_id = str(current_scan_id) if current_scan_id is not None else None
        p_id = str(previous_scan_id) if previous_scan_id is not None else None

        # Invalidate cached difference if scans change
        if self.current_scan_id != c_id or self.previous_scan_id != p_id:
            self._cached_diff_volume = None
            self._cache_key = None
            self._cached_changed_voxel_count = 0
            self._cached_threshold_for_count = None

        self.current_scan_id = c_id
        self.previous_scan_id = p_id
        self._current_volume = current_volume
        self._previous_volume = previous_volume
        self._current_geom = current_geom
        self._previous_geom = previous_geom

        # Check compatibility
        compat, reason = self.check_compatibility(
            current_volume=current_volume,
            previous_volume=previous_volume,
            current_geom=current_geom,
            previous_geom=previous_geom
        )

        self.difference_available = compat
        self.rejection_reason = reason if not compat else None

        if not self.current_scan_id or not self.previous_scan_id:
            self.status_message = "No difference review active"
            self.difference_available = False
        elif not compat:
            self.status_message = "Difference map unavailable"
            self.enabled = False
        else:
            self.status_message = "Difference map available"
            # Precompute difference volume if compatible
            self.compute_difference_volume()

    def check_compatibility(
        self,
        current_volume: Optional[np.ndarray] = None,
        previous_volume: Optional[np.ndarray] = None,
        current_geom: Optional[dict] = None,
        previous_geom: Optional[dict] = None
    ) -> Tuple[bool, str]:
        """Validates that Current and Previous scans share demonstrably compatible voxel grids.
        
        At minimum verifies:
        1. Both volumes are present.
        2. Dimensions match exactly.
        3. Voxel spacings match within numerical tolerance (1e-3).
        4. Physical origins match within numerical tolerance (1e-3).
        5. Patients must match.
        """
        if self.current_patient_mrn and self.previous_patient_mrn:
            if self.current_patient_mrn != self.previous_patient_mrn:
                return False, "Current and Previous scans must belong to the same patient."

        c_vol = current_volume if current_volume is not None else self._current_volume
        p_vol = previous_volume if previous_volume is not None else self._previous_volume

        if c_vol is None or p_vol is None:
            return False, "Both Current and Previous volumes are required for difference review."

        # 1. Dimensions check
        if c_vol.shape != p_vol.shape:
            return False, "Current and Previous voxel grids are not directly compatible (differing dimensions)."

        # 2. Geometry check (if geometries provided)
        c_g = current_geom if current_geom is not None else self._current_geom
        p_g = previous_geom if previous_geom is not None else self._previous_geom

        if c_g is not None and p_g is not None:
            # Check dimensions from geometry if specified
            c_dims = c_g.get("dims")
            p_dims = p_g.get("dims")
            if c_dims and p_dims and tuple(c_dims) != tuple(p_dims):
                return False, "Current and Previous voxel grids are not directly compatible (differing matrix dimensions)."

            # Check spacing
            c_spacing = c_g.get("spacing") or (c_g.get("dy", 1.0), c_g.get("dx", 1.0))
            p_spacing = p_g.get("spacing") or (p_g.get("dy", 1.0), p_g.get("dx", 1.0))
            if abs(float(c_spacing[0]) - float(p_spacing[0])) > 1e-3 or abs(float(c_spacing[1]) - float(p_spacing[1])) > 1e-3:
                return False, "Current and Previous voxel grids are not directly compatible (differing voxel spacing)."

            # Check slice thickness
            c_dz = float(c_g.get("thickness") or c_g.get("dz") or 1.0)
            p_dz = float(p_g.get("thickness") or p_g.get("dz") or 1.0)
            if abs(c_dz - p_dz) > 1e-3:
                return False, "Current and Previous voxel grids are not directly compatible (differing slice thickness)."

            # Check origin
            c_origin = c_g.get("origin") or (0.0, 0.0, 0.0)
            p_origin = p_g.get("origin") or (0.0, 0.0, 0.0)
            if (abs(float(c_origin[0]) - float(p_origin[0])) > 1e-3 or
                abs(float(c_origin[1]) - float(p_origin[1])) > 1e-3 or
                abs(float(c_origin[2]) - float(p_origin[2])) > 1e-3):
                return False, "Current and Previous voxel grids are not directly compatible (differing physical origin)."

        return True, "Compatible"

    def compute_difference_volume(self) -> Optional[np.ndarray]:
        """Calculates and caches the objective voxel-level absolute difference volume:
        
        difference = abs(current_volume - previous_volume)
        
        Returns None if volumes are not compatible.
        """
        if not self.difference_available:
            return None

        c_vol = self._current_volume
        p_vol = self._previous_volume
        if c_vol is None or p_vol is None:
            return None

        cache_key = (str(self.current_patient_mrn), str(self.current_scan_id), str(self.previous_scan_id))
        if self._cached_diff_volume is not None and self._cache_key == cache_key:
            return self._cached_diff_volume

        # Compute absolute difference using float32 to prevent overflow
        diff = np.abs(c_vol.astype(np.float32) - p_vol.astype(np.float32))
        self._cached_diff_volume = diff
        self._cache_key = cache_key
        self._cached_threshold_for_count = None
        return diff

    def get_difference_volume(self) -> Optional[np.ndarray]:
        """Returns the cached difference volume or computes it."""
        if self._cached_diff_volume is not None:
            return self._cached_diff_volume
        return self.compute_difference_volume()

    def set_threshold(self, threshold: float) -> float:
        """Sets the visualization difference threshold in HU."""
        self.threshold = float(max(1.0, threshold))
        self._cached_threshold_for_count = None
        return self.threshold

    def set_enabled(self, enabled: bool) -> bool:
        """Enables or disables visual difference review. Requires difference to be available."""
        if enabled and not self.difference_available:
            self.enabled = False
            return False
        self.enabled = bool(enabled)
        return self.enabled

    def get_changed_voxel_count(self, threshold: Optional[float] = None) -> int:
        """Returns the number of voxels whose absolute difference is >= threshold.
        
        Strictly reported as 'Changed voxels: X', never as 'abnormal' or 'diseased'.
        """
        thresh = float(threshold) if threshold is not None else self.threshold
        if not self.difference_available:
            return 0

        diff_vol = self.get_difference_volume()
        if diff_vol is None:
            return 0

        if self._cached_threshold_for_count == thresh:
            return self._cached_changed_voxel_count

        count = int(np.count_nonzero(diff_vol >= thresh))
        self._cached_changed_voxel_count = count
        self._cached_threshold_for_count = thresh
        return count

    def get_slice_difference(
        self,
        orientation: str,
        slice_idx: int,
        threshold: Optional[float] = None
    ) -> Optional[np.ndarray]:
        """Extracts a 2D difference slice for the specified orientation and slice index.
        
        Returns a 2D float32 array of absolute difference values, or None if unavailable.
        """
        if not self.difference_available:
            return None

        diff_vol = self.get_difference_volume()
        if diff_vol is None:
            return None

        H, W, D = diff_vol.shape
        orient = orientation.lower()

        if orient == "axial":
            if 0 <= slice_idx < D:
                # diff_vol[:, :, slice_idx]
                return diff_vol[:, :, slice_idx]
        elif orient == "coronal":
            if 0 <= slice_idx < H:
                # np.flipud(diff_vol[slice_idx, :, :].T)
                return np.flipud(diff_vol[slice_idx, :, :].T)
        elif orient == "sagittal":
            if 0 <= slice_idx < W:
                # np.flipud(diff_vol[:, slice_idx, :].T)
                return np.flipud(diff_vol[:, slice_idx, :].T)

        return None

    def clear(self):
        """Clears difference review state, cached volumes, and overlays."""
        self.current_scan_id = None
        self.previous_scan_id = None
        self.current_patient_mrn = None
        self.previous_patient_mrn = None
        self.threshold = 50.0
        self.enabled = False
        self.difference_available = False
        self.status_message = "No difference review active"
        self.rejection_reason = None
        self._current_volume = None
        self._previous_volume = None
        self._current_geom = None
        self._previous_geom = None
        self._cached_diff_volume = None
        self._cache_key = None
        self._cached_changed_voxel_count = 0
        self._cached_threshold_for_count = None

    def reset(self):
        """Alias for clear()."""
        self.clear()

    def clear_review(self):
        """Alias for clear()."""
        self.clear()

    def is_active(self) -> bool:
        """Returns True if difference review is actively enabled and available."""
        return bool(self.enabled and self.difference_available)

    @property
    def difference_volume(self) -> Optional[np.ndarray]:
        """Returns cached difference volume or None."""
        return self.get_difference_volume()

    @difference_volume.setter
    def difference_volume(self, val: Optional[np.ndarray]):
        self._cached_diff_volume = val

    def set_patient_and_scans(
        self,
        current_patient_mrn: Optional[str],
        previous_patient_mrn: Optional[str],
        current_scan_id: Optional[str],
        previous_scan_id: Optional[str]
    ):
        """Configures patient MRNs and scan identifiers."""
        self.current_patient_mrn = str(current_patient_mrn) if current_patient_mrn is not None else None
        self.previous_patient_mrn = str(previous_patient_mrn) if previous_patient_mrn is not None else None
        self.current_scan_id = str(current_scan_id) if current_scan_id is not None else None
        self.previous_scan_id = str(previous_scan_id) if previous_scan_id is not None else None

    def compute_difference(
        self,
        current_volume: np.ndarray,
        previous_volume: np.ndarray,
        current_geom: Optional[dict] = None,
        previous_geom: Optional[dict] = None
    ) -> Optional[np.ndarray]:
        """Validates compatibility and computes the objective difference volume."""
        self._current_volume = current_volume
        self._previous_volume = previous_volume
        self._current_geom = current_geom
        self._previous_geom = previous_geom

        compat, reason = self.check_compatibility(current_volume, previous_volume, current_geom, previous_geom)
        if not compat:
            self.set_difference_unavailable(reason)
            return None

        self.difference_available = True
        self.status_message = "Difference map available"
        return self.compute_difference_volume()

    def set_difference_unavailable(self, reason: str = ""):
        """Sets difference review to unavailable with an objective reason."""
        self.difference_available = False
        self.enabled = False
        self.rejection_reason = reason
        self._cached_diff_volume = None
        if reason:
            self.status_message = f"Difference map unavailable: {reason}"
        else:
            self.status_message = "Difference map unavailable"
