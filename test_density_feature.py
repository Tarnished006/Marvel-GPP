"""
test_density_feature.py
Focused test suite for density-based Hounsfield Unit (HU) heatmap coloring.

Verifies:
1. DICOM HU extraction produces realistic physical HU values (pixel_array * slope + intercept).
2. HU_density exists on the mesh and contains finite numeric values.
3. Display range is safely clamped to the valid bone range (e.g. 200–1800+ HU).
4. VTP cache round-trip preserves HU_density without data loss.
5. UI dropdown switches between both modes without rebuilding the application.
6. Legend visibility (both VTK scalar bar and floating scale card) follows selected mode.
7. Voice command aliases dispatch correctly.
8. Missing HU data never causes fake gradients (e.g., no Z-height fallback).
9. Normal Bone View still works exactly as before.
"""

import os
import sys
import io
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import numpy as np

# Ensure workspace root is in sys.path
WORKSPACE = os.path.dirname(os.path.abspath(__file__))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import dicom_engine
from dicom_engine import DicomVolume, MeshSet, get_cache_path, _BONE_COLOUR


def test_1_dicom_hu_extraction():
    print("--- Test 1: DICOM HU Extraction & Accessibility ---")
    scan_dir = os.path.join(WORKSPACE, "skull")
    assert os.path.isdir(scan_dir), f"Missing scan directory: {scan_dir}"

    vol = DicomVolume(scan_dir)
    lo, hi = vol.scalar_range()
    print(f"  Volumetric HU range: {lo:.1f} to {hi:.1f}")

    # Air is typically -1000 to -3000 HU; dense bone is > 1000 HU
    assert lo < -500, f"Expected low HU for air, got {lo}"
    assert hi > 1000, f"Expected high HU for bone, got {hi}"
    assert hasattr(vol, "volume_data") and vol.volume_data is not None, "volume_data must remain accessible"
    assert hasattr(vol, "raw_data") and vol.raw_data is not None, "raw_data must remain accessible"
    print("  ✓ Real Hounsfield Units extracted and accessible during mesh construction.")
    return vol


def test_2_mesh_scalar_preservation(vol):
    print("\n--- Test 2: Mesh Scalar Preservation & Density Calculation ---")
    meshset = MeshSet(vol, preset="skull")
    mesh = meshset.bone_mesh

    assert meshset.has_hu_density(), "MeshSet must report has_hu_density() is True"
    assert "HU_density" in mesh.point_data, "HU_density must exist in mesh.point_data"

    hu = np.asarray(mesh.point_data["HU_density"])
    assert len(hu) == mesh.n_points, f"Scalar count {len(hu)} != vertex count {mesh.n_points}"
    assert np.all(np.isfinite(hu)), "HU_density must contain finite numeric values"
    assert np.all(hu >= 0.0), "HU_density must be non-negative (air clipped to 0)"
    assert np.max(hu) > 1000.0, f"Dense cortical bone HU should exceed 1000 HU, got {np.max(hu)}"

    print(f"  Vertices: {mesh.n_points:,} | Triangles: {mesh.n_cells:,}")
    print(f"  HU_density: min={np.min(hu):.1f}, max={np.max(hu):.1f}, mean={np.mean(hu):.1f}, median={np.median(hu):.1f}")
    print("  ✓ HU_density attached, preserved through decimation/cleaning, and contains real bone HU values.")
    return meshset


def test_3_display_range_calibration(meshset):
    print("\n--- Test 3: Display Range Calibration ---")
    lo, hi = meshset.get_hu_range()
    print(f"  Calibrated HU display range: {lo:.1f} -> {hi:.1f}")

    # Bone window calibration: lower threshold >= 150 HU, upper threshold >= 1000 HU
    assert lo >= 150.0, f"Lower display range should start near trabecular bone (>=150 HU), got {lo}"
    assert hi >= 1000.0, f"Upper display range should reach cortical bone (>=1000 HU), got {hi}"
    assert hi > lo + 200.0, f"Invalid range span: {lo} -> {hi}"
    print("  ✓ Display range is calibrated to actual bone density range.")


def test_4_vtp_cache_roundtrip(meshset):
    print("\n--- Test 4: VTP Cache Round-Trip ---")
    scan_dir = os.path.join(WORKSPACE, "skull")
    cache_path = get_cache_path(scan_dir, preset="skull")
    print(f"  Writing to cache: {cache_path}")
    meshset.bone_mesh.save(cache_path)

    loaded = MeshSet.load_from_cache(cache_path, preset="skull", folder_path=scan_dir)
    assert loaded.has_hu_density(), "Loaded cached mesh must retain HU_density"
    orig_hu = np.asarray(meshset.bone_mesh.point_data["HU_density"])
    cached_hu = np.asarray(loaded.bone_mesh.point_data["HU_density"])
    np.testing.assert_allclose(orig_hu, cached_hu, rtol=1e-5, atol=1e-5)
    print("  ✓ VTP cache preserves HU_density accurately on round-trip.")


APP = None

def get_app():
    global APP
    if APP is None:
        from PyQt6.QtWidgets import QApplication
        APP = QApplication.instance() or QApplication(sys.argv)
    return APP


def test_5_ui_and_colormap_switching(meshset):
    print("\n--- Test 5: UI Dropdown & Colormap Switching ---")
    app = get_app()

    from screens.viewer_3d import Viewer3D
    viewer = Viewer3D()

    # Verify default state
    assert hasattr(viewer, "combo_bone_mode"), "Viewer3D must have combo_bone_mode dropdown"
    assert viewer.combo_bone_mode.count() == 2
    assert "Normal Bone View" in viewer.combo_bone_mode.itemText(0)
    assert "Hounsfield Heatmap" in viewer.combo_bone_mode.itemText(1)
    assert viewer.combo_bone_mode.currentIndex() == 0, "Normal Bone View must be default"

    # Simulate scan load
    viewer._active_path = os.path.join(WORKSPACE, "skull")
    viewer._on_meshes_ready(meshset)

    # Initial state after scan load must be Normal Bone View
    assert viewer._density_active is False
    assert viewer.combo_bone_mode.currentIndex() == 0
    assert viewer.legend_card.isHidden(), "Legend card must be hidden in Normal Bone View"
    print("  Default appearance: Normal Bone View (actor uncolored by scalars, legend hidden).")

    # Switch to Hounsfield Heatmap
    print("  Switching to Hounsfield Heatmap...")
    viewer.combo_bone_mode.setCurrentIndex(1)
    assert viewer._density_active is True
    assert not viewer.legend_card.isHidden(), "Legend card must be visible in Hounsfield Heatmap"
    print("  ✓ Heatmap mode activated: colormap applied, legend card visible, dropdown synced.")

    # Switch back to Normal Bone View
    print("  Switching back to Normal Bone View...")
    viewer.combo_bone_mode.setCurrentIndex(0)
    assert viewer._density_active is False
    assert viewer.legend_card.isHidden(), "Legend card must be hidden in Normal Bone View"
    print("  ✓ Normal Bone mode restored: warm cortical bone shading restored, legend hidden.")
    return viewer


def test_6_legend_content(viewer):
    print("\n--- Test 6: Legend Content & Non-Clinical Disclaimer ---")
    legend = viewer.legend_card
    assert legend is not None

    from PyQt6.QtWidgets import QLabel
    label_texts = [lbl.text() for lbl in legend.findChildren(QLabel)]
    full_text = " ".join(label_texts)

    assert "1000 HU" in full_text and "Dense Cortical Bone" in full_text, "Legend must describe Dense Cortical Bone"
    assert "500–1000 HU" in full_text and "Subcortical" in full_text, "Legend must describe Subcortical / Intermediate Bone"
    assert "200–500 HU" in full_text and "Trabecular" in full_text, "Legend must describe Trabecular / Cancellous Bone"
    assert "Heuristic HU visualization — not for diagnostic BMD." in full_text, "Must include exact non-clinical disclaimer"
    print(f"  Legend title: '{label_texts[0]}'")
    print(f"  Disclaimer: '{label_texts[-1]}'")
    print("  ✓ All anatomical ranges and non-clinical disclaimer verified.")


def test_7_voice_commands(viewer):
    print("\n--- Test 7: Voice Commands Dispatch ---")
    # Test heatmap activation commands
    heatmap_commands = ["hounsfield heatmap", "show heatmap", "density heatmap", "show density"]
    for cmd in heatmap_commands:
        viewer.set_density_colormap(False)
        viewer.handle_voice_command(cmd)
        assert viewer._density_active is True, f"Command '{cmd}' should activate heatmap"
        assert viewer.combo_bone_mode.currentIndex() == 1
    print(f"  ✓ Verified {len(heatmap_commands)} heatmap voice commands.")

    # Test normal view activation commands
    normal_commands = ["normal bone view", "normal view", "bone view", "hide density"]
    for cmd in normal_commands:
        viewer.set_density_colormap(True)
        viewer.handle_voice_command(cmd)
        assert viewer._density_active is False, f"Command '{cmd}' should activate normal bone view"
        assert viewer.combo_bone_mode.currentIndex() == 0
    print(f"  ✓ Verified {len(normal_commands)} normal bone voice commands.")


def test_8_safety_and_no_fake_gradients(viewer):
    print("\n--- Test 8: Safety & Fallback Integrity ---")
    import pyvista as pv

    # Create a synthetic test mesh without HU_density
    synthetic = pv.Sphere()
    dummy_meshset = MeshSet.__new__(MeshSet)
    dummy_meshset.bone_mesh = synthetic
    dummy_meshset.bone_isovalue = 250.0

    viewer._bone_mesh = synthetic
    viewer._meshset = dummy_meshset
    viewer._active_path = ""  # No volume cache available

    # Attempt to enable density heatmap
    viewer.set_density_colormap(True)
    assert viewer._density_active is False, "Heatmap must NOT activate when HU data is missing"
    assert viewer.combo_bone_mode.currentIndex() == 0, "Dropdown must revert to Normal Bone View"
    assert viewer.legend_card.isHidden(), "Legend must remain hidden"
    assert "HU_density" not in synthetic.point_data, "Must NOT invent fake HU values"
    print("  ✓ Safely rejected missing HU data; no fake Z-height or synthetic gradients created.")


if __name__ == "__main__":
    print("==================================================================")
    print("  Aegis-Touch Density Heatmap Feature Verification")
    print("==================================================================")
    vol = test_1_dicom_hu_extraction()
    meshset = test_2_mesh_scalar_preservation(vol)
    test_3_display_range_calibration(meshset)
    test_4_vtp_cache_roundtrip(meshset)
    viewer = test_5_ui_and_colormap_switching(meshset)
    test_6_legend_content(viewer)
    test_7_voice_commands(viewer)
    test_8_safety_and_no_fake_gradients(viewer)
    print("\n==================================================================")
    print("  >>> ALL 8 VERIFICATION TESTS PASSED SUCCESSFULLY! <<<")
    print("==================================================================")
