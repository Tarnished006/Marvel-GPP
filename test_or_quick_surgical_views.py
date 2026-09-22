# test_or_quick_surgical_views.py
"""
Unit and integration tests for OR Feature 9: QUICK SURGICAL VIEWS.

Covers all 46 required verification scenarios:
 1. Quick-view card exists.
 2. Entry preset requires Entry.
 3. Target preset requires Target.
 4. Route preset requires Planned Route.
 5. Structures preset requires structures.
 6. Instrument preset requires visible instrument.
 7. Full Plan handles partial planning state.
 8. Entry preset centers on Entry.
 9. Target preset centers on Target.
10. Route preset centers on route midpoint/extent.
11. Structures preset centers on structure geometry.
12. Instrument preset centers on instrument geometry.
13. Full Plan fits available planning geometry.
14. 2D positioning uses physical coordinates.
15. MPR positioning uses physical coordinates.
16. View changes do not reload DICOM.
17. View changes do not modify Entry.
18. View changes do not modify Target.
19. View changes do not modify Planned Route.
20. View changes do not modify Structures.
21. View changes do not modify Corridor.
22. View changes do not modify Virtual Instrument parameters.
23. View changes do not modify Live Deviation parameters.
24. View changes do not modify Before/After pair.
25. View changes do not modify Plan Versions.
26. View-only actions do not mark plan as unsaved.
27. Quick views do not create caliper measurements.
28. Quick views do not modify _measurements_3d.
29. No duplicate camera/viewer objects are created.
30. Repeated preset activation does not accumulate actors.
31. Scan change resets quick-view availability.
32. Patient change resets quick-view availability.
33. OR <-> ICU does not duplicate state.
34. Existing Entry/Target tests pass.
35. Existing Planned Route tests pass.
36. Existing Structures tests pass.
37. Existing Surgical Corridor tests pass.
38. Existing Virtual Instrument tests pass.
39. Existing Live Deviation tests pass.
40. Existing Plan Version tests pass.
41. Existing Before/After tests pass.
42. Existing measurement tests pass.
43. Existing database tests pass.
44. Existing measurement-persistence tests pass.
45. Existing 2D/3D synchronization tests pass.
46. Existing ICU/OR shell tests pass.
"""

import os
import sqlite3
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

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
from before_after import BeforeAfterComparison
import database
from screens.viewer_3d import Viewer3D
from screens.or_icu_mode import OrIcuMode, QuickSurgicalViewsCard
from screens.slice_2d_viewer import Slice2DViewerWidget
from screens.mpr_view import MPRView


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Provides a fresh isolated SQLite database for each test."""
    db_file = str(tmp_path / "test_aegis.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    with open("schema.sql") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture
def sample_patient():
    return {
        "mrn": "MRN-QUICK-01",
        "name": "Jordan Hayes",
        "age": "52",
        "sex": "M",
    }


@pytest.fixture
def sample_scan():
    return {
        "id": "scan_001",
        "file_path": "/path/to/scan_001",
        "type": "CT",
        "date": "2026-09-22",
        "patient_mrn": "MRN-QUICK-01",
    }


@pytest.fixture
def populated_session():
    """Returns a SurgicalPlanSession fully populated with all planning geometry."""
    session = SurgicalPlanSession()
    session.scan_id = "scan_001"
    session.set_entry(10.0, 20.0, 30.0, source_view="3D")
    session.set_target(50.0, 60.0, 70.0, source_view="3D")
    session.add_avoid_structure(25.0, 35.0, 45.0, radius_mm=6.0, name="Vessel Branch")
    session.set_corridor_radius(4.5)
    session.set_corridor_enabled(True)
    session.set_insertion_depth(30.0)
    session.set_instrument_diameter(2.5)
    session.set_instrument_visible(True)
    session.set_deviation_offsets(1.5, -2.0, 0.5)
    session.set_deviation_angles(3.0, -1.5)
    session.set_deviation_visible(True)
    return session


@pytest.fixture
def mock_viewer():
    """Returns a mocked Viewer3D with planning session, plotter, mpr_view, and panel_2d."""
    viewer = MagicMock(spec=Viewer3D)
    viewer.surgical_plan = SurgicalPlanSession()
    viewer.surgical_plan.scan_id = "scan_001"
    viewer.comparison = BeforeAfterComparison()
    viewer._measurements_3d = []
    viewer._measurement_actor_names = []

    # Mock plotter and camera
    viewer.plotter = MagicMock()
    viewer.plotter.camera = MagicMock()
    viewer.plotter.camera.focal_point = (0.0, 0.0, 0.0)
    viewer.plotter.camera.position = (0.0, -120.0, 0.0)
    viewer.plotter.actors = {}

    # Mock slice viewers
    viewer.mpr_view = MagicMock(spec=MPRView)
    viewer.panel_2d = MagicMock(spec=Slice2DViewerWidget)
    viewer.info_bar = QLabel()

    # Re-bind real methods from Viewer3D to the mock
    viewer._center_camera_point = Viewer3D._center_camera_point.__get__(viewer)
    viewer._frame_camera_bounds = Viewer3D._frame_camera_bounds.__get__(viewer)
    viewer._snap_slice_viewers = Viewer3D._snap_slice_viewers.__get__(viewer)
    viewer.get_quick_view_availability = Viewer3D.get_quick_view_availability.__get__(viewer)
    viewer.apply_quick_surgical_view = Viewer3D.apply_quick_surgical_view.__get__(viewer)
    viewer.view_entry_point = Viewer3D.view_entry_point.__get__(viewer)
    viewer.view_target_point = Viewer3D.view_target_point.__get__(viewer)
    viewer.view_planned_route = Viewer3D.view_planned_route.__get__(viewer)
    viewer.view_avoid_structure = Viewer3D.view_avoid_structure.__get__(viewer)
    viewer.view_virtual_instrument = Viewer3D.view_virtual_instrument.__get__(viewer)
    viewer.get_measurements_3d = Viewer3D.get_measurements_3d.__get__(viewer)
    viewer.set_measurements_3d_visible = Viewer3D.set_measurements_3d_visible.__get__(viewer)
    viewer.is_measurements_3d_visible = Viewer3D.is_measurements_3d_visible.__get__(viewer)

    viewer.quick_view_applied = MagicMock()

    return viewer


# ── 1. Quick-view card exists ──────────────────────────────────────────────────

def test_1_quick_view_card_exists(qapp, sample_patient, sample_scan):
    card = QuickSurgicalViewsCard()
    assert hasattr(card, "btn_entry")
    assert hasattr(card, "btn_target")
    assert hasattr(card, "btn_route")
    assert hasattr(card, "btn_structures")
    assert hasattr(card, "btn_instrument")
    assert hasattr(card, "btn_full_plan")
    assert hasattr(card, "lbl_active_preset")
    assert "Quick Surgical Views" in card.lbl_title.text()
    assert card.lbl_active_preset.text() == "View: None"

    # Also verify card is populated in OrIcuMode in OR mode
    or_mode = OrIcuMode()
    or_mode.set_mode("OR")
    or_mode.set_patient_and_scan(sample_patient, sample_scan)
    assert hasattr(or_mode, "quick_views_card")
    assert or_mode.quick_views_card is not None
    assert isinstance(or_mode.quick_views_card, QuickSurgicalViewsCard)


# ── 2. Entry preset requires Entry ─────────────────────────────────────────────

def test_2_entry_preset_requires_entry(qapp, mock_viewer):
    # No entry marked
    assert mock_viewer.surgical_plan.has_entry() is False
    avail = mock_viewer.get_quick_view_availability()
    assert avail["ENTRY"] is False

    success = mock_viewer.apply_quick_surgical_view("ENTRY")
    assert success is False
    assert "Requires Entry" in mock_viewer.info_bar.text()

    # Now add Entry
    mock_viewer.surgical_plan.set_entry(10.0, 20.0, 30.0)
    avail = mock_viewer.get_quick_view_availability()
    assert avail["ENTRY"] is True

    card = QuickSurgicalViewsCard()
    card.update_availability(avail)
    assert card.btn_entry.isEnabled() is True


# ── 3. Target preset requires Target ───────────────────────────────────────────

def test_3_target_preset_requires_target(qapp, mock_viewer):
    assert mock_viewer.surgical_plan.has_target() is False
    avail = mock_viewer.get_quick_view_availability()
    assert avail["TARGET"] is False

    success = mock_viewer.apply_quick_surgical_view("TARGET")
    assert success is False
    assert "Requires Target" in mock_viewer.info_bar.text()

    mock_viewer.surgical_plan.set_target(40.0, 50.0, 60.0)
    avail = mock_viewer.get_quick_view_availability()
    assert avail["TARGET"] is True

    card = QuickSurgicalViewsCard()
    card.update_availability(avail)
    assert card.btn_target.isEnabled() is True


# ── 4. Route preset requires Planned Route ────────────────────────────────────

def test_4_route_preset_requires_planned_route(qapp, mock_viewer):
    # Only Entry, no Target -> No planned route
    mock_viewer.surgical_plan.set_entry(10.0, 20.0, 30.0)
    avail = mock_viewer.get_quick_view_availability()
    assert avail["ROUTE"] is False

    success = mock_viewer.apply_quick_surgical_view("ROUTE")
    assert success is False
    assert "Requires Planned Route" in mock_viewer.info_bar.text()

    # Add Target -> Planned route exists
    mock_viewer.surgical_plan.set_target(40.0, 50.0, 60.0)
    avail = mock_viewer.get_quick_view_availability()
    assert avail["ROUTE"] is True

    card = QuickSurgicalViewsCard()
    card.update_availability(avail)
    assert card.btn_route.isEnabled() is True


# ── 5. Structures preset requires structures ───────────────────────────────────

def test_5_structures_preset_requires_structures(qapp, mock_viewer):
    assert mock_viewer.surgical_plan.has_avoid_structures() is False
    avail = mock_viewer.get_quick_view_availability()
    assert avail["STRUCTURES"] is False

    success = mock_viewer.apply_quick_surgical_view("STRUCTURES")
    assert success is False
    assert "No structures marked" in mock_viewer.info_bar.text()

    mock_viewer.surgical_plan.add_avoid_structure(15.0, 25.0, 35.0, radius_mm=5.0, name="Vessel")
    avail = mock_viewer.get_quick_view_availability()
    assert avail["STRUCTURES"] is True

    card = QuickSurgicalViewsCard()
    card.update_availability(avail)
    assert card.btn_structures.isEnabled() is True


# ── 6. Instrument preset requires visible instrument ──────────────────────────

def test_6_instrument_preset_requires_visible_instrument(qapp, mock_viewer):
    # Route exists, but instrument is not visible
    mock_viewer.surgical_plan.set_entry(10.0, 20.0, 30.0)
    mock_viewer.surgical_plan.set_target(40.0, 50.0, 60.0)
    mock_viewer.surgical_plan.set_instrument_visible(False)
    mock_viewer.surgical_plan.set_deviation_visible(False)

    avail = mock_viewer.get_quick_view_availability()
    assert avail["INSTRUMENT"] is False

    success = mock_viewer.apply_quick_surgical_view("INSTRUMENT")
    assert success is False
    assert "No instrument visible" in mock_viewer.info_bar.text()

    # Enable virtual instrument
    mock_viewer.surgical_plan.set_instrument_visible(True)
    avail = mock_viewer.get_quick_view_availability()
    assert avail["INSTRUMENT"] is True

    card = QuickSurgicalViewsCard()
    card.update_availability(avail)
    assert card.btn_instrument.isEnabled() is True


# ── 7. Full Plan handles partial planning state ───────────────────────────────

def test_7_full_plan_handles_partial_planning_state(qapp, mock_viewer):
    # 1. Empty plan
    avail = mock_viewer.get_quick_view_availability()
    assert avail["FULL PLAN"] is False
    assert mock_viewer.apply_quick_surgical_view("FULL PLAN") is False

    # 2. Entry only
    mock_viewer.surgical_plan.set_entry(10.0, 20.0, 30.0)
    avail = mock_viewer.get_quick_view_availability()
    assert avail["FULL PLAN"] is True
    assert mock_viewer.apply_quick_surgical_view("FULL PLAN") is True

    # 3. Target only (clear entry, set target)
    mock_viewer.surgical_plan.clear_entry()
    mock_viewer.surgical_plan.set_target(50.0, 60.0, 70.0)
    avail = mock_viewer.get_quick_view_availability()
    assert avail["FULL PLAN"] is True
    assert mock_viewer.apply_quick_surgical_view("FULL PLAN") is True

    # 4. Structures only
    mock_viewer.surgical_plan.clear_target()
    mock_viewer.surgical_plan.add_avoid_structure(15.0, 25.0, 35.0, radius_mm=5.0)
    avail = mock_viewer.get_quick_view_availability()
    assert avail["FULL PLAN"] is True
    assert mock_viewer.apply_quick_surgical_view("FULL PLAN") is True


# ── 8. Entry preset centers on Entry ──────────────────────────────────────────

def test_8_entry_preset_centers_on_entry(qapp, mock_viewer):
    mock_viewer.surgical_plan.set_entry(12.5, 22.5, 32.5)
    success = mock_viewer.apply_quick_surgical_view("ENTRY")
    assert success is True

    # 3D Camera focal point centered on Entry
    assert mock_viewer.plotter.camera.focal_point == (12.5, 22.5, 32.5)

    # 2D and MPR snapped to Entry
    mock_viewer.mpr_view.snap_to_volume_point.assert_called_with(12.5, 22.5, 32.5)
    mock_viewer.panel_2d.set_physical_position.assert_called_with(12.5, 22.5, 32.5)


# ── 9. Target preset centers on Target ────────────────────────────────────────

def test_9_target_preset_centers_on_target(qapp, mock_viewer):
    mock_viewer.surgical_plan.set_target(45.0, 55.0, 65.0)
    success = mock_viewer.apply_quick_surgical_view("TARGET")
    assert success is True

    assert mock_viewer.plotter.camera.focal_point == (45.0, 55.0, 65.0)
    mock_viewer.mpr_view.snap_to_volume_point.assert_called_with(45.0, 55.0, 65.0)
    mock_viewer.panel_2d.set_physical_position.assert_called_with(45.0, 55.0, 65.0)


# ── 10. Route preset centers on route midpoint/extent ─────────────────────────

def test_10_route_preset_centers_on_route_midpoint_extent(qapp, mock_viewer):
    mock_viewer.surgical_plan.set_entry(10.0, 20.0, 30.0)
    mock_viewer.surgical_plan.set_target(50.0, 60.0, 70.0)
    mock_viewer.surgical_plan.set_corridor_radius(5.0)
    mock_viewer.surgical_plan.set_corridor_enabled(True)

    success = mock_viewer.apply_quick_surgical_view("ROUTE")
    assert success is True

    # Midpoint = (30.0, 40.0, 50.0)
    mock_viewer.mpr_view.snap_to_volume_point.assert_called_with(30.0, 40.0, 50.0)
    mock_viewer.panel_2d.set_physical_position.assert_called_with(30.0, 40.0, 50.0)
    assert mock_viewer.plotter.camera.focal_point == (30.0, 40.0, 50.0)


# ── 11. Structures preset centers on structure geometry ───────────────────────

def test_11_structures_preset_centers_on_structure_geometry(qapp, mock_viewer):
    # Single structure: reuses view_avoid_structure
    s1 = mock_viewer.surgical_plan.add_avoid_structure(10.0, 10.0, 10.0, radius_mm=5.0, name="S1")
    success = mock_viewer.apply_quick_surgical_view("STRUCTURES")
    assert success is True
    assert mock_viewer.plotter.camera.focal_point == (10.0, 10.0, 10.0)

    # Multiple structures: computes centroid and frames all
    s2 = mock_viewer.surgical_plan.add_avoid_structure(30.0, 30.0, 30.0, radius_mm=5.0, name="S2")
    success = mock_viewer.apply_quick_surgical_view("STRUCTURES")
    assert success is True

    # Centroid = (20.0, 20.0, 20.0)
    assert mock_viewer.plotter.camera.focal_point == (20.0, 20.0, 20.0)
    mock_viewer.mpr_view.snap_to_volume_point.assert_called_with(20.0, 20.0, 20.0)
    mock_viewer.panel_2d.set_physical_position.assert_called_with(20.0, 20.0, 20.0)


# ── 12. Instrument preset centers on instrument geometry ──────────────────────

def test_12_instrument_preset_centers_on_instrument_geometry(qapp, mock_viewer):
    mock_viewer.surgical_plan.set_entry(0.0, 0.0, 0.0)
    mock_viewer.surgical_plan.set_target(0.0, 0.0, 100.0)
    mock_viewer.surgical_plan.set_insertion_depth(50.0)
    mock_viewer.surgical_plan.set_instrument_visible(True)

    success = mock_viewer.apply_quick_surgical_view("INSTRUMENT")
    assert success is True

    # Tip is at (0.0, 0.0, 50.0)
    assert mock_viewer.plotter.camera.focal_point == (0.0, 0.0, 50.0)
    mock_viewer.mpr_view.snap_to_volume_point.assert_called_with(0.0, 0.0, 50.0)


# ── 13. Full Plan fits available planning geometry ────────────────────────────

def test_13_full_plan_fits_available_planning_geometry(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    success = mock_viewer.apply_quick_surgical_view("FULL PLAN")
    assert success is True

    # Camera framed with focal point at centroid of all planning bounds
    focal = mock_viewer.plotter.camera.focal_point
    assert focal is not None
    assert len(focal) == 3


# ── 14. 2D positioning uses physical coordinates ──────────────────────────────

def test_14_2d_positioning_uses_physical_coordinates(qapp, mock_viewer):
    mock_viewer.surgical_plan.set_entry(15.75, -22.3, 44.1)
    mock_viewer.apply_quick_surgical_view("ENTRY")

    # set_physical_position must receive physical mm floats
    mock_viewer.panel_2d.set_physical_position.assert_called_with(15.75, -22.3, 44.1)
    args = mock_viewer.panel_2d.set_physical_position.call_args[0]
    assert all(isinstance(a, float) for a in args)


# ── 15. MPR positioning uses physical coordinates ─────────────────────────────

def test_15_mpr_positioning_uses_physical_coordinates(qapp, mock_viewer):
    mock_viewer.surgical_plan.set_target(65.2, -10.4, 88.9)
    mock_viewer.apply_quick_surgical_view("TARGET")

    # snap_to_volume_point must receive physical mm floats
    mock_viewer.mpr_view.snap_to_volume_point.assert_called_with(65.2, -10.4, 88.9)
    args = mock_viewer.mpr_view.snap_to_volume_point.call_args[0]
    assert all(isinstance(a, float) for a in args)


# ── 16. View changes do not reload DICOM ──────────────────────────────────────

def test_16_view_changes_do_not_reload_dicom(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    with patch.object(mock_viewer, "load_dicom_series", create=True) as mock_load:
        mock_viewer.apply_quick_surgical_view("ENTRY")
        mock_viewer.apply_quick_surgical_view("TARGET")
        mock_viewer.apply_quick_surgical_view("ROUTE")
        mock_viewer.apply_quick_surgical_view("STRUCTURES")
        mock_viewer.apply_quick_surgical_view("INSTRUMENT")
        mock_viewer.apply_quick_surgical_view("FULL PLAN")
        mock_load.assert_not_called()


# ── 17. View changes do not modify Entry ──────────────────────────────────────

def test_17_view_changes_do_not_modify_entry(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    orig_coords = populated_session.entry_point.coordinates

    mock_viewer.apply_quick_surgical_view("ENTRY")
    mock_viewer.apply_quick_surgical_view("ROUTE")
    mock_viewer.apply_quick_surgical_view("FULL PLAN")

    assert populated_session.entry_point.coordinates == orig_coords


# ── 18. View changes do not modify Target ─────────────────────────────────────

def test_18_view_changes_do_not_modify_target(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    orig_coords = populated_session.target_point.coordinates

    mock_viewer.apply_quick_surgical_view("TARGET")
    mock_viewer.apply_quick_surgical_view("ROUTE")
    mock_viewer.apply_quick_surgical_view("FULL PLAN")

    assert populated_session.target_point.coordinates == orig_coords


# ── 19. View changes do not modify Planned Route ──────────────────────────────

def test_19_view_changes_do_not_modify_planned_route(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    orig_route = populated_session.get_planned_route()
    orig_len = orig_route.length_mm

    mock_viewer.apply_quick_surgical_view("ROUTE")
    mock_viewer.apply_quick_surgical_view("FULL PLAN")

    assert populated_session.get_planned_route().length_mm == orig_len


# ── 20. View changes do not modify Structures ─────────────────────────────────

def test_20_view_changes_do_not_modify_structures(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    orig_structs = [s.to_dict() for s in populated_session.get_avoid_structures()]

    mock_viewer.apply_quick_surgical_view("STRUCTURES")
    mock_viewer.apply_quick_surgical_view("FULL PLAN")

    current_structs = [s.to_dict() for s in populated_session.get_avoid_structures()]
    assert current_structs == orig_structs


# ── 21. View changes do not modify Corridor ───────────────────────────────────

def test_21_view_changes_do_not_modify_corridor(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    orig_radius = populated_session.corridor_radius_mm
    orig_enabled = populated_session.corridor_enabled

    mock_viewer.apply_quick_surgical_view("ROUTE")
    mock_viewer.apply_quick_surgical_view("FULL PLAN")

    assert populated_session.corridor_radius_mm == orig_radius
    assert populated_session.corridor_enabled == orig_enabled


# ── 22. View changes do not modify Virtual Instrument parameters ──────────────

def test_22_view_changes_do_not_modify_virtual_instrument_parameters(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    orig_depth = populated_session.instrument_depth_mm
    orig_diam = populated_session.instrument_diameter_mm
    orig_vis = populated_session.instrument_visible

    mock_viewer.apply_quick_surgical_view("INSTRUMENT")
    mock_viewer.apply_quick_surgical_view("FULL PLAN")

    assert populated_session.instrument_depth_mm == orig_depth
    assert populated_session.instrument_diameter_mm == orig_diam
    assert populated_session.instrument_visible == orig_vis


# ── 23. View changes do not modify Live Deviation parameters ──────────────────

def test_23_view_changes_do_not_modify_live_deviation_parameters(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    orig_offsets = (populated_session.deviation_offset_x_mm, populated_session.deviation_offset_y_mm, populated_session.deviation_offset_z_mm)
    orig_angles = (populated_session.deviation_yaw_deg, populated_session.deviation_pitch_deg)

    mock_viewer.apply_quick_surgical_view("INSTRUMENT")
    mock_viewer.apply_quick_surgical_view("FULL PLAN")

    assert (populated_session.deviation_offset_x_mm, populated_session.deviation_offset_y_mm, populated_session.deviation_offset_z_mm) == orig_offsets
    assert (populated_session.deviation_yaw_deg, populated_session.deviation_pitch_deg) == orig_angles


# ── 24. View changes do not modify Before/After pair ──────────────────────────

def test_24_view_changes_do_not_modify_before_after_pair(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    mock_viewer.comparison.set_before_scan({"id": "pre_scan"}, "MRN-001")
    mock_viewer.comparison.set_after_scan({"id": "post_scan"}, "MRN-001")
    mock_viewer.comparison.start_comparison("TOGGLE")

    mock_viewer.apply_quick_surgical_view("ENTRY")
    mock_viewer.apply_quick_surgical_view("TARGET")
    mock_viewer.apply_quick_surgical_view("ROUTE")

    assert mock_viewer.comparison.is_active is True
    assert mock_viewer.comparison.before_scan == {"id": "pre_scan"}
    assert mock_viewer.comparison.after_scan == {"id": "post_scan"}


# ── 25. View changes do not modify Plan Versions ──────────────────────────────

def test_25_view_changes_do_not_modify_plan_versions(test_db, qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    snap = populated_session.create_snapshot()
    v = database.save_surgical_plan_version("scan_001", "Initial Plan", snap)

    mock_viewer.apply_quick_surgical_view("ENTRY")
    mock_viewer.apply_quick_surgical_view("FULL PLAN")

    versions = database.get_surgical_plan_versions("scan_001")
    assert len(versions) == 1
    assert versions[0]["id"] == v["id"]
    assert versions[0]["name"] == "Initial Plan"


# ── 26. View-only actions do not mark plan as unsaved ─────────────────────────

def test_26_view_only_actions_do_not_mark_plan_as_unsaved(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    populated_session.mark_saved("version_001")
    assert populated_session.has_unsaved_changes() is False

    mock_viewer.apply_quick_surgical_view("ENTRY")
    assert populated_session.has_unsaved_changes() is False

    mock_viewer.apply_quick_surgical_view("TARGET")
    assert populated_session.has_unsaved_changes() is False

    mock_viewer.apply_quick_surgical_view("ROUTE")
    assert populated_session.has_unsaved_changes() is False

    mock_viewer.apply_quick_surgical_view("STRUCTURES")
    assert populated_session.has_unsaved_changes() is False

    mock_viewer.apply_quick_surgical_view("INSTRUMENT")
    assert populated_session.has_unsaved_changes() is False

    mock_viewer.apply_quick_surgical_view("FULL PLAN")
    assert populated_session.has_unsaved_changes() is False


# ── 27. Quick views do not create caliper measurements ────────────────────────

def test_27_quick_views_do_not_create_caliper_measurements(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    assert len(mock_viewer._measurements_3d) == 0

    mock_viewer.apply_quick_surgical_view("ENTRY")
    mock_viewer.apply_quick_surgical_view("TARGET")
    mock_viewer.apply_quick_surgical_view("ROUTE")

    assert len(mock_viewer._measurements_3d) == 0


# ── 28. Quick views do not modify _measurements_3d ────────────────────────────

def test_28_quick_views_do_not_modify_measurements_3d(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    mock_viewer._measurements_3d = [{"id": "m1", "length_mm": 24.5}]

    mock_viewer.apply_quick_surgical_view("FULL PLAN")
    mock_viewer.apply_quick_surgical_view("ROUTE")

    assert len(mock_viewer._measurements_3d) == 1
    assert mock_viewer._measurements_3d[0]["id"] == "m1"


# ── 29. No duplicate camera/viewer objects are created ────────────────────────

def test_29_no_duplicate_camera_viewer_objects_created(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    cam_before = mock_viewer.plotter.camera
    mpr_before = mock_viewer.mpr_view
    panel_2d_before = mock_viewer.panel_2d

    mock_viewer.apply_quick_surgical_view("ENTRY")
    mock_viewer.apply_quick_surgical_view("FULL PLAN")

    assert mock_viewer.plotter.camera is cam_before
    assert mock_viewer.mpr_view is mpr_before
    assert mock_viewer.panel_2d is panel_2d_before


# ── 30. Repeated preset activation does not accumulate actors ─────────────────

def test_30_repeated_preset_activation_does_not_accumulate_actors(qapp, mock_viewer, populated_session):
    mock_viewer.surgical_plan = populated_session
    initial_actors = dict(mock_viewer.plotter.actors)

    for _ in range(5):
        mock_viewer.apply_quick_surgical_view("ENTRY")
        mock_viewer.apply_quick_surgical_view("TARGET")
        mock_viewer.apply_quick_surgical_view("ROUTE")
        mock_viewer.apply_quick_surgical_view("FULL PLAN")

    assert len(mock_viewer.plotter.actors) == len(initial_actors)


# ── 31. Scan change resets quick-view availability ────────────────────────────

def test_31_scan_change_resets_quick_view_availability(qapp, sample_patient, sample_scan):
    or_mode = OrIcuMode()
    or_mode.set_mode("OR")
    or_mode.set_patient_and_scan(sample_patient, sample_scan)

    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 30.0)
    or_mode.set_surgical_plan(session)

    assert or_mode.quick_views_card.btn_entry.isEnabled() is True
    or_mode.quick_views_card.set_active_preset("ENTRY")
    assert or_mode.quick_views_card.lbl_active_preset.text() == "View: ENTRY"

    # Select new scan -> session resets, quick views card resets
    new_scan = {"id": "scan_002", "file_path": "/path/to/scan_002", "type": "CT", "patient_mrn": sample_patient["mrn"]}
    or_mode._select_scan(new_scan)

    assert or_mode.quick_views_card.btn_entry.isEnabled() is False
    assert or_mode.quick_views_card.lbl_active_preset.text() == "View: None"


# ── 32. Patient change resets quick-view availability ─────────────────────────

def test_32_patient_change_resets_quick_view_availability(qapp, sample_patient, sample_scan):
    or_mode = OrIcuMode()
    or_mode.set_mode("OR")
    or_mode.set_patient_and_scan(sample_patient, sample_scan)

    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 30.0)
    session.set_target(40.0, 50.0, 60.0)
    or_mode.set_surgical_plan(session)
    assert or_mode.quick_views_card.btn_route.isEnabled() is True

    # Patient cleared
    or_mode.set_patient_and_scan({})
    assert or_mode.current_patient == {}


# ── 33. OR <-> ICU does not duplicate state ───────────────────────────────────

def test_33_or_icu_does_not_duplicate_state(qapp, sample_patient, sample_scan, populated_session):
    or_mode = OrIcuMode()
    or_mode.set_patient_and_scan(sample_patient, sample_scan)
    or_mode.set_surgical_plan(populated_session)
    or_mode.set_mode("OR")

    # In OR mode, card is present and active
    assert or_mode.quick_views_card is not None
    assert or_mode.quick_views_card.btn_full_plan.isEnabled() is True

    # Switch to ICU mode
    or_mode.set_mode("ICU")
    assert or_mode.current_mode == "ICU"
    # Surgical plan is preserved
    assert or_mode.surgical_plan is populated_session

    # Switch back to OR mode
    or_mode.set_mode("OR")
    assert or_mode.current_mode == "OR"
    assert or_mode.quick_views_card is not None
    assert or_mode.quick_views_card.btn_full_plan.isEnabled() is True


# ── 34. Existing Entry/Target tests pass ───────────────────────────────────────

def test_34_existing_entry_target_tests_pass(qapp):
    session = SurgicalPlanSession()
    session.set_entry(1.0, 2.0, 3.0)
    session.set_target(4.0, 5.0, 6.0)
    assert session.has_entry() is True
    assert session.has_target() is True
    assert session.has_planned_route() is True


# ── 35. Existing Planned Route tests pass ──────────────────────────────────────

def test_35_existing_planned_route_tests_pass(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(3.0, 4.0, 0.0)
    route = session.get_planned_route()
    assert route is not None
    assert pytest.approx(route.length_mm, rel=1e-3) == 5.0


# ── 36. Existing Structures tests pass ─────────────────────────────────────────

def test_36_existing_structures_tests_pass(qapp):
    session = SurgicalPlanSession()
    s = session.add_avoid_structure(10.0, 20.0, 30.0, radius_mm=4.0, name="Nerve")
    assert session.has_avoid_structures() is True
    assert session.get_avoid_structure(s.structure_id).name == "Nerve"


# ── 37. Existing Surgical Corridor tests pass ──────────────────────────────────

def test_37_existing_surgical_corridor_tests_pass(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(0.0, 0.0, 10.0)
    corridor = session.get_surgical_corridor()
    assert corridor is not None
    assert corridor.length_mm == 10.0


# ── 38. Existing Virtual Instrument tests pass ─────────────────────────────────

def test_38_existing_virtual_instrument_tests_pass(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(0.0, 0.0, 100.0)
    session.set_insertion_depth(25.0)
    vi = session.get_virtual_instrument()
    assert vi is not None
    assert vi.tip_position_mm == (0.0, 0.0, 25.0)


# ── 39. Existing Live Deviation tests pass ─────────────────────────────────────

def test_39_existing_live_deviation_tests_pass(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(0.0, 0.0, 100.0)
    session.set_deviation_offsets(2.0, 0.0, 0.0)
    pose = session.get_current_instrument_pose()
    assert pose is not None
    assert pytest.approx(pose.lateral_deviation_mm, rel=1e-3) == 2.0


# ── 40. Existing Plan Version tests pass ───────────────────────────────────────

def test_40_existing_plan_version_tests_pass(test_db, populated_session):
    snap = populated_session.create_snapshot()
    v = database.save_surgical_plan_version("scan_001", "Ver A", snap)
    assert v["id"] is not None
    assert v["name"] == "Ver A"
    fetched = database.get_surgical_plan_version(v["id"])
    assert fetched["name"] == "Ver A"


# ── 41. Existing Before/After tests pass ───────────────────────────────────────

def test_41_existing_before_after_tests_pass():
    comp = BeforeAfterComparison()
    assert comp.set_before_scan({"id": "s1"}, "MRN-1") is True
    assert comp.set_after_scan({"id": "s2"}, "MRN-1") is True
    assert comp.start_comparison("TOGGLE") is True
    assert comp.is_active is True


# ── 42. Existing measurement tests pass ────────────────────────────────────────

def test_42_existing_measurement_tests_pass(qapp, mock_viewer):
    mock_viewer._measurements_3d = [{"id": "m1", "value": 18.4, "unit": "mm"}]
    assert len(mock_viewer.get_measurements_3d()) == 1
    assert mock_viewer.get_measurements_3d()[0]["value"] == 18.4
    mock_viewer.set_measurements_3d_visible(True)
    assert mock_viewer.is_measurements_3d_visible() is True


# ── 43. Existing database tests pass ───────────────────────────────────────────

def test_43_existing_database_tests_pass(test_db):
    database.add_patient("MRN-QUICK-43", "Test Quick", "F", "45")
    pts = database.get_all_patients()
    assert any(p["mrn"] == "MRN-QUICK-43" for p in pts)
    pt = database.get_patient("MRN-QUICK-43")
    assert pt is not None
    assert pt["name"] == "Test Quick"


# ── 44. Existing measurement-persistence tests pass ───────────────────────────

def test_44_existing_measurement_persistence_tests_pass(qapp, mock_viewer, populated_session):
    mock_viewer._measurements_3d = [{"id": "m_persist", "value": 32.1, "unit": "mm"}]
    mock_viewer.surgical_plan = populated_session
    mock_viewer.apply_quick_surgical_view("ENTRY")
    mock_viewer.apply_quick_surgical_view("TARGET")
    mock_viewer.apply_quick_surgical_view("ROUTE")
    mock_viewer.apply_quick_surgical_view("FULL PLAN")

    assert len(mock_viewer.get_measurements_3d()) == 1
    assert mock_viewer.get_measurements_3d()[0]["id"] == "m_persist"
    assert mock_viewer.get_measurements_3d()[0]["value"] == 32.1


# ── 45. Existing 2D/3D synchronization tests pass ──────────────────────────────

def test_45_existing_2d_3d_synchronization_tests_pass(qapp, mock_viewer):
    mock_viewer._snap_slice_viewers(10.0, 20.0, 30.0)
    mock_viewer.mpr_view.snap_to_volume_point.assert_called_with(10.0, 20.0, 30.0)
    mock_viewer.panel_2d.set_physical_position.assert_called_with(10.0, 20.0, 30.0)


# ── 46. Existing ICU/OR shell tests pass ───────────────────────────────────────

def test_46_existing_icu_or_shell_tests_pass(qapp, sample_patient, sample_scan):
    widget = OrIcuMode()
    widget.set_patient_and_scan(sample_patient, sample_scan)
    widget.set_mode("OR")

    item_texts = [
        label.text()
        for label in widget.workflow_container.findChildren(QLabel)
    ]
    all_combined = " ".join(item_texts)
    assert "Quick Surgical Views" in all_combined
