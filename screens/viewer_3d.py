# screens/viewer_3d.py
"""
3D DICOM viewer screen for Aegis-Touch.

Architecture:
  - DicomLoader (QThread from dicom_engine) runs all heavy meshing off the
    UI thread. The viewer shows a status page while it loads, then switches
    to the live scene.
  - Multi-Modal View Modes:
      1. 🧊 3D Surface Model: Realistic bone isosurface with 90° discrete snap turns.
      2. 📐 MPR Slices: Multi-Planar Reconstruction (Axial, Coronal, Sagittal) with
         synchronized crosshairs, clinical Window/Level presets, and calibrated calipers.
  - A LOCAL DATASETS sidebar lists every DICOM folder found next to the
    project root so you can switch scans without needing a patient in the DB.
  - Hand Gesture & Voice 3D Control:
      1. Open palm left/right movement → Azimuth orbit
      2. Open palm up/down movement → Elevation orbit
      3. Thumb+Index pinch ratio → Zoom In / Out
      4. Voice / Button 90° Anatomical Snap Turns (Anterior, Posterior, Lateral, Superior)
"""

import os
import time
import numpy as np
import pyvista as pv
import vtk
from pyvistaqt import QtInteractor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QSizePolicy, QScrollArea,
    QComboBox, QFrame, QSlider, QSplitter, QMenu,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction
from signal_bus import signal_bus
from dicom_engine import DicomLoader, MeshSet, load_volume_for_mpr
from screens.mpr_view import MPRView
from screens.slice_2d_viewer import Slice2DViewerWidget
from screens.study_info_panel import StudyInfoDialog, StudyInfoPanel, extract_safe_metadata
from surgical_plan import PlanningPoint, SurgicalPlanSession, AvoidStructure, SurgicalCorridor
from before_after import BeforeAfterComparison


try:
    from database import get_scans_for_ui
except Exception:
    def get_scans_for_ui(_mrn):
        return []

# ── Locate project-root DICOM folders ────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from dicom_engine import detect_scan_anatomy

def _preset_from_path(path: str) -> str:
    anatomy = detect_scan_anatomy(path)
    if anatomy in ("head", "skull"):
        return "skull"
    elif anatomy == "chest":
        return "chest"
    elif anatomy == "spine":
        return "spine"
    elif anatomy == "abdomen":
        return "abdomen"
    return "body"

def _discover_local_datasets():
    candidates = []
    for entry in os.listdir(_ROOT):
        path = os.path.join(_ROOT, entry)
        if os.path.isdir(path) and entry not in (".git", ".venv", ".cache", "__pycache__"):
            dcm_count = sum(1 for f in os.listdir(path) if f.lower().endswith((".dcm", ".ima")))
            if dcm_count > 1:
                preset = _preset_from_path(path)
                display = entry.replace("_", " ").capitalize()
                label  = f"{display}  ({dcm_count} slices)"
                candidates.append((label, path, preset))
    return candidates

LOCAL_DATASETS = _discover_local_datasets()


class Viewer3D(QWidget):
    plan_save_requested = pyqtSignal()
    plan_versions_requested = pyqtSignal()
    quick_view_applied = pyqtSignal(str)
    icu_mark_same_location_requested = pyqtSignal()
    icu_view_same_location_requested = pyqtSignal()
    icu_view_previous_location_requested = pyqtSignal()
    icu_view_current_location_requested = pyqtSignal()
    icu_clear_same_location_requested = pyqtSignal()

    # Very slow, smooth automatic rotation
    VOICE_SPIN_STEP_DEG = 0.35
    VOICE_SPIN_INTERVAL_MS = 40

    # Page indices inside self.state_stack
    _PAGE_SELECT  = 0   # scan picker (shown when no scan is loaded)
    _PAGE_LOADING = 1   # progress text while DicomLoader runs
    _PAGE_SCENE   = 2   # live viewport & MPR stack

    def __init__(self, parent=None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.state_stack = QStackedWidget()
        outer.addWidget(self.state_stack)

        self.state_stack.addWidget(self._build_select_page())   # 0
        self.state_stack.addWidget(self._build_loading_page())  # 1
        self.state_stack.addWidget(self._build_scene_page())    # 2
        self.state_stack.setCurrentIndex(self._PAGE_SELECT)

        # Null VTK style: keeps our camera controls working while blocking
        # VTK's own mouse-drag-orbit so air-mouse clicks can't grab the camera.
        import weakref
        style = vtk.vtkInteractorStyleUser()
        style._parent = weakref.ref(self.plotter.iren)
        self.plotter.iren.interactor.SetInteractorStyle(style)

        # Viewer state
        self.bone_actor         = None
        self._loader            = None
        self._initial_load_done = False   # lazy-load guard
        self._ghost_active      = False
        self.mesh_bounds        = None

        # Multi-organ & Anatomical Layers state
        self.organ_actors: dict[str, pv.Actor] = {}
        self.organ_meshes: dict[str, dict] = {}
        self.organ_visible: dict[str, bool] = {}
        self.skeleton_mode: str = "solid"  # "solid" (1.0), "ghost" (0.28), "hidden" (0.0)

        # Direct Volume Rendering (DVR) state
        self._dvr_active: bool = False
        self._dvr_preset: str = "soft_tissue"
        self._dvr_actor = None
        self._volume_grid: pv.ImageData | None = None

        # Voice/feature state
        self._voice_zoom_level = 1.0      # bookkeeping for "zoom to X percent"
        self._clip_active      = False    # cross-section clipping plane
        self._clip_axis        = "y"      # active clipping axis: 'x', 'y', or 'z'
        self._clip_fraction    = 0.5      # position along axis (0.0 to 1.0)
        self._clip_inverted    = False    # direction toggle (invert retained side)
        self._clipped_mesh     = None     # derived clipped mesh
        self._original_mesh    = None     # reference to unmodified complete bone mesh
        self._density_active   = False    # HU density colormap
        self._bone_opacity     = 1.0      # bone mesh opacity (0.1 to 1.0)
        self._bone_mesh        = None
        self._spin_timer       = QTimer(self)
        self._spin_timer.timeout.connect(self._spin_tick)

        # ── 2D ↔ 3D Synchronization State ──────────────────────────────────────
        self._sync_2d_3d_enabled = False  # OFF by default
        self._sync_show_marker   = True   # 3D reference marker visible when sync is active
        self._sync_link_clipping = False  # optionally link clipping plane to 2D slice
        self._sync_marker_actor  = None
        self._sync_in_progress   = False  # reentrancy guard against feedback loops
        self._reference_pos      = (0.0, 0.0, 0.0)

        # ── 3D Measurement State ──────────────────────────────────────────────
        self._measuring_3d            = False  # OFF by default
        self._measurements_3d_visible = True
        self._pending_3d_point        = None
        self._measurements_3d         = []
        self._measurement_actor_names = []

        # ── Surgical Planning Landmark State (OR Mode: ENTRY + TARGET) ───────
        self.surgical_plan = SurgicalPlanSession()
        self._planning_picking_mode = "IDLE"  # "IDLE", "SET_ENTRY", "SET_TARGET"

        # ── Before vs After Comparison State (OR Mode: Feature 8) ─────────────
        self.comparison = BeforeAfterComparison()
        self._comparison_actor_names = []

        # ── Study Information & Scan Metadata Panel ───────────────────────────
        self._safe_dicom_headers = None
        self.study_info_dialog = StudyInfoDialog(viewer=self, parent=self)
        self.study_info_panel = self.study_info_dialog
        self.study_info_dialog.closed.connect(self._on_study_info_dialog_closed)

        # Voice commands arrive via MainWindow._handle_voice_command(), which calls
        # handle_voice_command() directly -- do NOT also connect signal_bus.voice_command
        # here or every command would fire twice.
        # Wire gesture signals
        signal_bus.hand_rotation.connect(self.rotate_camera)
        signal_bus.zoom_command.connect(self.zoom_camera)
        signal_bus.tissue_melt.connect(self.set_tissue_melt)

    # ── Page builders ─────────────────────────────────────────────────────────

    def _build_select_page(self) -> QWidget:
        """Scan picker shown while no scan is loaded (or if loading fails)."""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(16)

        title = QLabel("Select a Scan to Render")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "color: #ccc; font-size: 16px; font-weight: 600; margin-bottom: 8px;"
        )
        v.addWidget(title)

        if not LOCAL_DATASETS:
            msg = QLabel("No local DICOM folders found.\nAdd a skull/ or DICOM/ folder to the project root.")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            msg.setStyleSheet("color: #555; font-size: 12px;")
            v.addWidget(msg)
            return page

        for display_name, folder_path, preset in LOCAL_DATASETS:
            btn = QPushButton(f"  🗂  {display_name}")
            btn.setFixedWidth(320)
            btn.setFixedHeight(48)
            btn.setStyleSheet(
                "QPushButton {"
                "  background: #1a1a1a; color: #ccc; border: 1px solid #333;"
                "  border-radius: 6px; font-size: 13px; text-align: left; padding-left: 14px;"
                "}"
                "QPushButton:hover { background: #252525; border-color: #555; color: #fff; }"
                "QPushButton:pressed { background: #111; }"
            )
            btn.clicked.connect(
                lambda _=False, p=folder_path, pr=preset, n=display_name:
                    self._start_load(p, preset=pr, label=n)
            )
            v.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("Or open a scan from the patient Gallery →")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #444; font-size: 10px; margin-top: 12px;")
        v.addWidget(hint)
        return page

    def _build_loading_page(self) -> QWidget:
        """Status/progress screen while DicomLoader thread runs."""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(12)

        self.scan_label = QLabel("")
        self.scan_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_label.setStyleSheet(
            "color: #bbb; font-size: 14px; font-weight: 700;"
        )
        v.addWidget(self.scan_label)

        self.status_label = QLabel("Initialising…")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "color: #666; font-size: 11px; padding: 0 60px;"
        )
        v.addWidget(self.status_label)

        back = QPushButton("← Choose a different scan")
        back.setFixedWidth(220)
        back.setStyleSheet(
            "QPushButton { background: transparent; color: #444; border: none;"
            " font-size: 10px; margin-top: 20px; }"
            "QPushButton:hover { color: #888; }"
        )
        back.clicked.connect(lambda: self.state_stack.setCurrentIndex(self._PAGE_SELECT))
        v.addWidget(back, alignment=Qt.AlignmentFlag.AlignCenter)
        return page

    def _build_scene_page(self) -> QWidget:
        """Live 3D viewport and 2D MPR slices driven by hand gestures and voice."""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Responsive Two-Row Header Toolbar (MPR-Style Compact Architecture) ─
        toolbar_container = QVBoxLayout()
        toolbar_container.setContentsMargins(8, 3, 8, 3)
        toolbar_container.setSpacing(3)

        row_top = QHBoxLayout()
        row_top.setContentsMargins(0, 0, 0, 0)
        row_top.setSpacing(5)

        row_bottom = QHBoxLayout()
        row_bottom.setContentsMargins(0, 0, 0, 0)
        row_bottom.setSpacing(5)

        def _make_vsep():
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.VLine)
            sep.setFrameShadow(QFrame.Shadow.Sunken)
            sep.setStyleSheet("color: #242424; background-color: #242424; width: 1px; max-height: 18px;")
            return sep

        def _make_group_lbl(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #555; font-size: 9px; font-weight: 700; margin-right: 2px; text-transform: uppercase;")
            return lbl

        btn_style_base = (
            "QPushButton { background: #141414; color: #aaa; border: 1px solid #282828; "
            "border-radius: 4px; font-size: 9px; font-weight: 600; padding: 0 6px; } "
            "QPushButton:hover { background: #1f1f1f; color: #eee; border-color: #444; } "
            "QPushButton::menu-indicator { image: none; width: 0px; }"
        )

        menu_style = (
            "QMenu { background: #121212; color: #ccc; border: 1px solid #2c2c2c; padding: 4px; }"
            "QMenu::item { padding: 5px 18px; font-size: 10px; border-radius: 3px; }"
            "QMenu::item:selected { background: #002e3b; color: #00e5ff; }"
            "QMenu::separator { height: 1px; background: #222; margin: 3px 6px; }"
        )

        # ── Group 1: VIEW (Row 1) ─────────────────────────────────────────────
        row_top.addWidget(_make_group_lbl("VIEW:"))

        self.btn_3d_mode = QPushButton("🧊 3D Volume")
        self.btn_3d_mode.setCheckable(True)
        self.btn_3d_mode.setChecked(True)
        self.btn_3d_mode.setFixedHeight(24)
        self.btn_3d_mode.setStyleSheet(
            btn_style_base +
            "QPushButton:checked { background: #1a2a1a; color: #7cfc00; border-color: #3a5a3a; }"
        )
        self.btn_3d_mode.clicked.connect(lambda: self._switch_view_mode(0))
        row_top.addWidget(self.btn_3d_mode)

        self.btn_mpr_mode = QPushButton("📐 MPR Slices")
        self.btn_mpr_mode.setCheckable(True)
        self.btn_mpr_mode.setChecked(False)
        self.btn_mpr_mode.setFixedHeight(24)
        self.btn_mpr_mode.setStyleSheet(
            btn_style_base +
            "QPushButton:checked { background: #1a2a1a; color: #7cfc00; border-color: #3a5a3a; }"
        )
        self.btn_mpr_mode.clicked.connect(lambda: self._switch_view_mode(1))
        row_top.addWidget(self.btn_mpr_mode)

        self.btn_toggle_2d = QPushButton("🖼 2D Slice: OFF")
        self.btn_toggle_2d.setCheckable(True)
        self.btn_toggle_2d.setChecked(False)
        self.btn_toggle_2d.setFixedHeight(24)
        self.btn_toggle_2d.setStyleSheet(
            btn_style_base +
            "QPushButton:checked { background: #00222a; color: #00e5ff; border-color: #00b4d8; }"
        )
        self.btn_toggle_2d.clicked.connect(self._on_toggle_2d_clicked)
        row_top.addWidget(self.btn_toggle_2d)

        row_top.addWidget(_make_vsep())

        # ── Group 2: TOOLS (Row 1) ────────────────────────────────────────────
        row_top.addWidget(_make_group_lbl("TOOLS:"))

        # Measure dropdown button
        self.btn_measure_3d = QPushButton("📏 Measure: OFF ▼")
        self.btn_measure_3d.setCheckable(True)
        self.btn_measure_3d.setChecked(False)
        self.btn_measure_3d.setFixedHeight(24)
        self.btn_measure_3d.setStyleSheet(
            btn_style_base +
            "QPushButton:checked { background: #2a2200; color: #ffea00; border-color: #ffd600; }"
        )
        self.menu_measure = QMenu(self.btn_measure_3d)
        self.menu_measure.setStyleSheet(menu_style)
        self.act_measure_dist = QAction("📏 Distance (Point-to-Point)", self)
        self.act_measure_dist.setCheckable(True)
        self.act_measure_dist.setChecked(False)
        self.act_measure_dist.triggered.connect(lambda: self.set_measuring_3d(not self._measuring_3d))
        self.menu_measure.addAction(self.act_measure_dist)

        self.act_measure_vis = QAction("👁 Show / Hide Measurements", self)
        self.act_measure_vis.setCheckable(True)
        self.act_measure_vis.setChecked(True)
        self.act_measure_vis.triggered.connect(lambda: self.set_measurements_3d_visible(not self._measurements_3d_visible))
        self.menu_measure.addAction(self.act_measure_vis)

        self.menu_measure.addSeparator()
        self.act_measure_clear = QAction("🗑 Clear All Measurements", self)
        self.act_measure_clear.triggered.connect(self.clear_all_measurements)
        self.menu_measure.addAction(self.act_measure_clear)
        self.btn_measure_3d.setMenu(self.menu_measure)
        row_top.addWidget(self.btn_measure_3d)

        # Retained legacy buttons for test suite compatibility
        self.btn_measure_3d_vis = QPushButton("👁 Measure: ON")
        self.btn_measure_3d_vis.setCheckable(True)
        self.btn_measure_3d_vis.setChecked(True)
        self.btn_measure_3d_vis.setVisible(False)
        self.btn_measure_3d_vis.clicked.connect(self._on_measure_3d_vis_clicked)

        self.btn_clear_measure_3d = QPushButton("🗑 Clear")
        self.btn_clear_measure_3d.setVisible(False)
        self.btn_clear_measure_3d.clicked.connect(self.clear_all_measurements)

        # Clip dropdown button
        self.btn_clip_toggle = QPushButton("✂ Clip: OFF ▼")
        self.btn_clip_toggle.setCheckable(True)
        self.btn_clip_toggle.setChecked(False)
        self.btn_clip_toggle.setFixedHeight(24)
        self.btn_clip_toggle.setStyleSheet(
            btn_style_base +
            "QPushButton:checked { background: #00222a; color: #00e5ff; border-color: #00b4d8; }"
        )
        self.menu_clip = QMenu(self.btn_clip_toggle)
        self.menu_clip.setStyleSheet(menu_style)
        self.act_clip_enable = QAction("✂ Enable Clipping Plane", self)
        self.act_clip_enable.setCheckable(True)
        self.act_clip_enable.setChecked(False)
        self.act_clip_enable.triggered.connect(lambda: self.set_clipping(not self._clip_active))
        self.menu_clip.addAction(self.act_clip_enable)

        menu_axis = self.menu_clip.addMenu("📐 Plane Axis")
        menu_axis.setStyleSheet(menu_style)
        self.act_clip_x = QAction("X Axis (Sagittal)", self)
        self.act_clip_x.triggered.connect(lambda: self.set_clip_axis("x"))
        menu_axis.addAction(self.act_clip_x)
        self.act_clip_y = QAction("Y Axis (Coronal)", self)
        self.act_clip_y.triggered.connect(lambda: self.set_clip_axis("y"))
        menu_axis.addAction(self.act_clip_y)
        self.act_clip_z = QAction("Z Axis (Axial)", self)
        self.act_clip_z.triggered.connect(lambda: self.set_clip_axis("z"))
        menu_axis.addAction(self.act_clip_z)

        self.act_clip_rev = QAction("⇄ Reverse Direction", self)
        self.act_clip_rev.triggered.connect(self.reverse_clip_direction)
        self.menu_clip.addAction(self.act_clip_rev)
        self.btn_clip_toggle.setMenu(self.menu_clip)
        row_top.addWidget(self.btn_clip_toggle)

        # Presets dropdown button
        self.btn_presets_menu = QPushButton("🎨 Presets ▼")
        self.btn_presets_menu.setFixedHeight(24)
        self.btn_presets_menu.setStyleSheet(btn_style_base)
        self.menu_presets = QMenu(self.btn_presets_menu)
        self.menu_presets.setStyleSheet(menu_style)
        self.act_preset_bone = QAction("🦴 Normal Bone View", self)
        self.act_preset_bone.triggered.connect(lambda: self._select_preset("Bone"))
        self.menu_presets.addAction(self.act_preset_bone)
        self.act_preset_density = QAction("🌡 Hounsfield Heatmap", self)
        self.act_preset_density.triggered.connect(lambda: self.combo_bone_mode.setCurrentIndex(1))
        self.menu_presets.addAction(self.act_preset_density)
        self.act_preset_soft = QAction("🥩 Soft Tissue Window", self)
        self.act_preset_soft.triggered.connect(lambda: self._select_preset("Soft Tissue"))
        self.menu_presets.addAction(self.act_preset_soft)
        self.act_preset_lung = QAction("🫁 Lung Window", self)
        self.act_preset_lung.triggered.connect(lambda: self._select_preset("Lung"))
        self.menu_presets.addAction(self.act_preset_lung)
        self.btn_presets_menu.setMenu(self.menu_presets)
        row_top.addWidget(self.btn_presets_menu)

        # Segmented buttons for Bone Mode (Touchless & Air-Mouse Optimized)
        self.btn_bone_normal = QPushButton("🦴 Normal")
        self.btn_bone_normal.setCheckable(True)
        self.btn_bone_normal.setChecked(True)
        self.btn_bone_normal.setFixedHeight(24)
        self.btn_bone_normal.setStyleSheet(
            btn_style_base +
            "QPushButton:checked { background: #00222a; color: #00e5ff; border-color: #00b4d8; font-weight: bold; }"
        )
        self.btn_bone_normal.clicked.connect(lambda: self._set_bone_mode_index(0))
        row_top.addWidget(self.btn_bone_normal)

        self.btn_bone_heatmap = QPushButton("🌡 Heatmap")
        self.btn_bone_heatmap.setCheckable(True)
        self.btn_bone_heatmap.setChecked(False)
        self.btn_bone_heatmap.setFixedHeight(24)
        self.btn_bone_heatmap.setStyleSheet(
            btn_style_base +
            "QPushButton:checked { background: #00222a; color: #00e5ff; border-color: #00b4d8; font-weight: bold; }"
        )
        self.btn_bone_heatmap.clicked.connect(lambda: self._set_bone_mode_index(1))
        row_top.addWidget(self.btn_bone_heatmap)

        # Retained combo_bone_mode for test suite
        self.combo_bone_mode = QComboBox()
        self.combo_bone_mode.addItem("🦴 Normal Bone View")
        self.combo_bone_mode.addItem("🌡 Hounsfield Heatmap")
        self.combo_bone_mode.setVisible(False)
        self.combo_bone_mode.currentIndexChanged.connect(self._on_bone_mode_changed)

        # Reset / Views menu button
        self.btn_reset_view = QPushButton("↺ Reset View ▼")
        self.btn_reset_view.setFixedHeight(24)
        self.btn_reset_view.setStyleSheet(btn_style_base)
        self.menu_views = QMenu(self.btn_reset_view)
        self.menu_views.setStyleSheet(menu_style)
        act_reset = QAction("↺ Reset Camera View", self)
        act_reset.triggered.connect(lambda: self.snap_to_view("reset"))
        self.menu_views.addAction(act_reset)
        self.menu_views.addSeparator()
        for name, cmd in [
            ("Anterior (Front)", "anterior"),
            ("Posterior (Back)", "posterior"),
            ("Left Lateral", "left_lateral"),
            ("Right Lateral", "right_lateral"),
            ("Superior (Top)", "superior"),
            ("Inferior (Bottom)", "inferior"),
        ]:
            act_snap = QAction(name, self)
            act_snap.triggered.connect(lambda checked, c=cmd: self.snap_to_view(c))
            self.menu_views.addAction(act_snap)
        self.menu_views.addSeparator()
        act_spin_start = QAction("▶ Start Spin", self)
        act_spin_start.triggered.connect(self.start_spin)
        self.menu_views.addAction(act_spin_start)
        act_spin_stop = QAction("■ Stop Spin", self)
        act_spin_stop.triggered.connect(self.stop_spin)
        self.menu_views.addAction(act_spin_stop)
        self.act_ghost = QAction("👁 3D Ghost Slice", self)
        self.act_ghost.setCheckable(True)
        self.act_ghost.triggered.connect(self._toggle_ghost_plane)
        self.menu_views.addAction(self.act_ghost)
        self.btn_reset_view.setMenu(self.menu_views)
        row_top.addWidget(self.btn_reset_view)

        row_top.addWidget(_make_vsep())

        # Skeleton Mode (Retained reference for backwards compatibility)
        self.btn_skeleton_mode = None

        # Retained organ/DVR references for backwards compatibility
        self.btn_organ_heart = None
        self.btn_organ_lungs = None
        self.btn_organ_brain = None
        self.btn_organ_kidneys = None
        self.btn_organ_rkidney = None
        self.btn_organ_lkidney = None
        self.btn_organ_liver = None
        self.btn_layers_reset = None
        self.btn_dvr_mode = None

        # Retained spin/ghost buttons for backwards compatibility
        self.btn_start_spin = QPushButton("▶ Start Spin")
        self.btn_start_spin.setVisible(False)
        self.btn_start_spin.clicked.connect(self.start_spin)
        self.btn_stop_spin = QPushButton("■ Stop Spin")
        self.btn_stop_spin.setVisible(False)
        self.btn_stop_spin.clicked.connect(self.stop_spin)
        self.btn_ghost_plane = QPushButton("👁 3D Ghost Slice: OFF")
        self.btn_ghost_plane.setCheckable(True)
        self.btn_ghost_plane.setVisible(False)
        self.btn_ghost_plane.clicked.connect(self._toggle_ghost_plane)

        row_top.addStretch(1)

        # ── Group 3: DISPLAY (Row 2) ──────────────────────────────────────────
        row_bottom.addWidget(_make_group_lbl("DISPLAY:"))

        lbl_op = QLabel("Opacity:")
        lbl_op.setStyleSheet("color: #666; font-size: 9px; font-weight: 600; text-transform: uppercase;")
        row_bottom.addWidget(lbl_op)

        self.lbl_opacity = QLabel("100%")
        self.lbl_opacity.setStyleSheet("color: #00e5ff; font-size: 9px; font-weight: 600; min-width: 28px;")
        row_bottom.addWidget(self.lbl_opacity)

        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(10, 100)
        self.slider_opacity.setValue(100)
        self.slider_opacity.setFixedHeight(22)
        self.slider_opacity.setFixedWidth(75)
        self.slider_opacity.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #222; border-radius: 2px; }"
            "QSlider::sub-page:horizontal { background: #0088a8; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #00e5ff; border: 1px solid #00b4d8; width: 10px; margin-top: -3px; margin-bottom: -3px; border-radius: 5px; }"
            "QSlider::handle:horizontal:hover { background: #fff; border-color: #00e5ff; }"
        )
        self.slider_opacity.valueChanged.connect(self._on_opacity_slider_changed)
        row_bottom.addWidget(self.slider_opacity)

        # Clipping controls in DISPLAY group (contextual)
        self.lbl_clip_pos = QLabel("Clip: 50%")
        self.lbl_clip_pos.setStyleSheet("color: #444; font-size: 9px; font-weight: 600; min-width: 50px;")
        row_bottom.addWidget(self.lbl_clip_pos)

        self.slider_clip_pos = QSlider(Qt.Orientation.Horizontal)
        self.slider_clip_pos.setRange(0, 100)
        self.slider_clip_pos.setValue(50)
        self.slider_clip_pos.setFixedHeight(22)
        self.slider_clip_pos.setFixedWidth(70)
        self.slider_clip_pos.setEnabled(False)
        self.slider_clip_pos.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #222; border-radius: 2px; }"
            "QSlider::sub-page:horizontal { background: #0088a8; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #00e5ff; border: 1px solid #00b4d8; width: 10px; margin-top: -3px; margin-bottom: -3px; border-radius: 5px; }"
            "QSlider::handle:horizontal:hover { background: #fff; border-color: #00e5ff; }"
            "QSlider:disabled { background: transparent; }"
        )
        self.slider_clip_pos.valueChanged.connect(self._on_clip_slider_changed)
        row_bottom.addWidget(self.slider_clip_pos)

        self.btn_clip_reverse = QPushButton("⇄")
        self.btn_clip_reverse.setToolTip("Reverse Clipping Direction")
        self.btn_clip_reverse.setFixedSize(22, 22)
        self.btn_clip_reverse.setEnabled(False)
        self.btn_clip_reverse.setStyleSheet(
            "QPushButton { background: #141414; color: #888; border: 1px solid #282828; border-radius: 3px; font-size: 10px; }"
            "QPushButton:hover { background: #222; color: #eee; border-color: #444; }"
            "QPushButton:disabled { color: #333; border-color: #1a1a1a; }"
        )
        self.btn_clip_reverse.clicked.connect(self.reverse_clip_direction)
        row_bottom.addWidget(self.btn_clip_reverse)

        self.combo_clip_axis = QComboBox()
        self.combo_clip_axis.addItem("X")
        self.combo_clip_axis.addItem("Y")
        self.combo_clip_axis.addItem("Z")
        self.combo_clip_axis.setCurrentIndex(1)
        self.combo_clip_axis.setEnabled(False)
        self.combo_clip_axis.setFixedSize(40, 22)
        self.combo_clip_axis.setStyleSheet(
            "QComboBox { background: #141414; color: #bbb; border: 1px solid #282828; border-radius: 3px; font-size: 9px; font-weight: 600; padding: 0 4px; }"
            "QComboBox:disabled { color: #444; border-color: #1a1a1a; }"
            "QComboBox::drop-down { border: none; width: 12px; }"
            "QComboBox QAbstractItemView { background: #111; color: #ddd; selection-background-color: #003344; selection-color: #00e5ff; border: 1px solid #333; }"
        )
        self.combo_clip_axis.currentIndexChanged.connect(self._on_clip_axis_changed)
        row_bottom.addWidget(self.combo_clip_axis)

        # Segmented buttons for Axis (Touchless & Air-Mouse Optimized)
        self.btn_axis_x = QPushButton("X")
        self.btn_axis_x.setCheckable(True)
        self.btn_axis_x.setChecked(False)
        self.btn_axis_x.setEnabled(False)
        self.btn_axis_x.setFixedSize(22, 22)
        self.btn_axis_x.setStyleSheet(
            "QPushButton { background: #141414; color: #888; border: 1px solid #282828; "
            "border-radius: 3px; font-size: 9px; font-weight: bold; } "
            "QPushButton:checked { background: #00222a; color: #00e5ff; border: 1px solid #00b4d8; } "
            "QPushButton:disabled { color: #333; border-color: #1a1a1a; background: #0e0e0e; } "
            "QPushButton:hover:!disabled { background: #222; color: #eee; }"
        )
        self.btn_axis_x.clicked.connect(lambda: self._set_clip_axis_index(0))
        row_bottom.addWidget(self.btn_axis_x)

        self.btn_axis_y = QPushButton("Y")
        self.btn_axis_y.setCheckable(True)
        self.btn_axis_y.setChecked(True)
        self.btn_axis_y.setEnabled(False)
        self.btn_axis_y.setFixedSize(22, 22)
        self.btn_axis_y.setStyleSheet(
            "QPushButton { background: #141414; color: #888; border: 1px solid #282828; "
            "border-radius: 3px; font-size: 9px; font-weight: bold; } "
            "QPushButton:checked { background: #00222a; color: #00e5ff; border: 1px solid #00b4d8; } "
            "QPushButton:disabled { color: #333; border-color: #1a1a1a; background: #0e0e0e; } "
            "QPushButton:hover:!disabled { background: #222; color: #eee; }"
        )
        self.btn_axis_y.clicked.connect(lambda: self._set_clip_axis_index(1))
        row_bottom.addWidget(self.btn_axis_y)

        self.btn_axis_z = QPushButton("Z")
        self.btn_axis_z.setCheckable(True)
        self.btn_axis_z.setChecked(False)
        self.btn_axis_z.setEnabled(False)
        self.btn_axis_z.setFixedSize(22, 22)
        self.btn_axis_z.setStyleSheet(
            "QPushButton { background: #141414; color: #888; border: 1px solid #282828; "
            "border-radius: 3px; font-size: 9px; font-weight: bold; } "
            "QPushButton:checked { background: #00222a; color: #00e5ff; border: 1px solid #00b4d8; } "
            "QPushButton:disabled { color: #333; border-color: #1a1a1a; background: #0e0e0e; } "
            "QPushButton:hover:!disabled { background: #222; color: #eee; }"
        )
        self.btn_axis_z.clicked.connect(lambda: self._set_clip_axis_index(2))
        row_bottom.addWidget(self.btn_axis_z)

        # Initially hidden until clipping is activated
        self.lbl_clip_pos.setVisible(False)
        self.slider_clip_pos.setVisible(False)
        self.btn_clip_reverse.setVisible(False)
        self.combo_clip_axis.setVisible(False)
        self.btn_axis_x.setVisible(False)
        self.btn_axis_y.setVisible(False)
        self.btn_axis_z.setVisible(False)

        row_bottom.addWidget(_make_vsep())

        # ── Group 4: SYNCHRONIZATION (Row 2) ──────────────────────────────────
        row_bottom.addWidget(_make_group_lbl("SYNC:"))

        self.btn_sync_toggle = QPushButton("🔗 Sync: OFF")
        self.btn_sync_toggle.setCheckable(True)
        self.btn_sync_toggle.setChecked(False)
        self.btn_sync_toggle.setFixedHeight(24)
        self.btn_sync_toggle.setStyleSheet(
            btn_style_base +
            "QPushButton:checked { background: #00222a; color: #00e5ff; border-color: #00b4d8; }"
        )
        self.btn_sync_toggle.clicked.connect(self._on_sync_toggle_clicked)
        row_bottom.addWidget(self.btn_sync_toggle)

        # Retained sync sub-controls for tests & options menu
        self.btn_sync_marker = QPushButton("📍 3D Marker: ON")
        self.btn_sync_marker.setCheckable(True)
        self.btn_sync_marker.setChecked(True)
        self.btn_sync_marker.setVisible(False)
        self.btn_sync_marker.clicked.connect(self._on_sync_marker_clicked)

        self.btn_sync_crosshair = QPushButton("🎯 Crosshair: ON")
        self.btn_sync_crosshair.setCheckable(True)
        self.btn_sync_crosshair.setChecked(True)
        self.btn_sync_crosshair.setVisible(False)
        self.btn_sync_crosshair.clicked.connect(self._on_sync_crosshair_clicked)

        self.btn_sync_clip = QPushButton("✂ Link Clip: OFF")
        self.btn_sync_clip.setCheckable(True)
        self.btn_sync_clip.setChecked(False)
        self.btn_sync_clip.setVisible(False)
        self.btn_sync_clip.clicked.connect(self._on_sync_clip_clicked)

        row_bottom.addWidget(_make_vsep())

        # ── Group 5: MORE (Row 2) ─────────────────────────────────────────────
        row_bottom.addWidget(_make_group_lbl("MORE:"))

        self.btn_study_info = QPushButton("📋 Study Info")
        self.btn_study_info.setCheckable(True)
        self.btn_study_info.setChecked(False)
        self.btn_study_info.setFixedHeight(24)
        self.btn_study_info.setStyleSheet(
            btn_style_base +
            "QPushButton:checked { background: #00222a; color: #00e5ff; border-color: #00b4d8; }"
        )
        self.btn_study_info.clicked.connect(self.toggle_study_info)
        row_bottom.addWidget(self.btn_study_info)

        self.btn_export_report = QPushButton("📄 Export Report")
        self.btn_export_report.setFixedHeight(24)
        self.btn_export_report.setStyleSheet(btn_style_base)
        self.btn_export_report.clicked.connect(self.export_case_report)
        row_bottom.addWidget(self.btn_export_report)

        self.switch_btn = QPushButton("↩ Switch Scan")
        self.switch_btn.setFixedHeight(24)
        self.switch_btn.setStyleSheet(btn_style_base)
        self.switch_btn.clicked.connect(lambda: self.state_stack.setCurrentIndex(self._PAGE_SELECT))
        row_bottom.addWidget(self.switch_btn)

        row_bottom.addWidget(_make_vsep())

        self.info_bar = QLabel("")
        self.info_bar.setStyleSheet("color: #7cfc00; font-size: 10px; font-weight: 500;")
        row_bottom.addWidget(self.info_bar, stretch=1)

        toolbar_container.addLayout(row_top)
        toolbar_container.addLayout(row_bottom)

        info_bar_widget = QWidget()
        info_bar_widget.setStyleSheet("background: #0d0d0d; border-bottom: 1px solid #1c1c1c;")
        info_bar_widget.setLayout(toolbar_container)
        outer.addWidget(info_bar_widget)

        # ── Content row: view stack (3D or MPR) + sidebar ────────────────────
        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(0)
        outer.addLayout(content_row)

        self.view_mode_stack = QStackedWidget()

        # 1. 3D View Container (clean, spacious viewport layout)
        self.container_3d = QWidget()
        self.container_3d.resizeEvent = self._on_container_3d_resized
        container_3d = self.container_3d
        layout_3d = QVBoxLayout(container_3d)
        layout_3d.setContentsMargins(0, 0, 0, 0)
        layout_3d.setSpacing(0)
        # Placeholders to preserve attributes without cluttering the screen
        self.snap_bar_widget = QWidget()
        self.clip_bar_widget = QWidget()

        self.plotter = QtInteractor(container_3d, auto_update=False)
        self.plotter.set_background("#090909")

        self.view_split = QSplitter(Qt.Orientation.Horizontal)
        self.view_split.setStyleSheet("QSplitter::handle { background: #1a1a1a; width: 4px; }")
        self.view_split.addWidget(self.plotter.interactor)
        self.panel_2d = Slice2DViewerWidget(parent=self)
        self.slice_viewer = self.panel_2d
        self.panel_2d.setVisible(False)
        self.panel_2d.closed.connect(lambda: self.set_2d_panel_visible(False))
        self.panel_2d.slice_changed.connect(self._on_2d_slice_changed)
        self.panel_2d.reference_position_changed.connect(self._on_2d_ref_pos_changed)
        self.panel_2d.measurement_added.connect(self._on_2d_measurement_added)
        self.panel_2d.measurement_cleared.connect(self._on_2d_measurement_cleared)
        self.panel_2d.physical_point_selected.connect(self._on_2d_physical_point_selected)
        self.panel_2d.planning_point_selected.connect(self._on_2d_planning_point_selected)
        self.panel_2d.window_level_changed.connect(lambda w, l: self._notify_metadata_changed())
        self.panel_2d.orientation_changed.connect(lambda o: self._notify_metadata_changed())
        if hasattr(self.panel_2d, "btn_crosshair_toggle"):
            self.panel_2d.btn_crosshair_toggle.clicked.connect(
                lambda: self.set_crosshair_visible(self.panel_2d.is_crosshair_visible())
            )
        self.view_split.addWidget(self.panel_2d)
        self.view_split.setStretchFactor(0, 3)
        self.view_split.setStretchFactor(1, 2)
        layout_3d.addWidget(self.view_split, stretch=1)

        # Floating HUD card explaining HU density scale
        self.legend_card = self._build_legend_card(container_3d)

        self.view_mode_stack.addWidget(container_3d)  # Index 0: 3D

        # 2. 2D Multi-Planar Reconstruction (MPR) View
        self.mpr_view = MPRView()
        self.mpr_view.axial_changed.connect(self._on_mpr_axial_changed)
        self.view_mode_stack.addWidget(self.mpr_view)  # Index 1: MPR

        content_row.addWidget(self.view_mode_stack, stretch=1)

        # Sidebar: local dataset switcher
        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(168)
        self.sidebar.setStyleSheet("background: #0f0f0f; border-left: 1px solid #1e1e1e;")
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        self.sidebar_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.sidebar_layout.setContentsMargins(8, 10, 8, 10)
        self.sidebar_layout.setSpacing(6)
        self._rebuild_sidebar()
        content_row.addWidget(self.sidebar)

        self.toggle_btn = QPushButton("›")
        self.toggle_btn.setFixedWidth(18)
        self.toggle_btn.setStyleSheet(
            "QPushButton { background: #131313; color: #555; border: none; }"
            "QPushButton:hover { color: #aaa; }"
        )
        self.toggle_btn.clicked.connect(self._toggle_sidebar)
        content_row.addWidget(self.toggle_btn)

        return page

    def _build_legend_card(self, parent: QWidget) -> QFrame:
        """Constructs floating HUD card explaining the HU density color scale."""
        card = QFrame(parent)
        card.setFixedSize(235, 122)
        card.setStyleSheet(
            "QFrame {"
            "  background: rgba(13, 13, 13, 230);"
            "  border: 1px solid #2a2a2a;"
            "  border-radius: 6px;"
            "}"
        )
        card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)

        title = QLabel("🌡 CT Density Scale (HU)")
        title.setStyleSheet(
            "color: #00e5ff; font-size: 10px; font-weight: 700; border: none; background: transparent;"
        )
        v.addWidget(title)

        items = [
            ("🔴 > 1000 HU", "Dense Cortical Bone", "#ff4444"),
            ("🟢 500–1000 HU", "Subcortical / Intermediate Bone", "#44dd66"),
            ("🔵 200–500 HU", "Trabecular / Cancellous Bone", "#3399ff"),
        ]
        for tag, desc, col in items:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            dot = QLabel("●")
            dot.setStyleSheet(
                f"color: {col}; font-size: 10px; border: none; background: transparent;"
            )
            lbl_tag = QLabel(tag)
            lbl_tag.setStyleSheet(
                "color: #ccc; font-size: 9px; font-weight: 600; border: none; background: transparent;"
            )
            lbl_desc = QLabel(f"({desc})")
            lbl_desc.setStyleSheet(
                "color: #777; font-size: 8px; border: none; background: transparent;"
            )
            row.addWidget(dot)
            row.addWidget(lbl_tag)
            row.addWidget(lbl_desc)
            row.addStretch()
            v.addLayout(row)

        disclaimer = QLabel("Heuristic HU visualization — not for diagnostic BMD.")
        disclaimer.setStyleSheet(
            "color: #555; font-size: 8px; font-style: italic; border: none; background: transparent; margin-top: 2px;"
        )
        v.addWidget(disclaimer)

        card.setVisible(False)
        return card

    def _on_container_3d_resized(self, event):
        """Handle container_3d resize to keep the legend card properly positioned."""
        QWidget.resizeEvent(self.container_3d, event)
        self._reposition_legend()

    def _reposition_legend(self):
        """Keep the legend card anchored at the bottom-left of the 3D viewport."""
        if hasattr(self, "legend_card") and hasattr(self, "plotter") and hasattr(self, "container_3d"):
            card_w = self.legend_card.width()
            card_h = self.legend_card.height()
            try:
                pt = self.plotter.interactor.mapTo(self.container_3d, self.plotter.interactor.rect().bottomLeft())
                x = pt.x() + 16
                y = pt.y() - card_h - 16
            except Exception:
                p_geom = self.plotter.interactor.geometry()
                x = p_geom.x() + 16
                y = max(p_geom.y() + 16, p_geom.y() + p_geom.height() - card_h - 20)
            self.legend_card.move(x, y)
            self.legend_card.raise_()

    def _on_toggle_2d_clicked(self, checked: bool):
        self.set_2d_panel_visible(checked)

    def set_2d_panel_visible(self, visible: bool):
        """Toggles or sets the visibility of the side-by-side 2D slice viewer panel."""
        if not hasattr(self, "panel_2d"):
            return
        is_vis = bool(visible)
        self.panel_2d.setVisible(is_vis)
        if hasattr(self, "btn_toggle_2d"):
            self.btn_toggle_2d.blockSignals(True)
            self.btn_toggle_2d.setChecked(is_vis)
            self.btn_toggle_2d.setText("🖼 2D Slice: ON" if is_vis else "🖼 2D Slice: OFF")
            self.btn_toggle_2d.blockSignals(False)

        if is_vis and hasattr(self, "view_split"):
            tot_w = max(400, self.view_split.width())
            self.view_split.setSizes([int(tot_w * 0.58), int(tot_w * 0.42)])

        # Re-anchor legend card after splitter layout updates
        QTimer.singleShot(50, self._reposition_legend)

    set_2d_slice_visible = set_2d_panel_visible

    # ── 2D ↔ 3D Synchronization Handlers & State Management ───────────────────

    def _on_sync_toggle_clicked(self, checked: bool):
        self.set_sync_2d_3d(checked)

    def _on_sync_marker_clicked(self, checked: bool):
        self.set_marker_visible(checked)

    def _on_sync_crosshair_clicked(self, checked: bool):
        self.set_crosshair_visible(checked)

    def _on_sync_clip_clicked(self, checked: bool):
        self.set_link_clipping(checked)

    def set_sync_2d_3d(self, enabled: bool):
        """Enables or disables synchronized 2D–3D anatomical position linking."""
        self._sync_2d_3d_enabled = bool(enabled)
        if hasattr(self, "btn_sync_toggle"):
            self.btn_sync_toggle.blockSignals(True)
            self.btn_sync_toggle.setChecked(self._sync_2d_3d_enabled)
            self.btn_sync_toggle.setText("🔗 Sync: ON" if self._sync_2d_3d_enabled else "🔗 Sync: OFF")
            self.btn_sync_toggle.blockSignals(False)

        if self._sync_2d_3d_enabled:
            if hasattr(self, "panel_2d"):
                pos = self.panel_2d.get_current_physical_position()
                self._reference_pos = pos
                if getattr(self, "_sync_show_marker", True):
                    self._update_3d_marker(pos[0], pos[1], pos[2])
                if getattr(self, "_sync_link_clipping", False):
                    self._sync_2d_to_clipping(
                        self.panel_2d.current_orientation,
                        self.panel_2d.get_current_slice_index()
                    )
        else:
            self._remove_3d_marker()
        self._notify_metadata_changed()

    def is_sync_2d_3d_enabled(self) -> bool:
        """Returns True if 2D–3D position synchronization is currently active."""
        return getattr(self, "_sync_2d_3d_enabled", False)

    def set_marker_visible(self, visible: bool):
        """Sets visibility of the 3D reference marker."""
        self._sync_show_marker = bool(visible)
        if hasattr(self, "btn_sync_marker"):
            self.btn_sync_marker.blockSignals(True)
            self.btn_sync_marker.setChecked(self._sync_show_marker)
            self.btn_sync_marker.setText("📍 3D Marker: ON" if self._sync_show_marker else "📍 3D Marker: OFF")
            self.btn_sync_marker.blockSignals(False)

        if self._sync_show_marker and getattr(self, "_sync_2d_3d_enabled", False):
            self._update_3d_marker(*getattr(self, "_reference_pos", (0.0, 0.0, 0.0)))
        else:
            self._remove_3d_marker()

    def is_marker_visible(self) -> bool:
        """Returns True if the 3D reference marker is currently set to visible."""
        return getattr(self, "_sync_show_marker", True)

    def set_crosshair_visible(self, visible: bool):
        """Sets visibility of the 2D crosshair overlay."""
        vis = bool(visible)
        if hasattr(self, "btn_sync_crosshair"):
            self.btn_sync_crosshair.blockSignals(True)
            self.btn_sync_crosshair.setChecked(vis)
            self.btn_sync_crosshair.setText("🎯 Crosshair: ON" if vis else "🎯 Crosshair: OFF")
            self.btn_sync_crosshair.blockSignals(False)

        if hasattr(self, "panel_2d"):
            self.panel_2d.set_crosshair_visible(vis)

    def is_crosshair_visible(self) -> bool:
        """Returns True if the 2D crosshair overlay is enabled."""
        if hasattr(self, "panel_2d"):
            return self.panel_2d.is_crosshair_visible()
        return True

    def set_link_clipping(self, enabled: bool):
        """Toggles bidirectional linking between the 2D slice and the 3D clipping plane."""
        self._sync_link_clipping = bool(enabled)
        if hasattr(self, "btn_sync_clip"):
            self.btn_sync_clip.blockSignals(True)
            self.btn_sync_clip.setChecked(self._sync_link_clipping)
            self.btn_sync_clip.setText("✂ Link Clip: ON" if self._sync_link_clipping else "✂ Link Clip: OFF")
            self.btn_sync_clip.blockSignals(False)

        if self._sync_link_clipping and getattr(self, "_sync_2d_3d_enabled", False) and hasattr(self, "panel_2d"):
            self._sync_2d_to_clipping(
                self.panel_2d.current_orientation,
                self.panel_2d.get_current_slice_index()
            )

    def is_clipping_linked(self) -> bool:
        """Returns True if clipping plane is linked to the 2D slice."""
        return getattr(self, "_sync_link_clipping", False)

    def map_slice_to_3d(self, orientation: str, slice_index: int) -> tuple[float, float, float]:
        """Calculates physical position (X, Y, Z) in mm for a given slice orientation and index.
        - Axial slice maps to physical Z position: slice_index * slice_thickness
        - Coronal slice maps to physical Y position: slice_index * pixel_spacing[0]
        - Sagittal slice maps to physical X position: slice_index * pixel_spacing[1]
        """
        dx, dy, dz = 1.0, 1.0, 1.0
        if hasattr(self, "panel_2d") and self.panel_2d.vol_data is not None:
            dy = float(self.panel_2d.pixel_spacing[0])
            dx = float(self.panel_2d.pixel_spacing[1])
            dz = float(self.panel_2d.slice_thickness)
        elif hasattr(self, "mpr_view") and self.mpr_view.vol_8bit is not None:
            dy = float(self.mpr_view.pixel_spacing[0])
            dx = float(self.mpr_view.pixel_spacing[1])
            dz = float(self.mpr_view.slice_thickness)

        cur_x, cur_y, cur_z = getattr(self, "_reference_pos", (0.0, 0.0, 0.0))
        orient = orientation.lower().strip()
        if orient == "axial":
            z = slice_index * dz
            return (cur_x, cur_y, float(z))
        elif orient == "coronal":
            y = slice_index * dy
            return (cur_x, float(y), cur_z)
        else:  # sagittal
            x = slice_index * dx
            return (float(x), cur_y, cur_z)

    def map_3d_to_slice(self, x_mm: float, y_mm: float, z_mm: float) -> tuple[int, int, int]:
        """Maps physical coordinate (X, Y, Z) in mm to volume slice indices (idx_x, idx_y, idx_z).
        Safely clamped to volume dimensions.
        """
        H, W, D = 1, 1, 1
        dx, dy, dz = 1.0, 1.0, 1.0
        if hasattr(self, "panel_2d") and self.panel_2d.vol_data is not None:
            H, W, D = self.panel_2d.vol_data.shape
            dy = max(1e-4, float(self.panel_2d.pixel_spacing[0]))
            dx = max(1e-4, float(self.panel_2d.pixel_spacing[1]))
            dz = max(1e-4, float(self.panel_2d.slice_thickness))
        elif hasattr(self, "mpr_view") and self.mpr_view.vol_8bit is not None:
            H, W, D = self.mpr_view.vol_8bit.shape
            dy = max(1e-4, float(self.mpr_view.pixel_spacing[0]))
            dx = max(1e-4, float(self.mpr_view.pixel_spacing[1]))
            dz = max(1e-4, float(self.mpr_view.slice_thickness))

        ix = int(np.clip(round(x_mm / dx), 0, W - 1))
        iy = int(np.clip(round(y_mm / dy), 0, H - 1))
        iz = int(np.clip(round(z_mm / dz), 0, D - 1))
        return (ix, iy, iz)

    def _update_3d_marker(self, x: float, y: float, z: float):
        """Renders or moves the lightweight 3D reference marker sphere at physical position (X, Y, Z).
        Does NOT permanently modify the bone mesh or re-run HU sampling.
        """
        self._reference_pos = (float(x), float(y), float(z))
        if (
            not getattr(self, "_sync_2d_3d_enabled", False)
            or not getattr(self, "_sync_show_marker", True)
            or not hasattr(self, "plotter")
            or self.plotter is None
        ):
            return

        radius = 2.5
        if getattr(self, "mesh_bounds", None) is not None:
            xmin, xmax, ymin, ymax, zmin, zmax = self.mesh_bounds
            diag = ((xmax - xmin)**2 + (ymax - ymin)**2 + (zmax - zmin)**2)**0.5
            radius = max(1.5, diag * 0.015)

        try:
            sphere = pv.Sphere(radius=radius, center=(float(x), float(y), float(z)))
            self._sync_marker_actor = self.plotter.add_mesh(
                sphere,
                name="sync_marker",
                color="#00e5ff",
                specular=0.6,
                specular_power=20,
                ambient=0.35,
                render=False
            )
            self.plotter.render()
        except Exception as exc:
            print(f"[Viewer3D] Marker update error: {exc}")

    def _remove_3d_marker(self):
        """Removes the lightweight 3D reference marker from the scene without touching the bone mesh."""
        try:
            if hasattr(self, "plotter") and self.plotter is not None:
                self.plotter.remove_actor("sync_marker", render=False)
                self.plotter.render()
            self._sync_marker_actor = None
        except Exception:
            pass

    def _on_2d_slice_changed(self, orientation: str, slice_index: int):
        """Slot called when 2D slice index changes."""
        if getattr(self, "_sync_in_progress", False):
            return

        self._sync_in_progress = True
        try:
            if getattr(self, "_sync_2d_3d_enabled", False):
                if hasattr(self, "panel_2d"):
                    pos = self.panel_2d.get_current_physical_position()
                    self._reference_pos = pos
                    if getattr(self, "_sync_show_marker", True):
                        self._update_3d_marker(pos[0], pos[1], pos[2])

                    if getattr(self, "_sync_link_clipping", False):
                        self._sync_2d_to_clipping(orientation, slice_index)
        finally:
            self._sync_in_progress = False

        self._notify_metadata_changed()

    def _on_2d_ref_pos_changed(self, x_mm: float, y_mm: float, z_mm: float):
        """Slot called when 2D in-plane crosshair or slice moves."""
        if getattr(self, "_sync_in_progress", False):
            return
        self._reference_pos = (x_mm, y_mm, z_mm)
        if getattr(self, "_sync_2d_3d_enabled", False) and getattr(self, "_sync_show_marker", True):
            self._update_3d_marker(x_mm, y_mm, z_mm)
        self._notify_metadata_changed()

    def _sync_2d_to_clipping(self, orientation: str, slice_index: int):
        """Updates 3D clipping plane to align with 2D slice."""
        if not hasattr(self, "panel_2d"):
            return
        count = self.panel_2d.get_slice_count()
        if count <= 1:
            return

        fraction = float(np.clip(slice_index / max(1, count - 1), 0.005, 0.995))
        axis_map = {"axial": "z", "coronal": "y", "sagittal": "x"}
        target_axis = axis_map.get(orientation.lower(), "z")

        if getattr(self, "_clip_axis", "y") != target_axis:
            self.set_clip_axis(target_axis)
        self.set_clip_position(fraction)
        if hasattr(self, "slider_clip_pos"):
            self.slider_clip_pos.blockSignals(True)
            self.slider_clip_pos.setValue(int(round(fraction * 100)))
            self.slider_clip_pos.blockSignals(False)
        if not getattr(self, "_clip_active", False):
            self.set_clipping(True)

    def _sync_clip_to_2d(self, fraction: float):
        """Updates 2D slice position when 3D clipping slider is adjusted."""
        if (
            not getattr(self, "_sync_2d_3d_enabled", False)
            or not getattr(self, "_sync_link_clipping", False)
            or getattr(self, "_sync_in_progress", False)
            or not hasattr(self, "panel_2d")
        ):
            return

        self._sync_in_progress = True
        try:
            axis = getattr(self, "_clip_axis", "y").lower().strip()
            axis_to_orient = {"z": "axial", "y": "coronal", "x": "sagittal"}
            target_orient = axis_to_orient.get(axis, "axial")

            if self.panel_2d.current_orientation != target_orient:
                self.panel_2d.set_orientation(target_orient)

            count = self.panel_2d.get_slice_count()
            if count > 0:
                idx = int(np.clip(round(fraction * (count - 1)), 0, count - 1))
                self.panel_2d.set_slice_index(idx)

            pos = self.panel_2d.get_current_physical_position()
            self._reference_pos = pos
            if getattr(self, "_sync_show_marker", True):
                self._update_3d_marker(pos[0], pos[1], pos[2])
        finally:
            self._sync_in_progress = False

    # ── 3D Distance Measurement System ────────────────────────────────────────

    @staticmethod
    def calc_3d_distance(p1: tuple[float, float, float], p2: tuple[float, float, float]) -> float:
        """Calculates Euclidean distance in mm between two 3D physical coordinates."""
        x1, y1, z1 = float(p1[0]), float(p1[1]), float(p1[2])
        x2, y2, z2 = float(p2[0]), float(p2[1]), float(p2[2])
        return float(np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2))

    def start_measurement(self):
        """Activates 3D point-to-point measurement mode."""
        self.set_measuring_3d(True)

    def finish_measurement(self):
        """Finishes or cancels active 3D measurement mode."""
        self.set_measuring_3d(False)

    def set_measuring_3d(self, active: bool):
        """Sets active 3D point-to-point measurement state."""
        self._measuring_3d = bool(active)
        if hasattr(self, "btn_measure_3d"):
            self.btn_measure_3d.blockSignals(True)
            self.btn_measure_3d.setChecked(self._measuring_3d)
            self.btn_measure_3d.setText("📏 Measure: ON" if self._measuring_3d else "📏 Measure: OFF")
            self.btn_measure_3d.blockSignals(False)
        if hasattr(self, "act_measure_dist"):
            self.act_measure_dist.blockSignals(True)
            self.act_measure_dist.setChecked(self._measuring_3d)
            self.act_measure_dist.blockSignals(False)
        if hasattr(self, "panel_2d") and self.panel_2d is not None:
            if self.panel_2d.is_measuring() != self._measuring_3d:
                self.panel_2d.set_measurement_mode(self._measuring_3d)
        if self._measuring_3d:
            self._pending_3d_point = None
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  📏 Select first point in 3D surface or 2D slice")
        else:
            self.cancel_pending_3d_measurement()
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  📏 3D Measurement: OFF")

    def is_measuring_3d(self) -> bool:
        return bool(self._measuring_3d)

    def cancel_pending_3d_measurement(self):
        """Cancels in-progress first point selection."""
        if self._pending_3d_point is not None:
            self._pending_3d_point = None
            try:
                if hasattr(self, "plotter") and self.plotter is not None:
                    self.plotter.remove_actor("measure_3d_temp_pt")
                    self.plotter.render()
            except Exception:
                pass
        if hasattr(self, "panel_2d") and self.panel_2d is not None:
            self.panel_2d.canvas.cancel_pending_measurement()

    def clear_all_measurements(self):
        """Clears measurements from both 3D and 2D views."""
        self.clear_measurements_3d()
        if hasattr(self, "panel_2d") and self.panel_2d is not None:
            self.panel_2d.clear_measurements()

    def clear_measurements_3d(self):
        """Removes all 3D measurement lines, endpoint markers, and labels."""
        if hasattr(self, "plotter") and self.plotter is not None:
            for name in list(self._measurement_actor_names):
                try:
                    self.plotter.remove_actor(name)
                except Exception:
                    pass
            self.cancel_pending_3d_measurement()
            self.plotter.render()
        self._measurement_actor_names.clear()
        self._measurements_3d.clear()
        if hasattr(self, "panel_2d") and self.panel_2d is not None and hasattr(self.panel_2d, "canvas"):
            if hasattr(self.panel_2d.canvas, "measurements_3d_synced"):
                self.panel_2d.canvas.measurements_3d_synced.clear()
                self.panel_2d.canvas.update()
        if hasattr(self, "info_bar") and self.info_bar and getattr(self, "_measuring_3d", False):
            self.info_bar.setText("  📏 3D Measurement: Cleared")
        self._notify_metadata_changed()

    def set_measurements_3d_visible(self, visible: bool):
        """Toggles visibility of 3D measurement actors."""
        self._measurements_3d_visible = bool(visible)
        if hasattr(self, "btn_measure_3d_vis"):
            self.btn_measure_3d_vis.blockSignals(True)
            self.btn_measure_3d_vis.setChecked(self._measurements_3d_visible)
            self.btn_measure_3d_vis.setText("👁 Measure: ON" if self._measurements_3d_visible else "👁 Measure: OFF")
            self.btn_measure_3d_vis.blockSignals(False)
        if hasattr(self, "act_measure_vis"):
            self.act_measure_vis.blockSignals(True)
            self.act_measure_vis.setChecked(self._measurements_3d_visible)
            self.act_measure_vis.blockSignals(False)
        if hasattr(self, "plotter") and self.plotter is not None:
            for name in self._measurement_actor_names:
                act = self.plotter.actors.get(name)
                if act:
                    act.SetVisibility(self._measurements_3d_visible)
            self.plotter.render()
        if hasattr(self, "panel_2d") and self.panel_2d is not None:
            self.panel_2d.set_measurements_visible(self._measurements_3d_visible)

    def is_measurements_3d_visible(self) -> bool:
        return bool(self._measurements_3d_visible)

    def get_measurements_3d(self) -> list[dict]:
        return list(self._measurements_3d)

    def _add_3d_measurement(
        self,
        pt1: tuple[float, float, float],
        pt2: tuple[float, float, float],
        source: str = "3D"
    ) -> dict:
        """Adds independent line, sphere endpoints, and distance label to PyVista scene."""
        dist = self.calc_3d_distance(pt1, pt2)
        import uuid
        m_id = str(uuid.uuid4())[:8]

        line_name = f"measure_line_{m_id}"
        pt1_name = f"measure_pt1_{m_id}"
        pt2_name = f"measure_pt2_{m_id}"
        lbl_name = f"measure_lbl_{m_id}"

        if hasattr(self, "plotter") and self.plotter is not None:
            try:
                line_mesh = pv.Line(pt1, pt2)
                self.plotter.add_mesh(line_mesh, name=line_name, color="#ffea00", line_width=3.5)

                s1 = pv.Sphere(radius=2.2, center=pt1)
                s2 = pv.Sphere(radius=2.2, center=pt2)
                self.plotter.add_mesh(s1, name=pt1_name, color="#7cfc00")  # Distinguishable Green for P1
                self.plotter.add_mesh(s2, name=pt2_name, color="#ffea00")  # Distinguishable Yellow for P2

                midpoint = [
                    (float(pt1[0]) + float(pt2[0])) / 2.0,
                    (float(pt1[1]) + float(pt2[1])) / 2.0,
                    (float(pt1[2]) + float(pt2[2])) / 2.0
                ]
                self.plotter.add_point_labels(
                    [midpoint],
                    [f"Distance: {dist:.1f} mm"],
                    name=lbl_name,
                    point_color="#ffea00",
                    point_size=1,
                    text_color="#ffffff",
                    fill_shape=True,
                    shape_color="#111111",
                    shape_opacity=0.85,
                    font_size=11,
                    always_visible=True
                )
            except Exception as exc:
                print(f"[Viewer3D] Add 3D measurement actor warning: {exc}")

        actor_names = [line_name, pt1_name, pt2_name, lbl_name]
        self._measurement_actor_names.extend(actor_names)

        m_record = {
            "id": m_id,
            "source": source,
            "pt1": pt1,
            "pt2": pt2,
            "distance_mm": dist,
            "actor_names": actor_names
        }
        self._measurements_3d.append(m_record)

        if not self._measurements_3d_visible and hasattr(self, "plotter") and self.plotter is not None:
            for name in actor_names:
                act = self.plotter.actors.get(name)
                if act:
                    act.SetVisibility(False)

        if hasattr(self, "plotter") and self.plotter is not None:
            self.plotter.render()
        self._notify_metadata_changed()
        return m_record

    def _on_measure_3d_btn_clicked(self):
        self.set_measuring_3d(self.btn_measure_3d.isChecked())

    def _on_measure_3d_vis_clicked(self):
        self.set_measurements_3d_visible(self.btn_measure_3d_vis.isChecked())

    def _on_2d_physical_point_selected(self, pt_num: int, x_mm: float, y_mm: float, z_mm: float):
        """Slot called when a physical point is clicked on the 2D CT slice."""
        if pt_num == 1:
            self._pending_3d_point = (x_mm, y_mm, z_mm)
            try:
                if hasattr(self, "plotter") and self.plotter is not None:
                    temp_s = pv.Sphere(radius=2.5, center=(x_mm, y_mm, z_mm))
                    self.plotter.add_mesh(temp_s, name="measure_3d_temp_pt", color="#7cfc00")
                    self.plotter.render()
            except Exception as exc:
                print(f"[Viewer3D] 2D->3D Temp point warning: {exc}")
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(
                    f"  📏 Point 1: ({x_mm:.1f}, {y_mm:.1f}, {z_mm:.1f}) mm · Select second point"
                )
        elif pt_num == 2:
            try:
                if hasattr(self, "plotter") and self.plotter is not None:
                    self.plotter.remove_actor("measure_3d_temp_pt")
            except Exception:
                pass
            self._pending_3d_point = None

    def _on_2d_measurement_added(self, m):
        """When 2D measurement is created, synchronize line, markers, and distance label to 3D."""
        try:
            pt1 = m.physical_start
            pt2 = m.physical_end
            if pt1 and pt2:
                already_exists = any(
                    rec.get("pt1") == pt1 and rec.get("pt2") == pt2
                    for rec in self._measurements_3d
                )
                if not already_exists:
                    self._add_3d_measurement(pt1, pt2, source="2D_synced")
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(f"  📏 Distance: {m.distance_mm:.1f} mm  ·  P1 to P2")
        except Exception as exc:
            print(f"[Viewer3D] 2D->3D measurement sync warning: {exc}")
        self._notify_metadata_changed()

    def _on_2d_measurement_cleared(self):
        """When 2D measurements are cleared, remove synced 3D measurements."""
        to_remove = [m for m in self._measurements_3d if m.get("source") == "2D_synced"]
        for m in to_remove:
            for name in m.get("actor_names", []):
                if hasattr(self, "plotter") and self.plotter is not None:
                    try:
                        self.plotter.remove_actor(name)
                    except Exception:
                        pass
                if name in self._measurement_actor_names:
                    self._measurement_actor_names.remove(name)
            self._measurements_3d.remove(m)
        if hasattr(self, "plotter") and self.plotter is not None:
            self.plotter.render()
        self._notify_metadata_changed()

    # ── Surgical Planning Landmark Methods (OR Mode: ENTRY + TARGET) ─────────

    def set_planning_picking_mode(self, mode: str):
        """Sets active landmark/structure picking mode (IDLE, SET_ENTRY, SET_TARGET, ADD_STRUCTURE)."""
        valid_mode = mode if mode in ("SET_ENTRY", "SET_TARGET", "ADD_STRUCTURE") else "IDLE"
        self._planning_picking_mode = valid_mode

        if valid_mode == "SET_ENTRY":
            if getattr(self, "_measuring_3d", False):
                self.set_measuring_3d(False)
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  🎯 Select ENTRY landmark: click on 3D surface or 2D slice")
        elif valid_mode == "SET_TARGET":
            if getattr(self, "_measuring_3d", False):
                self.set_measuring_3d(False)
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  🎯 Select TARGET landmark: click on 3D surface or 2D slice")
        elif valid_mode == "ADD_STRUCTURE":
            if getattr(self, "_measuring_3d", False):
                self.set_measuring_3d(False)
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  ⚠️ Select structure location: click on 3D surface or 2D slice")
        else:
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  🎯 Landmark picking: IDLE")

        if hasattr(self, "panel_2d") and self.panel_2d is not None:
            self.panel_2d.set_planning_picking_mode(valid_mode)

    def is_planning_picking(self) -> bool:
        return getattr(self, "_planning_picking_mode", "IDLE") != "IDLE"

    def set_entry_point(self, x: float, y: float, z: float, source_view: str = "3D") -> PlanningPoint:
        """Stores physical coordinates (X, Y, Z) in mm for ENTRY and updates visual markers."""
        pt = self.surgical_plan.set_entry(x, y, z, source_view=source_view)
        self._render_planning_marker(pt)
        self._render_planned_route()
        self._sync_planning_landmarks_to_2d()
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText(f"  🎯 ENTRY set to ({x:.1f}, {y:.1f}, {z:.1f}) mm")
        return pt

    def set_target_point(self, x: float, y: float, z: float, source_view: str = "3D") -> PlanningPoint:
        """Stores physical coordinates (X, Y, Z) in mm for TARGET and updates visual markers."""
        pt = self.surgical_plan.set_target(x, y, z, source_view=source_view)
        self._render_planning_marker(pt)
        self._render_planned_route()
        self._sync_planning_landmarks_to_2d()
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText(f"  🎯 TARGET set to ({x:.1f}, {y:.1f}, {z:.1f}) mm")
        return pt

    def clear_entry_point(self):
        """Removes ENTRY landmark, leaving TARGET intact."""
        self.surgical_plan.clear_entry()
        self._remove_planning_marker("ENTRY")
        self._render_planned_route()
        self._sync_planning_landmarks_to_2d()
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText("  🎯 ENTRY landmark cleared")

    def clear_target_point(self):
        """Removes TARGET landmark, leaving ENTRY intact."""
        self.surgical_plan.clear_target()
        self._remove_planning_marker("TARGET")
        self._render_planned_route()
        self._sync_planning_landmarks_to_2d()
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText("  🎯 TARGET landmark cleared")

    def clear_both_planning_points(self):
        """Removes both ENTRY and TARGET landmarks and the planned route."""
        self.surgical_plan.clear_both()
        self._remove_planning_marker("ENTRY")
        self._remove_planning_marker("TARGET")
        self._remove_planned_route()
        self._sync_planning_landmarks_to_2d()
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText("  🎯 Planning landmarks and route cleared")

    def view_entry_point(self):
        """Navigates 2D slice panel and MPR viewports to the physical coordinates of ENTRY."""
        if not self.surgical_plan.entry_point:
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  ⚠️ ENTRY point not marked")
            return
        pt = self.surgical_plan.entry_point
        if hasattr(self, "mpr_view") and self.mpr_view:
            self.mpr_view.snap_to_volume_point(pt.x_mm, pt.y_mm, pt.z_mm)
        if hasattr(self, "panel_2d") and self.panel_2d:
            self.panel_2d.set_physical_position(pt.x_mm, pt.y_mm, pt.z_mm)
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText(f"  🎯 Viewing ENTRY: ({pt.x_mm:.1f}, {pt.y_mm:.1f}, {pt.z_mm:.1f}) mm")

    def view_target_point(self):
        """Navigates 2D slice panel and MPR viewports to the physical coordinates of TARGET."""
        if not self.surgical_plan.target_point:
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  ⚠️ TARGET point not marked")
            return
        pt = self.surgical_plan.target_point
        if hasattr(self, "mpr_view") and self.mpr_view:
            self.mpr_view.snap_to_volume_point(pt.x_mm, pt.y_mm, pt.z_mm)
        if hasattr(self, "panel_2d") and self.panel_2d:
            self.panel_2d.set_physical_position(pt.x_mm, pt.y_mm, pt.z_mm)
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText(f"  🎯 Viewing TARGET: ({pt.x_mm:.1f}, {pt.y_mm:.1f}, {pt.z_mm:.1f}) mm")

    def view_planned_route(self):
        """Navigates 2D slice panel and MPR viewports to the midpoint of the planned route."""
        route = self.surgical_plan.get_planned_route()
        if not route:
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  ⚠️ Planned route requires both ENTRY and TARGET")
            return

        mx = (route.entry_point.x_mm + route.target_point.x_mm) / 2.0
        my = (route.entry_point.y_mm + route.target_point.y_mm) / 2.0
        mz = (route.entry_point.z_mm + route.target_point.z_mm) / 2.0

        if hasattr(self, "mpr_view") and self.mpr_view:
            self.mpr_view.snap_to_volume_point(mx, my, mz)
        if hasattr(self, "panel_2d") and self.panel_2d:
            self.panel_2d.set_physical_position(mx, my, mz)
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText(f"  🚀 Planned Route: {route.length_mm:.1f} mm · Midpoint ({mx:.1f}, {my:.1f}, {mz:.1f}) mm")

    def clear_planned_route(self):
        """Clears the planned route by clearing planning landmarks."""
        self.clear_both_planning_points()

    def set_planned_route_visible(self, visible: bool):
        """Toggles visibility of the 3D planned route in the scene."""
        self._planned_route_visible = bool(visible)
        if hasattr(self, "plotter") and self.plotter is not None:
            act = self.plotter.actors.get("or_planned_route")
            if act:
                act.SetVisibility(self._planned_route_visible)
            for k in list(self.plotter.actors.keys()):
                if "or_planned_route_label" in k:
                    self.plotter.actors[k].SetVisibility(self._planned_route_visible)
            self.plotter.render()

    def _render_planning_marker(self, pt: PlanningPoint):
        """Renders distinct 3D sphere marker and text label in plotter."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return

        is_entry = (pt.point_type == "ENTRY")
        marker_name = "or_entry_marker" if is_entry else "or_target_marker"
        label_name = "or_entry_label" if is_entry else "or_target_label"
        color = "#00ff7f" if is_entry else "#ff3366"  # Distinct Emerald Green vs Coral Red
        label_text = "ENTRY" if is_entry else "TARGET"

        radius = 2.8
        if getattr(self, "mesh_bounds", None) is not None:
            xmin, xmax, ymin, ymax, zmin, zmax = self.mesh_bounds
            diag = ((xmax - xmin)**2 + (ymax - ymin)**2 + (zmax - zmin)**2)**0.5
            radius = max(2.0, diag * 0.016)

        try:
            sphere = pv.Sphere(radius=radius, center=(pt.x_mm, pt.y_mm, pt.z_mm))
            self.plotter.add_mesh(
                sphere,
                name=marker_name,
                color=color,
                specular=0.7,
                specular_power=25,
                ambient=0.4,
                render=False
            )
            self.plotter.add_point_labels(
                [[pt.x_mm, pt.y_mm, pt.z_mm]],
                [label_text],
                name=label_name,
                point_color=color,
                point_size=1,
                text_color="#ffffff",
                fill_shape=True,
                shape_color="#111111",
                shape_opacity=0.85,
                font_size=11,
                always_visible=True,
                render=False
            )
            self.plotter.render()
        except Exception as exc:
            print(f"[Viewer3D] Error rendering {pt.point_type} marker: {exc}")

    def _remove_planning_marker(self, point_type: str):
        """Removes the 3D marker and label for the specified landmark."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        is_entry = (point_type == "ENTRY")
        marker_name = "or_entry_marker" if is_entry else "or_target_marker"
        label_name = "or_entry_label" if is_entry else "or_target_label"
        try:
            self.plotter.remove_actor(marker_name, render=False)
            self.plotter.remove_actor(label_name, render=False)
            if f"{label_name}-labels" in self.plotter.actors:
                self.plotter.remove_actor(f"{label_name}-labels", render=False)
            if f"{label_name}-points" in self.plotter.actors:
                self.plotter.remove_actor(f"{label_name}-points", render=False)
            self.plotter.render()
        except Exception:
            pass

    def _render_planned_route(self):
        """Renders geometric planned route line/tube and length label connecting Entry and Target."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        route = self.surgical_plan.get_planned_route()
        if not route or route.length_mm < 1e-3:
            self._remove_planned_route()
            return

        p1 = (route.entry_point.x_mm, route.entry_point.y_mm, route.entry_point.z_mm)
        p2 = (route.target_point.x_mm, route.target_point.y_mm, route.target_point.z_mm)

        try:
            line_mesh = pv.Line(p1, p2)
            radius = 1.2
            if getattr(self, "mesh_bounds", None) is not None:
                xmin, xmax, ymin, ymax, zmin, zmax = self.mesh_bounds
                diag = ((xmax - xmin)**2 + (ymax - ymin)**2 + (zmax - zmin)**2)**0.5
                radius = max(0.8, diag * 0.007)

            tube_mesh = line_mesh.tube(radius=radius)
            self.plotter.add_mesh(
                tube_mesh,
                name="or_planned_route",
                color="#00e5ff",  # Distinct Surgical Cyan
                specular=0.8,
                specular_power=30,
                ambient=0.4,
                render=False
            )

            midpoint = [
                (p1[0] + p2[0]) / 2.0,
                (p1[1] + p2[1]) / 2.0,
                (p1[2] + p2[2]) / 2.0,
            ]
            self.plotter.add_point_labels(
                [midpoint],
                [f"Route: {route.length_mm:.1f} mm"],
                name="or_planned_route_label",
                point_color="#00e5ff",
                point_size=1,
                text_color="#ffffff",
                fill_shape=True,
                shape_color="#111111",
                shape_opacity=0.85,
                font_size=11,
                always_visible=True,
                render=False
            )
            self.plotter.render()
            if hasattr(self, "surgical_plan") and self.surgical_plan.has_surgical_corridor():
                self._render_surgical_corridor()
            if hasattr(self, "surgical_plan") and self.surgical_plan.has_virtual_instrument() and self.surgical_plan.instrument_visible:
                self._render_virtual_instrument()
            if hasattr(self, "surgical_plan") and self.surgical_plan.has_live_deviation() and self.surgical_plan.deviation_visible:
                self._render_live_deviation()
        except Exception as exc:
            print(f"[Viewer3D] Error rendering planned route: {exc}")

    def _remove_planned_route(self):
        """Removes the planned route actor and label from the 3D scene."""
        self._remove_surgical_corridor()
        self._remove_virtual_instrument()
        self._remove_live_deviation()
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        try:
            self.plotter.remove_actor("or_planned_route", render=False)
            self.plotter.remove_actor("or_planned_route_label", render=False)
            if "or_planned_route_label-labels" in self.plotter.actors:
                self.plotter.remove_actor("or_planned_route_label-labels", render=False)
            if "or_planned_route_label-points" in self.plotter.actors:
                self.plotter.remove_actor("or_planned_route_label-points", render=False)
            self.plotter.render()
        except Exception:
            pass

    # ── Surgical Corridor Management Methods (OR Mode: Feature 4) ─────────────

    def set_surgical_corridor_enabled(self, enabled: bool):
        """Enables or disables 3D/2D rendering of the surgical corridor."""
        self.surgical_plan.set_corridor_enabled(enabled)
        if enabled and self.surgical_plan.has_planned_route():
            self._render_surgical_corridor()
        else:
            self._remove_surgical_corridor()
        self._sync_planning_landmarks_to_2d()

    def set_surgical_corridor_radius(self, radius_mm: float):
        """Updates corridor radius in mm and refreshes 3D/2D geometry immediately."""
        self.surgical_plan.set_corridor_radius(radius_mm)
        if self.surgical_plan.has_surgical_corridor():
            self._render_surgical_corridor()
        self._sync_planning_landmarks_to_2d()

    def _render_surgical_corridor(self):
        """Renders 3D translucent cylinder/tube around the planned route in plotter."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        corridor = self.surgical_plan.get_surgical_corridor()
        if not corridor or not corridor.is_enabled or corridor.length_mm < 1e-3:
            self._remove_surgical_corridor()
            return

        p1 = (corridor.route.entry_point.x_mm, corridor.route.entry_point.y_mm, corridor.route.entry_point.z_mm)
        p2 = (corridor.route.target_point.x_mm, corridor.route.target_point.y_mm, corridor.route.target_point.z_mm)

        try:
            line_mesh = pv.Line(p1, p2)
            cylinder_mesh = line_mesh.tube(radius=corridor.radius_mm)
            self.plotter.add_mesh(
                cylinder_mesh,
                name="or_surgical_corridor",
                color="#7c4dff",  # Distinct Translucent Violet
                opacity=0.35,
                specular=0.5,
                ambient=0.3,
                render=False
            )
            midpoint = [
                (p1[0] + p2[0]) / 2.0,
                (p1[1] + p2[1]) / 2.0,
                (p1[2] + p2[2]) / 2.0,
            ]
            self.plotter.add_point_labels(
                [midpoint],
                [f"Corridor: R={corridor.radius_mm:.1f} mm"],
                name="or_surgical_corridor_label",
                point_color="#7c4dff",
                point_size=1,
                text_color="#ffffff",
                fill_shape=True,
                shape_color="#181818",
                shape_opacity=0.85,
                font_size=10,
                always_visible=True,
                render=False
            )
            self.plotter.render()
        except Exception as exc:
            print(f"[Viewer3D] Error rendering surgical corridor: {exc}")

    def _remove_surgical_corridor(self):
        """Removes the surgical corridor actor and label from the 3D scene."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        try:
            self.plotter.remove_actor("or_surgical_corridor", render=False)
            self.plotter.remove_actor("or_surgical_corridor_label", render=False)
            if "or_surgical_corridor_label-labels" in self.plotter.actors:
                self.plotter.remove_actor("or_surgical_corridor_label-labels", render=False)
            if "or_surgical_corridor_label-points" in self.plotter.actors:
                self.plotter.remove_actor("or_surgical_corridor_label-points", render=False)
            self.plotter.render()
        except Exception:
            pass

    # ── Virtual Instrument Management Methods (OR Mode: Feature 5) ─────────────

    def set_virtual_instrument_visible(self, visible: bool):
        """Enables or disables 3D/2D rendering of the virtual instrument."""
        self.surgical_plan.set_instrument_visible(visible)
        if visible and self.surgical_plan.has_planned_route():
            self._render_virtual_instrument()
        else:
            self._remove_virtual_instrument()
        self._sync_planning_landmarks_to_2d()

    def set_virtual_instrument_depth(self, depth_mm: float):
        """Updates virtual instrument insertion depth in mm and refreshes 3D/2D geometry."""
        self.surgical_plan.set_insertion_depth(depth_mm)
        if self.surgical_plan.has_virtual_instrument() and self.surgical_plan.instrument_visible:
            self._render_virtual_instrument()
        self._sync_planning_landmarks_to_2d()

    def set_virtual_instrument_diameter(self, diameter_mm: float):
        """Updates virtual instrument diameter in mm and refreshes 3D/2D geometry."""
        self.surgical_plan.set_instrument_diameter(diameter_mm)
        if self.surgical_plan.has_virtual_instrument() and self.surgical_plan.instrument_visible:
            self._render_virtual_instrument()
        self._sync_planning_landmarks_to_2d()

    def view_virtual_instrument(self):
        """Centers the 3D camera and snaps 2D/MPR slice views to the current virtual tip position."""
        inst = self.surgical_plan.get_virtual_instrument() if hasattr(self, "surgical_plan") else None
        if not inst:
            return
        tip_x, tip_y, tip_z = inst.tip_position_mm
        if hasattr(self, "plotter") and self.plotter is not None:
            self.plotter.camera.focal_point = (tip_x, tip_y, tip_z)
            self.plotter.render()
        if hasattr(self, "_snap_slice_viewers"):
            self._snap_slice_viewers(tip_x, tip_y, tip_z)
        elif hasattr(self, "snap_to_volume_point"):
            self.snap_to_volume_point(tip_x, tip_y, tip_z)
        elif hasattr(self, "panel_2d") and hasattr(self.panel_2d, "set_physical_position"):
            self.panel_2d.set_physical_position(tip_x, tip_y, tip_z)

    def _render_virtual_instrument(self):
        """Renders 3D cylinder shaft and tip marker for the virtual instrument in plotter."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        inst = self.surgical_plan.get_virtual_instrument() if hasattr(self, "surgical_plan") else None
        if not inst or not inst.is_visible:
            self._remove_virtual_instrument()
            return

        ex, ey, ez = inst.route.entry_point.coordinates
        tip_x, tip_y, tip_z = inst.tip_position_mm
        depth = inst.insertion_depth_mm
        radius = inst.diameter_mm / 2.0

        try:
            # 1. Shaft Cylinder (rendered when depth > 1e-3)
            if depth > 1e-3:
                line_mesh = pv.Line((ex, ey, ez), (tip_x, tip_y, tip_z))
                cylinder_mesh = line_mesh.tube(radius=radius)
                self.plotter.add_mesh(
                    cylinder_mesh,
                    name="or_virtual_instrument",
                    color="#ffd600",  # High-visibility Gold / Amber
                    opacity=0.85,
                    specular=0.6,
                    ambient=0.4,
                    render=False
                )
            else:
                self.plotter.remove_actor("or_virtual_instrument", render=False)

            # 2. Tip Marker (Sphere at tip position)
            tip_sphere = pv.Sphere(radius=max(1.5, radius * 1.2), center=(tip_x, tip_y, tip_z))
            self.plotter.add_mesh(
                tip_sphere,
                name="or_virtual_instrument_tip",
                color="#ffffff",  # White tip highlight
                opacity=1.0,
                specular=0.8,
                ambient=0.5,
                render=False
            )

            # 3. Tip Label
            self.plotter.add_point_labels(
                [[tip_x, tip_y, tip_z]],
                [f"Tip: {depth:.1f} mm"],
                name="or_virtual_instrument_label",
                point_color="#ffd600",
                point_size=1,
                text_color="#ffffff",
                fill_shape=True,
                shape_color="#181818",
                shape_opacity=0.85,
                font_size=10,
                always_visible=True,
                render=False
            )
            self.plotter.render()
        except Exception as exc:
            print(f"[Viewer3D] Error rendering virtual instrument: {exc}")

    def _remove_virtual_instrument(self):
        """Removes virtual instrument actors and label from the 3D scene."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        try:
            self.plotter.remove_actor("or_virtual_instrument", render=False)
            self.plotter.remove_actor("or_virtual_instrument_tip", render=False)
            self.plotter.remove_actor("or_virtual_instrument_label", render=False)
            if "or_virtual_instrument_label-labels" in self.plotter.actors:
                self.plotter.remove_actor("or_virtual_instrument_label-labels", render=False)
            if "or_virtual_instrument_label-points" in self.plotter.actors:
                self.plotter.remove_actor("or_virtual_instrument_label-points", render=False)
            self.plotter.render()
        except Exception:
            pass

    # ── Live Deviation Management Methods (OR Mode: Feature 6) ────────────────

    def set_live_deviation_visible(self, visible: bool):
        """Enables or disables 3D/2D rendering of simulated current instrument and deviation connector."""
        self.surgical_plan.set_deviation_visible(visible)
        if visible and self.surgical_plan.has_planned_route():
            self._render_live_deviation()
        else:
            self._remove_live_deviation()
        self._sync_planning_landmarks_to_2d()

    def set_live_deviation_offsets(self, dx: float, dy: float, dz: float):
        """Updates simulation offsets in physical mm and refreshes 3D/2D geometry."""
        self.surgical_plan.set_deviation_offsets(dx, dy, dz)
        if self.surgical_plan.has_live_deviation() and self.surgical_plan.deviation_visible:
            self._render_live_deviation()
        self._sync_planning_landmarks_to_2d()

    def set_live_deviation_angles(self, yaw_deg: float, pitch_deg: float):
        """Updates simulation yaw/pitch angles in degrees and refreshes 3D/2D geometry."""
        self.surgical_plan.set_deviation_angles(yaw_deg, pitch_deg)
        if self.surgical_plan.has_live_deviation() and self.surgical_plan.deviation_visible:
            self._render_live_deviation()
        self._sync_planning_landmarks_to_2d()

    def reset_live_deviation_to_planned(self):
        """Resets all simulation offsets and angles to 0.0, aligning the current instrument with the Planned Route."""
        self.surgical_plan.reset_deviation_to_planned()
        if self.surgical_plan.has_live_deviation() and self.surgical_plan.deviation_visible:
            self._render_live_deviation()
        self._sync_planning_landmarks_to_2d()

    def _render_live_deviation(self):
        """Renders 3D simulated instrument, current tip, deviation connector line, and label."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        pose = self.surgical_plan.get_current_instrument_pose() if hasattr(self, "surgical_plan") else None
        if not pose or not pose.is_visible:
            self._remove_live_deviation()
            return

        cx, cy, cz = pose.current_tip_position_mm
        nx, ny, nz = pose.nearest_planned_point_mm
        dx_vec, dy_vec, dz_vec = pose.current_direction_vector
        route_len = pose.route.length_mm
        radius = pose.diameter_mm / 2.0

        # Shaft back point: tip - length * dir
        sx = cx - route_len * dx_vec
        sy = cy - route_len * dy_vec
        sz = cz - route_len * dz_vec

        try:
            # 1. Simulated Current Instrument Shaft (Coral/Red cylinder)
            shaft_line = pv.Line((sx, sy, sz), (cx, cy, cz))
            shaft_mesh = shaft_line.tube(radius=radius)
            self.plotter.add_mesh(
                shaft_mesh,
                name="or_live_instrument",
                color="#ff5252",  # Coral / Red for current simulated instrument
                opacity=0.85,
                specular=0.6,
                ambient=0.4,
                render=False
            )

            # 2. Simulated Current Tip Marker (Sphere at current tip)
            tip_sphere = pv.Sphere(radius=max(1.8, radius * 1.3), center=(cx, cy, cz))
            self.plotter.add_mesh(
                tip_sphere,
                name="or_live_instrument_tip",
                color="#ff1744",  # Vivid red tip
                opacity=1.0,
                specular=0.8,
                ambient=0.5,
                render=False
            )

            # 3. Deviation Connector Line from Nearest Planned Point to Current Tip
            if pose.lateral_deviation_mm > 0.1:
                dev_line = pv.Line((nx, ny, nz), (cx, cy, cz))
                dev_mesh = dev_line.tube(radius=max(0.5, radius * 0.4))
                self.plotter.add_mesh(
                    dev_mesh,
                    name="or_live_deviation_line",
                    color="#ff1744",  # High-visibility red connector
                    opacity=0.9,
                    render=False
                )
            else:
                self.plotter.remove_actor("or_live_deviation_line", render=False)

            # 4. Neutral Deviation Label at midpoint of connector
            mid_pt = [(nx + cx) / 2.0, (ny + cy) / 2.0, (nz + cz) / 2.0]
            label_text = f"Dev: {pose.lateral_deviation_mm:.1f} mm"
            if abs(pose.yaw_deg) > 0.1 or abs(pose.pitch_deg) > 0.1:
                label_text += f" | {pose.angular_deviation_deg:.1f}°"

            self.plotter.add_point_labels(
                [mid_pt],
                [label_text],
                name="or_live_deviation_label",
                point_color="#ff1744",
                point_size=1,
                text_color="#ffffff",
                fill_shape=True,
                shape_color="#181818",
                shape_opacity=0.85,
                font_size=10,
                always_visible=True,
                render=False
            )
            self.plotter.render()
        except Exception as exc:
            print(f"[Viewer3D] Error rendering live deviation: {exc}")

    def _remove_live_deviation(self):
        """Removes simulated current instrument actors, tip, connector, and labels from 3D scene."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        try:
            self.plotter.remove_actor("or_live_instrument", render=False)
            self.plotter.remove_actor("or_live_instrument_tip", render=False)
            self.plotter.remove_actor("or_live_deviation_line", render=False)
            self.plotter.remove_actor("or_live_deviation_label", render=False)
            if "or_live_deviation_label-labels" in self.plotter.actors:
                self.plotter.remove_actor("or_live_deviation_label-labels", render=False)
            if "or_live_deviation_label-points" in self.plotter.actors:
                self.plotter.remove_actor("or_live_deviation_label-points", render=False)
            self.plotter.render()
        except Exception:
            pass

    def _sync_planning_landmarks_to_2d(self):
        """Passes active Entry, Target, Avoid Structures, Surgical Corridor, Virtual Instrument, and Live Deviation to 2D slice panel and MPR views."""
        entry = self.surgical_plan.entry_point.coordinates if self.surgical_plan.entry_point else None
        target = self.surgical_plan.target_point.coordinates if self.surgical_plan.target_point else None
        avoid_structs = self.surgical_plan.get_avoid_structures() if hasattr(self, "surgical_plan") else []
        corridor = self.surgical_plan.get_surgical_corridor() if hasattr(self, "surgical_plan") else None
        instrument = self.surgical_plan.get_virtual_instrument() if hasattr(self, "surgical_plan") else None
        pose = self.surgical_plan.get_current_instrument_pose() if hasattr(self, "surgical_plan") else None
        if hasattr(self, "panel_2d") and self.panel_2d is not None:
            self.panel_2d.set_planning_landmarks(entry, target)
            self.panel_2d.set_avoid_structures(avoid_structs)
            self.panel_2d.set_surgical_corridor(corridor)
            self.panel_2d.set_virtual_instrument(instrument)
            self.panel_2d.set_current_instrument_pose(pose)
        if hasattr(self, "mpr_view") and self.mpr_view is not None:
            self.mpr_view.set_planned_route(entry, target)
            self.mpr_view.set_avoid_structures(avoid_structs)
            self.mpr_view.set_surgical_corridor(corridor)
            self.mpr_view.set_virtual_instrument(instrument)
            self.mpr_view.set_current_instrument_pose(pose)



    def _on_2d_planning_point_selected(self, pt_type: str, x_mm: float, y_mm: float, z_mm: float):
        """Slot called when 2D slice click commits an ENTRY, TARGET, or STRUCTURE point."""
        if pt_type == "ENTRY":
            self.set_entry_point(x_mm, y_mm, z_mm, source_view="2D")
        elif pt_type == "TARGET":
            self.set_target_point(x_mm, y_mm, z_mm, source_view="2D")
        elif pt_type == "STRUCTURE":
            self.add_avoid_structure(x_mm, y_mm, z_mm, source_view="2D")
        self.set_planning_picking_mode("IDLE")

    def restore_surgical_plan_snapshot(self, snapshot: dict) -> bool:
        """Restores planning state from snapshot and updates 3D, 2D, and MPR views."""
        # 1. Clear 3D actors
        self._remove_planning_marker("ENTRY")
        self._remove_planning_marker("TARGET")
        self._remove_planned_route()
        self._remove_all_avoid_structure_actors()
        self._remove_surgical_corridor()
        self._remove_virtual_instrument()
        self._remove_live_deviation()

        # 2. Restore session
        success = self.surgical_plan.restore_snapshot(snapshot)
        if not success:
            return False

        # 3. Render 3D actors
        if self.surgical_plan.entry_point:
            self._render_planning_marker(self.surgical_plan.entry_point)
        if self.surgical_plan.target_point:
            self._render_planning_marker(self.surgical_plan.target_point)
        if self.surgical_plan.has_planned_route():
            self._render_planned_route()
        for struct in self.surgical_plan.get_avoid_structures():
            self._render_avoid_structure(struct)
        if self.surgical_plan.has_surgical_corridor():
            self._render_surgical_corridor()
        if self.surgical_plan.has_virtual_instrument() and self.surgical_plan.instrument_visible:
            self._render_virtual_instrument()
        if self.surgical_plan.has_live_deviation() and self.surgical_plan.deviation_visible:
            self._render_live_deviation()

        # 4. Synchronize 2D & MPR
        self._sync_planning_landmarks_to_2d()
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText("  📂 Restored surgical plan version")
        return True

    # ── Before vs After Comparison Methods (OR Mode: Feature 8) ───────────────

    def start_before_after_comparison(self, before_scan: dict, after_scan: dict, mode: str = "TOGGLE") -> bool:
        """Initiates visual comparison between Before and After scans for the same patient.
        
        Strictly preserves the current active surgical plan and avoids duplicate viewer creation.
        """
        mrn = str(self.current_patient.get("mrn", "") if self.current_patient else "").strip() or None
        if not self.comparison.set_before_scan(before_scan, mrn):
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(f"  ❌ {self.comparison.validation_error}")
            return False

        if not self.comparison.set_after_scan(after_scan, mrn):
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(f"  ❌ {self.comparison.validation_error}")
            return False

        if not self.comparison.can_compare():
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(f"  ❌ {self.comparison.validation_error or 'Both Before and After scans required'}")
            return False

        # Cache active After volume & mesh data from currently loaded state
        if self.comparison.after_volume is None and hasattr(self, "mpr_view"):
            self.comparison.after_volume = getattr(self.mpr_view, "vol_data", None)
            self.comparison.after_pixel_spacing = getattr(self.mpr_view, "pixel_spacing", (1.0, 1.0))
            self.comparison.after_slice_thickness = getattr(self.mpr_view, "slice_thickness", 1.0)
            self.comparison.after_anatomy = getattr(self.mpr_view, "current_anatomy", "general")
        if self.comparison.after_mesh is None:
            self.comparison.after_mesh = getattr(self, "_bone_mesh", None)

        # Pre-load & cache Before scan volume data ONCE
        before_path = before_scan.get("file_path", "")
        if before_path and os.path.isdir(before_path) and self.comparison.before_volume is None:
            try:
                from dicom_engine import load_volume_for_mpr
                vol, sp, z_sp, anat = load_volume_for_mpr(before_path)
                self.comparison.before_volume = vol
                self.comparison.before_pixel_spacing = sp
                self.comparison.before_slice_thickness = z_sp
                self.comparison.before_anatomy = anat
            except Exception as exc:
                print(f"[Viewer3D] Warning: could not load Before volume: {exc}")

        # Pre-load & cache Before scan bone mesh ONCE
        if before_path and os.path.isdir(before_path) and self.comparison.before_mesh is None:
            try:
                from dicom_engine import build_meshes_from_folder
                preset = "skull" if "skull" in before_path.lower() else "body"
                mset = build_meshes_from_folder(before_path, preset=preset)
                if mset and mset.bone_mesh:
                    self.comparison.before_mesh = mset.bone_mesh
            except Exception as exc:
                print(f"[Viewer3D] Warning: could not load Before mesh: {exc}")

        # Start comparison
        self.comparison.start_comparison(mode)
        self._apply_comparison_visualization()
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText(f"  ⚖️ Before vs After comparison active [{mode}]")
        return True

    def exit_before_after_comparison(self):
        """Exits comparison mode, cleans up comparison actors, and restores active view."""
        self.comparison.exit_comparison()
        self._remove_comparison_actors()

        # Restore normal active bone mesh in 3D
        if getattr(self, "_bone_mesh", None) is not None:
            self._render_normal_bone(self._bone_mesh)

        # Restore 2D slice viewer
        if hasattr(self, "panel_2d") and self.panel_2d is not None:
            self.panel_2d.set_comparison_state(False)

        # Restore MPR view
        if hasattr(self, "mpr_view") and self.mpr_view is not None:
            self.mpr_view.set_comparison_view(None)

        self.plotter.render()
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText("  Comparison inactive")

    def toggle_before_after_view(self, target: str | None = None):
        """Toggles between BEFORE and AFTER in TOGGLE mode."""
        if not self.comparison.is_active:
            return
        if target in ("BEFORE", "AFTER"):
            self.comparison.current_view = target
        else:
            self.comparison.toggle_view()
        self._apply_comparison_visualization()

    def set_comparison_mode(self, mode: str):
        """Updates comparison display mode ('TOGGLE', 'SIDE_BY_SIDE', 'OVERLAY')."""
        if not self.comparison.is_active:
            return
        self.comparison.set_display_mode(mode)
        self._apply_comparison_visualization()

    def set_comparison_overlay_opacity(self, opacity: float):
        """Updates overlay opacity in 2D and 3D views."""
        self.comparison.set_overlay_opacity(opacity)
        if self.comparison.is_active:
            if hasattr(self, "panel_2d") and self.panel_2d is not None:
                self.panel_2d.set_comparison_state(
                    True,
                    self.comparison.display_mode,
                    self.comparison.current_view,
                    self.comparison.before_volume,
                    self.comparison.overlay_opacity
                )
            if self.comparison.display_mode == "OVERLAY":
                self._apply_comparison_visualization()

    def _remove_comparison_actors(self):
        """Cleans up all comparison 3D actors from plotter without touching planning actors."""
        actor_names = [
            "comparison_before_mesh",
            "comparison_label",
            "comparison_label-labels",
            "comparison_label-points",
            "comparison_watermark",
            "comparison_watermark-labels",
            "comparison_watermark-points",
        ]
        for name in actor_names:
            try:
                self.plotter.remove_actor(name, render=False)
            except Exception:
                pass

    def _apply_comparison_visualization(self):
        """Applies neutral visual comparison to 3D, 2D, and MPR without modifying planning state."""
        self._remove_comparison_actors()

        # 1. 3D Visualization
        mode = self.comparison.display_mode
        view = self.comparison.current_view

        if mode == "TOGGLE":
            if view == "BEFORE":
                # Render Before mesh in cyan with BEFORE label
                mesh_to_show = self.comparison.before_mesh or getattr(self, "_bone_mesh", None)
                if mesh_to_show is not None:
                    try:
                        self.plotter.remove_actor("bone", render=False)
                    except Exception:
                        pass
                    self.plotter.add_mesh(
                        mesh_to_show,
                        color="#00e5ff",
                        smooth_shading=True,
                        ambient=0.35,
                        diffuse=0.75,
                        opacity=1.0,
                        name="comparison_before_mesh"
                    )
                self.plotter.add_text("PREVIOUS", position="upper_right", font_size=12, color="#00e5ff", name="comparison_label")
            else:  # AFTER
                if getattr(self, "_bone_mesh", None) is not None:
                    self._render_normal_bone(self._bone_mesh)
                self.plotter.add_text("CURRENT", position="upper_right", font_size=12, color="#7cfc00", name="comparison_label")

        elif mode == "SIDE_BY_SIDE":
            # Render After in normal position, Before shifted along X
            if getattr(self, "_bone_mesh", None) is not None:
                self._render_normal_bone(self._bone_mesh)
            mesh_to_shift = self.comparison.before_mesh or getattr(self, "_bone_mesh", None)
            if mesh_to_shift is not None:
                bnds = mesh_to_shift.bounds
                span_x = (bnds[1] - bnds[0]) * 1.25 if bnds else 200.0
                try:
                    shifted_before = mesh_to_shift.copy().translate([-span_x, 0.0, 0.0])
                    self.plotter.add_mesh(
                        shifted_before,
                        color="#00e5ff",
                        smooth_shading=True,
                        ambient=0.35,
                        diffuse=0.75,
                        opacity=1.0,
                        name="comparison_before_mesh"
                    )
                except Exception as exc:
                    print(f"[Viewer3D] Side-by-side shift error: {exc}")
            self.plotter.add_text("BEFORE (Left)  |  AFTER (Right)", position="upper_right", font_size=11, color="#e0e0e0", name="comparison_label")

        elif mode == "OVERLAY":
            # Render After mesh + Before mesh with opacity and 'Unregistered Overlay' watermark
            if getattr(self, "_bone_mesh", None) is not None:
                self._render_normal_bone(self._bone_mesh)
            mesh_overlay = self.comparison.before_mesh or getattr(self, "_bone_mesh", None)
            if mesh_overlay is not None:
                self.plotter.add_mesh(
                    mesh_overlay,
                    color="#00e5ff",
                    smooth_shading=True,
                    opacity=self.comparison.overlay_opacity,
                    name="comparison_before_mesh"
                )
            self.plotter.add_text("Unregistered Overlay", position="lower_left", font_size=11, color="#ffb300", name="comparison_watermark")

        # 2. 2D Slice Viewer Synchronization
        if hasattr(self, "panel_2d") and self.panel_2d is not None:
            self.panel_2d.set_comparison_state(
                True,
                mode,
                view,
                self.comparison.before_volume,
                self.comparison.overlay_opacity
            )

        # 3. MPR View Synchronization
        if hasattr(self, "mpr_view") and self.mpr_view is not None:
            mpr_view_target = view if mode == "TOGGLE" else "AFTER"
            self.mpr_view.set_comparison_view(
                mpr_view_target,
                self.comparison.before_volume,
                (self.comparison.before_pixel_spacing, self.comparison.before_slice_thickness)
            )

        self.plotter.render()

    # ── Structures to Avoid Management Methods (OR Mode: Feature 3) ─────────

    def add_avoid_structure(self, x: float, y: float, z: float, radius_mm: float = 5.0, name: str = "", source_view: str = "3D") -> AvoidStructure:
        """Stores physical coordinates (X, Y, Z) in mm for a structure to avoid and renders its 3D marker."""
        struct = self.surgical_plan.add_avoid_structure(x, y, z, radius_mm=radius_mm, name=name, source_view=source_view)
        self._render_avoid_structure(struct)
        self._sync_planning_landmarks_to_2d()
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText(f"  ⚠️ Added {struct.name} at ({x:.1f}, {y:.1f}, {z:.1f}) mm · Radius {struct.radius_mm:.1f} mm")
        return struct

    def remove_avoid_structure(self, structure_id: str) -> bool:
        """Removes a single avoid structure by its identifier and removes its 3D actor."""
        struct = self.surgical_plan.get_avoid_structure(structure_id)
        sname = struct.name if struct else structure_id
        removed = self.surgical_plan.remove_avoid_structure(structure_id)
        if removed:
            self._remove_avoid_structure_actor(structure_id)
            self._sync_planning_landmarks_to_2d()
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(f"  ⚠️ Removed {sname}")
        return removed

    def clear_avoid_structures(self):
        """Removes all avoid structures and their 3D actors."""
        self.surgical_plan.clear_avoid_structures()
        self._remove_all_avoid_structure_actors()
        self._sync_planning_landmarks_to_2d()
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText("  ⚠️ All avoid structures cleared")

    def view_avoid_structure(self, structure_id: str):
        """Navigates 2D slice panel and MPR viewports to the physical coordinates of the structure."""
        struct = self.surgical_plan.get_avoid_structure(structure_id)
        if not struct:
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  ⚠️ Structure not found")
            return
        if hasattr(self, "mpr_view") and self.mpr_view:
            self.mpr_view.snap_to_volume_point(struct.center_x_mm, struct.center_y_mm, struct.center_z_mm)
        if hasattr(self, "panel_2d") and self.panel_2d:
            self.panel_2d.set_physical_position(struct.center_x_mm, struct.center_y_mm, struct.center_z_mm)
        if hasattr(self, "info_bar") and self.info_bar:
            self.info_bar.setText(f"  ⚠️ Viewing {struct.name}: ({struct.center_x_mm:.1f}, {struct.center_y_mm:.1f}, {struct.center_z_mm:.1f}) mm")

    def _render_avoid_structure(self, struct: AvoidStructure):
        """Renders 3D sphere marker and text label in plotter for an avoid structure."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        actor_name = f"or_avoid_structure_{struct.structure_id}"
        label_name = f"or_avoid_structure_label_{struct.structure_id}"
        try:
            sphere = pv.Sphere(radius=struct.radius_mm, center=struct.center)
            self.plotter.add_mesh(
                sphere,
                name=actor_name,
                color="#ff9100",  # Distinct Amber Warning
                opacity=0.55,
                specular=0.7,
                specular_power=25,
                ambient=0.4,
                render=False
            )
            self.plotter.add_point_labels(
                [[struct.center_x_mm, struct.center_y_mm, struct.center_z_mm]],
                [struct.name],
                name=label_name,
                point_color="#ff9100",
                point_size=1,
                text_color="#ffffff",
                fill_shape=True,
                shape_color="#181818",
                shape_opacity=0.85,
                font_size=10,
                always_visible=True,
                render=False
            )
            self.plotter.render()
        except Exception as exc:
            print(f"[Viewer3D] Error rendering avoid structure {struct.structure_id}: {exc}")

    def _remove_avoid_structure_actor(self, structure_id: str):
        """Removes the 3D marker and label for the specified avoid structure."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        actor_name = f"or_avoid_structure_{structure_id}"
        label_name = f"or_avoid_structure_label_{structure_id}"
        try:
            self.plotter.remove_actor(actor_name, render=False)
            self.plotter.remove_actor(label_name, render=False)
            if f"{label_name}-labels" in self.plotter.actors:
                self.plotter.remove_actor(f"{label_name}-labels", render=False)
            if f"{label_name}-points" in self.plotter.actors:
                self.plotter.remove_actor(f"{label_name}-points", render=False)
            self.plotter.render()
        except Exception:
            pass

    def _remove_all_avoid_structure_actors(self):
        """Removes all avoid structure actors and labels from the 3D scene."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        actors_to_remove = [k for k in self.plotter.actors.keys() if k.startswith("or_avoid_structure_")]
        for act in actors_to_remove:
            try:
                self.plotter.remove_actor(act, render=False)
            except Exception:
                pass
        self.plotter.render()

    # ── ICU Feature 2: Same-Location Review 3D Marker ─────────────────────────
    def set_same_location_marker(self, x_mm: float, y_mm: float, z_mm: float):
        """Renders 3D sphere marker and text label for ICU Same-Location Review."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        self.clear_same_location_marker()
        actor_name = "icu_same_location_marker"
        label_name = "icu_same_location_label"
        try:
            sphere = pv.Sphere(radius=2.5, center=(float(x_mm), float(y_mm), float(z_mm)))
            self.plotter.add_mesh(
                sphere,
                name=actor_name,
                color="#00e5ff",  # Vibrant Cyan
                opacity=0.9,
                specular=0.8,
                ambient=0.3,
                render=False
            )
            self.plotter.add_point_labels(
                [[float(x_mm), float(y_mm), float(z_mm)]],
                ["Same Location"],
                name=label_name,
                point_color="#00e5ff",
                point_size=1,
                text_color="#ffffff",
                fill_shape=True,
                shape_color="#101010",
                shape_opacity=0.85,
                font_size=10,
                always_visible=True,
                render=False
            )
            self.plotter.render()
        except Exception as exc:
            print(f"[Viewer3D] Error rendering same-location marker: {exc}")

    def clear_same_location_marker(self):
        """Removes the 3D marker and label for ICU Same-Location Review."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        try:
            self.plotter.remove_actor("icu_same_location_marker", render=False)
            self.plotter.remove_actor("icu_same_location_label", render=False)
            if "icu_same_location_label-labels" in self.plotter.actors:
                self.plotter.remove_actor("icu_same_location_label-labels", render=False)
            if "icu_same_location_label-points" in self.plotter.actors:
                self.plotter.remove_actor("icu_same_location_label-points", render=False)
            self.plotter.render()
        except Exception:
            pass

    # ── ICU Feature 4: Annotation Carry-Forward 3D Marker ─────────────────────
    def set_annotation_marker(self, x_mm: float, y_mm: float, z_mm: float, label: str = "Annotation", is_carried: bool = False):
        """Renders 3D marker and text label for ICU Annotation Carry-Forward."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        self.clear_annotation_marker()
        actor_name = "icu_annotation_marker"
        label_name = "icu_annotation_label"
        color = "#e040fb" if is_carried else "#ffb300"  # Magenta if carried, Amber if source
        try:
            sphere = pv.Sphere(radius=2.5, center=(float(x_mm), float(y_mm), float(z_mm)))
            self.plotter.add_mesh(
                sphere,
                name=actor_name,
                color=color,
                opacity=0.9,
                specular=0.8,
                ambient=0.3,
                render=False
            )
            display_text = f"{label} (Carried)" if is_carried else label
            self.plotter.add_point_labels(
                [[float(x_mm), float(y_mm), float(z_mm)]],
                [display_text],
                name=label_name,
                point_color=color,
                point_size=1,
                text_color="#ffffff",
                fill_shape=True,
                shape_color="#101010",
                shape_opacity=0.85,
                font_size=10,
                always_visible=True,
                render=False
            )
            self.plotter.render()
        except Exception as exc:
            print(f"[Viewer3D] Error rendering annotation marker: {exc}")

    def clear_annotation_marker(self):
        """Removes the 3D marker and label for ICU Annotation Carry-Forward."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        try:
            self.plotter.remove_actor("icu_annotation_marker", render=False)
            self.plotter.remove_actor("icu_annotation_label", render=False)
            if "icu_annotation_label-labels" in self.plotter.actors:
                self.plotter.remove_actor("icu_annotation_label-labels", render=False)
            if "icu_annotation_label-points" in self.plotter.actors:
                self.plotter.remove_actor("icu_annotation_label-points", render=False)
            self.plotter.render()
        except Exception:
            pass

    # ── ICU Feature 6: Device Markers 3D Actors ──────────────────────────────
    def set_device_marker(self, x_mm: float, y_mm: float, z_mm: float, device_type: str = "Device", label: str = "", marker_id: str = ""):
        """Renders 3D marker and text label for a user-placed ICU Device Marker."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        mid = str(marker_id or "default")
        actor_name = f"icu_device_marker_{mid}"
        label_name = f"icu_device_label_{mid}"
        color = "#00bfa5"  # Distinct Teal/Mint

        # Remove existing actor if already present
        self.remove_device_marker(mid)

        try:
            sphere = pv.Sphere(radius=2.5, center=(float(x_mm), float(y_mm), float(z_mm)))
            self.plotter.add_mesh(
                sphere,
                name=actor_name,
                color=color,
                opacity=0.9,
                specular=0.8,
                ambient=0.3,
                render=False
            )
            display_text = f"{device_type}: {label}" if label and label != device_type else device_type
            self.plotter.add_point_labels(
                [[float(x_mm), float(y_mm), float(z_mm)]],
                [display_text],
                name=label_name,
                point_color=color,
                point_size=1,
                text_color="#ffffff",
                fill_shape=True,
                shape_color="#101010",
                shape_opacity=0.85,
                font_size=10,
                always_visible=True,
                render=False
            )
            self.plotter.render()
        except Exception as exc:
            print(f"[Viewer3D] Error rendering device marker: {exc}")

    def set_device_markers(self, markers: list[dict] | None):
        """Renders a collection of device markers in 3D view."""
        self.clear_device_markers()
        if not markers:
            return
        for m in markers:
            mid = str(m.get("id", ""))
            x = float(m.get("physical_x_mm", m.get("x", 0.0)))
            y = float(m.get("physical_y_mm", m.get("y", 0.0)))
            z = float(m.get("physical_z_mm", m.get("z", 0.0)))
            dtype = str(m.get("device_type", "Device"))
            lbl = str(m.get("label", ""))
            self.set_device_marker(x, y, z, dtype, lbl, mid)

    def remove_device_marker(self, marker_id: str):
        """Removes an individual device marker and label actor by ID."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        mid = str(marker_id)
        actor_name = f"icu_device_marker_{mid}"
        label_name = f"icu_device_label_{mid}"
        try:
            self.plotter.remove_actor(actor_name, render=False)
            self.plotter.remove_actor(label_name, render=False)
            if f"{label_name}-labels" in self.plotter.actors:
                self.plotter.remove_actor(f"{label_name}-labels", render=False)
            if f"{label_name}-points" in self.plotter.actors:
                self.plotter.remove_actor(f"{label_name}-points", render=False)
            self.plotter.render()
        except Exception:
            pass

    def clear_device_markers(self):
        """Removes all 3D device marker and label actors."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        try:
            actors_to_remove = [
                act for act in self.plotter.actors
                if str(act).startswith("icu_device_marker_") or str(act).startswith("icu_device_label_")
            ]
            for act in actors_to_remove:
                self.plotter.remove_actor(act, render=False)
            self.plotter.render()
        except Exception:
            pass


    # ── Quick Surgical Views Methods (OR Mode: Feature 9) ───────────────────

    def _center_camera_point(self, x: float, y: float, z: float, dist: float = 120.0):
        """Centers the 3D camera focal point on a physical point (X, Y, Z) in mm, preserving viewing angle."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        center = np.array([x, y, z], dtype=float)
        cam = self.plotter.camera
        curr_focal = np.array(cam.focal_point, dtype=float)
        curr_pos = np.array(cam.position, dtype=float)
        direction = curr_pos - curr_focal
        dir_len = np.linalg.norm(direction)
        if dir_len < 1e-4:
            norm_dir = np.array([0.0, -1.0, 0.0], dtype=float)
        else:
            norm_dir = direction / dir_len

        cam.focal_point = tuple(center)
        cam.position = tuple(center + norm_dir * dist)
        self.plotter.render()

    def _frame_camera_bounds(self, bounds: tuple[float, float, float, float, float, float], margin_factor: float = 1.3):
        """Frames the 3D camera smoothly around physical bounding box (xmin, xmax, ymin, ymax, zmin, zmax) in mm."""
        if not hasattr(self, "plotter") or self.plotter is None:
            return
        xmin, xmax, ymin, ymax, zmin, zmax = bounds
        cx = (xmin + xmax) / 2.0
        cy = (ymin + ymax) / 2.0
        cz = (zmin + zmax) / 2.0
        center = np.array([cx, cy, cz], dtype=float)

        dx = max(10.0, xmax - xmin)
        dy = max(10.0, ymax - ymin)
        dz = max(10.0, zmax - zmin)
        diag = np.sqrt(dx ** 2 + dy ** 2 + dz ** 2)
        dist = max(50.0, float(diag * margin_factor))

        cam = self.plotter.camera
        curr_focal = np.array(cam.focal_point, dtype=float)
        curr_pos = np.array(cam.position, dtype=float)
        direction = curr_pos - curr_focal
        dir_len = np.linalg.norm(direction)
        if dir_len < 1e-4:
            norm_dir = np.array([0.0, -1.0, 0.0], dtype=float)
        else:
            norm_dir = direction / dir_len

        cam.focal_point = tuple(center)
        cam.position = tuple(center + norm_dir * dist)
        self.plotter.render()

    def _snap_slice_viewers(self, x_mm: float, y_mm: float, z_mm: float):
        """Helper to snap both 2D slice viewer and MPR viewports to physical point (X, Y, Z) in mm."""
        if hasattr(self, "mpr_view") and self.mpr_view is not None:
            self.mpr_view.snap_to_volume_point(x_mm, y_mm, z_mm)
        if hasattr(self, "panel_2d") and self.panel_2d is not None:
            self.panel_2d.set_physical_position(x_mm, y_mm, z_mm)

    def get_quick_view_availability(self) -> dict[str, bool]:
        """Returns the availability state of all 6 Quick Surgical View presets."""
        plan = getattr(self, "surgical_plan", None)
        if not plan:
            return {
                "ENTRY": False,
                "TARGET": False,
                "ROUTE": False,
                "STRUCTURES": False,
                "INSTRUMENT": False,
                "FULL PLAN": False,
            }

        has_entry = plan.has_entry()
        has_target = plan.has_target()
        has_route = plan.has_planned_route()
        has_structures = len(plan.get_avoid_structures()) > 0
        has_instrument = (
            (plan.has_virtual_instrument() and getattr(plan, "instrument_visible", False)) or
            (plan.has_live_deviation() and getattr(plan, "deviation_visible", False))
        )
        has_any = has_entry or has_target or has_structures or has_instrument

        return {
            "ENTRY": has_entry,
            "TARGET": has_target,
            "ROUTE": has_route,
            "STRUCTURES": has_structures,
            "INSTRUMENT": has_instrument,
            "FULL PLAN": has_any,
        }

    def apply_quick_surgical_view(self, preset: str) -> bool:
        """Applies a Quick Surgical View preset, centering camera, 2D, and MPR without altering surgical planning state.

        Supported presets:
          - "ENTRY": Centers views on Entry point.
          - "TARGET": Centers views on Target point.
          - "ROUTE": Centers 2D/MPR on route midpoint; frames 3D camera to route extent.
          - "STRUCTURES": Centers 2D/MPR on structure centroid; frames 3D camera to all structures.
          - "INSTRUMENT": Centers views on currently visible virtual/live deviation tip.
          - "FULL PLAN": Frames all available planning geometry in 3D, snaps 2D/MPR to centroid.
        """
        preset_clean = preset.strip().upper()
        plan = getattr(self, "surgical_plan", None)
        if not plan:
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  ⚠️ No surgical plan active")
            return False

        if preset_clean == "ENTRY":
            if not plan.has_entry():
                if hasattr(self, "info_bar") and self.info_bar:
                    self.info_bar.setText("  ⚠️ Requires Entry")
                return False
            pt = plan.entry_point
            self.view_entry_point()
            self._center_camera_point(pt.x_mm, pt.y_mm, pt.z_mm)
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(f"  🎯 Quick View: ENTRY ({pt.x_mm:.1f}, {pt.y_mm:.1f}, {pt.z_mm:.1f}) mm")
            self.quick_view_applied.emit("ENTRY")
            return True

        elif preset_clean == "TARGET":
            if not plan.has_target():
                if hasattr(self, "info_bar") and self.info_bar:
                    self.info_bar.setText("  ⚠️ Requires Target")
                return False
            pt = plan.target_point
            self.view_target_point()
            self._center_camera_point(pt.x_mm, pt.y_mm, pt.z_mm)
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(f"  🎯 Quick View: TARGET ({pt.x_mm:.1f}, {pt.y_mm:.1f}, {pt.z_mm:.1f}) mm")
            self.quick_view_applied.emit("TARGET")
            return True

        elif preset_clean == "ROUTE":
            route = plan.get_planned_route()
            if not route:
                if hasattr(self, "info_bar") and self.info_bar:
                    self.info_bar.setText("  ⚠️ Requires Planned Route")
                return False
            ex, ey, ez = route.entry_point.coordinates
            tx, ty, tz = route.target_point.coordinates
            self.view_planned_route()

            corridor = plan.get_surgical_corridor()
            r = corridor.radius_mm if (corridor and corridor.is_enabled) else 10.0
            bounds = (
                min(ex, tx) - r, max(ex, tx) + r,
                min(ey, ty) - r, max(ey, ty) + r,
                min(ez, tz) - r, max(ez, tz) + r
            )
            self._frame_camera_bounds(bounds, margin_factor=1.3)
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(f"  🚀 Quick View: ROUTE ({route.length_mm:.1f} mm)")
            self.quick_view_applied.emit("ROUTE")
            return True

        elif preset_clean == "STRUCTURES":
            structs = plan.get_avoid_structures()
            if not structs:
                if hasattr(self, "info_bar") and self.info_bar:
                    self.info_bar.setText("  ⚠️ No structures marked")
                return False
            if len(structs) == 1:
                s = structs[0]
                self.view_avoid_structure(s.structure_id)
                self._center_camera_point(s.center_x_mm, s.center_y_mm, s.center_z_mm, dist=100.0)
                if hasattr(self, "info_bar") and self.info_bar:
                    self.info_bar.setText(f"  ⚠️ Quick View: STRUCTURE ({s.name})")
                self.quick_view_applied.emit("STRUCTURES")
                return True

            avg_x = float(np.mean([s.center_x_mm for s in structs]))
            avg_y = float(np.mean([s.center_y_mm for s in structs]))
            avg_z = float(np.mean([s.center_z_mm for s in structs]))
            self._snap_slice_viewers(avg_x, avg_y, avg_z)

            bounds = (
                min(s.center_x_mm - s.radius_mm for s in structs),
                max(s.center_x_mm + s.radius_mm for s in structs),
                min(s.center_y_mm - s.radius_mm for s in structs),
                max(s.center_y_mm + s.radius_mm for s in structs),
                min(s.center_z_mm - s.radius_mm for s in structs),
                max(s.center_z_mm + s.radius_mm for s in structs),
            )
            self._frame_camera_bounds(bounds, margin_factor=1.4)
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(f"  ⚠️ Quick View: STRUCTURES ({len(structs)} marked)")
            self.quick_view_applied.emit("STRUCTURES")
            return True

        elif preset_clean == "INSTRUMENT":
            tip_pos = None
            source_lbl = ""
            if plan.has_live_deviation() and getattr(plan, "deviation_visible", False):
                pose = plan.get_current_instrument_pose()
                if pose:
                    tip_pos = pose.current_tip_position_mm
                    source_lbl = "Live Deviation"
                    self._snap_slice_viewers(*tip_pos)
                    self._center_camera_point(*tip_pos, dist=100.0)
            elif plan.has_virtual_instrument() and getattr(plan, "instrument_visible", False):
                inst = plan.get_virtual_instrument()
                if inst:
                    tip_pos = inst.tip_position_mm
                    source_lbl = "Virtual Instrument"
                    self._snap_slice_viewers(*tip_pos)
                    self._center_camera_point(*tip_pos, dist=100.0)

            if tip_pos is None:
                if hasattr(self, "info_bar") and self.info_bar:
                    self.info_bar.setText("  ⚠️ No instrument visible")
                return False

            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(f"  📍 Quick View: INSTRUMENT ({source_lbl})")
            self.quick_view_applied.emit("INSTRUMENT")
            return True

        elif preset_clean == "FULL PLAN":
            points = []
            pad = 5.0
            if plan.has_entry():
                points.append(plan.entry_point.coordinates)
            if plan.has_target():
                points.append(plan.target_point.coordinates)
            if plan.has_planned_route() and getattr(plan, "corridor_enabled", False):
                cr = getattr(plan, "corridor_radius_mm", 5.0)
                ex, ey, ez = plan.entry_point.coordinates
                tx, ty, tz = plan.target_point.coordinates
                points.extend([
                    (ex - cr, ey, ez), (ex + cr, ey, ez),
                    (ex, ey - cr, ez), (ex, ey + cr, ez),
                    (ex, ey, ez - cr), (ex, ey, ez + cr),
                    (tx - cr, ty, tz), (tx + cr, ty, tz),
                    (tx, ty - cr, tz), (tx, ty + cr, tz),
                    (tx, ty, tz - cr), (tx, ty, tz + cr),
                ])
            for s in plan.get_avoid_structures():
                r = s.radius_mm
                cx, cy, cz = s.center
                points.extend([
                    (cx - r, cy, cz), (cx + r, cy, cz),
                    (cx, cy - r, cz), (cx, cy + r, cz),
                    (cx, cy, cz - r), (cx, cy, cz + r),
                ])
            if plan.has_virtual_instrument() and getattr(plan, "instrument_visible", False):
                inst = plan.get_virtual_instrument()
                if inst:
                    points.append(inst.tip_position_mm)
            if plan.has_live_deviation() and getattr(plan, "deviation_visible", False):
                pose = plan.get_current_instrument_pose()
                if pose:
                    points.append(pose.current_tip_position_mm)

            if not points:
                if hasattr(self, "info_bar") and self.info_bar:
                    self.info_bar.setText("  ⚠️ No planning elements to display")
                return False

            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            zs = [p[2] for p in points]

            bounds = (
                min(xs) - pad, max(xs) + pad,
                min(ys) - pad, max(ys) + pad,
                min(zs) - pad, max(zs) + pad,
            )
            mid_x = (min(xs) + max(xs)) / 2.0
            mid_y = (min(ys) + max(ys)) / 2.0
            mid_z = (min(zs) + max(zs)) / 2.0
            self._snap_slice_viewers(mid_x, mid_y, mid_z)
            self._frame_camera_bounds(bounds, margin_factor=1.4)
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  📐 Quick View: FULL PLAN")
            self.quick_view_applied.emit("FULL PLAN")
            return True

        else:
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(f"  ⚠️ Unknown preset: {preset}")
            return False

    # ── Study Information & Scan Metadata Panel Methods ───────────────────────

    def show_study_info(self):
        """Opens and refreshes the safe study metadata panel."""
        if hasattr(self, "study_info_dialog"):
            self.study_info_dialog.update_metadata()
            self.study_info_dialog.show()
            self.study_info_dialog.raise_()
            self.study_info_dialog.activateWindow()
        if hasattr(self, "btn_study_info"):
            self.btn_study_info.blockSignals(True)
            self.btn_study_info.setChecked(True)
            self.btn_study_info.blockSignals(False)

    def hide_study_info(self):
        """Closes the safe study metadata panel."""
        if hasattr(self, "study_info_dialog"):
            self.study_info_dialog.hide()
        if hasattr(self, "btn_study_info"):
            self.btn_study_info.blockSignals(True)
            self.btn_study_info.setChecked(False)
            self.btn_study_info.blockSignals(False)

    def toggle_study_info(self):
        """Toggles visibility of the safe study metadata panel."""
        if hasattr(self, "study_info_dialog") and self.study_info_dialog.isVisible():
            self.hide_study_info()
        else:
            self.show_study_info()

    def is_study_info_visible(self) -> bool:
        """Returns True if the study information panel is currently visible."""
        return self.study_info_dialog.isVisible() if hasattr(self, "study_info_dialog") else False

    def get_safe_study_metadata(self) -> dict:
        """Returns non-sensitive DICOM metadata and real-time viewer state."""
        return extract_safe_metadata(self)

    def _notify_metadata_changed(self):
        """Pushes live state updates to the open study info panel without re-reading DICOM."""
        if hasattr(self, "study_info_dialog") and self.study_info_dialog is not None:
            if self.study_info_dialog.isVisible():
                self.study_info_dialog.update_metadata()

    def _on_study_info_dialog_closed(self):
        """Resets the Study Info button state when the dialog is closed."""
        if hasattr(self, "btn_study_info"):
            self.btn_study_info.blockSignals(True)
            self.btn_study_info.setChecked(False)
            self.btn_study_info.blockSignals(False)

    def _on_bone_mode_changed(self, index: int):
        """Slot for Bone Mode dropdown: 0 = Normal Bone View, 1 = Hounsfield Heatmap."""
        self._sync_bone_mode_buttons(index == 1)
        self.set_density_colormap(index == 1)

    def _set_bone_mode_index(self, idx: int):
        if hasattr(self, "combo_bone_mode"):
            self.combo_bone_mode.blockSignals(True)
            self.combo_bone_mode.setCurrentIndex(idx)
            self.combo_bone_mode.blockSignals(False)
        self._sync_bone_mode_buttons(idx == 1)
        self.set_density_colormap(idx == 1)

    def _sync_bone_mode_buttons(self, active: bool):
        if hasattr(self, "btn_bone_normal") and hasattr(self, "btn_bone_heatmap"):
            self.btn_bone_normal.blockSignals(True)
            self.btn_bone_heatmap.blockSignals(True)
            self.btn_bone_normal.setChecked(not active)
            self.btn_bone_heatmap.setChecked(active)
            self.btn_bone_normal.blockSignals(False)
            self.btn_bone_heatmap.blockSignals(False)

    def _switch_view_mode(self, idx: int):
        self.view_mode_stack.setCurrentIndex(idx)
        self.btn_3d_mode.setChecked(idx == 0)
        self.btn_mpr_mode.setChecked(idx == 1)

    def _toggle_sidebar(self):
        v = not self.sidebar.isVisible()
        self.sidebar.setVisible(v)
        self.toggle_btn.setText("‹" if v else "›")

    def _rebuild_sidebar(self, active_path: str = ""):
        """Populate sidebar with one button per local dataset."""
        while self.sidebar_layout.count():
            item = self.sidebar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        hdr = QLabel("LOCAL SCANS")
        hdr.setStyleSheet(
            "color: #444; font-size: 9px; font-weight: 700;"
        )
        self.sidebar_layout.addWidget(hdr)

        for display_name, folder_path, preset in LOCAL_DATASETS:
            is_active = (folder_path == active_path)
            btn = QPushButton(display_name.split("  ")[0])
            btn.setFixedHeight(32)
            btn.setStyleSheet(
                f"QPushButton {{ background: {'#1e2e1e' if is_active else '#181818'}; "
                f"color: {'#7cfc00' if is_active else '#888'}; "
                "border-radius: 4px; font-size: 11px; text-align: left; padding-left: 8px; } "
                "QPushButton:hover { background: #202020; color: #ccc; }"
            )
            btn.clicked.connect(
                lambda _=False, p=folder_path, pr=preset, n=display_name:
                    self._start_load(p, preset=pr, label=n)
            )
            self.sidebar_layout.addWidget(btn)

        self.sidebar_layout.addStretch()

    def showEvent(self, event):
        """Lazy-load the first local dataset the first time the viewer is shown."""
        super().showEvent(event)
        if not self._initial_load_done and LOCAL_DATASETS:
            self._initial_load_done = True
            first_name, first_path, first_preset = LOCAL_DATASETS[0]
            self._start_load(first_path, preset=first_preset, label=first_name)

    # ── Load pipeline ─────────────────────────────────────────────────────────

    def _start_load(self, folder_path: str, preset: str = "body", label: str = ""):
        """Cancel any running load, show progress screen, start background thread."""

        # Local datasets opened directly from the viewer do not come
        # through MainWindow.show_3d_direct(), so create a synthetic
        # patient/scan context for report export.
        if not getattr(self, "current_patient", None):
            folder_name = os.path.basename(
                os.path.normpath(folder_path)
            )

            self.current_patient = {
                "_is_local": True,
                "name": label or folder_name,
                "mrn": f"LOCAL-{folder_name.upper()}",
                "age": "—",
                "sex": "—",
                "scans": 1,
            }

        self.current_scan = {
            "type": "CT",
            "date": "Local dataset",
            "description": label or os.path.basename(folder_path),
            "file_path": folder_path,
            "slice_count": sum(
                1
                for f in os.listdir(folder_path)
                if f.lower().endswith((".dcm", ".ima"))
            ),
        }
        if self._loader and self._loader.isRunning():
            try:
                self._loader.finished.disconnect()
                self._loader.progress.disconnect()
                self._loader.failed.disconnect()
            except Exception:
                pass
            self._loader.requestInterruption()
            self._loader.wait(2000)

        self._active_path = folder_path
        self.scan_label.setText(label or os.path.basename(folder_path))
        self.status_label.setText("Reading DICOM slices…")
        self.state_stack.setCurrentIndex(self._PAGE_LOADING)

        self._loader = DicomLoader(folder_path, preset=preset)
        self._loader.progress.connect(self.status_label.setText)
        self._loader.finished.connect(self._on_meshes_ready)
        self._loader.failed.connect(self._on_load_failed)
        self._loader.start()

    def _on_meshes_ready(self, meshset: MeshSet):
        """Slot: called on UI thread when DicomLoader completes."""
        self.plotter.clear()

        # Warm key + cool fill lighting setup
        self.plotter.remove_all_lights()
        self.plotter.add_light(pv.Light(
            position=(1.0, -1.0, 1.5),
            focal_point=(0.0, 0.0, 0.0),
            intensity=0.90,
            color=(1.00, 0.97, 0.88),
            light_type="scene light",
        ))
        self.plotter.add_light(pv.Light(
            position=(-1.2, 1.0, -0.6),
            focal_point=(0.0, 0.0, 0.0),
            intensity=0.35,
            color=(0.75, 0.85, 1.00),
            light_type="scene light",
        ))

        self.bone_actor, _ = meshset.add_to_plotter(
            self.plotter,
            density_mode=self._density_active,
        )
        self.organ_actors = {}
        self.organ_meshes = {}
        self.organ_visible = {}
        self.skeleton_mode = "solid"

        self.mesh_bounds = meshset.bone_mesh.bounds
        # Kept so cross-section clipping and the density colormap can re-add the
        # mesh without re-running the whole DICOM -> mesh pipeline.
        self._bone_mesh = meshset.bone_mesh
        self._original_mesh = meshset.bone_mesh
        self._clipped_mesh = None
        self._meshset = meshset
        self._voice_zoom_level = 1.0
        self._clip_active = False
        self._clip_axis = "y"
        self._clip_fraction = 0.5
        self._clip_inverted = False
        self._density_active = False
        self._spin_timer.stop()

        if hasattr(self, "combo_bone_mode"):
            self.combo_bone_mode.blockSignals(True)
            self.combo_bone_mode.setCurrentIndex(0)
            self.combo_bone_mode.blockSignals(False)
        self._sync_bone_mode_buttons(False)
        if hasattr(self, "legend_card"):
            self.legend_card.setVisible(False)
            self._reposition_legend()

        if hasattr(self, "btn_clip_toggle"):
            self.btn_clip_toggle.blockSignals(True)
            self.btn_clip_toggle.setChecked(False)
            self.btn_clip_toggle.setText("✂ Clip: OFF ▼")
            self.btn_clip_toggle.blockSignals(False)
        if hasattr(self, "combo_clip_axis"):
            self.combo_clip_axis.blockSignals(True)
            self.combo_clip_axis.setCurrentIndex(1)
            self.combo_clip_axis.blockSignals(False)
        self._sync_axis_buttons(1)
        if hasattr(self, "slider_clip_pos"):
            self.slider_clip_pos.blockSignals(True)
            self.slider_clip_pos.setValue(50)
            self.slider_clip_pos.blockSignals(False)
        if hasattr(self, "lbl_clip_pos"):
            self.lbl_clip_pos.setText("Position: 50%")
        self._sync_clip_controls_enabled()

        self._bone_opacity = 1.0
        if hasattr(self, "slider_opacity"):
            self.slider_opacity.blockSignals(True)
            self.slider_opacity.setValue(100)
            self.slider_opacity.blockSignals(False)
        if hasattr(self, "lbl_opacity"):
            self.lbl_opacity.setText("100%")

        # Establish and remember a clean, upright, front-facing 3D anatomical view
        if hasattr(self, "meshset") and self.meshset is not None and getattr(self.meshset, "bone_mesh", None) is not None:
            bounds = self.meshset.bone_mesh.bounds
            center = self.meshset.bone_mesh.center
            diag = float(np.linalg.norm([bounds[1]-bounds[0], bounds[3]-bounds[2], bounds[5]-bounds[4]]))
            dist = max(diag * 1.5, 300.0)

            default_pos = (
                center[0] - dist * 0.88,
                center[1] + dist * 0.38,
                center[2] + dist * 0.28,
            )
            try:
                self.plotter.camera_position = [default_pos, center, (0.0, 0.0, 1.0)]
                self.plotter.renderer.ResetCameraClippingRange()
            except Exception:
                self.plotter.reset_camera()
        else:
            self.plotter.reset_camera()

        default_cam = self.plotter.renderer.GetActiveCamera()

        self._default_camera_position = tuple(
            default_cam.GetPosition()
        )

        self._default_camera_focal_point = tuple(
            default_cam.GetFocalPoint()
        )

        self._default_camera_view_up = tuple(
            default_cam.GetViewUp()
        )

        self.plotter.render()

        # Enable targeted 3D-to-2D raycast snapping
        try:
            self.plotter.disable_picking()
            self.plotter.enable_point_picking(
                callback=self._on_3d_point_picked,
                show_message=False,
                show_point=True,
                color="#00e5ff",
                point_size=10,
                left_clicking=True
            )
        except Exception as exc:
            print(f"[Viewer3D] Point picking setup error: {exc}")

        iso = getattr(meshset, "bone_isovalue", 400.0)
        hu_info = f"HU={iso:.0f}"
        if meshset.has_hu_density():
            lo, hi = meshset.get_hu_range()
            hu_info += f" (HU range {lo:.0f}–{hi:.0f})"
        self.info_bar.setText(
            f"  {self.scan_label.text()}  ·  "
            f"Bone {meshset.bone_mesh.n_points:,} pts  "
            f"{meshset.bone_mesh.n_cells:,} tris  ·  "
            f"{hu_info}"
        )

        self._rebuild_sidebar(active_path=getattr(self, "_active_path", ""))
        self.state_stack.setCurrentIndex(self._PAGE_SCENE)

        # Initialize 2D MPR view from cached volume
        try:
            if hasattr(self, "_active_path") and self._active_path:
                self.mpr_view.load_scan_folder(self._active_path)
                if self._ghost_active:
                    self._update_ghost_plane(self.mpr_view.idx_z * self.mpr_view.slice_thickness)
        except Exception as exc:
            print(f"[Viewer3D] Warning: could not load MPR slices: {exc}")

        # Initialize 2D slice viewer panel from loaded volume
        try:
            if hasattr(self, "panel_2d") and hasattr(self, "mpr_view"):
                if getattr(self.mpr_view, "vol_data", None) is not None:
                    self.panel_2d.load_volume(
                        self.mpr_view.vol_data,
                        self.mpr_view.pixel_spacing,
                        self.mpr_view.slice_thickness
                    )
                    if getattr(self, "_sync_2d_3d_enabled", False):
                        pos = self.panel_2d.get_current_physical_position()
                        self._reference_pos = pos
                        if getattr(self, "_sync_show_marker", True):
                            self._update_3d_marker(pos[0], pos[1], pos[2])
        except Exception as exc:
            print(f"[Viewer3D] Warning: could not load 2D slice panel: {exc}")

        self._safe_dicom_headers = None
        self._notify_metadata_changed()

    def _toggle_ghost_plane(self):
        self._ghost_active = self.btn_ghost_plane.isChecked()
        self.btn_ghost_plane.setText("👁 3D Ghost Slice: ON" if self._ghost_active else "👁 3D Ghost Slice: OFF")
        if self._ghost_active:
            z_mm = self.mpr_view.idx_z * self.mpr_view.slice_thickness
            self._update_ghost_plane(z_mm)
        else:
            try:
                self.plotter.remove_actor("ghost_plane")
                self.plotter.render()
            except Exception:
                pass

    def _update_ghost_plane(self, z_mm: float):
        """Renders single-quad transparent MPR cut plane floating inside 3D bone space."""
        if not getattr(self, "_ghost_active", False) or self.mesh_bounds is None:
            return
        try:
            xmin, xmax, ymin, ymax, zmin, zmax = self.mesh_bounds
            cx = (xmin + xmax) / 2.0
            cy = (ymin + ymax) / 2.0
            w = (xmax - xmin) * 1.15
            h = (ymax - ymin) * 1.15
            plane = pv.Plane(
                center=(cx, cy, float(z_mm)),
                direction=(0, 0, 1),
                i_size=max(10.0, w),
                j_size=max(10.0, h),
                i_resolution=1,
                j_resolution=1
            )
            self.plotter.add_mesh(
                plane,
                name="ghost_plane",
                color="#00e5ff",
                opacity=0.35,
                show_edges=True,
                edge_color="#00e5ff",
                line_width=2
            )
            self.plotter.render()
        except Exception as exc:
            print(f"[Viewer3D] Ghost plane error: {exc}")

    def _on_3d_point_picked(self, point):
        """Raycasts 3D surface click directly into MPR slices, 2D slice viewer, and synchronizes ghost plane."""
        if point is None:
            return
        x_mm, y_mm, z_mm = float(point[0]), float(point[1]), float(point[2])

        # ── Explicit Surgical Landmark/Structure Placement Mode (SET_ENTRY / SET_TARGET / ADD_STRUCTURE) ─
        picking_mode = getattr(self, "_planning_picking_mode", "IDLE")
        if picking_mode == "SET_ENTRY":
            self.set_entry_point(x_mm, y_mm, z_mm, source_view="3D")
            self.set_planning_picking_mode("IDLE")
            return
        elif picking_mode == "SET_TARGET":
            self.set_target_point(x_mm, y_mm, z_mm, source_view="3D")
            self.set_planning_picking_mode("IDLE")
            return
        elif picking_mode == "ADD_STRUCTURE":
            self.add_avoid_structure(x_mm, y_mm, z_mm, source_view="3D")
            self.set_planning_picking_mode("IDLE")
            return

        if getattr(self, "_measuring_3d", False):
            if self._pending_3d_point is None:
                self._pending_3d_point = (x_mm, y_mm, z_mm)
                try:
                    if hasattr(self, "plotter") and self.plotter is not None:
                        temp_s = pv.Sphere(radius=2.5, center=(x_mm, y_mm, z_mm))
                        self.plotter.add_mesh(temp_s, name="measure_3d_temp_pt", color="#7cfc00")
                        self.plotter.render()
                except Exception as exc:
                    print(f"[Viewer3D] Temp point add warning: {exc}")
                if hasattr(self, "panel_2d") and self.panel_2d is not None:
                    self.panel_2d.set_pending_physical_point(x_mm, y_mm, z_mm)
                if hasattr(self, "info_bar") and self.info_bar:
                    self.info_bar.setText(
                        f"  📏 Point 1: ({x_mm:.1f}, {y_mm:.1f}, {z_mm:.1f}) mm · Select second point"
                    )
            else:
                pt1 = self._pending_3d_point
                pt2 = (x_mm, y_mm, z_mm)
                self._pending_3d_point = None
                try:
                    if hasattr(self, "plotter") and self.plotter is not None:
                        self.plotter.remove_actor("measure_3d_temp_pt")
                except Exception:
                    pass
                m_rec = self._add_3d_measurement(pt1, pt2, source="3D")
                dist = m_rec["distance_mm"]
                if hasattr(self, "panel_2d") and self.panel_2d is not None:
                    self.panel_2d.add_measurement_from_3d(pt1, pt2, dist)
                if hasattr(self, "info_bar") and self.info_bar:
                    self.info_bar.setText(f"  📏 Distance: {dist:.1f} mm  ·  P1 to P2")
            return

        if hasattr(self, "mpr_view") and self.mpr_view:
            self.mpr_view.snap_to_volume_point(x_mm, y_mm, z_mm)
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(
                    f"  🎯 Snapped MPR to ({x_mm:.1f}, {y_mm:.1f}, {z_mm:.1f}) mm  ·  "
                    f"Slice {self.mpr_view.idx_z + 1}"
                )
        if getattr(self, "_ghost_active", False):
            self._update_ghost_plane(z_mm)

        if getattr(self, "_sync_2d_3d_enabled", False):
            if not getattr(self, "_sync_in_progress", False):
                self._sync_in_progress = True
                try:
                    if hasattr(self, "panel_2d"):
                        self.panel_2d.set_physical_position(x_mm, y_mm, z_mm)
                    self._reference_pos = (x_mm, y_mm, z_mm)
                    if getattr(self, "_sync_show_marker", True):
                        self._update_3d_marker(x_mm, y_mm, z_mm)
                finally:
                    self._sync_in_progress = False

    def _on_mpr_axial_changed(self, z_mm: float):
        if getattr(self, "_ghost_active", False):
            self._update_ghost_plane(z_mm)

    def _on_load_failed(self, error_msg: str):
        self.status_label.setText(f"Load failed:\n{error_msg}")
        print(f"[Viewer3D] DICOM load error: {error_msg}")

    # ── Public entry point ───────────────────────────────────────────────────

    def load_scan(self, patient: dict, scan: dict):
        """Load a specific patient scan from the DB-backed gallery."""
        # Kept so MainWindow.export_case_report() knows what is on screen
        old_mrn = str(getattr(self, "current_patient", {}).get("mrn", "") if hasattr(self, "current_patient") and self.current_patient else "").strip()
        new_mrn = str(patient.get("mrn", "") if patient else "").strip()
        if old_mrn != new_mrn and hasattr(self, "comparison"):
            self.comparison.clear()
            self._remove_comparison_actors()

        self.current_patient = patient
        self.current_scan = scan

        # Reset surgical planning landmarks if scan changed
        scan_id = (scan.get("file_path") if scan else "") or str(scan.get("id", "") if scan else "")
        if hasattr(self, "surgical_plan"):
            self.surgical_plan.reset_for_scan(scan_id)
            self._remove_planning_marker("ENTRY")
            self._remove_planning_marker("TARGET")
            self._remove_planned_route()
            self._remove_all_avoid_structure_actors()
            self._remove_surgical_corridor()
            self._remove_virtual_instrument()
            self._remove_live_deviation()
            self._sync_planning_landmarks_to_2d()


        label = (
            f"{patient.get('name', 'Patient')} — "
            f"{scan.get('type', 'CT')} ({scan.get('slice_count', 1)} slices)"
        )
        folder = scan.get("file_path", "")
        preset = _preset_from_path(folder)

        if folder and os.path.isdir(folder) and scan.get("slice_count", 1) > 1:
            self._start_load(folder, preset=preset, label=label)
        else:
            self.scan_label.setText(label)
            self.status_label.setText(
                f"No renderable volume found at:\n{folder or '(no path)'}"
            )
            self.state_stack.setCurrentIndex(self._PAGE_LOADING)

    # ── Discrete 90° Anatomical Snap Turns & Voice Routing ───────────────────

    def snap_to_view(self, command: str):
        """
        Move the 3D camera to a standard anatomical view.
        """

        cmd = " ".join(
            command.lower().strip().split()
        )

    # Normalize aliases.
        aliases = {
            "left lateral": "left_lateral",
            "left side": "left_lateral",
            "left lat": "left_lateral",
            "left": "left_lateral",

            "right lateral": "right_lateral",
            "right side": "right_lateral",
            "right lat": "right_lateral",
            "right": "right_lateral",

            "front": "anterior",
            "front view": "anterior",

            "back": "posterior",
            "back view": "posterior",

            "top view": "superior",
            "top": "superior",

            "bottom view": "inferior",
            "bottom": "inferior",

            "reset camera": "reset",
            "reset the view": "reset view",
        }

        cmd = aliases.get(cmd, cmd)

        cam = self.plotter.renderer.GetActiveCamera()

        focal = np.array(
            cam.GetFocalPoint(),
            dtype=float,
        )

        pos = np.array(
            cam.GetPosition(),
            dtype=float,
        )

        distance = float(
            np.linalg.norm(pos - focal)
        )

        if distance <= 0:
            distance = 1.0

        positions = {
            "anterior": np.array(
                [-distance, 0.0, 0.0]
            ),

            "posterior": np.array(
                [distance, 0.0, 0.0]
            ),

            "left_lateral": np.array(
                [0.0, distance, 0.0]
            ),

            "right_lateral": np.array(
                [0.0, -distance, 0.0]
            ),

            "superior": np.array(
                [0.0, 0.0, distance]
            ),

            "inferior": np.array(
                [0.0, 0.0, -distance]
            ),
        }

        if cmd in ("reset", "reset view"):
            # Stop automatic rotation when resetting.
            self._spin_timer.stop()

            cam = self.plotter.renderer.GetActiveCamera()

            if hasattr(self, "_default_camera_position"):
                try:
                    self.plotter.camera_position = [
                        self._default_camera_position,
                        self._default_camera_focal_point,
                        self._default_camera_view_up,
                    ]
                except Exception:
                    cam.SetPosition(*self._default_camera_position)
                    cam.SetFocalPoint(*self._default_camera_focal_point)
                    cam.SetViewUp(*self._default_camera_view_up)
                    cam.OrthogonalizeViewUp()

            else:
                # Fallback if no stored camera exists yet.
                self.plotter.reset_camera()

            self.plotter.renderer.ResetCameraClippingRange()
            self.plotter.render()

            print(
                "[Viewer3D] Camera reset to default view.",
                flush=True,
            )

            return

        if cmd not in positions:
            print(
                f"[Viewer3D] Unknown camera view: {cmd}",
                flush=True,
            )
            return

        new_position = focal + positions[cmd]
        view_up = (1.0, 0.0, 0.0) if cmd in ("superior", "inferior") else (0.0, 0.0, 1.0)

        try:
            self.plotter.camera_position = [tuple(new_position), tuple(focal), view_up]
        except Exception:
            cam.SetPosition(*new_position)
            cam.SetFocalPoint(*focal)
            cam.SetViewUp(*view_up)
            cam.OrthogonalizeViewUp()

        self.plotter.renderer.ResetCameraClippingRange()
        self.plotter.render()

        print(
            f"[Viewer3D] Camera moved to: {cmd}",
            flush=True,
        )   
    def handle_voice_command(self, phrase: str):
        """
        Execute recognized voice commands on the active 3D viewer.
        """

        command = " ".join(
            phrase.lower().strip().split()
        )

        print(
            f"[Viewer3D] Received voice command: {command!r}",
            flush=True,
        )

    # Only respond while the 3D scene is visible.
        if self.state_stack.currentIndex() != self._PAGE_SCENE:
            print(
                "[Viewer3D] Ignoring command because 3D scene is not active.",
                flush=True,
            )
            return

        aliases = {
            "show anterior": "anterior",
            "go anterior": "anterior",
            "anterior view": "anterior",

            "show posterior": "posterior",
            "go posterior": "posterior",
            "posterior view": "posterior",

            "show left lateral": "left lateral",
            "go left lateral": "left lateral",
            "left side": "left lateral",
            "left": "left lateral",

            "show right lateral": "right lateral",
            "go right lateral": "right lateral",
            "right side": "right lateral",
            "right": "right lateral",

            "show top": "superior",
            "go top": "superior",
            "top view": "superior",
            "top": "superior",

            "show bottom": "inferior",
            "go bottom": "inferior",
            "bottom view": "inferior",
            "bottom": "inferior",

            "reset camera": "reset",
            "reset the view": "reset view",

            # Bone density heatmap voice aliases
            "show density map": "show density",
            "hide density map": "hide density",
            "density map": "show density",
            "bone heatmap": "show density",
            "bone density": "show density",
            "hounsfield heatmap": "show density",
            "show heatmap": "show density",
            "density heatmap": "show density",
            "normal bone view": "normal view",
            "normal bone": "normal view",
            "bone view": "normal view",
            "hide heatmap": "normal view",
            "hide density": "normal view",

            # Interactive clipping plane voice aliases
            "enable clipping": "enable clipping",
            "clip model": "enable clipping",
            "start clipping": "enable clipping",
            "show cross section": "enable clipping",
            "cross section": "enable clipping",
            "disable clipping": "disable clipping",
            "remove clipping": "disable clipping",
            "stop clipping": "disable clipping",
            "hide cross section": "disable clipping",
            "clip along x": "clip along x",
            "clip x": "clip along x",
            "clip on x": "clip along x",
            "x axis clip": "clip along x",
            "clip along y": "clip along y",
            "clip y": "clip along y",
            "clip on y": "clip along y",
            "y axis clip": "clip along y",
            "clip along z": "clip along z",
            "clip z": "clip along z",
            "clip on z": "clip along z",
            "z axis clip": "clip along z",
            "reverse clipping": "reverse clipping",
            "reverse clip direction": "reverse clipping",
            "invert clipping": "reverse clipping",
            "flip clipping": "reverse clipping",
            "flip direction": "reverse clipping",

            # Bone transparency / opacity voice aliases
            "make bone transparent": "half opacity",
            "make transparent": "half opacity",
            "semi transparent": "half opacity",
            "medium transparency": "half opacity",
            "50 percent opacity": "half opacity",
            "50% opacity": "half opacity",
            "half opacity": "half opacity",
            "high transparency": "high transparency",
            "transparent bone": "high transparency",
            "transparent": "high transparency",
            "25 percent opacity": "high transparency",
            "25% opacity": "high transparency",
            "opaque": "full opacity",
            "opaque bone": "full opacity",
            "100 percent opacity": "full opacity",
            "100% opacity": "full opacity",
            "full opacity": "full opacity",
            "reset opacity": "full opacity",
            "increase transparency": "increase transparency",
            "more transparent": "increase transparency",
            "decrease transparency": "decrease transparency",
            "more opaque": "decrease transparency",
            "increase opacity": "decrease transparency",
            "decrease opacity": "increase transparency",
            "less transparent": "decrease transparency",
            "less opaque": "increase transparency",

            # 2D CT slice viewer voice aliases
            "show 2d": "show 2d",
            "show 2d viewer": "show 2d",
            "open 2d": "show 2d",
            "open 2d viewer": "show 2d",
            "2d viewer": "show 2d",
            "2d slice": "show 2d",
            "show slice": "show 2d",
            "hide 2d": "hide 2d",
            "hide 2d viewer": "hide 2d",
            "close 2d": "hide 2d",
            "close 2d viewer": "hide 2d",
            "toggle 2d": "toggle 2d",
            "show axial": "show axial",
            "axial view": "show axial",
            "axial": "show axial",
            "axial slice": "show axial",
            "show coronal": "show coronal",
            "coronal view": "show coronal",
            "coronal": "show coronal",
            "coronal slice": "show coronal",
            "show sagittal": "show sagittal",
            "sagittal view": "show sagittal",
            "sagittal": "show sagittal",
            "sagittal slice": "show sagittal",
            "next slice": "next slice",
            "previous slice": "previous slice",
            "prev slice": "previous slice",
            "bone window": "bone window",
            "soft tissue window": "soft tissue window",
            "soft tissue": "soft tissue window",
            "lung window": "lung window",
            "lung": "lung window",

            # Distance measurement voice aliases
            "start measurement": "start measurement",
            "measure distance": "start measurement",
            "take measurement": "start measurement",
            "start measuring": "start measurement",
            "finish measurement": "finish measurement",
            "complete measurement": "finish measurement",
            "stop measuring": "finish measurement",
            "stop measurement": "finish measurement",
            "clear measurements": "clear measurements",
            "remove measurements": "clear measurements",
            "delete measurements": "clear measurements",
            "show measurements": "show measurements",
            "display measurements": "show measurements",
            "hide measurements": "hide measurements",

            # Study information & scan metadata voice aliases
            "show study information": "show study info",
            "show study info": "show study info",
            "open scan metadata": "show study info",
            "show scan details": "show study info",
            "show metadata": "show study info",
            "study information": "show study info",
            "study info": "show study info",
            "scan metadata": "show study info",
            "scan details": "show study info",
            "hide study information": "hide study info",
            "hide study info": "hide study info",
            "close scan metadata": "hide study info",
            "hide scan details": "hide study info",
            "close study info": "hide study info",
            "toggle study info": "toggle study info",
            "toggle scan metadata": "toggle study info",
            "toggle study information": "toggle study info",

            # Before vs After voice aliases (OR Feature 8)
            "compare before and after": "compare before and after",
            "show before scan": "show before scan",
            "show before": "show before scan",
            "show after scan": "show after scan",
            "show after": "show after scan",
            "toggle before after": "toggle before after",
            "exit comparison": "exit comparison",
            "stop comparison": "exit comparison",
            
            # Skeleton transparency aliases
            "ghost skeleton": "ghost skeleton",
            "ghost bone": "ghost skeleton",
            "translucent skeleton": "ghost skeleton",
            "solid skeleton": "solid skeleton",
            "solid bone": "solid skeleton",
            "hide skeleton": "hide skeleton",
            "hide bone": "hide skeleton",
            "show skeleton": "solid skeleton",
            "show bone": "solid skeleton",
        }

        command = aliases.get(command, command)

        # Skeleton transparency voice actions
        if command == "ghost skeleton":
            self.set_skeleton_mode("ghost")
            return
        if command == "solid skeleton":
            self.set_skeleton_mode("solid")
            return
        if command == "hide skeleton":
            self.set_skeleton_mode("hidden")
            return

        # Density heatmap commands
        if command in ("show density", "density"):
            self.set_density_colormap(True)
            return

        if command in ("normal view", "hide density"):
            self.set_density_colormap(False)
            return

        # Interactive clipping plane commands
        if command in ("enable clipping", "clip model"):
            self.set_clipping(True)
            return

        if command in ("disable clipping", "remove clipping"):
            self.set_clipping(False)
            return

        if command == "clip along x":
            self.set_clip_axis("x")
            return

        if command == "clip along y":
            self.set_clip_axis("y")
            return

        if command == "clip along z":
            self.set_clip_axis("z")
            return

        if command in ("reverse clipping", "reverse clip direction"):
            self.reverse_clip_direction()
            return

        # Bone transparency / opacity commands
        if command in ("full opacity", "opaque", "opaque bone"):
            self.set_bone_opacity(1.0)
            return

        if command in ("half opacity", "semi transparent", "medium transparency"):
            self.set_bone_opacity(0.5)
            return

        if command in ("high transparency", "transparent bone", "transparent"):
            self.set_bone_opacity(0.25)
            return

        if command in ("increase transparency", "more transparent"):
            self.set_bone_opacity(getattr(self, "_bone_opacity", 1.0) - 0.2)
            return

        if command in ("decrease transparency", "increase opacity", "more opaque", "less transparent"):
            self.set_bone_opacity(getattr(self, "_bone_opacity", 1.0) + 0.2)
            return

        import re
        m_op = re.search(r"(\d+)\s*(?:percent|%)\s*opacity", command)
        if m_op:
            val = float(m_op.group(1)) / 100.0
            self.set_bone_opacity(val)
            return

        # 2D CT slice viewer commands
        if command in ("show 2d", "open 2d viewer"):
            self.set_2d_panel_visible(True)
            return

        if command in ("hide 2d", "close 2d viewer"):
            self.set_2d_panel_visible(False)
            return

        if command == "toggle 2d":
            cur = self.panel_2d.isVisible() if hasattr(self, "panel_2d") else False
            self.set_2d_panel_visible(not cur)
            return

        if command == "show axial":
            self.set_2d_panel_visible(True)
            if hasattr(self, "panel_2d"):
                self.panel_2d.set_orientation("axial")
            return

        if command == "show coronal":
            self.set_2d_panel_visible(True)
            if hasattr(self, "panel_2d"):
                self.panel_2d.set_orientation("coronal")
            return

        if command == "show sagittal":
            self.set_2d_panel_visible(True)
            if hasattr(self, "panel_2d"):
                self.panel_2d.set_orientation("sagittal")
            return

        if command == "next slice":
            if hasattr(self, "panel_2d") and (not self.panel_2d.isHidden() or (hasattr(self, "btn_toggle_2d") and self.btn_toggle_2d.isChecked())):
                self.panel_2d.step_slice(1)
                return
            elif hasattr(self, "mpr_view"):
                if hasattr(self.mpr_view, "step_axial"):
                    self.mpr_view.step_axial(1)
                elif hasattr(self.mpr_view, "slider_axial"):
                    self.mpr_view.slider_axial.setValue(self.mpr_view.slider_axial.value() + 1)
                return

        if command == "previous slice":
            if hasattr(self, "panel_2d") and (not self.panel_2d.isHidden() or (hasattr(self, "btn_toggle_2d") and self.btn_toggle_2d.isChecked())):
                self.panel_2d.step_slice(-1)
                return
            elif hasattr(self, "mpr_view"):
                if hasattr(self.mpr_view, "step_axial"):
                    self.mpr_view.step_axial(-1)
                elif hasattr(self.mpr_view, "slider_axial"):
                    self.mpr_view.slider_axial.setValue(self.mpr_view.slider_axial.value() - 1)
                return

        if command == "bone window":
            if hasattr(self, "panel_2d"):
                self.panel_2d.set_window_preset("Bone")
            if hasattr(self, "mpr_view"):
                self.mpr_view.set_window_preset("Bone")
            return

        if command == "soft tissue window":
            if hasattr(self, "panel_2d"):
                self.panel_2d.set_window_preset("Soft Tissue")
            if hasattr(self, "mpr_view"):
                self.mpr_view.set_window_preset("Soft Tissue")
            return

        if command == "lung window":
            if hasattr(self, "panel_2d"):
                self.panel_2d.set_window_preset("Lung")
            if hasattr(self, "mpr_view"):
                self.mpr_view.set_window_preset("Lung")
            return

        # Numeric slice command: e.g. "go to slice 40" or "slice 40"
        m_slice = re.search(r"(?:go\s+to\s+)?slice\s+(\d+)", command)
        if m_slice:
            num = int(m_slice.group(1))
            if hasattr(self, "panel_2d"):
                self.panel_2d.go_to_slice(num)
            return

        # ── Synchronized 2D ↔ 3D Linking Voice Commands ───────────────────────
        if command in ("enable 2d 3d sync", "enable 2d-3d sync", "enable sync", "sync 2d and 3d", "sync on"):
            self.set_sync_2d_3d(True)
            return

        if command in ("disable 2d 3d sync", "disable 2d-3d sync", "disable sync", "unsync 2d and 3d", "sync off"):
            self.set_sync_2d_3d(False)
            return

        if command in ("toggle sync", "toggle 2d 3d sync", "toggle 2d-3d sync"):
            self.set_sync_2d_3d(not getattr(self, "_sync_2d_3d_enabled", False))
            return

        if command in ("show crosshair", "enable crosshair"):
            self.set_crosshair_visible(True)
            return

        if command in ("hide crosshair", "disable crosshair"):
            self.set_crosshair_visible(False)
            return

        if command in ("show 3d marker", "show marker", "enable marker"):
            self.set_marker_visible(True)
            return

        if command in ("hide 3d marker", "hide marker", "disable marker"):
            self.set_marker_visible(False)
            return

        if command in ("link clipping plane", "link clipping", "link clip", "link plane"):
            self.set_link_clipping(True)
            return

        if command in ("unlink clipping plane", "unlink clipping", "unlink clip", "unlink plane"):
            self.set_link_clipping(False)
            return

        # ── Distance Measurement Voice Commands ───────────────────────────────
        if command in ("start measurement", "measure distance", "take measurement", "start measuring"):
            self.start_measurement()
            if hasattr(self, "panel_2d") and self.panel_2d.isVisible():
                self.panel_2d.start_measurement()
            return

        if command in ("finish measurement", "complete measurement", "stop measuring", "stop measurement"):
            self.finish_measurement()
            if hasattr(self, "panel_2d"):
                self.panel_2d.finish_measurement()
            return

        if command in ("clear measurements", "remove measurements", "delete measurements"):
            self.clear_measurements_3d()
            if hasattr(self, "panel_2d"):
                self.panel_2d.clear_measurements()
            return

        if command in ("show measurements", "display measurements"):
            self.set_measurements_3d_visible(True)
            if hasattr(self, "panel_2d"):
                self.panel_2d.set_measurements_visible(True)
            return

        if command == "hide measurements":
            self.set_measurements_3d_visible(False)
            if hasattr(self, "panel_2d"):
                self.panel_2d.set_measurements_visible(False)
            return

        # ── Surgical Planning Landmark Voice Commands ────────────────────────
        if command in ("set entry", "place entry", "mark entry"):
            self.set_planning_picking_mode("SET_ENTRY")
            return

        if command in ("set target", "place target", "mark target"):
            self.set_planning_picking_mode("SET_TARGET")
            return

        if command in ("view entry", "show entry", "go to entry"):
            self.view_entry_point()
            return

        if command in ("view target", "show target", "go to target"):
            self.view_target_point()
            return

        if command in ("clear entry", "remove entry", "delete entry"):
            self.clear_entry_point()
            return

        if command in ("clear target", "remove target", "delete target"):
            self.clear_target_point()
            return

        if command in ("clear both", "clear planning", "clear landmarks"):
            self.clear_both_planning_points()
            return

        # ── Planned Route Voice Commands ─────────────────────────────────────
        if command in ("show planned route", "view planned route", "show route", "view route"):
            self.view_planned_route()
            return

        if command in ("hide planned route", "hide route"):
            self.set_planned_route_visible(False)
            return

        if command in ("clear planned route", "clear route"):
            self.clear_planned_route()
            return

        # ── Structures to Avoid Voice Commands ─────────────────────────────────
        if command in ("add structure", "mark structure", "avoid structure", "place structure"):
            self.set_planning_picking_mode("ADD_STRUCTURE")
            return

        if command in ("show structures", "view structures"):
            structs = self.surgical_plan.get_avoid_structures() if hasattr(self, "surgical_plan") else []
            if structs:
                self.view_avoid_structure(structs[0].structure_id)
            return

        if command in ("clear structures", "clear avoid structures", "remove structures"):
            self.clear_avoid_structures()
            return

        # ── Surgical Corridor Voice Commands (OR Mode: Feature 4) ─────────────
        if command in ("show surgical corridor", "show corridor", "enable corridor", "enable surgical corridor"):
            self.set_surgical_corridor_enabled(True)
            return

        if command in ("hide surgical corridor", "hide corridor", "disable corridor", "disable surgical corridor"):
            self.set_surgical_corridor_enabled(False)
            return

        if command in ("increase corridor radius", "increase corridor", "wider corridor"):
            new_r = (self.surgical_plan.corridor_radius_mm if hasattr(self, "surgical_plan") else 5.0) + 1.0
            self.set_surgical_corridor_radius(new_r)
            return

        if command in ("decrease corridor radius", "decrease corridor", "narrower corridor"):
            new_r = (self.surgical_plan.corridor_radius_mm if hasattr(self, "surgical_plan") else 5.0) - 1.0
            self.set_surgical_corridor_radius(new_r)
            return

        # ── Virtual Instrument Voice Commands (OR Mode: Feature 5) ─────────────
        if command in ("show virtual instrument", "show instrument", "enable virtual instrument", "enable instrument"):
            self.set_virtual_instrument_visible(True)
            return

        if command in ("hide virtual instrument", "hide instrument", "disable virtual instrument", "disable instrument"):
            self.set_virtual_instrument_visible(False)
            return

        if command in ("view virtual instrument", "view instrument", "center instrument", "center virtual instrument"):
            self.view_virtual_instrument()
            return

        if command in ("increase instrument depth", "increase depth", "insert instrument", "advance instrument"):
            current_d = self.surgical_plan.instrument_depth_mm if hasattr(self, "surgical_plan") else 0.0
            self.set_virtual_instrument_depth(current_d + 5.0)
            return

        if command in ("decrease instrument depth", "decrease depth", "retract instrument", "withdraw instrument"):
            current_d = self.surgical_plan.instrument_depth_mm if hasattr(self, "surgical_plan") else 0.0
            self.set_virtual_instrument_depth(current_d - 5.0)
            return

        # ── Live Deviation Voice Commands (OR Mode: Feature 6) ─────────────────
        if command in ("show live deviation", "show current instrument", "enable live deviation"):
            self.set_live_deviation_visible(True)
            return

        if command in ("hide live deviation", "hide current instrument", "disable live deviation"):
            self.set_live_deviation_visible(False)
            return

        if command in ("reset deviation", "reset instrument", "reset to planned route"):
            self.reset_live_deviation_to_planned()
            return

        # ── Plan Saving / Versions Voice Commands (OR Mode: Feature 7) ─────────
        if command in ("save plan", "save surgical plan"):
            self.plan_save_requested.emit()
            return

        if command in ("show plan versions", "show plans", "list plans"):
            self.plan_versions_requested.emit()
            return

        # ── Study Information & Scan Metadata Voice Commands ──────────────────
        if command in ("show study info", "open scan metadata", "show scan details"):
            self.show_study_info()
            return

        if command in ("hide study info", "close scan metadata", "hide scan details"):
            self.hide_study_info()
            return

        if command in ("toggle study info", "toggle scan metadata"):
            self.toggle_study_info()
            return

        # ── Before vs After Voice Commands (OR Mode: Feature 8) ───────────────
        if command in ("compare before and after", "toggle before after"):
            if self.comparison.is_active:
                self.toggle_before_after_view()
            return

        if command == "show before scan":
            if self.comparison.is_active:
                self.toggle_before_after_view("BEFORE")
            return

        if command == "show after scan":
            if self.comparison.is_active:
                self.toggle_before_after_view("AFTER")
            return

        if command in ("exit comparison", "stop comparison"):
            if self.comparison.is_active:
                self.exit_before_after_comparison()
            return

        # ── Quick Surgical Views Voice Commands (OR Mode: Feature 9) ─────────
        if command in ("show entry view", "entry view", "quick entry"):
            self.apply_quick_surgical_view("ENTRY")
            return

        if command in ("show target view", "target view", "quick target"):
            self.apply_quick_surgical_view("TARGET")
            return

        if command in ("show route view", "route view", "quick route"):
            self.apply_quick_surgical_view("ROUTE")
            return

        if command in ("show structures view", "structures view", "quick structures"):
            self.apply_quick_surgical_view("STRUCTURES")
            return

        if command in ("show instrument view", "instrument view", "quick instrument"):
            self.apply_quick_surgical_view("INSTRUMENT")
            return

        if command in ("show full plan", "full plan view", "full plan", "quick full plan"):
            self.apply_quick_surgical_view("FULL PLAN")
            return

        # ── ICU Feature 1: Current vs Previous Voice Commands ─────────────────
        if command in ("show previous scan", "view previous scan", "previous scan"):
            if not self.comparison.is_active:
                mrn = getattr(self, "current_patient", {}).get("mrn", "")
                curr = getattr(self, "current_scan", {})
                from database import get_previous_scan_for_patient
                prev = get_previous_scan_for_patient(mrn, curr)
                if prev:
                    self.start_before_after_comparison(prev, curr, "TOGGLE")
            self.toggle_before_after_view("BEFORE")
            return

        if command in ("show current scan", "view current scan", "current scan"):
            if self.comparison.is_active:
                self.toggle_before_after_view("AFTER")
            return

        if command in ("compare current and previous", "toggle previous scan"):
            if not self.comparison.is_active:
                mrn = getattr(self, "current_patient", {}).get("mrn", "")
                curr = getattr(self, "current_scan", {})
                from database import get_previous_scan_for_patient
                prev = get_previous_scan_for_patient(mrn, curr)
                if prev:
                    self.start_before_after_comparison(prev, curr, "TOGGLE")
            else:
                self.toggle_before_after_view()
            return

        # ── ICU Feature 2: Same-Location Voice Commands ───────────────────────
        if command in ("mark same location", "mark location"):
            self.icu_mark_same_location_requested.emit()
            return

        if command in ("show same location", "view same location"):
            self.icu_view_same_location_requested.emit()
            return

        if command in ("view previous location", "show previous location"):
            self.icu_view_previous_location_requested.emit()
            return

        if command in ("view current location", "show current location"):
            self.icu_view_current_location_requested.emit()
            return

        if command in ("clear location", "clear same location"):
            self.clear_same_location_marker()
            self.icu_clear_same_location_requested.emit()
            return

    # Anatomical views.
        anatomical_commands = {
            "anterior",
            "posterior",
            "left lateral",
            "right lateral",
            "superior",
            "inferior",
            "reset",
            "reset view",
        }

        if command in anatomical_commands:
            self.snap_to_view(command)
            return

    # Existing spin commands.
        if command == "start spin":
            if hasattr(self, "start_spin"):
                self.start_spin()
            elif hasattr(self, "toggle_spin"):
                self.toggle_spin(True)
            return

        if command == "stop spin":
            if hasattr(self, "stop_spin"):
                self.stop_spin()
            elif hasattr(self, "toggle_spin"):
                self.toggle_spin(False)
            return

    # Existing zoom commands.
        if command == "zoom in":
            self.zoom_camera(1)
            return

        if command == "zoom out":
            self.zoom_camera(-1)
            return

    # Existing rotation commands.
        if command == "turn left":
            self.rotate_camera(-0.08, 0.0, 0.0)
            return

        if command == "turn right":
            self.rotate_camera(0.08, 0.0, 0.0)
            return

        print(
            f"[Viewer3D] Unsupported voice command: {command!r}",
            flush=True,
        )

    def _handle_mpr_voice_command(self, command: str):
        """Voice control for the 2D MPR stack (slices, windowing, measurement)."""
        mpr = getattr(self, "mpr_view", None)
        if mpr is None:
            return

        if command in ("next slice", "previous slice", "jump forward", "jump back"):
            step = self.MPR_JUMP_SLICES if command.startswith("jump") else 1
            if command in ("previous slice", "jump back"):
                step = -step
            mpr.step_axial(step)
        elif command == "first slice":
            mpr.goto_axial(0)
        elif command == "last slice":
            mpr.goto_axial(10 ** 9)   # clamped to the last slice internally
        elif command == "middle slice":
            mpr.goto_axial_fraction(0.5)
        elif command == "bone window":
            mpr.set_window_preset("Bone")
        elif command == "soft tissue window":
            mpr.set_window_preset_contains("soft")
        elif command == "next window":
            mpr.cycle_window_preset()
        elif command == "measure":
            mpr._toggle_caliper()
        elif command == "measure angle":
            mpr._toggle_cobb()
        elif command == "undo measurement":
            mpr.undo_measurement()
        elif command == "clear measurements":
            mpr.clear_measurements()
        elif command == "flashlight":
            mpr._toggle_flashlight()

    def _tilt_camera(self, el_deg: float):
        """Elevation nudge with the same +/-80 degree gimbal clamp rotate_camera uses."""
        cam = self.plotter.renderer.GetActiveCamera()
        try:
            pos   = np.array(cam.GetPosition(), dtype=float)
            focal = np.array(cam.GetFocalPoint(), dtype=float)
            vec   = pos - focal
            r     = float(np.linalg.norm(vec))
            if r > 0:
                cur_el = float(np.degrees(np.arcsin(np.clip(vec[2] / r, -1.0, 1.0))))
                if cur_el + el_deg > 80.0:
                    el_deg = max(0.0, 80.0 - cur_el)
                elif cur_el + el_deg < -80.0:
                    el_deg = min(0.0, -80.0 - cur_el)
        except Exception:
            pass
        cam.Elevation(el_deg)
        cam.OrthogonalizeViewUp()

    def start_spin(self):
        """
        Start very slow continuous 360-degree model rotation.
        Only works in 3D mode.
        """
        if (
            self.state_stack.currentIndex() != self._PAGE_SCENE
            or self.view_mode_stack.currentIndex() != 0
        ):
            return

        if not self._spin_timer.isActive():
            self._spin_timer.start(
                self.VOICE_SPIN_INTERVAL_MS
            )

        print(
            "[Viewer3D] Slow spin started.",
            flush=True,
        )

    def stop_spin(self):
        """
        Stop continuous model rotation.
        """
        self._spin_timer.stop()

        print(
            "[Viewer3D] Slow spin stopped.",
            flush=True,
        )

    def _spin_tick(self):
        """One frame of hands-free 360-degree auto-rotation ('start spin')."""
        if not self.isVisible() or self.state_stack.currentIndex() != self._PAGE_SCENE:
            self._spin_timer.stop()
            return
        if hasattr(self, "view_mode_stack") and self.view_mode_stack.currentIndex() != 0:
            self._spin_timer.stop()
            return
        cam = self.plotter.renderer.GetActiveCamera()
        cam.Azimuth(self.VOICE_SPIN_STEP_DEG)
        cam.OrthogonalizeViewUp()
        self.plotter.render()

    # ── Pure Gesture Camera Control ──────────────────────────────────────────

    def rotate_camera(self, delta_x: float, delta_y: float, delta_z: float = 0.0):
        """
        Fluid, gimbal-safe 3D camera orbit using VTK's native camera
        Azimuth/Elevation methods driven by open-palm gesture movement.
        """
        if not self.isVisible() or self.state_stack.currentIndex() != self._PAGE_SCENE:
            return

        # In MPR mode, repurpose vertical hand movement as slice scrolling rather
        # than doing nothing -- otherwise half the app is mouse-only, which defeats
        # the point of a touchless workstation.
        if hasattr(self, "view_mode_stack") and self.view_mode_stack.currentIndex() != 0:
            self._gesture_scroll_slices(delta_y)
            return

        # Throttle continuous rotation renders to 40 FPS (~25ms) to prevent UI event queue starvation
        now = time.time()
        if hasattr(self, "_last_rot_time") and (now - self._last_rot_time < 0.025):
            return
        self._last_rot_time = now

        az_deg = float(np.clip(-delta_x * 180.0, -12.0, 12.0))
        el_deg = float(np.clip(-delta_y * 180.0, -12.0, 12.0))

        # Elevation safety: keep camera between -80° and +80° from equator
        try:
            pos   = np.array(self.plotter.camera.position)
            focal = np.array(self.plotter.camera.focal_point)
            vec   = pos - focal
            r     = np.linalg.norm(vec)
            if r > 0:
                current_el = np.degrees(np.arcsin(np.clip(vec[2] / r, -1.0, 1.0)))
                new_el     = current_el + el_deg
                if not (-80.0 <= new_el <= 80.0):
                    el_deg = 0.0
        except Exception:
            pass

        cam = self.plotter.renderer.GetActiveCamera()
        cam.Azimuth(az_deg)
        cam.Elevation(el_deg)
        cam.OrthogonalizeViewUp()
        self.plotter.renderer.ResetCameraClippingRange()
        self.plotter.render()

    def zoom_camera(self, direction: int):
        """
        Smooth, clamped Dolly zoom.
        direction = +1 (zoom in) | -1 (zoom out)
        """
        if not self.isVisible() or self.state_stack.currentIndex() != self._PAGE_SCENE:
            return

        # Pinch-zoom in MPR mode jumps through slices in larger steps
        if hasattr(self, "view_mode_stack") and self.view_mode_stack.currentIndex() != 0:
            mpr = getattr(self, "mpr_view", None)
            if mpr is not None:
                mpr.step_axial(1 if direction > 0 else -1)
            return

        cam    = self.plotter.renderer.GetActiveCamera()
        factor = 1.05 if direction > 0 else (1.0 / 1.05)

        focal = np.array(cam.GetFocalPoint())
        pos   = np.array(cam.GetPosition())
        dist  = np.linalg.norm(pos - focal)

        if direction > 0 and dist < 15.0:
            return
        if direction < 0 and dist > 3000.0:
            return

        cam.Dolly(factor)
        self.plotter.render()

    # \u2500\u2500 Cross-section / density / capture \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    # ── Interactive Clipping Plane & Rendering Pipeline ──────────────────────────

    def _sync_clip_controls_enabled(self):
        """Synchronize the enabled state and appearance of clipping sub-controls."""
        enabled = getattr(self, "_clip_active", False)
        if hasattr(self, "combo_clip_axis"):
            self.combo_clip_axis.setEnabled(enabled)
        for btn in (getattr(self, "btn_axis_x", None), getattr(self, "btn_axis_y", None), getattr(self, "btn_axis_z", None)):
            if btn is not None:
                btn.setEnabled(enabled)
                btn.setVisible(enabled)
        if hasattr(self, "slider_clip_pos"):
            self.slider_clip_pos.setEnabled(enabled)
            self.slider_clip_pos.setVisible(enabled)
        if hasattr(self, "btn_clip_reverse"):
            self.btn_clip_reverse.setEnabled(enabled)
            self.btn_clip_reverse.setVisible(enabled)
        if hasattr(self, "lbl_clip_pos"):
            self.lbl_clip_pos.setVisible(enabled)
            self.lbl_clip_pos.setStyleSheet(
                f"color: {'#00e5ff' if enabled else '#444'}; font-size: 9px; font-weight: 600; min-width: 50px;"
            )

    def _on_clip_toggle_clicked(self, checked: bool):
        self.set_clipping(checked)

    def _on_clip_axis_changed(self, index: int):
        self._sync_axis_buttons(index)
        axis = ["x", "y", "z"][index] if 0 <= index < 3 else "y"
        self.set_clip_axis(axis)
        if getattr(self, "_sync_2d_3d_enabled", False) and getattr(self, "_sync_link_clipping", False):
            if hasattr(self, "slider_clip_pos"):
                self._sync_clip_to_2d(self.slider_clip_pos.value() / 100.0)

    def _set_clip_axis_index(self, idx: int):
        if hasattr(self, "combo_clip_axis"):
            self.combo_clip_axis.blockSignals(True)
            self.combo_clip_axis.setCurrentIndex(idx)
            self.combo_clip_axis.blockSignals(False)
        self._sync_axis_buttons(idx)
        axis = ["x", "y", "z"][idx] if 0 <= idx < 3 else "y"
        self.set_clip_axis(axis)

    def _sync_axis_buttons(self, idx: int):
        btns = [getattr(self, "btn_axis_x", None), getattr(self, "btn_axis_y", None), getattr(self, "btn_axis_z", None)]
        for i, b in enumerate(btns):
            if b is not None:
                b.blockSignals(True)
                b.setChecked(i == idx)
                b.blockSignals(False)

    def _on_clip_slider_changed(self, value: int):
        self.set_clip_position(value / 100.0)
        if getattr(self, "_sync_2d_3d_enabled", False) and getattr(self, "_sync_link_clipping", False):
            self._sync_clip_to_2d(value / 100.0)

    def _on_opacity_slider_changed(self, value: int):
        self.set_bone_opacity(value / 100.0)

    def _update_clipped_mesh(self) -> bool:
        """Generates the clipped mesh from the unmodified original master mesh.
        Returns True if successful, False if clipping produced an empty mesh or failed.
        """
        mesh = getattr(self, "_bone_mesh", None)
        if mesh is None:
            return False

        axis = getattr(self, "_clip_axis", "y").lower().strip()
        if axis not in ("x", "y", "z"):
            axis = "y"

        bounds = mesh.bounds  # (xmin, xmax, ymin, ymax, zmin, zmax)
        if axis == "x":
            lo, hi = bounds[0], bounds[1]
        elif axis == "y":
            lo, hi = bounds[2], bounds[3]
        else:
            lo, hi = bounds[4], bounds[5]

        # Clamp fraction slightly inside bounds to prevent clipping plane disappearing
        fraction = float(np.clip(self._clip_fraction, 0.005, 0.995))
        pos = lo + fraction * (hi - lo)

        if hasattr(self, "lbl_clip_pos"):
            pct = int(round(fraction * 100))
            self.lbl_clip_pos.setText(f"Pos: {pct}% ({pos:+.1f}mm)")

        cx, cy, cz = mesh.center
        if axis == "x":
            origin = (pos, cy, cz)
        elif axis == "y":
            origin = (cx, pos, cz)
        else:
            origin = (cx, cy, pos)

        try:
            try:
                mesh.clear_cell_data()
            except Exception:
                pass
            clipped = mesh.clip(
                normal=axis,
                origin=origin,
                invert=getattr(self, "_clip_inverted", False),
            )
            try:
                clipped.clear_cell_data()
            except Exception:
                pass
            if clipped.n_points == 0 or clipped.n_cells == 0:
                print(f"[Viewer3D] Warning: Clipping plane at {axis}={pos:.1f} produced an empty mesh.")
                p = self.window()
                if hasattr(p, "flash_status"):
                    p.flash_status(f"Empty mesh on {axis.upper()} axis clipping")
                return False

            self._clipped_mesh = clipped
            return True
        except Exception as exc:
            print(f"[Viewer3D] Mesh clip calculation failed: {exc}")
            return False

    def _get_current_display_mesh(self) -> pv.PolyData | None:
        """Returns the active mesh for display:
        - If clipping is enabled and a valid clipped mesh exists, returns self._clipped_mesh.
        - Otherwise returns the complete unmodified self._bone_mesh.
        """
        orig = getattr(self, "_bone_mesh", None)
        if getattr(self, "_clip_active", False) and getattr(self, "_clipped_mesh", None) is not None:
            return self._clipped_mesh
        return orig

    def _apply_bone_rendering(self):
        """Renders the current display mesh (clipped or unclipped) using either
        the Hounsfield Heatmap colormap or warm natural cortical bone shading,
        depending on self._density_active.
        """
        mesh = self._get_current_display_mesh()
        if mesh is None or not hasattr(self, "plotter"):
            return

        try:
            if self.bone_actor is not None:
                self.plotter.remove_actor(self.bone_actor, render=False)
                self.bone_actor = None

            if getattr(self, "skeleton_mode", "solid") != "hidden":
                if getattr(self, "_density_active", False):
                    # Heatmap mode: verify presence of HU_density scalars
                    has_hu = "HU_density" in mesh.point_data
                    if not has_hu:
                        orig = getattr(self, "_bone_mesh", None)
                        if orig is not None and "HU_density" in orig.point_data:
                            self._update_clipped_mesh()
                            mesh = self._get_current_display_mesh()
                            has_hu = mesh is not None and "HU_density" in mesh.point_data

                    if has_hu:
                        meshset = getattr(self, "_meshset", None)
                        if meshset is not None and hasattr(meshset, "get_hu_range"):
                            lo, hi = meshset.get_hu_range()
                        else:
                            arr = np.asarray(mesh.point_data["HU_density"])
                            lo = float(max(150.0, np.percentile(arr, 5)))
                            hi = float(max(lo + 300.0, min(2200.0, np.percentile(arr, 98))))

                        self.bone_actor = self.plotter.add_mesh(
                            mesh,
                            scalars="HU_density",
                            cmap="turbo",
                            clim=(lo, hi),
                            smooth_shading=True,
                            ambient=0.35,
                            diffuse=0.75,
                            specular=0.15,
                            specular_power=10,
                            opacity=getattr(self, "_bone_opacity", 1.0),
                            name="bone",
                            scalar_bar_args={
                                "title": "Density (HU)",
                                "color": "#e0e0e0",
                                "title_font_size": 11,
                                "label_font_size": 9,
                                "shadow": False,
                                "n_labels": 5,
                                "fmt": "%.0f",
                                "position_x": 0.84,
                                "position_y": 0.05,
                                "width": 0.12,
                                "height": 0.38,
                            },
                        )
                        if hasattr(self, "legend_card"):
                            self.legend_card.setVisible(True)
                            self._reposition_legend()
                    else:
                        self._density_active = False
                        if hasattr(self, "combo_bone_mode"):
                            self.combo_bone_mode.blockSignals(True)
                            self.combo_bone_mode.setCurrentIndex(0)
                            self.combo_bone_mode.blockSignals(False)
                        self._sync_bone_mode_buttons(False)
                        if hasattr(self, "legend_card"):
                            self.legend_card.setVisible(False)
                        self._render_normal_bone(mesh)
                else:
                    # Normal bone view: warm natural cortical bone shading
                    try:
                        self.plotter.remove_scalar_bar("Density (HU)")
                    except Exception:
                        pass
                    if hasattr(self, "legend_card"):
                        self.legend_card.setVisible(False)
                    self._render_normal_bone(mesh)
            else:
                # Skeleton hidden: hide scalar bar if active
                try:
                    self.plotter.remove_scalar_bar("Density (HU)")
                except Exception:
                    pass
                if hasattr(self, "legend_card"):
                    self.legend_card.setVisible(False)

            self.plotter.render()
        except Exception as exc:
            print(f"[Viewer3D] _apply_bone_rendering failed: {exc}")

    def _render_normal_bone(self, mesh: pv.PolyData):
        """Helper to add mesh with warm natural cortical bone shading."""
        from dicom_engine import (
            _BONE_COLOUR, _BONE_AMBIENT, _BONE_DIFFUSE,
            _BONE_SPECULAR, _BONE_SPECULAR_PWR,
        )
        self.bone_actor = self.plotter.add_mesh(
            mesh,
            color=_BONE_COLOUR,
            smooth_shading=True,
            ambient=_BONE_AMBIENT,
            diffuse=_BONE_DIFFUSE,
            specular=_BONE_SPECULAR,
            specular_power=_BONE_SPECULAR_PWR,
            opacity=getattr(self, "_bone_opacity", 1.0),
            name="bone",
        )

    def set_clipping(self, active: bool):
        """Enable or disable interactive clipping plane.
        - When active=True: generates clipped mesh and renders with current mode.
        - When active=False: restores the complete original bone mesh without permanently modifying geometry.
        """
        orig = getattr(self, "_original_mesh", None) or getattr(self, "_bone_mesh", None)
        if orig is None:
            self._clip_active = False
            return

        self._clip_active = bool(active)

        if hasattr(self, "btn_clip_toggle"):
            self.btn_clip_toggle.blockSignals(True)
            self.btn_clip_toggle.setChecked(self._clip_active)
            self.btn_clip_toggle.setText("✂ Clip: ON ▼" if self._clip_active else "✂ Clip: OFF ▼")
            self.btn_clip_toggle.blockSignals(False)

        self._sync_clip_controls_enabled()

        if self._clip_active:
            self._update_clipped_mesh()
        else:
            self._clipped_mesh = None

        self._apply_bone_rendering()
        self._notify_metadata_changed()

    def set_clip_axis(self, axis: str):
        """Sets the clipping plane axis ('x', 'y', or 'z') and updates the mesh."""
        axis = str(axis).lower().strip()
        if axis not in ("x", "y", "z"):
            axis = "y"
        self._clip_axis = axis

        if hasattr(self, "combo_clip_axis"):
            axis_indices = {"x": 0, "y": 1, "z": 2}
            idx = axis_indices.get(axis, 1)
            if self.combo_clip_axis.currentIndex() != idx:
                self.combo_clip_axis.blockSignals(True)
                self.combo_clip_axis.setCurrentIndex(idx)
                self.combo_clip_axis.blockSignals(False)
            self._sync_axis_buttons(idx)

        if self._clip_active:
            self._update_clipped_mesh()
            self._apply_bone_rendering()
            self._notify_metadata_changed()
        else:
            # Auto-enable when an axis is explicitly chosen
            self.set_clipping(True)

    def set_clip_position(self, fraction: float):
        """Sets clipping position as a fraction of the model bounds (0.0 to 1.0)."""
        self._clip_fraction = float(np.clip(fraction, 0.0, 1.0))

        if hasattr(self, "slider_clip_pos"):
            slider_val = int(round(self._clip_fraction * 100))
            if self.slider_clip_pos.value() != slider_val:
                self.slider_clip_pos.blockSignals(True)
                self.slider_clip_pos.setValue(slider_val)
                self.slider_clip_pos.blockSignals(False)

        if self._clip_active:
            self._update_clipped_mesh()
            self._apply_bone_rendering()
            self._notify_metadata_changed()

    def reverse_clip_direction(self):
        """Inverts which side of the clipping plane is retained."""
        self._clip_inverted = not getattr(self, "_clip_inverted", False)
        if self._clip_active:
            self._update_clipped_mesh()
            self._apply_bone_rendering()
            self._notify_metadata_changed()

    def set_bone_opacity(self, opacity: float):
        """Sets the bone model opacity interactively.
        - Clamped between 0.1 (10% almost invisible) and 1.0 (100% fully opaque).
        - Changes the VTK actor's opacity property directly in <1ms without rebuilding geometry.
        - Preserves clipping, heatmap colors, scalars, lighting, and camera.
        """
        self._bone_opacity = float(np.clip(opacity, 0.1, 1.0))

        # Synchronize UI slider and label
        pct = int(round(self._bone_opacity * 100))
        if hasattr(self, "lbl_opacity"):
            self.lbl_opacity.setText(f"{pct}%")

        if hasattr(self, "slider_opacity"):
            if self.slider_opacity.value() != pct:
                self.slider_opacity.blockSignals(True)
                self.slider_opacity.setValue(pct)
                self.slider_opacity.blockSignals(False)

        # Update active VTK actor directly without recreating mesh
        if getattr(self, "bone_actor", None) is not None:
            try:
                self.bone_actor.prop.opacity = self._bone_opacity
            except Exception:
                try:
                    self.bone_actor.GetProperty().SetOpacity(self._bone_opacity)
                except Exception as exc:
                    print(f"[Viewer3D] Setting actor opacity failed: {exc}")
            if hasattr(self, "plotter"):
                self.plotter.render()
        self._notify_metadata_changed()

    # ── Anatomical Layers & Multi-Organ Management ────────────────────────────

    def set_skeleton_mode(self, mode: str):
        """Sets skeleton rendering mode: 'solid', 'ghost', or 'hidden'."""
        mode = str(mode).lower().strip()
        if mode not in ("solid", "ghost", "hidden"):
            mode = "solid"
        self.skeleton_mode = mode

        if mode == "hidden":
            self._bone_opacity = 0.0
            if getattr(self, "bone_actor", None) is not None:
                try:
                    self.bone_actor.prop.opacity = 0.0
                except Exception:
                    try:
                        self.bone_actor.GetProperty().SetOpacity(0.0)
                    except Exception:
                        pass
            if hasattr(self, "lbl_opacity"):
                self.lbl_opacity.setText("0%")
            if hasattr(self, "slider_opacity"):
                self.slider_opacity.blockSignals(True)
                self.slider_opacity.setValue(0)
                self.slider_opacity.blockSignals(False)
            if hasattr(self, "plotter"):
                self.plotter.render()
        elif mode == "ghost":
            self.set_bone_opacity(0.28)
        else:
            self.set_bone_opacity(1.0)

    def set_organ_visible(self, organ_name: str, visible: bool):
        """Compatibility stub."""
        pass

    def toggle_organ(self, organ_name: str):
        """Compatibility stub."""
        pass

    def isolate_organ(self, organ_name: str):
        """Compatibility stub."""
        pass

    def reset_anatomical_layers(self):
        """Compatibility stub."""
        self.set_skeleton_mode("solid")

    def toggle_dvr_mode(self, enabled: bool | None = None, preset: str | None = None):
        """Compatibility stub."""
        pass

    def set_dvr_preset(self, preset_name: str):
        """Compatibility stub."""
        pass

    def _apply_dvr_rendering(self):
        """Compatibility stub."""
        pass

    def _update_organ_actors(self):
        """Compatibility stub."""
        pass

    def set_density_colormap(self, active: bool):
        """Toggle density-based heatmap coloring using genuine CT Hounsfield Units (HU).
        - When active=False (Normal Bone View): restores warm cortical bone shading.
        - When active=True (Hounsfield Heatmap): renders bone with a continuous HU color gradient.
        - Preserves clipping state seamlessly!
        """
        mesh = self._get_current_display_mesh()
        if mesh is None:
            return

        # Synchronize UI dropdown and buttons
        self._sync_bone_mode_buttons(active)
        if hasattr(self, "combo_bone_mode"):
            target_idx = 1 if active else 0
            if self.combo_bone_mode.currentIndex() != target_idx:
                self.combo_bone_mode.blockSignals(True)
                self.combo_bone_mode.setCurrentIndex(target_idx)
                self.combo_bone_mode.blockSignals(False)

        if active:
            has_hu = "HU_density" in mesh.point_data
            if not has_hu:
                # Attempt to populate from cached volume if available
                meshset = getattr(self, "_meshset", None)
                active_path = getattr(self, "_active_path", "")
                if meshset is not None and hasattr(meshset, "_try_attach_hu_from_volume") and active_path:
                    meshset._try_attach_hu_from_volume(active_path)
                    if getattr(self, "_clip_active", False):
                        self._update_clipped_mesh()
                    mesh = self._get_current_display_mesh()
                    has_hu = mesh is not None and "HU_density" in mesh.point_data

            if not has_hu:
                print("[Viewer3D] Genuine HU density data is unavailable for this scan. No fake HU values will be displayed.")
                if hasattr(self, "combo_bone_mode"):
                    self.combo_bone_mode.blockSignals(True)
                    self.combo_bone_mode.setCurrentIndex(0)
                    self.combo_bone_mode.blockSignals(False)
                self._sync_bone_mode_buttons(False)
                self._density_active = False
                if hasattr(self, "legend_card"):
                    self.legend_card.setVisible(False)
                p = self.window()
                if hasattr(p, "flash_status"):
                    p.flash_status("HU density unavailable for this scan")
                self._apply_bone_rendering()
                return

            self._density_active = True
        else:
            self._density_active = False

        self._apply_bone_rendering()
        self._notify_metadata_changed()

    def save_screenshot(self) -> str:
        """Capture the current 3D viewport to captures/. Voice: 'take screenshot'."""
        import datetime, os as _os
        try:
            out_dir = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "captures"
            )
            _os.makedirs(out_dir, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            path = _os.path.join(out_dir, f"aegis-capture-{stamp}.png")
            self.plotter.screenshot(path)
            print(f"[Viewer3D] Screenshot saved: {path}")
            return path
        except Exception as exc:
            print(f"[Viewer3D] Screenshot failed: {exc}")
            return ""

    _GESTURE_SLICE_SENSITIVITY = 0.04   # hand travel needed per slice step

    def _gesture_scroll_slices(self, delta_y: float):
        """Open-palm vertical movement scrolls the MPR axial stack.

        Movement is accumulated so sub-threshold jitter doesn't fire a step --
        without this, MediaPipe noise would flip through slices uncontrollably.
        """
        mpr = getattr(self, "mpr_view", None)
        if mpr is None or getattr(mpr, "vol_data", None) is None:
            return
        self._slice_scroll_accum = getattr(self, "_slice_scroll_accum", 0.0) + float(delta_y)
        if abs(self._slice_scroll_accum) < self._GESTURE_SLICE_SENSITIVITY:
            return
        steps = int(self._slice_scroll_accum / self._GESTURE_SLICE_SENSITIVITY)
        self._slice_scroll_accum -= steps * self._GESTURE_SLICE_SENSITIVITY
        mpr.step_axial(steps)

    def set_tissue_melt(self, melt_factor: float):
        """No-op: skin mesh removed. Signal still connected to avoid errors."""
        pass

    def _select_preset(self, name: str):
        """Applies an anatomical window / rendering preset."""
        if name == "Bone":
            self.combo_bone_mode.setCurrentIndex(0)
            self._set_wl_preset_all("Bone")
        elif name == "Soft Tissue":
            self._set_wl_preset_all("Soft Tissue")
        elif name == "Lung":
            self._set_wl_preset_all("Lung")

    def _set_wl_preset_all(self, preset_name: str):
        """Propagates window / level preset to 2D slice and MPR viewports."""
        target = getattr(self, "slice_2d_viewer", None) or getattr(self, "panel_2d", None)
        if target is not None and hasattr(target, "set_window_preset"):
            target.set_window_preset(preset_name)
        if hasattr(self, "mpr_view") and self.mpr_view is not None:
            self.mpr_view.set_window_preset(preset_name)

    def export_case_report(self) -> str:
        """Generates and exports an exhaustive, publication-grade PDF case summary using report_export."""
        from report_export import build_case_report
        from screens.study_info_panel import extract_safe_metadata

        patient = getattr(self, "current_patient", None)
        if not patient or not isinstance(patient, dict):
            folder = getattr(self, "_active_path", "")
            folder_name = os.path.basename(os.path.normpath(folder)) if folder else "Local Scan"
            patient = {
                "_is_local": True,
                "name": folder_name,
                "mrn": f"LOCAL-{folder_name.upper()}",
                "age": "—",
                "sex": "—",
                "scans": 1,
            }

        scan = getattr(self, "current_scan", None)
        if not scan or not isinstance(scan, dict):
            active_p = getattr(self, "_active_path", "")
            scan = {
                "type": "CT",
                "date": "Local dataset",
                "file_path": active_p or "-",
                "slice_count": len(self.slices) if hasattr(self, "slices") and self.slices is not None else "-",
            }

        # Safe technical DICOM and volume metadata
        safe_meta = extract_safe_metadata(self)

        # 2D and 3D Viewport State
        target_2d = getattr(self, "slice_2d_viewer", None) or getattr(self, "panel_2d", None)
        if target_2d is not None and getattr(target_2d, "vol_data", None) is None:
            if hasattr(self, "mpr_view") and getattr(self.mpr_view, "vol_data", None) is not None:
                target_2d.load_volume(
                    self.mpr_view.vol_data,
                    self.mpr_view.pixel_spacing,
                    self.mpr_view.slice_thickness
                )
            elif hasattr(self, "_active_path") and self._active_path and os.path.isdir(self._active_path):
                try:
                    from dicom_engine import load_volume_for_mpr
                    vol, sp, z_sp, _ = load_volume_for_mpr(self._active_path)
                    target_2d.load_volume(vol, sp, z_sp)
                except Exception as exc:
                    print(f"[Viewer3D] Could not preload volume for 2D report slice: {exc}")

        cur_orient = "Axial"
        cur_slice_str = None
        cur_pos_str = None
        cur_ww = safe_meta.get("current_window_width")
        cur_wl = safe_meta.get("current_window_level")
        cur_preset = safe_meta.get("current_wl_preset", "Bone")

        if target_2d is not None:
            cur_orient = getattr(target_2d, "current_orientation", "axial").capitalize()
            cur_idx = target_2d.get_current_slice_index() if hasattr(target_2d, "get_current_slice_index") else getattr(target_2d, "current_slice_index", 0)
            total_cnt = target_2d.get_slice_count() if hasattr(target_2d, "get_slice_count") else 0
            if total_cnt > 0:
                cur_slice_str = f"Slice {cur_idx + 1} / {total_cnt}"
            if hasattr(target_2d, "get_current_physical_position"):
                pos = target_2d.get_current_physical_position()
                if pos != (0.0, 0.0, 0.0):
                    cur_pos_str = f"X: {pos[0]:.1f} mm, Y: {pos[1]:.1f} mm, Z: {pos[2]:.1f} mm"
            cur_ww = f"{target_2d.window_width:.0f} HU" if hasattr(target_2d, "window_width") else cur_ww
            cur_wl = f"{target_2d.window_level:.0f} HU" if hasattr(target_2d, "window_level") else cur_wl
            cur_preset = getattr(target_2d, "current_preset", "bone").capitalize()

        clip_active = bool(getattr(self, "_clip_active", False))
        clip_axis = str(getattr(self, "_clip_axis", "Y")).upper()
        clip_frac = int(getattr(self, "_clip_fraction", 0.5) * 100)
        clip_inv = getattr(self, "_clip_inverted", False)
        clip_summary = (
            f"Enabled — {clip_axis}-axis — Position {clip_frac}% — "
            f"{'Inverted direction' if clip_inv else 'Standard direction'}"
        ) if clip_active else None

        # Check if MPR was actively used or is open
        mpr_state = None
        mpr = getattr(self, "mpr_view", None)
        if mpr is not None and getattr(mpr, "vol_data", None) is not None:
            if (hasattr(mpr, "isVisible") and mpr.isVisible()) or getattr(self, "_mpr_active", False):
                H, W, D = mpr.vol_data.shape
                mpr_state = {
                    "axial": f"Slice {getattr(mpr, 'idx_z', 0) + 1} / {D}",
                    "coronal": f"Slice {getattr(mpr, 'idx_y', 0) + 1} / {H}",
                    "sagittal": f"Slice {getattr(mpr, 'idx_x', 0) + 1} / {W}",
                }

        # Meaningful viewing state only -- omit UI toggles & internal defaults
        view_state = {
            "orientation": cur_orient,
            "slice_index": cur_slice_str,
            "physical_pos": cur_pos_str,
            "window_width": cur_ww,
            "window_level": cur_wl,
            "preset": cur_preset,
            "sync_mode": "Enabled" if getattr(self, "_sync_2d_3d_enabled", False) else None,
            "clipping_active": clip_active,
            "clipping_summary": clip_summary,
            "mpr_state": mpr_state,
            "cursor_hu": getattr(self, "_last_cursor_hu", None),
        }

        # Deduplicated Measurements Collection
        measurements = []
        is_synced = getattr(self, "_sync_2d_3d_enabled", False)

        # 1. 2D CT Slice Measurements (with true slice index and physical coordinates)
        if target_2d is not None:
            try:
                meas_2d = target_2d.get_measurements() if hasattr(target_2d, "get_measurements") else getattr(target_2d, "measurements", [])
                for idx, m in enumerate(meas_2d):
                    val = getattr(m, "distance_mm", 0.0)
                    orient = getattr(m, "orientation", "axial").capitalize()
                    sl_num = getattr(m, "slice_idx", 0) + 1
                    plane_desc = f"2D {orient} / 3D synchronized" if is_synced else f"2D {orient}"
                    measurements.append({
                        "plane": plane_desc,
                        "kind": "distance",
                        "value": float(val),
                        "unit": "mm",
                        "slice_idx": f"Slice {sl_num}",
                        "p1": getattr(m, "physical_start", None),
                        "p2": getattr(m, "physical_end", None),
                        "source": "2D",
                    })
            except Exception as exc:
                print(f"[Viewer3D] could not collect 2D measurements: {exc}")

        # 2. 3D Volume Measurements (exclude 2D_synced to avoid duplication!)
        try:
            for idx, m in enumerate(getattr(self, "_measurements_3d", [])):
                if m.get("source") == "2D_synced":
                    continue  # Already represented from 2D above!
                val = m.get("distance_mm", 0.0)
                measurements.append({
                    "plane": "3D Volume Space",
                    "kind": "distance",
                    "value": float(val),
                    "unit": "mm",
                    "slice_idx": "3D Space",
                    "p1": m.get("pt1"),
                    "p2": m.get("pt2"),
                    "source": "3D",
                })
        except Exception as exc:
            print(f"[Viewer3D] could not collect 3D measurements: {exc}")

        # 3. MPR Viewport Measurements
        try:
            mpr = getattr(self, "mpr_view", None)
            if mpr is not None and hasattr(mpr, "get_measurement_summary"):
                measurements.extend(mpr.get_measurement_summary())
        except Exception as exc:
            print(f"[Viewer3D] could not collect MPR measurements: {exc}")

        # Visual Figure Captures (2D CT Slice first as primary representative scan, then 3D Viewport)
        images = []
        try:
            if target_2d is not None and hasattr(target_2d, "save_screenshot"):
                shot_2d = target_2d.save_screenshot()
                if shot_2d and os.path.isfile(shot_2d):
                    sl_ref = f" ({cur_slice_str})" if cur_slice_str else ""
                    images.append({
                        "path": shot_2d,
                        "title": f"2D CT — {cur_orient}",
                        "caption": f"Figure 1 — Calibrated 2D {cur_orient} CT slice{sl_ref}",
                    })
        except Exception as exc:
            print(f"[Viewer3D] Screenshot 2D failed: {exc}")

        shot_3d = ""
        try:
            shot_3d = self.save_screenshot()
            if shot_3d and os.path.isfile(shot_3d):
                fig_num = len(images) + 1
                images.append({
                    "path": shot_3d,
                    "title": "3D Volume Reconstruction",
                    "caption": f"Figure {fig_num} — 3D anatomical volume reconstruction",
                })
        except Exception as exc:
            print(f"[Viewer3D] Screenshot 3D failed: {exc}")

        try:
            mpr = getattr(self, "mpr_view", None)
            if mpr is not None and hasattr(mpr, "save_screenshot") and hasattr(mpr, "isVisible") and mpr.isVisible():
                shot_mpr = mpr.save_screenshot()
                if shot_mpr and os.path.isfile(shot_mpr):
                    images.append({
                        "path": shot_mpr,
                        "title": "MPR Tri-Planar Viewports",
                        "caption": "Figure 3 — Synchronized Multi-Planar Reconstruction (Axial, Coronal, Sagittal) overview",
                    })
        except Exception as exc:
            print(f"[Viewer3D] Screenshot MPR failed: {exc}")

        # Notes from Database
        notes = []
        try:
            if not patient.get("_is_local") and patient.get("mrn"):
                from database import get_notes_for_patient
                notes = get_notes_for_patient(patient["mrn"]) or []
        except Exception:
            notes = []

        try:
            path = build_case_report(
                patient=patient,
                scan=scan,
                measurements=measurements,
                notes=notes,
                screenshot_path=shot_3d,
                metadata=safe_meta,
                view_state=view_state,
                images=images,
            )
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText(f"  📄 Report saved: {os.path.basename(path)}")
            try:
                from database import log_action
                if patient.get("mrn"):
                    log_action("export_report", patient.get("mrn"), path)
            except Exception:
                pass
            return path
        except Exception as exc:
            print(f"[Viewer3D] report export failed: {exc}")
            import traceback
            traceback.print_exc()
            if hasattr(self, "info_bar") and self.info_bar:
                self.info_bar.setText("  ⚠️ Report export failed")
            return ""
