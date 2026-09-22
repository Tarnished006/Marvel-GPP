# test_sync_2d_3d.py
"""
Automated unit and integration test suite for Synchronized 2D-3D Anatomical Position Linking in Aegis-Touch / Marvel-GPP.

Verifies all 17 required conditions:
 1. Synchronization is OFF by default.
 2. Crosshair visibility toggles correctly.
 3. 3D marker visibility toggles correctly.
 4. Axial slice maps to the correct Z position.
 5. Coronal slice maps to the correct Y position.
 6. Sagittal slice maps to the correct X position.
 7. Slice changes update the 3D marker.
 8. Clipping-plane linking works when enabled.
 9. Disabling sync stops automatic updates.
10. No recursive update loop occurs.
11. No crash without a loaded scan.
12. No crash without a loaded mesh.
13. Existing 2D viewer remains functional.
14. Existing clipping remains functional.
15. Existing heatmap remains functional.
16. Existing opacity remains functional.
17. Existing voice commands remain functional.
"""

import os
import pytest
import numpy as np
import pyvista as pv
from PyQt6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from screens.slice_2d_viewer import Slice2DViewerWidget, CTSliceCanvas
from screens.viewer_3d import Viewer3D


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def synthetic_volume():
    """Generates synthetic 3D CT volume (H=40, W=50, D=30) with known spacings."""
    H, W, D = 40, 50, 30
    vol = np.full((H, W, D), -1000.0, dtype=np.float32)
    vol[10:30, 15:35, 5:25] = 40.0
    vol[15:25, 20:30, 10:20] = 1200.0
    pixel_spacing = (0.8, 0.8)  # (dy, dx)
    slice_thickness = 1.5       # dz
    return vol, pixel_spacing, slice_thickness


def _setup_viewer_with_mesh_and_volume(vol, pixel_spacing, slice_thickness):
    """Creates a fully configured Viewer3D with synthetic volume and matching bone mesh."""
    viewer = Viewer3D()
    H, W, D = vol.shape
    dy, dx = pixel_spacing
    dz = slice_thickness

    # Mesh spanning the volume bounding box
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

        @staticmethod
        def add_to_plotter(plotter, density_mode=False, cmap="turbo"):
            return plotter.add_mesh(mesh, color="#e8c87a", name="bone"), None

    viewer._on_meshes_ready(MockMeshSet())
    viewer.panel_2d.load_volume(vol, pixel_spacing, slice_thickness)
    return viewer


# ── Test 1: Synchronization is OFF by default ────────────────────────────────
def test_1_sync_disabled_by_default(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    assert viewer.is_sync_2d_3d_enabled() is False
    assert viewer._sync_2d_3d_enabled is False
    assert viewer.btn_sync_toggle.isChecked() is False
    assert "OFF" in viewer.btn_sync_toggle.text()
    assert viewer._sync_marker_actor is None
    assert "sync_marker" not in viewer.plotter.actors


# ── Test 2: Crosshair visibility toggles correctly ───────────────────────────
def test_2_crosshair_visibility_toggles(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    assert viewer.is_crosshair_visible() is True
    assert viewer.btn_sync_crosshair.isChecked() is True
    assert viewer.panel_2d.canvas.show_crosshair is True

    # Toggle crosshair OFF
    viewer.set_crosshair_visible(False)
    assert viewer.is_crosshair_visible() is False
    assert viewer.btn_sync_crosshair.isChecked() is False
    assert viewer.panel_2d.canvas.show_crosshair is False

    # Toggle crosshair ON
    viewer.set_crosshair_visible(True)
    assert viewer.is_crosshair_visible() is True
    assert viewer.btn_sync_crosshair.isChecked() is True
    assert viewer.panel_2d.canvas.show_crosshair is True


# ── Test 3: 3D marker visibility toggles correctly ───────────────────────────
def test_3_marker_visibility_toggles(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # Enable sync first so marker is active
    viewer.set_sync_2d_3d(True)
    assert viewer.is_marker_visible() is True
    assert "sync_marker" in viewer.plotter.actors

    # Toggle marker OFF
    viewer.set_marker_visible(False)
    assert viewer.is_marker_visible() is False
    assert viewer.btn_sync_marker.isChecked() is False
    assert "sync_marker" not in viewer.plotter.actors

    # Toggle marker ON
    viewer.set_marker_visible(True)
    assert viewer.is_marker_visible() is True
    assert viewer.btn_sync_marker.isChecked() is True
    assert "sync_marker" in viewer.plotter.actors


# ── Test 4: Axial slice maps to the correct Z position ───────────────────────
def test_4_axial_slice_maps_to_z_position(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    dz = st
    for idx in (0, 5, 12, 29):
        pos = viewer.map_slice_to_3d("axial", idx)
        expected_z = idx * dz
        assert abs(pos[2] - expected_z) < 1e-4, f"Axial slice {idx} mapped to Z={pos[2]}, expected {expected_z}"


# ── Test 5: Coronal slice maps to the correct Y position ─────────────────────
def test_5_coronal_slice_maps_to_y_position(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    dy = sp[0]
    for idx in (0, 10, 25, 39):
        pos = viewer.map_slice_to_3d("coronal", idx)
        expected_y = idx * dy
        assert abs(pos[1] - expected_y) < 1e-4, f"Coronal slice {idx} mapped to Y={pos[1]}, expected {expected_y}"


# ── Test 6: Sagittal slice maps to the correct X position ────────────────────
def test_6_sagittal_slice_maps_to_x_position(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    dx = sp[1]
    for idx in (0, 15, 30, 49):
        pos = viewer.map_slice_to_3d("sagittal", idx)
        expected_x = idx * dx
        assert abs(pos[0] - expected_x) < 1e-4, f"Sagittal slice {idx} mapped to X={pos[0]}, expected {expected_x}"


# ── Test 7: Slice changes update the 3D marker ───────────────────────────────
def test_7_slice_changes_update_3d_marker(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_sync_2d_3d(True)
    viewer.panel_2d.set_orientation("axial")
    viewer.panel_2d.set_slice_index(22)

    assert "sync_marker" in viewer.plotter.actors
    expected_z = 22 * st
    assert abs(viewer._reference_pos[2] - expected_z) < 1e-4

    # Change to coronal
    viewer.panel_2d.set_orientation("coronal")
    viewer.panel_2d.set_slice_index(18)
    expected_y = 18 * sp[0]
    assert abs(viewer._reference_pos[1] - expected_y) < 1e-4


# ── Test 8: Clipping-plane linking works when enabled ────────────────────────
def test_8_clipping_plane_linking_works(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_sync_2d_3d(True)
    viewer.set_link_clipping(True)
    assert viewer.is_clipping_linked() is True

    # 1. 2D slice update -> 3D clipping update
    viewer.panel_2d.set_orientation("axial")
    count_z = viewer.panel_2d.get_slice_count()
    viewer.panel_2d.set_slice_index(15)

    assert viewer._clip_axis == "z"
    assert viewer._clip_active is True
    expected_fraction = 15 / (count_z - 1)
    assert abs(viewer._clip_fraction - expected_fraction) < 0.05

    # 2. 3D clipping adjustment -> 2D slice update
    viewer.set_clip_axis("y")
    viewer.slider_clip_pos.setValue(70)
    assert viewer.panel_2d.current_orientation == "coronal"
    count_y = viewer.panel_2d.get_slice_count()
    expected_slice = int(round(0.70 * (count_y - 1)))
    assert viewer.panel_2d.get_current_slice_index() == expected_slice


# ── Test 9: Disabling sync stops automatic updates ───────────────────────────
def test_9_disabling_sync_stops_automatic_updates(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_sync_2d_3d(True)
    assert "sync_marker" in viewer.plotter.actors

    # Disable sync
    viewer.set_sync_2d_3d(False)
    assert "sync_marker" not in viewer.plotter.actors

    # Changing slice must not resurrect the marker actor
    viewer.panel_2d.set_slice_index(8)
    assert "sync_marker" not in viewer.plotter.actors


# ── Test 10: No recursive update loop occurs ─────────────────────────────────
def test_10_no_recursive_update_loop(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_sync_2d_3d(True)
    viewer.set_link_clipping(True)

    # Perform rapid back-and-forth updates between 2D and 3D
    try:
        for i in range(10):
            viewer.panel_2d.set_slice_index(i * 2)
            viewer.slider_clip_pos.setValue(i * 8)
            viewer.panel_2d.canvas.crosshair_moved.emit(0.2, 0.8)
            viewer._on_3d_point_picked((10.0 + i, 15.0 + i, 20.0 + i))
    except RecursionError:
        pytest.fail("RecursionError detected during 2D-3D synchronization updates!")

    assert viewer._sync_in_progress is False


# ── Test 11: No crash without a loaded scan ──────────────────────────────────
def test_11_no_crash_without_loaded_scan(qapp):
    viewer = Viewer3D()
    # Execute all sync operations in empty uninitialized state
    viewer.set_sync_2d_3d(True)
    viewer.set_marker_visible(False)
    viewer.set_crosshair_visible(False)
    viewer.set_link_clipping(True)
    pos = viewer.map_slice_to_3d("axial", 10)
    assert pos is not None
    indices = viewer.map_3d_to_slice(10.0, 20.0, 30.0)
    assert len(indices) == 3
    viewer._update_3d_marker(10.0, 20.0, 30.0)
    viewer._remove_3d_marker()
    viewer.set_sync_2d_3d(False)


# ── Test 12: No crash without a loaded mesh ──────────────────────────────────
def test_12_no_crash_without_loaded_mesh(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = Viewer3D()
    # Volume is loaded in 2D panel, but bone mesh is not loaded
    viewer.panel_2d.load_volume(vol, sp, st)

    viewer.set_sync_2d_3d(True)
    viewer.panel_2d.set_slice_index(10)
    viewer.set_link_clipping(True)
    viewer.slider_clip_pos.setValue(40)
    viewer._on_3d_point_picked((5.0, 10.0, 15.0))
    viewer.set_sync_2d_3d(False)


# ── Test 13: Existing 2D viewer remains functional ───────────────────────────
def test_13_existing_2d_viewer_remains_functional(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_2d_panel_visible(True)
    assert not viewer.panel_2d.isHidden()

    # Orientation switching
    viewer.panel_2d.set_orientation("coronal")
    assert viewer.panel_2d.current_orientation == "coronal"

    # Window / Level preset
    viewer.panel_2d.set_window_preset("Soft Tissue")
    assert viewer.panel_2d.cur_wl_preset == "Soft Tissue"

    # HU query
    hu = viewer.panel_2d.get_current_hu_at(20, 20)
    assert hu is not None


# ── Test 14: Existing clipping remains functional ────────────────────────────
def test_14_existing_clipping_remains_functional(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_clipping(True)
    assert viewer._clip_active is True
    viewer.set_clip_axis("y")
    viewer.set_clip_position(0.5)

    disp = viewer._get_current_display_mesh()
    assert disp is not None
    assert disp is viewer._clipped_mesh

    viewer.set_clipping(False)
    assert viewer._get_current_display_mesh() is viewer._bone_mesh


# ── Test 15: Existing heatmap remains functional ─────────────────────────────
def test_15_existing_heatmap_remains_functional(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_density_colormap(True)
    assert viewer._density_active is True
    mesh = viewer._get_current_display_mesh()
    assert "HU_density" in mesh.point_data

    viewer.set_density_colormap(False)
    assert viewer._density_active is False


# ── Test 16: Existing opacity remains functional ─────────────────────────────
def test_16_existing_opacity_remains_functional(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_bone_opacity(0.65)
    assert abs(viewer._bone_opacity - 0.65) < 1e-4
    if viewer.bone_actor is not None:
        assert abs(viewer.bone_actor.GetProperty().GetOpacity() - 0.65) < 1e-3


# ── Test 17: Existing and new voice commands remain functional ───────────────
def test_17_voice_commands_remain_functional(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # 1. New sync commands
    viewer.handle_voice_command("enable 2d 3d sync")
    assert viewer.is_sync_2d_3d_enabled() is True

    viewer.handle_voice_command("hide crosshair")
    assert viewer.is_crosshair_visible() is False

    viewer.handle_voice_command("show crosshair")
    assert viewer.is_crosshair_visible() is True

    viewer.handle_voice_command("hide 3d marker")
    assert viewer.is_marker_visible() is False

    viewer.handle_voice_command("show 3d marker")
    assert viewer.is_marker_visible() is True

    viewer.handle_voice_command("link clipping plane")
    assert viewer.is_clipping_linked() is True

    viewer.handle_voice_command("unlink clipping plane")
    assert viewer.is_clipping_linked() is False

    viewer.handle_voice_command("disable 2d 3d sync")
    assert viewer.is_sync_2d_3d_enabled() is False

    # 2. Existing voice commands continue working
    viewer.handle_voice_command("show 2d")
    assert not viewer.panel_2d.isHidden()

    viewer.handle_voice_command("show coronal")
    assert viewer.panel_2d.current_orientation == "coronal"

    viewer.handle_voice_command("lung window")
    assert viewer.panel_2d.cur_wl_preset == "Lung"

    viewer.handle_voice_command("anterior")
    viewer.handle_voice_command("zoom in")
    viewer.handle_voice_command("50% opacity")
    assert abs(viewer._bone_opacity - 0.50) < 1e-3
