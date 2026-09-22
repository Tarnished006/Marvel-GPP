"""
annotation_carry_forward.py - Core domain model and spatial mapping for ICU Feature 4: ANNOTATION CARRY-FORWARD.

Allows a clinician to take an existing user-created spatial annotation from one scan (Current or Previous)
and carry it forward to the corresponding physical location in the other scan for the same patient.

In strict adherence to medical safety specifications:
- Direct physical-coordinate carry-forward is a geometric navigation aid only.
- Zero clinical interpretation, no diagnostic claims, no claim of progression/regression/improvement/deterioration.
- When direct physical mapping succeeds: "Approx. Correspondence".
- When spatial correspondence cannot be established: "Physical correspondence unavailable".
- Physical coordinates (X, Y, Z) in mm are canonical; target slice indices are derived from target geometry.
- Carry-forward creates a target annotation record without mutating the source annotation.
- Strictly isolated by patient MRN and scan ID.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
import uuid
import numpy as np


@dataclass
class ScanAnnotation:
    """Represents a clinically created spatial annotation associated with a specific scan and patient."""
    id: str
    patient_mrn: str
    scan_id: str
    label: str
    text: str = ""
    physical_x_mm: float = 0.0
    physical_y_mm: float = 0.0
    physical_z_mm: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def physical_coordinates(self) -> Tuple[float, float, float]:
        return (float(self.physical_x_mm), float(self.physical_y_mm), float(self.physical_z_mm))

    @property
    def source_scan_id(self) -> str:
        return str(self.metadata.get("source_scan_id", self.scan_id))

    @property
    def target_scan_id(self) -> Optional[str]:
        return self.metadata.get("target_scan_id")

    def is_carried_forward(self) -> bool:
        return bool(self.metadata.get("carried_forward", False))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "patient_mrn": self.patient_mrn,
            "scan_id": self.scan_id,
            "label": self.label,
            "text": self.text,
            "physical_x_mm": self.physical_x_mm,
            "physical_y_mm": self.physical_y_mm,
            "physical_z_mm": self.physical_z_mm,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScanAnnotation":
        return cls(
            id=str(data.get("id", "")),
            patient_mrn=str(data.get("patient_mrn", "")),
            scan_id=str(data.get("scan_id", "")),
            label=str(data.get("label", "Annotation")),
            text=str(data.get("text", "")),
            physical_x_mm=float(data.get("physical_x_mm", 0.0)),
            physical_y_mm=float(data.get("physical_y_mm", 0.0)),
            physical_z_mm=float(data.get("physical_z_mm", 0.0)),
            created_at=str(data.get("created_at", datetime.now(timezone.utc).isoformat())),
            metadata=dict(data.get("metadata", {})),
        )


class AnnotationCarryForwardManager:
    """Manages spatial annotations and handles carry-forward mapping between scans for the active patient."""

    def __init__(self):
        self.patient_mrn: Optional[str] = None
        self.current_scan_id: Optional[str] = None
        self.previous_scan_id: Optional[str] = None
        self.current_geom: Optional[dict] = None
        self.previous_geom: Optional[dict] = None

        self._annotations: Dict[str, ScanAnnotation] = {}
        self.selected_annotation_id: Optional[str] = None
        self.target_scan_id: Optional[str] = None

    def set_patient(self, mrn: Optional[str]):
        """Sets active patient MRN. Clears all annotations and selections if patient changes."""
        mrn_str = str(mrn) if mrn is not None else None
        if self.patient_mrn != mrn_str:
            self.patient_mrn = mrn_str
            self._annotations.clear()
            self.selected_annotation_id = None
            self.target_scan_id = None

    def set_scans(
        self,
        current_scan_id: Optional[str],
        previous_scan_id: Optional[str],
        current_geom: Optional[dict] = None,
        previous_geom: Optional[dict] = None
    ):
        """Sets scan identifiers and geometry dictionaries for Current and Previous scans."""
        self.current_scan_id = str(current_scan_id) if current_scan_id is not None else None
        self.previous_scan_id = str(previous_scan_id) if previous_scan_id is not None else None

        if current_geom is not None:
            self.current_geom = self._normalize_geometry(current_geom)
        if previous_geom is not None:
            self.previous_geom = self._normalize_geometry(previous_geom)

        # Validate selection
        if self.selected_annotation_id and self.selected_annotation_id not in self._annotations:
            self.selected_annotation_id = None
            self.target_scan_id = None
        elif self.selected_annotation_id:
            # Recompute target scan ID
            self._update_target_scan_id()

    def _normalize_geometry(self, geom: dict) -> dict:
        """Ensures geometry contains required spatial parameters."""
        dims = geom.get("dims") or (geom.get("H", 1), geom.get("W", 1), geom.get("D", 1))
        H = int(dims[0]) if len(dims) > 0 else 1
        W = int(dims[1]) if len(dims) > 1 else 1
        D = int(dims[2]) if len(dims) > 2 else 1

        spacing = geom.get("spacing") or (geom.get("dy", 1.0), geom.get("dx", 1.0))
        dy = float(spacing[0]) if len(spacing) > 0 else 1.0
        dx = float(spacing[1]) if len(spacing) > 1 else 1.0
        dz = float(geom.get("thickness") or geom.get("dz") or 1.0)

        origin = geom.get("origin") or (geom.get("ox", 0.0), geom.get("oy", 0.0), geom.get("oz", 0.0))
        ox = float(origin[0]) if len(origin) > 0 else 0.0
        oy = float(origin[1]) if len(origin) > 1 else 0.0
        oz = float(origin[2]) if len(origin) > 2 else 0.0

        return {
            "H": H, "W": W, "D": D,
            "dx": dx, "dy": dy, "dz": dz,
            "ox": ox, "oy": oy, "oz": oz
        }

    def add_annotation(self, annot: ScanAnnotation) -> bool:
        """Adds an annotation. Enforces patient MRN matching."""
        if self.patient_mrn and annot.patient_mrn != self.patient_mrn:
            return False

        self._annotations[annot.id] = annot
        if self.selected_annotation_id is None:
            self.select_annotation(annot.id)
        return True

    def remove_annotation(self, annotation_id: str) -> bool:
        """Removes a specific annotation without affecting unrelated annotations."""
        if annotation_id in self._annotations:
            del self._annotations[annotation_id]
            if self.selected_annotation_id == annotation_id:
                self.selected_annotation_id = None
                self.target_scan_id = None
                remaining = list(self._annotations.keys())
                if remaining:
                    self.select_annotation(remaining[0])
            return True
        return False

    def select_annotation(self, annotation_id: Optional[str]):
        """Selects an annotation by ID and configures default target scan."""
        if annotation_id is None:
            self.selected_annotation_id = None
            self.target_scan_id = None
            return

        if annotation_id in self._annotations:
            self.selected_annotation_id = annotation_id
            self._update_target_scan_id()

    def _update_target_scan_id(self):
        """Sets default target scan based on source annotation's scan ID."""
        annot = self.get_selected_annotation()
        if not annot:
            self.target_scan_id = None
            return

        if annot.scan_id == self.current_scan_id:
            self.target_scan_id = self.previous_scan_id
        elif annot.scan_id == self.previous_scan_id:
            self.target_scan_id = self.current_scan_id
        else:
            self.target_scan_id = self.current_scan_id or self.previous_scan_id

    def get_annotation(self, annotation_id: str) -> Optional[ScanAnnotation]:
        """Returns annotation by ID."""
        return self._annotations.get(str(annotation_id))

    def get_annotations_for_patient(self, mrn: str) -> List[ScanAnnotation]:
        """Returns all annotations belonging to a specific patient MRN."""
        m = str(mrn)
        return [a for a in self._annotations.values() if a.patient_mrn == m]

    def get_selected_annotation(self) -> Optional[ScanAnnotation]:
        if self.selected_annotation_id:
            return self._annotations.get(self.selected_annotation_id)
        return None

    def get_annotations_for_scan(self, scan_id: Optional[str]) -> List[ScanAnnotation]:
        if not scan_id:
            return []
        s_id = str(scan_id)
        return [
            a for a in self._annotations.values()
            if a.scan_id == s_id and (not self.patient_mrn or a.patient_mrn == self.patient_mrn)
        ]

    def get_current_annotations(self) -> List[ScanAnnotation]:
        return self.get_annotations_for_scan(self.current_scan_id)

    def get_previous_annotations(self) -> List[ScanAnnotation]:
        return self.get_annotations_for_scan(self.previous_scan_id)

    def get_geometry_for_scan(self, scan_id: Optional[str]) -> Optional[dict]:
        if not scan_id:
            return None
        s_id = str(scan_id)
        if s_id == self.current_scan_id:
            return self.current_geom
        if s_id == self.previous_scan_id:
            return self.previous_geom
        return None

    def is_point_within_geometry(self, x: float, y: float, z: float, geom: Optional[dict]) -> bool:
        """Checks whether physical (x, y, z) falls within the physical bounding box of the volume."""
        if not geom:
            return False
        H, W, D = geom["H"], geom["W"], geom["D"]
        dx, dy, dz = geom["dx"], geom["dy"], geom["dz"]
        ox, oy, oz = geom["ox"], geom["oy"], geom["oz"]

        # Standard (H=Y, W=X, D=Z)
        min_x, max_x = ox, ox + max(0, W - 1) * dx
        min_y, max_y = oy, oy + max(0, H - 1) * dy
        min_z, max_z = oz, oz + max(0, D - 1) * dz

        # Allow slight boundary tolerance (1 voxel)
        tol_x, tol_y, tol_z = dx, dy, dz
        if (min_x - tol_x <= x <= max_x + tol_x and
            min_y - tol_y <= y <= max_y + tol_y and
            min_z - tol_z <= z <= max_z + tol_z):
            return True

        # Fallback if dims were provided in transposed order (D, H, W)
        min_x2, max_x2 = ox, ox + max(0, D - 1) * dx
        min_y2, max_y2 = oy, oy + max(0, W - 1) * dy
        min_z2, max_z2 = oz, oz + max(0, H - 1) * dz
        return (min_x2 - tol_x <= x <= max_x2 + tol_x and
                min_y2 - tol_y <= y <= max_y2 + tol_y and
                min_z2 - tol_z <= z <= max_z2 + tol_z)

    def get_derived_slice(self, scan_id: Optional[str], orientation: str = "axial") -> Optional[int]:
        """Derives the slice index for the specified scan from the selected annotation's physical coordinates."""
        annot = self.get_selected_annotation()
        if not annot:
            return None

        geom = self.get_geometry_for_scan(scan_id)
        if not geom:
            return None

        x, y, z = annot.physical_coordinates
        H, W, D = geom["H"], geom["W"], geom["D"]
        dx, dy, dz = geom["dx"], geom["dy"], geom["dz"]
        ox, oy, oz = geom["ox"], geom["oy"], geom["oz"]

        orient = orientation.lower()
        if orient == "axial":
            if dz <= 0:
                return None
            idx = int(round((z - oz) / dz))
            return int(np.clip(idx, 0, D - 1))
        elif orient == "coronal":
            if dy <= 0:
                return None
            idx = int(round((y - oy) / dy))
            return int(np.clip(idx, 0, H - 1))
        elif orient == "sagittal":
            if dx <= 0:
                return None
            idx = int(round((x - ox) / dx))
            return int(np.clip(idx, 0, W - 1))
        return None

    def can_carry_forward(self, target_scan_id: Optional[str] = None) -> Tuple[bool, str]:
        """Evaluates whether the selected annotation can be carried forward to target scan.
        
        Returns:
            (can_carry: bool, status_message: str)
            Status message is either 'Approx. Correspondence' or 'Physical correspondence unavailable'.
        """
        annot = self.get_selected_annotation()
        if not annot:
            return False, "Physical correspondence unavailable"

        tgt_id = str(target_scan_id) if target_scan_id is not None else self.target_scan_id
        if not tgt_id:
            return False, "Physical correspondence unavailable"

        if annot.scan_id == tgt_id:
            return False, "Physical correspondence unavailable"

        target_geom = self.get_geometry_for_scan(tgt_id)
        if not target_geom:
            return False, "Physical correspondence unavailable"

        x, y, z = annot.physical_coordinates
        if not self.is_point_within_geometry(x, y, z, target_geom):
            return False, "Physical correspondence unavailable"

        return True, "Approx. Correspondence"

    def carry_forward(self, target_scan_id: Optional[str] = None) -> Optional[ScanAnnotation]:
        """Carries forward selected annotation to target scan, creating a new target annotation record.
        
        Does NOT mutate or overwrite the source annotation.
        Both retain patient MRN and scan ID.
        """
        can_cf, status = self.can_carry_forward()
        if not can_cf:
            return None

        source_annot = self.get_selected_annotation()
        if not source_annot:
            return None

        tgt_id = target_scan_id or self.target_scan_id
        if not tgt_id:
            return None

        # Create new carried-forward annotation
        new_id = f"annot_cf_{uuid.uuid4().hex[:8]}"
        now_str = datetime.now(timezone.utc).isoformat()
        metadata = dict(source_annot.metadata)
        metadata.update({
            "source_scan_id": source_annot.scan_id,
            "source_annotation_id": source_annot.id,
            "carried_forward": True,
            "status": "Approx. Correspondence",
            "carried_at": now_str
        })

        carried = ScanAnnotation(
            id=new_id,
            patient_mrn=source_annot.patient_mrn,
            scan_id=str(tgt_id),
            label=f"[Carried] {source_annot.label}",
            text=source_annot.text,
            physical_x_mm=source_annot.physical_x_mm,
            physical_y_mm=source_annot.physical_y_mm,
            physical_z_mm=source_annot.physical_z_mm,
            created_at=now_str,
            metadata=metadata
        )

        self.add_annotation(carried)
        return carried

    def clear(self):
        """Clears manager state completely."""
        self.patient_mrn = None
        self.current_scan_id = None
        self.previous_scan_id = None
        self.current_geom = None
        self.previous_geom = None
        self._annotations.clear()
        self.selected_annotation_id = None
        self.target_scan_id = None
