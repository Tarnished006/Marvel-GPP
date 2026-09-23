# test_or_surgical_corridor.py
"""
Unit and integration tests for OR Feature 4: SURGICAL CORRIDOR.

Covers all required verification scenarios:
 1. No corridor without Entry.
 2. No corridor without Target.
 3. Corridor exists when Planned Route exists.
 4. Corridor uses physical mm coordinates.
 5. Corridor follows Entry → Target.
 6. Corridor length matches Planned Route length.
 7. Corridor radius is stored correctly.
 8. Changing radius changes corridor geometry.
 9. Replacing Entry updates corridor.
10. Replacing Target updates corridor.
11. Clearing Entry removes corridor.
12. Clearing Target removes corridor.
13. Clearing Planned Route removes corridor.
14. Corridor actor is distinct from Planned Route actor.
15. Corridor actor is distinct from Entry/Target actors.
16. Corridor actor is distinct from avoid-structure actors.
17. Corridor is not added to _measurements_3d.
18. Corridor does not create a caliper measurement.
19. Existing structures remain independent.
20. Structure/corridor geometric relationship is mathematically correct.
21. Corridor updates without DICOM reload.
22. Slice changes do not reload DICOM.
23. MPR updates without DICOM reload.
24. No duplicate corridor actors accumulate after repeated updates.
25. Scan change clears corridor.
26. OR <-> ICU does not create duplicate state.
27. Voice commands route correctly to surgical corridor.
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

from surgical_plan import PlanningPoint, PlannedRoute, AvoidStructure, SurgicalCorridor, SurgicalPlanSession
from screens.or_icu_mode import OrIcuMode, SurgicalCorridorCard
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


# ── Tests 1–3: Corridor Availability ─────────────────────────────────────────

def test_1_no_corridor_without_entry(qapp):
    session = SurgicalPlanSession()
    session.set_target(10.0, 20.0, 30.0)
    assert not session.has_surgical_corridor()
    assert session.get_surgical_corridor() is None


def test_2_no_corridor_without_target(qapp):
    session = SurgicalPlanSession()
    session.set_entry(5.0, 10.0, 15.0)
    assert not session.has_surgical_corridor()
    assert session.get_surgical_corridor() is None


def test_3_corridor_exists_when_planned_route_exists(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    assert session.has_planned_route()
    assert session.has_surgical_corridor()
    corridor = session.get_surgical_corridor()
    assert corridor is not None
    assert isinstance(corridor, SurgicalCorridor)


# ── Tests 4–7: Physical Coordinates, Length & Radius ─────────────────────────

def test_4_corridor_uses_physical_mm_coordinates(qapp):
    session = SurgicalPlanSession()
    session.set_entry(12.5, 24.8, 36.2)
    session.set_target(42.5, 64.8, 76.2)
    corridor = session.get_surgical_corridor()
    assert corridor.route.entry_point.x_mm == 12.5
    assert corridor.route.entry_point.y_mm == 24.8
    assert corridor.route.entry_point.z_mm == 36.2
    assert corridor.route.target_point.x_mm == 42.5
    assert corridor.route.target_point.y_mm == 64.8
    assert corridor.route.target_point.z_mm == 76.2


def test_5_corridor_follows_entry_to_target(qapp):
    session = SurgicalPlanSession()
    e = session.set_entry(10.0, 20.0, 30.0)
    t = session.set_target(50.0, 60.0, 70.0)
    corridor = session.get_surgical_corridor()
    assert corridor.route.entry_point == e
    assert corridor.route.target_point == t


def test_6_corridor_length_matches_planned_route_length(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(3.0, 4.0, 12.0)  # sqrt(3^2 + 4^2 + 12^2) = sqrt(9+16+144) = sqrt(169) = 13.0
    corridor = session.get_surgical_corridor()
    route = session.get_planned_route()
    assert math.isclose(corridor.length_mm, 13.0, rel_tol=1e-5)
    assert math.isclose(corridor.length_mm, route.length_mm, rel_tol=1e-5)


def test_7_corridor_radius_is_stored_correctly(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    assert session.corridor_radius_mm == 5.0  # default
    session.set_corridor_radius(7.5)
    assert session.corridor_radius_mm == 7.5
    corridor = session.get_surgical_corridor()
    assert corridor.radius_mm == 7.5


# ── Tests 8–10: Radius & Endpoint Mutations ──────────────────────────────────

def test_8_changing_radius_changes_corridor_geometry(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    session.set_corridor_radius(4.0)
    c1 = session.get_surgical_corridor()
    vol1 = c1.volume_mm3  # pi * 4^2 * 10 = 160*pi

    session.set_corridor_radius(8.0)
    c2 = session.get_surgical_corridor()
    vol2 = c2.volume_mm3  # pi * 8^2 * 10 = 640*pi
    assert math.isclose(vol2, 4.0 * vol1, rel_tol=1e-5)


def test_9_replacing_entry_updates_corridor(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    assert math.isclose(session.get_surgical_corridor().length_mm, 10.0)

    session.set_entry(5.0, 0.0, 0.0)
    assert math.isclose(session.get_surgical_corridor().length_mm, 5.0)


def test_10_replacing_target_updates_corridor(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    assert math.isclose(session.get_surgical_corridor().length_mm, 10.0)

    session.set_target(20.0, 0.0, 0.0)
    assert math.isclose(session.get_surgical_corridor().length_mm, 20.0)


# ── Tests 11–13: Clearing Landmarks Removes Corridor ─────────────────────────

def test_11_clearing_entry_removes_corridor(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    assert session.has_surgical_corridor()
    session.clear_entry()
    assert not session.has_surgical_corridor()
    assert session.get_surgical_corridor() is None


def test_12_clearing_target_removes_corridor(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    assert session.has_surgical_corridor()
    session.clear_target()
    assert not session.has_surgical_corridor()
    assert session.get_surgical_corridor() is None


def test_13_clearing_planned_route_removes_corridor(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(10.0, 0.0, 0.0)
    assert session.has_surgical_corridor()
    session.clear_both()
    assert not session.has_surgical_corridor()
    assert session.get_surgical_corridor() is None


# ── Tests 14–16: Actor Names & Distinction ───────────────────────────────────

def test_14_corridor_actor_distinct_from_planned_route_actor(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 10.0, 15.0)
    viewer.set_target_point(20.0, 25.0, 30.0)

    assert "or_surgical_corridor" in viewer.plotter.actors
    assert "or_planned_route" in viewer.plotter.actors
    assert "or_surgical_corridor" != "or_planned_route"


def test_15_corridor_actor_distinct_from_entry_target_actors(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 10.0, 15.0)
    viewer.set_target_point(20.0, 25.0, 30.0)

    assert "or_entry_marker" in viewer.plotter.actors
    assert "or_target_marker" in viewer.plotter.actors
    assert "or_surgical_corridor" not in ("or_entry_marker", "or_target_marker")


def test_16_corridor_actor_distinct_from_avoid_structure_actors(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 10.0, 15.0)
    viewer.set_target_point(20.0, 25.0, 30.0)
    struct = viewer.add_avoid_structure(10.0, 15.0, 20.0, name="Structure 1")

    struct_actor = f"or_avoid_structure_{struct.structure_id}"
    assert struct_actor in viewer.plotter.actors
    assert "or_surgical_corridor" in viewer.plotter.actors
    assert "or_surgical_corridor" != struct_actor


# ── Tests 17–18: Measurement Isolation ───────────────────────────────────────

def test_17_corridor_is_not_added_to_measurements_3d(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 10.0, 15.0)
    viewer.set_target_point(20.0, 25.0, 30.0)

    assert "or_surgical_corridor" not in viewer._measurements_3d
    assert "or_surgical_corridor" not in viewer._measurement_actor_names


def test_18_corridor_does_not_create_caliper_measurement(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    initial_measurements = len(viewer._measurements_3d)

    viewer.set_entry_point(5.0, 10.0, 15.0)
    viewer.set_target_point(20.0, 25.0, 30.0)
    viewer.set_surgical_corridor_radius(8.0)

    assert len(viewer._measurements_3d) == initial_measurements


# ── Tests 19–20: Structures Independence & Geometric Relationship ─────────────

def test_19_existing_structures_remain_independent(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    struct = viewer.add_avoid_structure(12.0, 14.0, 16.0, name="Structure Alpha")
    assert viewer.surgical_plan.has_avoid_structures()

    viewer.set_entry_point(5.0, 10.0, 15.0)
    viewer.set_target_point(20.0, 25.0, 30.0)
    assert viewer.surgical_plan.has_avoid_structures()
    assert viewer.surgical_plan.get_avoid_structure(struct.structure_id) is not None

    viewer.clear_planned_route()
    assert viewer.surgical_plan.has_avoid_structures()


def test_20_structure_corridor_geometric_relationship(qapp):
    session = SurgicalPlanSession()
    session.set_entry(0.0, 0.0, 0.0)
    session.set_target(100.0, 0.0, 0.0)  # Along X axis
    session.set_corridor_radius(10.0)

    # Structure 1: center at (50.0, 6.0, 0.0) -> distance is 6.0 mm (<= 10.0 mm -> within corridor)
    s1 = session.add_avoid_structure(50.0, 6.0, 0.0, name="Structure 1")
    # Structure 2: center at (50.0, 15.0, 0.0) -> distance is 15.0 mm (> 10.0 mm -> outside corridor)
    s2 = session.add_avoid_structure(50.0, 15.0, 0.0, name="Structure 2")

    route = session.get_planned_route()
    corridor = session.get_surgical_corridor()

    d1 = route.distance_to_structure(s1)
    d2 = route.distance_to_structure(s2)

    assert math.isclose(d1, 6.0, rel_tol=1e-5)
    assert math.isclose(d2, 15.0, rel_tol=1e-5)

    assert d1 <= corridor.radius_mm
    assert d2 > corridor.radius_mm


# ── Tests 21–23: Zero Reload on Corridor & Slice Updates ──────────────────────

def test_21_corridor_updates_without_dicom_reload(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 10.0, 15.0)
    viewer.set_target_point(20.0, 25.0, 30.0)

    viewer._start_load = MagicMock()
    viewer.set_surgical_corridor_radius(10.0)
    viewer.set_surgical_corridor_enabled(False)
    viewer.set_surgical_corridor_enabled(True)

    viewer._start_load.assert_not_called()


def test_22_slice_changes_do_not_reload_dicom(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 10.0, 15.0)
    viewer.set_target_point(20.0, 25.0, 30.0)

    viewer._start_load = MagicMock()
    viewer.panel_2d.slider_slice.setValue(10)
    viewer._start_load.assert_not_called()


def test_23_mpr_updates_without_dicom_reload(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 10.0, 15.0)
    viewer.set_target_point(20.0, 25.0, 30.0)

    viewer._start_load = MagicMock()
    viewer.mpr_view.snap_to_volume_point(10.0, 12.0, 14.0)
    viewer._start_load.assert_not_called()



# ── Tests 24–27: Actor Cleanup, Scan Reset, UI & Voice ─────────────────────────

def test_24_no_duplicate_corridor_actors_accumulate(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 10.0, 15.0)
    viewer.set_target_point(20.0, 25.0, 30.0)

    for r in (6.0, 7.0, 8.0, 9.0, 10.0):
        viewer.set_surgical_corridor_radius(r)

    corridor_actors = [k for k in viewer.plotter.actors.keys() if k == "or_surgical_corridor"]
    assert len(corridor_actors) == 1


def test_25_scan_change_clears_corridor(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 10.0, 15.0)
    viewer.set_target_point(20.0, 25.0, 30.0)
    assert "or_surgical_corridor" in viewer.plotter.actors

    new_patient = {"name": "Test Patient", "mrn": "MRN_NEW"}
    new_scan = {"id": "scan_999", "file_path": "/fake/new/path", "slice_count": 1}
    viewer.load_scan(new_patient, new_scan)

    assert "or_surgical_corridor" not in viewer.plotter.actors
    assert not viewer.surgical_plan.has_surgical_corridor()


def test_26_or_icu_state_persistence(qapp):
    session = SurgicalPlanSession()
    session.set_entry(10.0, 20.0, 30.0)
    session.set_target(40.0, 50.0, 60.0)
    session.set_corridor_radius(8.0)

    card = SurgicalCorridorCard()
    card.update_corridor(session)
    assert card.lbl_status.text() == "Configured"
    assert "8.0 mm" in card.lbl_metrics.text()

    # Add structure within corridor
    s = session.add_avoid_structure(25.0, 35.0, 45.0, name="Structure Inside")
    card.update_corridor(session)
    labels = [lbl.text() for lbl in card.findChildren(QLabel)]
    assert any("Within Corridor Geometry" in txt for txt in labels)


def test_27_voice_commands_surgical_corridor(qapp, synthetic_volume):
    vol, sp, dz = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, dz)
    viewer.set_entry_point(5.0, 10.0, 15.0)
    viewer.set_target_point(20.0, 25.0, 30.0)

    # Hide corridor via voice
    viewer.handle_voice_command("hide surgical corridor")
    assert not viewer.surgical_plan.corridor_enabled
    assert "or_surgical_corridor" not in viewer.plotter.actors

    # Show corridor via voice
    viewer.handle_voice_command("show surgical corridor")
    assert viewer.surgical_plan.corridor_enabled
    assert "or_surgical_corridor" in viewer.plotter.actors

    # Increase radius via voice
    initial_r = viewer.surgical_plan.corridor_radius_mm
    viewer.handle_voice_command("increase corridor radius")
    assert math.isclose(viewer.surgical_plan.corridor_radius_mm, initial_r + 1.0)

    # Decrease radius via voice
    viewer.handle_voice_command("decrease corridor radius")
    assert math.isclose(viewer.surgical_plan.corridor_radius_mm, initial_r)
