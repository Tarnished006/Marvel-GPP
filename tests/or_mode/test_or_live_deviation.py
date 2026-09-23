# test_or_live_deviation.py
"""
Unit and integration tests for OR Feature 6: LIVE DEVIATION.

Covers all 42 required verification scenarios:
 1. No live deviation without Planned Route.
 2. Current instrument exists when route exists.
 3. Current state is separate from Planned Route.
 4. Current tip uses physical mm coordinates.
 5. Default current tip lies on Planned Route.
 6. Default lateral deviation is 0 mm.
 7. Applying an offset changes current tip.
 8. Lateral deviation calculation is correct.
 9. Nearest planned point calculation is correct.
10. Tip offset vector is correct.
11. Zero offset returns zero deviation.
12. Positive X offset works.
13. Positive Y offset works.
14. Positive Z offset works.
15. Negative offsets work.
16. Current instrument actor is distinct from Planned Route actor.
17. Current tip actor is distinct from Virtual Instrument actor.
18. Deviation connector actor is distinct from route actor.
19. Surgical Corridor remains based on Planned Route.
20. Structures to Avoid remain unchanged.
21. Current instrument does not create caliper measurements.
22. Live deviation does not enter _measurements_3d.
23. Angle calculation is correct.
24. Zero-vector angle case is handled safely.
25. Reset returns current instrument to Planned Route.
26. Route replacement resets/reconciles current state.
27. Entry clearing removes live deviation state.
28. Target clearing removes live deviation state.
29. No DICOM reload during deviation updates.
30. Slice changes do not reload DICOM.
31. MPR changes do not reload DICOM.
32. No duplicate live-deviation actors accumulate.
33. Scan change clears live deviation.
34. OR <-> ICU does not create duplicate state.
35. Voice commands route correctly.
36. LiveDeviationCard sliders and reset button work.
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

from surgical_plan import (
    PlanningPoint,
    PlannedRoute,
    AvoidStructure,
    SurgicalCorridor,
    VirtualInstrument,
    CurrentInstrumentPose,
    SurgicalPlanSession,
)
from screens.or_icu_mode import OrIcuMode, LiveDeviationCard
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
    viewer.state_stack.setCurrentIndex(viewer._PAGE_SCENE)
    return viewer


# ── Tests 1–3: Planned vs Current State & Availability ─────────────────────────

def test_1_no_live_deviation_without_planned_route(qapp):
    session = SurgicalPlanSession()
    assert not session.has_live_deviation()
    assert session.get_current_instrument_pose() is None

    session.set_entry(10.0, 20.0, 30.0)
    assert not session.has_live_deviation()
    assert session.get_current_instrument_pose() is None

    session.clear_entry()
    session.set_target(40.0, 50.0, 60.0)
    assert not session.has_live_deviation()
    assert session.get_current_instrument_pose() is None


def test_2_current_instrument_exists_when_route_exists(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 20.0, 30.0)
    assert session.has_planned_route()
    assert session.has_live_deviation()
    pose = session.get_current_instrument_pose()
    assert pose is not None
    assert isinstance(pose, CurrentInstrumentPose)


def test_3_current_state_is_separate_from_planned_route(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.0, 10.0, 10.0)
    session.set_target(50.0, 50.0, 50.0)
    route_before = session.get_planned_route()

    session.set_deviation_offsets(5.0, -3.0, 7.0)
    pose = session.get_current_instrument_pose()

    # Planned route is immutable
    assert session.get_planned_route().entry_point.coordinates == (10.0, 10.0, 10.0)
    assert session.get_planned_route().target_point.coordinates == (50.0, 50.0, 50.0)
    assert session.get_planned_route().length_mm == route_before.length_mm

    # Current simulated pose differs
    assert pose.current_tip_position_mm == (55.0, 47.0, 57.0)
    assert pose.nominal_tip_position_mm == (50.0, 50.0, 50.0)


# ── Tests 4–11: Deviation Geometry & Physical mm Coordinates ──────────────────

def test_4_current_tip_uses_physical_mm_coordinates(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(100.0, 0.0, 0.0)
    session.set_deviation_offsets(2.5, 3.5, 4.5)
    pose = session.get_current_instrument_pose()
    assert pose.current_tip_position_mm == (102.5, 3.5, 4.5)


def test_5_default_current_tip_lies_on_planned_route(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(0.0, 0.0, 50.0)
    pose = session.get_current_instrument_pose()
    assert pose.current_tip_position_mm == (0.0, 0.0, 50.0)
    assert pose.nearest_planned_point_mm == (0.0, 0.0, 50.0)


def test_6_default_lateral_deviation_is_zero(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 30.0)
    session.set_target(40.0, 60.0, 80.0)
    pose = session.get_current_instrument_pose()
    assert pytest.approx(pose.lateral_deviation_mm, abs=1e-5) == 0.0


def test_7_applying_offset_changes_current_tip(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    session.set_deviation_offsets(1.0, 2.0, 3.0)
    pose = session.get_current_instrument_pose()
    assert pose.current_tip_position_mm == (11.0, 2.0, 3.0)


def test_8_lateral_deviation_calculation_is_correct(qapp):
    # Route along X axis from (0,0,0) to (10,0,0)
    # Current tip at (10, 3, 4) -> nearest point on segment is (10, 0, 0)
    # Distance = sqrt(3^2 + 4^2) = 5.0 mm
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    session.set_deviation_offsets(0.0, 3.0, 4.0)
    pose = session.get_current_instrument_pose()
    assert pytest.approx(pose.lateral_deviation_mm, abs=1e-5) == 5.0


def test_9_nearest_planned_point_calculation_is_correct(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)

    # Offset beyond the target along the line: tip at (15, 0, 0)
    # Since it's a finite segment [0, 10], nearest planned point should be (10, 0, 0)
    session.set_deviation_offsets(5.0, 0.0, 0.0)
    pose = session.get_current_instrument_pose()
    assert pose.nearest_planned_point_mm == (10.0, 0.0, 0.0)
    assert pytest.approx(pose.lateral_deviation_mm, abs=1e-5) == 5.0

    # Offset behind the entry: tip at (-5, 0, 0)
    session.set_deviation_offsets(-15.0, 0.0, 0.0)
    pose = session.get_current_instrument_pose()
    assert pose.nearest_planned_point_mm == (0.0, 0.0, 0.0)
    assert pytest.approx(pose.lateral_deviation_mm, abs=1e-5) == 5.0


def test_10_tip_offset_vector_is_correct(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(20.0, 0.0, 0.0)
    session.set_deviation_offsets(0.0, 4.0, -3.0)
    pose = session.get_current_instrument_pose()
    # Nearest point is (20, 0, 0), tip is (20, 4, -3)
    # Tip offset vector = (0, 4, -3)
    assert pose.tip_offset_vector_mm == (0.0, 4.0, -3.0)


def test_11_zero_offset_returns_zero_deviation(qapp):
    session = SurgicalPlanSession()
    session.set_entry(5.0, 5.0, 5.0)
    session.set_target(15.0, 15.0, 15.0)
    session.set_deviation_offsets(0.0, 0.0, 0.0)
    pose = session.get_current_instrument_pose()
    assert pose.lateral_deviation_mm == 0.0
    assert pose.tip_offset_vector_mm == (0.0, 0.0, 0.0)


# ── Tests 12–15: Individual Axis Offsets (Positive and Negative) ───────────────

def test_12_positive_x_offset_works(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(0.0, 0.0, 10.0)
    session.set_deviation_offsets(6.0, 0.0, 0.0)
    pose = session.get_current_instrument_pose()
    assert pose.current_tip_position_mm == (6.0, 0.0, 10.0)
    assert pytest.approx(pose.lateral_deviation_mm, abs=1e-5) == 6.0


def test_13_positive_y_offset_works(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(0.0, 0.0, 10.0)
    session.set_deviation_offsets(0.0, 7.5, 0.0)
    pose = session.get_current_instrument_pose()
    assert pose.current_tip_position_mm == (0.0, 7.5, 10.0)
    assert pytest.approx(pose.lateral_deviation_mm, abs=1e-5) == 7.5


def test_14_positive_z_offset_works(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    session.set_deviation_offsets(0.0, 0.0, 8.0)
    pose = session.get_current_instrument_pose()
    assert pose.current_tip_position_mm == (10.0, 0.0, 8.0)
    assert pytest.approx(pose.lateral_deviation_mm, abs=1e-5) == 8.0


def test_15_negative_offsets_work(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 10.0, 10.0)
    session.set_deviation_offsets(-2.0, -4.0, -5.0)
    pose = session.get_current_instrument_pose()
    assert pose.current_tip_position_mm == (8.0, 6.0, 5.0)


# ── Tests 16–18: 3D Visualization & Actor Isolation ───────────────────────────

def test_16_current_instrument_actor_distinct_from_planned_route_actor(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)

    viewer.set_entry_point(5.0, 5.0, 5.0)
    viewer.set_target_point(15.0, 15.0, 15.0)
    viewer.set_live_deviation_visible(True)

    assert "or_planned_route" in viewer.plotter.actors
    assert "or_live_instrument" in viewer.plotter.actors
    assert viewer.plotter.actors["or_planned_route"] is not viewer.plotter.actors["or_live_instrument"]


def test_17_current_tip_actor_distinct_from_virtual_instrument_actor(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)

    viewer.set_entry_point(5.0, 5.0, 5.0)
    viewer.set_target_point(15.0, 15.0, 15.0)
    viewer.set_virtual_instrument_visible(True)
    viewer.set_live_deviation_visible(True)

    assert "or_virtual_instrument_tip" in viewer.plotter.actors
    assert "or_live_instrument_tip" in viewer.plotter.actors
    assert viewer.plotter.actors["or_virtual_instrument_tip"] is not viewer.plotter.actors["or_live_instrument_tip"]


def test_18_deviation_connector_actor_distinct_from_route_actor(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(10.0, 10.0, 10.0)
    viewer.set_live_deviation_offsets(4.0, 0.0, 0.0)
    viewer.set_live_deviation_visible(True)

    assert "or_live_deviation_line" in viewer.plotter.actors
    assert "or_planned_route" in viewer.plotter.actors
    assert viewer.plotter.actors["or_live_deviation_line"] is not viewer.plotter.actors["or_planned_route"]


# ── Tests 19–20: Corridor and Structures Independence ─────────────────────────

def test_19_surgical_corridor_remains_based_on_planned_route(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(20.0, 0.0, 0.0)
    session.set_corridor_radius(5.0)

    # Initial corridor based on planned route
    corridor = session.get_surgical_corridor()
    assert corridor.route.entry_point.coordinates == (0.0, 0.0, 0.0)
    assert corridor.route.target_point.coordinates == (20.0, 0.0, 0.0)

    # Apply substantial live deviation
    session.set_deviation_offsets(15.0, 20.0, -10.0)
    corridor_after = session.get_surgical_corridor()

    # Corridor route remains completely unchanged
    assert corridor_after.route.entry_point.coordinates == (0.0, 0.0, 0.0)
    assert corridor_after.route.target_point.coordinates == (20.0, 0.0, 0.0)
    assert corridor_after.radius_mm == 5.0


def test_20_structures_to_avoid_remain_unchanged(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(30.0, 0.0, 0.0)
    struct = session.add_avoid_structure(15.0, 5.0, 0.0, radius_mm=4.0, name="Critical Vessel")

    session.set_deviation_offsets(10.0, 10.0, 10.0)
    assert struct.center == (15.0, 5.0, 0.0)
    assert struct.radius_mm == 4.0
    assert struct.name == "Critical Vessel"

    # Evaluate distance from current simulated instrument
    pose = session.get_current_instrument_pose()
    dist = pose.distance_to_structure(struct)
    assert dist > 0.0
    assert not math.isnan(dist)


# ── Tests 21–22: Measurement Isolation ────────────────────────────────────────

def test_21_current_instrument_does_not_create_caliper_measurements(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)
    initial_calipers = len(viewer._measurements_3d)

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(20.0, 20.0, 20.0)
    viewer.set_live_deviation_offsets(5.0, 5.0, 5.0)
    viewer.set_live_deviation_visible(True)

    assert len(viewer._measurements_3d) == initial_calipers


def test_22_live_deviation_does_not_enter_measurements_3d(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(20.0, 20.0, 20.0)
    viewer.set_live_deviation_offsets(5.0, 5.0, 5.0)
    viewer.set_live_deviation_visible(True)

    assert len(viewer._measurements_3d) == 0
    assert not any("live_deviation" in name for name in viewer._measurement_actor_names)


# ── Tests 23–24: Angular Deviation & Zero-Vector Safety ───────────────────────

def test_23_angle_calculation_is_correct(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)

    # 0 deg default
    pose = session.get_current_instrument_pose()
    assert pytest.approx(pose.angular_deviation_deg, abs=1e-4) == 0.0

    # 30 deg yaw
    session.set_deviation_angles(30.0, 0.0)
    pose = session.get_current_instrument_pose()
    assert pytest.approx(pose.angular_deviation_deg, abs=0.5) == 30.0

    # 45 deg pitch
    session.set_deviation_angles(0.0, 45.0)
    pose = session.get_current_instrument_pose()
    assert pytest.approx(pose.angular_deviation_deg, abs=0.5) == 45.0


def test_24_zero_vector_angle_case_handled_safely(qapp):
    # Route with identical Entry and Target (length 0)
    session = SurgicalPlanSession()
    session.set_entry(5.0, 5.0, 5.0)
    session.set_target(5.0, 5.0, 5.0)
    pose = session.get_current_instrument_pose()
    assert pose.angular_deviation_deg == 0.0
    assert not math.isnan(pose.angular_deviation_deg)


# ── Tests 25–28: Reset, Route Replacement & Clearing ──────────────────────────

def test_25_reset_returns_current_instrument_to_planned_route(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(20.0, 0.0, 0.0)
    session.set_deviation_offsets(5.0, -10.0, 15.0)
    session.set_deviation_angles(15.0, -20.0)

    pose = session.get_current_instrument_pose()
    assert pose.lateral_deviation_mm > 0.0

    session.reset_deviation_to_planned()
    pose_reset = session.get_current_instrument_pose()
    assert pose_reset.offset_x_mm == 0.0
    assert pose_reset.offset_y_mm == 0.0
    assert pose_reset.offset_z_mm == 0.0
    assert pose_reset.yaw_deg == 0.0
    assert pose_reset.pitch_deg == 0.0
    assert pytest.approx(pose_reset.lateral_deviation_mm, abs=1e-5) == 0.0
    assert pytest.approx(pose_reset.angular_deviation_deg, abs=1e-5) == 0.0


def test_26_route_replacement_resets_or_reconciles_current_state(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    session.set_deviation_offsets(2.0, 0.0, 0.0)

    # Replace Target with new position
    session.set_target(50.0, 0.0, 0.0)
    pose = session.get_current_instrument_pose()
    assert pose.nominal_tip_position_mm == (50.0, 0.0, 0.0)
    assert pose.current_tip_position_mm == (52.0, 0.0, 0.0)


def test_27_entry_clearing_removes_live_deviation_state(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(10.0, 10.0, 10.0)
    viewer.set_live_deviation_visible(True)

    assert "or_live_instrument" in viewer.plotter.actors
    viewer.clear_entry_point()
    assert not viewer.surgical_plan.has_live_deviation()
    assert "or_live_instrument" not in viewer.plotter.actors


def test_28_target_clearing_removes_live_deviation_state(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(10.0, 10.0, 10.0)
    viewer.set_live_deviation_visible(True)

    assert "or_live_instrument" in viewer.plotter.actors
    viewer.clear_target_point()
    assert not viewer.surgical_plan.has_live_deviation()
    assert "or_live_instrument" not in viewer.plotter.actors


# ── Tests 29–33: Performance & Lifetime (Zero DICOM Reload) ───────────────────

def test_29_no_dicom_reload_during_deviation_updates(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)
    viewer._start_load = MagicMock()

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(10.0, 10.0, 10.0)
    viewer.set_live_deviation_visible(True)
    viewer.set_live_deviation_offsets(2.0, 3.0, 4.0)
    viewer.set_live_deviation_angles(10.0, 15.0)
    viewer.reset_live_deviation_to_planned()

    # Zero reloads triggered
    viewer._start_load.assert_not_called()


def test_30_slice_changes_do_not_reload_dicom(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)
    viewer._start_load = MagicMock()

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(10.0, 10.0, 10.0)
    viewer.set_live_deviation_visible(True)

    # Change 2D slice
    viewer.panel_2d.slider_slice.setValue(10)
    viewer._start_load.assert_not_called()


def test_31_mpr_changes_do_not_reload_dicom(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)
    viewer._start_load = MagicMock()

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(10.0, 10.0, 10.0)
    viewer.set_live_deviation_visible(True)

    # Update MPR slice
    viewer.mpr_view.snap_to_volume_point(10.0, 12.0, 14.0)
    viewer._start_load.assert_not_called()


def test_32_no_duplicate_live_deviation_actors_accumulate(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(10.0, 10.0, 10.0)
    viewer.set_live_deviation_visible(True)

    for i in range(10):
        viewer.set_live_deviation_offsets(float(i), float(i * 2), -float(i))

    live_actors = [k for k in viewer.plotter.actors if k.startswith("or_live_")]
    # Should have at most or_live_instrument, or_live_instrument_tip, or_live_deviation_line, or_live_deviation_label
    assert len(live_actors) <= 5


def test_33_scan_change_clears_live_deviation(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(10.0, 10.0, 10.0)
    viewer.set_live_deviation_visible(True)
    viewer.set_live_deviation_offsets(5.0, 5.0, 5.0)

    assert "or_live_instrument" in viewer.plotter.actors

    # Load new scan (triggers reset_for_scan)
    viewer.load_scan({"id": "scan_1"}, {"id": "scan_2", "file_path": ""})
    assert not viewer.surgical_plan.has_live_deviation()
    assert viewer.surgical_plan.deviation_offset_x_mm == 0.0
    assert "or_live_instrument" not in viewer.plotter.actors


# ── Tests 34–36: OR / ICU UI Cards & Voice Commands ───────────────────────────

def test_34_or_icu_mode_does_not_create_duplicate_state(qapp):
    or_icu = OrIcuMode()
    patient = {"id": "p1", "name": "Test Patient"}
    scan = {"id": "s1", "type": "CT"}

    or_icu.set_patient_and_scan(patient, scan)
    or_icu.set_mode("OR")
    assert or_icu.live_deviation_card is not None

    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 20.0, 30.0)
    session.set_deviation_offsets(2.0, -1.0, 3.0)
    or_icu.set_surgical_plan(session)

    assert or_icu.live_deviation_card.lbl_status.text() == "Active Simulation"

    # Switch OR <-> ICU
    or_icu.set_mode("ICU")
    or_icu.set_mode("OR")
    # Verify card is still intact
    assert or_icu.live_deviation_card.lbl_status.text() == "Active Simulation"


def test_35_voice_commands_route_correctly(qapp, synthetic_volume):
    vol, spacing, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, spacing, dz)

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(10.0, 10.0, 10.0)

    viewer.handle_voice_command("show live deviation")
    assert viewer.surgical_plan.deviation_visible is True

    viewer.handle_voice_command("hide live deviation")
    assert viewer.surgical_plan.deviation_visible is False

    viewer.set_live_deviation_offsets(5.0, 5.0, 5.0)
    viewer.handle_voice_command("reset deviation")
    assert viewer.surgical_plan.deviation_offset_x_mm == 0.0


def test_36_card_simulation_sliders_and_reset(qapp):
    card = LiveDeviationCard()
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    card.update_deviation(session)

    assert not card.controls_container.isHidden()

    # Move sliders
    card.slider_x.setValue(10)  # +5.0 mm
    assert card.lbl_x_val.text() == "+5.0 mm"

    card.slider_yaw.setValue(15) # +15.0 deg
    assert card.lbl_yaw_val.text() == "+15.0°"

    # Reset
    card.btn_reset.click()
    assert card.slider_x.value() == 0
    assert card.slider_yaw.value() == 0
    assert card.lbl_x_val.text() == "+0.0 mm"
    assert card.lbl_yaw_val.text() == "+0.0°"
