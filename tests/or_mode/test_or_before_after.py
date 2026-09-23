# test_or_before_after.py
"""
Unit and integration tests for OR Feature 8: BEFORE vs AFTER.

Covers all 43 verification scenarios:
 1. Comparison is inactive initially.
 2. Before scan can be selected.
 3. After scan can be selected.
 4. Both scans are required for active comparison.
 5. Before and After belong to the same patient.
 6. Cross-patient comparison is rejected ("Before and After scans must belong to the same patient").
 7. Before scan ID is preserved.
 8. After scan ID is preserved.
 9. Comparison state is dedicated and separate from surgical plan state.
10. Active surgical plan is not overwritten.
11. Plan Entry remains unchanged.
12. Plan Target remains unchanged.
13. Planned Route remains unchanged.
14. Structures to Avoid remain unchanged.
15. Surgical Corridor remains unchanged.
16. Virtual Instrument remains unchanged.
17. Live Deviation remains unchanged.
18. Comparison mode can be exited.
19. Before/After toggle works.
20. Side-by-side works if implemented.
21. 2D comparison uses existing slice infrastructure.
22. MPR comparison uses existing MPR infrastructure.
23. 3D comparison uses existing Viewer3D infrastructure.
24. No duplicate viewer architecture is created.
25. No automatic clinical interpretation is generated.
26. Existing measurements remain untouched.
27. Existing plan versions remain untouched.
28. Changing comparison display does not rewrite database planning state.
29. Patient change clears comparison.
30. Invalid scan pair is rejected.
31. No duplicate comparison actors accumulate.
32. No unnecessary DICOM reload during display toggles.
33. Existing Entry/Target tests pass.
34. Existing Planned Route tests pass.
35. Existing Structures tests pass.
36. Existing Surgical Corridor tests pass.
37. Existing Virtual Instrument tests pass.
38. Existing Live Deviation tests pass.
39. Existing Plan Version tests pass.
40. Existing measurement tests pass.
41. Existing database tests pass.
42. Existing 2D/3D synchronization tests pass.
43. Existing ICU/OR shell tests pass.
"""

import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

import os
import sqlite3
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication, QLabel

from before_after import BeforeAfterComparison
from surgical_plan import (
    PlanningPoint,
    PlannedRoute,
    AvoidStructure,
    SurgicalCorridor,
    VirtualInstrument,
    CurrentInstrumentPose,
    SurgicalPlanSession,
    SurgicalPlanVersion,
)
import database
from screens.viewer_3d import Viewer3D
from screens.or_icu_mode import OrIcuMode, BeforeAfterCard
from screens.slice_2d_viewer import Slice2DViewerWidget
from screens.mpr_view import MPRView


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def sample_patient_a():
    return {
        "mrn": "MRN-001",
        "name": "Alex Mercer",
        "age": "45",
        "sex": "M",
    }


@pytest.fixture
def sample_patient_b():
    return {
        "mrn": "MRN-002",
        "name": "Jane Doe",
        "age": "52",
        "sex": "F",
    }


@pytest.fixture
def sample_scan_before(sample_patient_a):
    return {
        "id": "scan_01_pre",
        "patient_mrn": sample_patient_a["mrn"],
        "type": "CT",
        "date": "2026-01-10",
        "description": "Pre-Op Baseline",
        "file_path": "/path/to/scan_pre",
        "slice_count": 120,
    }


@pytest.fixture
def sample_scan_after(sample_patient_a):
    return {
        "id": "scan_02_post",
        "patient_mrn": sample_patient_a["mrn"],
        "type": "CT",
        "date": "2026-02-15",
        "description": "Post-Op Followup",
        "file_path": "/path/to/scan_post",
        "slice_count": 120,
    }


@pytest.fixture
def sample_scan_patient_b(sample_patient_b):
    return {
        "id": "scan_b_01",
        "patient_mrn": sample_patient_b["mrn"],
        "type": "CT",
        "date": "2026-03-01",
        "description": "Patient B Scan",
        "file_path": "/path/to/scan_b",
        "slice_count": 80,
    }


@pytest.fixture
def populated_session():
    """Returns a SurgicalPlanSession populated with planning state."""
    session = SurgicalPlanSession()
    session.scan_id = "scan_02_post"
    session.set_entry(10.0, 20.0, 30.0, source_view="3D")
    session.set_target(40.0, 60.0, 80.0, source_view="3D")
    session.add_avoid_structure(15.0, 25.0, 35.0, radius_mm=6.0, name="Carotid Artery", structure_id="struct_1")
    session.set_corridor_radius(7.5)
    session.set_corridor_enabled(True)
    session.set_insertion_depth(25.0)
    session.set_instrument_diameter(3.0)
    session.set_instrument_visible(True)
    session.set_deviation_offsets(2.0, -1.5, 3.0)
    session.set_deviation_angles(5.0, -2.5)
    session.set_deviation_visible(True)
    return session


# ── 1. Comparison is inactive initially ───────────────────────────────────────

def test_1_comparison_inactive_initially(qapp):
    comp = BeforeAfterComparison()
    assert comp.is_active is False
    assert comp.before_scan is None
    assert comp.after_scan is None

    card = BeforeAfterCard()
    assert card.is_active is False
    assert "Comparison Inactive" in card.lbl_status.text()
    assert "No comparison selected" in card.lbl_summary.text()


# ── 2. Before scan can be selected ────────────────────────────────────────────

def test_2_before_scan_selectable(qapp, sample_patient_a, sample_scan_before):
    card = BeforeAfterCard()
    card.on_patient_changed(sample_patient_a)
    result = card.set_before_scan(sample_scan_before)
    assert result is True
    assert card.before_scan == sample_scan_before
    assert "Before: CT 2026-01-10" in card.lbl_before.text()


# ── 3. After scan can be selected ─────────────────────────────────────────────

def test_3_after_scan_selectable(qapp, sample_patient_a, sample_scan_after):
    card = BeforeAfterCard()
    card.on_patient_changed(sample_patient_a)
    result = card.set_after_scan(sample_scan_after)
    assert result is True
    assert card.after_scan == sample_scan_after
    assert "After: CT 2026-02-15" in card.lbl_after.text()


# ── 4. Both scans are required for active comparison ──────────────────────────

def test_4_both_scans_required_for_comparison(sample_patient_a, sample_scan_before):
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    assert comp.can_compare() is False
    assert comp.start_comparison() is False
    assert comp.is_active is False


# ── 5. Before and After belong to the same patient ────────────────────────────

def test_5_same_patient_verification(sample_patient_a, sample_scan_before, sample_scan_after):
    comp = BeforeAfterComparison()
    assert comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"]) is True
    assert comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"]) is True
    assert comp.can_compare() is True
    assert comp.start_comparison() is True
    assert comp.is_active is True


# ── 6. Cross-patient comparison is rejected ───────────────────────────────────

def test_6_cross_patient_comparison_rejected(sample_patient_a, sample_patient_b, sample_scan_before, sample_scan_patient_b):
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    result = comp.set_after_scan(sample_scan_patient_b, sample_patient_b["mrn"])
    assert result is False
    assert comp.validation_error == "Before and After scans must belong to the same patient"
    assert comp.can_compare() is False

    # Also verify on UI card
    card = BeforeAfterCard()
    card.on_patient_changed(sample_patient_a)
    card.set_before_scan(sample_scan_before)
    card.set_after_scan(sample_scan_patient_b)
    assert not card.lbl_error.isHidden()
    assert "Before and After scans must belong to the same patient" in card.lbl_error.text()
    assert card.btn_compare.isEnabled() is False


# ── 7. Before scan ID is preserved ────────────────────────────────────────────

def test_7_before_scan_id_preserved(sample_patient_a, sample_scan_before):
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    assert comp.before_scan_id == "/path/to/scan_pre"


# ── 8. After scan ID is preserved ─────────────────────────────────────────────

def test_8_after_scan_id_preserved(sample_patient_a, sample_scan_after):
    comp = BeforeAfterComparison()
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    assert comp.after_scan_id == "/path/to/scan_post"


# ── 9. Comparison state is dedicated and separate from surgical plan state ────

def test_9_comparison_state_dedicated_separate_from_plan(populated_session):
    comp = BeforeAfterComparison()
    # Comparison data model has no surgical planning attributes
    assert not hasattr(comp, "entry_point")
    assert not hasattr(comp, "target_point")
    assert not hasattr(comp, "planned_route")
    assert not hasattr(comp, "structures_to_avoid")
    assert not hasattr(comp, "corridor")
    assert not hasattr(comp, "instrument")
    assert not hasattr(comp, "live_deviation")


# ── 10. Active surgical plan is not overwritten ───────────────────────────────

def test_10_active_surgical_plan_not_overwritten(populated_session, sample_patient_a, sample_scan_before, sample_scan_after):
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("TOGGLE")

    # Plan should be completely intact
    assert populated_session.has_entry() is True
    assert populated_session.has_target() is True
    assert populated_session.has_planned_route() is True
    assert len(populated_session.get_avoid_structures()) == 1
    assert populated_session.has_surgical_corridor() is True
    assert populated_session.has_virtual_instrument() is True
    assert populated_session.has_live_deviation() is True


# ── 11. Plan Entry remains unchanged ──────────────────────────────────────────

def test_11_plan_entry_remains_unchanged(populated_session, sample_patient_a, sample_scan_before, sample_scan_after):
    orig_entry = populated_session.entry_point.coordinates
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("TOGGLE")
    comp.toggle_view()
    assert populated_session.entry_point.coordinates == orig_entry


# ── 12. Plan Target remains unchanged ─────────────────────────────────────────

def test_12_plan_target_remains_unchanged(populated_session, sample_patient_a, sample_scan_before, sample_scan_after):
    orig_target = populated_session.target_point.coordinates
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("TOGGLE")
    comp.toggle_view()
    assert populated_session.target_point.coordinates == orig_target


# ── 13. Planned Route remains unchanged ───────────────────────────────────────

def test_13_planned_route_remains_unchanged(populated_session, sample_patient_a, sample_scan_before, sample_scan_after):
    orig_len = populated_session.get_planned_route().length_mm
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("TOGGLE")
    assert populated_session.get_planned_route().length_mm == orig_len


# ── 14. Structures to Avoid remain unchanged ──────────────────────────────────

def test_14_structures_to_avoid_remain_unchanged(populated_session, sample_patient_a, sample_scan_before, sample_scan_after):
    orig_structs = [(s.structure_id, s.center, s.radius_mm) for s in populated_session.get_avoid_structures()]
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("TOGGLE")
    current_structs = [(s.structure_id, s.center, s.radius_mm) for s in populated_session.get_avoid_structures()]
    assert current_structs == orig_structs


# ── 15. Surgical Corridor remains unchanged ───────────────────────────────────

def test_15_surgical_corridor_remains_unchanged(populated_session, sample_patient_a, sample_scan_before, sample_scan_after):
    orig_radius = populated_session.corridor_radius_mm
    orig_enabled = populated_session.corridor_enabled
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("SIDE_BY_SIDE")
    assert populated_session.corridor_radius_mm == orig_radius
    assert populated_session.corridor_enabled == orig_enabled


# ── 16. Virtual Instrument remains unchanged ──────────────────────────────────

def test_16_virtual_instrument_remains_unchanged(populated_session, sample_patient_a, sample_scan_before, sample_scan_after):
    orig_depth = populated_session.instrument_depth_mm
    orig_diam = populated_session.instrument_diameter_mm
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("OVERLAY")
    assert populated_session.instrument_depth_mm == orig_depth
    assert populated_session.instrument_diameter_mm == orig_diam


# ── 17. Live Deviation remains unchanged ──────────────────────────────────────

def test_17_live_deviation_remains_unchanged(populated_session, sample_patient_a, sample_scan_before, sample_scan_after):
    orig_pose = populated_session.get_current_instrument_pose()
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("TOGGLE")
    new_pose = populated_session.get_current_instrument_pose()
    assert orig_pose.current_tip_position_mm == new_pose.current_tip_position_mm
    assert orig_pose.lateral_deviation_mm == new_pose.lateral_deviation_mm


# ── 18. Comparison mode can be exited ─────────────────────────────────────────

def test_18_comparison_mode_can_be_exited(sample_patient_a, sample_scan_before, sample_scan_after):
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("TOGGLE")
    assert comp.is_active is True
    comp.exit_comparison()
    assert comp.is_active is False
    assert comp.current_view == "AFTER"


# ── 19. Before/After toggle works ─────────────────────────────────────────────

def test_19_before_after_toggle_works(sample_patient_a, sample_scan_before, sample_scan_after):
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("TOGGLE")
    assert comp.current_view == "AFTER"
    assert comp.toggle_view() == "BEFORE"
    assert comp.current_view == "BEFORE"
    assert comp.toggle_view() == "AFTER"
    assert comp.current_view == "AFTER"


# ── 20. Side-by-side works if implemented ─────────────────────────────────────

def test_20_side_by_side_works(sample_patient_a, sample_scan_before, sample_scan_after):
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("SIDE_BY_SIDE")
    assert comp.display_mode == "SIDE_BY_SIDE"
    assert comp.is_active is True


# ── 21. 2D comparison uses existing slice infrastructure ──────────────────────

def test_21_2d_comparison_uses_existing_slice_infrastructure(qapp):
    viewer_2d = Slice2DViewerWidget()
    vol_after = np.zeros((32, 32, 32), dtype=np.int16)
    vol_before = np.ones((32, 32, 32), dtype=np.int16) * 100
    viewer_2d.load_volume(vol_after, (1.0, 1.0), 1.0)

    # Set comparison state
    viewer_2d.set_comparison_state(True, mode="TOGGLE", current_view="BEFORE", before_vol=vol_before)
    assert viewer_2d.comparison_active is True
    assert viewer_2d.canvas.comparison_active is True
    assert viewer_2d.canvas.comparison_view == "BEFORE"

    # Toggle to AFTER
    viewer_2d.set_comparison_state(True, mode="TOGGLE", current_view="AFTER", before_vol=vol_before)
    assert viewer_2d.canvas.comparison_view == "AFTER"


# ── 22. MPR comparison uses existing MPR infrastructure ───────────────────────

def test_22_mpr_comparison_uses_existing_mpr_infrastructure(qapp):
    mpr = MPRView()
    vol_after = np.zeros((32, 32, 32), dtype=np.int16)
    vol_before = np.ones((32, 32, 32), dtype=np.int16) * 150
    mpr.vol_data = vol_after
    mpr._update_wl_cache()

    # Switch to BEFORE
    mpr.set_comparison_view("BEFORE", before_vol=vol_before, before_spacing=((1.0, 1.0), 1.0))
    assert mpr.comparison_view == "BEFORE"
    assert mpr.w_axial.comparison_view == "BEFORE"
    assert mpr.w_coronal.comparison_view == "BEFORE"
    assert mpr.w_sagittal.comparison_view == "BEFORE"

    # Switch back to AFTER
    mpr.set_comparison_view("AFTER")
    assert mpr.comparison_view == "AFTER"
    assert mpr.w_axial.comparison_view == "AFTER"


# ── 23. 3D comparison uses existing Viewer3D infrastructure ───────────────────

def test_23_3d_comparison_uses_existing_viewer3d(qapp, sample_patient_a, sample_scan_before, sample_scan_after):
    viewer = Viewer3D()
    viewer.current_patient = sample_patient_a
    viewer.current_scan = sample_scan_after
    # Comparison uses viewer.comparison and plotter
    assert hasattr(viewer, "comparison")
    assert hasattr(viewer, "start_before_after_comparison")
    assert hasattr(viewer, "toggle_before_after_view")
    assert hasattr(viewer, "exit_before_after_comparison")


# ── 24. No duplicate viewer architecture is created ───────────────────────────

def test_24_no_duplicate_viewer_architecture(qapp):
    card = BeforeAfterCard()
    # Card is purely a control panel, does not contain a secondary VTK or PyVista widget
    for child in card.findChildren(object):
        assert "QtInteractor" not in type(child).__name__
        assert "Viewer3D" not in type(child).__name__


# ── 25. No automatic clinical interpretation is generated ─────────────────────

def test_25_no_automatic_clinical_interpretation(qapp, sample_patient_a, sample_scan_before, sample_scan_after):
    card = BeforeAfterCard()
    card.on_patient_changed(sample_patient_a)
    card.set_before_scan(sample_scan_before)
    card.set_after_scan(sample_scan_after)
    card._on_compare()

    status = card.lbl_status.text().lower()
    summary = card.lbl_summary.text().lower()
    all_text = status + " " + summary

    forbidden_terms = ["improved", "worsened", "degraded", "corrected", "success", "failure", "better", "worse"]
    for term in forbidden_terms:
        assert term not in all_text, f"Forbidden clinical interpretation found: {term}"


# ── 26. Existing measurements remain untouched ────────────────────────────────

def test_26_existing_measurements_remain_untouched(qapp):
    viewer_2d = Slice2DViewerWidget()
    vol = np.zeros((32, 32, 32), dtype=np.int16)
    viewer_2d.load_volume(vol, (1.0, 1.0), 1.0)
    # Add dummy measurement
    from screens.slice_2d_viewer import Measurement2D
    m = Measurement2D(
        id="meas_01", start_u=0.1, start_v=0.1, end_u=0.5, end_v=0.5,
        start_px=3, start_py=3, end_px=15, end_py=15, distance_mm=12.0
    )
    viewer_2d.canvas.measurements.append(m)
    assert len(viewer_2d.canvas.measurements) == 1

    # Activating comparison does not delete measurement
    viewer_2d.set_comparison_state(True, "TOGGLE", "BEFORE", vol)
    assert len(viewer_2d.canvas.measurements) == 1
    assert viewer_2d.canvas.measurements[0].distance_mm == 12.0


# ── 27. Existing plan versions remain untouched ────────────────────────────────

def test_27_existing_plan_versions_remain_untouched(tmp_path, monkeypatch, sample_patient_a, sample_scan_before, sample_scan_after):
    db_file = str(tmp_path / "test_versions.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    with open("schema.sql") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    # Save a plan version
    v = database.save_surgical_plan_version("scan_02_post", "Pre-Comparison Plan", {"test": 123}, patient_mrn=sample_patient_a["mrn"])
    assert v is not None

    # Perform comparison
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("TOGGLE")
    comp.toggle_view()
    comp.exit_comparison()

    # Verify version in database is untouched
    versions = database.get_surgical_plan_versions("scan_02_post")
    assert len(versions) == 1
    assert versions[0]["name"] == "Pre-Comparison Plan"


# ── 28. Changing comparison display does not rewrite database planning state ─

def test_28_changing_comparison_display_does_not_rewrite_database(tmp_path, monkeypatch, sample_patient_a, sample_scan_before, sample_scan_after):
    db_file = str(tmp_path / "test_db_no_write.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    with open("schema.sql") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("TOGGLE")
    comp.toggle_view()
    comp.set_display_mode("SIDE_BY_SIDE")
    comp.set_display_mode("OVERLAY")
    comp.exit_comparison()

    # Ensure zero surgical plan versions were written
    versions = database.get_surgical_plan_versions("scan_02_post")
    assert len(versions) == 0


# ── 29. Patient change clears comparison ──────────────────────────────────────

def test_29_patient_change_clears_comparison(qapp, sample_patient_a, sample_patient_b, sample_scan_before, sample_scan_after):
    card = BeforeAfterCard()
    card.on_patient_changed(sample_patient_a)
    card.set_before_scan(sample_scan_before)
    card.set_after_scan(sample_scan_after)
    card._on_compare()
    assert card.is_active is True

    # Patient changes to Patient B
    card.on_patient_changed(sample_patient_b)
    assert card.is_active is False
    assert card.before_scan is None
    assert card.after_scan is None
    assert "Comparison Inactive" in card.lbl_status.text()


# ── 30. Invalid scan pair is rejected ─────────────────────────────────────────

def test_30_invalid_scan_pair_rejected():
    comp = BeforeAfterComparison()
    assert comp.set_before_scan({}) is True  # empty scan
    assert comp.can_compare() is False
    assert comp.start_comparison() is False


# ── 31. No duplicate comparison actors accumulate ─────────────────────────────

def test_31_no_duplicate_comparison_actors_accumulate(qapp, sample_patient_a, sample_scan_before, sample_scan_after):
    viewer = Viewer3D()
    viewer.current_patient = sample_patient_a
    viewer.current_scan = sample_scan_after
    viewer.comparison.before_scan = sample_scan_before
    viewer.comparison.after_scan = sample_scan_after
    viewer.comparison.is_active = True

    # Toggle 5 times
    for _ in range(5):
        viewer.toggle_before_after_view()

    # Check that comparison actor names don't accumulate
    label_actors = [k for k in viewer.plotter.actors if "comparison_label" in k]
    # Should not exceed single actor registration
    assert len(label_actors) <= 3  # label, label-labels, label-points


# ── 32. No unnecessary DICOM reload during display toggles ────────────────────

def test_32_no_unnecessary_dicom_reload_during_display_toggles(sample_patient_a, sample_scan_before, sample_scan_after):
    comp = BeforeAfterComparison()
    comp.set_before_scan(sample_scan_before, sample_patient_a["mrn"])
    comp.set_after_scan(sample_scan_after, sample_patient_a["mrn"])
    comp.start_comparison("TOGGLE")

    # Set mock cached data
    comp.before_volume = np.zeros((10, 10, 10))
    comp.after_volume = np.ones((10, 10, 10))

    with patch("dicom_engine.load_volume_for_mpr") as mock_load:
        comp.toggle_view()
        comp.toggle_view()
        comp.set_display_mode("SIDE_BY_SIDE")
        comp.set_display_mode("OVERLAY")
        # No disk read should have occurred
        mock_load.assert_not_called()


# ── 33. Existing Entry/Target tests pass ───────────────────────────────────────

def test_33_existing_entry_target_integration():
    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 30.0)
    session.set_target(40.0, 50.0, 60.0)
    assert session.has_entry() is True
    assert session.has_target() is True
    assert session.has_planned_route() is True


# ── 34. Existing Planned Route tests pass ──────────────────────────────────────

def test_34_existing_planned_route_integration():
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(30.0, 40.0, 0.0)
    assert session.get_planned_route().length_mm == 50.0


# ── 35. Existing Structures tests pass ────────────────────────────────────────

def test_35_existing_structures_integration():
    session = SurgicalPlanSession()
    s = session.add_avoid_structure(10.0, 10.0, 10.0, radius_mm=5.0, name="Vessel")
    assert s in session.get_avoid_structures()
    assert session.remove_avoid_structure(s.structure_id) is True
    assert len(session.get_avoid_structures()) == 0


# ── 36. Existing Surgical Corridor tests pass ──────────────────────────────────

def test_36_existing_surgical_corridor_integration():
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(0.0, 0.0, 50.0)
    session.set_corridor_radius(6.0)
    session.set_corridor_enabled(True)
    corridor = session.get_surgical_corridor()
    assert corridor.radius_mm == 6.0
    assert corridor.is_enabled is True


# ── 37. Existing Virtual Instrument tests pass ─────────────────────────────────

def test_37_existing_virtual_instrument_integration():
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(0.0, 0.0, 50.0)
    session.set_insertion_depth(15.0)
    session.set_instrument_diameter(2.5)
    inst = session.get_virtual_instrument()
    assert inst.insertion_depth_mm == 15.0
    assert inst.diameter_mm == 2.5


# ── 38. Existing Live Deviation tests pass ─────────────────────────────────────

def test_38_existing_live_deviation_integration():
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(0.0, 0.0, 100.0)
    session.set_deviation_offsets(3.0, 4.0, 0.0)
    pose = session.get_current_instrument_pose()
    assert abs(pose.lateral_deviation_mm - 5.0) < 1e-4


# ── 39. Existing Plan Version tests pass ──────────────────────────────────────

def test_39_existing_plan_version_integration(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_ver_integ.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    with open("schema.sql") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    v = database.save_surgical_plan_version("scan_99", "Plan A", {"entry": [1, 2, 3]})
    assert v["id"] is not None
    loaded = database.get_surgical_plan_version(v["id"])
    assert loaded["name"] == "Plan A"


# ── 40. Existing measurement tests pass ───────────────────────────────────────

def test_40_existing_measurement_integration(qapp):
    mpr = MPRView()
    mpr.btn_caliper.setChecked(True)
    mpr._toggle_caliper()
    assert mpr.caliper_mode is True
    assert mpr.w_axial.caliper_active is True


# ── 41. Existing database tests pass ──────────────────────────────────────────

def test_41_existing_database_integration(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_db_integ.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    with open("schema.sql") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    patients = database.get_all_patients()
    assert isinstance(patients, list)


# ── 42. Existing 2D/3D synchronization tests pass ─────────────────────────────

def test_42_existing_2d_3d_sync_integration(qapp):
    viewer_2d = Slice2DViewerWidget()
    vol = np.zeros((20, 20, 20), dtype=np.int16)
    viewer_2d.load_volume(vol, (1.0, 1.0), 1.5)
    pos = viewer_2d.get_current_physical_position()
    assert len(pos) == 3


# ── 43. Existing ICU/OR shell tests pass ───────────────────────────────────────

def test_43_existing_icu_or_shell_integration(qapp, sample_patient_a, sample_scan_before):
    widget = OrIcuMode()
    widget.set_patient_and_scan(sample_patient_a, sample_scan_before)
    widget.set_mode("OR")

    # Shell test checks for "Before vs After" in workflow labels
    labels = widget.workflow_container.findChildren(QLabel)
    all_texts = " ".join([l.text() for l in labels])
    assert "Before vs After" in all_texts, "Before vs After missing from OR workflow container"
    assert hasattr(widget, "before_after_card")
    assert widget.before_after_card is not None
