# same_location.py
"""
Dedicated Same-Location Review data model for ICU Mode in Aegis-Touch / Marvel-GPP.

Supports geometric physical-coordinate mapping between Current and Previous scans
for the same patient. Enforces:
- Canonical representation in 3D physical millimeters (X, Y, Z).
- Independent derivation of slice indices based on each scan's individual geometry.
- Graceful detection of missing/invalid spatial metadata.
- Complete isolation from surgical planning and caliper measurement states.
- No clinical interpretation (change detection, improvement, deterioration).
"""

from typing import Optional, Any
import numpy as np


class SameLocationReview:
    """Manages same-location review state between Current and Previous scans."""

    def __init__(self):
        self.current_scan_id: Optional[str] = None
        self.previous_scan_id: Optional[str] = None
        self.current_patient_mrn: Optional[str] = None
        self.previous_patient_mrn: Optional[str] = None

        self.physical_x_mm: Optional[float] = None
        self.physical_y_mm: Optional[float] = None
        self.physical_z_mm: Optional[float] = None

        self.current_slice_indices: dict[str, int] = {}
        self.previous_slice_indices: dict[str, int] = {}

        self.is_active: bool = False
        self.correspondence_available: bool = True
        self.status_message: str = "No location selected"

    def clear(self):
        """Resets same-location state and clears coordinates and indices."""
        self.physical_x_mm = None
        self.physical_y_mm = None
        self.physical_z_mm = None
        self.current_scan_id = None
        self.previous_scan_id = None
        self.current_patient_mrn = None
        self.previous_patient_mrn = None
        self.current_slice_indices = {}
        self.previous_slice_indices = {}
        self.is_active = False
        self.correspondence_available = True
        self.status_message = "No location selected"

    def is_point_valid(self) -> bool:
        """Returns True if a valid physical coordinate is currently stored."""
        return (
            self.is_active
            and self.physical_x_mm is not None
            and self.physical_y_mm is not None
            and self.physical_z_mm is not None
            and not (
                np.isnan(self.physical_x_mm)
                or np.isnan(self.physical_y_mm)
                or np.isnan(self.physical_z_mm)
            )
        )

    def get_physical_coordinates(self) -> Optional[tuple[float, float, float]]:
        """Returns canonical physical coordinates (X, Y, Z) in mm, or None."""
        if not self.is_point_valid():
            return None
        return (float(self.physical_x_mm), float(self.physical_y_mm), float(self.physical_z_mm))

    @staticmethod
    def _parse_geometry(geom: Optional[dict]) -> Optional[dict]:
        """Validates and extracts volume dimensions, spacings, and origin."""
        if not geom or not isinstance(geom, dict):
            return None

        dims = geom.get("dims")
        spacing = geom.get("spacing")
        thickness = geom.get("thickness") or geom.get("slice_thickness")
        origin = geom.get("origin", (0.0, 0.0, 0.0))

        if dims is None or spacing is None or thickness is None:
            return None

        try:
            H, W, D = int(dims[0]), int(dims[1]), int(dims[2])
            dy = float(spacing[0])
            dx = float(spacing[1])
            dz = float(thickness)
            ox = float(origin[0]) if len(origin) > 0 else 0.0
            oy = float(origin[1]) if len(origin) > 1 else 0.0
            oz = float(origin[2]) if len(origin) > 2 else 0.0
        except (ValueError, TypeError, IndexError):
            return None

        if H <= 0 or W <= 0 or D <= 0 or dy <= 0.0 or dx <= 0.0 or dz <= 0.0:
            return None

        return {
            "H": H, "W": W, "D": D,
            "dy": dy, "dx": dx, "dz": dz,
            "ox": ox, "oy": oy, "oz": oz
        }

    def map_to_geometry(
        self,
        x_mm: float,
        y_mm: float,
        z_mm: float,
        geom: Optional[dict],
        orientation: str = "axial"
    ) -> Optional[tuple[int, float, float]]:
        """Maps physical (X, Y, Z) to (slice_index, u, v) in normalized [0, 1] slice space.

        Returns (slice_idx, u, v) or None if geometry is missing/invalid.
        """
        parsed = self._parse_geometry(geom)
        if parsed is None:
            return None

        H = parsed["H"]
        W = parsed["W"]
        D = parsed["D"]
        dx = parsed["dx"]
        dy = parsed["dy"]
        dz = parsed["dz"]
        ox = parsed["ox"]
        oy = parsed["oy"]
        oz = parsed["oz"]

        orient = orientation.lower()
        if orient == "axial":
            slice_idx = int(np.clip(round((z_mm - oz) / dz), 0, D - 1))
            u = float(np.clip((x_mm - ox) / max(1e-4, (W - 1) * dx), 0.0, 1.0)) if W > 1 else 0.5
            v = float(np.clip((y_mm - oy) / max(1e-4, (H - 1) * dy), 0.0, 1.0)) if H > 1 else 0.5
        elif orient == "coronal":
            slice_idx = int(np.clip(round((y_mm - oy) / dy), 0, H - 1))
            u = float(np.clip((x_mm - ox) / max(1e-4, (W - 1) * dx), 0.0, 1.0)) if W > 1 else 0.5
            v = float(np.clip(1.0 - (z_mm - oz) / max(1e-4, (D - 1) * dz), 0.0, 1.0)) if D > 1 else 0.5
        else:  # sagittal
            slice_idx = int(np.clip(round((x_mm - ox) / dx), 0, W - 1))
            u = float(np.clip((y_mm - oy) / max(1e-4, (H - 1) * dy), 0.0, 1.0)) if H > 1 else 0.5
            v = float(np.clip(1.0 - (z_mm - oz) / max(1e-4, (D - 1) * dz), 0.0, 1.0)) if D > 1 else 0.5

        return (slice_idx, u, v)

    def set_location(
        self,
        x_mm: float,
        y_mm: float,
        z_mm: float,
        current_geom: Optional[dict] = None,
        previous_geom: Optional[dict] = None,
        current_scan_id: Optional[str] = None,
        previous_scan_id: Optional[str] = None,
        patient_mrn: Optional[str] = None
    ) -> bool:
        """Sets the canonical physical location and derives slice indices for both scans."""
        try:
            x_f = float(x_mm)
            y_f = float(y_mm)
            z_f = float(z_mm)
        except (ValueError, TypeError):
            self.clear()
            return False

        if np.isnan(x_f) or np.isnan(y_f) or np.isnan(z_f):
            self.clear()
            return False

        self.physical_x_mm = x_f
        self.physical_y_mm = y_f
        self.physical_z_mm = z_f
        self.current_scan_id = current_scan_id
        self.previous_scan_id = previous_scan_id
        self.current_patient_mrn = patient_mrn
        self.previous_patient_mrn = patient_mrn
        self.is_active = True

        # Derive Current slice indices
        cur_parsed = self._parse_geometry(current_geom)
        if cur_parsed:
            self.current_slice_indices = {
                "axial": int(np.clip(round((z_f - cur_parsed["oz"]) / cur_parsed["dz"]), 0, cur_parsed["D"] - 1)),
                "coronal": int(np.clip(round((y_f - cur_parsed["oy"]) / cur_parsed["dy"]), 0, cur_parsed["H"] - 1)),
                "sagittal": int(np.clip(round((x_f - cur_parsed["ox"]) / cur_parsed["dx"]), 0, cur_parsed["W"] - 1)),
            }
        else:
            self.current_slice_indices = {}

        # Derive Previous slice indices using Previous scan's own geometry
        prev_parsed = self._parse_geometry(previous_geom)
        if prev_parsed:
            self.previous_slice_indices = {
                "axial": int(np.clip(round((z_f - prev_parsed["oz"]) / prev_parsed["dz"]), 0, prev_parsed["D"] - 1)),
                "coronal": int(np.clip(round((y_f - prev_parsed["oy"]) / prev_parsed["dy"]), 0, prev_parsed["H"] - 1)),
                "sagittal": int(np.clip(round((x_f - prev_parsed["ox"]) / prev_parsed["dx"]), 0, prev_parsed["W"] - 1)),
            }
            self.correspondence_available = True
            self.status_message = "Approx. Correspondence"
        else:
            self.previous_slice_indices = {}
            self.correspondence_available = False
            self.status_message = "Physical correspondence unavailable"

        return True

    def get_slice_index(self, scan_type: str = "current", orientation: str = "axial") -> Optional[int]:
        """Returns the derived slice index for 'current' or 'previous' in given orientation."""
        orient = orientation.lower()
        if scan_type.lower() == "current":
            return self.current_slice_indices.get(orient)
        else:
            return self.previous_slice_indices.get(orient)
