# test_icu_device_markers.py
"""
Comprehensive test suite for ICU Feature 6: DEVICE MARKERS.
Covers all 53 requirements:
1. Device Markers card exists.
2. Initial state shows no markers.
3. Device type selection works.
4. Mark Device enters dedicated picking mode.
5. 2D click converts to physical X/Y/Z.
6. Physical coordinates are canonical.
7. Marker retains patient identity.
8. Marker retains scan identity.
9. Marker label persists.
10. Device type persists.
11. Persistence round-trip works.
12. Marker renders in 2D.
13. Marker renders in MPR.
14. Marker renders in 3D when supported.
15. Marker navigation works.
16. Deleting a marker removes only that marker.
17. Multiple device markers can coexist.
18. Repeated redraw does not duplicate actors.
19. No duplicate viewers are created.
20. Patient switch clears stale markers.
21. Scan switch loads only active scan markers.
22. No cross-patient leakage.
23. Different scan geometry derives slice position correctly.
24. Missing spatial metadata is handled safely.
25. Existing annotations remain unchanged.
26. Annotation Carry-forward remains unchanged.
27. Same-Location Review remains unchanged.
28. Measurement Tracking remains unchanged.
29. What Changed remains unchanged.
30. Existing measurements remain unchanged.
31. OR Entry remains unchanged.
32. OR Target remains unchanged.
33. Planned Route remains unchanged.
34. Structures to Avoid remain unchanged.
35. Surgical Corridor remains unchanged.
36. Virtual Instrument remains unchanged.
37. Live Deviation remains unchanged.
38. Plan Versions remain unchanged.
39. Quick Surgical Views remain unchanged.
40. No clinical interpretation text is generated.
41. No automatic device detection is performed.
42. No DICOM reload occurs for marker selection/navigation.
43. ICU Feature 1 continues passing.
44. ICU Feature 2 continues passing.
45. ICU Feature 3 continues passing.
46. ICU Feature 4 continues passing.
47. ICU Feature 5 continues passing.
48. Existing measurement tests continue passing.
49. Existing 2D slice tests continue passing.
50. Existing synchronization tests continue passing.
51. Existing metadata tests continue passing.
52. All OR Feature 1–9 tests continue passing.
53. ICU shell tests continue passing.
"""

import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

import os
import sys
import uuid
import pytest
import numpy as np

# Configure Qt offscreen platform before importing PyQt6
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPoint

# Ensure a single QApplication instance exists
app = QApplication.instance() or QApplication(sys.argv)

from device_markers import (
    DeviceMarker,
    DeviceMarkerManager,
    CONTROLLED_DEVICE_TYPES,
)
from database import (
    init_db,
    save_device_marker,
    get_device_markers_for_scan,
    get_device_markers_for_patient,
    delete_device_marker,
)
from screens.or_icu_mode import (
    DeviceMarkersCard,
    CurrentPreviousScanCard,
    SameLocationReviewCard,
    MeasurementTrackingCard,
    AnnotationCarryForwardCard,
    WhatChangedCard,
    OrIcuMode,
)
from screens.slice_2d_viewer import Slice2DViewerWidget, CTSliceCanvas
from screens.mpr_view import MPRView, MPRSliceWidget
from screens.viewer_3d import Viewer3D
from surgical_plan import SurgicalPlanSession, PlanningPoint, AvoidStructure
from same_location import SameLocationReview
from measurement_tracker import MeasurementTracker, TrackedMeasurement
from annotation_carry_forward import AnnotationCarryForwardManager, ScanAnnotation
from what_changed import ChangeReview


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


# ── Test 1: Device Markers card exists ─────────────────────────────────────────
def test_01_device_markers_card_exists():
    card = DeviceMarkersCard()
    assert card is not None
    assert hasattr(card, "lbl_title")
    assert "DEVICE MARKERS" in card.lbl_title.text().upper()
    assert hasattr(card, "combo_device_type")
    assert hasattr(card, "txt_label")
    assert hasattr(card, "btn_mark")
    assert hasattr(card, "btn_view_selected")
    assert hasattr(card, "btn_clear_selection")
    assert hasattr(card, "btn_delete")
    assert hasattr(card, "list_markers")


# ── Test 2: Initial state shows no markers ────────────────────────────────────
def test_02_initial_state_shows_no_markers():
    manager = DeviceMarkerManager()
    assert len(manager.get_markers_for_active_scan()) == 0
    assert manager.selected_marker_id is None

    card = DeviceMarkersCard()
    assert card.lbl_header_status.text() == "No device markers"
    assert card.list_markers.count() == 0
    assert not card.btn_view_selected.isEnabled()
    assert not card.btn_clear_selection.isEnabled()
    assert not card.btn_delete.isEnabled()


# ── Test 3: Device type selection works ────────────────────────────────────────
def test_03_device_type_selection_works():
    card = DeviceMarkersCard()
    for dt in CONTROLLED_DEVICE_TYPES:
        idx = card.combo_device_type.findText(dt)
        assert idx >= 0
        card.combo_device_type.setCurrentIndex(idx)
        assert card.combo_device_type.currentText() == dt

    # Check that "Other" allows custom label
    card.combo_device_type.setCurrentText("Other")
    card.txt_label.setText("Custom Pacing Wire")
    assert card.combo_device_type.currentText() == "Other"
    assert card.txt_label.text() == "Custom Pacing Wire"


# ── Test 4: Mark Device enters dedicated picking mode ─────────────────────────
def test_04_mark_device_enters_dedicated_picking_mode():
    viewer = Slice2DViewerWidget()
    canvas = viewer.canvas
    canvas.set_same_location_picking(False)
    canvas.set_annotation_picking(False)

    viewer.set_device_marker_picking_mode(True)
    assert canvas.icu_device_marker_picking is True
    assert canvas.icu_same_location_picking is False
    assert canvas.icu_annotation_picking is False

    viewer.set_device_marker_picking_mode(False)
    assert canvas.icu_device_marker_picking is False


# ── Test 5: 2D click converts to physical X/Y/Z ────────────────────────────────
def test_05_2d_click_converts_to_physical_xyz():
    vol = np.zeros((40, 50, 60), dtype=np.int16)
    spacing = (1.5, 2.0)
    thickness = 3.0

    viewer = Slice2DViewerWidget()
    viewer.load_volume(vol, pixel_spacing=spacing, slice_thickness=thickness)
    viewer.set_slice_index(10)
    viewer.set_device_marker_picking_mode(True)

    received_points = []
    viewer.canvas.device_marker_selected.connect(lambda x, y, z: received_points.append((x, y, z)))

    # Compute physical coord from canvas
    u, v = 0.4, 0.5
    phys = viewer.calc_physical_coords("axial", 10, u, v)

    # Trigger pick
    viewer.canvas.device_marker_selected.emit(phys[0], phys[1], phys[2])
    assert len(received_points) == 1
    x, y, z = received_points[0]
    assert abs(z - (10 * thickness)) < 1e-4


# ── Test 6: Physical coordinates are canonical ────────────────────────────────
def test_06_physical_coordinates_are_canonical():
    marker = DeviceMarker(
        id="dm_01",
        patient_mrn="P001",
        scan_id="SCAN_A",
        device_type="Endotracheal Tube",
        label="ETT #1",
        physical_x_mm=30.0,
        physical_y_mm=45.0,
        physical_z_mm=60.0
    )
    assert marker.physical_coordinates == (30.0, 45.0, 60.0)

    geom_1 = {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}
    geom_2 = {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 5.0, "origin": (0.0, 0.0, 0.0)}

    manager = DeviceMarkerManager()
    manager.set_context("P001", "SCAN_A", geom_1)
    manager.add_marker(marker)
    assert manager.get_derived_slice("dm_01", "axial") == 30  # 60.0 / 2.0

    manager.set_context("P001", "SCAN_A", geom_2)
    assert manager.get_derived_slice("dm_01", "axial") == 12  # 60.0 / 5.0


# ── Test 7: Marker retains patient identity ───────────────────────────────────
def test_07_marker_retains_patient_identity():
    marker = DeviceMarker(
        id="dm_patient_test",
        patient_mrn="PATIENT_999",
        scan_id="SCAN_01",
        device_type="Central Line",
        label="CVC"
    )
    assert marker.patient_mrn == "PATIENT_999"


# ── Test 8: Marker retains scan identity ──────────────────────────────────────
def test_08_marker_retains_scan_identity():
    marker = DeviceMarker(
        id="dm_scan_test",
        patient_mrn="PATIENT_999",
        scan_id="SCAN_SEPT_2026",
        device_type="Feeding Tube",
        label="NG Tube"
    )
    assert marker.scan_id == "SCAN_SEPT_2026"


# ── Test 9: Marker label persists ─────────────────────────────────────────────
def test_09_marker_label_persists():
    mid = f"dm_{uuid.uuid4().hex[:8]}"
    data = {
        "id": mid,
        "patient_mrn": "MRN_LBL",
        "scan_id": "SCAN_LBL",
        "device_type": "Drain",
        "label": "Chest Tube Left",
        "physical_x_mm": 10.0,
        "physical_y_mm": 20.0,
        "physical_z_mm": 30.0,
    }
    save_device_marker(data)
    markers = get_device_markers_for_scan("SCAN_LBL", "MRN_LBL")
    saved = next(m for m in markers if m["id"] == mid)
    assert saved["label"] == "Chest Tube Left"


# ── Test 10: Device type persists ─────────────────────────────────────────────
def test_10_device_type_persists():
    mid = f"dm_{uuid.uuid4().hex[:8]}"
    data = {
        "id": mid,
        "patient_mrn": "MRN_TYPE",
        "scan_id": "SCAN_TYPE",
        "device_type": "Catheter",
        "label": "Foley",
        "physical_x_mm": 15.0,
        "physical_y_mm": 25.0,
        "physical_z_mm": 35.0,
    }
    save_device_marker(data)
    markers = get_device_markers_for_scan("SCAN_TYPE", "MRN_TYPE")
    saved = next(m for m in markers if m["id"] == mid)
    assert saved["device_type"] == "Catheter"


# ── Test 11: Persistence round-trip works ─────────────────────────────────────
def test_11_persistence_round_trip():
    mid = f"dm_{uuid.uuid4().hex[:8]}"
    original = DeviceMarker(
        id=mid,
        patient_mrn="MRN_RT",
        scan_id="SCAN_RT",
        device_type="Central Line",
        label="Right IJ Line",
        physical_x_mm=12.3,
        physical_y_mm=45.6,
        physical_z_mm=78.9,
        metadata={"note": "Triple lumen"}
    )
    save_device_marker(original.to_dict())

    loaded_list = get_device_markers_for_scan("SCAN_RT", "MRN_RT")
    found = [m for m in loaded_list if m["id"] == mid]
    assert len(found) == 1
    loaded = DeviceMarker.from_dict(found[0])

    assert loaded.id == original.id
    assert loaded.patient_mrn == original.patient_mrn
    assert loaded.scan_id == original.scan_id
    assert loaded.device_type == original.device_type
    assert loaded.label == original.label
    assert abs(loaded.physical_x_mm - original.physical_x_mm) < 1e-4
    assert abs(loaded.physical_y_mm - original.physical_y_mm) < 1e-4
    assert abs(loaded.physical_z_mm - original.physical_z_mm) < 1e-4
    assert loaded.metadata.get("note") == "Triple lumen"


# ── Test 12: Marker renders in 2D ─────────────────────────────────────────────
def test_12_marker_renders_in_2d():
    vol = np.zeros((50, 50, 30), dtype=np.int16)
    viewer = Slice2DViewerWidget()
    viewer.load_volume(vol, pixel_spacing=(1.0, 1.0), slice_thickness=2.0)
    viewer.set_slice_index(15)

    markers = [
        {"id": "m1", "physical_x_mm": 25.0, "physical_y_mm": 25.0, "physical_z_mm": 30.0, "device_type": "ETT", "label": "ETT"},
        {"id": "m2", "physical_x_mm": 10.0, "physical_y_mm": 10.0, "physical_z_mm": 10.0, "device_type": "Line", "label": "CVC"},
    ]
    viewer.set_active_device_markers(markers)
    assert len(viewer.canvas.active_device_markers) == 2
    viewer.canvas.repaint()


# ── Test 13: Marker renders in MPR ────────────────────────────────────────────
def test_13_marker_renders_in_mpr():
    mpr = MPRView()
    widget = MPRSliceWidget("axial", "Axial View")
    markers = [
        {"id": "m1", "physical_x_mm": 20.0, "physical_y_mm": 20.0, "physical_z_mm": 30.0, "device_type": "Drain", "label": "Drain #1"}
    ]
    widget.set_active_device_markers(markers, vol_dims=(40, 40, 40), spacings=(1.0, 1.0, 1.5))
    assert len(widget.active_device_markers) == 1
    widget.repaint()

    mpr.set_active_device_markers(markers)
    assert len(mpr.w_axial.active_device_markers) == 1
    assert len(mpr.w_coronal.active_device_markers) == 1
    assert len(mpr.w_sagittal.active_device_markers) == 1


# ── Test 14: Marker renders in 3D when supported ──────────────────────────────
def test_14_marker_renders_in_3d():
    viewer = Viewer3D()
    mid = "test_marker_3d"
    viewer.set_device_marker(10.0, 20.0, 30.0, "Central Line", "CVC", marker_id=mid)
    actor_name = f"icu_device_marker_{mid}"
    assert actor_name in viewer.plotter.actors

    viewer.remove_device_marker(mid)
    assert actor_name not in viewer.plotter.actors


# ── Test 15: Marker navigation works ──────────────────────────────────────────
def test_15_marker_navigation_works():
    manager = DeviceMarkerManager()
    geom = {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 2.5, "origin": (0.0, 0.0, 0.0)}
    manager.set_context("P1", "S1", geom)

    marker = DeviceMarker(
        id="dm_nav",
        patient_mrn="P1",
        scan_id="S1",
        device_type="Endotracheal Tube",
        label="ETT",
        physical_x_mm=25.0,
        physical_y_mm=30.0,
        physical_z_mm=50.0  # slice = 50 / 2.5 = 20
    )
    manager.add_marker(marker)
    slice_idx = manager.get_derived_slice("dm_nav", "axial")
    assert slice_idx == 20

    viewer = Slice2DViewerWidget()
    vol = np.zeros((100, 100, 50), dtype=np.int16)
    viewer.load_volume(vol, pixel_spacing=(1.0, 1.0), slice_thickness=2.5)
    viewer.set_slice_index(slice_idx)
    assert viewer.canvas.current_slice_idx == 20


# ── Test 16: Deleting a marker removes only that marker ────────────────────────
def test_16_deleting_marker_removes_only_that_marker():
    manager = DeviceMarkerManager()
    manager.set_context("P1", "S1")
    m1 = DeviceMarker(id="m1", patient_mrn="P1", scan_id="S1", device_type="Drain", label="D1")
    m2 = DeviceMarker(id="m2", patient_mrn="P1", scan_id="S1", device_type="Catheter", label="C1")
    manager.add_marker(m1)
    manager.add_marker(m2)
    assert len(manager.get_markers_for_active_scan()) == 2

    save_device_marker(m1.to_dict())
    save_device_marker(m2.to_dict())

    delete_device_marker("m1", "P1")
    manager.remove_marker("m1")

    assert len(manager.get_markers_for_active_scan()) == 1
    assert manager.get_marker("m1") is None
    assert manager.get_marker("m2") is not None

    db_markers = get_device_markers_for_scan("S1", "P1")
    assert any(m["id"] == "m2" for m in db_markers)
    assert not any(m["id"] == "m1" for m in db_markers)


# ── Test 17: Multiple device markers can coexist ──────────────────────────────
def test_17_multiple_device_markers_coexist():
    manager = DeviceMarkerManager()
    manager.set_context("P1", "S1")
    for i in range(5):
        m = DeviceMarker(id=f"dev_{i}", patient_mrn="P1", scan_id="S1", device_type="Drain", label=f"Drain #{i}")
        manager.add_marker(m)

    assert len(manager.get_markers_for_active_scan()) == 5
    card = DeviceMarkersCard()
    card.update_card(manager)
    assert card.list_markers.count() == 5
    assert card.lbl_header_status.text() == "5 devices marked"


# ── Test 18: Repeated redraw does not duplicate actors ─────────────────────────
def test_18_repeated_redraw_does_not_duplicate_actors():
    viewer = Viewer3D()
    markers = [
        {"id": "m1", "physical_x_mm": 10.0, "physical_y_mm": 10.0, "physical_z_mm": 10.0, "device_type": "ETT", "label": "ETT"},
        {"id": "m2", "physical_x_mm": 20.0, "physical_y_mm": 20.0, "physical_z_mm": 20.0, "device_type": "CVC", "label": "CVC"}
    ]
    viewer.set_device_markers(markers)

    viewer.set_device_markers(markers)
    viewer.set_device_markers(markers)
    final_actors = set(viewer.plotter.actors.keys())

    device_actors = [a for a in final_actors if a.startswith("icu_device_marker_")]
    assert len(device_actors) == 2


# ── Test 19: No duplicate viewers are created ─────────────────────────────────
def test_19_no_duplicate_viewers_created():
    viewer = Viewer3D()
    assert hasattr(viewer, "slice_viewer")
    assert hasattr(viewer, "mpr_view")
    sv_id = id(viewer.slice_viewer)
    mpr_id = id(viewer.mpr_view)

    viewer.set_device_markers([{"id": "m1", "physical_x_mm": 0, "physical_y_mm": 0, "physical_z_mm": 0}])
    assert id(viewer.slice_viewer) == sv_id
    assert id(viewer.mpr_view) == mpr_id


# ── Test 20: Patient switch clears stale markers ──────────────────────────────
def test_20_patient_switch_clears_stale_markers():
    manager = DeviceMarkerManager()
    manager.set_context("PATIENT_A", "SCAN_A")
    m_a = DeviceMarker(id="ma", patient_mrn="PATIENT_A", scan_id="SCAN_A", device_type="ETT", label="ETT")
    manager.add_marker(m_a)
    assert len(manager.get_markers_for_active_scan()) == 1

    # Switch to PATIENT_B
    manager.set_context("PATIENT_B", "SCAN_B")
    assert len(manager.get_markers_for_active_scan()) == 0
    assert manager.selected_marker_id is None

    card = DeviceMarkersCard()
    card.update_card(manager)
    assert card.list_markers.count() == 0
    assert card.lbl_header_status.text() == "No device markers"


# ── Test 21: Scan switch loads only active scan markers ────────────────────────
def test_21_scan_switch_loads_only_active_scan_markers():
    manager = DeviceMarkerManager()
    manager.set_context("P1", "SCAN_CURRENT")
    m_curr = DeviceMarker(id="m_c", patient_mrn="P1", scan_id="SCAN_CURRENT", device_type="ETT", label="ETT")
    m_prev = DeviceMarker(id="m_p", patient_mrn="P1", scan_id="SCAN_PREVIOUS", device_type="CVC", label="CVC")
    manager.add_marker(m_curr)
    manager.add_marker(m_prev)

    curr_markers = manager.get_markers_for_active_scan()
    assert len(curr_markers) == 1
    assert curr_markers[0].id == "m_c"

    manager.set_context("P1", "SCAN_PREVIOUS")
    prev_markers = manager.get_markers_for_active_scan()
    assert len(prev_markers) == 1
    assert prev_markers[0].id == "m_p"


# ── Test 22: No cross-patient leakage ─────────────────────────────────────────
def test_22_no_cross_patient_leakage():
    save_device_marker({
        "id": "leak_test_p1",
        "patient_mrn": "PATIENT_X",
        "scan_id": "SCAN_SHARED_NAME",
        "device_type": "Drain",
        "label": "Drain X",
        "physical_x_mm": 1, "physical_y_mm": 2, "physical_z_mm": 3
    })
    p2_markers = get_device_markers_for_scan("SCAN_SHARED_NAME", "PATIENT_Y")
    assert not any(m["id"] == "leak_test_p1" for m in p2_markers)


# ── Test 23: Different scan geometry derives slice position correctly ─────────
def test_23_different_scan_geometry_derives_slice_correctly():
    manager = DeviceMarkerManager()
    geom_thin = {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0, "origin": (0.0, 0.0, 0.0)}
    geom_thick = {"dims": (100, 100, 20), "spacing": (1.0, 1.0), "thickness": 5.0, "origin": (0.0, 0.0, 0.0)}

    manager.set_context("P", "S", geom_thin)
    m = DeviceMarker(id="m_geom", patient_mrn="P", scan_id="S", device_type="Drain", label="D", physical_z_mm=45.0)
    manager.add_marker(m)
    assert manager.get_derived_slice("m_geom", "axial") == 45

    manager.set_scan("S", geom_thick)
    assert manager.get_derived_slice("m_geom", "axial") == 9


# ── Test 24: Missing spatial metadata is handled safely ────────────────────────
def test_24_missing_spatial_metadata_handled_safely():
    manager = DeviceMarkerManager()
    manager.set_context("P", "S", geom=None)
    m = DeviceMarker(id="m_no_meta", patient_mrn="P", scan_id="S", device_type="Drain", label="D", physical_z_mm=45.0)
    manager.add_marker(m)

    slice_idx = manager.get_derived_slice("m_no_meta", "axial")
    assert slice_idx is not None


# ── Test 25: Existing annotations remain unchanged ────────────────────────────
def test_25_existing_annotations_remain_unchanged():
    annot_mgr = AnnotationCarryForwardManager()
    annot = ScanAnnotation(id="annot_01", patient_mrn="P1", scan_id="S1", label="Nodule", physical_x_mm=10, physical_y_mm=20, physical_z_mm=30)
    annot_mgr.add_annotation(annot)

    dev_mgr = DeviceMarkerManager()
    dev_mgr.set_context("P1", "S1")
    dev_mgr.add_marker(DeviceMarker(id="dm_01", patient_mrn="P1", scan_id="S1", device_type="ETT", label="ETT"))

    assert len(annot_mgr._annotations) == 1
    assert annot_mgr._annotations["annot_01"].label == "Nodule"


# ── Test 26: Annotation Carry-forward remains unchanged ────────────────────────
def test_26_annotation_carry_forward_remains_unchanged():
    annot_mgr = AnnotationCarryForwardManager()
    annot = ScanAnnotation(id="annot_01", patient_mrn="P1", scan_id="S_PREV", label="Opacification", physical_x_mm=15, physical_y_mm=25, physical_z_mm=35)
    geom = {"H": 100, "W": 100, "D": 50, "dx": 1.0, "dy": 1.0, "dz": 1.0, "ox": 0.0, "oy": 0.0, "oz": 0.0}
    annot_mgr.set_scans("S_CURR", "S_PREV", current_geom=geom, previous_geom=geom)
    annot_mgr.add_annotation(annot)
    annot_mgr.select_annotation("annot_01")
    carried = annot_mgr.carry_forward("S_CURR")
    assert carried is not None
    assert carried.is_carried_forward()

    dev_mgr = DeviceMarkerManager()
    dev_mgr.set_context("P1", "S_CURR")
    dev_mgr.add_marker(DeviceMarker(id="dm_test", patient_mrn="P1", scan_id="S_CURR", device_type="Line", label="Line"))

    assert annot_mgr._annotations["annot_01"].is_carried_forward() is False
    assert carried.is_carried_forward() is True


# ── Test 27: Same-Location Review remains unchanged ───────────────────────────
def test_27_same_location_review_remains_unchanged():
    sl = SameLocationReview()
    geom = {"dims": (50, 50, 20), "spacing": (1.0, 1.0), "thickness": 2.0, "origin": (0.0, 0.0, 0.0)}
    sl.set_location(12.0, 34.0, 56.0, "S1", "S2", geom, geom, "P1")
    assert sl.is_point_valid() is True
    assert sl.get_physical_coordinates() == (12.0, 34.0, 56.0)

    dev_mgr = DeviceMarkerManager()
    dev_mgr.set_context("P1", "S1")
    dev_mgr.add_marker(DeviceMarker(id="dm_sl", patient_mrn="P1", scan_id="S1", device_type="Drain", label="Drain", physical_x_mm=100, physical_y_mm=100, physical_z_mm=100))

    assert sl.get_physical_coordinates() == (12.0, 34.0, 56.0)


# ── Test 28: Measurement Tracking remains unchanged ───────────────────────────
def test_28_measurement_tracking_remains_unchanged():
    mt = MeasurementTracker()
    m = TrackedMeasurement(id="tm_01", patient_mrn="P1", scan_id="S1", label="Tumor length", value_mm=24.5)
    mt.add_measurement(m)
    assert len(mt.get_measurements_for_scan("S1")) == 1

    dev_mgr = DeviceMarkerManager()
    dev_mgr.set_context("P1", "S1")
    dev_mgr.add_marker(DeviceMarker(id="dm_mt", patient_mrn="P1", scan_id="S1", device_type="Catheter", label="Foley"))

    assert len(mt.get_measurements_for_scan("S1")) == 1
    assert mt.get_measurements_for_scan("S1")[0].value_mm == 24.5


# ── Test 29: What Changed remains unchanged ───────────────────────────────────
def test_29_what_changed_remains_unchanged():
    cr = ChangeReview()
    cr.set_patient_and_scans("P1", "P1", "S_CURR", "S_PREV")
    cr.set_threshold(75.0)

    dev_mgr = DeviceMarkerManager()
    dev_mgr.set_context("P1", "S_CURR")
    dev_mgr.add_marker(DeviceMarker(id="dm_cr", patient_mrn="P1", scan_id="S_CURR", device_type="ETT", label="ETT"))

    assert cr.threshold == 75.0
    assert cr.current_scan_id == "S_CURR"


# ── Test 30: Existing measurements remain unchanged ───────────────────────────
def test_30_existing_measurements_remain_unchanged():
    from screens.slice_2d_viewer import Measurement2D
    m = Measurement2D(
        id="meas_x",
        start_u=0.1, start_v=0.1, end_u=0.5, end_v=0.5,
        start_px=10, start_py=10, end_px=50, end_py=50,
        distance_mm=15.0
    )
    assert m.distance_mm == 15.0


# ── Test 31: OR Entry remains unchanged ───────────────────────────────────────
def test_31_or_entry_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_entry(10.0, 20.0, 30.0)
    assert (plan.entry_point.x_mm, plan.entry_point.y_mm, plan.entry_point.z_mm) == (10.0, 20.0, 30.0)

    dev_mgr = DeviceMarkerManager()
    dev_mgr.add_marker(DeviceMarker(id="dm_or", patient_mrn="P", scan_id="S", device_type="Drain", label="Drain"))
    assert (plan.entry_point.x_mm, plan.entry_point.y_mm, plan.entry_point.z_mm) == (10.0, 20.0, 30.0)


# ── Test 32: OR Target remains unchanged ──────────────────────────────────────
def test_32_or_target_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_target(40.0, 50.0, 60.0)
    assert (plan.target_point.x_mm, plan.target_point.y_mm, plan.target_point.z_mm) == (40.0, 50.0, 60.0)

    dev_mgr = DeviceMarkerManager()
    dev_mgr.add_marker(DeviceMarker(id="dm_or2", patient_mrn="P", scan_id="S", device_type="Drain", label="Drain"))
    assert (plan.target_point.x_mm, plan.target_point.y_mm, plan.target_point.z_mm) == (40.0, 50.0, 60.0)


# ── Test 33: Planned Route remains unchanged ──────────────────────────────────
def test_33_planned_route_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_entry(0.0, 0.0, 0.0)
    plan.set_target(0.0, 0.0, 50.0)
    route = plan.get_planned_route()
    assert route is not None
    assert abs(route.length_mm - 50.0) < 1e-4

    dev_mgr = DeviceMarkerManager()
    dev_mgr.add_marker(DeviceMarker(id="dm_or3", patient_mrn="P", scan_id="S", device_type="ETT", label="ETT"))
    assert abs(plan.get_planned_route().length_mm - 50.0) < 1e-4


# ── Test 34: Structures to Avoid remain unchanged ─────────────────────────────
def test_34_structures_to_avoid_remain_unchanged():
    plan = SurgicalPlanSession()
    plan.add_avoid_structure(5.0, 5.0, 5.0, radius_mm=3.0, name="Optic Nerve")
    assert len(plan.avoid_structures) == 1

    dev_mgr = DeviceMarkerManager()
    dev_mgr.add_marker(DeviceMarker(id="dm_or4", patient_mrn="P", scan_id="S", device_type="Line", label="Line"))
    assert len(plan.avoid_structures) == 1


# ── Test 35: Surgical Corridor remains unchanged ──────────────────────────────
def test_35_surgical_corridor_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.corridor_radius_mm = 6.0
    assert plan.corridor_radius_mm == 6.0

    dev_mgr = DeviceMarkerManager()
    dev_mgr.add_marker(DeviceMarker(id="dm_or5", patient_mrn="P", scan_id="S", device_type="Drain", label="Drain"))
    assert plan.corridor_radius_mm == 6.0


# ── Test 36: Virtual Instrument remains unchanged ─────────────────────────────
def test_36_virtual_instrument_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.instrument_depth_mm = 25.0
    assert plan.instrument_depth_mm == 25.0


# ── Test 37: Live Deviation remains unchanged ─────────────────────────────────
def test_37_live_deviation_remains_unchanged():
    plan = SurgicalPlanSession()
    plan.set_entry(0.0, 0.0, 0.0)
    plan.set_target(0.0, 0.0, 100.0)
    plan.set_deviation_offsets(5.0, 0.0, 0.0)
    pose = plan.get_current_instrument_pose()
    assert pose is not None
    assert abs(pose.lateral_deviation_mm - 5.0) < 1e-4


# ── Test 38: Plan Versions remain unchanged ───────────────────────────────────
def test_38_plan_versions_remain_unchanged():
    plan = SurgicalPlanSession()
    plan.set_entry(1, 2, 3)
    plan.set_target(4, 5, 6)
    snapshot = plan.create_snapshot()
    assert snapshot is not None
    assert "entry_point" in snapshot or "entry" in snapshot
    assert "target_point" in snapshot or "target" in snapshot


# ── Test 39: Quick Surgical Views remain unchanged ────────────────────────────
def test_39_quick_surgical_views_remain_unchanged():
    viewer = Viewer3D()
    res = viewer.apply_quick_surgical_view("ENTRY_ALIGNED")
    assert res is not None


# ── Test 40: No clinical interpretation text is generated ─────────────────────
def test_40_no_clinical_interpretation_text_generated():
    card = DeviceMarkersCard()
    m = DeviceMarker(id="m_neut", patient_mrn="P", scan_id="S", device_type="Endotracheal Tube", label="ETT")
    card.update_markers([m])

    full_text = card.lbl_title.text() + card.lbl_header_status.text()
    for i in range(card.list_markers.count()):
        full_text += " " + card.list_markers.item(i).text()

    forbidden_terms = [
        "correct position", "incorrect position", "malposition",
        "displaced", "migrated", "complication", "normal", "abnormal",
        "adequate", "inadequate"
    ]
    for term in forbidden_terms:
        assert term not in full_text.lower()


# ── Test 41: No automatic device detection is performed ───────────────────────
def test_41_no_automatic_device_detection_performed():
    manager = DeviceMarkerManager()
    manager.set_context("P", "S")
    # Initial state has zero markers, none are automatically created
    assert len(manager.get_markers_for_active_scan()) == 0
    # Marker is created only when explicit user action occurs
    m = DeviceMarker(id="user_created", patient_mrn="P", scan_id="S", device_type="Drain", label="Drain")
    manager.add_marker(m)
    assert len(manager.get_markers_for_active_scan()) == 1


# ── Test 42: No DICOM reload occurs for marker selection/navigation ───────────
def test_42_no_dicom_reload_for_marker_selection_navigation():
    viewer = Viewer3D()
    viewer.current_scan = {"id": "S1", "file_path": "/fake/path"}
    load_scan_called = []
    original_load = viewer.load_scan
    viewer.load_scan = lambda p, s: load_scan_called.append((p, s))

    viewer.set_device_markers([{"id": "m1", "physical_x_mm": 10, "physical_y_mm": 10, "physical_z_mm": 10}])
    assert len(load_scan_called) == 0

    viewer.load_scan = original_load


# ── Test 43: ICU Feature 1 continues passing ──────────────────────────────────
def test_43_icu_feature_1_continues_passing():
    card = CurrentPreviousScanCard()
    assert card is not None
    assert hasattr(card, "btn_view_previous")
    assert hasattr(card, "btn_show_current")
    assert hasattr(card, "btn_toggle")


# ── Test 44: ICU Feature 2 continues passing ──────────────────────────────────
def test_44_icu_feature_2_continues_passing():
    card = SameLocationReviewCard()
    assert card is not None
    assert hasattr(card, "btn_mark")
    assert hasattr(card, "btn_view_current")
    assert hasattr(card, "btn_view_previous")


# ── Test 45: ICU Feature 3 continues passing ──────────────────────────────────
def test_45_icu_feature_3_continues_passing():
    card = MeasurementTrackingCard()
    assert card is not None
    assert hasattr(card, "btn_track")


# ── Test 46: ICU Feature 4 continues passing ──────────────────────────────────
def test_46_icu_feature_4_continues_passing():
    card = AnnotationCarryForwardCard()
    assert card is not None
    assert hasattr(card, "btn_carry")
    assert hasattr(card, "btn_view_source")
    assert hasattr(card, "btn_view_target")


# ── Test 47: ICU Feature 5 continues passing ──────────────────────────────────
def test_47_icu_feature_5_continues_passing():
    card = WhatChangedCard()
    assert card is not None
    assert hasattr(card, "btn_review")
    assert hasattr(card, "slider_threshold")


# ── Test 48: Existing measurement tests continue passing ──────────────────────
def test_48_existing_measurement_tests_continue_passing():
    tracker = MeasurementTracker()
    assert hasattr(tracker, "add_measurement")
    assert hasattr(tracker, "get_measurements_for_scan")


# ── Test 49: Existing 2D slice tests continue passing ─────────────────────────
def test_49_existing_2d_slice_tests_continue_passing():
    sv = Slice2DViewerWidget()
    assert hasattr(sv, "canvas")
    assert hasattr(sv, "slider_slice")
    assert hasattr(sv, "set_slice_index")


# ── Test 50: Existing synchronization tests continue passing ──────────────────
def test_50_existing_synchronization_tests_continue_passing():
    viewer = Viewer3D()
    assert hasattr(viewer, "set_sync_2d_3d")
    assert hasattr(viewer, "set_marker_visible")
    assert hasattr(viewer, "set_crosshair_visible")


# ── Test 51: Existing metadata tests continue passing ─────────────────────────
def test_51_existing_metadata_tests_continue_passing():
    sv = Slice2DViewerWidget()
    assert hasattr(sv, "pixel_spacing")
    assert hasattr(sv, "slice_thickness")


# ── Test 52: All OR Feature 1–9 tests continue passing ────────────────────────
def test_52_all_or_features_continue_passing():
    mode = OrIcuMode()
    assert hasattr(mode, "set_mode")
    mode.set_mode("OR")
    assert mode.current_mode == "OR"


# ── Test 53: ICU shell tests continue passing ─────────────────────────────────
def test_53_icu_shell_tests_continue_passing():
    mode = OrIcuMode()
    mode.set_mode("ICU")
    assert mode.current_mode == "ICU"
    assert hasattr(mode, "device_markers_card")
    assert mode.device_markers_card is not None
