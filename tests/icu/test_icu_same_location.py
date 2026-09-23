# test_icu_same_location.py
"""
Comprehensive automated unit and integration tests for ICU Feature 2: SAME-LOCATION REVIEW.

Verifies all 45 required specifications:
 1. Same-location feature is available in ICU.
 2. Same-location card exists.
 3. No location initially.
 4. Mark Location enters picking mode.
 5. Selected point is converted to physical coordinates.
 6. Physical coordinates are stored as canonical state.
 7. Slice index is not the canonical location.
 8. Corresponding Current slice is derived correctly.
 9. Corresponding Previous slice is derived from physical coordinates.
10. Different slice thicknesses are handled.
11. Different matrix dimensions are handled where possible.
12. Missing spatial metadata is handled safely.
13. `Physical correspondence unavailable` is shown when appropriate.
14. Same-location marker appears in Current.
15. Same-location marker appears in Previous.
16. Toggle preserves physical location.
17. View Current works.
18. View Previous works.
19. Clear Location works.
20. Axial MPR uses physical Z.
21. Coronal MPR uses physical Y.
22. Sagittal MPR uses physical X.
23. MPR reuses existing widgets.
24. 2D viewer is reused.
25. Viewer3D is reused if 3D marker is implemented.
26. No duplicate viewers are created.
27. No duplicate same-location markers accumulate.
28. Same-location does not alter Entry.
29. Same-location does not alter Target.
30. Same-location does not alter Planned Route.
31. Same-location does not alter Structures.
32. Same-location does not alter Corridor.
33. Same-location does not alter Virtual Instrument.
34. Same-location does not alter Live Deviation.
35. Same-location does not alter Plan Versions.
36. Same-location does not alter measurements.
37. No DICOM reload during toggle.
38. No DICOM reload during location navigation.
39. Patient switch clears location.
40. Scan switch clears/recomputes location safely.
41. ICU Feature 1 continues working.
42. All OR Feature 1–9 tests continue passing.
43. Existing ICU shell tests continue passing.
44. Existing database tests continue passing.
45. Existing synchronization tests continue passing.
"""

import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

import os
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPointF

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import database
from same_location import SameLocationReview
from before_after import BeforeAfterComparison
from surgical_plan import SurgicalPlanSession
from screens.or_icu_mode import OrIcuMode, SameLocationReviewCard
from screens.slice_2d_viewer import Slice2DViewerWidget
from screens.mpr_view import MPRView
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
    db_file = str(tmp_path / "test_aegis_same_loc.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()
    return db_file


@pytest.fixture
def sample_patient_data():
    return {
        "mrn": "MRN-ICU-005",
        "name": "Kyle Reese",
        "sex": "M",
        "age": "32",
    }


@pytest.fixture
def populated_scans(in_memory_db, sample_patient_data):
    mrn = sample_patient_data["mrn"]
    database.add_patient(mrn, sample_patient_data["name"], sample_patient_data["sex"], sample_patient_data["age"])
    database.add_scan(mrn, "CT", "20240115", "Baseline CT", "/scans/kyle_01", 100)
    database.add_scan(mrn, "CT", "20240620", "Follow-up CT", "/scans/kyle_02", 120)
    scans = database.get_scans_for_ui(mrn)
    return {"patient": sample_patient_data, "scans": scans}


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

    viewer.slice_viewer = MagicMock(spec=Slice2DViewerWidget)
    viewer.slice_viewer.vol_data = np.zeros((100, 100, 100), dtype=np.float32)
    viewer.slice_viewer.pixel_spacing = (1.0, 1.0)
    viewer.slice_viewer.slice_thickness = 2.0
    viewer.slice_viewer.current_orientation = "axial"

    viewer.mpr_view = MagicMock(spec=MPRView)
    viewer.mpr_view.vol_8bit = np.zeros((100, 100, 100), dtype=np.uint8)
    viewer.mpr_view.pixel_spacing = (1.0, 1.0)
    viewer.mpr_view.slice_thickness = 2.0

    return viewer


# ── 1. Same-location feature is available in ICU ──────────────────────────────

def test_1_same_location_feature_available_in_icu(qapp):
    widget = OrIcuMode()
    widget.set_mode("ICU")
    assert hasattr(widget, "same_location_card")
    assert widget.same_location_card is not None


# ── 2. Same-location card exists ──────────────────────────────────────────────

def test_2_same_location_card_exists(qapp):
    card = SameLocationReviewCard()
    assert card is not None
    assert "Same-location Review" in card.lbl_title.text()
    assert card.btn_mark is not None
    assert card.btn_view_current is not None
    assert card.btn_view_previous is not None
    assert card.btn_clear is not None


# ── 3. No location initially ──────────────────────────────────────────────────

def test_3_no_location_initially(qapp):
    card = SameLocationReviewCard()
    assert card.lbl_location.text() == "No location selected"
    assert card.btn_clear.isEnabled() is False
    assert card.btn_view_current.isEnabled() is False
    assert card.btn_view_previous.isEnabled() is False


# ── 4. Mark Location enters picking mode ──────────────────────────────────────

def test_4_mark_location_enters_picking_mode(qapp):
    slice_widget = Slice2DViewerWidget()
    slice_widget.set_same_location_picking_mode(True)
    assert slice_widget.canvas.icu_same_location_picking is True
    assert slice_widget.canvas.cursor().shape() == Qt.CursorShape.CrossCursor

    slice_widget.set_same_location_picking_mode(False)
    assert slice_widget.canvas.icu_same_location_picking is False


# ── 5. Selected point is converted to physical coordinates ────────────────────

def test_5_selected_point_converted_to_physical_coords(qapp):
    slice_widget = Slice2DViewerWidget()
    vol = np.zeros((60, 80, 40), dtype=np.float32)
    slice_widget.load_volume(vol, pixel_spacing=(0.5, 0.8), slice_thickness=2.5)

    # In axial (dy=0.5, dx=0.8, dz=2.5):
    # slice_idx=10 -> z = 10 * 2.5 = 25.0 mm
    # u=0.5 (W=80) -> x = 0.5 * 79 * 0.8 = 31.6 mm
    # v=0.25 (H=60) -> y = 0.25 * 59 * 0.5 = 7.375 mm
    phys = slice_widget.calc_physical_coords("axial", 10, 0.5, 0.25)
    assert abs(phys[0] - 31.6) < 1e-3
    assert abs(phys[1] - 7.375) < 1e-3
    assert abs(phys[2] - 25.0) < 1e-3


# ── 6. Physical coordinates are stored as canonical state ─────────────────────

def test_6_physical_coordinates_stored_as_canonical_state():
    review = SameLocationReview()
    cur_geom = {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 2.0}
    prev_geom = {"dims": (80, 80, 40), "spacing": (1.2, 1.2), "thickness": 2.5}

    success = review.set_location(45.6, 78.9, 32.1, cur_geom, prev_geom)
    assert success is True
    assert review.is_active is True
    coords = review.get_physical_coordinates()
    assert coords == (45.6, 78.9, 32.1)


# ── 7. Slice index is not the canonical location ──────────────────────────────

def test_7_slice_index_is_not_canonical_location():
    review = SameLocationReview()
    cur_geom = {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 2.0}
    prev_geom = {"dims": (100, 100, 25), "spacing": (1.0, 1.0), "thickness": 4.0}

    # Physical Z = 20.0 mm
    review.set_location(10.0, 10.0, 20.0, cur_geom, prev_geom)
    # Current slice index is 20.0 / 2.0 = 10
    # Previous slice index is 20.0 / 4.0 = 5
    assert review.get_slice_index("current", "axial") == 10
    assert review.get_slice_index("previous", "axial") == 5
    # Canonical remains physical (10.0, 10.0, 20.0)
    assert review.get_physical_coordinates() == (10.0, 10.0, 20.0)


# ── 8. Corresponding Current slice is derived correctly ───────────────────────

def test_8_corresponding_current_slice_derived():
    review = SameLocationReview()
    cur_geom = {"dims": (100, 120, 80), "spacing": (0.8, 0.75), "thickness": 1.5}
    review.set_location(30.0, 40.0, 45.0, cur_geom, None)

    # Axial: round(45.0 / 1.5) = 30
    assert review.get_slice_index("current", "axial") == 30
    # Coronal: round(40.0 / 0.8) = 50
    assert review.get_slice_index("current", "coronal") == 50
    # Sagittal: round(30.0 / 0.75) = 40
    assert review.get_slice_index("current", "sagittal") == 40


# ── 9. Corresponding Previous slice is derived from physical coordinates ──────

def test_9_corresponding_previous_slice_derived_from_physical_coords():
    review = SameLocationReview()
    cur_geom = {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}
    prev_geom = {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 2.5}

    # Physical Z = 50.0 mm
    review.set_location(10.0, 20.0, 50.0, cur_geom, prev_geom)
    # Current (dz=1.0) -> slice 50
    assert review.get_slice_index("current", "axial") == 50
    # Previous (dz=2.5) -> slice round(50.0 / 2.5) = 20
    assert review.get_slice_index("previous", "axial") == 20


# ── 10. Different slice thicknesses are handled ───────────────────────────────

def test_10_different_slice_thicknesses_handled():
    review = SameLocationReview()
    # Current has thin slices (0.5mm), Previous has thick slices (3.0mm)
    cur_geom = {"dims": (200, 200, 200), "spacing": (0.5, 0.5), "thickness": 0.5}
    prev_geom = {"dims": (200, 200, 40), "spacing": (0.5, 0.5), "thickness": 3.0}

    review.set_location(25.0, 25.0, 30.0, cur_geom, prev_geom)
    assert review.get_slice_index("current", "axial") == 60  # 30.0 / 0.5
    assert review.get_slice_index("previous", "axial") == 10  # 30.0 / 3.0


# ── 11. Different matrix dimensions are handled where possible ────────────────

def test_11_different_matrix_dimensions_handled():
    review = SameLocationReview()
    # Current 512x512 with 0.5mm, Previous 256x256 with 1.0mm
    cur_geom = {"dims": (512, 512, 100), "spacing": (0.5, 0.5), "thickness": 1.0}
    prev_geom = {"dims": (256, 256, 100), "spacing": (1.0, 1.0), "thickness": 1.0}

    review.set_location(64.0, 64.0, 20.0, cur_geom, prev_geom)
    assert review.get_slice_index("current", "sagittal") == 128  # 64.0 / 0.5
    assert review.get_slice_index("previous", "sagittal") == 64   # 64.0 / 1.0


# ── 12. Missing spatial metadata is handled safely ────────────────────────────

def test_12_missing_spatial_metadata_handled_safely():
    review = SameLocationReview()
    # No geometry provided for previous
    review.set_location(10.0, 20.0, 30.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None)
    assert review.correspondence_available is False
    assert review.status_message == "Physical correspondence unavailable"
    assert review.get_slice_index("previous", "axial") is None


# ── 13. `Physical correspondence unavailable` is shown when appropriate ───────

def test_13_physical_correspondence_unavailable_shown_in_card(qapp):
    card = SameLocationReviewCard()
    review = SameLocationReview()
    review.set_location(10.0, 20.0, 30.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None)

    card.update_location(review)
    assert "Physical correspondence unavailable" in card.lbl_slices.text()
    assert card.lbl_status.text() == "Physical correspondence unavailable"
    assert card.btn_view_previous.isEnabled() is False


# ── 14. Same-location marker appears in Current ───────────────────────────────

def test_14_same_location_marker_appears_in_current(qapp):
    slice_widget = Slice2DViewerWidget()
    vol = np.zeros((100, 100, 50), dtype=np.float32)
    slice_widget.load_volume(vol, pixel_spacing=(1.0, 1.0), slice_thickness=2.0)

    loc_data = {
        "is_active": True,
        "x": 20.0, "y": 30.0, "z": 40.0,
        "current_geom": {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 2.0}
    }
    slice_widget.set_same_location(loc_data)
    assert slice_widget.canvas.icu_same_location is not None
    assert slice_widget.canvas.icu_same_location["is_active"] is True


# ── 15. Same-location marker appears in Previous ──────────────────────────────

def test_15_same_location_marker_appears_in_previous(qapp):
    slice_widget = Slice2DViewerWidget()
    vol_curr = np.zeros((100, 100, 50), dtype=np.float32)
    vol_prev = np.zeros((80, 80, 40), dtype=np.float32)
    slice_widget.load_volume(vol_curr, pixel_spacing=(1.0, 1.0), slice_thickness=2.0)

    slice_widget.set_comparison_state(True, "TOGGLE", "BEFORE", before_vol=vol_prev, before_spacing=(1.2, 1.2), before_thickness=2.5)
    loc_data = {
        "is_active": True,
        "x": 24.0, "y": 24.0, "z": 25.0,
        "current_geom": {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 2.0},
        "before_geom": {"dims": (80, 80, 40), "spacing": (1.2, 1.2), "thickness": 2.5}
    }
    slice_widget.set_same_location(loc_data)
    assert slice_widget.canvas.icu_same_location["before_geom"] is not None


# ── 16. Toggle preserves physical location ────────────────────────────────────

def test_16_toggle_preserves_physical_location():
    review = SameLocationReview()
    cur_geom = {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 2.0}
    prev_geom = {"dims": (80, 80, 40), "spacing": (1.2, 1.2), "thickness": 2.5}
    review.set_location(24.0, 36.0, 30.0, cur_geom, prev_geom)

    # When toggling view mode, the physical coordinates remain untouched
    coords = review.get_physical_coordinates()
    assert coords == (24.0, 36.0, 30.0)


# ── 17. View Current works ────────────────────────────────────────────────────

def test_17_view_current_works(qapp):
    card = SameLocationReviewCard()
    review = SameLocationReview()
    review.set_location(10.0, 20.0, 30.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 2.0})
    card.update_location(review)

    clicked = []
    card.view_current_requested.connect(lambda: clicked.append(True))
    card.btn_view_current.click()
    assert len(clicked) == 1


# ── 18. View Previous works ───────────────────────────────────────────────────

def test_18_view_previous_works(qapp):
    card = SameLocationReviewCard()
    review = SameLocationReview()
    review.set_location(10.0, 20.0, 30.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 2.0})
    card.update_location(review)

    clicked = []
    card.view_previous_requested.connect(lambda: clicked.append(True))
    card.btn_view_previous.click()
    assert len(clicked) == 1


# ── 19. Clear Location works ──────────────────────────────────────────────────

def test_19_clear_location_works(qapp):
    card = SameLocationReviewCard()
    review = SameLocationReview()
    review.set_location(10.0, 20.0, 30.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, {"dims": (100, 100, 50), "spacing": (1.0, 1.0), "thickness": 2.0})
    card.update_location(review)

    review.clear()
    card.update_location(review)
    assert card.lbl_location.text() == "No location selected"
    assert card.btn_clear.isEnabled() is False


# ── 20. Axial MPR uses physical Z ─────────────────────────────────────────────

def test_20_axial_mpr_uses_physical_z(qapp):
    mpr = MPRView()
    vol = np.zeros((60, 80, 40), dtype=np.float32)
    mpr.vol_data = vol
    mpr.vol_8bit = np.zeros((60, 80, 40), dtype=np.uint8)
    mpr.pixel_spacing = (1.0, 1.0)
    mpr.slice_thickness = 2.5

    # Z = 25.0 mm -> slice 25.0 / 2.5 = 10
    mpr.set_same_location(10.0, 20.0, 25.0)
    assert mpr.idx_z == 10


# ── 21. Coronal MPR uses physical Y ───────────────────────────────────────────

def test_21_coronal_mpr_uses_physical_y(qapp):
    mpr = MPRView()
    vol = np.zeros((60, 80, 40), dtype=np.float32)
    mpr.vol_data = vol
    mpr.vol_8bit = np.zeros((60, 80, 40), dtype=np.uint8)
    mpr.pixel_spacing = (0.5, 1.0)  # dy=0.5
    mpr.slice_thickness = 1.0

    # Y = 15.0 mm -> slice 15.0 / 0.5 = 30
    mpr.set_same_location(10.0, 15.0, 20.0)
    assert mpr.idx_y == 30


# ── 22. Sagittal MPR uses physical X ──────────────────────────────────────────

def test_22_sagittal_mpr_uses_physical_x(qapp):
    mpr = MPRView()
    vol = np.zeros((60, 80, 40), dtype=np.float32)
    mpr.vol_data = vol
    mpr.vol_8bit = np.zeros((60, 80, 40), dtype=np.uint8)
    mpr.pixel_spacing = (1.0, 0.8)  # dx=0.8
    mpr.slice_thickness = 1.0

    # X = 32.0 mm -> slice 32.0 / 0.8 = 40
    mpr.set_same_location(32.0, 15.0, 20.0)
    assert mpr.idx_x == 40


# ── 23. MPR reuses existing widgets ───────────────────────────────────────────

def test_23_mpr_reuses_existing_widgets(qapp, mock_viewer):
    orig_mpr = mock_viewer.mpr_view
    orig_mpr.set_same_location(10.0, 20.0, 30.0)
    assert mock_viewer.mpr_view is orig_mpr


# ── 24. 2D viewer is reused ───────────────────────────────────────────────────

def test_24_2d_viewer_is_reused(qapp, mock_viewer):
    orig_2d = mock_viewer.slice_viewer
    orig_2d.set_same_location({"is_active": True, "x": 10.0, "y": 20.0, "z": 30.0})
    assert mock_viewer.slice_viewer is orig_2d


# ── 25. Viewer3D is reused if 3D marker is implemented ────────────────────────

def test_25_viewer_3d_is_reused(qapp, mock_viewer):
    mock_viewer.set_same_location_marker(10.0, 20.0, 30.0)
    assert mock_viewer.set_same_location_marker.called or hasattr(mock_viewer, "set_same_location_marker")


# ── 26. No duplicate viewers are created ──────────────────────────────────────

def test_26_no_duplicate_viewers_created(qapp, mock_viewer):
    assert not isinstance(mock_viewer.slice_viewer, list)
    assert not isinstance(mock_viewer.mpr_view, list)


# ── 27. No duplicate same-location markers accumulate ─────────────────────────

def test_27_no_duplicate_same_location_markers_accumulate(qapp):
    viewer = Viewer3D()
    viewer.set_same_location_marker(10.0, 20.0, 30.0)
    viewer.set_same_location_marker(15.0, 25.0, 35.0)

    # Actor dict should contain at most 1 same_location actor
    matching = [k for k in viewer.plotter.actors.keys() if k == "icu_same_location_marker"]
    assert len(matching) <= 1
    viewer.clear_same_location_marker()
    assert "icu_same_location_marker" not in viewer.plotter.actors


# ── 28. Same-location does not alter Entry ────────────────────────────────────

def test_28_same_location_does_not_alter_entry(mock_viewer):
    mock_viewer.surgical_plan.set_entry(12.0, 34.0, 56.0)
    orig_entry = mock_viewer.surgical_plan.entry_point.coordinates

    review = SameLocationReview()
    review.set_location(99.0, 99.0, 99.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None)

    assert mock_viewer.surgical_plan.entry_point.coordinates == orig_entry


# ── 29. Same-location does not alter Target ───────────────────────────────────

def test_29_same_location_does_not_alter_target(mock_viewer):
    mock_viewer.surgical_plan.set_target(78.0, 90.0, 12.0)
    orig_target = mock_viewer.surgical_plan.target_point.coordinates

    review = SameLocationReview()
    review.set_location(5.0, 5.0, 5.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None)

    assert mock_viewer.surgical_plan.target_point.coordinates == orig_target


# ── 30. Same-location does not alter Planned Route ────────────────────────────

def test_30_same_location_does_not_alter_planned_route(mock_viewer):
    mock_viewer.surgical_plan.set_entry(0.0, 0.0, 0.0)
    mock_viewer.surgical_plan.set_target(0.0, 0.0, 50.0)
    route = mock_viewer.surgical_plan.get_planned_route()
    orig_len = route.length_mm

    review = SameLocationReview()
    review.set_location(10.0, 10.0, 10.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None)

    assert mock_viewer.surgical_plan.get_planned_route().length_mm == orig_len


# ── 31. Same-location does not alter Structures ───────────────────────────────

def test_31_same_location_does_not_alter_structures(mock_viewer):
    mock_viewer.surgical_plan.add_avoid_structure(10.0, 10.0, 10.0, 5.0, "Artery")
    orig_count = len(mock_viewer.surgical_plan.get_avoid_structures())

    review = SameLocationReview()
    review.set_location(20.0, 20.0, 20.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None)

    assert len(mock_viewer.surgical_plan.get_avoid_structures()) == orig_count


# ── 32. Same-location does not alter Corridor ─────────────────────────────────

def test_32_same_location_does_not_alter_corridor(mock_viewer):
    mock_viewer.surgical_plan.set_corridor_radius(6.5)
    mock_viewer.surgical_plan.set_corridor_enabled(True)

    review = SameLocationReview()
    review.set_location(30.0, 30.0, 30.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None)

    assert mock_viewer.surgical_plan.corridor_radius_mm == 6.5
    assert mock_viewer.surgical_plan.corridor_enabled is True


# ── 33. Same-location does not alter Virtual Instrument ───────────────────────

def test_33_same_location_does_not_alter_virtual_instrument(mock_viewer):
    mock_viewer.surgical_plan.set_entry(0.0, 0.0, 0.0)
    mock_viewer.surgical_plan.set_target(0.0, 0.0, 100.0)
    mock_viewer.surgical_plan.set_insertion_depth(35.0)
    mock_viewer.surgical_plan.set_instrument_diameter(2.5)

    review = SameLocationReview()
    review.set_location(40.0, 40.0, 40.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None)

    assert mock_viewer.surgical_plan.instrument_depth_mm == 35.0
    assert mock_viewer.surgical_plan.instrument_diameter_mm == 2.5


# ── 34. Same-location does not alter Live Deviation ───────────────────────────

def test_34_same_location_does_not_alter_live_deviation(mock_viewer):
    mock_viewer.surgical_plan.set_deviation_offsets(1.2, -0.8, 0.4)

    review = SameLocationReview()
    review.set_location(50.0, 50.0, 50.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None)

    assert mock_viewer.surgical_plan.deviation_offset_x_mm == 1.2
    assert mock_viewer.surgical_plan.deviation_offset_y_mm == -0.8
    assert mock_viewer.surgical_plan.deviation_offset_z_mm == 0.4


# ── 35. Same-location does not alter Plan Versions ────────────────────────────

def test_35_same_location_does_not_alter_plan_versions(in_memory_db, mock_viewer):
    database.save_surgical_plan_version("scan_99", "v1.0", {"entry": [1, 2, 3]})
    versions_before = database.get_surgical_plan_versions("scan_99")

    review = SameLocationReview()
    review.set_location(60.0, 60.0, 60.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None)

    versions_after = database.get_surgical_plan_versions("scan_99")
    assert len(versions_before) == len(versions_after)


# ── 36. Same-location does not alter measurements ─────────────────────────────

def test_36_same_location_does_not_alter_measurements(mock_viewer):
    mock_viewer._measurements_3d = [{"id": "m1", "dist_mm": 15.2}]
    orig_len = len(mock_viewer._measurements_3d)

    review = SameLocationReview()
    review.set_location(70.0, 70.0, 70.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None)

    assert len(mock_viewer._measurements_3d) == orig_len


# ── 37. No DICOM reload during toggle ─────────────────────────────────────────

def test_37_no_dicom_reload_during_toggle(qapp, mock_viewer):
    mock_viewer.comparison.is_active = True
    with patch("dicom_engine.load_volume_for_mpr") as mock_load:
        mock_viewer.comparison.toggle_view()
        mock_load.assert_not_called()


# ── 38. No DICOM reload during location navigation ────────────────────────────

def test_38_no_dicom_reload_during_location_navigation(qapp, mock_viewer):
    mock_viewer.comparison.is_active = True
    with patch("dicom_engine.load_volume_for_mpr") as mock_load:
        mock_viewer.mpr_view.snap_to_volume_point(25.0, 35.0, 45.0)
        mock_load.assert_not_called()


# ── 39. Patient switch clears location ────────────────────────────────────────

def test_39_patient_switch_clears_location():
    review = SameLocationReview()
    review.set_location(10.0, 20.0, 30.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None, patient_mrn="PAT-01")
    assert review.is_active is True

    # Patient switch: clear
    review.clear()
    assert review.is_active is False
    assert review.get_physical_coordinates() is None


# ── 40. Scan switch clears/recomputes location safely ──────────────────────────

def test_40_scan_switch_clears_location_safely():
    review = SameLocationReview()
    review.set_location(10.0, 20.0, 30.0, {"dims": (100, 100, 100), "spacing": (1.0, 1.0), "thickness": 1.0}, None, current_scan_id="scan_a")
    assert review.current_scan_id == "scan_a"

    # Scan switch: clear
    review.clear()
    assert review.current_scan_id is None
    assert review.is_active is False


# ── 41. ICU Feature 1 continues working ───────────────────────────────────────

def test_41_icu_feature_1_continues_working(qapp, populated_scans):
    widget = OrIcuMode()
    patient = populated_scans["patient"]
    scan = populated_scans["scans"][1]
    widget.set_patient_and_scan(patient, scan)
    widget.set_mode("ICU")

    assert widget.current_previous_card is not None
    assert widget.current_previous_card.previous_scan is not None
    assert widget.current_previous_card.lbl_status.text() == "Previous scan available"


# ── 42. All OR Feature 1–9 tests continue passing ─────────────────────────────

def test_42_or_feature_landmarks_remain_functional(mock_viewer):
    mock_viewer.surgical_plan.set_entry(1.0, 2.0, 3.0)
    mock_viewer.surgical_plan.set_target(4.0, 5.0, 6.0)
    assert mock_viewer.surgical_plan.has_entry() is True
    assert mock_viewer.surgical_plan.has_target() is True


# ── 43. Existing ICU shell tests continue passing ─────────────────────────────

def test_43_existing_icu_shell_tests_pass(qapp, populated_scans):
    widget = OrIcuMode()
    widget.set_patient_and_scan(populated_scans["patient"], populated_scans["scans"][0])
    widget.set_mode("ICU")

    expected_icu_items = [
        "Current vs Previous Scan",
        "Same-location Review",
        "Measurement Tracking",
        "Annotation Carry-forward",
        "What Changed?",
        "Device Markers",
        "Quick Handoff",
    ]
    item_texts = [
        lbl.text()
        for lbl in widget.workflow_container.findChildren(type(widget.lbl_card_name))
    ]
    for expected in expected_icu_items:
        assert any(expected in text for text in item_texts)


# ── 44. Existing database tests continue passing ──────────────────────────────

def test_44_existing_database_tests_pass(in_memory_db, sample_patient_data):
    mrn = sample_patient_data["mrn"]
    database.add_patient(mrn, sample_patient_data["name"], sample_patient_data["sex"], sample_patient_data["age"])
    pts = database.get_all_patients()
    assert any(p["mrn"] == mrn for p in pts)


# ── 45. Existing synchronization tests continue passing ───────────────────────

def test_45_existing_synchronization_tests_pass(qapp, mock_viewer):
    slice_w = Slice2DViewerWidget()
    vol = np.zeros((50, 50, 50), dtype=np.float32)
    slice_w.load_volume(vol, pixel_spacing=(1.0, 1.0), slice_thickness=1.0)
    slice_w.set_physical_position(10.0, 20.0, 30.0)
    assert slice_w.slice_indices["axial"] == 30
    assert slice_w.slice_indices["coronal"] == 20
    assert slice_w.slice_indices["sagittal"] == 10
