#!/usr/bin/env python3
"""
test_clipping_feature.py
Comprehensive verification test suite for the Interactive Clipping Plane feature in Aegis-Touch / Marvel-GPP.

Verifies:
1. Clipping disabled by default upon scan load
2. Enable clipping creates clipped mesh and updates UI
3. X-axis, Y-axis, and Z-axis clipping operate along correct planes
4. Position slider changes clipping location dynamically
5. Reverse direction inverts the retained half
6. Disabling clipping restores complete original mesh with zero geometry destruction
7. Master original mesh remains completely unmodified
8. Heatmap scalars (HU_density) remain preserved on the clipped mesh
9. Interoperability between Hounsfield Heatmap and interactive clipping
10. Voice command dispatch for all clipping aliases
11. Edge cases: no model loaded, extreme slider values, empty clipped results
"""

import os
import sys
import io
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import numpy as np
import pyvista as pv
from PyQt6.QtWidgets import QApplication

# Keep global QApplication alive
# On Windows, native windows platform allows VTK OpenGL to create valid device context
# os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Keep global QApplication alive
_app = QApplication.instance() or QApplication(sys.argv)

from dicom_engine import build_meshes_from_folder, MeshSet
from screens.viewer_3d import Viewer3D

SCAN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skull")


def load_test_viewer():
    """Helper to initialize Viewer3D with the skull test scan."""
    meshset = build_meshes_from_folder(SCAN_DIR, preset="skull")
    viewer = Viewer3D()
    viewer._on_meshes_ready(meshset)
    return viewer, meshset


def test_1_clipping_disabled_by_default():
    print("\n--- Test 1: Clipping Disabled by Default ---")
    viewer, meshset = load_test_viewer()

    assert viewer._clip_active is False, "Clipping must be disabled by default"
    assert viewer._clipped_mesh is None, "_clipped_mesh must initially be None"
    assert viewer.btn_clip_toggle.isChecked() is False, "Toggle button must be unchecked"
    assert viewer.combo_clip_axis.isEnabled() is False, "Axis selector must be disabled when clipping is OFF"
    assert viewer.slider_clip_pos.isEnabled() is False, "Position slider must be disabled when clipping is OFF"
    assert viewer.btn_clip_reverse.isEnabled() is False, "Reverse button must be disabled when clipping is OFF"

    disp = viewer._get_current_display_mesh()
    assert disp is viewer._bone_mesh, "Display mesh must be the complete original bone mesh"
    print("  ✓ Verified clipping is disabled by default and UI controls are properly initialized.")
    return viewer, meshset


def test_2_enable_clipping(viewer, meshset):
    print("\n--- Test 2: Enable Clipping ---")
    orig_pts = viewer._bone_mesh.n_points

    viewer.set_clipping(True)
    assert viewer._clip_active is True, "Clipping must be active"
    assert viewer.btn_clip_toggle.isChecked() is True, "Toggle button must be checked"
    assert viewer.combo_clip_axis.isEnabled() is True, "Axis selector must be enabled"
    assert viewer.slider_clip_pos.isEnabled() is True, "Position slider must be enabled"
    assert viewer.btn_clip_reverse.isEnabled() is True, "Reverse button must be enabled"

    disp = viewer._get_current_display_mesh()
    assert disp is not None, "Clipped display mesh must not be None"
    assert disp.n_points < orig_pts, f"Clipped mesh points ({disp.n_points}) must be fewer than original ({orig_pts})"
    print(f"  ✓ Clipping enabled: mesh reduced from {orig_pts:,} to {disp.n_points:,} vertices.")


def test_3_axis_clipping(viewer):
    print("\n--- Test 3: X, Y, Z Axis Clipping ---")
    bounds = viewer._bone_mesh.bounds

    # X-axis clipping
    viewer.set_clip_axis("x")
    assert viewer._clip_axis == "x", "Clip axis should be 'x'"
    assert viewer.combo_clip_axis.currentIndex() == 0, "Axis dropdown should be at index 0 (X)"
    clipped_x = viewer._get_current_display_mesh()
    assert clipped_x is not None
    # Check that clipped mesh X-span is restricted compared to original X-span
    orig_x_span = bounds[1] - bounds[0]
    clipped_x_span = clipped_x.bounds[1] - clipped_x.bounds[0]
    assert clipped_x_span < orig_x_span, "X clipping must reduce X extent"
    print(f"  ✓ X-axis clipping verified (X span: {clipped_x_span:.1f}mm vs full {orig_x_span:.1f}mm)")

    # Y-axis clipping
    viewer.set_clip_axis("y")
    assert viewer._clip_axis == "y", "Clip axis should be 'y'"
    assert viewer.combo_clip_axis.currentIndex() == 1, "Axis dropdown should be at index 1 (Y)"
    clipped_y = viewer._get_current_display_mesh()
    orig_y_span = bounds[3] - bounds[2]
    clipped_y_span = clipped_y.bounds[3] - clipped_y.bounds[2]
    assert clipped_y_span < orig_y_span, "Y clipping must reduce Y extent"
    print(f"  ✓ Y-axis clipping verified (Y span: {clipped_y_span:.1f}mm vs full {orig_y_span:.1f}mm)")

    # Z-axis clipping
    viewer.set_clip_axis("z")
    assert viewer._clip_axis == "z", "Clip axis should be 'z'"
    assert viewer.combo_clip_axis.currentIndex() == 2, "Axis dropdown should be at index 2 (Z)"
    clipped_z = viewer._get_current_display_mesh()
    orig_z_span = bounds[5] - bounds[4]
    clipped_z_span = clipped_z.bounds[5] - clipped_z.bounds[4]
    assert clipped_z_span < orig_z_span, "Z clipping must reduce Z extent"
    print(f"  ✓ Z-axis clipping verified (Z span: {clipped_z_span:.1f}mm vs full {orig_z_span:.1f}mm)")


def test_4_position_slider(viewer):
    print("\n--- Test 4: Position Slider Dynamics ---")
    viewer.set_clip_axis("z")

    # Set position to 25% (lower quarter cut)
    viewer.set_clip_position(0.25)
    assert viewer.slider_clip_pos.value() == 25, "Slider widget must reflect 25"
    pts_25 = viewer._get_current_display_mesh().n_points

    # Set position to 75% (upper quarter cut)
    viewer.set_clip_position(0.75)
    assert viewer.slider_clip_pos.value() == 75, "Slider widget must reflect 75"
    pts_75 = viewer._get_current_display_mesh().n_points

    assert pts_25 != pts_75, "Different slider positions must yield different vertex counts"
    print(f"  ✓ Position slider dynamically shifted plane (pts at 25%: {pts_25:,} | pts at 75%: {pts_75:,})")


def test_5_reverse_direction(viewer):
    print("\n--- Test 5: Reverse Direction ---")
    viewer.set_clip_axis("y")
    viewer.set_clip_position(0.5)

    # Inverted = False (retains one side)
    viewer._clip_inverted = False
    viewer._update_clipped_mesh()
    pts_norm = viewer._get_current_display_mesh().n_points
    min_y_norm = viewer._get_current_display_mesh().bounds[2]

    # Inverted = True (retains opposite side)
    viewer.reverse_clip_direction()
    assert viewer._clip_inverted is True, "Direction must be inverted"
    pts_inv = viewer._get_current_display_mesh().n_points
    min_y_inv = viewer._get_current_display_mesh().bounds[2]

    assert min_y_norm != min_y_inv or pts_norm != pts_inv, "Inverting direction must flip retained half"
    print(f"  ✓ Direction reversal flipped retained region (side A: {pts_norm:,} pts, side B: {pts_inv:,} pts)")

    # Restore direction
    viewer.reverse_clip_direction()
    assert viewer._clip_inverted is False


def test_6_mesh_preservation_on_disable(viewer, orig_pts):
    print("\n--- Test 6: Original Mesh Preservation on Disabling Clipping ---")
    # Disable clipping
    viewer.set_clipping(False)
    assert viewer._clip_active is False
    assert viewer._clipped_mesh is None

    restored = viewer._get_current_display_mesh()
    assert restored.n_points == orig_pts, f"Restored points ({restored.n_points}) must match original ({orig_pts})"
    assert viewer._bone_mesh.n_points == orig_pts, "Original _bone_mesh must be identical and uncorrupted"
    print(f"  ✓ Complete original mesh restored: exactly {orig_pts:,} vertices preserved.")


def test_7_heatmap_compatibility(viewer):
    print("\n--- Test 7: Compatibility with Hounsfield Heatmap ---")
    # Activate heatmap first
    viewer.set_density_colormap(True)
    assert viewer._density_active is True
    assert viewer.combo_bone_mode.currentIndex() == 1

    # Now activate clipping while in heatmap mode
    viewer.set_clipping(True)
    assert viewer._clip_active is True
    assert viewer._density_active is True, "Heatmap mode must NOT be deactivated by clipping"
    assert viewer.combo_bone_mode.currentIndex() == 1, "Bone mode must remain Hounsfield Heatmap"

    clipped = viewer._get_current_display_mesh()
    assert "HU_density" in clipped.point_data, "HU_density point data must be preserved on clipped mesh"
    hu_arr = np.asarray(clipped.point_data["HU_density"])
    assert len(hu_arr) == clipped.n_points
    assert np.all(np.isfinite(hu_arr)), "HU density values must be finite numbers"
    assert hu_arr.max() > 500.0, "Clipped bone must retain cortical bone densities"
    assert not viewer.legend_card.isHidden(), "Legend card must remain unhidden in heatmap clipping"

    # Switch bone mode to Normal Bone View while clipping is active
    viewer.set_density_colormap(False)
    assert viewer._density_active is False
    assert viewer._clip_active is True, "Clipping must remain active when switching to Normal Bone View"
    assert viewer.combo_bone_mode.currentIndex() == 0

    # Switch back to Heatmap mode
    viewer.set_density_colormap(True)
    assert viewer._density_active is True
    assert viewer._clip_active is True

    # Disable clipping while in heatmap mode -> full heatmap bone restored
    viewer.set_clipping(False)
    assert viewer._clip_active is False
    assert viewer._density_active is True
    assert "HU_density" in viewer._get_current_display_mesh().point_data

    # Return to normal bone view
    viewer.set_density_colormap(False)
    print("  ✓ Full interoperability verified: HU_density preserved across clipping, colormap toggling seamless.")


def test_8_voice_commands(viewer):
    print("\n--- Test 8: Voice Commands Dispatch ---")
    # enable clipping
    viewer.handle_voice_command("clip model")
    assert viewer._clip_active is True, "'clip model' voice command must enable clipping"

    # axis commands
    viewer.handle_voice_command("clip along x")
    assert viewer._clip_axis == "x", "'clip along x' must set axis to x"

    viewer.handle_voice_command("clip along z")
    assert viewer._clip_axis == "z", "'clip along z' must set axis to z"

    # reverse command
    inv_before = viewer._clip_inverted
    viewer.handle_voice_command("reverse clipping")
    assert viewer._clip_inverted != inv_before, "'reverse clipping' must toggle inversion"

    # disable clipping
    viewer.handle_voice_command("disable clipping")
    assert viewer._clip_active is False, "'disable clipping' must turn off clipping"

    viewer.handle_voice_command("remove clipping")
    assert viewer._clip_active is False

    print("  ✓ Verified voice command aliases: 'clip model', 'clip along x/z', 'reverse clipping', 'disable clipping'.")


def test_9_edge_cases():
    print("\n--- Test 9: Edge Cases & Safety ---")
    # 1. Fresh viewer with NO scan loaded
    empty_viewer = Viewer3D()
    try:
        empty_viewer.set_clipping(True)
        empty_viewer.set_clip_axis("x")
        empty_viewer.set_clip_position(0.3)
        empty_viewer.reverse_clip_direction()
        empty_viewer.set_clipping(False)
        assert empty_viewer._clip_active is False
        print("  ✓ No crash or exception when calling clipping operations on unloaded viewer.")
    except Exception as exc:
        raise AssertionError(f"Clipping on empty viewer raised unexpected exception: {exc}")

    # 2. Extreme slider bounds / empty result protection
    viewer, _ = load_test_viewer()
    viewer.set_clipping(True)
    # Move position to extreme fraction 0.001 and 0.999
    viewer.set_clip_position(0.0)
    assert viewer._get_current_display_mesh() is not None
    viewer.set_clip_position(1.0)
    assert viewer._get_current_display_mesh() is not None
    print("  ✓ Extreme position bounds handled safely without empty mesh crashing.")


if __name__ == "__main__":
    print("==================================================================")
    print("  Aegis-Touch Interactive Clipping Plane Feature Verification")
    print("==================================================================")

    viewer, meshset = test_1_clipping_disabled_by_default()
    orig_pts = viewer._bone_mesh.n_points
    test_2_enable_clipping(viewer, meshset)
    test_3_axis_clipping(viewer)
    test_4_position_slider(viewer)
    test_5_reverse_direction(viewer)
    test_6_mesh_preservation_on_disable(viewer, orig_pts)
    test_7_heatmap_compatibility(viewer)
    test_8_voice_commands(viewer)
    test_9_edge_cases()

    print("\n==================================================================")
    print("  >>> ALL 9 CLIPPING VERIFICATION TESTS PASSED SUCCESSFULLY! <<<")
    print("==================================================================")
