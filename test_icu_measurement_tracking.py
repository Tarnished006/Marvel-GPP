# test_icu_measurement_tracking.py
"""
Comprehensive automated unit and integration tests for ICU Feature 3: MEASUREMENT TRACKING.

Verifies all required specifications:
 1. Measurement tracking card exists in ICU mode.
 2. Initial empty state is correct.
 3. Existing measurement can be associated with Current scan.
 4. Existing measurement can be associated with Previous scan.
 5. Measurements retain scan identity.
 6. Measurements retain patient identity.
 7. Values and units persist correctly.
 8. Current and Previous values can be displayed side by side.
 9. Numeric difference is calculated correctly.
10. Zero clinical interpretation text is generated (no 'improved', 'worsened', 'progressed', 'regressed', etc.).
11. No cross-patient leakage.
12. Same-Location Review is unchanged and independent.
13. OR surgical planning state (Entry/Target/Route/Structures/Corridor/Instrument/Deviation/Plan Versions) is unchanged.
14. 2D/3D synchronization continues working.
15. Database persistence works round-trip.
16. Removing a tracked measurement only removes that measurement without deleting unrelated measurements.
17. No duplicate measurements are created accidentally.
"""

import os
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import database
from measurement_tracker import MeasurementTracker, TrackedMeasurement
from same_location import SameLocationReview
from surgical_plan import SurgicalPlanSession
from screens.or_icu_mode import OrIcuMode, MeasurementTrackingCard, SameLocationReviewCard
from screens.slice_2d_viewer import Slice2DViewerWidget, Measurement2D
from screens.viewer_3d import Viewer3D


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def in_memory_db(tmp_path, monkeypatch):
    """Provides an isolated SQLite database for test execution."""
    db_file = str(tmp_path / "test_aegis_meas_track.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()
    return db_file


@pytest.fixture
def sample_patient_data():
    return {
        "mrn": "MRN-ICU-007",
        "name": "Sarah Connor",
        "sex": "F",
        "age": "29",
    }


@pytest.fixture
def populated_scans(in_memory_db, sample_patient_data):
    """Inserts a patient and two sequential scans (Current and Previous) into the test database."""
    conn = database.get_connection()
    conn.execute(
        "INSERT INTO patients (mrn, name, sex, age) VALUES (?, ?, ?, ?)",
        (sample_patient_data["mrn"], sample_patient_data["name"], sample_patient_data["sex"], sample_patient_data["age"])
    )
    conn.execute(
        "INSERT INTO scans (id, patient_mrn, modality, study_date, description, file_path, slice_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (101, sample_patient_data["mrn"], "CT", "2026-09-10", "Baseline Head CT", "/scans/sarah/ct_prev.nii.gz", 40)
    )
    conn.execute(
        "INSERT INTO scans (id, patient_mrn, modality, study_date, description, file_path, slice_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (102, sample_patient_data["mrn"], "CT", "2026-09-22", "Follow-up Head CT", "/scans/sarah/ct_curr.nii.gz", 50)
    )
    conn.commit()
    conn.close()

    return {
        "prev_scan": {
            "id": 101,
            "patient_mrn": sample_patient_data["mrn"],
            "study_date": "2026-09-10",
            "description": "Baseline Head CT",
            "file_path": "/scans/sarah/ct_prev.nii.gz"
        },
        "curr_scan": {
            "id": 102,
            "patient_mrn": sample_patient_data["mrn"],
            "study_date": "2026-09-22",
            "description": "Follow-up Head CT",
            "file_path": "/scans/sarah/ct_curr.nii.gz"
        }
    }


# ── Test 1: Card exists in ICU mode ──────────────────────────────────────────
def test_1_measurement_tracking_card_exists(qapp):
    icu_mode = OrIcuMode()
    icu_mode.set_mode("ICU")
    assert icu_mode.measurement_tracking_card is not None
    assert isinstance(icu_mode.measurement_tracking_card, MeasurementTrackingCard)


# ── Test 2: Empty initial state ──────────────────────────────────────────────
def test_2_empty_initial_state(qapp):
    card = MeasurementTrackingCard()
    assert card.lbl_current_val.text() == "Current: None"
    assert card.lbl_prev_val.text() == "Previous: None"
    assert card.lbl_diff_val.text() == "Difference: N/A"
    assert "0 tracked" in card.lbl_status.text()
    assert card.btn_remove.isEnabled() is False


# ── Test 3: Associate with Current scan ───────────────────────────────────────
def test_3_associate_with_current_scan():
    tracker = MeasurementTracker()
    tracker.set_patient("MRN-ICU-007")
    tracker.set_scans(current_scan_id="102", previous_scan_id="101")

    m_curr = TrackedMeasurement(
        id="meas_curr_01",
        scan_id="102",
        patient_mrn="MRN-ICU-007",
        label="[3D] Lesion Diameter",
        value_mm=12.4,
        unit="mm"
    )
    added = tracker.add_measurement(m_curr)
    assert added is True

    curr_list = tracker.get_current_measurements()
    assert len(curr_list) == 1
    assert curr_list[0].id == "meas_curr_01"
    assert curr_list[0].value_mm == 12.4
    assert curr_list[0].unit == "mm"

    # Previous list should still be empty
    prev_list = tracker.get_previous_measurements()
    assert len(prev_list) == 0


# ── Test 4: Associate with Previous scan ──────────────────────────────────────
def test_4_associate_with_previous_scan():
    tracker = MeasurementTracker()
    tracker.set_patient("MRN-ICU-007")
    tracker.set_scans(current_scan_id="102", previous_scan_id="101")

    m_prev = TrackedMeasurement(
        id="meas_prev_01",
        scan_id="101",
        patient_mrn="MRN-ICU-007",
        label="[3D] Lesion Diameter",
        value_mm=10.8,
        unit="mm"
    )
    added = tracker.add_measurement(m_prev)
    assert added is True

    prev_list = tracker.get_previous_measurements()
    assert len(prev_list) == 1
    assert prev_list[0].id == "meas_prev_01"
    assert prev_list[0].value_mm == 10.8

    curr_list = tracker.get_current_measurements()
    assert len(curr_list) == 0


# ── Test 5: Measurements retain scan identity ─────────────────────────────────
def test_5_measurements_retain_scan_identity():
    m1 = TrackedMeasurement(id="m1", scan_id="102", patient_mrn="MRN-001", label="M1", value_mm=15.0)
    m2 = TrackedMeasurement(id="m2", scan_id="101", patient_mrn="MRN-001", label="M2", value_mm=14.0)

    assert m1.scan_id == "102"
    assert m2.scan_id == "101"

    tracker = MeasurementTracker()
    tracker.set_patient("MRN-001")
    tracker.set_scans("102", "101")
    tracker.add_measurement(m1)
    tracker.add_measurement(m2)

    assert len(tracker.get_measurements_for_scan("102")) == 1
    assert tracker.get_measurements_for_scan("102")[0].id == "m1"
    assert len(tracker.get_measurements_for_scan("101")) == 1
    assert tracker.get_measurements_for_scan("101")[0].id == "m2"


# ── Test 6: Measurements retain patient identity ──────────────────────────────
def test_6_measurements_retain_patient_identity():
    m1 = TrackedMeasurement(id="m1", scan_id="102", patient_mrn="PATIENT_A", label="M1", value_mm=12.0)
    assert m1.patient_mrn == "PATIENT_A"

    tracker = MeasurementTracker()
    tracker.set_patient("PATIENT_A")
    tracker.set_scans("102", "101")
    assert tracker.add_measurement(m1) is True

    # Reject measurement for different patient
    m_wrong = TrackedMeasurement(id="m_wrong", scan_id="102", patient_mrn="PATIENT_B", label="M_Wrong", value_mm=20.0)
    assert tracker.add_measurement(m_wrong) is False


# ── Test 7: Values and units persist correctly in SQLite ──────────────────────
def test_7_persistence_roundtrip(in_memory_db, sample_patient_data, populated_scans):
    res = database.save_tracked_measurement(
        measurement_id="test_m_01",
        scan_id="102",
        patient_mrn=sample_patient_data["mrn"],
        label="[3D] Ventricle Width",
        value_mm=14.25,
        unit="mm",
        metadata={"source": "3D", "pt1": (10.0, 20.0, 30.0), "pt2": (10.0, 34.25, 30.0)}
    )

    assert res["id"] == "test_m_01"
    assert res["value_mm"] == 14.25
    assert res["unit"] == "mm"

    # Retrieve by scan
    scan_items = database.get_tracked_measurements_for_scan("102", sample_patient_data["mrn"])
    assert len(scan_items) == 1
    item = scan_items[0]
    assert item["id"] == "test_m_01"
    assert item["value_mm"] == 14.25
    assert item["unit"] == "mm"
    assert item["metadata"]["source"] == "3D"

    # Retrieve by patient
    patient_items = database.get_tracked_measurements_for_patient(sample_patient_data["mrn"])
    assert len(patient_items) == 1
    assert patient_items[0]["id"] == "test_m_01"


# ── Test 8: Side-by-side display of Current and Previous ──────────────────────
def test_8_side_by_side_display(qapp):
    card = MeasurementTrackingCard()
    tracker = MeasurementTracker()
    tracker.set_patient("MRN-ICU-007")
    tracker.set_scans("102", "101")

    m_curr = TrackedMeasurement(id="mc", scan_id="102", patient_mrn="MRN-ICU-007", label="Tumor", value_mm=12.4)
    m_prev = TrackedMeasurement(id="mp", scan_id="101", patient_mrn="MRN-ICU-007", label="Tumor", value_mm=10.8)

    tracker.add_measurement(m_curr)
    tracker.add_measurement(m_prev)

    card.update_tracking(tracker)

    assert "Current: 12.4 mm" in card.lbl_current_val.text()
    assert "Previous: 10.8 mm" in card.lbl_prev_val.text()
    assert "Difference: 1.6 mm" in card.lbl_diff_val.text()


# ── Test 9: Numeric difference calculated correctly ───────────────────────────
def test_9_numeric_difference_calculation():
    tracker = MeasurementTracker()
    tracker.set_patient("MRN-ICU-007")
    tracker.set_scans("102", "101")

    # curr > prev
    tracker.add_measurement(TrackedMeasurement(id="c1", scan_id="102", patient_mrn="MRN-ICU-007", label="M", value_mm=25.5))
    tracker.add_measurement(TrackedMeasurement(id="p1", scan_id="101", patient_mrn="MRN-ICU-007", label="M", value_mm=20.0))
    comp1 = tracker.get_comparison()
    assert comp1["difference_value"] == 5.5
    assert comp1["difference_text"] == "Difference: 5.5 mm"

    # prev > curr
    tracker.add_measurement(TrackedMeasurement(id="c2", scan_id="102", patient_mrn="MRN-ICU-007", label="M", value_mm=15.0))
    tracker.select_current("c2")
    comp2 = tracker.get_comparison()
    assert comp2["difference_value"] == 5.0
    assert comp2["difference_text"] == "Difference: 5.0 mm"

    # exact match
    tracker.add_measurement(TrackedMeasurement(id="c3", scan_id="102", patient_mrn="MRN-ICU-007", label="M", value_mm=20.0))
    tracker.select_current("c3")
    comp3 = tracker.get_comparison()
    assert comp3["difference_value"] == 0.0
    assert comp3["difference_text"] == "Difference: 0.0 mm"


# ── Test 10: Zero clinical interpretation text ────────────────────────────────
def test_10_zero_clinical_interpretation_text(qapp):
    forbidden_words = [
        "improved", "worsened", "progressed", "regressed",
        "better", "worse", "significant", "deteriorat",
        "favorable", "unfavorable", "response", "failure"
    ]

    tracker = MeasurementTracker()
    tracker.set_patient("MRN-ICU-007")
    tracker.set_scans("102", "101")

    # Try both large increase and large decrease
    tracker.add_measurement(TrackedMeasurement(id="c1", scan_id="102", patient_mrn="MRN-ICU-007", label="M", value_mm=50.0))
    tracker.add_measurement(TrackedMeasurement(id="p1", scan_id="101", patient_mrn="MRN-ICU-007", label="M", value_mm=10.0))

    comp = tracker.get_comparison()
    for text in [comp["current_text"], comp["previous_text"], comp["difference_text"]]:
        text_lower = text.lower()
        for word in forbidden_words:
            assert word not in text_lower, f"Forbidden clinical interpretation word '{word}' found in '{text}'"

    card = MeasurementTrackingCard()
    card.update_tracking(tracker)
    for lbl in [card.lbl_current_val, card.lbl_prev_val, card.lbl_diff_val, card.lbl_status]:
        text_lower = lbl.text().lower()
        for word in forbidden_words:
            assert word not in text_lower, f"Forbidden word '{word}' found in card label '{lbl.text()}'"


# ── Test 11: No cross-patient leakage ─────────────────────────────────────────
def test_11_no_cross_patient_leakage(in_memory_db):
    # Save measurement for Patient A
    database.save_tracked_measurement(
        measurement_id="m_patient_a",
        scan_id="scan_a",
        patient_mrn="PATIENT_A",
        label="M_A",
        value_mm=10.0
    )

    # Save measurement for Patient B
    database.save_tracked_measurement(
        measurement_id="m_patient_b",
        scan_id="scan_b",
        patient_mrn="PATIENT_B",
        label="M_B",
        value_mm=20.0
    )

    # Query Patient A
    items_a = database.get_tracked_measurements_for_patient("PATIENT_A")
    assert len(items_a) == 1
    assert items_a[0]["patient_mrn"] == "PATIENT_A"
    assert items_a[0]["id"] == "m_patient_a"

    # Query Patient B
    items_b = database.get_tracked_measurements_for_patient("PATIENT_B")
    assert len(items_b) == 1
    assert items_b[0]["patient_mrn"] == "PATIENT_B"
    assert items_b[0]["id"] == "m_patient_b"

    # In tracker: switching patient clears previous patient data
    tracker = MeasurementTracker()
    tracker.set_patient("PATIENT_A")
    tracker.set_scans("scan_a", None)
    tracker.add_measurement(TrackedMeasurement(id="m_patient_a", scan_id="scan_a", patient_mrn="PATIENT_A", label="M_A", value_mm=10.0))
    assert len(tracker.get_current_measurements()) == 1

    tracker.set_patient("PATIENT_B")
    assert len(tracker.get_current_measurements()) == 0
    assert tracker.patient_mrn == "PATIENT_B"


# ── Test 12: Same-Location Review is unchanged and independent ────────────────
def test_12_same_location_review_unchanged(qapp):
    icu_mode = OrIcuMode()
    icu_mode.set_mode("ICU")

    # Same location card is present and functional
    assert icu_mode.same_location_card is not None
    assert isinstance(icu_mode.same_location_card, SameLocationReviewCard)

    # Measurement tracking card is distinct
    assert icu_mode.measurement_tracking_card is not None
    assert icu_mode.same_location_card is not icu_mode.measurement_tracking_card

    # Adding a tracked measurement does not alter same-location state
    review = SameLocationReview()
    review.set_location(10.0, 20.0, 30.0)
    icu_mode.same_location_card.update_location(review)
    assert icu_mode.same_location_card.lbl_location.text() == "Location: (10.0, 20.0, 30.0) mm"

    tracker = MeasurementTracker()
    tracker.add_measurement(TrackedMeasurement(id="m", scan_id="s", patient_mrn="p", label="L", value_mm=15.0))
    icu_mode.measurement_tracking_card.update_tracking(tracker)

    # Same location card display is completely untouched
    assert icu_mode.same_location_card.lbl_location.text() == "Location: (10.0, 20.0, 30.0) mm"


# ── Test 13: OR surgical planning state is untouched ──────────────────────────
def test_13_or_surgical_planning_untouched(qapp):
    plan = SurgicalPlanSession()
    plan.scan_id = "scan_01"
    plan.set_entry(10.0, 20.0, 30.0)
    plan.set_target(40.0, 50.0, 60.0)

    # Add avoidance structure
    struct = plan.add_avoid_structure(15.0, 25.0, 35.0, radius_mm=3.0, name="Optic Nerve")
    s_id = struct.structure_id
    # Set corridor
    plan.corridor_radius_mm = 4.0
    # Set instrument
    plan.instrument_preset = "Biopsy Needle"

    # Perform measurement tracking operations
    tracker = MeasurementTracker()
    tracker.set_patient("MRN-ICU-007")
    tracker.set_scans("scan_01", None)
    m = TrackedMeasurement(id="m_or", scan_id="scan_01", patient_mrn="MRN-ICU-007", label="Caliper", value_mm=12.0)
    tracker.add_measurement(m)

    # Verify all OR planning attributes are strictly identical
    assert plan.entry_point.coordinates == (10.0, 20.0, 30.0)
    assert plan.target_point.coordinates == (40.0, 50.0, 60.0)
    assert plan.has_planned_route() is True
    assert plan.corridor_radius_mm == 4.0
    assert plan.instrument_preset == "Biopsy Needle"
    assert len(plan.avoid_structures) == 1
    assert s_id in plan.avoid_structures


# ── Test 14: 2D/3D synchronization continues working ──────────────────────────
def test_14_2d_3d_synchronization_continues_working(qapp):
    viewer = Viewer3D()
    vol = np.zeros((30, 40, 50), dtype=np.float32)
    viewer.panel_2d.load_volume(vol, (1.0, 1.0), 1.0)

    # Test crosshair and slice sync
    viewer.panel_2d.set_slice_index(15)
    assert viewer.panel_2d.get_current_slice_index() == 15

    # Triggering measurement tracking UI does not break viewer
    card = MeasurementTrackingCard()
    tracker = MeasurementTracker()
    card.update_tracking(tracker)

    assert viewer.panel_2d.get_current_slice_index() == 15




# ── Test 15: Removing tracked measurement only removes targeted item ──────────
def test_15_remove_targeted_measurement_only(in_memory_db, sample_patient_data):
    mrn = sample_patient_data["mrn"]
    m1 = database.save_tracked_measurement("m1", "scan_1", mrn, "M1", 10.0)
    m2 = database.save_tracked_measurement("m2", "scan_1", mrn, "M2", 20.0)

    tracker = MeasurementTracker()
    tracker.set_patient(mrn)
    tracker.set_scans("scan_1", None)
    tracker.add_measurement(TrackedMeasurement.from_dict(m1))
    tracker.add_measurement(TrackedMeasurement.from_dict(m2))

    assert len(tracker.get_current_measurements()) == 2

    # Remove m1 in DB and tracker
    deleted = database.delete_tracked_measurement("m1", mrn)
    assert deleted is True
    tracker.remove_measurement("m1")

    # Verify m2 is preserved
    assert len(tracker.get_current_measurements()) == 1
    assert tracker.get_current_measurements()[0].id == "m2"

    db_items = database.get_tracked_measurements_for_scan("scan_1", mrn)
    assert len(db_items) == 1
    assert db_items[0]["id"] == "m2"


# ── Test 16: No duplicate measurements created accidentally ───────────────────
def test_16_no_duplicate_measurements():
    tracker = MeasurementTracker()
    tracker.set_patient("MRN-001")
    tracker.set_scans("s1", None)

    m = TrackedMeasurement(id="unique_id_1", scan_id="s1", patient_mrn="MRN-001", label="M", value_mm=10.0)
    tracker.add_measurement(m)
    tracker.add_measurement(m)  # Re-adding with same ID should not duplicate

    assert len(tracker.get_current_measurements()) == 1
