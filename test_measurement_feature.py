# test_measurement_feature.py
"""
Automated unit and integration test suite for the Distance Measurement and Annotation System in Aegis-Touch / Marvel-GPP.

Verifies all 20 required specifications:
 1. Measurement mode is OFF by default.
 2. Starting measurement enters the correct state.
 3. First point selection works.
 4. Second point selection completes measurement.
 5. 2D axial distance uses dx and dy correctly.
 6. 2D coronal distance uses dx and dz correctly.
 7. 2D sagittal distance uses dy and dz correctly.
 8. 3D Euclidean distance is correct.
 9. Multiple measurements are supported.
10. Clear measurements removes all measurement state and actors.
11. Show/hide measurements works.
12. No crash without a loaded scan.
13. No crash without a loaded mesh.
14. Incomplete measurement can be cancelled safely.
15. Existing crosshair remains functional.
16. Existing 2D-3D synchronization remains functional.
17. Existing clipping remains functional.
18. Existing HU heatmap remains functional.
19. Existing opacity remains functional.
20. Existing voice commands remain functional.
"""

import os
import pytest
import numpy as np
import pyvista as pv
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QMouseEvent

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from screens.slice_2d_viewer import Slice2DViewerWidget, CTSliceCanvas, Measurement2D
from screens.viewer_3d import Viewer3D


@pytest.fixture(scope="session")
def qapp():
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
    """Creates a fully configured Viewer3D with synthetic volume and matching bone mesh."""
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

        @staticmethod
        def add_to_plotter(plotter, density_mode=False, cmap="turbo"):
            return plotter.add_mesh(mesh, color="#e8c87a", name="bone"), None

    viewer._on_meshes_ready(MockMeshSet())
    viewer.panel_2d.load_volume(vol, pixel_spacing, slice_thickness)
    return viewer


# ── Test 1: Measurement mode is OFF by default ────────────────────────────────
def test_1_measurement_mode_off_by_default(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    assert viewer.is_measuring_3d() is False
    assert viewer._measuring_3d is False
    assert viewer.btn_measure_3d.isChecked() is False
    assert "OFF" in viewer.btn_measure_3d.text()
    assert len(viewer.get_measurements_3d()) == 0

    assert viewer.panel_2d.is_measuring() is False
    assert viewer.panel_2d.canvas.is_measuring() is False
    assert viewer.panel_2d.btn_measure.isChecked() is False
    assert "OFF" in viewer.panel_2d.btn_measure.text()
    assert len(viewer.panel_2d.get_measurements()) == 0


# ── Test 2: Starting measurement enters the correct state ─────────────────────
def test_2_start_measurement_enters_correct_state(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.start_measurement()
    assert viewer.is_measuring_3d() is True
    assert viewer._measuring_3d is True
    assert viewer.btn_measure_3d.isChecked() is True
    assert "ON" in viewer.btn_measure_3d.text()
    assert "Select first" in viewer.info_bar.text()

    viewer.panel_2d.start_measurement()
    assert viewer.panel_2d.is_measuring() is True
    assert viewer.panel_2d.btn_measure.isChecked() is True
    assert "ON" in viewer.panel_2d.btn_measure.text()
    assert "Select first point" in viewer.panel_2d.lbl_measure_status.text()


# ── Test 3: First point selection works ────────────────────────────────────────
def test_3_first_point_selection_works(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # 3D first point
    viewer.start_measurement()
    viewer._on_3d_point_picked((15.0, 25.0, 35.0))
    assert viewer._pending_3d_point == (15.0, 25.0, 35.0)
    assert "Select second point" in viewer.info_bar.text()

    # 2D first point
    panel = viewer.panel_2d
    panel.start_measurement()
    canvas = panel.canvas
    rect = canvas._get_target_rect()

    # Click on center of target_rect
    p1 = QPointF(rect.center())
    ev1 = QMouseEvent(QMouseEvent.Type.MouseButtonPress, p1, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev1)

    assert canvas.pending_point is not None
    assert "Select second point" in panel.lbl_measure_status.text()


# ── Test 4: Second point selection completes measurement ──────────────────────
def test_4_second_point_selection_completes_measurement(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # 3D second point
    viewer.start_measurement()
    viewer._on_3d_point_picked((0.0, 0.0, 0.0))
    viewer._on_3d_point_picked((3.0, 4.0, 0.0))
    assert viewer._pending_3d_point is None
    m3d = viewer.get_measurements_3d()
    assert len(m3d) == 1
    assert pytest.approx(m3d[0]["distance_mm"], 0.01) == 5.0
    assert "Distance: 5.0 mm" in viewer.info_bar.text()

    # 2D second point
    panel = viewer.panel_2d
    panel.start_measurement()
    canvas = panel.canvas
    rect = canvas._get_target_rect()

    p1 = QPointF(rect.left() + 0.2 * rect.width(), rect.top() + 0.2 * rect.height())
    p2 = QPointF(rect.left() + 0.5 * rect.width(), rect.top() + 0.6 * rect.height())

    ev1 = QMouseEvent(QMouseEvent.Type.MouseButtonPress, p1, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev1)
    ev2 = QMouseEvent(QMouseEvent.Type.MouseButtonPress, p2, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev2)

    assert canvas.pending_point is None
    m2d = panel.get_measurements()
    assert len(m2d) == 1
    assert m2d[0].distance_mm > 0.0
    assert "Distance:" in panel.lbl_measure_status.text()


# ── Test 5: 2D axial distance uses dx and dy correctly ────────────────────────
def test_5_2d_axial_distance_uses_dx_and_dy(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume  # sp=(dy=0.8, dx=0.5), st=dz=2.0
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)
    panel = viewer.panel_2d

    # Axial in-plane spacing is dx and dy
    # P1 at (col=10, row=10), P2 at (col=20, row=10) -> delta col=10, row=0
    dist_x = panel.calc_2d_distance((10, 10), (20, 10), orientation="axial")
    assert pytest.approx(dist_x, 0.001) == 10 * 0.5  # 5.0 mm

    # P1 at (col=10, row=10), P2 at (col=10, row=20) -> delta col=0, row=10
    dist_y = panel.calc_2d_distance((10, 10), (10, 20), orientation="axial")
    assert pytest.approx(dist_y, 0.001) == 10 * 0.8  # 8.0 mm

    # Diagonal
    dist_diag = panel.calc_2d_distance((10, 10), (20, 20), orientation="axial")
    expected = np.sqrt((10 * 0.5)**2 + (10 * 0.8)**2)
    assert pytest.approx(dist_diag, 0.001) == expected


# ── Test 6: 2D coronal distance uses dx and dz correctly ──────────────────────
def test_6_2d_coronal_distance_uses_dx_and_dz(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume  # dx=0.5, dy=0.8, dz=2.0
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)
    panel = viewer.panel_2d

    # Coronal columns are X (dx=0.5), rows are Z (dz=2.0)
    dist_x = panel.calc_2d_distance((10, 10), (20, 10), orientation="coronal")
    assert pytest.approx(dist_x, 0.001) == 10 * 0.5  # 5.0 mm

    dist_z = panel.calc_2d_distance((10, 10), (10, 20), orientation="coronal")
    assert pytest.approx(dist_z, 0.001) == 10 * 2.0  # 20.0 mm

    dist_diag = panel.calc_2d_distance((10, 10), (20, 20), orientation="coronal")
    expected = np.sqrt((10 * 0.5)**2 + (10 * 2.0)**2)
    assert pytest.approx(dist_diag, 0.001) == expected


# ── Test 7: 2D sagittal distance uses dy and dz correctly ─────────────────────
def test_7_2d_sagittal_distance_uses_dy_and_dz(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume  # dx=0.5, dy=0.8, dz=2.0
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)
    panel = viewer.panel_2d

    # Sagittal columns are Y (dy=0.8), rows are Z (dz=2.0)
    dist_y = panel.calc_2d_distance((10, 10), (20, 10), orientation="sagittal")
    assert pytest.approx(dist_y, 0.001) == 10 * 0.8  # 8.0 mm

    dist_z = panel.calc_2d_distance((10, 10), (10, 20), orientation="sagittal")
    assert pytest.approx(dist_z, 0.001) == 10 * 2.0  # 20.0 mm

    dist_diag = panel.calc_2d_distance((10, 10), (20, 20), orientation="sagittal")
    expected = np.sqrt((10 * 0.8)**2 + (10 * 2.0)**2)
    assert pytest.approx(dist_diag, 0.001) == expected


# ── Test 8: 3D Euclidean distance is correct ──────────────────────────────────
def test_8_3d_euclidean_distance_is_correct(qapp):
    p1 = (10.0, 20.0, 30.0)
    p2 = (13.0, 24.0, 30.0)
    dist = Viewer3D.calc_3d_distance(p1, p2)
    assert pytest.approx(dist, 0.001) == 5.0

    p3 = (0.0, 0.0, 0.0)
    p4 = (1.0, 2.0, 2.0)
    dist2 = Viewer3D.calc_3d_distance(p3, p4)
    assert pytest.approx(dist2, 0.001) == 3.0


# ── Test 9: Multiple measurements are supported ───────────────────────────────
def test_9_multiple_measurements_supported(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # Add 3 3D measurements
    viewer._add_3d_measurement((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    viewer._add_3d_measurement((0.0, 0.0, 0.0), (0.0, 20.0, 0.0))
    viewer._add_3d_measurement((0.0, 0.0, 0.0), (0.0, 0.0, 30.0))
    assert len(viewer.get_measurements_3d()) == 3
    assert len(viewer._measurement_actor_names) == 12  # 3 * (line, pt1, pt2, lbl)

    # Add 2 2D measurements
    panel = viewer.panel_2d
    canvas = panel.canvas
    m1 = Measurement2D("1", 0.1, 0.1, 0.2, 0.2, 5, 5, 10, 10, 12.5, "axial", 15)
    m2 = Measurement2D("2", 0.3, 0.3, 0.5, 0.5, 15, 15, 25, 25, 25.0, "axial", 15)
    canvas.measurements.extend([m1, m2])
    assert len(panel.get_measurements()) == 2


# ── Test 10: Clear measurements removes all measurement state and actors ──────
def test_10_clear_measurements_removes_state_and_actors(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # Add measurements in 3D
    viewer._add_3d_measurement((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    assert len(viewer.get_measurements_3d()) == 1
    viewer.clear_measurements_3d()
    assert len(viewer.get_measurements_3d()) == 0
    assert len(viewer._measurement_actor_names) == 0

    # Add measurements in 2D
    panel = viewer.panel_2d
    panel.canvas.measurements.append(
        Measurement2D("1", 0.1, 0.1, 0.2, 0.2, 5, 5, 10, 10, 12.5, "axial", 15)
    )
    assert len(panel.get_measurements()) == 1
    panel.clear_measurements()
    assert len(panel.get_measurements()) == 0


# ── Test 11: Show/hide measurements works ─────────────────────────────────────
def test_11_show_hide_measurements(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # 3D
    viewer._add_3d_measurement((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    viewer.set_measurements_3d_visible(False)
    assert viewer.is_measurements_3d_visible() is False
    assert viewer.btn_measure_3d_vis.isChecked() is False
    assert "OFF" in viewer.btn_measure_3d_vis.text()

    viewer.set_measurements_3d_visible(True)
    assert viewer.is_measurements_3d_visible() is True
    assert viewer.btn_measure_3d_vis.isChecked() is True
    assert "ON" in viewer.btn_measure_3d_vis.text()

    # 2D
    panel = viewer.panel_2d
    panel.set_measurements_visible(False)
    assert panel.is_measurements_visible() is False
    assert panel.btn_measure_vis.isChecked() is False
    assert "Hide" in panel.btn_measure_vis.text()

    panel.set_measurements_visible(True)
    assert panel.is_measurements_visible() is True
    assert panel.btn_measure_vis.isChecked() is True
    assert "Show" in panel.btn_measure_vis.text()


# ── Test 12: No crash without a loaded scan ───────────────────────────────────
def test_12_no_crash_without_loaded_scan(qapp):
    viewer = Viewer3D()
    panel = viewer.panel_2d

    # 3D calls on empty viewer
    viewer.start_measurement()
    viewer.cancel_pending_3d_measurement()
    viewer.clear_measurements_3d()
    viewer.set_measurements_3d_visible(False)
    viewer.finish_measurement()
    assert viewer.is_measuring_3d() is False

    # 2D calls on empty panel
    panel.start_measurement()
    panel.clear_measurements()
    panel.set_measurements_visible(False)
    panel.finish_measurement()
    assert panel.is_measuring() is False


# ── Test 13: No crash without a loaded mesh ───────────────────────────────────
def test_13_no_crash_without_loaded_mesh(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = Viewer3D()
    viewer.panel_2d.load_volume(vol, sp, st)

    # 3D has volume in 2D but no bone mesh
    viewer.start_measurement()
    viewer.finish_measurement()
    viewer.clear_measurements_3d()
    assert len(viewer.get_measurements_3d()) == 0


# ── Test 14: Incomplete measurement can be cancelled safely ───────────────────
def test_14_incomplete_measurement_cancelled_safely(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # 3D: select point 1, then finish/cancel
    viewer.start_measurement()
    viewer._on_3d_point_picked((10.0, 20.0, 30.0))
    assert viewer._pending_3d_point is not None
    viewer.finish_measurement()
    assert viewer._pending_3d_point is None
    assert len(viewer.get_measurements_3d()) == 0

    # 2D: select point 1, then finish/cancel
    panel = viewer.panel_2d
    panel.start_measurement()
    panel.canvas.pending_point = (0.3, 0.4)
    panel.finish_measurement()
    assert panel.canvas.pending_point is None
    assert len(panel.get_measurements()) == 0


# ── Test 15: Existing crosshair remains functional ────────────────────────────
def test_15_existing_crosshair_remains_functional(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)
    panel = viewer.panel_2d
    canvas = panel.canvas

    # When measuring is False, clicking updates crosshair
    assert canvas.is_measuring() is False
    rect = canvas._get_target_rect()
    p = QPointF(rect.left() + 0.3 * rect.width(), rect.top() + 0.7 * rect.height())
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, p, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev)

    assert pytest.approx(canvas.cross_u, 0.05) == 0.3
    assert pytest.approx(canvas.cross_v, 0.05) == 0.7

    # Crosshair toggle works
    panel.set_crosshair_visible(False)
    assert panel.is_crosshair_visible() is False
    panel.set_crosshair_visible(True)
    assert panel.is_crosshair_visible() is True


# ── Test 16: Existing 2D-3D synchronization remains functional ────────────────
def test_16_existing_2d_3d_sync_remains_functional(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.set_sync_2d_3d(True)
    assert viewer.is_sync_2d_3d_enabled() is True

    # Moving slice updates 3D marker
    viewer.panel_2d.set_orientation("axial")
    viewer.panel_2d.set_slice_index(10)
    assert viewer._reference_pos[2] == pytest.approx(10 * st, 0.01)

    # Completing a 2D measurement synchronizes to 3D when sync is ON
    m = Measurement2D(
        "sync_test", 0.2, 0.2, 0.8, 0.8, 10, 8, 40, 32,
        35.0, "axial", 10,
        physical_start=(5.0, 6.4, 20.0),
        physical_end=(20.0, 25.6, 20.0)
    )
    viewer.panel_2d.measurement_added.emit(m)
    m3d = viewer.get_measurements_3d()
    assert any(rec.get("source") == "2D_synced" for rec in m3d)


# ── Test 17: Existing clipping remains functional ─────────────────────────────
def test_17_existing_clipping_remains_functional(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # Add a measurement first
    viewer._add_3d_measurement((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))

    # Toggle clipping
    viewer.set_clipping(True)
    assert viewer._clip_active is True
    viewer.set_clip_position(0.3)
    assert pytest.approx(viewer._clip_fraction, 0.01) == 0.3
    viewer.reverse_clip_direction()
    assert viewer._clip_inverted is True

    # Measurement actor is independent of clipped mesh
    assert len(viewer.get_measurements_3d()) == 1


# ── Test 18: Existing HU heatmap remains functional ───────────────────────────
def test_18_existing_heatmap_remains_functional(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # Add a measurement first
    viewer._add_3d_measurement((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))

    # Activate heatmap
    viewer.set_density_colormap(True)
    assert viewer._density_active is True

    # Switch back to normal
    viewer.set_density_colormap(False)
    assert viewer._density_active is False

    # Measurement remains intact
    assert len(viewer.get_measurements_3d()) == 1


# ── Test 19: Existing opacity remains functional ──────────────────────────────
def test_19_existing_opacity_remains_functional(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer._add_3d_measurement((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))

    viewer.set_bone_opacity(0.5)
    assert pytest.approx(viewer._bone_opacity, 0.01) == 0.5
    assert viewer.lbl_opacity.text() == "50%"

    # Measurements remain visible
    assert viewer.is_measurements_3d_visible() is True


# ── Test 20: Existing voice commands remain functional ────────────────────────
def test_20_existing_voice_commands_remain_functional(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # 1. Measurement voice commands
    viewer.handle_voice_command("start measurement")
    assert viewer.is_measuring_3d() is True

    viewer.handle_voice_command("finish measurement")
    assert viewer.is_measuring_3d() is False

    viewer.handle_voice_command("measure distance")
    assert viewer.is_measuring_3d() is True

    viewer.handle_voice_command("hide measurements")
    assert viewer.is_measurements_3d_visible() is False

    viewer.handle_voice_command("show measurements")
    assert viewer.is_measurements_3d_visible() is True

    viewer.handle_voice_command("clear measurements")
    assert len(viewer.get_measurements_3d()) == 0

    # 2. Existing voice commands continue to work
    viewer.handle_voice_command("show heatmap")
    assert viewer._density_active is True

    viewer.handle_voice_command("normal view")
    assert viewer._density_active is False

    viewer.handle_voice_command("half opacity")
    assert pytest.approx(viewer._bone_opacity, 0.01) == 0.5

    viewer.handle_voice_command("enable clipping")
    assert viewer._clip_active is True

    viewer.handle_voice_command("disable clipping")
    assert viewer._clip_active is False

    viewer.handle_voice_command("enable 2d 3d sync")
    assert viewer.is_sync_2d_3d_enabled() is True

    viewer.handle_voice_command("disable 2d 3d sync")
    assert viewer.is_sync_2d_3d_enabled() is False
