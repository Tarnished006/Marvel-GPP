# test_metadata_panel.py
"""
Comprehensive automated tests for DICOM Metadata and Study Information Panel.
Validates:
 1. Metadata panel can be created.
 2. Panel is hidden by default and opens only on request.
 3. Safe metadata fields are displayed correctly.
 4. Missing metadata does not crash and displays "Not available".
 5. Patient-identifying fields are never displayed.
 6. Dimensions are calculated correctly.
 7. Pixel spacing and slice thickness are displayed correctly.
 8. HU minimum and maximum come from actual loaded volume.
 9. Window/level values update correctly.
10. Orientation and slice index update correctly.
11. Current physical position updates correctly.
12. Heatmap/clipping/opacity states are reflected correctly.
13. Measurement count updates correctly.
14. Voice aliases are routed correctly.
15. Existing features remain unaffected.
"""

import pytest
import numpy as np
import pyvista as pv
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QMouseEvent

from screens.viewer_3d import Viewer3D
from screens.study_info_panel import StudyInfoDialog, StudyInfoPanel, extract_safe_metadata


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def synthetic_volume():
    """Generates synthetic 3D CT volume (H=40, W=50, D=30)."""
    H, W, D = 40, 50, 30
    vol = np.full((H, W, D), -1000.0, dtype=np.float32)
    # Synthetic bone structure with high HU values (up to 1500 HU)
    vol[10:30, 15:35, 8:22] = 800.0
    vol[12:28, 18:32, 10:20] = 1500.0
    pixel_spacing = (0.8, 0.5)  # (dy, dx)
    slice_thickness = 2.0        # dz
    return vol, pixel_spacing, slice_thickness


def _setup_viewer_with_mesh_and_volume(vol, pixel_spacing, slice_thickness):
    viewer = Viewer3D()
    viewer.resize(1000, 700)

    # Build a synthetic bone-like surface mesh for PyVista plotter
    H, W, D = vol.shape
    dy, dx = pixel_spacing
    dz = slice_thickness
    pts = np.array([
        [0.0, 0.0, 0.0],
        [W * dx, 0.0, 0.0],
        [W * dx, H * dy, 0.0],
        [0.0, H * dy, 0.0],
        [0.0, 0.0, D * dz],
        [W * dx, 0.0, D * dz],
        [W * dx, H * dy, D * dz],
        [0.0, H * dy, D * dz],
    ], dtype=np.float32)

    faces = np.hstack([
        [4, 0, 1, 2, 3],
        [4, 4, 5, 6, 7],
        [4, 0, 1, 5, 4],
        [4, 2, 3, 7, 6],
        [4, 0, 3, 7, 4],
        [4, 1, 2, 6, 5],
    ])
    mesh = pv.PolyData(pts, faces)
    mesh.point_data["HU_density"] = np.linspace(300.0, 1400.0, len(pts), dtype=np.float32)

    class MockMeshSet:
        def __init__(self):
            self.bone_mesh = mesh
            self.bone_isovalue = 400.0

        @staticmethod
        def has_hu_density():
            return True

        @staticmethod
        def get_hu_range():
            return (300.0, 1400.0)

        @staticmethod
        def add_to_plotter(plotter, density_mode=False, cmap="turbo"):
            return plotter.add_mesh(mesh, color="#e8c87a", name="bone"), None

    viewer._on_meshes_ready(MockMeshSet())
    viewer.panel_2d.load_volume(vol, pixel_spacing, slice_thickness)
    return viewer


# ── Test 1: Metadata panel can be created ───────────────────────────────────────
def test_1_metadata_panel_can_be_created(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # Instantiate StudyInfoDialog directly
    dialog = StudyInfoDialog(viewer=viewer)
    assert dialog is not None
    assert isinstance(dialog, StudyInfoDialog)
    assert "Study Information" in dialog.windowTitle()
    assert dialog.isModal() is False
    assert len(dialog.field_labels) > 15
    assert StudyInfoPanel is StudyInfoDialog

    # Verify Viewer3D exposes it
    assert hasattr(viewer, "study_info_dialog")
    assert hasattr(viewer, "study_info_panel")
    assert viewer.study_info_dialog is not None


# ── Test 2: Panel is hidden by default and opens only on request ────────────────
def test_2_panel_is_hidden_by_default_or_opens_only_on_request(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # By default, dialog is not visible
    assert viewer.is_study_info_visible() is False
    assert viewer.btn_study_info.isChecked() is False

    # Open on request
    viewer.show_study_info()
    assert viewer.is_study_info_visible() is True
    assert viewer.btn_study_info.isChecked() is True

    # Close on request
    viewer.hide_study_info()
    assert viewer.is_study_info_visible() is False
    assert viewer.btn_study_info.isChecked() is False

    # Toggle
    viewer.toggle_study_info()
    assert viewer.is_study_info_visible() is True
    viewer.toggle_study_info()
    assert viewer.is_study_info_visible() is False


# ── Test 3: Safe metadata fields are displayed correctly ────────────────────────
def test_3_safe_metadata_fields_displayed_correctly(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    viewer.show_study_info()
    dialog = viewer.study_info_dialog

    # Scan info
    assert dialog.get_field_value("series_status") == "Loaded (Active Volume)"

    # Geometry
    assert "40 × 50 × 30" in dialog.get_field_value("dimensions")
    assert "0.80 mm" in dialog.get_field_value("pixel_spacing")
    assert "2.00 mm" in dialog.get_field_value("slice_thickness")

    # Intensity
    assert "HU" in dialog.get_field_value("hu_min")
    assert "HU" in dialog.get_field_value("hu_max")

    # State
    assert "Axial" in dialog.get_field_value("current_orientation")
    assert "100%" in dialog.get_field_value("opacity_pct")


# ── Test 4: Missing metadata does not crash and displays "Not available" ────────
def test_4_missing_metadata_does_not_crash_and_displays_not_available(qapp):
    # Viewer with no scan loaded
    empty_viewer = Viewer3D()
    meta = extract_safe_metadata(empty_viewer)

    assert meta["series_status"] == "No scan loaded"
    assert meta["dimensions"] == "Not available"
    assert meta["pixel_spacing"] == "Not available"
    assert meta["slice_thickness"] == "Not available"
    assert meta["hu_min"] == "Not available"
    assert meta["hu_max"] == "Not available"
    assert meta["study_date"] == "Not available"

    # Dialog with empty viewer does not crash
    dialog = StudyInfoDialog(viewer=empty_viewer)
    dialog.update_metadata()
    assert dialog.get_field_value("dimensions") == "Not available"
    assert dialog.get_field_value("study_date") == "Not available"


# ── Test 5: Patient-identifying fields are never displayed ──────────────────────
def test_5_patient_identifying_fields_never_displayed(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # Attach simulated DICOM headers including sensitive fields that MUST be stripped
    viewer._safe_dicom_headers = {
        "Modality": "CT",
        "PatientName": "JOHN^DOE",
        "PatientID": "PAT123456",
        "PatientBirthDate": "19700101",
        "AccessionNumber": "ACC987654",
        "InstitutionName": "General Hospital",
        "ReferringPhysicianName": "Dr. Smith",
    }

    meta = viewer.get_safe_study_metadata()

    # None of the forbidden sensitive keys may exist in extracted metadata
    forbidden_keys = [
        "PatientName", "patient_name", "PatientID", "patient_id",
        "PatientBirthDate", "patient_birth_date", "AccessionNumber", "accession_number",
        "InstitutionName", "institution_name", "ReferringPhysicianName", "referring_physician"
    ]
    for key in forbidden_keys:
        assert key not in meta

    # Values must not contain sensitive strings
    for k, v in meta.items():
        val_str = str(v).lower()
        assert "john" not in val_str
        assert "pat123456" not in val_str
        assert "acc987654" not in val_str
        assert "smith" not in val_str
        assert "general hospital" not in val_str

    # Update dialog and verify rendered UI
    viewer.study_info_dialog.update_metadata(meta)
    for k, lbl in viewer.study_info_dialog.field_labels.items():
        text_lower = lbl.text().lower()
        assert "john" not in text_lower
        assert "pat123456" not in text_lower
        assert "acc987654" not in text_lower


# ── Test 6: Dimensions are calculated correctly ────────────────────────────────
def test_6_dimensions_calculated_correctly(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    meta = viewer.get_safe_study_metadata()
    assert meta["dimensions"] == "40 × 50 × 30 voxels"
    assert meta["rows"] == "40 px"
    assert meta["columns"] == "50 px"
    assert meta["slices"] == "30 slices"

    # Physical volume span: W*dx = 50*0.5 = 25.0 mm, H*dy = 40*0.8 = 32.0 mm, D*dz = 30*2.0 = 60.0 mm
    assert "25.0 × 32.0 × 60.0 mm³" in meta["volume_dimensions_mm"]


# ── Test 7: Pixel spacing and slice thickness are displayed correctly ───────────
def test_7_pixel_spacing_and_slice_thickness_displayed_correctly(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    dialog = viewer.study_info_dialog
    dialog.update_metadata()

    assert "0.80 mm × 0.50 mm" in dialog.get_field_value("pixel_spacing")
    assert "2.00 mm" in dialog.get_field_value("slice_thickness")


# ── Test 8: HU minimum and maximum come from actual loaded volume ───────────────
def test_8_hu_min_and_max_come_from_actual_loaded_volume(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    # In synthetic volume, min is -1000.0 and max is 1500.0
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    dialog = viewer.study_info_dialog
    dialog.update_metadata()

    assert "-1000.0 HU" in dialog.get_field_value("hu_min")
    assert "1500.0 HU" in dialog.get_field_value("hu_max")


# ── Test 9: Window/level values update correctly ───────────────────────────────
def test_9_window_level_values_update_correctly(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)
    dialog = viewer.study_info_dialog
    viewer.show_study_info()

    # Set custom window/level on 2D viewer
    viewer.panel_2d.set_window_level(1400.0, 300.0)
    assert dialog.get_field_value("current_window_width") == "1400 HU"
    assert dialog.get_field_value("current_window_level") == "300 HU"

    # Set soft tissue preset
    viewer.panel_2d.set_window_preset("Soft Tissue")
    assert dialog.get_field_value("current_window_width") == "400 HU"
    assert dialog.get_field_value("current_window_level") == "40 HU"


# ── Test 10: Orientation and slice index update correctly ──────────────────────
def test_10_orientation_and_slice_index_update_correctly(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)
    dialog = viewer.study_info_dialog
    viewer.show_study_info()

    # Switch to coronal
    viewer.panel_2d.set_orientation("coronal")
    assert "Coronal" in dialog.get_field_value("current_orientation")

    # Change slice index
    viewer.panel_2d.set_slice_index(10)
    assert "11 / 40" in dialog.get_field_value("current_slice_index")

    # Switch to sagittal
    viewer.panel_2d.set_orientation("sagittal")
    assert "Sagittal" in dialog.get_field_value("current_orientation")
    viewer.panel_2d.set_slice_index(20)
    assert "21 / 50" in dialog.get_field_value("current_slice_index")


# ── Test 11: Current physical position updates correctly ───────────────────────
def test_11_current_physical_position_updates_correctly(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)
    dialog = viewer.study_info_dialog
    viewer.show_study_info()

    viewer.panel_2d.set_orientation("axial")
    viewer.panel_2d.set_slice_index(15)
    # Axial physical Z = 15 * 2.0 = 30.0 mm
    pos_str = dialog.get_field_value("current_physical_pos")
    assert "Z: 30.0 mm" in pos_str

    viewer.panel_2d.set_slice_index(25)
    pos_str2 = dialog.get_field_value("current_physical_pos")
    assert "Z: 50.0 mm" in pos_str2


# ── Test 12: Heatmap/clipping/opacity states are reflected correctly ───────────
def test_12_heatmap_clipping_opacity_states_reflected_correctly(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)
    dialog = viewer.study_info_dialog
    viewer.show_study_info()

    # Default states
    assert "Disabled" in dialog.get_field_value("heatmap_mode")
    assert "Disabled" in dialog.get_field_value("clipping_mode")
    assert "100%" in dialog.get_field_value("opacity_pct")

    # Toggle heatmap
    viewer.set_density_colormap(True)
    assert "Enabled" in dialog.get_field_value("heatmap_mode")

    # Toggle clipping
    viewer.set_clipping(True)
    viewer.set_clip_axis("z")
    viewer.set_clip_position(0.4)
    clip_str = dialog.get_field_value("clipping_mode")
    assert "Enabled" in clip_str
    assert "Z-axis" in clip_str
    assert "40%" in clip_str

    # Change opacity
    viewer.set_bone_opacity(0.6)
    assert "60%" in dialog.get_field_value("opacity_pct")


# ── Test 13: Measurement count updates correctly ───────────────────────────────
def test_13_measurement_count_updates_correctly(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)
    dialog = viewer.study_info_dialog
    viewer.show_study_info()

    assert "0" in dialog.get_field_value("measurement_count")

    # Add 3D measurement
    viewer._add_3d_measurement((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    assert "3D: 1" in dialog.get_field_value("measurement_count")

    # Add 2D measurement
    viewer.panel_2d.start_measurement()
    canvas = viewer.panel_2d.canvas
    rect = canvas._get_target_rect()
    p1 = QPointF(rect.left() + 20, rect.top() + 20)
    p2 = QPointF(rect.left() + 60, rect.top() + 60)
    canvas.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, p1, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    canvas.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, p2, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    m_str = dialog.get_field_value("measurement_count")
    assert "2D: 1" in m_str
    assert "3D: 1" in m_str
    assert "Total: 2" in m_str

    # Clear 3D measurements
    viewer.clear_measurements_3d()
    assert "3D: 0" in dialog.get_field_value("measurement_count")

    # Clear 2D measurements
    viewer.panel_2d.clear_measurements()
    assert "0 active measurements" in dialog.get_field_value("measurement_count")


# ── Test 14: Voice aliases are routed correctly ────────────────────────────────
def test_14_voice_aliases_routed_correctly(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # Initial state: closed
    assert viewer.is_study_info_visible() is False

    # Command: "show study information"
    viewer.handle_voice_command("show study information")
    assert viewer.is_study_info_visible() is True

    # Command: "hide study information"
    viewer.handle_voice_command("hide study information")
    assert viewer.is_study_info_visible() is False

    # Command: "open scan metadata"
    viewer.handle_voice_command("open scan metadata")
    assert viewer.is_study_info_visible() is True

    # Command: "close scan metadata"
    viewer.handle_voice_command("close scan metadata")
    assert viewer.is_study_info_visible() is False

    # Command: "show scan details"
    viewer.handle_voice_command("show scan details")
    assert viewer.is_study_info_visible() is True

    # Command: "toggle study info"
    viewer.handle_voice_command("toggle study info")
    assert viewer.is_study_info_visible() is False


# ── Test 15: Existing features remain unaffected ───────────────────────────────
def test_15_existing_features_remain_unaffected(qapp, synthetic_volume):
    vol, sp, st = synthetic_volume
    viewer = _setup_viewer_with_mesh_and_volume(vol, sp, st)

    # Open study info
    viewer.show_study_info()

    # 1. 2D CT Viewer and Crosshair
    viewer.panel_2d.set_orientation("axial")
    viewer.panel_2d.set_slice_index(12)
    assert viewer.panel_2d.get_current_slice_index() == 12
    viewer.set_crosshair_visible(False)
    assert viewer.is_crosshair_visible() is False
    viewer.set_crosshair_visible(True)
    assert viewer.is_crosshair_visible() is True

    # 2. 2D-3D Synchronization
    viewer.set_sync_2d_3d(True)
    assert viewer.is_sync_2d_3d_enabled() is True
    viewer.set_sync_2d_3d(False)
    assert viewer.is_sync_2d_3d_enabled() is False

    # 3. Interactive Clipping
    viewer.set_clipping(True)
    assert viewer._clip_active is True
    viewer.set_clip_position(0.5)
    assert pytest.approx(viewer._clip_fraction, 0.01) == 0.5
    viewer.set_clipping(False)
    assert viewer._clip_active is False

    # 4. Opacity
    viewer.set_bone_opacity(0.4)
    assert pytest.approx(viewer._bone_opacity, 0.01) == 0.4
    viewer.set_bone_opacity(1.0)
    assert pytest.approx(viewer._bone_opacity, 0.01) == 1.0

    # 5. Density Heatmap
    viewer.set_density_colormap(True)
    assert viewer._density_active is True
    viewer.set_density_colormap(False)
    assert viewer._density_active is False

    # 6. Distance Measurement
    viewer._add_3d_measurement((0.0, 0.0, 0.0), (3.0, 4.0, 0.0))
    assert len(viewer.get_measurements_3d()) == 1
    assert pytest.approx(viewer.get_measurements_3d()[0]["distance_mm"], 0.01) == 5.0
    viewer.clear_measurements_3d()
    assert len(viewer.get_measurements_3d()) == 0

    # Close study info
    viewer.hide_study_info()
    assert viewer.is_study_info_visible() is False
