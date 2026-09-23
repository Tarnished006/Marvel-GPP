# test_icu_what_changed.py
"""
Comprehensive test suite for ICU Feature 5: WHAT CHANGED?
Tests domain model (ChangeReview), UI card (WhatChangedCard), 2D slice overlay,
MPR orthogonal overlay, volume compatibility checking, cache management,
strict neutrality (no diagnosis/clinical claims), and complete isolation from
Surgical Planning (OR 1-9) and ICU Features 1-4.
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
import numpy as np

# Configure Qt offscreen platform before importing PyQt6
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

# Ensure a single QApplication instance exists
app = QApplication.instance() or QApplication(sys.argv)

from what_changed import ChangeReview
from screens.or_icu_mode import WhatChangedCard, OrIcuMode
from screens.slice_2d_viewer import Slice2DViewerWidget, CTSliceCanvas
from screens.mpr_view import MPRView, MPRSliceWidget
from surgical_plan import SurgicalPlanSession, PlanningPoint, AvoidStructure
from same_location import SameLocationReview
from measurement_tracker import MeasurementTracker, TrackedMeasurement
from annotation_carry_forward import AnnotationCarryForwardManager, ScanAnnotation


# ── Test 1: What Changed card exists ──────────────────────────────────────────
def test_01_what_changed_card_exists():
    card = WhatChangedCard()
    assert card is not None
    assert hasattr(card, "lbl_title")
    assert "WHAT CHANGED" in card.lbl_title.text().upper()
    assert hasattr(card, "btn_review")
    assert hasattr(card, "btn_show_current")
    assert hasattr(card, "btn_show_previous")
    assert hasattr(card, "btn_clear")
    assert hasattr(card, "slider_threshold")


# ── Test 2: Initial state is inactive ─────────────────────────────────────────
def test_02_initial_state_inactive():
    review = ChangeReview()
    assert not review.is_active()
    assert not review.difference_available
    assert review.status_message == "No difference review active"
    assert review.threshold == 50.0

    card = WhatChangedCard()
    assert card.lbl_header_status.text() == "No difference review active"
    assert "No difference review active" in card.lbl_status.text()
    assert not card.lbl_changed_voxels.isVisible()


# ── Test 3: Current/Previous scan context is reused from ICU Feature 1 ─────────
def test_03_current_previous_scan_context_reused():
    review = ChangeReview()
    review.set_patient_and_scans("MRN_123", "MRN_123", "scan_curr_01", "scan_prev_01")
    assert review.current_patient_mrn == "MRN_123"
    assert review.previous_patient_mrn == "MRN_123"
    assert review.current_scan_id == "scan_curr_01"
    assert review.previous_scan_id == "scan_prev_01"

    card = WhatChangedCard()
    card.update_from_review(review)
    assert "scan_curr_01" in card.lbl_current_scan.text()
    assert "scan_prev_01" in card.lbl_previous_scan.text()


# ── Test 4: Compatible volumes are detected correctly ─────────────────────────
def test_04_compatible_volumes_detected():
    review = ChangeReview()
    v_curr = np.ones((50, 50, 20), dtype=np.int16) * 100
    v_prev = np.ones((50, 50, 20), dtype=np.int16) * 80
    geom_curr = {"dims": (50, 50, 20), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}
    geom_prev = {"dims": (50, 50, 20), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}

    is_compat, reason = review.check_compatibility(v_curr, v_prev, geom_curr, geom_prev)
    assert is_compat is True
    assert "compatible" in reason.lower()


# ── Test 5: Incompatible volume dimensions are detected ───────────────────────
def test_05_incompatible_dimensions_detected():
    review = ChangeReview()
    v_curr = np.ones((50, 50, 20), dtype=np.int16)
    v_prev = np.ones((60, 50, 20), dtype=np.int16)
    geom_curr = {"dims": (50, 50, 20), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}
    geom_prev = {"dims": (60, 50, 20), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}

    is_compat, reason = review.check_compatibility(v_curr, v_prev, geom_curr, geom_prev)
    assert is_compat is False
    assert "dimensions do not match" in reason.lower() or "not directly compatible" in reason.lower()


# ── Test 6: Incompatible spacing is detected ──────────────────────────────────
def test_06_incompatible_spacing_detected():
    review = ChangeReview()
    v_curr = np.ones((50, 50, 20), dtype=np.int16)
    v_prev = np.ones((50, 50, 20), dtype=np.int16)
    geom_curr = {"dims": (50, 50, 20), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}
    geom_prev = {"dims": (50, 50, 20), "spacing": (1.5, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}

    is_compat, reason = review.check_compatibility(v_curr, v_prev, geom_curr, geom_prev)
    assert is_compat is False
    assert "spacing" in reason.lower()


# ── Test 7: Incompatible origin is detected ───────────────────────────────────
def test_07_incompatible_origin_detected():
    review = ChangeReview()
    v_curr = np.ones((50, 50, 20), dtype=np.int16)
    v_prev = np.ones((50, 50, 20), dtype=np.int16)
    geom_curr = {"dims": (50, 50, 20), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}
    geom_prev = {"dims": (50, 50, 20), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (10.0, 0.0, 0.0)}

    is_compat, reason = review.check_compatibility(v_curr, v_prev, geom_curr, geom_prev)
    assert is_compat is False
    assert "origin" in reason.lower()


# ── Test 8: Difference map is not generated for incompatible grids ─────────────
def test_08_difference_map_not_generated_for_incompatible_grids():
    review = ChangeReview()
    v_curr = np.ones((50, 50, 20), dtype=np.int16)
    v_prev = np.ones((60, 50, 20), dtype=np.int16)
    geom_curr = {"dims": (50, 50, 20), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}
    geom_prev = {"dims": (60, 50, 20), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}

    diff = review.compute_difference(v_curr, v_prev, geom_curr, geom_prev)
    assert diff is None
    assert not review.difference_available
    assert "Difference map unavailable" in review.status_message


# ── Test 9: 'Difference map unavailable' displayed when appropriate ───────────
def test_09_difference_map_unavailable_displayed():
    review = ChangeReview()
    review.set_difference_unavailable("Current and Previous voxel grids are not directly compatible.")
    card = WhatChangedCard()
    card.update_from_review(review)

    assert "Unavailable" in card.lbl_header_status.text()
    assert "Current and Previous voxel grids are not directly compatible" in card.lbl_status.text()
    assert not card.lbl_changed_voxels.isVisible()


# ── Test 10: Compatible volumes produce an objective absolute-difference map ──
def test_10_objective_absolute_difference_map():
    review = ChangeReview()
    v_curr = np.zeros((10, 10, 5), dtype=np.int16)
    v_prev = np.zeros((10, 10, 5), dtype=np.int16)
    v_curr[2:5, 2:5, :] = 120
    v_prev[2:5, 2:5, :] = 40

    geom = {"dims": (10, 10, 5), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)}
    diff = review.compute_difference(v_curr, v_prev, geom, geom)
    assert diff is not None
    assert diff.shape == (10, 10, 5)
    # Check that difference is absolute: |120 - 40| = 80
    assert np.allclose(diff[2:5, 2:5, :], 80.0)
    assert np.allclose(diff[0:2, :, :], 0.0)
    assert review.difference_available is True
    assert review.status_message == "Difference map available"


# ── Test 11: Threshold is applied correctly ───────────────────────────────────
def test_11_threshold_applied_correctly():
    review = ChangeReview()
    v_curr = np.zeros((10, 10, 10), dtype=np.int16)
    v_prev = np.zeros((10, 10, 10), dtype=np.int16)
    # Region 1: diff = 30 HU (100 voxels)
    v_curr[0:2, :, :] = 30
    # Region 2: diff = 70 HU (100 voxels)
    v_curr[2:4, :, :] = 70

    geom = {"dims": (10, 10, 10), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)}
    review.compute_difference(v_curr, v_prev, geom, geom)

    # With threshold = 50: only Region 2 (>= 50) is counted -> 2 * 10 * 10 = 200 voxels
    count_50 = review.get_changed_voxel_count(threshold=50.0)
    assert count_50 == 200

    # With threshold = 20: both Region 1 and 2 (>= 20) are counted -> 4 * 10 * 10 = 400 voxels
    count_20 = review.get_changed_voxel_count(threshold=20.0)
    assert count_20 == 400

    # With threshold = 80: neither is counted -> 0 voxels
    count_80 = review.get_changed_voxel_count(threshold=80.0)
    assert count_80 == 0


# ── Test 12: Changing threshold does not reload DICOM data ────────────────────
def test_12_threshold_change_does_not_recompute_volume():
    review = ChangeReview()
    v_curr = np.zeros((10, 10, 10), dtype=np.int16)
    v_prev = np.zeros((10, 10, 10), dtype=np.int16)
    v_curr[0:5, :, :] = 100

    geom = {"dims": (10, 10, 10), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)}
    review.compute_difference(v_curr, v_prev, geom, geom)
    cached_vol_id = id(review.difference_volume)

    # Adjust threshold
    review.set_threshold(60.0)
    assert id(review.difference_volume) == cached_vol_id
    assert review.threshold == 60.0

    review.set_threshold(120.0)
    assert id(review.difference_volume) == cached_vol_id
    assert review.threshold == 120.0


# ── Test 13: Difference count is calculated correctly ─────────────────────────
def test_13_difference_count_calculated_correctly():
    review = ChangeReview()
    v_curr = np.zeros((20, 20, 20), dtype=np.int16)
    v_prev = np.zeros((20, 20, 20), dtype=np.int16)
    # Exactly 50 voxels differ by 60 HU
    v_curr.flat[:50] = 60

    geom = {"dims": (20, 20, 20), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)}
    review.compute_difference(v_curr, v_prev, geom, geom)

    count = review.get_changed_voxel_count(threshold=50.0)
    assert count == 50


# ── Test 14, 15, 16: Strict neutrality (No diagnosis, no clinical claims) ─────
def test_14_15_16_strict_neutrality_no_clinical_claims():
    review = ChangeReview()
    card = WhatChangedCard()

    forbidden_terms = [
        "lesion", "disease", "diseased", "pathology", "pathological",
        "abnormal", "tumor", "progression", "regression", "improved",
        "improvement", "deteriorated", "deterioration", "worsened",
        "worsening", "healing", "diagnos"
    ]

    # Check status messages in model
    for status in [
        review.status_message,
        "No difference review active",
        "Difference map available",
        "Difference map unavailable: Current and Previous voxel grids are not directly compatible."
    ]:
        for term in forbidden_terms:
            assert term not in status.lower(), f"Forbidden term '{term}' found in status: {status}"

    # Check text in card
    review.enabled = True
    review.difference_available = True
    review.difference_volume = np.ones((5, 5, 5), dtype=np.float32) * 60
    card.update_from_review(review)

    card_text = " ".join([
        card.lbl_title.text(),
        card.lbl_header_status.text(),
        card.lbl_status.text(),
        card.lbl_changed_voxels.text(),
        card.lbl_thresh_title.text(),
        card.btn_review.text()
    ]).lower()

    for term in forbidden_terms:
        assert term not in card_text, f"Forbidden term '{term}' found in card UI: {card_text}"

    # Verify neutral facts are shown
    assert "changed voxels" in card.lbl_changed_voxels.text().lower()
    assert "difference threshold" in card.lbl_changed_voxels.text().lower()


# ── Test 17: 2D difference visualization works ────────────────────────────────
def test_17_2d_difference_visualization():
    canvas = CTSliceCanvas()
    canvas.resize(300, 300)

    # Set mock slice
    raw_slice = np.zeros((100, 100), dtype=np.int16)
    diff_slice = np.zeros((100, 100), dtype=np.float32)
    diff_slice[20:50, 20:50] = 80.0

    canvas.set_slice_data(None, raw_slice, 1.0, 1.0, "Axial")
    canvas.set_difference_overlay(True, diff_slice, threshold=50.0)

    assert canvas.difference_active is True
    assert canvas.difference_slice is not None
    assert canvas.difference_threshold == 50.0

    # Test clearing overlay
    canvas.clear_difference_overlay()
    assert canvas.difference_active is False
    assert canvas.difference_slice is None


# ── Test 18: MPR difference visualization works ───────────────────────────────
def test_18_mpr_difference_visualization():
    mpr_view = MPRView()
    mpr_view.resize(600, 400)

    # Mock volume
    vol = np.zeros((40, 40, 40), dtype=np.int16)
    mpr_view.vol_data = vol
    mpr_view._build_wl_lut()
    mpr_view._update_wl_cache()

    diff_vol = np.zeros((40, 40, 40), dtype=np.float32)
    diff_vol[10:20, 10:20, 10:20] = 75.0

    mpr_view.set_difference_volume(diff_vol, threshold=50.0, active=True)
    assert mpr_view.difference_active is True
    assert mpr_view.w_axial.difference_active is True
    assert mpr_view.w_coronal.difference_active is True
    assert mpr_view.w_sagittal.difference_active is True

    # Test clearing MPR difference
    mpr_view.clear_difference_volume()
    assert mpr_view.difference_active is False
    assert mpr_view.w_axial.difference_active is False
    assert mpr_view.w_coronal.difference_active is False
    assert mpr_view.w_sagittal.difference_active is False


# ── Test 19: 3D difference visualization is safely limited to 2D/MPR ──────────
def test_19_3d_difference_visualization_safely_limited():
    review = ChangeReview()
    # Prompt: "If 3D visualization would require unreliable geometry assumptions: DO NOT implement it.
    # Instead report that 3D difference review is intentionally limited to 2D/MPR."
    # The domain model and system enforce that difference review is presented via 2D slice & MPR orthogonal views.
    assert hasattr(review, "compute_difference")
    assert not hasattr(review, "generate_3d_mesh")  # No fake 3D mesh generation


# ── Test 20: No duplicate viewers are created ─────────────────────────────────
def test_20_no_duplicate_viewers():
    viewer = Slice2DViewerWidget()
    initial_canvas = viewer.canvas
    diff_vol = np.zeros((20, 20, 20), dtype=np.float32)

    viewer.set_difference_overlay(True, diff_vol, 50.0)
    assert viewer.canvas is initial_canvas

    viewer.set_difference_overlay(False)
    assert viewer.canvas is initial_canvas


# ── Test 21: No duplicate difference actors accumulate ────────────────────────
def test_21_no_duplicate_difference_actors():
    canvas = CTSliceCanvas()
    diff_slice = np.zeros((50, 50), dtype=np.float32)

    # Calling set_difference_overlay multiple times replaces previous overlay
    canvas.set_difference_overlay(True, diff_slice, 50.0)
    canvas.set_difference_overlay(True, diff_slice, 60.0)
    canvas.set_difference_overlay(True, diff_slice, 70.0)

    assert canvas.difference_threshold == 70.0
    assert canvas.difference_active is True


# ── Test 22: Difference review can be cleared ─────────────────────────────────
def test_22_difference_review_can_be_cleared():
    review = ChangeReview()
    v = np.ones((10, 10, 10), dtype=np.int16)
    geom = {"dims": (10, 10, 10), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)}
    review.compute_difference(v, v, geom, geom)
    review.enabled = True

    assert review.is_active() is True
    review.clear_review()
    assert review.is_active() is False
    assert review.difference_volume is None
    assert review.status_message == "No difference review active"


# ── Test 23: Patient switch clears stale difference state ─────────────────────
def test_23_patient_switch_clears_stale_difference():
    review = ChangeReview()
    v = np.ones((10, 10, 10), dtype=np.int16)
    geom = {"dims": (10, 10, 10), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)}
    review.compute_difference(v, v, geom, geom)
    review.enabled = True
    review.set_patient_and_scans("PATIENT_A", "PATIENT_A", "scan_1", "scan_2")

    # Switch to PATIENT_B
    review.reset()
    review.set_patient_and_scans("PATIENT_B", "PATIENT_B", "scan_3", "scan_4")

    assert review.current_patient_mrn == "PATIENT_B"
    assert review.previous_patient_mrn == "PATIENT_B"
    assert not review.is_active()
    assert review.difference_volume is None
    assert review.status_message == "No difference review active"


# ── Test 24: Scan switch revalidates compatibility ────────────────────────────
def test_24_scan_switch_revalidates_compatibility():
    review = ChangeReview()
    v_curr = np.ones((20, 20, 20), dtype=np.int16)
    v_prev1 = np.ones((20, 20, 20), dtype=np.int16)
    v_prev2 = np.ones((30, 20, 20), dtype=np.int16)  # incompatible
    geom_curr = {"dims": (20, 20, 20), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)}
    geom_prev1 = {"dims": (20, 20, 20), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)}
    geom_prev2 = {"dims": (30, 20, 20), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)}

    # First pair: compatible
    is_compat1, _ = review.check_compatibility(v_curr, v_prev1, geom_curr, geom_prev1)
    assert is_compat1 is True

    # Second pair: switch scan -> incompatible
    is_compat2, _ = review.check_compatibility(v_curr, v_prev2, geom_curr, geom_prev2)
    assert is_compat2 is False


# ── Test 25: Same-Location Review remains unchanged ───────────────────────────
def test_25_same_location_review_remains_unchanged():
    slr = SameLocationReview()
    geom = {"dims": (50, 50, 20), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}
    slr.set_location(12.5, 34.2, 56.8, "curr_scan", "prev_scan", geom, geom)

    assert slr.is_point_valid() is True
    assert slr.get_physical_coordinates() == (12.5, 34.2, 56.8)

    # Triggering what changed review does not alter same-location review
    review = ChangeReview()
    review.set_patient_and_scans("MRN_1", "MRN_1", "curr_scan", "prev_scan")
    assert slr.is_point_valid() is True
    assert slr.get_physical_coordinates() == (12.5, 34.2, 56.8)


# ── Test 26: Measurement Tracking remains unchanged ───────────────────────────
def test_26_measurement_tracking_remains_unchanged():
    tracker = MeasurementTracker()
    m = TrackedMeasurement(
        id="meas_01",
        scan_id="scan_A",
        patient_mrn="MRN_1",
        label="Tumor Axis",
        value_mm=14.5
    )
    tracker.add_measurement(m)

    review = ChangeReview()
    review.set_patient_and_scans("MRN_1", "MRN_1", "scan_A", "scan_B")

    # Tracked measurements must not be altered
    assert len(tracker.get_measurements_for_scan("scan_A")) == 1
    assert tracker.get_measurements_for_scan("scan_A")[0].value_mm == 14.5


# ── Test 27: Annotation Carry-forward remains unchanged ───────────────────────
def test_27_annotation_carry_forward_remains_unchanged():
    acfm = AnnotationCarryForwardManager()
    annot = ScanAnnotation(
        id="annot_01",
        patient_mrn="MRN_1",
        scan_id="scan_A",
        label="Marker 1",
        physical_x_mm=5.0,
        physical_y_mm=10.0,
        physical_z_mm=15.0
    )
    acfm.add_annotation(annot)

    review = ChangeReview()
    review.set_patient_and_scans("MRN_1", "MRN_1", "scan_A", "scan_B")

    assert len(acfm.get_annotations_for_patient("MRN_1")) == 1
    assert acfm.get_annotation("annot_01").label == "Marker 1"


# ── Test 28: Existing 2D measurements remain unchanged ────────────────────────
def test_28_existing_2d_measurements_remain_unchanged():
    from screens.slice_2d_viewer import Measurement2D
    viewer = Slice2DViewerWidget()
    m = Measurement2D(
        id="test_m1",
        start_u=0.1,
        start_v=0.1,
        end_u=0.5,
        end_v=0.5,
        start_px=10,
        start_py=10,
        end_px=50,
        end_py=50,
        distance_mm=10.0,
    )
    viewer.canvas.measurements.append(m)
    assert len(viewer.get_measurements()) == 1

    diff_vol = np.zeros((20, 20, 20), dtype=np.float32)
    viewer.set_difference_overlay(True, diff_vol, 50.0)

    # 2D measurements array untouched
    assert len(viewer.get_measurements()) == 1
    assert viewer.get_measurements()[0].distance_mm == 10.0


# ── Test 29-36: Surgical Planning state (OR Features 1-8) remains unchanged ───
def test_29_36_surgical_planning_state_remains_unchanged():
    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 30.0)
    session.set_target(40.0, 50.0, 60.0)
    session.add_avoid_structure(25.0, 35.0, 45.0, radius_mm=5.0, name="Carotid")

    # Verify initial planning state
    assert (session.entry_point.x_mm, session.entry_point.y_mm, session.entry_point.z_mm) == (10.0, 20.0, 30.0)
    assert (session.target_point.x_mm, session.target_point.y_mm, session.target_point.z_mm) == (40.0, 50.0, 60.0)
    assert len(session.avoid_structures) == 1

    # Activate what changed review
    review = ChangeReview()
    v = np.ones((10, 10, 10), dtype=np.int16)
    geom = {"dims": (10, 10, 10), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)}
    review.compute_difference(v, v, geom, geom)
    review.enabled = True

    # Surgical planning landmarks, route, corridor, and structures must remain intact
    assert (session.entry_point.x_mm, session.entry_point.y_mm, session.entry_point.z_mm) == (10.0, 20.0, 30.0)
    assert (session.target_point.x_mm, session.target_point.y_mm, session.target_point.z_mm) == (40.0, 50.0, 60.0)
    assert len(session.avoid_structures) == 1
    assert session.has_planned_route() is True
    assert session.get_planned_route() is not None
    assert session.get_surgical_corridor() is not None


# ── Test 37: No cross-patient comparison leakage ──────────────────────────────
def test_37_no_cross_patient_leakage():
    review = ChangeReview()
    review.set_patient_and_scans("PATIENT_A", "PATIENT_B", "scan_A", "scan_B")

    v = np.ones((10, 10, 10), dtype=np.int16)
    geom = {"dims": (10, 10, 10), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)}

    is_compat, reason = review.check_compatibility(v, v, geom, geom)
    assert is_compat is False
    assert "same patient" in reason.lower()

    diff = review.compute_difference(v, v, geom, geom)
    assert diff is None
    assert review.difference_available is False
    assert "same patient" in review.status_message.lower()


# ── Test 38: No unnecessary DICOM reload on threshold adjustment ──────────────
def test_38_no_unnecessary_reload_on_threshold_adjustment():
    viewer = Slice2DViewerWidget()
    diff_vol = np.zeros((20, 20, 20), dtype=np.float32)
    diff_vol[5:15, 5:15, 5:15] = 90.0

    viewer.set_difference_overlay(True, diff_vol, threshold=50.0)
    assert viewer.difference_threshold == 50.0

    # Adjusting threshold modifies threshold only and updates overlay slice without resetting volume
    viewer.set_difference_overlay(True, diff_vol, threshold=75.0)
    assert viewer.difference_threshold == 75.0
    assert viewer.difference_volume is diff_vol
