"""
test_multi_organ_feature.py - Verification suite for Patient-Specific Multi-Organ 3D Visualization.
Tests:
  1. Organ mesh extraction & disk cache persistence (chest, abdomen, skull).
  2. Sub-second loading of organ meshes from cache.
  3. Viewer3D anatomical layer controls (Ghost skeleton, solid skeleton, hidden skeleton).
  4. Independent organ visibility toggling and isolate mode.
  5. Voice command execution for all organ intents.
  6. Synchronized 3D cross-sectional clipping plane across bones and organs.
"""

import os
import pytest
from PyQt6.QtWidgets import QApplication
import pyvista as pv
import numpy as np

import dicom_engine
from screens.viewer_3d import Viewer3D


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_meshset_organ_cache_persistence():
    """Verify that build_meshes_from_folder extracts and caches organ meshes."""
    # Chest
    ms_chest = dicom_engine.build_meshes_from_folder("chest_real", preset="chest")
    assert "heart" in ms_chest.organ_meshes, "Heart mesh should be extracted for chest"
    assert "lungs" in ms_chest.organ_meshes, "Lungs mesh should be extracted for chest"
    assert ms_chest.organ_meshes["heart"]["mesh"].n_cells > 500
    assert ms_chest.organ_meshes["lungs"]["mesh"].n_cells > 500

    heart_cache = dicom_engine.get_organ_cache_path("chest_real", "heart")
    lungs_cache = dicom_engine.get_organ_cache_path("chest_real", "lungs")
    assert os.path.isfile(heart_cache), f"Heart cache missing at {heart_cache}"
    assert os.path.isfile(lungs_cache), f"Lungs cache missing at {lungs_cache}"

    # Abdomen
    ms_abd = dicom_engine.build_meshes_from_folder("abdomen_real", preset="abdomen")
    assert "kidneys" in ms_abd.organ_meshes, "Kidneys mesh should be extracted for abdomen"
    assert ms_abd.organ_meshes["kidneys"]["mesh"].n_cells > 1000
    kidneys_cache = dicom_engine.get_organ_cache_path("abdomen_real", "kidneys")
    assert os.path.isfile(kidneys_cache), f"Kidneys cache missing at {kidneys_cache}"

    # Skull
    ms_skull = dicom_engine.build_meshes_from_folder("skull", preset="skull")
    assert "brain" in ms_skull.organ_meshes, "Brain mesh should be extracted for skull"
    assert ms_skull.organ_meshes["brain"]["mesh"].n_cells > 1000
    brain_cache = dicom_engine.get_organ_cache_path("skull", "brain")
    assert os.path.isfile(brain_cache), f"Brain cache missing at {brain_cache}"


def test_meshset_load_from_cache_speed_and_content():
    """Verify fast cached loading of bone and organ meshes."""
    import time
    t0 = time.time()
    bone_cache = dicom_engine.get_cache_path("chest_real", "chest")
    ms = dicom_engine.MeshSet.load_from_cache(bone_cache, preset="chest", folder_path="chest_real")
    dt = time.time() - t0

    assert dt < 0.6, f"Cached load took {dt:.3f}s (expected < 0.6s)"
    assert ms.bone_mesh is not None
    assert "heart" in ms.organ_meshes
    assert "lungs" in ms.organ_meshes
    assert ms.organ_meshes["heart"]["color"] == "#d32f2f"
    assert ms.organ_meshes["lungs"]["color"] == "#26c6da"


def test_viewer3d_layer_controls_and_ghost_skeleton(qapp):
    """Verify Viewer3D anatomical layer UI state changes."""
    viewer = Viewer3D()
    ms = dicom_engine.build_meshes_from_folder("chest_real", preset="chest")
    viewer._on_meshes_ready(ms)

    assert viewer.skeleton_mode == "solid"
    assert viewer._bone_opacity == 1.0
    assert "heart" in viewer.organ_actors
    assert "lungs" in viewer.organ_actors
    assert not viewer.btn_organ_heart.isHidden()
    assert not viewer.btn_organ_lungs.isHidden()

    # Test Ghost Skeleton
    viewer.set_skeleton_mode("ghost")
    assert viewer.skeleton_mode == "ghost"
    assert abs(viewer._bone_opacity - 0.28) < 0.02
    assert "Ghost" in viewer.btn_skeleton_mode.text()

    # Test Hidden Skeleton
    viewer.set_skeleton_mode("hidden")
    assert viewer.skeleton_mode == "hidden"
    assert viewer._bone_opacity == 0.0
    assert "Off" in viewer.btn_skeleton_mode.text()

    # Test Solid Skeleton
    viewer.set_skeleton_mode("solid")
    assert viewer.skeleton_mode == "solid"
    assert viewer._bone_opacity == 1.0


def test_viewer3d_organ_toggles_and_isolate(qapp):
    """Verify toggling individual organs and isolate mode."""
    viewer = Viewer3D()
    ms = dicom_engine.build_meshes_from_folder("chest_real", preset="chest")
    viewer._on_meshes_ready(ms)

    # Toggle heart off
    viewer.set_organ_visible("heart", False)
    assert not viewer.organ_visible["heart"]
    assert viewer.organ_actors.get("heart") is None
    assert not viewer.btn_organ_heart.isChecked()

    # Toggle heart back on
    viewer.toggle_organ("heart")
    assert viewer.organ_visible["heart"]
    assert viewer.organ_actors.get("heart") is not None
    assert viewer.btn_organ_heart.isChecked()

    # Isolate lungs
    viewer.isolate_organ("lungs")
    assert viewer.skeleton_mode == "hidden"
    assert viewer.organ_visible["lungs"]
    assert not viewer.organ_visible["heart"]
    assert viewer.organ_actors.get("heart") is None
    assert viewer.organ_actors.get("lungs") is not None

    # Reset layers
    viewer.reset_anatomical_layers()
    assert viewer.skeleton_mode == "solid"
    assert viewer.organ_visible["heart"]
    assert viewer.organ_visible["lungs"]
    assert viewer.organ_actors.get("heart") is not None
    assert viewer.organ_actors.get("lungs") is not None


def test_viewer3d_voice_commands(qapp):
    """Verify voice command execution for organ layers."""
    viewer = Viewer3D()
    ms = dicom_engine.build_meshes_from_folder("chest_real", preset="chest")
    viewer._on_meshes_ready(ms)

    # Voice: Ghost skeleton
    viewer.handle_voice_command("ghost skeleton")
    assert viewer.skeleton_mode == "ghost"

    # Voice: Hide heart
    viewer.handle_voice_command("hide heart")
    assert not viewer.organ_visible["heart"]

    # Voice: Show heart
    viewer.handle_voice_command("show heart")
    assert viewer.organ_visible["heart"]

    # Voice: Isolate heart
    viewer.handle_voice_command("isolate heart")
    assert viewer.skeleton_mode == "hidden"
    assert viewer.organ_visible["heart"]
    assert not viewer.organ_visible["lungs"]

    # Voice: Reset layers
    viewer.handle_voice_command("reset layers")
    assert viewer.skeleton_mode == "solid"
    assert viewer.organ_visible["heart"]
    assert viewer.organ_visible["lungs"]


def test_viewer3d_clipping_synchronization(qapp):
    """Verify clipping plane cuts through both bone and organ meshes synchronously."""
    viewer = Viewer3D()
    ms = dicom_engine.build_meshes_from_folder("chest_real", preset="chest")
    viewer._on_meshes_ready(ms)

    # Enable clipping
    viewer.set_clipping(True)
    assert viewer._clip_active
    assert viewer._clipped_mesh is not None
    # Check that organs are still rendered and clipped
    assert viewer.organ_actors.get("heart") is not None
    assert viewer.organ_actors.get("lungs") is not None

    # Reverse clipping
    viewer.reverse_clip_direction()
    assert viewer._clip_inverted
    assert viewer.organ_actors.get("heart") is not None

    # Turn clipping off
    viewer.set_clipping(False)
    assert not viewer._clip_active
    assert viewer._clipped_mesh is None
    assert viewer.organ_actors.get("heart") is not None


if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    print("Running multi-organ tests...")
    test_meshset_organ_cache_persistence()
    print("[OK] test_meshset_organ_cache_persistence passed")
    test_meshset_load_from_cache_speed_and_content()
    print("[OK] test_meshset_load_from_cache_speed_and_content passed")
    test_viewer3d_layer_controls_and_ghost_skeleton(app)
    print("[OK] test_viewer3d_layer_controls_and_ghost_skeleton passed")
    test_viewer3d_organ_toggles_and_isolate(app)
    print("[OK] test_viewer3d_organ_toggles_and_isolate passed")
    test_viewer3d_voice_commands(app)
    print("[OK] test_viewer3d_voice_commands passed")
    test_viewer3d_clipping_synchronization(app)
    print("[OK] test_viewer3d_clipping_synchronization passed")
    print("\nALL MULTI-ORGAN TESTS PASSED SUCCESSFULLY!")
