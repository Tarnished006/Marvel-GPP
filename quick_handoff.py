"""
quick_handoff.py - Domain model and logic for ICU Feature 7: QUICK HANDOFF.

Provides a compact, deterministic review context tool that helps one clinician rapidly
communicate the current imaging-review workspace context to another clinician.

Strictly summarizes only existing application state:
- Zero AI / Computer Vision / Automated Inference.
- Zero clinical interpretation, recommendations, diagnosis, or progression/regression claims.
- Missing values are explicitly stated as 'None recorded' or 'Not available'.
- Operates as an in-memory snapshot tool without duplicating ICU data in database tables.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple


@dataclass
class QuickHandoff:
    """Immutable snapshot of the current ICU imaging-review workspace state."""
    patient_mrn: str
    patient_display_name: str
    current_scan_id: Optional[str] = None
    previous_scan_id: Optional[str] = None
    current_scan_date: Optional[str] = None
    previous_scan_date: Optional[str] = None
    current_modality: Optional[str] = None
    previous_modality: Optional[str] = None

    same_location_summary: Dict[str, Any] = field(default_factory=dict)
    measurement_summary: Dict[str, Any] = field(default_factory=dict)
    annotation_summary: Dict[str, Any] = field(default_factory=dict)
    difference_review_summary: Dict[str, Any] = field(default_factory=dict)
    device_marker_summary: Dict[str, Any] = field(default_factory=dict)

    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_stale: bool = False

    def generate_text_summary(self) -> str:
        """Generates a clean, factual, deterministic plain-text handoff summary."""
        lines = []
        lines.append("QUICK HANDOFF SUMMARY")
        lines.append(f"Generated: {self.generated_at}")
        if self.is_stale:
            lines.append("Status: [STALE - Workspace context has changed since generation]")
        else:
            lines.append("Status: [CURRENT]")
        lines.append("=" * 48)

        # 1. Patient & Study
        lines.append("\n1. PATIENT & STUDY")
        patient_str = self.patient_display_name if self.patient_display_name else "Not available"
        lines.append(f"Patient: {patient_str}")
        lines.append(f"MRN: {self.patient_mrn if self.patient_mrn else 'Not available'}")

        c_date = self.current_scan_date or "Date not available"
        c_mod = self.current_modality or "Modality not available"
        lines.append(f"Current Scan: {self.current_scan_id or 'Not available'} ({c_date}, {c_mod})")

        if self.previous_scan_id:
            p_date = self.previous_scan_date or "Date not available"
            p_mod = self.previous_modality or "Modality not available"
            lines.append(f"Previous Scan: {self.previous_scan_id} ({p_date}, {p_mod})")
        else:
            lines.append("Previous Scan: None available")

        # 2. Difference Review
        lines.append("\n2. DIFFERENCE REVIEW")
        diff = self.difference_review_summary
        if not diff or not diff.get("available", False):
            lines.append(f"Status: {diff.get('status', 'Difference map unavailable')}")
        elif diff.get("active", False):
            lines.append("Status: Active")
            lines.append(f"Threshold: {diff.get('threshold_hu', 50.0):.1f} HU")
            lines.append(f"Changed voxels: {diff.get('changed_voxels', 0):,}")
        else:
            lines.append("Status: Inactive")

        # 3. Same Location
        lines.append("\n3. SAME-LOCATION REVIEW")
        sl = self.same_location_summary
        if sl and sl.get("is_valid", False):
            coords = sl.get("coordinates")
            if coords:
                lines.append(f"Location: ({coords[0]:.1f}, {coords[1]:.1f}, {coords[2]:.1f}) mm")
            else:
                lines.append("Location: Coordinates not available")
            lines.append(f"Status: {sl.get('status', 'Approx. Correspondence')}")
        else:
            lines.append("Location: None selected")
            lines.append(f"Status: {sl.get('status', 'No location selected') if sl else 'No location selected'}")

        # 4. Tracked Measurements
        lines.append("\n4. TRACKED MEASUREMENTS")
        meas = self.measurement_summary
        meas_items = meas.get("items", []) if meas else []
        if not meas_items:
            lines.append("None recorded")
        else:
            lines.append(f"Total: {len(meas_items)}")
            for item in meas_items:
                lbl = item.get("label", "Measurement")
                c_val = item.get("current_mm")
                p_val = item.get("previous_mm")
                diff_val = item.get("difference_mm")
                parts = [f"• {lbl}:"]
                if c_val is not None:
                    parts.append(f"Current: {c_val:.1f} mm")
                if p_val is not None:
                    parts.append(f"Previous: {p_val:.1f} mm")
                if diff_val is not None:
                    parts.append(f"Difference: {diff_val:.1f} mm")
                lines.append("  " + " | ".join(parts))

        # 5. Annotations
        lines.append("\n5. ANNOTATIONS")
        ann = self.annotation_summary
        ann_items = ann.get("items", []) if ann else []
        if not ann_items:
            lines.append("None recorded")
        else:
            lines.append(f"Total: {len(ann_items)}")
            for item in ann_items:
                lbl = item.get("label", "Annotation")
                is_cf = item.get("is_carried", False)
                prefix = "[Carried] " if is_cf else ""
                lines.append(f"• {prefix}{lbl}")

        # 6. Device Markers
        lines.append("\n6. DEVICE MARKERS")
        dm = self.device_marker_summary
        dm_items = dm.get("items", []) if dm else []
        if not dm_items:
            lines.append("None recorded")
        else:
            lines.append(f"Total: {len(dm_items)}")
            for item in dm_items:
                dtype = item.get("device_type", "Other")
                lbl = item.get("label", "")
                coords = item.get("coordinates")
                coord_str = f" at ({coords[0]:.1f}, {coords[1]:.1f}, {coords[2]:.1f}) mm" if coords else ""
                scan_str = f" [Scan: {item.get('scan_id')}]" if item.get("scan_id") else ""
                if lbl and lbl != dtype:
                    lines.append(f"• {dtype} ({lbl}){coord_str}{scan_str}")
                else:
                    lines.append(f"• {dtype}{coord_str}{scan_str}")

        lines.append("\n" + "=" * 48)
        lines.append("Note: This summary reflects user-created workspace review state only.")
        lines.append("No automated clinical assessment or recommendations provided.")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Returns dictionary representation of the snapshot."""
        return {
            "patient_mrn": self.patient_mrn,
            "patient_display_name": self.patient_display_name,
            "current_scan_id": self.current_scan_id,
            "previous_scan_id": self.previous_scan_id,
            "current_scan_date": self.current_scan_date,
            "previous_scan_date": self.previous_scan_date,
            "current_modality": self.current_modality,
            "previous_modality": self.previous_modality,
            "same_location_summary": dict(self.same_location_summary),
            "measurement_summary": dict(self.measurement_summary),
            "annotation_summary": dict(self.annotation_summary),
            "difference_review_summary": dict(self.difference_review_summary),
            "device_marker_summary": dict(self.device_marker_summary),
            "generated_at": self.generated_at,
            "is_stale": self.is_stale,
        }


class QuickHandoffManager:
    """Manages creation, inspection, and invalidation of the active Quick Handoff snapshot."""

    def __init__(self):
        self._snapshot: Optional[QuickHandoff] = None

    def get_snapshot(self) -> Optional[QuickHandoff]:
        """Returns the active handoff snapshot, if any."""
        return self._snapshot

    def clear(self):
        """Clears the active handoff snapshot."""
        self._snapshot = None

    def mark_stale(self):
        """Marks the current snapshot as stale due to workspace changes."""
        if self._snapshot is not None:
            self._snapshot.is_stale = True

    def generate_handoff(
        self,
        patient: Optional[dict] = None,
        current_scan: Optional[dict] = None,
        previous_scan: Optional[dict] = None,
        same_location_review: Optional[Any] = None,
        measurement_tracker: Optional[Any] = None,
        annotation_cf_manager: Optional[Any] = None,
        change_review: Optional[Any] = None,
        device_marker_manager: Optional[Any] = None,
    ) -> QuickHandoff:
        """Constructs a factual, deterministic snapshot from current workspace state."""
        patient_dict = patient or {}
        c_scan = current_scan or {}
        p_scan = previous_scan or {}

        mrn = str(patient_dict.get("mrn", "") or "")
        name = str(patient_dict.get("name", "") or patient_dict.get("patient_name", "") or mrn)

        c_id = str(c_scan.get("id", "") or c_scan.get("file_path", "") or "") or None
        p_id = str(p_scan.get("id", "") or p_scan.get("file_path", "") or "") or None

        c_date = c_scan.get("date") or c_scan.get("study_date")
        p_date = p_scan.get("date") or p_scan.get("study_date")

        c_mod = c_scan.get("modality") or "CT"
        p_mod = p_scan.get("modality") or "CT"

        # Same Location Summary
        sl_summary: Dict[str, Any] = {"is_valid": False, "status": "No location selected"}
        if same_location_review:
            if hasattr(same_location_review, "is_point_valid") and same_location_review.is_point_valid():
                coords = same_location_review.get_physical_coordinates()
                sl_summary = {
                    "is_valid": True,
                    "coordinates": coords,
                    "status": getattr(same_location_review, "status_message", "Approx. Correspondence")
                }
            else:
                sl_summary["status"] = getattr(same_location_review, "status_message", "No location selected")

        # Measurement Tracking Summary
        meas_summary: Dict[str, Any] = {"items": []}
        if measurement_tracker:
            items = []
            curr_meas = measurement_tracker.get_current_measurements() if hasattr(measurement_tracker, "get_current_measurements") else []
            prev_meas = measurement_tracker.get_previous_measurements() if hasattr(measurement_tracker, "get_previous_measurements") else []

            # Match or list measurements
            prev_by_label = {m.label: m for m in prev_meas}
            for cm in curr_meas:
                pm = prev_by_label.get(cm.label)
                p_val = pm.value_mm if pm else None
                diff = abs(cm.value_mm - p_val) if p_val is not None else None
                items.append({
                    "label": cm.label,
                    "current_mm": cm.value_mm,
                    "previous_mm": p_val,
                    "difference_mm": diff
                })

            # Check previous measurements not present in current
            curr_labels = {cm.label for cm in curr_meas}
            for pm in prev_meas:
                if pm.label not in curr_labels:
                    items.append({
                        "label": pm.label,
                        "current_mm": None,
                        "previous_mm": pm.value_mm,
                        "difference_mm": None
                    })

            meas_summary["items"] = items

        # Annotation Summary
        ann_summary: Dict[str, Any] = {"items": []}
        if annotation_cf_manager:
            items = []
            annots = annotation_cf_manager.get_current_annotations() if hasattr(annotation_cf_manager, "get_current_annotations") else []
            if not annots and hasattr(annotation_cf_manager, "get_annotations_for_patient"):
                annots = annotation_cf_manager.get_annotations_for_patient(mrn)
            for a in annots:
                is_cf = a.is_carried_forward() if hasattr(a, "is_carried_forward") else False
                items.append({
                    "id": a.id,
                    "label": a.label,
                    "is_carried": is_cf,
                    "scan_id": a.scan_id
                })
            ann_summary["items"] = items

        # Difference Review Summary
        diff_summary: Dict[str, Any] = {
            "active": False,
            "available": False,
            "threshold_hu": 50.0,
            "changed_voxels": 0,
            "status": "Difference map unavailable"
        }
        if change_review:
            diff_summary["active"] = getattr(change_review, "enabled", False)
            diff_summary["available"] = getattr(change_review, "difference_available", False)
            diff_summary["threshold_hu"] = getattr(change_review, "threshold", 50.0)
            diff_summary["status"] = getattr(change_review, "status_message", "Inactive")
            if hasattr(change_review, "get_changed_voxel_count") and diff_summary["available"]:
                diff_summary["changed_voxels"] = change_review.get_changed_voxel_count()

        # Device Marker Summary
        dm_summary: Dict[str, Any] = {"items": []}
        if device_marker_manager:
            items = []
            markers = device_marker_manager.get_markers_for_active_scan() if hasattr(device_marker_manager, "get_markers_for_active_scan") else []
            for m in markers:
                items.append({
                    "id": m.id,
                    "device_type": m.device_type,
                    "label": m.label,
                    "coordinates": m.physical_coordinates if hasattr(m, "physical_coordinates") else None,
                    "scan_id": m.scan_id
                })
            dm_summary["items"] = items

        self._snapshot = QuickHandoff(
            patient_mrn=mrn,
            patient_display_name=name,
            current_scan_id=c_id,
            previous_scan_id=p_id,
            current_scan_date=str(c_date) if c_date else None,
            previous_scan_date=str(p_date) if p_date else None,
            current_modality=str(c_mod) if c_mod else None,
            previous_modality=str(p_mod) if p_mod else None,
            same_location_summary=sl_summary,
            measurement_summary=meas_summary,
            annotation_summary=ann_summary,
            difference_review_summary=diff_summary,
            device_marker_summary=dm_summary,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            is_stale=False,
        )
        return self._snapshot
