# test_or_entry_target.py
"""
Unit and integration tests for OR Feature 1: ENTRY + TARGET planning landmarks.

Covers all 20 required verification scenarios:
 1. OR mode initializes with no Entry/Target.
 2. Set Entry stores exactly one physical XYZ point.
 3. Set Target stores exactly one physical XYZ point.
 4. Entry and Target remain independent.
 5. Replacing Entry does not modify Target.
 6. Replacing Target does not modify Entry.
 7. Clear Entry does not clear Target.
 8. Clear Target does not clear Entry.
 9. Clear Both removes both.
10. Entry marker is distinct from Target marker (actor names, colors, labels).
11. Entry and Target coordinates are in physical millimetres.
12. Entry/Target do not use slice index as their primary stored position.
13. View Entry moves the existing imaging view to the Entry coordinate.
14. View Target moves the existing imaging view to the Target coordinate.
15. Setting Entry/Target does not trigger a DICOM reload.
16. Setting Entry/Target does not create a standard caliper measurement.
17. Switching ICU <-> OR preserves Entry/Target during the active case.
18. Loading a different scan does not incorrectly retain previous scan's planning points.
19. Existing 2D/3D synchronization still passes.
20. Voice commands route correctly to surgical planning.
"""

import os
import pytest
import numpy as np
import pyvista as pv
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from surgical_plan import PlanningPoint, SurgicalPlanSession
from screens.or_icu_mode import OrIcuMode, EntryTargetCard
from screens.viewer_3d import Viewer3D
from screens.slice_2d_viewer import Slice2DViewerWidget


@pytest.fixture(scope="session")
def qapp():
    from database import init_db
    init_db()
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def synthetic_volume():
    """Generates synthetic 3D CT volume (H=40, W=50, D=30) with anisotropic spacings."""
    H, W, D = 40, 50, 30
    vol = np.full((H, W, D), -1000.0, dtype=np.float32)
    vol[10:30, 15:35, 5:25] = 40.0
    vol[15:25, 20:30, 10:20] = 1200.0
    pixel_spacing = (0.8, 0.5)  # (dy=0.8, dx=0.5)
    slice_thickness = 2.0       # dz=2.0
    return vol, pixel_spacing, slice_thickness


def _setup_viewer_with_mesh_and_volume(vol, pixel_spacing, slice_thickness):
    """Creates a configured Viewer3D with synthetic volume and matching bone mesh."""
    viewer = Viewer3D()
    H, W, D = vol.shape
    dy, dx = pixel_spacing
    dz = slice_thickness

    bounds = (0.0, (W - 1) * dx, 0.0, (H - 1) * dy, 0.0, (D - 1) * dz)
    sphere = pv.Sphere(radius=15.0, center=((bounds[0] + bounds[1]) / 2, (bounds[2] + bounds[3]) / 2, (bounds[4] + bounds[5]) / 2))
    mesh = sphere.triangulate()
    mesh.point_data["HU_density"] = np.linspace(200.0, 1600.0, mesh.n_points)

    class MockMeshSet:
        bone_mesh = mesh
        bone_isovalue = 400.0

        @staticmethod
        def has_hu_density():
            return True

        @staticmethod
        def get_hu_range():
            return (200.0, 1600.0)

    viewer.meshset = MockMeshSet()
    viewer.mesh_bounds = bounds
    viewer.panel_2d.load_volume(vol, pixel_spacing, slice_thickness)
    viewer.mpr_view.vol_data = vol
    viewer.mpr_view.vol_8bit = np.clip((vol + 1000.0) / 2000.0 * 255.0, 0, 255).astype(np.uint8)
    viewer.mpr_view.pixel_spacing = pixel_spacing
    viewer.mpr_view.slice_thickness = slice_thickness
    viewer.mpr_view.idx_x = W // 2
    viewer.mpr_view.idx_y = H // 2
    viewer.mpr_view.idx_z = D // 2
    viewer.state_stack.setCurrentIndex(viewer._PAGE_SCENE)

    return viewer


# ── Test 1: OR mode initializes with no Entry/Target ─────────────────────────
def test_1_or_mode_initializes_with_no_entry_target(qapp):
    session = SurgicalPlanSession()
    assert session.has_entry() is False
    assert session.has_target() is False
    assert session.entry_point is None
    assert session.target_point is None

    or_mode = OrIcuMode()
    or_mode.set_mode("OR")
    assert hasattr(or_mode, "entry_target_card")
    assert or_mode.entry_target_card is not None
    assert "Not marked" in or_mode.entry_target_card.lbl_entry_status.text()
    assert "Not marked" in or_mode.entry_target_card.lbl_target_status.text()


# ── Test 2: Set Entry stores exactly one physical XYZ point ──────────────────
def test_2_set_entry_stores_physical_xyz_point(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    pt = viewer.set_entry_point(12.4, 25.8, 38.2, source_view="3D")
    assert isinstance(pt, PlanningPoint)
    assert pt.point_type == "ENTRY"
    assert pt.coordinates == (12.4, 25.8, 38.2)
    assert viewer.surgical_plan.has_entry() is True
    assert viewer.surgical_plan.entry_point.coordinates == (12.4, 25.8, 38.2)


# ── Test 3: Set Target stores exactly one physical XYZ point ─────────────────
def test_3_set_target_stores_physical_xyz_point(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    pt = viewer.set_target_point(5.5, 15.2, 45.0, source_view="3D")
    assert isinstance(pt, PlanningPoint)
    assert pt.point_type == "TARGET"
    assert pt.coordinates == (5.5, 15.2, 45.0)
    assert viewer.surgical_plan.has_target() is True
    assert viewer.surgical_plan.target_point.coordinates == (5.5, 15.2, 45.0)


# ── Test 4: Entry and Target remain independent ──────────────────────────────
def test_4_entry_and_target_remain_independent(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 30.0)
    assert session.has_entry() is True
    assert session.has_target() is False

    session2 = SurgicalPlanSession()
    session2.set_target(15.0, 25.0, 35.0)
    assert session2.has_entry() is False
    assert session2.has_target() is True


# ── Test 5: Replacing Entry does not modify Target ───────────────────────────
def test_5_replacing_entry_does_not_modify_target(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)
    assert viewer.surgical_plan.target_point.coordinates == (50.0, 50.0, 50.0)

    # Replace Entry
    viewer.set_entry_point(20.0, 20.0, 20.0)
    assert viewer.surgical_plan.entry_point.coordinates == (20.0, 20.0, 20.0)
    assert viewer.surgical_plan.target_point.coordinates == (50.0, 50.0, 50.0)


# ── Test 6: Replacing Target does not modify Entry ───────────────────────────
def test_6_replacing_target_does_not_modify_entry(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)

    # Replace Target
    viewer.set_target_point(60.0, 60.0, 60.0)
    assert viewer.surgical_plan.entry_point.coordinates == (10.0, 10.0, 10.0)
    assert viewer.surgical_plan.target_point.coordinates == (60.0, 60.0, 60.0)


# ── Test 7: Clear Entry does not clear Target ────────────────────────────────
def test_7_clear_entry_does_not_clear_target(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)

    viewer.clear_entry_point()
    assert viewer.surgical_plan.has_entry() is False
    assert viewer.surgical_plan.entry_point is None
    assert viewer.surgical_plan.has_target() is True
    assert viewer.surgical_plan.target_point.coordinates == (50.0, 50.0, 50.0)


# ── Test 8: Clear Target does not clear Entry ────────────────────────────────
def test_8_clear_target_does_not_clear_entry(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)

    viewer.clear_target_point()
    assert viewer.surgical_plan.has_target() is False
    assert viewer.surgical_plan.target_point is None
    assert viewer.surgical_plan.has_entry() is True
    assert viewer.surgical_plan.entry_point.coordinates == (10.0, 10.0, 10.0)


# ── Test 9: Clear Both removes both ──────────────────────────────────────────
def test_9_clear_both_removes_both(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)

    viewer.clear_both_planning_points()
    assert viewer.surgical_plan.has_entry() is False
    assert viewer.surgical_plan.has_target() is False
    assert viewer.surgical_plan.entry_point is None
    assert viewer.surgical_plan.target_point is None


# ── Test 10: Entry marker is distinct from Target marker ─────────────────────
def test_10_entry_marker_is_distinct_from_target_marker(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)

    # Actor names are distinct
    assert "or_entry_marker" in viewer.plotter.actors
    assert "or_target_marker" in viewer.plotter.actors
    assert any("or_entry_label" in k for k in viewer.plotter.actors)
    assert any("or_target_label" in k for k in viewer.plotter.actors)

    # Clearing Entry removes Entry actors only
    viewer.clear_entry_point()
    assert "or_entry_marker" not in viewer.plotter.actors
    assert not any("or_entry_label" in k for k in viewer.plotter.actors)
    assert "or_target_marker" in viewer.plotter.actors
    assert any("or_target_label" in k for k in viewer.plotter.actors)


# ── Test 11: Entry and Target coordinates are in physical millimetres ────────
def test_11_entry_and_target_coordinates_are_in_physical_mm(qapp):
    session = SurgicalPlanSession()
    pt1 = session.set_entry(12.3456, 78.9101, 45.6789)
    pt2 = session.set_target(98.7654, 32.1098, 65.4321)

    assert isinstance(pt1.x_mm, float)
    assert isinstance(pt1.y_mm, float)
    assert isinstance(pt1.z_mm, float)
    assert abs(pt1.x_mm - 12.3456) < 1e-4
    assert abs(pt2.z_mm - 65.4321) < 1e-4


# ── Test 12: Entry/Target do not use slice index as primary stored position ──
def test_12_entry_target_do_not_use_slice_index_as_primary_position(qapp):
    session = SurgicalPlanSession()
    pt = session.set_entry(14.7, 28.3, 42.9)
    # The coordinate is not an integer index, but continuous mm
    assert pt.x_mm != round(pt.x_mm) or pt.y_mm != round(pt.y_mm) or pt.z_mm != round(pt.z_mm)
    assert hasattr(pt, "x_mm") and hasattr(pt, "y_mm") and hasattr(pt, "z_mm")
    assert not hasattr(pt, "slice_index")


# ── Test 13: View Entry moves imaging view to Entry coordinate ───────────────
def test_13_view_entry_moves_imaging_view_to_entry_coordinate(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(15.0, 20.0, 25.0)
    viewer.view_entry_point()

    # MPR and 2D panel should have snapped
    pos = viewer.panel_2d.get_current_physical_position()
    assert abs(pos[0] - 15.0) < 1.0
    assert abs(pos[1] - 20.0) < 1.0


# ── Test 14: View Target moves imaging view to Target coordinate ─────────────
def test_14_view_target_moves_imaging_view_to_target_coordinate(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_target_point(10.0, 15.0, 30.0)
    viewer.view_target_point()

    pos = viewer.panel_2d.get_current_physical_position()
    assert abs(pos[0] - 10.0) < 1.0
    assert abs(pos[1] - 15.0) < 1.0


# ── Test 15: Setting Entry/Target does not trigger a DICOM reload ─────────────
def test_15_setting_entry_target_does_not_trigger_dicom_reload(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer._start_load = MagicMock()
    viewer.set_entry_point(10.0, 20.0, 30.0)
    viewer.set_target_point(15.0, 25.0, 35.0)

    viewer._start_load.assert_not_called()


# ── Test 16: Setting Entry/Target does not create a standard caliper measurement
def test_16_setting_entry_target_does_not_create_standard_caliper_measurement(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_planning_picking_mode("SET_ENTRY")
    # Simulate picking a 3D point
    viewer._on_3d_point_picked(np.array([12.0, 24.0, 36.0]))

    # Must NOT create a 3D measurement or 2D measurement
    assert len(viewer._measurements_3d) == 0
    assert len(viewer.panel_2d.canvas.measurements) == 0
    assert viewer.surgical_plan.has_entry() is True
    assert viewer.surgical_plan.entry_point.coordinates == (12.0, 24.0, 36.0)
    assert viewer.is_planning_picking() is False


# ── Test 17: Switching ICU <-> OR preserves Entry/Target during active case ──
def test_17_switching_icu_or_preserves_entry_target_during_active_case(qapp):
    or_mode = OrIcuMode()
    plan = SurgicalPlanSession()
    plan.set_entry(11.1, 22.2, 33.3)
    plan.set_target(44.4, 55.5, 66.6)
    or_mode.set_surgical_plan(plan)

    or_mode.set_mode("OR")
    assert "11.1" in or_mode.entry_target_card.lbl_entry_status.text()
    assert "44.4" in or_mode.entry_target_card.lbl_target_status.text()

    or_mode.set_mode("ICU")
    or_mode.set_mode("OR")
    assert "11.1" in or_mode.entry_target_card.lbl_entry_status.text()
    assert "44.4" in or_mode.entry_target_card.lbl_target_status.text()


# ── Test 18: Loading a different scan clears previous scan's planning points ─
def test_18_loading_different_scan_clears_previous_planning_points(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    scan1 = {"id": 101, "file_path": "/path/scan1"}
    scan2 = {"id": 102, "file_path": "/path/scan2"}

    viewer.surgical_plan.reset_for_scan("/path/scan1")
    viewer.set_entry_point(10.0, 20.0, 30.0)
    assert viewer.surgical_plan.has_entry() is True

    # Loading scan 2
    viewer.load_scan({"name": "Test"}, scan2)
    assert viewer.surgical_plan.has_entry() is False
    assert viewer.surgical_plan.has_target() is False
    assert "or_entry_marker" not in viewer.plotter.actors


# ── Test 19: Existing 2D/3D synchronization still passes ─────────────────────
def test_19_existing_2d_3d_sync_still_passes(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_sync_2d_3d(True)
    assert viewer.is_sync_2d_3d_enabled() is True
    viewer.set_sync_2d_3d(False)
    assert viewer.is_sync_2d_3d_enabled() is False


# ── Test 20: Voice commands route correctly to surgical planning ────────────
def test_20_voice_commands_route_to_planning(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.handle_voice_command("set entry")
    assert viewer._planning_picking_mode == "SET_ENTRY"

    viewer.handle_voice_command("set target")
    assert viewer._planning_picking_mode == "SET_TARGET"

    viewer.set_entry_point(15.0, 25.0, 35.0)
    viewer.set_target_point(40.0, 50.0, 60.0)

    viewer.handle_voice_command("clear entry")
    assert viewer.surgical_plan.has_entry() is False
    assert viewer.surgical_plan.has_target() is True

    viewer.handle_voice_command("clear target")
    assert viewer.surgical_plan.has_target() is False
