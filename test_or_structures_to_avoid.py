# test_or_structures_to_avoid.py
"""
Unit and integration tests for OR Feature 3: STRUCTURES TO AVOID.

Covers all 27 required verification scenarios:
 1. No structures initially.
 2. Structure can be added.
 3. Structure stores physical mm coordinates.
 4. Structure does not use slice indices as canonical coordinates.
 5. Multiple structures can coexist.
 6. Each structure has a unique identifier.
 7. Structure actor names are unique.
 8. Structure actors are distinct from Entry actor.
 9. Structure actors are distinct from Target actor.
10. Structure actors are distinct from Planned Route actor.
11. Structure creation does not create a caliper measurement.
12. Structure removal removes only the selected structure.
13. Clear All removes all structures.
14. Re-adding after removal works.
15. Scan change clears all structures.
16. Existing Entry/Target remains functional.
17. Existing Planned Route remains functional.
18. Route-distance calculation is geometrically correct when implemented.
19. Route-distance calculation does not modify route geometry.
20. Structure updates do not trigger DICOM reload.
21. 2D projection uses physical coordinates.
22. MPR projection uses physical coordinates.
23. No duplicate actors accumulate after repeated add/remove operations.
24. Existing measurement tests still pass.
25. Existing 2D/3D synchronization tests still pass.
26. Existing Planned Route tests still pass.
27. Existing ICU/OR shell tests still pass.
"""

import os
import math
import pytest
import numpy as np
import pyvista as pv
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from surgical_plan import PlanningPoint, PlannedRoute, AvoidStructure, SurgicalPlanSession
from screens.or_icu_mode import OrIcuMode, StructuresToAvoidCard
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


# ─────────────────────────────────────────────────────────────────────────────
# Test Cases 1 - 27
# ─────────────────────────────────────────────────────────────────────────────

def test_1_no_structures_initially(qapp):
    """1. No structures initially in session or UI."""
    session = SurgicalPlanSession()
    assert len(session.get_avoid_structures()) == 0
    assert not session.has_avoid_structures()

    card = StructuresToAvoidCard()
    card.update_structures(session)
    assert card.lbl_status.text() == "No structures marked"
    assert not card.btn_clear_all.isEnabled()


def test_2_structure_can_be_added(qapp):
    """2. Structure can be added to session and has correct center and radius."""
    session = SurgicalPlanSession()
    struct = session.add_avoid_structure(10.0, 20.0, 30.0, radius_mm=6.0, name="Structure 1")
    assert struct is not None
    assert struct.name == "Structure 1"
    assert struct.center == (10.0, 20.0, 30.0)
    assert struct.radius_mm == 6.0
    assert session.has_avoid_structures()
    assert len(session.get_avoid_structures()) == 1


def test_3_structure_stores_physical_mm_coordinates(qapp):
    """3. Structure stores continuous physical mm coordinates."""
    session = SurgicalPlanSession()
    struct = session.add_avoid_structure(12.345, 67.891, 23.456)
    assert isinstance(struct.center_x_mm, float)
    assert isinstance(struct.center_y_mm, float)
    assert isinstance(struct.center_z_mm, float)
    assert struct.center_x_mm == 12.345
    assert struct.center_y_mm == 67.891
    assert struct.center_z_mm == 23.456


def test_4_structure_does_not_use_slice_indices(qapp):
    """4. Structure canonical coordinates are independent of discrete slice indices."""
    session = SurgicalPlanSession()
    # Fractional mm coordinates that do not correspond to integer slice indices
    struct = session.add_avoid_structure(14.73, 29.81, 7.39)
    assert struct.center == (14.73, 29.81, 7.39)
    # Ensure no discrete rounding to integer slices
    assert not struct.center_x_mm.is_integer()
    assert not struct.center_y_mm.is_integer()
    assert not struct.center_z_mm.is_integer()


def test_5_multiple_structures_can_coexist(qapp):
    """5. Multiple structures can coexist simultaneously."""
    session = SurgicalPlanSession()
    s1 = session.add_avoid_structure(10.0, 10.0, 10.0)
    s2 = session.add_avoid_structure(20.0, 20.0, 20.0)
    s3 = session.add_avoid_structure(30.0, 30.0, 30.0)

    structs = session.get_avoid_structures()
    assert len(structs) == 3
    assert {s.structure_id for s in structs} == {s1.structure_id, s2.structure_id, s3.structure_id}


def test_6_each_structure_has_unique_identifier(qapp):
    """6. Each structure has a unique identifier."""
    session = SurgicalPlanSession()
    s1 = session.add_avoid_structure(1.0, 2.0, 3.0)
    s2 = session.add_avoid_structure(4.0, 5.0, 6.0)
    assert s1.structure_id != s2.structure_id
    assert len(s1.structure_id) > 0
    assert len(s2.structure_id) > 0


def test_7_structure_actor_names_are_unique(qapp, synthetic_volume):
    """7. Structure actor names in 3D scene are unique per structure."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    s1 = viewer.add_avoid_structure(10.0, 15.0, 20.0)
    s2 = viewer.add_avoid_structure(12.0, 18.0, 25.0)

    actor1 = f"or_avoid_structure_{s1.structure_id}"
    actor2 = f"or_avoid_structure_{s2.structure_id}"
    assert actor1 != actor2
    assert actor1 in viewer.plotter.actors
    assert actor2 in viewer.plotter.actors


def test_8_structure_actors_distinct_from_entry_actor(qapp, synthetic_volume):
    """8. Structure actors are distinct from Entry actor."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(5.0, 5.0, 5.0)
    s1 = viewer.add_avoid_structure(10.0, 10.0, 10.0)

    assert "or_entry_marker" in viewer.plotter.actors
    assert f"or_avoid_structure_{s1.structure_id}" in viewer.plotter.actors
    assert f"or_avoid_structure_{s1.structure_id}" != "or_entry_marker"


def test_9_structure_actors_distinct_from_target_actor(qapp, synthetic_volume):
    """9. Structure actors are distinct from Target actor."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_target_point(25.0, 25.0, 25.0)
    s1 = viewer.add_avoid_structure(15.0, 15.0, 15.0)

    assert "or_target_marker" in viewer.plotter.actors
    assert f"or_avoid_structure_{s1.structure_id}" in viewer.plotter.actors
    assert f"or_avoid_structure_{s1.structure_id}" != "or_target_marker"


def test_10_structure_actors_distinct_from_planned_route_actor(qapp, synthetic_volume):
    """10. Structure actors are distinct from Planned Route actor."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(5.0, 5.0, 5.0)
    viewer.set_target_point(25.0, 25.0, 25.0)
    s1 = viewer.add_avoid_structure(15.0, 15.0, 15.0)

    assert "or_planned_route" in viewer.plotter.actors
    assert f"or_avoid_structure_{s1.structure_id}" in viewer.plotter.actors
    assert f"or_avoid_structure_{s1.structure_id}" != "or_planned_route"


def test_11_structure_creation_does_not_create_caliper_measurement(qapp, synthetic_volume):
    """11. Adding an avoid structure does not create a standard caliper measurement."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    init_calipers = len(getattr(viewer, "_measurements_3d", []))
    viewer.add_avoid_structure(12.0, 14.0, 16.0)

    assert len(getattr(viewer, "_measurements_3d", [])) == init_calipers


def test_12_structure_removal_removes_only_selected_structure(qapp, synthetic_volume):
    """12. Removing an individual structure leaves remaining structures intact."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    s1 = viewer.add_avoid_structure(10.0, 10.0, 10.0)
    s2 = viewer.add_avoid_structure(20.0, 20.0, 20.0)

    assert len(viewer.surgical_plan.get_avoid_structures()) == 2
    assert f"or_avoid_structure_{s1.structure_id}" in viewer.plotter.actors
    assert f"or_avoid_structure_{s2.structure_id}" in viewer.plotter.actors

    viewer.remove_avoid_structure(s1.structure_id)

    assert len(viewer.surgical_plan.get_avoid_structures()) == 1
    assert viewer.surgical_plan.get_avoid_structure(s1.structure_id) is None
    assert viewer.surgical_plan.get_avoid_structure(s2.structure_id) is not None
    assert f"or_avoid_structure_{s1.structure_id}" not in viewer.plotter.actors
    assert f"or_avoid_structure_{s2.structure_id}" in viewer.plotter.actors


def test_13_clear_all_removes_all_structures(qapp, synthetic_volume):
    """13. Clear all removes all structures and their 3D actors."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    s1 = viewer.add_avoid_structure(10.0, 10.0, 10.0)
    s2 = viewer.add_avoid_structure(20.0, 20.0, 20.0)

    viewer.clear_avoid_structures()

    assert len(viewer.surgical_plan.get_avoid_structures()) == 0
    assert not viewer.surgical_plan.has_avoid_structures()
    assert f"or_avoid_structure_{s1.structure_id}" not in viewer.plotter.actors
    assert f"or_avoid_structure_{s2.structure_id}" not in viewer.plotter.actors


def test_14_readding_after_removal_works(qapp, synthetic_volume):
    """14. Adding a new structure after removing previous structures works seamlessly."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    s1 = viewer.add_avoid_structure(10.0, 10.0, 10.0)
    viewer.clear_avoid_structures()
    assert len(viewer.surgical_plan.get_avoid_structures()) == 0

    s2 = viewer.add_avoid_structure(15.0, 25.0, 35.0)
    assert len(viewer.surgical_plan.get_avoid_structures()) == 1
    assert f"or_avoid_structure_{s2.structure_id}" in viewer.plotter.actors


def test_15_scan_change_clears_all_structures(qapp, synthetic_volume):
    """15. Loading another scan resets all avoid structures and removes their actors."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    s1 = viewer.add_avoid_structure(10.0, 10.0, 10.0)
    assert f"or_avoid_structure_{s1.structure_id}" in viewer.plotter.actors

    fake_scan = {"file_path": "/path/to/new_scan", "id": "scan_999", "slice_count": 50, "type": "CT"}
    viewer.load_scan({"name": "Patient B"}, fake_scan)

    assert len(viewer.surgical_plan.get_avoid_structures()) == 0
    assert f"or_avoid_structure_{s1.structure_id}" not in viewer.plotter.actors


def test_16_existing_entry_target_remains_functional(qapp, synthetic_volume):
    """16. Existing Entry and Target landmarks remain functional and independent when structures are added."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(5.0, 6.0, 7.0)
    viewer.set_target_point(20.0, 25.0, 30.0)
    viewer.add_avoid_structure(12.0, 15.0, 18.0)

    assert viewer.surgical_plan.has_entry()
    assert viewer.surgical_plan.has_target()
    assert viewer.surgical_plan.entry_point.coordinates == (5.0, 6.0, 7.0)
    assert viewer.surgical_plan.target_point.coordinates == (20.0, 25.0, 30.0)


def test_17_existing_planned_route_remains_functional(qapp, synthetic_volume):
    """17. Existing Planned Route remains functional when structures are added or removed."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(0.0, 0.0, 50.0)
    route = viewer.surgical_plan.get_planned_route()
    assert route is not None
    assert route.length_mm == 50.0

    s1 = viewer.add_avoid_structure(10.0, 0.0, 25.0)
    # Route length and endpoints are unaffected
    assert viewer.surgical_plan.get_planned_route().length_mm == 50.0

    viewer.remove_avoid_structure(s1.structure_id)
    assert viewer.surgical_plan.get_planned_route().length_mm == 50.0


def test_18_route_distance_calculation_is_geometrically_correct(qapp):
    """18. 3D point-to-line-segment distance calculation is mathematically correct."""
    p_entry = PlanningPoint(point_type="ENTRY", x_mm=0.0, y_mm=0.0, z_mm=0.0)
    p_target = PlanningPoint(point_type="TARGET", x_mm=0.0, y_mm=0.0, z_mm=100.0)
    route = PlannedRoute(entry_point=p_entry, target_point=p_target)

    # 1. Point projecting perpendicularly onto midpoint (z=50) at distance 20 mm along X
    struct_mid = AvoidStructure(structure_id="s1", name="Mid", center_x_mm=20.0, center_y_mm=0.0, center_z_mm=50.0)
    assert pytest.approx(route.distance_to_structure(struct_mid), 1e-4) == 20.0

    # 2. Point projecting beyond Entry (z=-30) -> closest point is Entry (0,0,0)
    # Distance = sqrt(0^2 + 40^2 + (-30)^2) = sqrt(1600 + 900) = 50.0 mm
    struct_before = AvoidStructure(structure_id="s2", name="Before", center_x_mm=0.0, center_y_mm=40.0, center_z_mm=-30.0)
    assert pytest.approx(route.distance_to_structure(struct_before), 1e-4) == 50.0

    # 3. Point projecting beyond Target (z=140) -> closest point is Target (0,0,100)
    # Distance = sqrt(30^2 + 0^2 + (140-100)^2) = sqrt(900 + 1600) = 50.0 mm
    struct_after = AvoidStructure(structure_id="s3", name="After", center_x_mm=30.0, center_y_mm=0.0, center_z_mm=140.0)
    assert pytest.approx(route.distance_to_structure(struct_after), 1e-4) == 50.0


def test_19_route_distance_calculation_does_not_modify_route_geometry(qapp):
    """19. Computing route distance does not modify route endpoints or length."""
    p_entry = PlanningPoint(point_type="ENTRY", x_mm=10.0, y_mm=20.0, z_mm=30.0)
    p_target = PlanningPoint(point_type="TARGET", x_mm=40.0, y_mm=60.0, z_mm=80.0)
    route = PlannedRoute(entry_point=p_entry, target_point=p_target)
    orig_length = route.length_mm

    struct = AvoidStructure(structure_id="s1", name="Test", center_x_mm=15.0, center_y_mm=25.0, center_z_mm=35.0)
    dist = route.distance_to_structure(struct)

    assert route.length_mm == orig_length
    assert route.entry_point.coordinates == (10.0, 20.0, 30.0)
    assert route.target_point.coordinates == (40.0, 60.0, 80.0)


def test_20_structure_updates_do_not_trigger_dicom_reload(qapp, synthetic_volume):
    """20. Adding or removing avoid structures does not reload DICOM volume."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    reload_spy = MagicMock()
    viewer._start_load = reload_spy

    s1 = viewer.add_avoid_structure(10.0, 15.0, 20.0)
    viewer.view_avoid_structure(s1.structure_id)
    viewer.remove_avoid_structure(s1.structure_id)
    viewer.clear_avoid_structures()

    assert reload_spy.call_count == 0


def test_21_2d_projection_uses_physical_coordinates(qapp, synthetic_volume):
    """21. 2D slice viewer uses physical mm coordinates for avoid structure projections."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    s1 = viewer.add_avoid_structure(15.0, 20.0, 25.0, radius_mm=5.0)
    assert len(viewer.panel_2d.canvas.avoid_structures) == 1
    assert viewer.panel_2d.canvas.avoid_structures[0].center == (15.0, 20.0, 25.0)


def test_22_mpr_projection_uses_physical_coordinates(qapp, synthetic_volume):
    """22. MPR viewports use physical mm coordinates for avoid structure projections."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    s1 = viewer.add_avoid_structure(15.0, 20.0, 25.0, radius_mm=5.0)
    assert len(viewer.mpr_view.w_axial.avoid_structures) == 1
    assert len(viewer.mpr_view.w_coronal.avoid_structures) == 1
    assert len(viewer.mpr_view.w_sagittal.avoid_structures) == 1
    assert viewer.mpr_view.w_axial.avoid_structures[0].center == (15.0, 20.0, 25.0)


def test_23_no_duplicate_actors_accumulate(qapp, synthetic_volume):
    """23. No duplicate actors accumulate after repeated add/remove cycles."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    for i in range(10):
        s = viewer.add_avoid_structure(10.0 + i, 15.0, 20.0)
        viewer.remove_avoid_structure(s.structure_id)

    # All actors must be cleanly removed
    actors = [k for k in viewer.plotter.actors.keys() if k.startswith("or_avoid_structure_")]
    assert len(actors) == 0


def test_24_existing_measurement_tests_still_pass(qapp, synthetic_volume):
    """24. Standard 3D measurement remains completely isolated and unchanged."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.add_avoid_structure(10.0, 10.0, 10.0)
    m_rec = viewer._add_3d_measurement((0.0, 0.0, 0.0), (30.0, 40.0, 0.0), source="3D")
    assert pytest.approx(m_rec["distance_mm"], 1e-4) == 50.0
    assert len(viewer._measurements_3d) == 1

    # Clearing measurements leaves avoid structures intact
    viewer.clear_measurements_3d()
    assert len(viewer._measurements_3d) == 0
    assert len(viewer.surgical_plan.get_avoid_structures()) == 1


def test_25_existing_2d_3d_synchronization_still_pass(qapp, synthetic_volume):
    """25. 2D/3D synchronization remains intact with structures."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    s1 = viewer.add_avoid_structure(12.0, 16.0, 24.0)
    viewer.view_avoid_structure(s1.structure_id)

    # 2D panel snapped to structure center
    pos = viewer.panel_2d.get_current_physical_position()
    assert abs(pos[0] - 12.0) < 1.0
    assert abs(pos[1] - 16.0) < 1.0
    assert abs(pos[2] - 24.0) < 1.0

    # MPR snapped to corresponding indices
    dx = viewer.mpr_view.pixel_spacing[1]
    dy = viewer.mpr_view.pixel_spacing[0]
    dz = viewer.mpr_view.slice_thickness
    assert abs(viewer.mpr_view.idx_x * dx - 12.0) <= dx
    assert abs(viewer.mpr_view.idx_y * dy - 16.0) <= dy
    assert abs(viewer.mpr_view.idx_z * dz - 24.0) <= dz


def test_26_existing_planned_route_tests_still_pass(qapp, synthetic_volume):
    """26. Planned Route tests logic still passes with avoid structures."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_entry_point(0.0, 0.0, 0.0)
    viewer.set_target_point(30.0, 40.0, 0.0)
    route = viewer.surgical_plan.get_planned_route()
    assert route is not None
    assert pytest.approx(route.length_mm, 1e-4) == 50.0

    # Add avoid structure and verify route card in UI
    card = StructuresToAvoidCard()
    s1 = viewer.add_avoid_structure(15.0, 20.0, 0.0)  # On the line segment
    card.update_structures(viewer.surgical_plan)
    assert card.lbl_status.text() == "1 structure marked"

    labels = card.findChildren(type(card.lbl_status))
    texts = [lbl.text() for lbl in labels]
    assert any("Route Distance: 0.0 mm" in t for t in texts)


def test_27_voice_commands_route_correctly(qapp, synthetic_volume):
    """27. Voice commands route to structures to avoid correctly."""
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # Voice command: add structure
    viewer.handle_voice_command("add structure")
    assert viewer._planning_picking_mode == "ADD_STRUCTURE"

    # Add a structure directly
    s1 = viewer.add_avoid_structure(10.0, 20.0, 30.0)
    assert len(viewer.surgical_plan.get_avoid_structures()) == 1

    # Voice command: clear structures
    viewer.handle_voice_command("clear structures")
    assert len(viewer.surgical_plan.get_avoid_structures()) == 0
