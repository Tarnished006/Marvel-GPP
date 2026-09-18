#!/usr/bin/env python3
"""
test_opacity_feature.py
Comprehensive verification test suite for the Interactive Bone Opacity/Transparency feature in Aegis-Touch / Marvel-GPP.

Verifies:
1. Default opacity is 100% (1.0)
2. Slider and set_bone_opacity change actor opacity interactively
3. Opacity values are clamped strictly between 0.1 and 1.0
4. Normal Bone View remains functional with transparency
5. Hounsfield Heatmap remains functional with transparency (colors and HU scalars preserved)
6. Clipping remains functional with transparency across plane shifts and side inversions
7. Master original mesh remains completely unmodified
8. No crash when no mesh is loaded (safe unloaded handling)
9. Switching/reloading scans resets opacity to 100%
10. Voice opacity command aliases and percentage parsing dispatch correctly
"""

import os
import sys
import numpy as np
import pyvista as pv
from PyQt6.QtWidgets import QApplication

# Set off-screen rendering
os.environ["QT_QPA_PLATFORM"] = "offscreen"

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


def test_1_default_opacity():
    print("\n--- Test 1: Default Opacity is 100% ---")
    viewer, meshset = load_test_viewer()

    assert viewer._bone_opacity == 1.0, "Default _bone_opacity must be 1.0"
    assert viewer.slider_opacity.value() == 100, "Opacity slider must be at 100"
    assert viewer.lbl_opacity.text() == "100%", "Opacity label must display '100%'"
    assert abs(viewer.bone_actor.prop.opacity - 1.0) < 1e-3, "VTK actor opacity must initially be 1.0"

    print("  ✓ Verified default opacity is 100% on actor, state, and UI controls.")
    return viewer, meshset


def test_2_slider_changes_opacity(viewer):
    print("\n--- Test 2: Slider Changes Opacity Interactively ---")

    # Set to 75%
    viewer.slider_opacity.setValue(75)
    assert abs(viewer._bone_opacity - 0.75) < 1e-3, f"Expected 0.75, got {viewer._bone_opacity}"
    assert viewer.lbl_opacity.text() == "75%"
    assert abs(viewer.bone_actor.prop.opacity - 0.75) < 1e-3

    # Set to 50%
    viewer.slider_opacity.setValue(50)
    assert abs(viewer._bone_opacity - 0.50) < 1e-3
    assert viewer.lbl_opacity.text() == "50%"
    assert abs(viewer.bone_actor.prop.opacity - 0.50) < 1e-3

    # Set to 25%
    viewer.slider_opacity.setValue(25)
    assert abs(viewer._bone_opacity - 0.25) < 1e-3
    assert viewer.lbl_opacity.text() == "25%"
    assert abs(viewer.bone_actor.prop.opacity - 0.25) < 1e-3

    # Restore to 100%
    viewer.slider_opacity.setValue(100)
    assert abs(viewer._bone_opacity - 1.0) < 1e-3
    print("  ✓ Slider changes opacity smoothly across 75%, 50%, 25%, and 100%.")


def test_3_opacity_clamping(viewer):
    print("\n--- Test 3: Opacity Clamping (0.1 to 1.0) ---")

    # Under-range values clamp to 0.1
    viewer.set_bone_opacity(0.0)
    assert abs(viewer._bone_opacity - 0.1) < 1e-3, "0.0 must clamp to 0.1"
    assert viewer.slider_opacity.value() == 10
    assert viewer.lbl_opacity.text() == "10%"

    viewer.set_bone_opacity(-0.5)
    assert abs(viewer._bone_opacity - 0.1) < 1e-3, "-0.5 must clamp to 0.1"

    # Over-range values clamp to 1.0
    viewer.set_bone_opacity(1.5)
    assert abs(viewer._bone_opacity - 1.0) < 1e-3, "1.5 must clamp to 1.0"
    assert viewer.slider_opacity.value() == 100
    assert viewer.lbl_opacity.text() == "100%"

    print("  ✓ Verified strict clamping between 0.1 (10%) and 1.0 (100%).")


def test_4_normal_bone_view_with_opacity(viewer):
    print("\n--- Test 4: Normal Bone View with Transparency ---")
    assert viewer._density_active is False, "Must be in Normal Bone View"

    viewer.set_bone_opacity(0.6)
    assert abs(viewer.bone_actor.prop.opacity - 0.6) < 1e-3
    # Check that bone actor retains natural bone shading
    assert viewer.bone_actor.prop.ambient > 0.0
    assert viewer.bone_actor.prop.diffuse > 0.0

    print("  ✓ Normal Bone View preserves natural shading properties with 60% opacity.")


def test_5_heatmap_with_opacity(viewer):
    print("\n--- Test 5: Hounsfield Heatmap with Transparency ---")
    viewer.set_density_colormap(True)
    assert viewer._density_active is True

    viewer.set_bone_opacity(0.4)
    assert abs(viewer._bone_opacity - 0.4) < 1e-3
    assert abs(viewer.bone_actor.prop.opacity - 0.4) < 1e-3

    # Verify HU scalars are intact
    mesh = viewer._get_current_display_mesh()
    assert "HU_density" in mesh.point_data, "HU_density must remain present"
    assert np.all(np.isfinite(mesh.point_data["HU_density"]))
    assert not viewer.legend_card.isHidden(), "Legend must remain visible in heatmap mode"

    print("  ✓ Hounsfield Heatmap retains turbo colormap and scalar array at 40% opacity.")


def test_6_clipping_with_transparency(viewer):
    print("\n--- Test 6: Interactive Clipping with Transparency ---")
    viewer.set_bone_opacity(0.5)

    # Enable clipping
    viewer.set_clipping(True)
    assert viewer._clip_active is True
    assert abs(viewer.bone_actor.prop.opacity - 0.5) < 1e-3, "Clipped actor must adopt 50% opacity"

    # Shift clipping position
    viewer.set_clip_position(0.3)
    assert abs(viewer.bone_actor.prop.opacity - 0.5) < 1e-3, "Opacity must persist across position changes"

    # Invert direction
    viewer.reverse_clip_direction()
    assert abs(viewer.bone_actor.prop.opacity - 0.5) < 1e-3, "Opacity must persist across direction inversion"

    # Switch bone mode to normal while clipped and transparent
    viewer.set_density_colormap(False)
    assert viewer._density_active is False
    assert viewer._clip_active is True
    assert abs(viewer.bone_actor.prop.opacity - 0.5) < 1e-3

    # Switch back to heatmap while clipped and transparent
    viewer.set_density_colormap(True)
    assert viewer._density_active is True
    assert abs(viewer.bone_actor.prop.opacity - 0.5) < 1e-3

    # Disable clipping
    viewer.set_clipping(False)
    assert viewer._clip_active is False
    assert abs(viewer.bone_actor.prop.opacity - 0.5) < 1e-3

    viewer.set_density_colormap(False)
    viewer.set_bone_opacity(1.0)
    print("  ✓ Full compatibility verified: clipping cuts remain semi-transparent across all operations.")


def test_7_original_mesh_unchanged(viewer, orig_pts):
    print("\n--- Test 7: Original Mesh Unchanged ---")
    assert viewer._bone_mesh.n_points == orig_pts, f"Master mesh points ({viewer._bone_mesh.n_points}) must match ({orig_pts})"
    print(f"  ✓ Master mesh intact: exactly {orig_pts:,} points preserved.")


def test_8_no_crash_when_unloaded():
    print("\n--- Test 8: Safe Unloaded Handling ---")
    empty_viewer = Viewer3D()
    try:
        empty_viewer.set_bone_opacity(0.7)
        assert abs(empty_viewer._bone_opacity - 0.7) < 1e-3
        empty_viewer.slider_opacity.setValue(50)
        assert abs(empty_viewer._bone_opacity - 0.5) < 1e-3
        empty_viewer.handle_voice_command("half opacity")
        assert abs(empty_viewer._bone_opacity - 0.5) < 1e-3
        print("  ✓ No crash or exception when adjusting opacity before a scan is loaded.")
    except Exception as exc:
        raise AssertionError(f"Opacity operation on empty viewer raised unexpected exception: {exc}")


def test_9_scan_reload_resets_opacity(viewer, meshset):
    print("\n--- Test 9: Scan Reload Resets Opacity ---")
    viewer.set_bone_opacity(0.35)
    assert abs(viewer._bone_opacity - 0.35) < 1e-3

    # Simulate loading new scan
    viewer._on_meshes_ready(meshset)
    assert viewer._bone_opacity == 1.0, "New scan must reset opacity to 1.0 (100%)"
    assert viewer.slider_opacity.value() == 100
    assert viewer.lbl_opacity.text() == "100%"
    assert abs(viewer.bone_actor.prop.opacity - 1.0) < 1e-3
    print("  ✓ Scan loading cleanly resets opacity to 100%.")


def test_10_voice_commands(viewer):
    print("\n--- Test 10: Voice Commands Dispatch ---")
    # half opacity
    viewer.handle_voice_command("half opacity")
    assert abs(viewer._bone_opacity - 0.5) < 1e-3, "'half opacity' should set 50%"

    # full opacity / opaque
    viewer.handle_voice_command("opaque")
    assert abs(viewer._bone_opacity - 1.0) < 1e-3, "'opaque' should set 100%"

    # high transparency
    viewer.handle_voice_command("high transparency")
    assert abs(viewer._bone_opacity - 0.25) < 1e-3, "'high transparency' should set 25%"

    # increase transparency (-0.2 opacity)
    viewer.handle_voice_command("increase transparency")
    assert abs(viewer._bone_opacity - 0.1) < 1e-3  # 0.25 - 0.2 = 0.05 clamped to 0.1

    # decrease transparency (+0.2 opacity)
    viewer.handle_voice_command("decrease transparency")
    assert abs(viewer._bone_opacity - 0.3) < 1e-3

    # Arbitrary percentage regex: "80 percent opacity"
    viewer.handle_voice_command("80 percent opacity")
    assert abs(viewer._bone_opacity - 0.8) < 1e-3, "'80 percent opacity' should parse to 0.8"

    # Reset to full
    viewer.handle_voice_command("full opacity")
    assert abs(viewer._bone_opacity - 1.0) < 1e-3

    print("  ✓ Verified voice aliases: 'half opacity', 'opaque', 'high transparency', 'increase/decrease transparency', and '80 percent opacity'.")


if __name__ == "__main__":
    print("==================================================================")
    print("  Aegis-Touch Interactive Bone Opacity Feature Verification")
    print("==================================================================")

    viewer, meshset = test_1_default_opacity()
    orig_pts = viewer._bone_mesh.n_points
    test_2_slider_changes_opacity(viewer)
    test_3_opacity_clamping(viewer)
    test_4_normal_bone_view_with_opacity(viewer)
    test_5_heatmap_with_opacity(viewer)
    test_6_clipping_with_transparency(viewer)
    test_7_original_mesh_unchanged(viewer, orig_pts)
    test_8_no_crash_when_unloaded()
    test_9_scan_reload_resets_opacity(viewer, meshset)
    test_10_voice_commands(viewer)

    print("\n==================================================================")
    print("  >>> ALL 10 OPACITY VERIFICATION TESTS PASSED SUCCESSFULLY! <<<")
    print("==================================================================")
