"""
measurement_tracker.py - Core domain model and business logic for ICU Feature 3: MEASUREMENT TRACKING.

Provides tracking and comparison of caliper/distance measurements across Current and Previous scans
for the same patient. Strictly avoids any clinical interpretation, qualitative judgment, or progression claims.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import uuid


@dataclass
class TrackedMeasurement:
    """Represents a clinically measured distance associated with a specific scan and patient."""
    id: str
    scan_id: str
    patient_mrn: str
    label: str
    value_mm: float
    unit: str = "mm"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "scan_id": self.scan_id,
            "patient_mrn": self.patient_mrn,
            "label": self.label,
            "value_mm": self.value_mm,
            "unit": self.unit,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrackedMeasurement":
        return cls(
            id=str(data.get("id", "")),
            scan_id=str(data.get("scan_id", "")),
            patient_mrn=str(data.get("patient_mrn", "")),
            label=str(data.get("label", "Measurement")),
            value_mm=float(data.get("value_mm", 0.0)),
            unit=str(data.get("unit", "mm")),
            created_at=str(data.get("created_at", datetime.now(timezone.utc).isoformat())),
            metadata=dict(data.get("metadata", {})),
        )


class MeasurementTracker:
    """Manages tracked measurements for the active patient across Current and Previous scans.
    
    Adheres to strict clinical objectivity:
    - Retains scan ID and patient MRN to prevent cross-patient or cross-scan leakage.
    - Zero clinical interpretation (no 'improved', 'worsened', 'progressed', 'regressed', etc.).
    - Pure numeric comparison: 'Current: XX.X mm', 'Previous: XX.X mm', 'Difference: X.X mm'.
    """

    def __init__(self):
        self.patient_mrn: Optional[str] = None
        self.current_scan_id: Optional[str] = None
        self.previous_scan_id: Optional[str] = None
        self._measurements: Dict[str, TrackedMeasurement] = {}
        self.selected_current_id: Optional[str] = None
        self.selected_previous_id: Optional[str] = None

    def set_patient(self, mrn: Optional[str]):
        """Sets active patient MRN. Clears measurements if switching patients."""
        mrn_str = str(mrn) if mrn is not None else None
        if self.patient_mrn != mrn_str:
            self.patient_mrn = mrn_str
            self._measurements.clear()
            self.selected_current_id = None
            self.selected_previous_id = None

    def set_scans(self, current_scan_id: Optional[str], previous_scan_id: Optional[str]):
        """Sets active Current and Previous scan identifiers."""
        self.current_scan_id = str(current_scan_id) if current_scan_id is not None else None
        self.previous_scan_id = str(previous_scan_id) if previous_scan_id is not None else None

        # Reset selection if it no longer matches the respective scan
        if self.selected_current_id and self.selected_current_id in self._measurements:
            if self._measurements[self.selected_current_id].scan_id != self.current_scan_id:
                self.selected_current_id = None
        if self.selected_previous_id and self.selected_previous_id in self._measurements:
            if self._measurements[self.selected_previous_id].scan_id != self.previous_scan_id:
                self.selected_previous_id = None

        # Auto-select first available if currently None
        if self.selected_current_id is None:
            curr_list = self.get_current_measurements()
            if curr_list:
                self.selected_current_id = curr_list[0].id
        if self.selected_previous_id is None:
            prev_list = self.get_previous_measurements()
            if prev_list:
                self.selected_previous_id = prev_list[0].id

    def add_measurement(self, m: TrackedMeasurement) -> bool:
        """Adds a tracked measurement. Enforces patient MRN matching."""
        if self.patient_mrn and m.patient_mrn != self.patient_mrn:
            return False

        self._measurements[m.id] = m
        if m.scan_id == self.current_scan_id:
            self.selected_current_id = m.id
        elif m.scan_id == self.previous_scan_id:
            self.selected_previous_id = m.id
        return True

    def remove_measurement(self, measurement_id: str) -> bool:
        """Removes a specific tracked measurement without affecting unrelated measurements."""
        if measurement_id in self._measurements:
            del self._measurements[measurement_id]
            if self.selected_current_id == measurement_id:
                self.selected_current_id = None
                curr_list = self.get_current_measurements()
                if curr_list:
                    self.selected_current_id = curr_list[0].id
            if self.selected_previous_id == measurement_id:
                self.selected_previous_id = None
                prev_list = self.get_previous_measurements()
                if prev_list:
                    self.selected_previous_id = prev_list[0].id
            return True
        return False

    def get_measurements_for_scan(self, scan_id: Optional[str]) -> List[TrackedMeasurement]:
        """Returns all tracked measurements associated with a given scan ID."""
        if not scan_id:
            return []
        s_id = str(scan_id)
        return [
            m for m in self._measurements.values()
            if m.scan_id == s_id and (not self.patient_mrn or m.patient_mrn == self.patient_mrn)
        ]

    def get_current_measurements(self) -> List[TrackedMeasurement]:
        """Returns tracked measurements for the current active scan."""
        return self.get_measurements_for_scan(self.current_scan_id)

    def get_previous_measurements(self) -> List[TrackedMeasurement]:
        """Returns tracked measurements for the previous scan."""
        return self.get_measurements_for_scan(self.previous_scan_id)

    def select_current(self, measurement_id: Optional[str]):
        """Selects a tracked measurement from the Current scan."""
        if measurement_id is None or measurement_id in self._measurements:
            self.selected_current_id = measurement_id

    def select_previous(self, measurement_id: Optional[str]):
        """Selects a tracked measurement from the Previous scan."""
        if measurement_id is None or measurement_id in self._measurements:
            self.selected_previous_id = measurement_id

    def get_comparison(self) -> dict:
        """Computes side-by-side comparison with zero clinical interpretation.
        
        Returns strictly neutral formatted texts and raw numeric values.
        """
        curr = self._measurements.get(self.selected_current_id) if self.selected_current_id else None
        prev = self._measurements.get(self.selected_previous_id) if self.selected_previous_id else None

        res = {
            "current_value": curr.value_mm if curr else None,
            "current_unit": curr.unit if curr else "mm",
            "current_text": f"Current: {curr.value_mm:.1f} {curr.unit}" if curr else "Current: None",
            "previous_value": prev.value_mm if prev else None,
            "previous_unit": prev.unit if prev else "mm",
            "previous_text": f"Previous: {prev.value_mm:.1f} {prev.unit}" if prev else "Previous: None",
            "difference_value": None,
            "difference_text": "Difference: N/A",
        }

        if curr is not None and prev is not None:
            diff = abs(curr.value_mm - prev.value_mm)
            res["difference_value"] = round(diff, 2)
            unit = curr.unit if curr.unit == prev.unit else "mm"
            res["difference_text"] = f"Difference: {diff:.1f} {unit}"

        return res

    def clear(self):
        """Resets tracker state completely."""
        self.patient_mrn = None
        self.current_scan_id = None
        self.previous_scan_id = None
        self._measurements.clear()
        self.selected_current_id = None
        self.selected_previous_id = None
