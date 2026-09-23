# test_or_virtual_instrument.py
"""
Unit and integration tests for OR Feature 5: VIRTUAL INSTRUMENT.

Covers all required verification scenarios:
 1. No instrument without Entry.
 2. No instrument without Target.
 3. Instrument becomes available with Planned Route.
 4. Instrument uses physical mm coordinates.
 5. Tip at depth 0 equals Entry.
 6. Tip at full route length equals Target.
 7. Intermediate depth produces correct interpolated tip.
 8. Instrument follows route direction.
 9. Instrument depth is clamped to route length.
10. Instrument diameter is stored correctly.
11. Changing depth updates tip.
12. Changing diameter updates geometry.
13. Replacing Entry updates instrument.
14. Replacing Target updates instrument.
15. Clearing Entry removes/hides instrument.
16. Clearing Target removes/hides instrument.
17. Instrument actor is distinct from Planned Route actor.
18. Instrument actor is distinct from Surgical Corridor actor.
19. Instrument actor is distinct from Entry/Target actors.
20. Instrument actor is distinct from Avoid Structure actors.
21. Instrument is not added to _measurements_3d.
22. Instrument does not create caliper measurements.
23. Structure distance calculation is geometrically correct.
24. Instrument updates without DICOM reload.
25. Slice changes do not reload DICOM.
26. MPR changes do not reload DICOM.
27. No duplicate instrument actors accumulate.
28. Scan change clears instrument.
29. OR <-> ICU does not create duplicate instrument state.
30. Voice commands route correctly to virtual instrument.
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
from PyQt6.QtWidgets import QApplication, QLabel

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from surgical_plan import (
    PlanningPoint,
    PlannedRoute,
    AvoidStructure,
    SurgicalCorridor,
    VirtualInstrument,
    SurgicalPlanSession,
)
from screens.or_icu_mode import OrIcuMode, VirtualInstrumentCard
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


# ── Tests 1–3: Instrument Availability ─────────────────────────────────────────

def test_1_no_instrument_without_entry(qapp):
    session = SurgicalPlanSession()
    session.set_target(10.0, 20.0, 30.0)
    assert not session.has_virtual_instrument()
    assert session.get_virtual_instrument() is None


def test_2_no_instrument_without_target(qapp):
    session = SurgicalPlanSession()
    session.set_entry(5.0, 10.0, 15.0)
    assert not session.has_virtual_instrument()
    assert session.get_virtual_instrument() is None


def test_3_instrument_becomes_available_with_planned_route(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    assert session.has_planned_route()
    assert session.has_virtual_instrument()
    inst = session.get_virtual_instrument()
    assert inst is not None
    assert isinstance(inst, VirtualInstrument)


# ── Tests 4–8: Geometry, Tip Interpolation & Direction ─────────────────────────

def test_4_instrument_uses_physical_mm_coordinates(qapp):
    session = SurgicalPlanSession()
    session.set_entry(12.5, 24.8, 36.2)
    session.set_target(42.5, 64.8, 76.2)
    inst = session.get_virtual_instrument()
    assert inst.route.entry_point.coordinates == (12.5, 24.8, 36.2)
    assert inst.route.target_point.coordinates == (42.5, 64.8, 76.2)


def test_5_tip_at_depth_0_equals_entry(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 30.0)
    session.set_target(10.0, 20.0, 80.0)
    session.set_insertion_depth(0.0)
    inst = session.get_virtual_instrument()
    assert inst.fraction_t == pytest.approx(0.0)
    assert inst.tip_position_mm == pytest.approx((10.0, 20.0, 30.0))


def test_6_tip_at_full_route_length_equals_target(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 30.0)
    session.set_target(10.0, 20.0, 80.0)
    route_len = session.get_planned_route().length_mm
    assert route_len == pytest.approx(50.0)
    session.set_insertion_depth(route_len)
    inst = session.get_virtual_instrument()
    assert inst.fraction_t == pytest.approx(1.0)
    assert inst.tip_position_mm == pytest.approx((10.0, 20.0, 80.0))


def test_7_intermediate_depth_produces_correct_interpolated_tip(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(100.0, 0.0, 0.0)
    session.set_insertion_depth(25.0)
    inst = session.get_virtual_instrument()
    assert inst.fraction_t == pytest.approx(0.25)
    assert inst.tip_position_mm == pytest.approx((25.0, 0.0, 0.0))

    session.set_insertion_depth(75.0)
    inst = session.get_virtual_instrument()
    assert inst.fraction_t == pytest.approx(0.75)
    assert inst.tip_position_mm == pytest.approx((75.0, 0.0, 0.0))


def test_8_instrument_follows_route_direction(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 30.0)
    session.set_target(10.0, 20.0, 90.0)
    inst = session.get_virtual_instrument()
    # Route is along +Z axis
    assert inst.direction_vector == pytest.approx((0.0, 0.0, 1.0))

    session.set_target(10.0, 80.0, 30.0)
    inst = session.get_virtual_instrument()
    # Route is along +Y axis
    assert inst.direction_vector == pytest.approx((0.0, 1.0, 0.0))


# ── Tests 9–12: Depth Clamping, Diameter & Updates ─────────────────────────────

def test_9_instrument_depth_is_clamped_to_route_length(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(50.0, 0.0, 0.0)

    # Negative depth clamped to 0.0
    session.set_insertion_depth(-10.0)
    assert session.instrument_depth_mm == 0.0
    assert session.get_virtual_instrument().insertion_depth_mm == 0.0

    # Depth beyond target clamped to route length (50.0)
    session.set_insertion_depth(100.0)
    assert session.instrument_depth_mm == 50.0
    assert session.get_virtual_instrument().insertion_depth_mm == 50.0
    assert session.get_virtual_instrument().tip_position_mm == pytest.approx((50.0, 0.0, 0.0))


def test_10_instrument_diameter_is_stored_correctly(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(50.0, 0.0, 0.0)
    assert session.instrument_diameter_mm == 2.0  # default

    session.set_instrument_diameter(4.5)
    assert session.instrument_diameter_mm == 4.5
    inst = session.get_virtual_instrument()
    assert inst.diameter_mm == 4.5


def test_11_changing_depth_updates_tip(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(0.0, 60.0, 0.0)

    session.set_insertion_depth(20.0)
    inst1 = session.get_virtual_instrument()
    assert inst1.tip_position_mm == pytest.approx((0.0, 20.0, 0.0))

    session.set_insertion_depth(40.0)
    inst2 = session.get_virtual_instrument()
    assert inst2.tip_position_mm == pytest.approx((0.0, 40.0, 0.0))


def test_12_changing_diameter_updates_geometry(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    session.set_instrument_diameter(3.0)
    inst = session.get_virtual_instrument()
    assert inst.diameter_mm == 3.0


# ── Tests 13–16: Replacing and Clearing Landmarks ──────────────────────────────

def test_13_replacing_entry_updates_instrument(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(100.0, 0.0, 0.0)
    session.set_insertion_depth(50.0)
    assert session.get_virtual_instrument().tip_position_mm == pytest.approx((50.0, 0.0, 0.0))

    # Replace Entry
    session.set_entry(50.0, 0.0, 0.0)
    # New route length is 50.0 mm; existing depth 50.0 is clamped to 50.0 mm
    inst = session.get_virtual_instrument()
    assert inst.route.length_mm == pytest.approx(50.0)
    assert inst.tip_position_mm == pytest.approx((100.0, 0.0, 0.0))


def test_14_replacing_target_updates_instrument(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(100.0, 0.0, 0.0)
    session.set_insertion_depth(50.0)

    # Replace Target with shorter route
    session.set_target(40.0, 0.0, 0.0)
    inst = session.get_virtual_instrument()
    # Route length is now 40.0; depth is clamped to 40.0
    assert inst.insertion_depth_mm == pytest.approx(40.0)
    assert inst.tip_position_mm == pytest.approx((40.0, 0.0, 0.0))


def test_15_clearing_entry_removes_instrument(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    assert session.has_virtual_instrument()

    session.clear_entry()
    assert not session.has_virtual_instrument()
    assert session.get_virtual_instrument() is None


def test_16_clearing_target_removes_instrument(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    assert session.has_virtual_instrument()

    session.clear_target()
    assert not session.has_virtual_instrument()
    assert session.get_virtual_instrument() is None


# ── Tests 17–22: Actor Distinction & Measurement Isolation ─────────────────────

def test_17_instrument_actor_distinct_from_planned_route_actor(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 5.0, 5.0)
    viewer.set_target_point(20.0, 20.0, 20.0)
    viewer.set_virtual_instrument_depth(10.0)
    viewer.set_virtual_instrument_visible(True)

    actors = list(viewer.plotter.actors.keys())
    assert "or_virtual_instrument" in actors
    assert "or_planned_route" in actors
    assert "or_virtual_instrument" != "or_planned_route"


def test_18_instrument_actor_distinct_from_surgical_corridor_actor(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 5.0, 5.0)
    viewer.set_target_point(20.0, 20.0, 20.0)
    viewer.set_surgical_corridor_enabled(True)
    viewer.set_virtual_instrument_depth(10.0)
    viewer.set_virtual_instrument_visible(True)

    actors = list(viewer.plotter.actors.keys())
    assert "or_virtual_instrument" in actors
    assert "or_surgical_corridor" in actors
    assert "or_virtual_instrument" != "or_surgical_corridor"


def test_19_instrument_actor_distinct_from_entry_target_actors(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 5.0, 5.0)
    viewer.set_target_point(20.0, 20.0, 20.0)
    viewer.set_virtual_instrument_depth(10.0)
    viewer.set_virtual_instrument_visible(True)

    actors = list(viewer.plotter.actors.keys())
    assert "or_virtual_instrument" in actors
    assert "or_virtual_instrument_tip" in actors
    assert "or_entry_marker" in actors
    assert "or_target_marker" in actors
    assert "or_virtual_instrument" not in ("or_entry_marker", "or_target_marker")


def test_20_instrument_actor_distinct_from_avoid_structure_actors(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 5.0, 5.0)
    viewer.set_target_point(20.0, 20.0, 20.0)
    struct = viewer.add_avoid_structure(10.0, 10.0, 10.0, radius_mm=4.0)
    viewer.set_virtual_instrument_depth(10.0)
    viewer.set_virtual_instrument_visible(True)

    actors = list(viewer.plotter.actors.keys())
    assert "or_virtual_instrument" in actors
    assert f"or_avoid_structure_{struct.structure_id}" in actors


def test_21_instrument_not_added_to_measurements_3d(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 5.0, 5.0)
    viewer.set_target_point(20.0, 20.0, 20.0)
    viewer.set_virtual_instrument_depth(10.0)
    viewer.set_virtual_instrument_visible(True)

    assert not any("instrument" in str(m).lower() for m in viewer._measurements_3d)
    assert "or_virtual_instrument" not in viewer._measurement_actor_names
    assert "or_virtual_instrument_tip" not in viewer._measurement_actor_names


def test_22_instrument_does_not_create_caliper_measurements(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    initial_calipers = len(viewer._measurements_3d)

    viewer.set_entry_point(5.0, 5.0, 5.0)
    viewer.set_target_point(20.0, 20.0, 20.0)
    viewer.set_virtual_instrument_depth(5.0)
    viewer.set_virtual_instrument_depth(15.0)
    viewer.set_virtual_instrument_visible(True)
    viewer.set_virtual_instrument_visible(False)

    assert len(viewer._measurements_3d) == initial_calipers


# ── Tests 23–27: Geometry, Zero Reload & Duplicate Prevention ─────────────────

def test_23_structure_distance_calculation_is_geometrically_correct(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(100.0, 0.0, 0.0)
    struct = session.add_avoid_structure(50.0, 10.0, 0.0, radius_mm=5.0)

    # When depth = 0.0 (segment is [0, 0, 0] to [0, 0, 0]), distance to (50, 10, 0) is hypot(50, 10)
    session.set_insertion_depth(0.0)
    inst = session.get_virtual_instrument()
    assert inst.distance_to_structure(struct) == pytest.approx(math.sqrt(50**2 + 10**2))

    # When depth = 50.0 (segment is [0, 0, 0] to [50, 0, 0]), distance to (50, 10, 0) is 10.0
    session.set_insertion_depth(50.0)
    inst = session.get_virtual_instrument()
    assert inst.distance_to_structure(struct) == pytest.approx(10.0)

    # When depth = 100.0 (segment is [0, 0, 0] to [100, 0, 0]), closest point is (50, 0, 0), distance is 10.0
    session.set_insertion_depth(100.0)
    inst = session.get_virtual_instrument()
    assert inst.distance_to_structure(struct) == pytest.approx(10.0)


def test_24_instrument_updates_without_dicom_reload(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer._start_load = MagicMock()

    viewer.set_entry_point(5.0, 5.0, 5.0)
    viewer.set_target_point(20.0, 20.0, 20.0)
    viewer.set_virtual_instrument_depth(12.0)
    viewer.set_virtual_instrument_diameter(3.5)
    viewer.set_virtual_instrument_visible(True)

    viewer._start_load.assert_not_called()


def test_25_slice_changes_do_not_reload_dicom(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer._start_load = MagicMock()

    viewer.panel_2d.slider_slice.setValue(10)
    viewer.panel_2d.slider_slice.setValue(15)

    viewer._start_load.assert_not_called()


def test_26_mpr_changes_do_not_reload_dicom(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer._start_load = MagicMock()

    viewer.mpr_view.snap_to_volume_point(10.0, 12.0, 14.0)
    viewer._start_load.assert_not_called()


def test_27_no_duplicate_instrument_actors_accumulate(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 5.0, 5.0)
    viewer.set_target_point(20.0, 20.0, 20.0)

    for depth in [2.0, 5.0, 10.0, 15.0, 20.0]:
        viewer.set_virtual_instrument_depth(depth)
        viewer.set_virtual_instrument_visible(True)

    instrument_actors = [a for a in viewer.plotter.actors.keys() if a == "or_virtual_instrument"]
    assert len(instrument_actors) == 1


# ── Tests 28–30: Scan Reset, OR <-> ICU & Voice Commands ───────────────────────

def test_28_scan_change_clears_instrument(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 5.0, 5.0)
    viewer.set_target_point(20.0, 20.0, 20.0)
    viewer.set_virtual_instrument_depth(10.0)
    viewer.set_virtual_instrument_visible(True)

    assert viewer.surgical_plan.has_virtual_instrument()
    assert "or_virtual_instrument" in viewer.plotter.actors

    # Switch scan
    viewer.load_scan({"name": "Patient B"}, {"file_path": "/path/to/scan_b", "slice_count": 50})
    assert not viewer.surgical_plan.has_virtual_instrument()
    assert "or_virtual_instrument" not in viewer.plotter.actors
    assert "or_virtual_instrument_tip" not in viewer.plotter.actors


def test_29_or_icu_does_not_create_duplicate_state(qapp):
    session = SurgicalPlanSession()
    session.set_entry(5.0, 10.0, 15.0)
    session.set_target(25.0, 30.0, 35.0)
    session.set_insertion_depth(12.0)
    session.set_instrument_visible(True)

    card = VirtualInstrumentCard()
    card.update_instrument(session)
    assert card.lbl_status.text() == "Configured"
    assert "12.0 /" in card.lbl_depth_val.text()

    or_icu = OrIcuMode()
    or_icu.set_patient_and_scan({"name": "Test"}, {"type": "CT"})
    or_icu.set_surgical_plan(session)
    or_icu.set_mode("OR")
    assert or_icu.virtual_instrument_card is not None
    assert or_icu.virtual_instrument_card.lbl_status.text() == "Configured"

    # Switch OR -> ICU -> OR
    or_icu.set_mode("ICU")
    or_icu.set_mode("OR")
    assert or_icu.virtual_instrument_card.lbl_status.text() == "Configured"
    assert session.instrument_depth_mm == 12.0
    assert session.instrument_visible is True



def test_30_voice_commands_route_correctly(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(50.0, 0.0, 0.0)

    viewer.handle_voice_command("show virtual instrument")
    assert viewer.surgical_plan.instrument_visible is True

    viewer.handle_voice_command("increase instrument depth")
    assert viewer.surgical_plan.instrument_depth_mm == 5.0

    viewer.handle_voice_command("decrease instrument depth")
    assert viewer.surgical_plan.instrument_depth_mm == 0.0

    viewer.handle_voice_command("hide virtual instrument")
    assert viewer.surgical_plan.instrument_visible is False
