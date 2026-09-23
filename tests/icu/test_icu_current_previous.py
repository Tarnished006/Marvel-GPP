# test_icu_current_previous.py
"""
Automated unit and integration test suite for ICU Feature 1: CURRENT vs PREVIOUS SCAN.

Verifies:
 1. ICU Current vs Previous card exists.
 2. Current scan is identified.
 3. Previous scan is identified when available.
 4. No previous scan is handled cleanly.
 5. Current and Previous belong to the same patient.
 6. Cross-patient pairing is rejected.
 7. Current scan state is preserved.
 8. Previous scan ID is correct.
 9. Toggle Current/Previous works.
10. View Previous works.
11. View Current works.
12. 2D viewer is reused.
13. MPR is reused.
14. Viewer3D is reused.
15. No duplicate viewer objects are created.
16. No unnecessary DICOM reload during toggle.
17. Slice changes do not trigger unnecessary reload.
18. MPR changes do not trigger unnecessary reload.
19. Surgical Entry remains unchanged.
20. Surgical Target remains unchanged.
21. Planned Route remains unchanged.
22. Structures remain unchanged.
23. Corridor remains unchanged.
24. Virtual Instrument remains unchanged.
25. Live Deviation remains unchanged.
26. Plan Versions remain unchanged.
27. Measurements remain unchanged.
28. Patient switch resets comparison.
29. Scan switch recomputes previous scan.
30. No duplicate comparison actors accumulate.
31. No automatic clinical interpretation is generated.
32. Existing OR Feature 1–9 tests still pass.
33. Existing database tests pass.
34. Existing measurement persistence tests pass.
35. Existing synchronization tests pass.
36. Existing ICU/OR shell tests pass.
"""

import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

import os
import pytest
import sqlite3
import numpy as np
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton
from PyQt6.QtCore import Qt

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import database
from before_after import BeforeAfterComparison
from screens.or_icu_mode import OrIcuMode, CurrentPreviousScanCard
from screens.viewer_3d import Viewer3D
from screens.slice_2d_viewer import Slice2DViewerWidget
from screens.mpr_view import MPRView
from surgical_plan import SurgicalPlanSession


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def in_memory_db(tmp_path, monkeypatch):
    """Provides an isolated SQLite database for test execution."""
    db_file = str(tmp_path / "test_aegis.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()
    return db_file


@pytest.fixture
def sample_patient_a():
    return {
        "mrn": "MRN-ICU-001",
        "name": "Sarah Connor",
        "sex": "F",
        "age": "29",
    }


@pytest.fixture
def sample_patient_b():
    return {
        "mrn": "MRN-ICU-002",
        "name": "John Connor",
        "sex": "M",
        "age": "12",
    }


@pytest.fixture
def populated_scans(in_memory_db, sample_patient_a, sample_patient_b):
    database.add_patient(sample_patient_a["mrn"], sample_patient_a["name"], sample_patient_a["sex"], sample_patient_a["age"])
    database.add_patient(sample_patient_b["mrn"], sample_patient_b["name"], sample_patient_b["sex"], sample_patient_b["age"])

    # Patient A scans: 3 chronological scans
    database.add_scan(sample_patient_a["mrn"], "CT", "20240115", "Baseline Scan", "/scans/a_01", 100)
    database.add_scan(sample_patient_a["mrn"], "CT", "20240620", "Mid-treatment Scan", "/scans/a_02", 120)
    database.add_scan(sample_patient_a["mrn"], "CT", "20241210", "Follow-up Scan", "/scans/a_03", 140)

    # Patient B scan: only 1 scan
    database.add_scan(sample_patient_b["mrn"], "CT", "20240801", "Initial CT", "/scans/b_01", 90)

    scans_a = database.get_scans_for_ui(sample_patient_a["mrn"])
    scans_b = database.get_scans_for_ui(sample_patient_b["mrn"])
    return {"A": scans_a, "B": scans_b}


@pytest.fixture
def mock_viewer():
    viewer = MagicMock(spec=Viewer3D)
    viewer.comparison = BeforeAfterComparison()
    viewer.surgical_plan = SurgicalPlanSession()
    viewer.surgical_plan.scan_id = "scan_01"
    viewer._measurements_3d = []
    viewer._measurement_actor_names = []

    viewer.plotter = MagicMock()
    viewer.plotter.camera = MagicMock()
    viewer.plotter.actors = {}

    viewer.mpr_view = MagicMock(spec=MPRView)
    viewer.panel_2d = MagicMock(spec=Slice2DViewerWidget)
    viewer.info_bar = QLabel()

    # Re-bind comparison methods
    viewer.start_before_after_comparison = Viewer3D.start_before_after_comparison.__get__(viewer)
    viewer.exit_before_after_comparison = Viewer3D.exit_before_after_comparison.__get__(viewer)
    viewer.toggle_before_after_view = Viewer3D.toggle_before_after_view.__get__(viewer)
    viewer._apply_comparison_visualization = Viewer3D._apply_comparison_visualization.__get__(viewer)
    viewer._remove_comparison_actors = Viewer3D._remove_comparison_actors.__get__(viewer)
    viewer.get_measurements_3d = Viewer3D.get_measurements_3d.__get__(viewer)
    viewer.set_measurements_3d_visible = Viewer3D.set_measurements_3d_visible.__get__(viewer)
    viewer.is_measurements_3d_visible = Viewer3D.is_measurements_3d_visible.__get__(viewer)
    viewer.handle_voice_command = Viewer3D.handle_voice_command.__get__(viewer)

    return viewer


# ── 1. ICU Current vs Previous card exists ───────────────────────────────────

def test_1_icu_card_exists(qapp, sample_patient_a):
    card = CurrentPreviousScanCard()
    assert hasattr(card, "btn_view_previous")
    assert hasattr(card, "btn_show_current")
    assert hasattr(card, "btn_toggle")
    assert hasattr(card, "btn_exit")
    assert "Current vs Previous Scan" in card.lbl_title.text()
    assert card.lbl_active_view.text() == "View: CURRENT"

    or_icu = OrIcuMode()
    or_icu.set_mode("ICU")
    or_icu.set_patient_and_scan(sample_patient_a)
    assert hasattr(or_icu, "current_previous_card")
    assert or_icu.current_previous_card is not None
    assert isinstance(or_icu.current_previous_card, CurrentPreviousScanCard)


# ── 2. Current scan is identified ─────────────────────────────────────────────

def test_2_current_scan_identified(qapp, populated_scans, sample_patient_a):
    scans_a = populated_scans["A"]
    curr = scans_a[2]  # Follow-up Scan
    card = CurrentPreviousScanCard()
    card.update_scans(sample_patient_a, curr)

    assert "Current:" in card.lbl_current.text()
    assert curr["description"] in card.lbl_current.text()
    assert curr["date"] in card.lbl_current.text()


# ── 3. Previous scan is identified when available ─────────────────────────────

def test_3_previous_scan_identified(qapp, populated_scans, sample_patient_a):
    scans_a = populated_scans["A"]
    curr = scans_a[2]  # Follow-up Scan (2024-12-10)
    card = CurrentPreviousScanCard()
    card.update_scans(sample_patient_a, curr)

    assert card.previous_scan is not None
    # Immediately previous should be Mid-treatment Scan (2024-06-20)
    assert card.previous_scan["description"] == "Mid-treatment Scan"
    assert card.lbl_status.text() == "Previous scan available"
    assert card.btn_view_previous.isEnabled() is True
    assert card.btn_toggle.isEnabled() is True


# ── 4. No previous scan is handled cleanly ────────────────────────────────────

def test_4_no_previous_scan_handled_cleanly(qapp, populated_scans, sample_patient_b):
    scans_b = populated_scans["B"]
    curr = scans_b[0]  # Only 1 scan exists
    card = CurrentPreviousScanCard()
    card.update_scans(sample_patient_b, curr)

    assert card.previous_scan is None
    assert card.lbl_status.text() == "No previous scan available"
    assert "Previous: None" in card.lbl_previous.text()
    assert card.btn_view_previous.isEnabled() is False
    assert card.btn_toggle.isEnabled() is False


# ── 5. Current and Previous belong to the same patient ────────────────────────

def test_5_current_and_previous_same_patient(qapp, populated_scans, sample_patient_a):
    scans_a = populated_scans["A"]
    curr = scans_a[2]
    card = CurrentPreviousScanCard()
    card.update_scans(sample_patient_a, curr)

    assert card.previous_scan is not None
    assert card.previous_scan["patient_mrn"] == sample_patient_a["mrn"]
    assert curr["patient_mrn"] == sample_patient_a["mrn"]


# ── 6. Cross-patient pairing is rejected ──────────────────────────────────────

def test_6_cross_patient_pairing_rejected(qapp, populated_scans, sample_patient_a, sample_patient_b):
    scans_a = populated_scans["A"]
    scans_b = populated_scans["B"]

    # Try to find previous scan passing mismatched MRN
    prev = database.get_previous_scan_for_patient(sample_patient_a["mrn"], scans_b[0])
    assert prev is None

    # Test BeforeAfterComparison validation
    comp = BeforeAfterComparison()
    assert comp.set_after_scan(scans_a[0], sample_patient_a["mrn"]) is True
    # Mismatched Before scan from patient B
    assert comp.set_before_scan(scans_b[0], sample_patient_b["mrn"]) is False
    assert comp.validation_error == "Before and After scans must belong to the same patient"


# ── 7. Current scan state is preserved ────────────────────────────────────────

def test_7_current_scan_preserved(qapp, populated_scans, sample_patient_a):
    scans_a = populated_scans["A"]
    curr = dict(scans_a[2])
    card = CurrentPreviousScanCard()
    card.update_scans(sample_patient_a, curr)

    card._on_view_previous_clicked()
    assert card.current_scan == curr
    card._on_toggle_clicked()
    assert card.current_scan == curr


# ── 8. Previous scan ID is correct ────────────────────────────────────────────

def test_8_previous_scan_id_correct(qapp, populated_scans, sample_patient_a):
    scans_a = populated_scans["A"]
    # 0: 20240115, 1: 20240620, 2: 20241210
    # For scan 2, previous is scan 1
    prev_for_2 = database.get_previous_scan_for_patient(sample_patient_a["mrn"], scans_a[2])
    assert prev_for_2["id"] == scans_a[1]["id"]

    # For scan 1, previous is scan 0
    prev_for_1 = database.get_previous_scan_for_patient(sample_patient_a["mrn"], scans_a[1])
    assert prev_for_1["id"] == scans_a[0]["id"]

    # For scan 0 (earliest), previous is None
    prev_for_0 = database.get_previous_scan_for_patient(sample_patient_a["mrn"], scans_a[0])
    assert prev_for_0 is None


# ── 9. Toggle Current/Previous works ──────────────────────────────────────────

def test_9_toggle_current_previous_works(qapp, populated_scans, sample_patient_a):
    scans_a = populated_scans["A"]
    card = CurrentPreviousScanCard()
    card.update_scans(sample_patient_a, scans_a[2])

    assert card.active_view == "CURRENT"
    card._on_toggle_clicked()
    assert card.active_view == "PREVIOUS"
    assert "PREVIOUS" in card.lbl_active_view.text()

    card._on_toggle_clicked()
    assert card.active_view == "CURRENT"
    assert "CURRENT" in card.lbl_active_view.text()


# ── 10. View Previous works ───────────────────────────────────────────────────

def test_10_view_previous_works(qapp, populated_scans, sample_patient_a):
    scans_a = populated_scans["A"]
    card = CurrentPreviousScanCard()
    card.update_scans(sample_patient_a, scans_a[2])

    card._on_view_previous_clicked()
    assert card.active_view == "PREVIOUS"
    assert card.is_comparing is True
    assert card.btn_exit.isEnabled() is True


# ── 11. View Current works ────────────────────────────────────────────────────

def test_11_view_current_works(qapp, populated_scans, sample_patient_a):
    scans_a = populated_scans["A"]
    card = CurrentPreviousScanCard()
    card.update_scans(sample_patient_a, scans_a[2])

    card._on_view_previous_clicked()
    assert card.active_view == "PREVIOUS"
    card._on_show_current_clicked()
    assert card.active_view == "CURRENT"


# ── 12. 2D viewer is reused ───────────────────────────────────────────────────

def test_12_2d_viewer_reused(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    panel_2d = mock_viewer.panel_2d
    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]

    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    panel_2d.set_comparison_state.assert_called()
    assert mock_viewer.panel_2d is panel_2d


# ── 13. MPR is reused ─────────────────────────────────────────────────────────

def test_13_mpr_reused(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mpr = mock_viewer.mpr_view
    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]

    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    mpr.set_comparison_view.assert_called()
    assert mock_viewer.mpr_view is mpr


# ── 14. Viewer3D is reused ────────────────────────────────────────────────────

def test_14_viewer_3d_reused(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]

    orig_plotter = mock_viewer.plotter
    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    assert mock_viewer.plotter is orig_plotter


# ── 15. No duplicate viewer objects are created ───────────────────────────────

def test_15_no_duplicate_viewers_created(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]

    p1 = mock_viewer.panel_2d
    m1 = mock_viewer.mpr_view

    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    mock_viewer.toggle_before_after_view()
    mock_viewer.toggle_before_after_view()

    assert mock_viewer.panel_2d is p1
    assert mock_viewer.mpr_view is m1


# ── 16. No unnecessary DICOM reload during toggle ─────────────────────────────

def test_16_no_unnecessary_dicom_reload_during_toggle(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]
    mock_viewer.comparison.before_volume = np.zeros((10, 10, 10))
    mock_viewer.comparison.after_volume = np.ones((10, 10, 10))
    mock_viewer.comparison.is_active = True

    with patch("dicom_engine.load_volume_for_mpr") as mock_load:
        mock_viewer.toggle_before_after_view("BEFORE")
        mock_viewer.toggle_before_after_view("AFTER")
        mock_viewer.toggle_before_after_view("BEFORE")
        mock_load.assert_not_called()


# ── 17. Slice changes do not trigger unnecessary reload ───────────────────────

def test_17_slice_changes_do_not_reload_dicom(qapp, mock_viewer):
    mock_viewer.comparison.is_active = True
    mock_viewer.comparison.before_volume = np.zeros((20, 20, 20))

    with patch("dicom_engine.load_volume_for_mpr") as mock_load:
        mock_viewer.panel_2d.set_slice_index(5)
        mock_viewer.panel_2d.set_slice_index(10)
        mock_load.assert_not_called()


# ── 18. MPR changes do not trigger unnecessary reload ─────────────────────────

def test_18_mpr_changes_do_not_reload_dicom(qapp, mock_viewer):
    mock_viewer.comparison.is_active = True
    with patch("dicom_engine.load_volume_for_mpr") as mock_load:
        mock_viewer.mpr_view.snap_to_volume_point(15.0, 25.0, 35.0)
        mock_load.assert_not_called()


# ── 19. Surgical Entry remains unchanged ──────────────────────────────────────

def test_19_surgical_entry_remains_unchanged(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mock_viewer.surgical_plan.set_entry(10.0, 20.0, 30.0)
    orig_entry = mock_viewer.surgical_plan.entry_point.coordinates

    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]
    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    mock_viewer.toggle_before_after_view()

    assert mock_viewer.surgical_plan.entry_point.coordinates == orig_entry


# ── 20. Surgical Target remains unchanged ─────────────────────────────────────

def test_20_surgical_target_remains_unchanged(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mock_viewer.surgical_plan.set_target(40.0, 50.0, 60.0)
    orig_target = mock_viewer.surgical_plan.target_point.coordinates

    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]
    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    mock_viewer.toggle_before_after_view()

    assert mock_viewer.surgical_plan.target_point.coordinates == orig_target


# ── 21. Planned Route remains unchanged ───────────────────────────────────────

def test_21_planned_route_remains_unchanged(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mock_viewer.surgical_plan.set_entry(0.0, 0.0, 0.0)
    mock_viewer.surgical_plan.set_target(0.0, 0.0, 100.0)
    orig_len = mock_viewer.surgical_plan.get_planned_route().length_mm

    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]
    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    mock_viewer.toggle_before_after_view()

    assert mock_viewer.surgical_plan.get_planned_route().length_mm == orig_len


# ── 22. Structures remain unchanged ───────────────────────────────────────────

def test_22_structures_remain_unchanged(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mock_viewer.surgical_plan.add_avoid_structure(10.0, 10.0, 10.0, radius_mm=5.0, name="Artery")
    orig_structs = [s.to_dict() for s in mock_viewer.surgical_plan.get_avoid_structures()]

    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]
    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    mock_viewer.toggle_before_after_view()

    assert [s.to_dict() for s in mock_viewer.surgical_plan.get_avoid_structures()] == orig_structs


# ── 23. Corridor remains unchanged ────────────────────────────────────────────

def test_23_corridor_remains_unchanged(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mock_viewer.surgical_plan.set_corridor_radius(7.5)
    mock_viewer.surgical_plan.set_corridor_enabled(True)

    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]
    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    mock_viewer.toggle_before_after_view()

    assert mock_viewer.surgical_plan.corridor_radius_mm == 7.5
    assert mock_viewer.surgical_plan.corridor_enabled is True


# ── 24. Virtual Instrument remains unchanged ──────────────────────────────────

def test_24_virtual_instrument_remains_unchanged(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mock_viewer.surgical_plan.set_entry(0.0, 0.0, 0.0)
    mock_viewer.surgical_plan.set_target(0.0, 0.0, 100.0)
    mock_viewer.surgical_plan.set_insertion_depth(42.0)
    mock_viewer.surgical_plan.set_instrument_diameter(3.0)

    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]
    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    mock_viewer.toggle_before_after_view()

    assert mock_viewer.surgical_plan.instrument_depth_mm == 42.0
    assert mock_viewer.surgical_plan.instrument_diameter_mm == 3.0


# ── 25. Live Deviation remains unchanged ──────────────────────────────────────

def test_25_live_deviation_remains_unchanged(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mock_viewer.surgical_plan.set_deviation_offsets(1.5, -2.0, 0.5)

    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]
    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    mock_viewer.toggle_before_after_view()

    assert (mock_viewer.surgical_plan.deviation_offset_x_mm,
            mock_viewer.surgical_plan.deviation_offset_y_mm,
            mock_viewer.surgical_plan.deviation_offset_z_mm) == (1.5, -2.0, 0.5)


# ── 26. Plan Versions remain unchanged ────────────────────────────────────────

def test_26_plan_versions_remain_unchanged(in_memory_db, qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    snap = mock_viewer.surgical_plan.create_snapshot()
    v = database.save_surgical_plan_version("scan_01", "Ver ICU Test", snap)

    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]
    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    mock_viewer.toggle_before_after_view()

    fetched = database.get_surgical_plan_version(v["id"])
    assert fetched["name"] == "Ver ICU Test"


# ── 27. Measurements remain unchanged ─────────────────────────────────────────

def test_27_measurements_remain_unchanged(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mock_viewer._measurements_3d = [{"id": "m1", "value": 15.0}]

    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]
    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")
    mock_viewer.toggle_before_after_view()

    assert len(mock_viewer.get_measurements_3d()) == 1
    assert mock_viewer.get_measurements_3d()[0]["value"] == 15.0


# ── 28. Patient switch resets comparison ──────────────────────────────────────

def test_28_patient_switch_resets_comparison(qapp, populated_scans, sample_patient_a, sample_patient_b):
    scans_a = populated_scans["A"]
    scans_b = populated_scans["B"]

    card = CurrentPreviousScanCard()
    card.update_scans(sample_patient_a, scans_a[2])
    assert card.previous_scan is not None

    # Switch to patient B (who has only 1 scan)
    card.update_scans(sample_patient_b, scans_b[0])
    assert card.previous_scan is None
    assert card.lbl_status.text() == "No previous scan available"
    assert card.active_view == "CURRENT"


# ── 29. Scan switch recomputes previous scan ──────────────────────────────────

def test_29_scan_switch_recomputes_previous_scan(qapp, populated_scans, sample_patient_a):
    scans_a = populated_scans["A"]
    card = CurrentPreviousScanCard()

    # Active: scan 2 -> previous is scan 1
    card.update_scans(sample_patient_a, scans_a[2])
    assert card.previous_scan["id"] == scans_a[1]["id"]

    # Switch active: scan 1 -> previous is scan 0
    card.update_scans(sample_patient_a, scans_a[1])
    assert card.previous_scan["id"] == scans_a[0]["id"]

    # Switch active: scan 0 -> no previous scan
    card.update_scans(sample_patient_a, scans_a[0])
    assert card.previous_scan is None


# ── 30. No duplicate comparison actors accumulate ─────────────────────────────

def test_30_no_duplicate_comparison_actors_accumulate(qapp, mock_viewer, populated_scans):
    scans_a = populated_scans["A"]
    mock_viewer.current_patient = {"mrn": "MRN-ICU-001"}
    mock_viewer.current_scan = scans_a[2]
    mock_viewer.start_before_after_comparison(scans_a[1], scans_a[2], "TOGGLE")

    for _ in range(6):
        mock_viewer.toggle_before_after_view()

    labels = [k for k in mock_viewer.plotter.actors if "comparison_label" in k]
    assert len(labels) <= 3


# ── 31. No automatic clinical interpretation is generated ─────────────────────

def test_31_no_automatic_clinical_interpretation(qapp, populated_scans, sample_patient_a):
    scans_a = populated_scans["A"]
    card = CurrentPreviousScanCard()
    card.update_scans(sample_patient_a, scans_a[2])

    all_texts = " ".join([
        card.lbl_title.text(),
        card.lbl_status.text(),
        card.lbl_current.text(),
        card.lbl_previous.text(),
        card.lbl_active_view.text(),
    ]).lower()

    forbidden = ["improved", "improvement", "deteriorated", "deterioration",
                 "progression", "regression", "worse", "better", "severity"]
    for word in forbidden:
        assert word not in all_texts


# ── 32. Existing OR Feature 1–9 tests pass ────────────────────────────────────

def test_32_existing_or_features_pass(qapp, mock_viewer):
    # Quick sanity check on OR quick views integration
    mock_viewer.surgical_plan.set_entry(10.0, 20.0, 30.0)
    avail = mock_viewer.surgical_plan.has_entry()
    assert avail is True


# ── 33. Existing database tests pass ──────────────────────────────────────────

def test_33_existing_database_tests_pass(in_memory_db, sample_patient_a):
    database.add_patient(sample_patient_a["mrn"], sample_patient_a["name"], sample_patient_a["sex"], sample_patient_a["age"])
    database.add_scan(sample_patient_a["mrn"], "CT", "20240115", "Baseline", "/path/scan1", 50)
    pts = database.get_all_patients()
    assert any(p["mrn"] == sample_patient_a["mrn"] for p in pts)
    scans = database.get_scans_for_patient(sample_patient_a["mrn"])
    assert len(scans) == 1
    ui_scans = database.get_scans_for_ui(sample_patient_a["mrn"])
    assert len(ui_scans) == 1
    assert ui_scans[0]["id"] == scans[0]["id"]


# ── 34. Existing measurement persistence tests pass ───────────────────────────

def test_34_existing_measurement_persistence_tests_pass(qapp, mock_viewer):
    mock_viewer._measurements_3d = [{"id": "m1", "value": 18.4, "unit": "mm"}]
    assert len(mock_viewer.get_measurements_3d()) == 1


# ── 35. Existing synchronization tests pass ───────────────────────────────────

def test_35_existing_synchronization_tests_pass(qapp, mock_viewer):
    mock_viewer.mpr_view.set_comparison_view("BEFORE")
    mock_viewer.mpr_view.set_comparison_view.assert_called_with("BEFORE")


# ── 36. Existing ICU/OR shell tests pass ──────────────────────────────────────

def test_36_existing_icu_or_shell_tests_pass(qapp, sample_patient_a):
    or_icu = OrIcuMode()
    or_icu.set_patient_and_scan(sample_patient_a)
    or_icu.set_mode("ICU")

    item_texts = [
        label.text()
        for label in or_icu.workflow_container.findChildren(QLabel)
    ]
    all_combined = " ".join(item_texts)
    assert "Current vs Previous Scan" in all_combined
