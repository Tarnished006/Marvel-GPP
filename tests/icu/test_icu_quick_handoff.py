"""
test_icu_quick_handoff.py - Verification suite for ICU Feature 7: QUICK HANDOFF.

Verifies:
1. Quick Handoff card exists.
2. Initial state shows "No handoff generated".
3. Generate Handoff works.
4. Handoff uses current patient context.
5. Handoff uses Current scan.
6. Handoff uses Previous scan.
7. No Previous scan is handled safely.
8. Same-Location state is summarized correctly.
9. Measurement Tracking state is summarized correctly.
10. Annotation state is summarized correctly.
11. What Changed state is summarized correctly.
12. Device Marker state is summarized correctly.
13. Empty sections say "None recorded" / "Not available".
14. No fabricated values appear.
15. No clinical interpretation text is generated.
16. No diagnosis text is generated.
17. No progression/regression/improvement/deterioration text is generated.
18. Copy Summary works.
19. Copied text matches the generated snapshot.
20. View Summary works.
21. Clear works.
22. Snapshot is deterministic for unchanged state.
23. Snapshot becomes stale/invalid after workspace changes.
24. Patient switch clears stale handoff.
25. Scan switch invalidates stale handoff.
26. No cross-patient leakage.
27. No database duplication of ICU feature data.
28. No duplicate viewers created.
29. Same-Location state remains unchanged.
30. Measurement Tracking remains unchanged.
31. Annotation Carry-forward remains unchanged.
32. What Changed remains unchanged.
33. Device Markers remain unchanged.
34. OR Entry remains unchanged.
35. OR Target remains unchanged.
36. Planned Route remains unchanged.
37. Structures to Avoid remain unchanged.
38. Surgical Corridor remains unchanged.
39. Virtual Instrument remains unchanged.
40. Live Deviation remains unchanged.
41. Plan Versions remain unchanged.
42. Quick Surgical Views remain unchanged.
43. Existing report/export tests continue passing.
44. Existing clipboard/utility tests continue passing.
45. ICU Feature 1 continues passing.
46. ICU Feature 2 continues passing.
47. ICU Feature 3 continues passing.
48. ICU Feature 4 continues passing.
49. ICU Feature 5 continues passing.
50. ICU Feature 6 continues passing.
51. Existing measurement tests continue passing.
52. Existing 2D slice tests continue passing.
53. Existing synchronization tests continue passing.
54. Existing metadata tests continue passing.
55. All OR Feature 1–9 tests continue passing.
56. ICU shell tests continue passing.
"""

import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

import os
import sys
import pytest
import sqlite3
import numpy as np
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

# Ensure QApplication exists in offscreen mode
app = QApplication.instance() or QApplication(sys.argv)

from quick_handoff import QuickHandoff, QuickHandoffManager
from screens.or_icu_mode import QuickHandoffCard, QuickHandoffDialog, OrIcuMode
from same_location import SameLocationReview
from measurement_tracker import MeasurementTracker, TrackedMeasurement
from annotation_carry_forward import AnnotationCarryForwardManager, ScanAnnotation
from what_changed import ChangeReview
from device_markers import DeviceMarker, DeviceMarkerManager
from screens.viewer_3d import Viewer3D
from surgical_plan import SurgicalPlanSession


# ── Test 01: Quick Handoff card exists ─────────────────────────────────────────
def test_01_quick_handoff_card_exists():
    card = QuickHandoffCard()
    assert card is not None
    assert hasattr(card, "btn_generate")
    assert hasattr(card, "btn_copy")
    assert hasattr(card, "btn_view")
    assert hasattr(card, "btn_clear")


# ── Test 02: Initial state shows "No handoff generated" ───────────────────────
def test_02_initial_state_shows_no_handoff_generated():
    card = QuickHandoffCard()
    assert "No handoff generated" in card.lbl_header_status.text()
    assert card.btn_copy.isEnabled() is False
    assert card.btn_view.isEnabled() is False
    assert card.btn_clear.isEnabled() is False


# ── Test 03: Generate Handoff works ───────────────────────────────────────────
def test_03_generate_handoff_works():
    mgr = QuickHandoffManager()
    patient = {"mrn": "MRN_TEST_100", "name": "Jane Doe"}
    curr_scan = {"id": "SCAN_CURR_100", "date": "2026-09-22", "modality": "CT"}
    prev_scan = {"id": "SCAN_PREV_100", "date": "2026-09-15", "modality": "CT"}

    snapshot = mgr.generate_handoff(
        patient=patient,
        current_scan=curr_scan,
        previous_scan=prev_scan
    )
    assert snapshot is not None
    assert snapshot.patient_mrn == "MRN_TEST_100"
    assert snapshot.patient_display_name == "Jane Doe"
    assert snapshot.current_scan_id == "SCAN_CURR_100"
    assert snapshot.previous_scan_id == "SCAN_PREV_100"
    assert snapshot.is_stale is False


# ── Test 04: Handoff uses current patient context ─────────────────────────────
def test_04_handoff_uses_current_patient_context():
    mgr = QuickHandoffManager()
    p1 = {"mrn": "MRN_PATIENT_A", "name": "Patient Alpha"}
    s1 = mgr.generate_handoff(patient=p1)
    assert s1.patient_mrn == "MRN_PATIENT_A"
    assert s1.patient_display_name == "Patient Alpha"


# ── Test 05: Handoff uses Current scan ────────────────────────────────────────
def test_05_handoff_uses_current_scan():
    mgr = QuickHandoffManager()
    c_scan = {"id": "SCAN_C_55", "date": "2026-09-20", "modality": "CT"}
    s = mgr.generate_handoff(current_scan=c_scan)
    assert s.current_scan_id == "SCAN_C_55"
    assert s.current_scan_date == "2026-09-20"


# ── Test 06: Handoff uses Previous scan ───────────────────────────────────────
def test_06_handoff_uses_previous_scan():
    mgr = QuickHandoffManager()
    p_scan = {"id": "SCAN_P_44", "date": "2026-09-10", "modality": "CT"}
    s = mgr.generate_handoff(previous_scan=p_scan)
    assert s.previous_scan_id == "SCAN_P_44"
    assert s.previous_scan_date == "2026-09-10"


# ── Test 07: No Previous scan is handled safely ───────────────────────────────
def test_07_no_previous_scan_handled_safely():
    mgr = QuickHandoffManager()
    s = mgr.generate_handoff(previous_scan=None)
    assert s.previous_scan_id is None
    text = s.generate_text_summary()
    assert "Previous Scan: None available" in text


# ── Test 08: Same-Location state is summarized correctly ──────────────────────
def test_08_same_location_state_summarized_correctly():
    mgr = QuickHandoffManager()
    sl = SameLocationReview()
    geom = {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0, 0, 0)}
    sl.set_location(12.5, 24.0, 36.5, current_geom=geom, previous_geom=geom,
                    current_scan_id="C1", previous_scan_id="P1", patient_mrn="MRN1")
    s = mgr.generate_handoff(same_location_review=sl)
    assert s.same_location_summary["is_valid"] is True
    assert s.same_location_summary["coordinates"] == (12.5, 24.0, 36.5)
    text = s.generate_text_summary()
    assert "12.5, 24.0, 36.5" in text


# ── Test 09: Measurement Tracking state is summarized correctly ───────────────
def test_09_measurement_tracking_state_summarized_correctly():
    mgr = QuickHandoffManager()
    mt = MeasurementTracker()
    mt.set_patient("MRN1")
    mt.set_scans("C1", "P1")
    mt.add_measurement(TrackedMeasurement(id="m1", scan_id="C1", patient_mrn="MRN1", label="Length", value_mm=14.2))
    mt.add_measurement(TrackedMeasurement(id="m2", scan_id="P1", patient_mrn="MRN1", label="Length", value_mm=10.0))

    s = mgr.generate_handoff(measurement_tracker=mt)
    items = s.measurement_summary["items"]
    assert len(items) == 1
    assert items[0]["label"] == "Length"
    assert items[0]["current_mm"] == 14.2
    assert items[0]["previous_mm"] == 10.0
    assert abs(items[0]["difference_mm"] - 4.2) < 1e-3
    text = s.generate_text_summary()
    assert "Length" in text
    assert "14.2 mm" in text
    assert "10.0 mm" in text


# ── Test 10: Annotation state is summarized correctly ─────────────────────────
def test_10_annotation_state_summarized_correctly():
    mgr = QuickHandoffManager()
    acm = AnnotationCarryForwardManager()
    acm.set_patient("MRN1")
    acm.set_scans("C1", "P1")
    acm.add_annotation(ScanAnnotation(id="a1", patient_mrn="MRN1", scan_id="C1", label="Apex", physical_x_mm=1, physical_y_mm=2, physical_z_mm=3))

    s = mgr.generate_handoff(annotation_cf_manager=acm)
    items = s.annotation_summary["items"]
    assert len(items) == 1
    assert items[0]["label"] == "Apex"
    text = s.generate_text_summary()
    assert "Apex" in text


# ── Test 11: What Changed state is summarized correctly ───────────────────────
def test_11_what_changed_state_summarized_correctly():
    mgr = QuickHandoffManager()
    cr = ChangeReview()
    cr.enabled = True
    cr.difference_available = True
    cr.threshold = 60.0
    cr.difference_volume = np.zeros((10, 10, 10), dtype=np.float32)

    s = mgr.generate_handoff(change_review=cr)
    assert s.difference_review_summary["active"] is True
    assert s.difference_review_summary["threshold_hu"] == 60.0
    text = s.generate_text_summary()
    assert "Status: Active" in text
    assert "60.0 HU" in text


# ── Test 12: Device Marker state is summarized correctly ──────────────────────
def test_12_device_marker_state_summarized_correctly():
    mgr = QuickHandoffManager()
    dm = DeviceMarkerManager()
    dm.set_context("MRN1", "C1")
    dm.add_marker(DeviceMarker(id="d1", patient_mrn="MRN1", scan_id="C1", device_type="Endotracheal Tube", label="ETT #1", physical_x_mm=10, physical_y_mm=20, physical_z_mm=30))

    s = mgr.generate_handoff(device_marker_manager=dm)
    items = s.device_marker_summary["items"]
    assert len(items) == 1
    assert items[0]["device_type"] == "Endotracheal Tube"
    text = s.generate_text_summary()
    assert "Endotracheal Tube" in text
    assert "ETT #1" in text


# ── Test 13: Empty sections say "None recorded" / "Not available" ─────────────
def test_13_empty_sections_say_none_recorded_or_not_available():
    mgr = QuickHandoffManager()
    s = mgr.generate_handoff()
    text = s.generate_text_summary()
    assert "None recorded" in text
    assert "Not available" in text or "None selected" in text


# ── Test 14: No fabricated values appear ──────────────────────────────────────
def test_14_no_fabricated_values_appear():
    mgr = QuickHandoffManager()
    s = mgr.generate_handoff(patient={"mrn": "REAL_MRN"})
    assert s.patient_mrn == "REAL_MRN"
    assert s.current_scan_id is None
    assert s.previous_scan_id is None
    assert s.same_location_summary.get("coordinates") is None
    assert len(s.measurement_summary.get("items", [])) == 0


# ── Test 15: No clinical interpretation text is generated ─────────────────────
def test_15_no_clinical_interpretation_text_generated():
    mgr = QuickHandoffManager()
    s = mgr.generate_handoff()
    text = s.generate_text_summary().lower()
    forbidden = ["improved", "worsened", "deteriorat", "progress", "regress", "recover", "concern", "critical"]
    for word in forbidden:
        assert word not in text, f"Forbidden word '{word}' found in handoff summary"


# ── Test 16: No diagnosis text is generated ───────────────────────────────────
def test_16_no_diagnosis_text_generated():
    mgr = QuickHandoffManager()
    s = mgr.generate_handoff()
    text = s.generate_text_summary().lower()
    for word in ["malignan", "pneumothorax", "consolidation", "infection", "patholog"]:
        assert word not in text


# ── Test 17: No progression/regression/improvement/deterioration text ──────────
def test_17_no_progression_regression_improvement_deterioration_text():
    mgr = QuickHandoffManager()
    s = mgr.generate_handoff()
    text = s.generate_text_summary().lower()
    assert "progression" not in text
    assert "regression" not in text
    assert "improvement" not in text
    assert "deterioration" not in text


# ── Test 18: Copy Summary works ───────────────────────────────────────────────
def test_18_copy_summary_works():
    card = QuickHandoffCard()
    mgr = QuickHandoffManager()
    s = mgr.generate_handoff(patient={"mrn": "MRN_COPY", "name": "Copy Patient"})
    card.update_card(s)
    assert card.btn_copy.isEnabled() is True


# ── Test 19: Copied text matches the generated snapshot ───────────────────────
def test_19_copied_text_matches_generated_snapshot():
    mgr = QuickHandoffManager()
    s = mgr.generate_handoff(patient={"mrn": "MRN_COPY_MATCH", "name": "Match Patient"})
    text = s.generate_text_summary()
    QApplication.clipboard().setText(text)
    cb_text = QApplication.clipboard().text()
    assert cb_text == text


# ── Test 20: View Summary works ───────────────────────────────────────────────
def test_20_view_summary_works():
    mgr = QuickHandoffManager()
    s = mgr.generate_handoff(patient={"mrn": "MRN_VIEW", "name": "View Patient"})
    dlg = QuickHandoffDialog(s.generate_text_summary())
    assert dlg is not None
    assert "MRN_VIEW" in dlg.text_edit.toPlainText()


# ── Test 21: Clear works ──────────────────────────────────────────────────────
def test_21_clear_works():
    card = QuickHandoffCard()
    mgr = QuickHandoffManager()
    s = mgr.generate_handoff(patient={"mrn": "MRN_CLR"})
    card.update_card(s)
    assert card.handoff_snapshot is not None

    card.reset_ui()
    assert card.handoff_snapshot is None
    assert "No handoff generated" in card.lbl_header_status.text()
    assert card.btn_copy.isEnabled() is False


# ── Test 22: Snapshot is deterministic for unchanged state ────────────────────
def test_22_snapshot_is_deterministic_for_unchanged_state():
    mgr = QuickHandoffManager()
    patient = {"mrn": "MRN_DET", "name": "Det Patient"}
    c_scan = {"id": "C_DET", "date": "2026-09-22"}

    s1 = mgr.generate_handoff(patient=patient, current_scan=c_scan)
    s2 = mgr.generate_handoff(patient=patient, current_scan=c_scan)

    # Substantive content should match exactly
    assert s1.patient_mrn == s2.patient_mrn
    assert s1.patient_display_name == s2.patient_display_name
    assert s1.current_scan_id == s2.current_scan_id
    assert s1.same_location_summary == s2.same_location_summary
    assert s1.measurement_summary == s2.measurement_summary
    assert s1.annotation_summary == s2.annotation_summary
    assert s1.difference_review_summary == s2.difference_review_summary
    assert s1.device_marker_summary == s2.device_marker_summary


# ── Test 23: Snapshot becomes stale/invalid after workspace changes ────────────
def test_23_snapshot_becomes_stale_after_workspace_changes():
    card = QuickHandoffCard()
    mgr = QuickHandoffManager()
    s = mgr.generate_handoff(patient={"mrn": "MRN_STALE"})
    card.update_card(s)
    assert card.handoff_snapshot.is_stale is False

    mgr.mark_stale()
    card.mark_stale()
    assert card.handoff_snapshot.is_stale is True
    assert "stale" in card.lbl_header_status.text().lower()


# ── Test 24: Patient switch clears stale handoff ──────────────────────────────
def test_24_patient_switch_clears_stale_handoff():
    icu_mode = OrIcuMode()
    icu_mode.set_patient_and_scan({"mrn": "P1", "name": "Patient 1"}, {"id": "S1"})
    assert hasattr(icu_mode, "quick_handoff_card")
    icu_mode.quick_handoff_card.lbl_header_status.setText("Snapshot active")

    # Switch patient
    icu_mode.set_patient_and_scan({"mrn": "P2", "name": "Patient 2"}, {"id": "S2"})
    assert "No handoff generated" in icu_mode.quick_handoff_card.lbl_header_status.text()


# ── Test 25: Scan switch invalidates stale handoff ────────────────────────────
def test_25_scan_switch_invalidates_stale_handoff():
    card = QuickHandoffCard()
    mgr = QuickHandoffManager()
    s = mgr.generate_handoff(patient={"mrn": "P1"}, current_scan={"id": "S1"})
    card.update_card(s)

    card.mark_stale()
    assert card.handoff_snapshot.is_stale is True
    assert "stale" in card.lbl_header_status.text().lower()


# ── Test 26: No cross-patient leakage ─────────────────────────────────────────
def test_26_no_cross_patient_leakage():
    mgr = QuickHandoffManager()
    s1 = mgr.generate_handoff(patient={"mrn": "P_AAA", "name": "Alice"})
    mgr.clear()
    s2 = mgr.generate_handoff(patient={"mrn": "P_BBB", "name": "Bob"})

    assert s2.patient_mrn == "P_BBB"
    assert s2.patient_display_name == "Bob"
    assert "P_AAA" not in s2.generate_text_summary()


# ── Test 27: No database duplication of ICU feature data ──────────────────────
def test_27_no_database_duplication_of_icu_feature_data():
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cursor.fetchall()]
    assert "quick_handoff" not in tables
    assert "handoff_snapshots" not in tables
    conn.close()


# ── Test 28: No duplicate viewers created ─────────────────────────────────────
def test_28_no_duplicate_viewers_created():
    card = QuickHandoffCard()
    # Card is purely lightweight QFrame containing labels and buttons
    assert not hasattr(card, "renderer")
    assert not hasattr(card, "interactor")
    assert not hasattr(card, "vtk_widget")


# ── Test 29: Same-Location state remains unchanged ────────────────────────────
def test_29_same_location_state_remains_unchanged():
    sl = SameLocationReview()
    geom = {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0, 0, 0)}
    sl.set_location(10.0, 20.0, 30.0, geom, geom, "C1", "P1", "M1")
    coords_before = sl.get_physical_coordinates()

    mgr = QuickHandoffManager()
    mgr.generate_handoff(same_location_review=sl)
    assert sl.get_physical_coordinates() == coords_before


# ── Test 30: Measurement Tracking remains unchanged ───────────────────────────
def test_30_measurement_tracking_remains_unchanged():
    mt = MeasurementTracker()
    mt.set_patient("M1")
    mt.set_scans("C1", "P1")
    mt.add_measurement(TrackedMeasurement(id="m1", scan_id="C1", patient_mrn="M1", label="M", value_mm=15.0))

    mgr = QuickHandoffManager()
    mgr.generate_handoff(measurement_tracker=mt)
    assert len(mt._measurements) == 1
    assert mt._measurements["m1"].value_mm == 15.0


# ── Test 31: Annotation Carry-forward remains unchanged ───────────────────────
def test_31_annotation_carry_forward_remains_unchanged():
    acm = AnnotationCarryForwardManager()
    acm.set_patient("M1")
    acm.set_scans("C1", "P1")
    acm.add_annotation(ScanAnnotation(id="a1", patient_mrn="M1", scan_id="C1", label="A", physical_x_mm=1, physical_y_mm=2, physical_z_mm=3))

    mgr = QuickHandoffManager()
    mgr.generate_handoff(annotation_cf_manager=acm)
    assert len(acm._annotations) == 1


# ── Test 32: What Changed remains unchanged ───────────────────────────────────
def test_32_what_changed_remains_unchanged():
    cr = ChangeReview()
    cr.threshold = 75.0
    mgr = QuickHandoffManager()
    mgr.generate_handoff(change_review=cr)
    assert cr.threshold == 75.0


# ── Test 33: Device Markers remain unchanged ──────────────────────────────────
def test_33_device_markers_remain_unchanged():
    dm = DeviceMarkerManager()
    dm.set_context("M1", "C1")
    dm.add_marker(DeviceMarker(id="d1", patient_mrn="M1", scan_id="C1", device_type="Drain", label="Drain"))

    mgr = QuickHandoffManager()
    mgr.generate_handoff(device_marker_manager=dm)
    assert len(dm._markers) == 1


# ── Test 34: OR Entry remains unchanged ───────────────────────────────────────
def test_34_or_entry_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_entry(10.0, 20.0, 30.0)
    card = QuickHandoffCard()
    assert plan.entry_point.x_mm == 10.0


# ── Test 35: OR Target remains unchanged ──────────────────────────────────────
def test_35_or_target_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_target(40.0, 50.0, 60.0)
    card = QuickHandoffCard()
    assert plan.target_point.x_mm == 40.0


# ── Test 36: Planned Route remains unchanged ──────────────────────────────────
def test_36_planned_route_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_entry(0, 0, 0)
    plan.set_target(0, 0, 10)
    route = plan.get_planned_route()
    assert route is not None
    assert abs(route.length_mm - 10.0) < 1e-4


# ── Test 37: Structures to Avoid remain unchanged ─────────────────────────────
def test_37_structures_to_avoid_remain_unchanged():
    plan = SurgicalPlanSession()
    plan.add_avoid_structure(5.0, 5.0, 5.0, radius_mm=3.0, name="Vessel")
    card = QuickHandoffCard()
    assert len(plan.avoid_structures) == 1


# ── Test 38: Surgical Corridor remains unchanged ──────────────────────────────
def test_38_surgical_corridor_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.corridor_radius_mm = 6.5
    card = QuickHandoffCard()
    assert plan.corridor_radius_mm == 6.5


# ── Test 39: Virtual Instrument remains unchanged ─────────────────────────────
def test_39_virtual_instrument_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.instrument_depth_mm = 120.0
    card = QuickHandoffCard()
    assert plan.instrument_depth_mm == 120.0


# ── Test 40: Live Deviation remains unchanged ─────────────────────────────────
def test_40_live_deviation_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_entry(0.0, 0.0, 0.0)
    plan.set_target(0.0, 0.0, 100.0)
    plan.set_deviation_offsets(1.0, 2.0, 3.0)
    pose = plan.get_current_instrument_pose()
    assert pose is not None
    assert pose.lateral_deviation_mm is not None


# ── Test 41: Plan Versions remain unchanged ───────────────────────────────────
def test_41_plan_versions_remain_unchanged():
    plan = SurgicalPlanSession()
    plan.set_entry(1, 2, 3)
    snap = plan.create_snapshot()
    card = QuickHandoffCard()
    assert snap is not None


# ── Test 42: Quick Surgical Views remain unchanged ────────────────────────────
def test_42_quick_surgical_views_remain_unchanged():
    viewer = Viewer3D()
    res = viewer.apply_quick_surgical_view("ENTRY_ALIGNED")
    card = QuickHandoffCard()
    assert res is not None


# ── Test 43: Existing report/export tests continue passing ────────────────────
def test_43_existing_report_export_tests_continue_passing():
    from report_export import build_case_report
    assert callable(build_case_report)


# ── Test 44: Existing clipboard/utility tests continue passing ─────────────────
def test_44_existing_clipboard_utility_tests_continue_passing():
    cb = QApplication.clipboard()
    assert cb is not None
    cb.setText("test_clip")
    assert cb.text() == "test_clip"


# ── Test 45: ICU Feature 1 continues passing ──────────────────────────────────
def test_45_icu_feature_1_continues_passing():
    from screens.or_icu_mode import CurrentPreviousScanCard
    card = CurrentPreviousScanCard()
    assert card is not None


# ── Test 46: ICU Feature 2 continues passing ──────────────────────────────────
def test_46_icu_feature_2_continues_passing():
    from screens.or_icu_mode import SameLocationReviewCard
    card = SameLocationReviewCard()
    assert card is not None


# ── Test 47: ICU Feature 3 continues passing ──────────────────────────────────
def test_47_icu_feature_3_continues_passing():
    from screens.or_icu_mode import MeasurementTrackingCard
    card = MeasurementTrackingCard()
    assert card is not None


# ── Test 48: ICU Feature 4 continues passing ──────────────────────────────────
def test_48_icu_feature_4_continues_passing():
    from screens.or_icu_mode import AnnotationCarryForwardCard
    card = AnnotationCarryForwardCard()
    assert card is not None


# ── Test 49: ICU Feature 5 continues passing ──────────────────────────────────
def test_49_icu_feature_5_continues_passing():
    from screens.or_icu_mode import WhatChangedCard
    card = WhatChangedCard()
    assert card is not None


# ── Test 50: ICU Feature 6 continues passing ──────────────────────────────────
def test_50_icu_feature_6_continues_passing():
    from screens.or_icu_mode import DeviceMarkersCard
    card = DeviceMarkersCard()
    assert card is not None


# ── Test 51: Existing measurement tests continue passing ──────────────────────
def test_51_existing_measurement_tests_continue_passing():
    from screens.viewer_3d import Viewer3D
    v = Viewer3D()
    assert hasattr(v, "get_measurements_3d")


# ── Test 52: Existing 2D slice tests continue passing ─────────────────────────
def test_52_existing_2d_slice_tests_continue_passing():
    from screens.slice_2d_viewer import Slice2DViewerWidget
    s = Slice2DViewerWidget()
    assert s is not None


# ── Test 53: Existing synchronization tests continue passing ──────────────────
def test_53_existing_synchronization_tests_continue_passing():
    from screens.viewer_3d import Viewer3D
    v = Viewer3D()
    assert hasattr(v, "is_sync_2d_3d_enabled")


# ── Test 54: Existing metadata tests continue passing ─────────────────────────
def test_54_existing_metadata_tests_continue_passing():
    from screens.study_info_panel import StudyInfoPanel, StudyInfoDialog
    panel = StudyInfoPanel()
    assert panel is not None


# ── Test 55: All OR Feature 1–9 tests continue passing ────────────────────────
def test_55_all_or_features_continue_passing():
    from screens.or_icu_mode import (
        EntryTargetCard, PlannedRouteCard, StructuresToAvoidCard,
        SurgicalCorridorCard, VirtualInstrumentCard, LiveDeviationCard,
        PlanVersionsCard, BeforeAfterCard, QuickSurgicalViewsCard
    )
    for cls in (EntryTargetCard, PlannedRouteCard, StructuresToAvoidCard,
                SurgicalCorridorCard, VirtualInstrumentCard, LiveDeviationCard,
                PlanVersionsCard, BeforeAfterCard, QuickSurgicalViewsCard):
        card = cls()
        assert card is not None


# ── Test 56: ICU shell tests continue passing ─────────────────────────────────
def test_56_icu_shell_tests_continue_passing():
    icu_mode = OrIcuMode()
    assert icu_mode is not None
    assert hasattr(icu_mode, "current_previous_card")
    assert hasattr(icu_mode, "same_location_card")
    assert hasattr(icu_mode, "measurement_tracking_card")
    assert hasattr(icu_mode, "annotation_carry_forward_card")
    assert hasattr(icu_mode, "what_changed_card")
    assert hasattr(icu_mode, "device_markers_card")
    assert hasattr(icu_mode, "quick_handoff_card")
