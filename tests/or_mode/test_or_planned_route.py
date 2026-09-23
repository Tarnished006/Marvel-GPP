# test_or_planned_route.py
"""
Unit and integration tests for OR Feature 2: PLANNED ROUTE.

Covers all 26 required verification scenarios:
 1. No route exists without Entry.
 2. No route exists without Target.
 3. Route becomes available when both exist.
 4. Route starts exactly at Entry coordinates.
 5. Route ends exactly at Target coordinates.
 6. Path length calculation is correct.
 7. ΔX calculation is correct.
 8. ΔY calculation is correct.
 9. ΔZ calculation is correct.
10. Route uses physical mm coordinates.
11. Route does not use slice indices as its geometric source.
12. Route actor is distinct from Entry actor.
13. Route actor is distinct from Target actor.
14. Replacing Entry updates route.
15. Replacing Target updates route.
16. Clearing Entry removes/hides route.
17. Clearing Target removes/hides route.
18. Clearing both removes/hides route.
19. Loading another scan resets route.
20. Route creation does not create a normal caliper measurement.
21. Route does not trigger DICOM reload.
22. View Route uses the existing Viewer3D/MPR without reload.
23. No duplicate route actors accumulate after repeated updates.
24. Existing Entry/Target functionality remains intact.
25. Existing measurement functionality remains isolated and unchanged.
26. Voice commands route correctly to planned route.
"""

import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

import os
import math
import pytest
import numpy as np
import pyvista as pv
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from surgical_plan import PlanningPoint, PlannedRoute, SurgicalPlanSession
from screens.or_icu_mode import OrIcuMode, PlannedRouteCard
from screens.viewer_3d import Viewer3D


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


# ── Test 1: No route exists without Entry ────────────────────────────────────
def test_1_no_route_exists_without_entry(qapp):
    session = SurgicalPlanSession()
    session.set_target(20.0, 30.0, 40.0)
    assert session.has_entry() is False
    assert session.has_planned_route() is False
    assert session.get_planned_route() is None

    card = PlannedRouteCard()
    card.update_route(session)
    assert "Requires Entry + Target" in card.lbl_route_status.text()
    assert card.btn_view_route.isEnabled() is False
    assert card.btn_clear_route.isEnabled() is False


# ── Test 2: No route exists without Target ───────────────────────────────────
def test_2_no_route_exists_without_target(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.0, 15.0, 20.0)
    assert session.has_target() is False
    assert session.has_planned_route() is False
    assert session.get_planned_route() is None

    card = PlannedRouteCard()
    card.update_route(session)
    assert "Requires Entry + Target" in card.lbl_route_status.text()
    assert card.btn_view_route.isEnabled() is False
    assert card.btn_clear_route.isEnabled() is False


# ── Test 3: Route becomes available when both exist ──────────────────────────
def test_3_route_available_when_both_exist(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 30.0)
    session.set_target(40.0, 60.0, 80.0)

    assert session.has_planned_route() is True
    route = session.get_planned_route()
    assert route is not None
    assert isinstance(route, PlannedRoute)

    card = PlannedRouteCard()
    card.update_route(session)
    assert "Planned Route defined" in card.lbl_route_status.text()
    assert card.btn_view_route.isEnabled() is True
    assert card.btn_clear_route.isEnabled() is True


# ── Test 4: Route starts exactly at Entry coordinates ────────────────────────
def test_4_route_starts_exactly_at_entry(qapp):
    session = SurgicalPlanSession()
    session.set_entry(12.5, 25.0, 37.5)
    session.set_target(50.0, 60.0, 70.0)

    route = session.get_planned_route()
    assert route.entry_point.coordinates == (12.5, 25.0, 37.5)


# ── Test 5: Route ends exactly at Target coordinates ─────────────────────────
def test_5_route_ends_exactly_at_target(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 30.0)
    session.set_target(95.5, 85.5, 75.5)

    route = session.get_planned_route()
    assert route.target_point.coordinates == (95.5, 85.5, 75.5)


# ── Test 6: Path length calculation is correct ───────────────────────────────
def test_6_path_length_calculation(qapp):
    session = SurgicalPlanSession()
    # 3-4-5 right triangle: ΔX=30, ΔY=40, ΔZ=0 -> Length = 50.0
    session.set_entry(0.0, 0.0, 10.0)
    session.set_target(30.0, 40.0, 10.0)

    route = session.get_planned_route()
    assert math.isclose(route.length_mm, 50.0, rel_tol=1e-5)

    # 3D: (10, 20, 30) -> (40, 60, 80) -> ΔX=30, ΔY=40, ΔZ=50 -> sqrt(900+1600+2500) = sqrt(5000) ≈ 70.7107
    session.set_entry(10.0, 20.0, 30.0)
    session.set_target(40.0, 60.0, 80.0)
    route = session.get_planned_route()
    expected_len = math.sqrt(30**2 + 40**2 + 50**2)
    assert math.isclose(route.length_mm, expected_len, rel_tol=1e-5)


# ── Test 7: ΔX calculation is correct ────────────────────────────────────────
def test_7_delta_x_calculation(qapp):
    session = SurgicalPlanSession()
    session.set_entry(15.0, 20.0, 30.0)
    session.set_target(45.0, 20.0, 30.0)
    route = session.get_planned_route()
    assert math.isclose(route.delta_x, 30.0, rel_tol=1e-5)

    # Negative delta
    session.set_target(5.0, 20.0, 30.0)
    route = session.get_planned_route()
    assert math.isclose(route.delta_x, -10.0, rel_tol=1e-5)


# ── Test 8: ΔY calculation is correct ────────────────────────────────────────
def test_8_delta_y_calculation(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.0, 25.0, 30.0)
    session.set_target(10.0, 75.0, 30.0)
    route = session.get_planned_route()
    assert math.isclose(route.delta_y, 50.0, rel_tol=1e-5)


# ── Test 9: ΔZ calculation is correct ────────────────────────────────────────
def test_9_delta_z_calculation(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 100.0)
    session.set_target(10.0, 20.0, 40.0)
    route = session.get_planned_route()
    assert math.isclose(route.delta_z, -60.0, rel_tol=1e-5)


# ── Test 10: Route uses physical mm coordinates ──────────────────────────────
def test_10_route_uses_physical_mm_coordinates(qapp):
    session = SurgicalPlanSession()
    session.set_entry(1.234, 5.678, 9.101)
    session.set_target(11.121, 13.141, 15.161)
    route = session.get_planned_route()

    assert isinstance(route.entry_point.x_mm, float)
    assert isinstance(route.target_point.x_mm, float)
    assert isinstance(route.length_mm, float)
    assert isinstance(route.delta_x, float)
    assert isinstance(route.delta_y, float)
    assert isinstance(route.delta_z, float)


# ── Test 11: Route does not use slice indices as geometric source ────────────
def test_11_route_does_not_use_slice_indices(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.5, 20.5, 30.5)
    session.set_target(40.5, 50.5, 60.5)
    route = session.get_planned_route()

    assert not hasattr(route, "slice_index")
    assert not hasattr(route, "axial_index")
    assert route.entry_point.x_mm == 10.5
    assert route.target_point.x_mm == 40.5


# ── Test 12: Route actor is distinct from Entry actor ────────────────────────
def test_12_route_actor_distinct_from_entry(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)

    assert "or_entry_marker" in viewer.plotter.actors
    assert "or_planned_route" in viewer.plotter.actors
    assert "or_planned_route" != "or_entry_marker"


# ── Test 13: Route actor is distinct from Target actor ───────────────────────
def test_13_route_actor_distinct_from_target(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)

    assert "or_target_marker" in viewer.plotter.actors
    assert "or_planned_route" in viewer.plotter.actors
    assert "or_planned_route" != "or_target_marker"


# ── Test 14: Replacing Entry updates route ───────────────────────────────────
def test_14_replacing_entry_updates_route(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(30.0, 40.0, 0.0)  # Length = 50.0
    route1 = viewer.surgical_plan.get_planned_route()
    assert math.isclose(route1.length_mm, 50.0, rel_tol=1e-5)

    # Replace Entry
    viewer.set_entry_point(30.0, 0.0, 0.0)  # New distance to (30, 40, 0) = 40.0
    route2 = viewer.surgical_plan.get_planned_route()
    assert math.isclose(route2.length_mm, 40.0, rel_tol=1e-5)
    assert route2.entry_point.x_mm == 30.0
    assert "or_planned_route" in viewer.plotter.actors


# ── Test 15: Replacing Target updates route ──────────────────────────────────
def test_15_replacing_target_updates_route(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(30.0, 40.0, 0.0)
    assert math.isclose(viewer.surgical_plan.get_planned_route().length_mm, 50.0, rel_tol=1e-5)

    # Replace Target
    viewer.set_target_point(0.0, 60.0, 0.0)  # New distance = 60.0
    assert math.isclose(viewer.surgical_plan.get_planned_route().length_mm, 60.0, rel_tol=1e-5)
    assert viewer.surgical_plan.get_planned_route().target_point.y_mm == 60.0


# ── Test 16: Clearing Entry removes/hides route ──────────────────────────────
def test_16_clearing_entry_removes_route(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)
    assert "or_planned_route" in viewer.plotter.actors

    viewer.clear_entry_point()
    assert viewer.surgical_plan.has_planned_route() is False
    assert "or_planned_route" not in viewer.plotter.actors
    assert not any("or_planned_route_label" in k for k in viewer.plotter.actors)
    # Target still exists
    assert "or_target_marker" in viewer.plotter.actors


# ── Test 17: Clearing Target removes/hides route ─────────────────────────────
def test_17_clearing_target_removes_route(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)
    assert "or_planned_route" in viewer.plotter.actors

    viewer.clear_target_point()
    assert viewer.surgical_plan.has_planned_route() is False
    assert "or_planned_route" not in viewer.plotter.actors
    assert not any("or_planned_route_label" in k for k in viewer.plotter.actors)
    # Entry still exists
    assert "or_entry_marker" in viewer.plotter.actors


# ── Test 18: Clearing both removes/hides route ───────────────────────────────
def test_18_clearing_both_removes_route(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)
    assert "or_planned_route" in viewer.plotter.actors

    viewer.clear_both_planning_points()
    assert viewer.surgical_plan.has_planned_route() is False
    assert "or_planned_route" not in viewer.plotter.actors
    assert "or_entry_marker" not in viewer.plotter.actors
    assert "or_target_marker" not in viewer.plotter.actors


# ── Test 19: Loading another scan resets route ───────────────────────────────
def test_19_loading_another_scan_resets_route(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)
    viewer.surgical_plan.scan_id = "scan_001"
    assert viewer.surgical_plan.has_planned_route() is True

    # Simulate loading different scan
    viewer.load_scan({"name": "Patient 2", "mrn": "MRN002"}, {"id": "scan_002", "file_path": "path/scan_002"})

    assert viewer.surgical_plan.has_planned_route() is False
    assert viewer.surgical_plan.entry_point is None
    assert viewer.surgical_plan.target_point is None
    assert "or_planned_route" not in viewer.plotter.actors


# ── Test 20: Route creation does not create a normal caliper measurement ────
def test_20_route_does_not_create_caliper_measurement(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    assert len(viewer._measurements_3d) == 0
    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)

    # 3D measurements list must remain completely empty
    assert len(viewer._measurements_3d) == 0
    assert len(viewer._measurement_actor_names) == 0


# ── Test 21: Route does not trigger DICOM reload ─────────────────────────────
def test_21_route_does_not_trigger_dicom_reload(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)
    viewer._start_load = MagicMock()

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)

    viewer._start_load.assert_not_called()


# ── Test 22: View Route uses the existing Viewer3D/MPR ───────────────────────
def test_22_view_route_uses_existing_viewer_mpr(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 20.0, 10.0)
    viewer.set_target_point(30.0, 40.0, 30.0)

    # Midpoint is (20.0, 30.0, 20.0)
    viewer.mpr_view.snap_to_volume_point = MagicMock()
    viewer.panel_2d.set_physical_position = MagicMock()

    viewer.view_planned_route()

    viewer.mpr_view.snap_to_volume_point.assert_called_once_with(20.0, 30.0, 20.0)
    viewer.panel_2d.set_physical_position.assert_called_once_with(20.0, 30.0, 20.0)


# ── Test 23: No duplicate route actors accumulate after repeated updates ─────
def test_23_no_duplicate_route_actors_accumulate(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)

    for i in range(5):
        viewer.set_entry_point(10.0 + i, 10.0, 10.0)
        viewer.set_target_point(50.0 + i, 50.0, 50.0)

    # Exactly one or_planned_route actor exists in plotter
    route_actors = [k for k in viewer.plotter.actors if k == "or_planned_route"]
    assert len(route_actors) == 1


# ── Test 24: Existing Entry/Target functionality remains intact ──────────────
def test_24_existing_entry_target_intact(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(15.0, 25.0, 35.0)
    assert viewer.surgical_plan.has_entry() is True
    assert "or_entry_marker" in viewer.plotter.actors

    viewer.set_target_point(55.0, 65.0, 75.0)
    assert viewer.surgical_plan.has_target() is True
    assert "or_target_marker" in viewer.plotter.actors


# ── Test 25: Existing measurement functionality remains isolated ─────────────
def test_25_existing_measurement_isolated(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # Set planned route
    viewer.set_entry_point(10.0, 10.0, 10.0)
    viewer.set_target_point(50.0, 50.0, 50.0)
    assert "or_planned_route" in viewer.plotter.actors

    # Add a normal 3D caliper measurement
    m = viewer._add_3d_measurement((5.0, 5.0, 5.0), (15.0, 15.0, 15.0))
    assert len(viewer._measurements_3d) == 1

    # Clear measurements -> MUST NOT remove the planned route
    viewer.clear_measurements_3d()
    assert len(viewer._measurements_3d) == 0
    assert "or_planned_route" in viewer.plotter.actors
    assert "or_entry_marker" in viewer.plotter.actors
    assert "or_target_marker" in viewer.plotter.actors


# ── Test 26: Voice commands route correctly to planned route ─────────────────
def test_26_voice_commands_route_to_planned_route(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(10.0, 20.0, 30.0)
    viewer.set_target_point(40.0, 60.0, 80.0)

    viewer.view_planned_route = MagicMock()
    viewer.handle_voice_command("view planned route")
    viewer.view_planned_route.assert_called_once()

    viewer.set_planned_route_visible = MagicMock()
    viewer.handle_voice_command("hide planned route")
    viewer.set_planned_route_visible.assert_called_once_with(False)

    viewer.clear_planned_route = MagicMock()
    viewer.handle_voice_command("clear planned route")
    viewer.clear_planned_route.assert_called_once()
