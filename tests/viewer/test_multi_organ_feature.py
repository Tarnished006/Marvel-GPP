"""
test_multi_organ_feature.py - Verification suite for pure bone-only 3D visualization.
Verifies that:
  1. Internal organ extraction has been cleanly removed and produces no soft-tissue clumps.
  2. MeshSet.organ_meshes is an empty dict across chest, abdomen, and skull scans.
  3. No organ cache files (*.vtp) are saved to .cache/.
  4. Viewer3D displays bone meshes without organ actors or UI organ toggles.
  5. Bone opacity and skeleton modes (solid, ghost, hidden) function cleanly.
"""

import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

import os
import glob
import pytest
from PyQt6.QtWidgets import QApplication
import dicom_engine
from screens.viewer_3d import Viewer3D


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_meshset_pure_bone_no_organs():
    """Verify that build_meshes_from_folder extracts pure bone meshes and no organs."""
    # Chest
    ms_chest = dicom_engine.build_meshes_from_folder("chest_real", preset="chest")
    assert ms_chest.bone_mesh is not None, "Bone mesh must be extracted for chest"
    assert ms_chest.bone_mesh.n_cells > 1000, "Chest bone mesh must have substantial cells"
    assert ms_chest.organ_meshes == {}, "organ_meshes must be empty dict"

    # Abdomen
    ms_abd = dicom_engine.build_meshes_from_folder("abdomen_real", preset="abdomen")
    assert ms_abd.bone_mesh is not None, "Bone mesh must be extracted for abdomen"
    assert ms_abd.bone_mesh.n_cells > 1000, "Abdomen bone mesh must have substantial cells"
    assert ms_abd.organ_meshes == {}, "organ_meshes must be empty dict"

    # Skull
    ms_skull = dicom_engine.build_meshes_from_folder("skull", preset="skull")
    assert ms_skull.bone_mesh is not None, "Bone mesh must be extracted for skull"
    assert ms_skull.bone_mesh.n_cells > 1000, "Skull bone mesh must have substantial cells"
    assert ms_skull.organ_meshes == {}, "organ_meshes must be empty dict"


def test_no_organ_cache_files_created():
    """Verify that no organ cache files (*heart*.vtp, *lungs*.vtp, etc.) exist in .cache."""
    organ_patterns = ["*heart*.vtp", "*lungs*.vtp", "*kidney*.vtp", "*brain*.vtp", "*liver*.vtp"]
    for pattern in organ_patterns:
        matches = glob.glob(os.path.join(".cache", pattern))
        assert len(matches) == 0, f"Found unexpected organ cache files matching {pattern}: {matches}"


def test_viewer3d_bone_only_rendering(qapp):
    """Verify Viewer3D initializes without organ actors or organ buttons."""
    viewer = Viewer3D()
    ms = dicom_engine.build_meshes_from_folder("chest_real", preset="chest")
    viewer._on_meshes_ready(ms)

    assert viewer.bone_actor is not None, "Bone actor must be rendered"
    assert viewer.organ_actors == {}, "organ_actors must be empty"
    assert viewer.organ_meshes == {}, "organ_meshes must be empty"

    # Verify organ buttons are not present in the UI
    assert viewer.btn_organ_heart is None
    assert viewer.btn_organ_lungs is None
    assert viewer.btn_organ_brain is None
    assert viewer.btn_organ_kidneys is None
    assert viewer.btn_organ_liver is None


def test_viewer3d_skeleton_opacity_modes(qapp):
    """Verify solid, ghost, and hidden bone opacity transitions."""
    viewer = Viewer3D()
    ms = dicom_engine.build_meshes_from_folder("chest_real", preset="chest")
    viewer._on_meshes_ready(ms)

    # Solid (default)
    assert viewer.skeleton_mode == "solid"
    assert viewer._bone_opacity == 1.0

    # Ghost
    viewer.set_skeleton_mode("ghost")
    assert viewer.skeleton_mode == "ghost"
    assert abs(viewer._bone_opacity - 0.28) < 0.05

    # Hidden
    viewer.set_skeleton_mode("hidden")
    assert viewer.skeleton_mode == "hidden"
    assert viewer._bone_opacity == 0.0

    # Restore solid
    viewer.set_skeleton_mode("solid")
    assert viewer.skeleton_mode == "solid"
    assert viewer._bone_opacity == 1.0


def test_viewer3d_voice_skeleton_commands(qapp):
    """Verify voice commands for bone opacity work without errors."""
    viewer = Viewer3D()
    ms = dicom_engine.build_meshes_from_folder("chest_real", preset="chest")
    viewer._on_meshes_ready(ms)

    viewer.handle_voice_command("ghost skeleton")
    assert viewer.skeleton_mode == "ghost"

    viewer.handle_voice_command("solid skeleton")
    assert viewer.skeleton_mode == "solid"

    viewer.handle_voice_command("hide skeleton")
    assert viewer.skeleton_mode == "hidden"


if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    print("Running bone-only verification tests...")
    test_meshset_pure_bone_no_organs()
    print("[OK] test_meshset_pure_bone_no_organs passed")
    test_no_organ_cache_files_created()
    print("[OK] test_no_organ_cache_files_created passed")
    test_viewer3d_bone_only_rendering(app)
    print("[OK] test_viewer3d_bone_only_rendering passed")
    test_viewer3d_skeleton_opacity_modes(app)
    print("[OK] test_viewer3d_skeleton_opacity_modes passed")
    test_viewer3d_voice_skeleton_commands(app)
    print("[OK] test_viewer3d_voice_skeleton_commands passed")
    print("\nALL BONE-ONLY VERIFICATION TESTS PASSED SUCCESSFULLY!")
