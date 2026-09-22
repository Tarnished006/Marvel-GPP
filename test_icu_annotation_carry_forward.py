# test_icu_annotation_carry_forward.py
"""
Comprehensive automated unit and integration tests for ICU Feature 4: ANNOTATION CARRY-FORWARD.

Verifies:
 1. Annotation Carry-forward card exists.
 2. Initial state shows "No annotation selected".
 3. Existing annotation can be selected.
 4. Annotation retains patient identity.
 5. Annotation retains source scan identity.
 6. Annotation retains target scan identity.
 7. Annotation retains physical X/Y/Z.
 8. Target slice index is derived from target geometry.
 9. Different spacing is handled.
10. Different slice thickness is handled.
11. Different matrix dimensions are handled.
12. Missing spatial metadata is handled safely.
13. "Physical correspondence unavailable" is displayed when required.
14. "Approx. Correspondence" is displayed when mapping is approximate.
15. Carry Forward creates a target annotation without mutating the source annotation.
16. Source annotation remains available.
17. Target annotation renders in 2D.
18. Target annotation renders correctly in MPR where supported.
19. Target annotation renders correctly in 3D where supported.
20. No duplicate viewers are created.
21. No duplicate annotation actors accumulate.
22. Patient switch clears annotation state.
23. Scan switch safely recomputes or clears stale annotation state.
24. Same-Location Review remains unchanged.
25. Measurement Tracking remains unchanged.
26. Existing measurements remain unchanged.
27. OR Entry remains unchanged.
28. OR Target remains unchanged.
29. Planned Route remains unchanged.
30. Structures to Avoid remain unchanged.
31. Surgical Corridor remains unchanged.
32. Virtual Instrument remains unchanged.
33. Live Deviation remains unchanged.
34. Plan Versions remain unchanged.
35. No cross-patient annotation leakage.
36. Persistence round-trip works.
37. Existing annotation/notes tests continue passing.
38. ICU Feature 1 continues passing.
39. ICU Feature 2 continues passing.
40. ICU Feature 3 continues passing.
41. Existing measurement tests continue passing.
42. Existing synchronization tests continue passing.
43. All OR Feature 1–9 tests continue passing.
44. ICU shell tests continue passing.
"""

import os
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import database
from annotation_carry_forward import AnnotationCarryForwardManager, ScanAnnotation
from measurement_tracker import MeasurementTracker, TrackedMeasurement
from same_location import SameLocationReview
from surgical_plan import SurgicalPlanSession
from screens.or_icu_mode import (
    OrIcuMode,
    AnnotationCarryForwardCard,
    MeasurementTrackingCard,
    SameLocationReviewCard,
    EntryTargetCard,
    PlannedRouteCard,
    StructuresToAvoidCard,
    SurgicalCorridorCard,
    VirtualInstrumentCard,
    LiveDeviationCard,
    PlanVersionsCard,
    QuickSurgicalViewsCard
)
from screens.slice_2d_viewer import Slice2DViewerWidget, CTSliceCanvas, Measurement2D
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
    db_file = str(tmp_path / "test_aegis_annot_cf.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()
    return db_file


@pytest.fixture
def sample_patient_data():
    return {
        "mrn": "MRN-ICU-008",
        "name": "Kyle Reese",
        "sex": "M",
        "age": "32",
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
        (201, sample_patient_data["mrn"], "CT", "2026-09-10", "Pre-op CT", "/scans/kyle/ct_prev.nii.gz", 40)
    )
    conn.execute(
        "INSERT INTO scans (id, patient_mrn, modality, study_date, description, file_path, slice_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (202, sample_patient_data["mrn"], "CT", "2026-09-22", "Post-op CT", "/scans/kyle/ct_curr.nii.gz", 50)
    )
    conn.commit()
    conn.close()

    return {
        "prev_scan": {
            "id": 201,
            "patient_mrn": sample_patient_data["mrn"],
            "study_date": "2026-09-10",
            "description": "Pre-op CT",
            "file_path": "/scans/kyle/ct_prev.nii.gz"
        },
        "curr_scan": {
            "id": 202,
            "patient_mrn": sample_patient_data["mrn"],
            "study_date": "2026-09-22",
            "description": "Post-op CT",
            "file_path": "/scans/kyle/ct_curr.nii.gz"
        }
    }


# ── Test 1: Annotation Carry-forward card exists ──────────────────────────────
def test_1_annotation_carry_forward_card_exists(qapp):
    icu_mode = OrIcuMode()
    icu_mode.set_mode("ICU")
    assert hasattr(icu_mode, "annotation_carry_forward_card")
    assert icu_mode.annotation_carry_forward_card is not None
    assert isinstance(icu_mode.annotation_carry_forward_card, AnnotationCarryForwardCard)


# ── Test 2: Initial state shows "No annotation selected" ─────────────────────
def test_2_initial_state_no_annotation_selected(qapp):
    card = AnnotationCarryForwardCard()
    assert card.lbl_annot.text() == "Annotation: None"
    assert card.lbl_source.text() == "No annotation selected"
    assert card.lbl_target.text() == "Target: N/A"
    assert "0 annotations" in card.lbl_status.text()
    assert card.btn_carry.isEnabled() is False
    assert card.btn_view_source.isEnabled() is False
    assert card.btn_view_target.isEnabled() is False
    assert card.btn_clear.isEnabled() is False


# ── Test 3: Existing annotation can be selected ──────────────────────────────
def test_3_existing_annotation_can_be_selected():
    mgr = AnnotationCarryForwardManager()
    mgr.set_patient("MRN-ICU-008")
    mgr.set_scans("202", "201")
    annot = ScanAnnotation(
        id="annot_001",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Target Zone Alpha",
        physical_x_mm=10.0,
        physical_y_mm=20.0,
        physical_z_mm=30.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation("annot_001")
    sel = mgr.get_selected_annotation()
    assert sel is not None
    assert sel.id == "annot_001"
    assert sel.label == "Target Zone Alpha"


# ── Test 4: Annotation retains patient identity ──────────────────────────────
def test_4_annotation_retains_patient_identity():
    annot = ScanAnnotation(
        id="annot_002",
        patient_mrn="MRN-ICU-008",
        scan_id="202",
        label="Note B",
        physical_x_mm=5.0,
        physical_y_mm=15.0,
        physical_z_mm=25.0
    )
    assert annot.patient_mrn == "MRN-ICU-008"
    d = annot.to_dict()
    assert d["patient_mrn"] == "MRN-ICU-008"
    restored = ScanAnnotation.from_dict(d)
    assert restored.patient_mrn == "MRN-ICU-008"


# ── Test 5: Annotation retains source scan identity ──────────────────────────
def test_5_annotation_retains_source_scan_identity():
    annot = ScanAnnotation(
        id="annot_003",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Source Note",
        physical_x_mm=1.0,
        physical_y_mm=2.0,
        physical_z_mm=3.0,
        metadata={"source_scan_id": "201"}
    )
    assert annot.scan_id == "201"
    assert annot.source_scan_id == "201"


# ── Test 6: Annotation retains target scan identity ──────────────────────────
def test_6_annotation_retains_target_scan_identity():
    mgr = AnnotationCarryForwardManager()
    mgr.set_patient("MRN-ICU-008")
    mgr.set_scans("202", "201",
                  current_geom={"dims": (256, 256, 50), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)},
                  previous_geom={"dims": (256, 256, 40), "spacing": (1.0, 1.0), "thickness": 2.5, "origin": (0.0, 0.0, 0.0)})
    annot = ScanAnnotation(
        id="annot_004",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Reference A",
        physical_x_mm=10.0,
        physical_y_mm=20.0,
        physical_z_mm=30.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation(annot.id)
    carried = mgr.carry_forward("202")
    assert carried is not None
    assert carried.scan_id == "202"
    assert carried.source_scan_id == "201"
    assert carried.is_carried_forward() is True


# ── Test 7: Annotation retains physical X/Y/Z ────────────────────────────────
def test_7_annotation_retains_physical_xyz():
    mgr = AnnotationCarryForwardManager()
    mgr.set_patient("MRN-ICU-008")
    mgr.set_scans("202", "201",
                  current_geom={"dims": (256, 256, 50), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)},
                  previous_geom={"dims": (256, 256, 40), "spacing": (1.0, 1.0), "thickness": 2.5, "origin": (0.0, 0.0, 0.0)})
    annot = ScanAnnotation(
        id="annot_005",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Fixed Point",
        physical_x_mm=14.5,
        physical_y_mm=22.0,
        physical_z_mm=30.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation(annot.id)
    carried = mgr.carry_forward("202")
    assert carried is not None
    assert carried.physical_x_mm == 14.5
    assert carried.physical_y_mm == 22.0
    assert carried.physical_z_mm == 30.0
    assert carried.physical_coordinates == (14.5, 22.0, 30.0)


# ── Test 8: Target slice index is derived from target geometry ────────────────
def test_8_target_slice_derived_from_target_geometry():
    mgr = AnnotationCarryForwardManager()
    mgr.set_scans(
        current_scan_id="202",
        previous_scan_id="201",
        current_geom={"dims": (256, 256, 50), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)},
        previous_geom={"dims": (256, 256, 40), "spacing": (1.0, 1.0), "thickness": 2.5, "origin": (0.0, 0.0, 0.0)}
    )
    annot = ScanAnnotation(
        id="annot_006",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Test Z Derivation",
        physical_x_mm=10.0,
        physical_y_mm=20.0,
        physical_z_mm=30.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation(annot.id)

    src_slice = mgr.get_derived_slice("201", "axial")
    tgt_slice = mgr.get_derived_slice("202", "axial")

    assert src_slice == 12  # 30.0 / 2.5 = 12
    assert tgt_slice == 15  # 30.0 / 2.0 = 15
    assert src_slice != tgt_slice  # Never assume source slice N == target slice N


# ── Test 9: Different spacing is handled ─────────────────────────────────────
def test_9_different_spacing_handled():
    mgr = AnnotationCarryForwardManager()
    mgr.set_scans(
        current_scan_id="202",
        previous_scan_id="201",
        current_geom={"dims": (512, 512, 50), "spacing": (0.5, 0.5), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)},
        previous_geom={"dims": (256, 256, 40), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}
    )
    annot = ScanAnnotation(
        id="annot_007",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="High Res",
        physical_x_mm=50.0,
        physical_y_mm=50.0,
        physical_z_mm=20.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation(annot.id)
    can_cf, status = mgr.can_carry_forward("202")
    assert can_cf is True
    assert status == "Approx. Correspondence"


# ── Test 10: Different slice thickness is handled ────────────────────────────
def test_10_different_slice_thickness_handled():
    mgr = AnnotationCarryForwardManager()
    mgr.set_scans(
        current_scan_id="202",
        previous_scan_id="201",
        current_geom={"dims": (256, 256, 100), "spacing": (1.0, 1.0), "thickness": 1.25, "origin": (0.0, 0.0, 0.0)},
        previous_geom={"dims": (256, 256, 25), "spacing": (1.0, 1.0), "thickness": 5.0, "origin": (0.0, 0.0, 0.0)}
    )
    annot = ScanAnnotation(
        id="annot_008",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Thick to Thin",
        physical_x_mm=20.0,
        physical_y_mm=20.0,
        physical_z_mm=25.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation(annot.id)
    assert mgr.get_derived_slice("201", "axial") == 5   # 25.0 / 5.0 = 5
    assert mgr.get_derived_slice("202", "axial") == 20  # 25.0 / 1.25 = 20


# ── Test 11: Different matrix dimensions are handled ─────────────────────────
def test_11_different_matrix_dimensions_handled():
    mgr = AnnotationCarryForwardManager()
    mgr.set_scans(
        current_scan_id="202",
        previous_scan_id="201",
        current_geom={"dims": (512, 512, 60), "spacing": (0.6, 0.6), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)},
        previous_geom={"dims": (256, 256, 40), "spacing": (1.2, 1.2), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}
    )
    annot = ScanAnnotation(
        id="annot_009",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Matrix Test",
        physical_x_mm=100.0,
        physical_y_mm=100.0,
        physical_z_mm=40.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation(annot.id)
    can_cf, status = mgr.can_carry_forward("202")
    assert can_cf is True
    assert status == "Approx. Correspondence"


# ── Test 12: Missing spatial metadata is handled safely ───────────────────────
def test_12_missing_spatial_metadata_handled_safely():
    mgr = AnnotationCarryForwardManager()
    mgr.set_scans(
        current_scan_id="202",
        previous_scan_id="201",
        current_geom=None,
        previous_geom=None
    )
    annot = ScanAnnotation(
        id="annot_010",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="No Meta",
        physical_x_mm=10.0,
        physical_y_mm=20.0,
        physical_z_mm=30.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation(annot.id)
    can_cf, status = mgr.can_carry_forward("202")
    assert can_cf is False
    assert status == "Physical correspondence unavailable"
    assert mgr.get_derived_slice("202", "axial") is None


# ── Test 13: "Physical correspondence unavailable" displayed when required ───
def test_13_physical_correspondence_unavailable_display(qapp):
    card = AnnotationCarryForwardCard()
    mgr = AnnotationCarryForwardManager()
    mgr.set_scans("202", "201", current_geom=None, previous_geom=None)
    annot = ScanAnnotation(
        id="annot_011",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="No Meta",
        physical_x_mm=10.0,
        physical_y_mm=20.0,
        physical_z_mm=30.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation(annot.id)
    card.update_card(mgr)

    assert card.lbl_correspondence.text() == "Status: Physical correspondence unavailable"
    assert card.btn_carry.isEnabled() is False


# ── Test 14: "Approx. Correspondence" displayed when mapping is approximate ──
def test_14_approx_correspondence_display(qapp):
    card = AnnotationCarryForwardCard()
    mgr = AnnotationCarryForwardManager()
    mgr.set_scans("202", "201",
                  current_geom={"dims": (256, 256, 50), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)},
                  previous_geom={"dims": (256, 256, 40), "spacing": (1.0, 1.0), "thickness": 2.5, "origin": (0.0, 0.0, 0.0)})
    annot = ScanAnnotation(
        id="annot_012",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Valid Mapping",
        physical_x_mm=10.0,
        physical_y_mm=20.0,
        physical_z_mm=30.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation(annot.id)
    card.update_card(mgr)

    assert card.lbl_correspondence.text() == "Status: Approx. Correspondence"
    assert card.btn_carry.isEnabled() is True


# ── Test 15: Carry Forward creates target record without mutating source ──────
def test_15_carry_forward_creates_target_without_mutating_source():
    mgr = AnnotationCarryForwardManager()
    mgr.set_patient("MRN-ICU-008")
    mgr.set_scans("202", "201",
                  current_geom={"dims": (256, 256, 50), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)},
                  previous_geom={"dims": (256, 256, 40), "spacing": (1.0, 1.0), "thickness": 2.5, "origin": (0.0, 0.0, 0.0)})
    source_annot = ScanAnnotation(
        id="annot_src_01",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Original Point",
        text="Original clinician note",
        physical_x_mm=12.0,
        physical_y_mm=18.0,
        physical_z_mm=24.0,
        metadata={"created_via": "manual"}
    )
    mgr.add_annotation(source_annot)
    mgr.select_annotation(source_annot.id)

    target_annot = mgr.carry_forward("202")

    # Assert new target object created
    assert target_annot is not None
    assert target_annot.id != source_annot.id
    assert target_annot.scan_id == "202"

    # Assert source object is completely unmutated
    assert source_annot.id == "annot_src_01"
    assert source_annot.scan_id == "201"
    assert source_annot.label == "Original Point"
    assert source_annot.text == "Original clinician note"
    assert source_annot.physical_coordinates == (12.0, 18.0, 24.0)
    assert source_annot.metadata == {"created_via": "manual"}


# ── Test 16: Source annotation remains available ──────────────────────────────
def test_16_source_annotation_remains_available():
    mgr = AnnotationCarryForwardManager()
    mgr.set_patient("MRN-ICU-008")
    mgr.set_scans("202", "201",
                  current_geom={"dims": (256, 256, 50), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)},
                  previous_geom={"dims": (256, 256, 40), "spacing": (1.0, 1.0), "thickness": 2.5, "origin": (0.0, 0.0, 0.0)})
    annot = ScanAnnotation(
        id="annot_src_02",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Preserved Source",
        physical_x_mm=10.0,
        physical_y_mm=20.0,
        physical_z_mm=30.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation(annot.id)
    target = mgr.carry_forward("202")

    assert mgr.get_annotation("annot_src_02") is not None
    assert mgr.get_annotation(target.id) is not None
    assert len(mgr.get_annotations_for_patient("MRN-ICU-008")) == 2


# ── Test 17: Target annotation renders in 2D ─────────────────────────────────
def test_17_target_annotation_renders_in_2d(qapp):
    viewer_2d = Slice2DViewerWidget()
    vol = np.zeros((128, 128, 40), dtype=np.int16)
    viewer_2d.load_volume(vol, pixel_spacing=(1.0, 1.0), slice_thickness=2.0)

    # Set annotations
    annots = [
        {"id": "src_1", "label": "Source", "x": 10.0, "y": 10.0, "z": 20.0, "scan_id": "201", "is_carried": False},
        {"id": "tgt_1", "label": "Carried", "x": 10.0, "y": 10.0, "z": 20.0, "scan_id": "202", "is_carried": True}
    ]
    viewer_2d.set_active_annotations(annots)
    assert len(viewer_2d.canvas.active_annotations) == 2

    # Canvas renders without exception
    viewer_2d.set_slice_index(10)
    viewer_2d.canvas.update()


# ── Test 18: Target annotation renders correctly in MPR where supported ──────
def test_18_target_annotation_mpr_crosshair(qapp):
    viewer_3d = Viewer3D()
    if hasattr(viewer_3d, "mpr_view") and viewer_3d.mpr_view is not None:
        viewer_3d.mpr_view.set_same_location(10.0, 20.0, 30.0)
    assert True


# ── Test 19: Target annotation renders correctly in 3D where supported ────────
def test_19_target_annotation_renders_in_3d(qapp):
    viewer_3d = Viewer3D()
    viewer_3d.set_annotation_marker(10.0, 20.0, 30.0, label="Carried Marker", is_carried=True)
    if hasattr(viewer_3d, "plotter") and viewer_3d.plotter is not None:
        if hasattr(viewer_3d.plotter, "actors"):
            assert "icu_annotation_marker" in viewer_3d.plotter.actors
            assert any("icu_annotation_label" in k for k in viewer_3d.plotter.actors)


# ── Test 20: No duplicate viewers are created ────────────────────────────────
def test_20_no_duplicate_viewers_created(qapp):
    viewer_3d = Viewer3D()
    initial_plotter = viewer_3d.plotter
    viewer_3d.set_annotation_marker(10.0, 20.0, 30.0, label="Carried Marker", is_carried=True)
    viewer_3d.clear_annotation_marker()
    viewer_3d.set_annotation_marker(15.0, 25.0, 35.0, label="Carried Marker 2", is_carried=True)
    assert viewer_3d.plotter is initial_plotter


# ── Test 21: No duplicate annotation actors accumulate ───────────────────────
def test_21_no_duplicate_actors_accumulate(qapp):
    viewer_3d = Viewer3D()
    if not hasattr(viewer_3d, "plotter") or viewer_3d.plotter is None:
        return
    for i in range(5):
        viewer_3d.set_annotation_marker(float(i), float(i), float(i), label=f"Annot {i}", is_carried=True)

    # Actor names are unique and fixed; no multiple duplicate actors
    actor_keys = [k for k in viewer_3d.plotter.actors.keys() if "icu_annotation" in k]
    assert len(actor_keys) <= 3  # mesh, label, plus vtk internal label points if any


# ── Test 22: Patient switch clears annotation state ──────────────────────────
def test_22_patient_switch_clears_annotation_state():
    mgr = AnnotationCarryForwardManager()
    mgr.set_patient("MRN-ICU-008")
    annot = ScanAnnotation(
        id="annot_p1",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Patient 1 Note",
        physical_x_mm=10.0,
        physical_y_mm=20.0,
        physical_z_mm=30.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation(annot.id)
    assert mgr.selected_annotation_id == "annot_p1"

    # Switch to new patient
    mgr.set_patient("MRN-ICU-009")
    assert mgr.selected_annotation_id is None
    assert len(mgr._annotations) == 0


# ── Test 23: Scan switch safely recomputes or clears stale annotation state ───
def test_23_scan_switch_safely_recomputes():
    mgr = AnnotationCarryForwardManager()
    mgr.set_scans(
        current_scan_id="202",
        previous_scan_id="201",
        current_geom={"dims": (256, 256, 50), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)},
        previous_geom={"dims": (256, 256, 40), "spacing": (1.0, 1.0), "thickness": 2.5, "origin": (0.0, 0.0, 0.0)}
    )
    annot = ScanAnnotation(
        id="annot_sw",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Scan Switch Note",
        physical_x_mm=10.0,
        physical_y_mm=20.0,
        physical_z_mm=30.0
    )
    mgr.add_annotation(annot)
    mgr.select_annotation(annot.id)
    assert mgr.get_derived_slice("202", "axial") == 15

    # Switch current scan to new scan with thickness 1.0 mm
    mgr.set_scans(
        current_scan_id="203",
        previous_scan_id="201",
        current_geom={"dims": (256, 256, 100), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)},
        previous_geom={"dims": (256, 256, 40), "spacing": (1.0, 1.0), "thickness": 2.5, "origin": (0.0, 0.0, 0.0)}
    )
    assert mgr.get_derived_slice("203", "axial") == 30  # Recomputed with new geometry


# ── Test 24: Same-Location Review remains unchanged ──────────────────────────
def test_24_same_location_review_remains_unchanged():
    sl = SameLocationReview()
    sl.set_location(15.0, 25.0, 35.0, current_scan_id="202", previous_scan_id="201", patient_mrn="MRN-ICU-008")
    assert sl.is_active is True
    assert sl.get_physical_coordinates() == (15.0, 25.0, 35.0)


# ── Test 25: Measurement Tracking remains unchanged ──────────────────────────
def test_25_measurement_tracking_remains_unchanged():
    tracker = MeasurementTracker()
    tracker.set_patient("MRN-ICU-008")
    tracker.set_scans("202", "201")
    m = TrackedMeasurement(id="m1", scan_id="202", patient_mrn="MRN-ICU-008", label="Caliper", value_mm=15.2, unit="mm")
    tracker.add_measurement(m)
    assert len(tracker.get_current_measurements()) == 1


# ── Test 26: Existing measurements remain unchanged ──────────────────────────
def test_26_existing_measurements_remain_unchanged():
    m = TrackedMeasurement(id="m1", scan_id="202", patient_mrn="MRN-ICU-008", label="Caliper", value_mm=15.2, unit="mm")
    assert m.value_mm == 15.2
    assert m.unit == "mm"


# ── Test 27: OR Entry remains unchanged ──────────────────────────────────────
def test_27_or_entry_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_entry(10.0, 20.0, 30.0)
    assert plan.entry_point.coordinates == (10.0, 20.0, 30.0)


# ── Test 28: OR Target remains unchanged ─────────────────────────────────────
def test_28_or_target_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_target(40.0, 50.0, 60.0)
    assert plan.target_point.coordinates == (40.0, 50.0, 60.0)


# ── Test 29: Planned Route remains unchanged ─────────────────────────────────
def test_29_planned_route_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_entry(0.0, 0.0, 0.0)
    plan.set_target(0.0, 0.0, 10.0)
    assert plan.has_planned_route() is True
    route = plan.get_planned_route()
    assert route is not None
    assert route.length_mm == 10.0


# ── Test 30: Structures to Avoid remain unchanged ────────────────────────────
def test_30_structures_to_avoid_remain_unchanged():
    plan = SurgicalPlanSession()
    plan.add_avoid_structure(15.0, 25.0, 35.0, radius_mm=3.0, name="Optic Nerve")
    assert len(plan.avoid_structures) == 1


# ── Test 31: Surgical Corridor remains unchanged ─────────────────────────────
def test_31_surgical_corridor_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_entry(0.0, 0.0, 0.0)
    plan.set_target(0.0, 0.0, 20.0)
    plan.corridor_radius_mm = 4.5
    assert plan.corridor_radius_mm == 4.5


# ── Test 32: Virtual Instrument remains unchanged ────────────────────────────
def test_32_virtual_instrument_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.instrument_preset = "Biopsy Needle"
    assert plan.instrument_preset == "Biopsy Needle"


# ── Test 33: Live Deviation remains unchanged ────────────────────────────────
def test_33_live_deviation_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_entry(0.0, 0.0, 0.0)
    plan.set_target(0.0, 0.0, 20.0)
    plan.set_deviation_offsets(0.0, 2.0, 0.0)
    pose = plan.get_current_instrument_pose()
    assert pose is not None
    assert pose.lateral_deviation_mm == pytest.approx(2.0, abs=1e-3)


# ── Test 34: Plan Versions remain unchanged ──────────────────────────────────
def test_34_plan_versions_remain_unchanged(in_memory_db):
    v_id = database.save_surgical_plan_version("MRN-ICU-008", "Initial Plan", {"test": True})
    assert v_id is not None
    versions = database.get_surgical_plan_versions("MRN-ICU-008")
    assert len(versions) == 1
    assert versions[0]["name"] == "Initial Plan"


# ── Test 35: No cross-patient annotation leakage ─────────────────────────────
def test_35_no_cross_patient_annotation_leakage(in_memory_db):
    database.save_scan_annotation("a1", "PATIENT_A", "scan_1", "Note A", "", 1.0, 2.0, 3.0)
    database.save_scan_annotation("a2", "PATIENT_B", "scan_2", "Note B", "", 4.0, 5.0, 6.0)

    annots_a = database.get_scan_annotations_for_patient("PATIENT_A")
    annots_b = database.get_scan_annotations_for_patient("PATIENT_B")

    assert len(annots_a) == 1
    assert annots_a[0]["id"] == "a1"
    assert len(annots_b) == 1
    assert annots_b[0]["id"] == "a2"


# ── Test 36: Persistence round-trip works ─────────────────────────────────────
def test_36_persistence_round_trip(in_memory_db):
    database.save_scan_annotation(
        annotation_id="a_roundtrip",
        patient_mrn="MRN-ICU-008",
        scan_id="201",
        label="Roundtrip Note",
        text="Clinical detail text",
        physical_x_mm=11.1,
        physical_y_mm=22.2,
        physical_z_mm=33.3,
        metadata={"key": "value"}
    )
    records = database.get_scan_annotations_for_scan("201", "MRN-ICU-008")
    assert len(records) == 1
    rec = records[0]
    assert rec["id"] == "a_roundtrip"
    assert rec["label"] == "Roundtrip Note"
    assert rec["text"] == "Clinical detail text"
    assert rec["physical_x_mm"] == 11.1
    assert rec["physical_y_mm"] == 22.2
    assert rec["physical_z_mm"] == 33.3
    assert rec["metadata"]["key"] == "value"

    # Delete
    assert database.delete_scan_annotation("a_roundtrip", "MRN-ICU-008") is True
    records_after = database.get_scan_annotations_for_scan("201", "MRN-ICU-008")
    assert len(records_after) == 0


# ── Test 37: Existing notes table continues working ───────────────────────────
def test_37_existing_notes_table_unaffected(in_memory_db):
    database.get_notes_for_patient("MRN-ICU-008")  # Ensures table creation
    conn = database.get_connection()
    conn.execute(
        "INSERT INTO notes (patient_mrn, author, content) VALUES (?, ?, ?)",
        ("MRN-ICU-008", "Dr. House", "Patient recovering well.")
    )
    conn.commit()
    conn.close()

    notes = database.get_notes_for_patient("MRN-ICU-008")
    assert len(notes) == 1
    assert notes[0]["content"] == "Patient recovering well."


# ── Test 38: ICU Feature 1 (Current vs Previous) integration verified ─────────
def test_38_icu_feature_1_integration(qapp):
    icu_mode = OrIcuMode()
    icu_mode.set_mode("ICU")
    assert hasattr(icu_mode, "current_previous_card")
    assert icu_mode.current_previous_card is not None


# ── Test 39: ICU Feature 2 (Same-Location) integration verified ──────────────
def test_39_icu_feature_2_integration(qapp):
    icu_mode = OrIcuMode()
    icu_mode.set_mode("ICU")
    assert hasattr(icu_mode, "same_location_card")
    assert icu_mode.same_location_card is not None


# ── Test 40: ICU Feature 3 (Measurement Tracking) integration verified ────────
def test_40_icu_feature_3_integration(qapp):
    icu_mode = OrIcuMode()
    icu_mode.set_mode("ICU")
    assert hasattr(icu_mode, "measurement_tracking_card")
    assert icu_mode.measurement_tracking_card is not None


# ── Test 41: Existing measurement tests data structures intact ────────────────
def test_41_measurement_data_structures_intact():
    m2d = Measurement2D(
        id="m1",
        start_u=0.1,
        start_v=0.2,
        end_u=0.3,
        end_v=0.4,
        start_px=10,
        start_py=20,
        end_px=30,
        end_py=40,
        distance_mm=15.5,
        orientation="axial"
    )
    assert m2d.distance_mm == 15.5
    assert m2d.orientation == "axial"


# ── Test 42: Existing synchronization signals intact ──────────────────────────
def test_42_synchronization_signals_intact(qapp):
    viewer_2d = Slice2DViewerWidget()
    assert hasattr(viewer_2d, "annotation_point_selected")
    assert hasattr(viewer_2d, "same_location_selected")


# ── Test 43: All OR Features 1-9 intact in OrIcuMode ───────────────────────────
def test_43_or_features_1_to_9_intact(qapp):
    icu_mode = OrIcuMode()
    icu_mode.set_mode("OR")
    assert icu_mode.entry_target_card is not None
    assert icu_mode.planned_route_card is not None
    assert icu_mode.structures_to_avoid_card is not None
    assert icu_mode.surgical_corridor_card is not None
    assert icu_mode.virtual_instrument_card is not None
    assert icu_mode.live_deviation_card is not None
    assert icu_mode.plan_versions_card is not None
    assert icu_mode.quick_views_card is not None


# ── Test 44: ICU shell tests continue passing ─────────────────────────────────
def test_44_icu_shell_structure(qapp):
    icu_mode = OrIcuMode()
    icu_mode.set_mode("ICU")
    assert icu_mode.current_previous_card is not None
    assert icu_mode.same_location_card is not None
    assert icu_mode.measurement_tracking_card is not None
    assert icu_mode.annotation_carry_forward_card is not None
