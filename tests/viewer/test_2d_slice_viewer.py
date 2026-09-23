# test_2d_slice_viewer.py
"""
Automated unit and integration tests for the interactive 2D CT slice viewer in Aegis-Touch.

Verifies all 15 requirements:
  1. 2D viewer initializes safely.
  2. No crash when no scan is loaded.
  3. Axial orientation displays correctly.
  4. Coronal orientation displays correctly.
  5. Sagittal orientation displays correctly.
  6. Slice slider stays within actual volume bounds.
  7. Slice changes update the displayed image.
  8. HU values are derived from the calibrated volume.
  9. Window/Level changes affect display correctly.
  10. Bone/Soft Tissue/Lung presets dispatch correctly.
  11. Existing 3D viewer continues working.
  12. Existing heatmap continues working.
  13. Existing clipping continues working.
  14. Existing opacity control continues working.
  15. Existing voice commands continue working.
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
import pyvista as pv
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

# Set headless offscreen environment for testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from screens.slice_2d_viewer import Slice2DViewerWidget, CTSliceCanvas, WL_PRESETS
from screens.viewer_3d import Viewer3D
from dicom_engine import MeshSet


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def synthetic_volume():
    """Generates a synthetic 3D CT volume (H=40, W=50, D=30) with realistic HU values."""
    H, W, D = 40, 50, 30
    vol = np.full((H, W, D), -1000.0, dtype=np.float32)  # air
    vol[10:30, 15:35, 5:25] = 40.0                       # soft tissue
    vol[15:25, 20:30, 10:20] = 1200.0                    # cortical bone
    pixel_spacing = (0.8, 0.8)                           # (dy, dx) in mm
    slice_thickness = 1.5                                # dz in mm
    return vol, pixel_spacing, slice_thickness


# ── Test 1: 2D viewer initializes safely ─────────────────────────────────────
def test_1_viewer_initializes_safely(qapp):
    viewer = Slice2DViewerWidget()
    assert viewer is not None
    assert viewer.vol_data is None
    assert viewer.current_orientation == "axial"
    assert viewer.window_width == 1800.0
    assert viewer.window_level == 400.0
    assert viewer.slider_slice.maximum() == 0
    assert viewer.lbl_slice_info.text() == "0 / 0"


# ── Test 2: No crash when no scan is loaded ──────────────────────────────────
def test_2_no_crash_when_no_scan_loaded(qapp):
    viewer = Slice2DViewerWidget()
    # Exercising interactive operations without volume data
    viewer.set_orientation("coronal")
    viewer.set_orientation("sagittal")
    viewer.step_slice(1)
    viewer.step_slice(-1)
    viewer.set_slice_index(10)
    viewer.go_to_slice(5)
    viewer.set_window_preset("Lung")
    viewer.set_window_level(1000, 200)

    # Hover and query
    assert viewer.get_current_hu_at(10, 10) is None
    assert viewer.get_slice_count() == 0
    assert viewer.canvas._pixmap is None


# ── Test 3: Axial orientation displays correctly ─────────────────────────────
def test_3_axial_orientation_displays_correctly(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    H, W, D = vol.shape
    viewer = Slice2DViewerWidget()
    viewer.load_volume(vol, sp, st)

    viewer.set_orientation("axial")
    assert viewer.current_orientation == "axial"
    assert viewer.get_slice_count() == D

    raw, mm_x, mm_y = viewer._extract_raw_slice()
    assert raw is not None
    assert raw.shape == (H, W)
    assert mm_x == pytest.approx(sp[1])
    assert mm_y == pytest.approx(sp[0])
    assert viewer.canvas._pixmap is not None
    assert viewer.canvas.plane_name == "axial"


# ── Test 4: Coronal orientation displays correctly ───────────────────────────
def test_4_coronal_orientation_displays_correctly(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    H, W, D = vol.shape
    viewer = Slice2DViewerWidget()
    viewer.load_volume(vol, sp, st)

    viewer.set_orientation("coronal")
    assert viewer.current_orientation == "coronal"
    assert viewer.get_slice_count() == H

    raw, mm_x, mm_y = viewer._extract_raw_slice()
    assert raw is not None
    assert raw.shape == (D, W)
    assert mm_x == pytest.approx(sp[1])
    assert mm_y == pytest.approx(st)
    assert viewer.canvas._pixmap is not None
    assert viewer.canvas.plane_name == "coronal"


# ── Test 5: Sagittal orientation displays correctly ──────────────────────────
def test_5_sagittal_orientation_displays_correctly(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    H, W, D = vol.shape
    viewer = Slice2DViewerWidget()
    viewer.load_volume(vol, sp, st)

    viewer.set_orientation("sagittal")
    assert viewer.current_orientation == "sagittal"
    assert viewer.get_slice_count() == W

    raw, mm_x, mm_y = viewer._extract_raw_slice()
    assert raw is not None
    assert raw.shape == (D, H)
    assert mm_x == pytest.approx(sp[0])
    assert mm_y == pytest.approx(st)
    assert viewer.canvas._pixmap is not None
    assert viewer.canvas.plane_name == "sagittal"


# ── Test 6: Slice slider stays within actual volume bounds ───────────────────
def test_6_slice_slider_bounds(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    H, W, D = vol.shape
    viewer = Slice2DViewerWidget()
    viewer.load_volume(vol, sp, st)

    # Axial range: 0..D-1 (0..29)
    viewer.set_orientation("axial")
    assert viewer.slider_slice.minimum() == 0
    assert viewer.slider_slice.maximum() == D - 1

    # Clamping tests
    viewer.set_slice_index(-5)
    assert viewer.get_current_slice_index() == 0
    viewer.set_slice_index(999)
    assert viewer.get_current_slice_index() == D - 1

    # Coronal range: 0..H-1 (0..39)
    viewer.set_orientation("coronal")
    assert viewer.slider_slice.maximum() == H - 1
    viewer.set_slice_index(999)
    assert viewer.get_current_slice_index() == H - 1

    # Sagittal range: 0..W-1 (0..49)
    viewer.set_orientation("sagittal")
    assert viewer.slider_slice.maximum() == W - 1
    viewer.set_slice_index(999)
    assert viewer.get_current_slice_index() == W - 1


# ── Test 7: Slice changes update the displayed image ─────────────────────────
def test_7_slice_changes_update_image(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = Slice2DViewerWidget()
    viewer.load_volume(vol, sp, st)

    # Slice at z=0 is pure air (-1000 HU)
    viewer.set_orientation("axial")
    viewer.set_slice_index(0)
    raw_0 = viewer.canvas._raw_slice.copy()

    # Slice at z=15 cuts through dense bone (1200 HU)
    viewer.set_slice_index(15)
    raw_15 = viewer.canvas._raw_slice.copy()

    assert not np.array_equal(raw_0, raw_15)
    assert np.max(raw_15) == pytest.approx(1200.0)


# ── Test 8: HU values are derived from calibrated volume ─────────────────────
def test_8_hu_values_derived_from_calibrated_volume(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = Slice2DViewerWidget()
    viewer.load_volume(vol, sp, st)

    viewer.set_orientation("axial")
    viewer.set_slice_index(15)

    # Voxel (y=20, x=25, z=15) is set to 1200 HU
    hu_bone = viewer.get_current_hu_at(px=25, py=20)
    assert hu_bone == pytest.approx(1200.0)

    # Voxel (y=0, x=0, z=15) is outside bone, in air (-1000 HU)
    hu_air = viewer.get_current_hu_at(px=0, py=0)
    assert hu_air == pytest.approx(-1000.0)

    # Test canvas hover emission
    hover_received = []
    viewer.canvas.pixel_hovered.connect(lambda x, y, hu: hover_received.append((x, y, hu)))
    viewer.canvas.pixel_hovered.emit(25, 20, 1200.0)
    assert len(hover_received) == 1
    assert hover_received[0] == (25, 20, 1200.0)
    assert "HU: +1200" in viewer.lbl_cursor_info.text()


# ── Test 9: Window/Level changes affect display correctly ────────────────────
def test_9_window_level_changes_affect_display(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = Slice2DViewerWidget()
    viewer.load_volume(vol, sp, st)
    viewer.set_orientation("axial")
    viewer.set_slice_index(15)

    # Render with Bone window (W=1800, L=400)
    viewer.set_window_level(1800, 400)
    lut_bone = viewer._wl_lut.copy()

    # Render with Soft Tissue window (W=400, L=40)
    viewer.set_window_level(400, 40)
    lut_soft = viewer._wl_lut.copy()

    # The lookup tables must differ significantly
    assert not np.array_equal(lut_bone, lut_soft)
    # At HU=40: soft tissue center (L=40) should map to ~128 (mid-gray)
    hu_idx = 40 + 1024
    assert abs(int(lut_soft[hu_idx]) - 128) <= 2


# ── Test 10: Bone/Soft Tissue/Lung presets dispatch correctly ────────────────
def test_10_presets_dispatch_correctly(qapp, synthetic_volume):
    viewer = Slice2DViewerWidget()
    viewer.load_volume(*synthetic_volume)

    # Bone Preset
    viewer.set_window_preset("Bone")
    assert viewer.window_width == 1800.0
    assert viewer.window_level == 400.0
    assert viewer.btn_wl_bone.isChecked()

    # Soft Tissue Preset
    viewer.set_window_preset("Soft Tissue")
    assert viewer.window_width == 400.0
    assert viewer.window_level == 40.0
    assert viewer.btn_wl_soft.isChecked()

    # Lung Preset
    viewer.set_window_preset("Lung")
    assert viewer.window_width == 1500.0
    assert viewer.window_level == -600.0
    assert viewer.btn_wl_lung.isChecked()


# ── Helper for Viewer3D Tests ────────────────────────────────────────────────
def _setup_viewer3d_with_mock_mesh():
    viewer = Viewer3D()
    sphere = pv.Sphere(radius=20.0, center=(0, 0, 0))
    sphere.point_data["HU_density"] = np.linspace(200.0, 1600.0, sphere.n_points)

    class MockMeshSet:
        bone_mesh = sphere
        bone_isovalue = 400.0

        @staticmethod
        def has_hu_density():
            return True

        @staticmethod
        def get_hu_range():
            return (200.0, 1600.0)

        @staticmethod
        def add_to_plotter(plotter, density_mode=False, cmap="turbo"):
            return plotter.add_mesh(sphere, color="#e8c87a", name="bone"), None

    meshset = MockMeshSet()
    viewer._on_meshes_ready(meshset)
    return viewer


# ── Test 11: Existing 3D viewer continues working ────────────────────────────
def test_11_existing_3d_viewer_continues_working(qapp):
    viewer = _setup_viewer3d_with_mock_mesh()
    assert viewer.plotter is not None
    assert viewer.bone_actor is not None
    assert hasattr(viewer, "panel_2d")
    assert hasattr(viewer, "btn_toggle_2d")

    # Verify 2D panel can be shown alongside 3D viewer
    viewer.set_2d_panel_visible(True)
    assert not viewer.panel_2d.isHidden()
    assert viewer.btn_toggle_2d.isChecked()

    # 3D camera remains operational
    cam = viewer.plotter.renderer.GetActiveCamera()
    pos_before = cam.GetPosition()
    viewer.snap_to_view("posterior")
    pos_after = cam.GetPosition()
    assert pos_before != pos_after

    viewer.set_2d_panel_visible(False)
    assert viewer.panel_2d.isHidden()
    assert not viewer.btn_toggle_2d.isChecked()


# ── Test 12: Existing heatmap continues working ──────────────────────────────
def test_12_existing_heatmap_continues_working(qapp):
    viewer = _setup_viewer3d_with_mock_mesh()
    # Toggle 2D on
    viewer.set_2d_panel_visible(True)

    # Enable heatmap
    viewer.set_density_colormap(True)
    assert viewer._density_active is True
    mesh = viewer._get_current_display_mesh()
    assert "HU_density" in mesh.point_data

    # Return to normal view
    viewer.set_density_colormap(False)
    assert viewer._density_active is False


# ── Test 13: Existing clipping continues working ─────────────────────────────
def test_13_existing_clipping_continues_working(qapp):
    viewer = _setup_viewer3d_with_mock_mesh()
    viewer.set_2d_panel_visible(True)

    # Enable clipping along Y axis
    viewer.set_clipping(True)
    viewer.set_clip_axis("y")
    viewer.set_clip_position(0.5)

    assert viewer._clip_active is True
    clipped = viewer._clipped_mesh
    assert clipped is not None
    assert clipped.n_points < viewer._original_mesh.n_points

    # Disable clipping restores full master mesh
    viewer.set_clipping(False)
    assert viewer._get_current_display_mesh().n_points == viewer._original_mesh.n_points


# ── Test 14: Existing opacity control continues working ──────────────────────
def test_14_existing_opacity_continues_working(qapp):
    viewer = _setup_viewer3d_with_mock_mesh()
    viewer.set_2d_panel_visible(True)

    viewer.set_bone_opacity(0.45)
    assert viewer._bone_opacity == pytest.approx(0.45)
    assert viewer.lbl_opacity.text() == "45%"
    assert viewer.bone_actor.prop.opacity == pytest.approx(0.45)

    # Clamping
    viewer.set_bone_opacity(0.0)
    assert viewer._bone_opacity == pytest.approx(0.1)
    viewer.set_bone_opacity(1.5)
    assert viewer._bone_opacity == pytest.approx(1.0)


# ── Test 15: Existing and new voice commands continue working ────────────────
def test_15_voice_commands_continue_working(qapp, synthetic_volume):
    viewer = _setup_viewer3d_with_mock_mesh()
    vol, sp, st = synthetic_volume
    viewer.panel_2d.load_volume(vol, sp, st)

    # 1. 2D panel toggle voice commands
    viewer.handle_voice_command("show 2d")
    assert not viewer.panel_2d.isHidden()

    viewer.handle_voice_command("show coronal")
    assert viewer.panel_2d.current_orientation == "coronal"

    viewer.handle_voice_command("show sagittal")
    assert viewer.panel_2d.current_orientation == "sagittal"

    viewer.handle_voice_command("show axial")
    assert viewer.panel_2d.current_orientation == "axial"

    viewer.handle_voice_command("lung window")
    assert viewer.panel_2d.window_width == 1500.0
    assert viewer.panel_2d.window_level == -600.0

    viewer.handle_voice_command("bone window")
    assert viewer.panel_2d.window_width == 1800.0
    assert viewer.panel_2d.window_level == 400.0

    viewer.handle_voice_command("go to slice 12")
    assert viewer.panel_2d.get_current_slice_index() == 11

    viewer.handle_voice_command("next slice")
    assert viewer.panel_2d.get_current_slice_index() == 12

    viewer.handle_voice_command("previous slice")
    assert viewer.panel_2d.get_current_slice_index() == 11

    viewer.handle_voice_command("hide 2d")
    assert viewer.panel_2d.isHidden()

    # 2. Existing 3D voice commands still work without collision
    viewer.handle_voice_command("enable clipping")
    assert viewer._clip_active is True

    viewer.handle_voice_command("clip along z")
    assert viewer._clip_axis == "z"

    viewer.handle_voice_command("half opacity")
    assert viewer._bone_opacity == pytest.approx(0.5)

    viewer.handle_voice_command("show density")
    assert viewer._density_active is True

    viewer.handle_voice_command("normal view")
    assert viewer._density_active is False

    viewer.handle_voice_command("anterior")
    # Camera moved to anterior view successfully without errors
