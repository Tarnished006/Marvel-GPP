"""
device_markers.py - Core domain model and manager for ICU Feature 6: DEVICE MARKERS.

Allows a clinician to place, view, and manage visual markers for medical devices
visible on an imaging scan (e.g., Endotracheal Tube, Central Line, Feeding Tube,
Drain, Catheter, Other) so their locations can be quickly identified.

Strict clinical safety rules:
- Strictly user-placed visual markers.
- Zero automatic detection, zero AI/computer vision, zero inferencing.
- Zero clinical interpretation: never claims correct/incorrect position, displacement,
  migration, or complication.
- Canonical physical (X, Y, Z) coordinates in mm.
- Strictly scoped to a specific patient MRN and scan ID.
- Completely decoupled from OR planning and ICU Features 1-5.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
import uuid
import numpy as np


CONTROLLED_DEVICE_TYPES = [
    "Endotracheal Tube",
    "Central Line",
    "Feeding Tube",
    "Drain",
    "Catheter",
    "Other"
]


@dataclass
class DeviceMarker:
    """Represents a user-placed visual marker for a medical device on a scan."""
    id: str
    patient_mrn: str
    scan_id: str
    device_type: str
    label: str
    physical_x_mm: float = 0.0
    physical_y_mm: float = 0.0
    physical_z_mm: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def physical_coordinates(self) -> Tuple[float, float, float]:
        return (float(self.physical_x_mm), float(self.physical_y_mm), float(self.physical_z_mm))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "patient_mrn": self.patient_mrn,
            "scan_id": self.scan_id,
            "device_type": self.device_type,
            "label": self.label,
            "physical_x_mm": self.physical_x_mm,
            "physical_y_mm": self.physical_y_mm,
            "physical_z_mm": self.physical_z_mm,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceMarker":
        return cls(
            id=str(data.get("id", "")),
            patient_mrn=str(data.get("patient_mrn", "")),
            scan_id=str(data.get("scan_id", "")),
            device_type=str(data.get("device_type", "Other")),
            label=str(data.get("label", "Device")),
            physical_x_mm=float(data.get("physical_x_mm", 0.0)),
            physical_y_mm=float(data.get("physical_y_mm", 0.0)),
            physical_z_mm=float(data.get("physical_z_mm", 0.0)),
            created_at=str(data.get("created_at", datetime.now(timezone.utc).isoformat())),
            metadata=dict(data.get("metadata", {})),
        )


class DeviceMarkerManager:
    """Manages user-placed device markers for the active patient and scans."""

    def __init__(self):
        self.patient_mrn: Optional[str] = None
        self.current_scan_id: Optional[str] = None
        self.current_geom: Optional[dict] = None
        self._markers: Dict[str, DeviceMarker] = {}
        self.selected_marker_id: Optional[str] = None

    def set_patient(self, mrn: Optional[str]):
        """Sets active patient MRN. Clears all markers if switching patients."""
        mrn_str = str(mrn) if mrn is not None else None
        if self.patient_mrn != mrn_str:
            self.patient_mrn = mrn_str
            self._markers.clear()
            self.selected_marker_id = None
            self.current_scan_id = None
            self.current_geom = None

    def set_scan(self, scan_id: Optional[str], geom: Optional[dict] = None):
        """Sets active scan identifier and its geometry."""
        self.current_scan_id = str(scan_id) if scan_id is not None else None
        if geom is not None:
            self.current_geom = self._parse_geometry(geom)

    def _parse_geometry(self, geom: Optional[dict]) -> Optional[dict]:
        """Normalizes geometry metadata into canonical numeric format."""
        if not geom or not isinstance(geom, dict):
            return None
        try:
            dims = geom.get("dims", (1, 1, 1))
            if len(dims) == 3:
                H, W, D = int(dims[0]), int(dims[1]), int(dims[2])
            elif len(dims) == 2:
                H, W, D = int(dims[0]), int(dims[1]), 1
            else:
                H, W, D = 1, 1, 1

            spacing = geom.get("spacing", (1.0, 1.0))
            if len(spacing) >= 2:
                dy = float(spacing[0])
                dx = float(spacing[1])
            else:
                dy, dx = 1.0, 1.0

            dz = float(geom.get("thickness", 1.0) or 1.0)

            origin = geom.get("origin", (0.0, 0.0, 0.0))
            if len(origin) >= 3:
                ox = float(origin[0])
                oy = float(origin[1])
                oz = float(origin[2])
            else:
                ox, oy, oz = 0.0, 0.0, 0.0

            return {
                "H": max(1, H), "W": max(1, W), "D": max(1, D),
                "dy": max(1e-5, dy), "dx": max(1e-5, dx), "dz": max(1e-5, dz),
                "ox": ox, "oy": oy, "oz": oz,
            }
        except Exception:
            return None

    def add_marker(self, marker: DeviceMarker):
        """Adds or updates a device marker."""
        if not marker or not marker.id:
            return
        # Ensure patient MRN matches
        if self.patient_mrn and marker.patient_mrn != self.patient_mrn:
            return
        self._markers[marker.id] = marker

    def get_markers_for_scan(self, scan_id: Optional[str]) -> List[DeviceMarker]:
        """Returns all markers belonging to the specified scan."""
        if not scan_id:
            return []
        sid = str(scan_id)
        return [m for m in self._markers.values() if m.scan_id == sid]

    def get_current_markers(self) -> List[DeviceMarker]:
        """Returns all markers belonging to the active scan."""
        return self.get_markers_for_scan(self.current_scan_id)

    def get_markers_for_patient(self, mrn: str) -> List[DeviceMarker]:
        """Returns all markers belonging to the patient MRN."""
        return [m for m in self._markers.values() if m.patient_mrn == str(mrn)]

    def get_all_markers(self) -> List[DeviceMarker]:
        """Returns all currently loaded markers."""
        return list(self._markers.values())

    def get_marker(self, marker_id: str) -> Optional[DeviceMarker]:
        """Retrieves a single marker by ID."""
        return self._markers.get(str(marker_id))

    def delete_marker(self, marker_id: str) -> bool:
        """Deletes a device marker from memory."""
        mid = str(marker_id)
        if mid in self._markers:
            del self._markers[mid]
            if self.selected_marker_id == mid:
                self.selected_marker_id = None
            return True
        return False

    def select_marker(self, marker_id: Optional[str]):
        """Selects a device marker by ID."""
        if marker_id is not None and str(marker_id) in self._markers:
            self.selected_marker_id = str(marker_id)
        else:
            self.selected_marker_id = None

    def get_selected_marker(self) -> Optional[DeviceMarker]:
        """Returns the currently selected device marker."""
        if self.selected_marker_id:
            return self._markers.get(self.selected_marker_id)
        return None

    def set_context(self, mrn: Optional[str], scan_id: Optional[str] = None, geom: Optional[dict] = None):
        """Sets active patient MRN, scan ID, and volume geometry context."""
        self.set_patient(mrn)
        if scan_id is not None or geom is not None:
            self.set_scan(scan_id, geom)

    def get_markers_for_active_scan(self) -> List[DeviceMarker]:
        """Returns all markers belonging to the active scan."""
        return self.get_current_markers()

    def remove_marker(self, marker_id: str) -> bool:
        """Alias for delete_marker."""
        return self.delete_marker(marker_id)

    def clear_selection(self):
        """Clears active marker selection."""
        self.selected_marker_id = None

    def get_derived_slice(self, marker_id: str, orientation: str = "axial") -> Optional[int]:
        """Derives the orthogonal slice index for the marker specified by ID."""
        marker = self.get_marker(marker_id)
        if not marker:
            return None
        return self.derive_slice_index(marker, orientation)

    def derive_slice_index(self, marker: DeviceMarker, orientation: str = "axial", geom: Optional[dict] = None) -> Optional[int]:
        """Derives the orthogonal slice index for the marker using scan geometry."""
        g = self._parse_geometry(geom) if geom else self.current_geom
        x, y, z = marker.physical_coordinates
        orient = orientation.lower()

        if not g:
            # Fallback if no geometry metadata available
            if orient == "axial":
                return max(0, int(round(z)))
            elif orient == "coronal":
                return max(0, int(round(y)))
            elif orient == "sagittal":
                return max(0, int(round(x)))
            return 0

        if orient == "axial":
            # z dimension: D slices along dz from oz
            idx = int(np.clip(round((z - g["oz"]) / g["dz"]), 0, g["D"] - 1))
            return idx
        elif orient == "coronal":
            # y dimension: H slices along dy from oy
            idx = int(np.clip(round((y - g["oy"]) / g["dy"]), 0, g["H"] - 1))
            return idx
        elif orient == "sagittal":
            # x dimension: W slices along dx from ox
            idx = int(np.clip(round((x - g["ox"]) / g["dx"]), 0, g["W"] - 1))
            return idx
        return None

    def clear(self):
        """Clears all state, markers, and selections."""
        self._markers.clear()
        self.selected_marker_id = None
        self.patient_mrn = None
        self.current_scan_id = None
        self.current_geom = None
