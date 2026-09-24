# before_after.py
"""
Dedicated Before vs After comparison data model for Sanketa / Marvel-GPP.

Supports visual review and comparison of imaging changes over time for the same patient.
Enforces patient safety, scan validation, and complete isolation from surgical planning state.
"""

from typing import Optional, Any
import numpy as np


class BeforeAfterComparison:
    """Dedicated data model managing Before vs After visual review for a single patient."""

    DISPLAY_MODES = ("TOGGLE", "SIDE_BY_SIDE", "OVERLAY")
    OVERLAY_LABEL = "Unregistered Overlay"

    def __init__(self):
        self.before_scan: Optional[dict] = None
        self.after_scan: Optional[dict] = None
        self.before_scan_id: Optional[str] = None
        self.after_scan_id: Optional[str] = None
        self.before_patient_mrn: Optional[str] = None
        self.after_patient_mrn: Optional[str] = None

        self.is_active: bool = False
        self.display_mode: str = "TOGGLE"
        self.current_view: str = "AFTER"  # "BEFORE" or "AFTER"
        self.overlay_opacity: float = 0.5
        self.validation_error: Optional[str] = None

        # Cached volume & mesh data to prevent repeated disk reads
        self.before_volume: Optional[np.ndarray] = None
        self.before_pixel_spacing: Optional[tuple[float, float]] = None
        self.before_slice_thickness: Optional[float] = None
        self.before_anatomy: Optional[str] = None
        self.before_mesh: Any = None

        self.after_volume: Optional[np.ndarray] = None
        self.after_pixel_spacing: Optional[tuple[float, float]] = None
        self.after_slice_thickness: Optional[float] = None
        self.after_anatomy: Optional[str] = None
        self.after_mesh: Any = None

    @staticmethod
    def _extract_mrn(scan: Optional[dict], patient_mrn: Optional[str] = None) -> Optional[str]:
        if patient_mrn:
            return str(patient_mrn).strip()
        if scan:
            return str(scan.get("patient_mrn", "") or scan.get("mrn", "")).strip() or None
        return None

    @staticmethod
    def _extract_scan_id(scan: Optional[dict]) -> Optional[str]:
        if not scan:
            return None
        return str(scan.get("file_path", "") or scan.get("id", "")).strip() or None

    def set_before_scan(self, scan: dict, patient_mrn: Optional[str] = None) -> bool:
        """Sets the Before scan and verifies patient compatibility."""
        target_mrn = self._extract_mrn(scan, patient_mrn)
        if self.after_patient_mrn and target_mrn and self.after_patient_mrn != target_mrn:
            self.validation_error = "Before and After scans must belong to the same patient"
            return False

        self.before_scan = dict(scan) if scan else None
        self.before_patient_mrn = target_mrn or self.after_patient_mrn
        self.before_scan_id = self._extract_scan_id(scan)
        self.validation_error = None
        return True

    def set_after_scan(self, scan: dict, patient_mrn: Optional[str] = None) -> bool:
        """Sets the After scan and verifies patient compatibility."""
        target_mrn = self._extract_mrn(scan, patient_mrn)
        if self.before_patient_mrn and target_mrn and self.before_patient_mrn != target_mrn:
            self.validation_error = "Before and After scans must belong to the same patient"
            return False

        self.after_scan = dict(scan) if scan else None
        self.after_patient_mrn = target_mrn or self.before_patient_mrn
        self.after_scan_id = self._extract_scan_id(scan)
        self.validation_error = None
        return True

    def can_compare(self) -> bool:
        """Returns True if both scans are set, valid, and belong to the same patient."""
        if not self.before_scan or not self.after_scan:
            return False
        if not self.before_patient_mrn or not self.after_patient_mrn:
            return False
        if self.before_patient_mrn != self.after_patient_mrn:
            self.validation_error = "Before and After scans must belong to the same patient"
            return False
        return True

    def start_comparison(self, mode: str = "TOGGLE") -> bool:
        """Starts comparison if both scans are valid and belong to the same patient."""
        if not self.can_compare():
            if not self.validation_error:
                self.validation_error = "Both Before and After scans are required"
            return False

        if mode in self.DISPLAY_MODES:
            self.display_mode = mode
        self.is_active = True
        self.current_view = "AFTER"
        self.validation_error = None
        return True

    def exit_comparison(self):
        """Exits comparison mode and restores normal active view."""
        self.is_active = False
        self.current_view = "AFTER"

    def toggle_view(self) -> str:
        """Toggles between BEFORE and AFTER in TOGGLE mode."""
        if self.current_view == "BEFORE":
            self.current_view = "AFTER"
        else:
            self.current_view = "BEFORE"
        return self.current_view

    def set_display_mode(self, mode: str) -> bool:
        """Updates display mode ('TOGGLE', 'SIDE_BY_SIDE', 'OVERLAY')."""
        if mode in self.DISPLAY_MODES:
            self.display_mode = mode
            return True
        return False

    def set_overlay_opacity(self, opacity: float):
        """Sets the opacity for the unregistered visual overlay (0.0 to 1.0)."""
        self.overlay_opacity = float(max(0.0, min(1.0, opacity)))

    def clear(self):
        """Resets comparison state and clears cached volume data."""
        self.before_scan = None
        self.after_scan = None
        self.before_scan_id = None
        self.after_scan_id = None
        self.before_patient_mrn = None
        self.after_patient_mrn = None
        self.is_active = False
        self.display_mode = "TOGGLE"
        self.current_view = "AFTER"
        self.overlay_opacity = 0.5
        self.validation_error = None

        self.before_volume = None
        self.before_pixel_spacing = None
        self.before_slice_thickness = None
        self.before_anatomy = None
        self.before_mesh = None

        self.after_volume = None
        self.after_pixel_spacing = None
        self.after_slice_thickness = None
        self.after_anatomy = None
        self.after_mesh = None
