import os
import sys
import site
if "QT_QPA_PLATFORM_PLUGIN_PATH" not in os.environ:
    for _sp in site.getsitepackages():
        _p = os.path.join(_sp, "PyQt6", "Qt6", "plugins", "platforms")
        if os.path.isdir(_p):
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = _p
            break

import time
import pyautogui
from PyQt6.QtGui import QCursor, QPixmap, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QStackedWidget, QLabel, QSizePolicy
)
from PyQt6.QtCore import Qt, QPoint, QPointF, QTimer

# Apply global PyAutoGUI speed overrides
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0
pyautogui.MINIMUM_DURATION = 0

from theme import DARK_STYLESHEET
from screens.dashboard import Dashboard
from screens.record import PatientRecord
from screens.scans import ScanGallery
from screens.viewer_3d import Viewer3D
from screens.or_icu_mode import OrIcuMode
from signal_bus import signal_bus

from gesture import GestureWorker  # Background AI & Air Mouse Engine
from voice_commands import VoiceCommandWorker  # Offline Voice Recognition Engine
from database import (
    touch_patient, log_action, get_patient, get_notes_for_patient,
    save_surgical_plan_version, get_surgical_plan_versions, get_surgical_plan_version,
    delete_surgical_plan_version, rename_surgical_plan_version,
    save_tracked_measurement, get_tracked_measurements_for_scan,
    get_tracked_measurements_for_patient, delete_tracked_measurement,
    get_previous_scan_for_patient,
    save_scan_annotation, get_scan_annotations_for_scan,
    get_scan_annotations_for_patient, delete_scan_annotation,
    save_device_marker, get_device_markers_for_scan,
    get_device_markers_for_patient, delete_device_marker
)
from report_export import build_case_report
from same_location import SameLocationReview
from measurement_tracker import MeasurementTracker, TrackedMeasurement
from annotation_carry_forward import AnnotationCarryForwardManager, ScanAnnotation
from what_changed import ChangeReview
from device_markers import DeviceMarker, DeviceMarkerManager, CONTROLLED_DEVICE_TYPES
from quick_handoff import QuickHandoff, QuickHandoffManager



# ─────────────────────────────────────────────────────────────────────────────
# Floating Camera HUD  (picture-in-picture overlay)
# Subscribes to signal_bus.camera_frame and renders each annotated QImage.
# Positioned programmatically as a child of the central widget so it floats
# above all other content without disrupting any layout.
# ─────────────────────────────────────────────────────────────────────────────
class CameraHUD(QWidget):
    # PiP dimensions — tall enough to see hand skeleton clearly
    HUD_W = 280
    HUD_H = 210
    MARGIN = 24          # gap from the window edges (clears scrollbar)
    BORDER_RADIUS = 10

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(self.HUD_W, self.HUD_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # Always render on top of sibling widgets
        self.raise_()

        # Outer card with rounded corners + subtle border
        card = QWidget(self)
        card.setObjectName("CameraHUDCard")
        card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        card.setGeometry(0, 0, self.HUD_W, self.HUD_H)
        card.setStyleSheet(
            "#CameraHUDCard {"
            "  background: #0d0d0d;"
            "  border: 1px solid #2a2a2a;"
            f" border-radius: {self.BORDER_RADIUS}px;"
            "}"
        )

        inner = QVBoxLayout(card)
        inner.setContentsMargins(6, 6, 6, 6)
        inner.setSpacing(4)

        # Header label
        header = QLabel("[Camera]  Gesture Camera")
        header.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.setStyleSheet(
            "color: #8a8a8a; font-size: 10px; font-weight: 600;"
        )
        header.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        inner.addWidget(header)

        # Camera feed label — fills remaining space
        self.feed = QLabel()
        self.feed.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.feed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.feed.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.feed.setText("Waiting for camera…")
        self.feed.setStyleSheet(
            "background: #000; color: #444; font-size: 10px;"
            " border-radius: 6px;"
        )
        inner.addWidget(self.feed)

        signal_bus.camera_frame.connect(self._on_frame)
        self._reposition()

    def _on_frame(self, qimage):
        """Slot: receives every annotated frame from GestureWorker."""
        if not self.isVisible():
            return
        pix = QPixmap.fromImage(qimage).scaled(
            self.feed.width(),
            self.feed.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.feed.setPixmap(pix)

    def _reposition(self):
        """Pin the HUD to the bottom-right corner of its parent."""
        if self.parent() is None:
            return
        pw = self.parent().width()
        ph = self.parent().height()
        x = pw - self.HUD_W - self.MARGIN
        y = ph - self.HUD_H - self.MARGIN
        self.move(x, y)

    # Re-pin after every parent resize
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition()

    def toggle_visibility(self):
        self.setVisible(not self.isVisible())
        if self.isVisible():
            self.raise_()  # ensure it stays on top after re-show


class MainWindow(QMainWindow):
    AUTO_LOCK_MS = 5 * 60 * 1000   # 5 minutes of no gesture activity
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Aegis-Touch")
        self.resize(1280, 720)

        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)

        # --- Top-level nav bar ---
        nav_bar = QHBoxLayout()
        self.clinical_btn = QPushButton("Clinical View")
        self.viewer_btn = QPushButton("3D Viewer")
        self.or_icu_btn = QPushButton("OR/ICU Mode")

        # Air Mouse toggle — OFF by default
        self._air_mouse_on = False
        self.air_mouse_btn = QPushButton("Air Mouse: OFF")
        self.air_mouse_btn.setCheckable(True)
        self.air_mouse_btn.setChecked(False)
        self.air_mouse_btn.setStyleSheet(
            "QPushButton { color: #888; border: 1px solid #555; padding: 4px 10px; }"
        )
        self.air_mouse_btn.clicked.connect(self._toggle_air_mouse)

        # Voice command toggle — OFF by default
        self._voice_on = False
        self.voice_btn = QPushButton("Voice: OFF")
        self.voice_btn.setCheckable(True)
        self.voice_btn.setChecked(False)
        self.voice_btn.setStyleSheet(
            "QPushButton { color: #888; border: 1px solid #555; padding: 4px 10px; }"
        )
        self.voice_btn.clicked.connect(self._toggle_voice)

        self.back_btn = QPushButton("<- Back")
        self.back_btn.setVisible(False)
        self.back_btn.clicked.connect(self._go_back)
        self._nav_history = []

        # Camera HUD toggle — shown in nav bar
        self.cam_btn = QPushButton("Camera: ON")
        self.cam_btn.setStyleSheet(
            "QPushButton { color: #aaa; border: 1px solid #444; padding: 4px 10px; }"
        )

        for btn in (self.back_btn, self.clinical_btn, self.viewer_btn, self.or_icu_btn):
            nav_bar.addWidget(btn)
        nav_bar.addStretch()
        nav_bar.addWidget(self.cam_btn)
        nav_bar.addWidget(self.air_mouse_btn)
        nav_bar.addWidget(self.voice_btn)

        # Command-confirmation toast (hidden until flash_status() is called)
        self.status_toast = QLabel("")
        self.status_toast.setVisible(False)
        self.status_toast.setStyleSheet(
            "QLabel { color: #00e5ff; background: #06232b; border: 1px solid #0d4a5c; "
            "border-radius: 4px; padding: 3px 10px; font-size: 10px; font-weight: 600; }"
        )
        nav_bar.addWidget(self.status_toast)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self.status_toast.setVisible(False))

        # PDF case-summary export
        self.export_btn = QPushButton("Export Report")
        self.export_btn.setStyleSheet(
            "QPushButton { color: #aaa; border: 1px solid #444; padding: 4px 10px; }"
        )
        self.export_btn.clicked.connect(self.export_case_report)
        nav_bar.addWidget(self.export_btn)
        outer_layout.addLayout(nav_bar)

        # --- Stacked screens ---
        self.stack = QStackedWidget()
        outer_layout.addWidget(self.stack)

        # Lock overlay (hidden until the inactivity timer fires)
        self.lock_screen = QWidget()
        self.lock_screen.setVisible(False)
        lock_layout = QVBoxLayout(self.lock_screen)
        lock_layout.addStretch()
        lock_title = QLabel("SESSION LOCKED")
        lock_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lock_title.setStyleSheet("color: #00e5ff; font-size: 22px; font-weight: 700;")
        lock_msg = QLabel("Patient data hidden after inactivity.")
        lock_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lock_msg.setStyleSheet("color: #666; font-size: 11px;")
        unlock_btn = QPushButton("Resume Session")
        unlock_btn.setFixedWidth(180)
        unlock_btn.setStyleSheet(
            "QPushButton { color: #00e5ff; border: 1px solid #00e5ff; padding: 8px 14px; "
            "border-radius: 4px; font-weight: 600; }"
        )
        unlock_btn.clicked.connect(self._release_lock)
        lock_layout.addWidget(lock_title)
        lock_layout.addWidget(lock_msg)
        btn_row = QHBoxLayout()
        btn_row.addStretch(); btn_row.addWidget(unlock_btn); btn_row.addStretch()
        lock_layout.addLayout(btn_row)
        lock_layout.addStretch()
        outer_layout.addWidget(self.lock_screen)

        self.dashboard = Dashboard()
        self.viewer_3d = Viewer3D()
        self.or_icu_mode = OrIcuMode()

        self.stack.addWidget(self.dashboard)
        self.stack.addWidget(self.viewer_3d)
        self.stack.addWidget(self.or_icu_mode)

        # --- Floating Camera HUD (PiP overlay, child of central widget) ---
        # Must be created AFTER outer_layout is fully built so the central
        # widget has its final geometry before the first _reposition() call.
        self.cam_hud = CameraHUD(central)
        self.cam_hud.show()
        self.cam_btn.clicked.connect(self._toggle_camera_hud)

        self.dashboard.view_records_clicked.connect(self.show_record)
        self.dashboard.view_scans_clicked.connect(self.show_scans)
        self.dashboard.view_3d_direct_clicked.connect(self.show_3d_direct)

        self.clinical_btn.clicked.connect(lambda: self._go_root(self.dashboard))
        self.viewer_btn.clicked.connect(lambda: self._go_root(self.viewer_3d))
        self.or_icu_btn.clicked.connect(self._open_or_icu)
        self.or_icu_mode.open_in_viewer_requested.connect(self._on_icu_or_open_in_viewer)
        self.or_icu_mode.set_surgical_plan(self.viewer_3d.surgical_plan)
        self.or_icu_mode.set_entry_requested.connect(self._on_icu_or_set_entry)
        self.or_icu_mode.set_target_requested.connect(self._on_icu_or_set_target)
        self.or_icu_mode.view_entry_requested.connect(self._on_icu_or_view_entry)
        self.or_icu_mode.view_target_requested.connect(self._on_icu_or_view_target)
        self.or_icu_mode.clear_entry_requested.connect(self._on_icu_or_clear_entry)
        self.or_icu_mode.clear_target_requested.connect(self._on_icu_or_clear_target)
        self.or_icu_mode.clear_both_requested.connect(self._on_icu_or_clear_both)
        self.or_icu_mode.view_route_requested.connect(self._on_icu_or_view_route)
        self.or_icu_mode.clear_route_requested.connect(self._on_icu_or_clear_route)
        self.or_icu_mode.add_structure_requested.connect(self._on_icu_or_add_structure)
        self.or_icu_mode.view_structure_requested.connect(self._on_icu_or_view_structure)
        self.or_icu_mode.remove_structure_requested.connect(self._on_icu_or_remove_structure)
        self.or_icu_mode.clear_structures_requested.connect(self._on_icu_or_clear_structures)
        self.or_icu_mode.corridor_visibility_changed.connect(self._on_icu_or_corridor_visibility_changed)
        self.or_icu_mode.corridor_radius_changed.connect(self._on_icu_or_corridor_radius_changed)
        self.or_icu_mode.instrument_visibility_changed.connect(self._on_icu_or_instrument_visibility_changed)
        self.or_icu_mode.instrument_depth_changed.connect(self._on_icu_or_instrument_depth_changed)
        self.or_icu_mode.instrument_diameter_changed.connect(self._on_icu_or_instrument_diameter_changed)
        self.or_icu_mode.view_instrument_requested.connect(self._on_icu_or_view_instrument)
        self.or_icu_mode.deviation_visibility_changed.connect(self._on_icu_or_deviation_visibility_changed)
        self.or_icu_mode.deviation_offsets_changed.connect(self._on_icu_or_deviation_offsets_changed)
        self.or_icu_mode.deviation_angles_changed.connect(self._on_icu_or_deviation_angles_changed)
        self.or_icu_mode.reset_deviation_requested.connect(self._on_icu_or_reset_deviation)
        self.or_icu_mode.save_plan_requested.connect(self._on_icu_or_save_plan)
        self.or_icu_mode.save_as_new_requested.connect(self._on_icu_or_save_as_new_plan)
        self.or_icu_mode.restore_plan_requested.connect(self._on_icu_or_restore_plan)
        self.or_icu_mode.delete_plan_requested.connect(self._on_icu_or_delete_plan)
        self.or_icu_mode.rename_plan_requested.connect(self._on_icu_or_rename_plan)
        self.viewer_3d.plan_save_requested.connect(self._on_icu_or_save_plan)
        self.viewer_3d.plan_versions_requested.connect(self._on_icu_or_show_plan_versions)
        self.or_icu_mode.quick_view_requested.connect(self._on_icu_or_quick_view)
        self.viewer_3d.quick_view_applied.connect(self._on_viewer_quick_view_applied)

        # Before vs After Comparison Signal Connections
        self.or_icu_mode.start_comparison_requested.connect(self._on_icu_or_start_comparison)
        self.or_icu_mode.exit_comparison_requested.connect(self._on_icu_or_exit_comparison)
        self.or_icu_mode.toggle_comparison_view_requested.connect(self._on_icu_or_toggle_comparison_view)
        self.or_icu_mode.comparison_mode_changed.connect(self._on_icu_or_comparison_mode_changed)
        self.or_icu_mode.overlay_opacity_changed.connect(self._on_icu_or_overlay_opacity_changed)

        # ICU Feature 1: Current vs Previous Scan Connections
        self.or_icu_mode.icu_view_previous_requested.connect(self._on_icu_view_previous)
        self.or_icu_mode.icu_view_current_requested.connect(self._on_icu_view_current)
        self.or_icu_mode.icu_toggle_requested.connect(self._on_icu_toggle)
        self.or_icu_mode.icu_exit_comparison_requested.connect(self._on_icu_exit_comparison)

        # ICU Feature 2: Same-Location Review State & Connections
        self.icu_same_location = SameLocationReview()
        self.or_icu_mode.icu_mark_same_location_requested.connect(self._on_icu_mark_same_location)
        self.or_icu_mode.icu_view_same_location_current_requested.connect(self._on_icu_view_same_location_current)
        self.or_icu_mode.icu_view_same_location_previous_requested.connect(self._on_icu_view_same_location_previous)
        self.or_icu_mode.icu_clear_same_location_requested.connect(self._on_icu_clear_same_location)

        self.viewer_3d.icu_mark_same_location_requested.connect(self._on_icu_mark_same_location)
        self.viewer_3d.icu_view_same_location_requested.connect(self._on_icu_view_same_location_current)
        self.viewer_3d.icu_view_previous_location_requested.connect(self._on_icu_view_same_location_previous)
        self.viewer_3d.icu_view_current_location_requested.connect(self._on_icu_view_same_location_current)
        self.viewer_3d.icu_clear_same_location_requested.connect(self._on_icu_clear_same_location)

        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.same_location_selected.connect(self._on_same_location_selected)

        # ICU Feature 3: Measurement Tracking State & Connections
        self.measurement_tracker = MeasurementTracker()
        self.or_icu_mode.icu_track_measurement_requested.connect(self._on_icu_track_measurement)
        self.or_icu_mode.icu_delete_tracked_measurement_requested.connect(self._on_icu_delete_tracked_measurement)
        self.or_icu_mode.icu_measurement_selection_changed.connect(self._on_icu_measurement_selection_changed)

        # ICU Feature 4: Annotation Carry-Forward State & Connections
        self.annotation_cf_manager = AnnotationCarryForwardManager()
        self.or_icu_mode.icu_create_annotation_requested.connect(self._on_icu_create_annotation)
        self.or_icu_mode.icu_select_annotation_requested.connect(self._on_icu_select_annotation)
        self.or_icu_mode.icu_carry_forward_requested.connect(self._on_icu_carry_forward)
        self.or_icu_mode.icu_view_annotation_source_requested.connect(self._on_icu_view_annotation_source)
        self.or_icu_mode.icu_view_annotation_target_requested.connect(self._on_icu_view_annotation_target)
        self.or_icu_mode.icu_delete_annotation_requested.connect(self._on_icu_delete_annotation)
        self.or_icu_mode.icu_clear_annotation_selection_requested.connect(self._on_icu_clear_annotation_selection)

        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.annotation_point_selected.connect(self._on_annotation_point_selected)

        # ICU Feature 5: What Changed? State & Connections
        self.change_review = ChangeReview()
        self.or_icu_mode.icu_review_differences_requested.connect(self._on_icu_review_differences)
        self.or_icu_mode.icu_show_current_requested.connect(self._on_icu_show_current)
        self.or_icu_mode.icu_show_previous_requested.connect(self._on_icu_show_previous)
        self.or_icu_mode.icu_difference_threshold_changed.connect(self._on_icu_difference_threshold_changed)
        self.or_icu_mode.icu_clear_difference_requested.connect(self._on_icu_clear_difference)

        # ICU Feature 6: Device Markers State & Connections
        self.device_marker_manager = DeviceMarkerManager()
        self.or_icu_mode.icu_mark_device_requested.connect(self._on_icu_mark_device)
        self.or_icu_mode.icu_view_device_marker_requested.connect(self._on_icu_view_device_marker)
        self.or_icu_mode.icu_delete_device_marker_requested.connect(self._on_icu_delete_device_marker)
        self.or_icu_mode.icu_clear_device_marker_selection_requested.connect(self._on_icu_clear_device_marker_selection)

        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.device_marker_selected.connect(self._on_device_marker_point_selected)

        # ICU Feature 7: Quick Handoff State & Connections
        self.quick_handoff_manager = QuickHandoffManager()
        self.or_icu_mode.icu_generate_handoff_requested.connect(self._on_icu_generate_handoff)
        self.or_icu_mode.icu_copy_handoff_requested.connect(self._on_icu_copy_handoff)
        self.or_icu_mode.icu_view_handoff_requested.connect(self._on_icu_view_handoff)
        self.or_icu_mode.icu_clear_handoff_requested.connect(self._on_icu_clear_handoff)


        # --- Air Mouse Signal Connections ---

        signal_bus.cursor_moved.connect(self.move_os_cursor)
        signal_bus.pinch_started.connect(self.trigger_mouse_down)
        signal_bus.pinch_ended.connect(self.trigger_mouse_up)
        # 👍 Thumbs-up → ON  |  👎 Thumbs-down → OFF
        signal_bus.air_mouse_gesture_set.connect(self._set_air_mouse)

        # --- Start Gesture Engine Thread ---
        self.worker = GestureWorker()
        self.worker.start()

        # --- Start Voice Command Process Thread ---
        # --- Start Voice Command Process Thread ---
        self.voice_worker = VoiceCommandWorker()

        self.voice_worker.voice_result.connect(
            self._handle_voice_command
        )

        self.voice_worker.start()  

        # --- Privacy auto-lock: blank patient data after inactivity ---
        self._locked = False
        self._lock_timer = QTimer(self)
        self._lock_timer.setSingleShot(True)
        self._lock_timer.timeout.connect(self._engage_lock)
        self._lock_timer.start(self.AUTO_LOCK_MS)
        # Any gesture activity counts as presence and defers the lock
        signal_bus.cursor_moved.connect(lambda *_: self._reset_lock_timer())
        signal_bus.pinch_started.connect(self._reset_lock_timer)

    def _resolve_clickable_widget(self, pos: QPoint):
        """Finds the interactive Qt widget under pos, ignoring overlays like CameraHUD."""
        w = QApplication.widgetAt(pos)
        if w is None:
            for top in QApplication.topLevelWidgets():
                if top.isVisible() and top.geometry().contains(pos):
                    child = top.childAt(top.mapFromGlobal(pos))
                    w = child if child is not None else top
                    break
            if w is None:
                return None

        # Ignore CameraHUD and its children so clicks pass through to underlying content
        if getattr(self, "cam_hud", None) is not None:
            if w is self.cam_hud or self.cam_hud.isAncestorOf(w):
                return None

        # Resolve up to interactive button/control parent if clicked on child label/icon
        from PyQt6.QtWidgets import (
            QAbstractButton, QAbstractSlider, QLineEdit, QTextEdit,
            QPlainTextEdit, QComboBox, QAbstractSpinBox, QTabBar,
            QAbstractItemView, QMenu, QMenuBar
        )
        curr = w
        while curr is not None:
            if isinstance(curr, (QAbstractButton, QAbstractSlider, QLineEdit, QTextEdit,
                                 QPlainTextEdit, QComboBox, QAbstractSpinBox, QTabBar,
                                 QAbstractItemView, QMenu, QMenuBar)):
                return curr
            if curr.__class__.__name__ in ("MPRSliceWidget", "QtInteractor", "QVTKRenderWindowInteractor"):
                return curr
            curr = curr.parentWidget()

        # Non-interactive background containers (MainWindow, QStackedWidget, panels)
        # return None so OS native mouse events are dispatched cleanly everywhere.
        return None

    def move_os_cursor(self, norm_x: float, norm_y: float):
        """Moves the OS cursor from the UI thread via QCursor (no OS throttling)."""
        screen = QApplication.primaryScreen().geometry()
        target_x = screen.left() + int(norm_x * (screen.width() - 1))
        target_y = screen.top()  + int(norm_y * (screen.height() - 1))
        clamped_x = max(screen.left(), min(screen.right(), target_x))
        clamped_y = max(screen.top(),  min(screen.bottom(), target_y))

        # Click stabilization deadband:
        # When pinch starts, user's hand naturally trembles by 5-15px.
        # Suppress hand jitter within 20px radius of click origin to prevent
        # converting deliberate button clicks into aborted drags.
        from PyQt6.QtWidgets import QAbstractButton
        clicked_w = getattr(self, "_clicked_widget", None)
        is_button = isinstance(clicked_w, QAbstractButton)

        if getattr(self, "_mouse_is_down", False):
            if is_button:
                # Buttons should never turn into drags; lock cursor to click origin
                self.cursor_x = self._click_origin_x
                self.cursor_y = self._click_origin_y
            elif not getattr(self, "_is_dragging", False):
                dx = clamped_x - self._click_origin_x
                dy = clamped_y - self._click_origin_y
                if (dx * dx + dy * dy > 20 * 20):
                    self._is_dragging = True
                    self.cursor_x = clamped_x
                    self.cursor_y = clamped_y
                else:
                    # Lock cursor to click origin to guarantee solid click registration
                    self.cursor_x = self._click_origin_x
                    self.cursor_y = self._click_origin_y
            else:
                self.cursor_x = clamped_x
                self.cursor_y = clamped_y
                # If dragging an in-app Qt widget, dispatch direct QMouseEvent Move
                if clicked_w is not None:
                    pos_pt = QPoint(self.cursor_x, self.cursor_y)
                    local_pos = clicked_w.mapFromGlobal(pos_pt)
                    move_ev = QMouseEvent(
                        QMouseEvent.Type.MouseMove,
                        QPointF(local_pos),
                        QPointF(pos_pt),
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier,
                    )
                    QApplication.sendEvent(clicked_w, move_ev)
        else:
            self.cursor_x = clamped_x
            self.cursor_y = clamped_y

        QCursor.setPos(self.cursor_x, self.cursor_y)

    def _send_mouse_event(self, flags: int, x: int = 0, y: int = 0):
        """Sends native mouse event at current cursor position without DPI distortion."""
        try:
            if sys.platform == "win32":
                import ctypes
                # Calling mouse_event with dx=0, dy=0 generates the event at the current cursor position
                # without MOUSEEVENTF_ABSOLUTE or MOUSEEVENTF_MOVE, eliminating all DPI scaling errors.
                ctypes.windll.user32.mouse_event(flags, 0, 0, 0, 0)
            else:
                if flags == 2:
                    pyautogui.mouseDown(x=x, y=y)
                elif flags == 4:
                    pyautogui.mouseUp(x=x, y=y)
        except Exception:
            pass

    def trigger_mouse_down(self):
        """Fires mouse down via direct Qt event (for in-app controls) or OS event."""
        cx = getattr(self, "cursor_x", None)
        cy = getattr(self, "cursor_y", None)
        if cx is None or cy is None:
            pos = QCursor.pos()
            cx, cy = pos.x(), pos.y()
        else:
            pos = QPoint(cx, cy)

        self.cursor_x = cx
        self.cursor_y = cy
        self._click_origin_x = cx
        self._click_origin_y = cy
        self._click_down_time = time.time()
        self._mouse_is_down = True
        self._is_dragging = False

        target = self._resolve_clickable_widget(pos)
        self._clicked_widget = target

        now = time.time()
        is_double_click = False
        last_down = getattr(self, "_last_press_time", 0.0)
        last_pos = getattr(self, "_last_press_pos", None)
        if last_pos is not None and (now - last_down <= 0.45):
            dx = cx - last_pos.x()
            dy = cy - last_pos.y()
            if dx * dx + dy * dy <= 20 * 20:
                is_double_click = True

        self._last_press_time = now
        self._last_press_pos = pos

        if target is not None:
            win = target.window()
            if win and not win.isActiveWindow():
                win.activateWindow()

            if target.focusPolicy() != Qt.FocusPolicy.NoFocus:
                target.setFocus(Qt.FocusReason.MouseFocusReason)

            from PyQt6.QtWidgets import QAbstractScrollArea, QComboBox
            actual_target = target.viewport() if isinstance(target, QAbstractScrollArea) else target
            local_pos = actual_target.mapFromGlobal(pos)
            press_ev = QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(local_pos),
                QPointF(pos),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(actual_target, press_ev)

            if is_double_click:
                dbl_ev = QMouseEvent(
                    QMouseEvent.Type.MouseButtonDblClick,
                    QPointF(local_pos),
                    QPointF(pos),
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                QApplication.sendEvent(actual_target, dbl_ev)

            if isinstance(target, QComboBox):
                target.showPopup()
        else:
            self._send_mouse_event(0x0002, cx, cy)

    def trigger_mouse_up(self):
        """Fires mouse up via direct Qt event (for in-app controls) or OS event."""
        if not getattr(self, "_mouse_is_down", False):
            return

        from PyQt6.QtWidgets import QAbstractButton
        target = getattr(self, "_clicked_widget", None)
        is_button = isinstance(target, QAbstractButton)

        # If button or stationary click (not dragged), snap back to exact click origin
        if is_button or not getattr(self, "_is_dragging", False):
            target_x = self._click_origin_x
            target_y = self._click_origin_y
            self.cursor_x = target_x
            self.cursor_y = target_y
            QCursor.setPos(target_x, target_y)
        else:
            target_x = getattr(self, "cursor_x", self._click_origin_x)
            target_y = getattr(self, "cursor_y", self._click_origin_y)

        target_pos = QPoint(target_x, target_y)

        if target is not None:
            from PyQt6.QtWidgets import QAbstractScrollArea, QAbstractItemView, QComboBox
            actual_target = target.viewport() if isinstance(target, QAbstractScrollArea) else target
            local_pos = actual_target.mapFromGlobal(target_pos)
            release_ev = QMouseEvent(
                QMouseEvent.Type.MouseButtonRelease,
                QPointF(local_pos),
                QPointF(target_pos),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(actual_target, release_ev)

            # Air mouse support for QComboBox / QAbstractItemView:
            # 1. If user pinches directly on a QComboBox, cycle to next option
            if isinstance(target, QComboBox) and not getattr(self, "_is_dragging", False):
                if target.count() > 0:
                    next_idx = (target.currentIndex() + 1) % target.count()
                    target.setCurrentIndex(next_idx)
                    target.activated.emit(next_idx)
                    target.hidePopup()

            # 2. If user pinches on an item view popup (e.g. combo popup list)
            elif isinstance(target, QAbstractItemView):
                vp_pos = target.viewport().mapFromGlobal(target_pos)
                idx = target.indexAt(vp_pos)
                if idx.isValid():
                    for top in QApplication.topLevelWidgets():
                        for c in top.findChildren(QComboBox):
                            if c.view() is target:
                                c.setCurrentIndex(idx.row())
                                c.activated.emit(idx.row())
                                c.hidePopup()
                                break
                    target.setCurrentIndex(idx)
                    target.activated.emit(idx)

            self._clicked_widget = None
        else:
            self._send_mouse_event(0x0004, target_x, target_y)

        self._mouse_is_down = False
        self._is_dragging = False

    def _go_root(self, widget):
        self._nav_history.clear()
        self.back_btn.setVisible(False)
        for i in reversed(range(self.stack.count())):
            w = self.stack.widget(i)
            if w not in (self.dashboard, self.viewer_3d, self.or_icu_mode):
                self.stack.removeWidget(w)
                w.deleteLater()
        import gc
        gc.collect()
        self.stack.setCurrentWidget(widget)

    def _go_back(self):
        if self._nav_history:
            current = self.stack.currentWidget()
            prev = self._nav_history.pop()
            self.stack.setCurrentWidget(prev)
            if current not in (self.dashboard, self.viewer_3d, self.or_icu_mode):
                self.stack.removeWidget(current)
                current.deleteLater()
            import gc
            gc.collect()
        self.back_btn.setVisible(len(self._nav_history) > 0)

    def _push_screen(self, widget):
        self._nav_history.append(self.stack.currentWidget())
        self.back_btn.setVisible(True)
        self.stack.addWidget(widget)
        self.stack.setCurrentWidget(widget)

    def _toggle_air_mouse(self):
        """Nav-bar button: flip current state."""
        self._set_air_mouse(not self._air_mouse_on)

    def _set_air_mouse(self, enabled: bool):
        """Set air mouse to an explicit state (used by gesture 👍/👎 and the nav button)."""
        if self._air_mouse_on == enabled:
            return  # already in the requested state — no-op
        self._air_mouse_on = enabled
        signal_bus.air_mouse_toggle.emit(enabled)
        if enabled:
            self.air_mouse_btn.setText("Air Mouse: ON")
            self.air_mouse_btn.setStyleSheet(
                "QPushButton { color: #00e5ff; border: 1px solid #00e5ff; "
                "padding: 4px 10px; font-weight: bold; }"
            )
        else:
            self.air_mouse_btn.setText("Air Mouse: OFF")
            self.air_mouse_btn.setStyleSheet(
                "QPushButton { color: #888; border: 1px solid #555; padding: 4px 10px; }"
            )

    def _toggle_voice(self):
        """
        Enable or disable voice recognition.
        Uses the same visual style as Air Mouse.
        """
        self._voice_on = not self._voice_on

        self.voice_worker.set_armed(self._voice_on)

        if self._voice_on:
            self.voice_btn.setText("Voice: ON")

            self.voice_btn.setStyleSheet(
                "QPushButton { "
                "color: #00e5ff; "
                "border: 1px solid #00e5ff; "
                "padding: 4px 10px; "
                "font-weight: bold; "
                "}"
            )

            print(
                "[MainWindow] Voice recognition enabled.",
                flush=True,
            )

        else:
            self.voice_btn.setText("Voice: OFF")

            self.voice_btn.setStyleSheet(
                "QPushButton { "
                "color: #888; "
                "border: 1px solid #555; "
                "padding: 4px 10px; "
                "}"
            )

            print(
                "[MainWindow] Voice recognition disabled.",
                flush=True,
            )
    
    def _handle_voice_command(self, phrase: str):
        """Forward recognized voice commands to the 3D viewer and confirm on screen.

        Touchless UX needs feedback: without it the surgeon can't tell whether a
        command was heard, misheard, or ignored.
        """
        cmd = " ".join(phrase.lower().strip().split())
        if "select annotation" in cmd:
            if hasattr(self, "annotation_cf_manager") and self.annotation_cf_manager:
                annots = list(self.annotation_cf_manager._annotations.values())
                if annots:
                    self._on_icu_select_annotation(annots[0].id)
        elif "carry annotation forward" in cmd or "carry forward" in cmd:
            if hasattr(self, "annotation_cf_manager") and self.annotation_cf_manager:
                tgt = self.annotation_cf_manager.target_scan_id
                if tgt:
                    self._on_icu_carry_forward(tgt)
        elif "view annotation source" in cmd:
            if hasattr(self, "annotation_cf_manager") and self.annotation_cf_manager and self.annotation_cf_manager.selected_annotation_id:
                self._on_icu_view_annotation_source(self.annotation_cf_manager.selected_annotation_id)
        elif "view annotation target" in cmd:
            if hasattr(self, "annotation_cf_manager") and self.annotation_cf_manager and self.annotation_cf_manager.selected_annotation_id:
                self._on_icu_view_annotation_target(self.annotation_cf_manager.selected_annotation_id)
        elif "clear annotation" in cmd:
            self._on_icu_clear_annotation_selection()
        elif "review differences" in cmd or "show difference map" in cmd:
            self._on_icu_review_differences()
        elif "show current" in cmd:
            self._on_icu_show_current()
        elif "show previous" in cmd:
            self._on_icu_show_previous()
        elif "clear difference" in cmd:
            self._on_icu_clear_difference()
        elif "mark device" in cmd:
            dtype = "Endotracheal Tube"
            if hasattr(self.or_icu_mode, "device_markers_card") and self.or_icu_mode.device_markers_card:
                dtype = self.or_icu_mode.device_markers_card.combo_device_type.currentText()
            self._on_icu_mark_device(dtype, dtype)
        elif "show device markers" in cmd:
            self._open_or_icu()
        elif "view device marker" in cmd:
            if hasattr(self, "device_marker_manager") and self.device_marker_manager:
                sel_id = self.device_marker_manager.selected_marker_id
                if not sel_id:
                    markers = self.device_marker_manager.get_markers_for_active_scan()
                    if markers:
                        sel_id = markers[0].id
                if sel_id:
                    self._on_icu_view_device_marker(sel_id)
        elif "clear device marker" in cmd:
            self._on_icu_clear_device_marker_selection()
        elif "generate quick handoff" in cmd or "generate handoff" in cmd:
            self._on_icu_generate_handoff()
        elif "copy handoff" in cmd or "copy quick handoff" in cmd:
            self._on_icu_copy_handoff()
        elif "view handoff" in cmd or "view quick handoff" in cmd:
            self._on_icu_view_handoff()
        elif "clear handoff" in cmd or "clear quick handoff" in cmd:
            self._on_icu_clear_handoff()

        self.viewer_3d.handle_voice_command(phrase)
        self.flash_status(f"Voice: {phrase}")

    def flash_status(self, message: str, msec: int = 1800):
        """Briefly show a confirmation banner over the nav bar."""
        if not hasattr(self, "status_toast"):
            return
        self.status_toast.setText(message)
        self.status_toast.setVisible(True)
        self._toast_timer.start(msec)

    def _audit_view(self, patient: dict, action: str):
        """Record access + stamp last-viewed, for real DB patients only.
        Local/imported cards use synthetic MRNs that aren't rows in patients."""
        try:
            if not patient.get("_is_local"):
                touch_patient(patient["mrn"])
            log_action(action, patient.get("mrn"), patient.get("name", ""))
        except Exception as exc:
            print(f"[main] audit/touch failed: {exc}")

    def export_case_report(self):
        """One-click PDF case summary for the patient currently in the viewer."""
        if hasattr(self, "viewer_3d") and self.viewer_3d is not None and hasattr(self.viewer_3d, "export_case_report"):
            try:
                path = self.viewer_3d.export_case_report()
                if path:
                    self.flash_status(f"Report saved: {path}")
                    return
            except Exception as exc:
                print(f"[main] viewer_3d.export_case_report failed, falling back: {exc}")

        patient = getattr(self.viewer_3d, "current_patient", None) or getattr(self, "_last_patient", None)
        if not patient:
            self.flash_status("No patient loaded to export")
            return
        scan = getattr(self.viewer_3d, "current_scan", None)

        measurements = []
        try:
            mpr = getattr(self.viewer_3d, "mpr_view", None)
            if mpr is not None:
                measurements = mpr.get_measurement_summary()
        except Exception as exc:
            print(f"[main] could not collect measurements: {exc}")

        try:
            if patient.get("_is_local"):
                notes = []
            else:
                notes = get_notes_for_patient(
                    patient["mrn"]
                )
        except Exception:
            notes = []

        shot = ""
        try:
            shot = self.viewer_3d.save_screenshot()
        except Exception:
            pass

        record = None
        try:
            if not patient.get("_is_local"):
                record = get_patient(
                    patient["mrn"]
                )
        except Exception:
            pass

        try:
            path = build_case_report(
                patient=record or patient,
                scan=scan,
                measurements=measurements,
                notes=notes,
                screenshot_path=shot,
            )
            log_action("export_report", patient.get("mrn"), path)
            self.flash_status(f"Report saved: {path}")
        except Exception as exc:
            print(f"[main] report export failed: {exc}")
            self.flash_status("Report export failed - see console")

    def show_record(self, patient: dict):
        self._last_patient = patient
        self._audit_view(patient, "view_record")
        record_screen = PatientRecord(patient)
        record_screen.view_scans_clicked.connect(self.show_scans)
        self._push_screen(record_screen)

    def show_scans(self, patient: dict):
        self._last_patient = patient
        self._audit_view(patient, "view_scans")
        # View in 2D goes straight to MPR slices!
        from database import get_scans_for_ui
        scans = get_scans_for_ui(patient["mrn"])
        scan = scans[0] if scans else patient.get("_scan", {})
        
        self.viewer_3d.load_scan(patient, scan)
        self.viewer_3d.btn_3d_mode.setChecked(False)
        self.viewer_3d.btn_mpr_mode.setChecked(True)
        self.viewer_3d._switch_view_mode(1)
        self._go_root(self.viewer_3d)

    def show_3d_viewer(self, patient: dict, scan: dict):
        self._last_patient = patient
        self._audit_view(patient, "view_3d")
        self.viewer_3d.load_scan(patient, scan)
        self._push_screen(self.viewer_3d)

    def show_3d_direct(self, patient: dict, scan: dict):
        """Called by local-scan cards on the dashboard — skip gallery, go straight to 3D."""
        self._last_patient = patient
        self._audit_view(patient, "view_3d_direct")
        self.viewer_3d.load_scan(patient, scan)
        self.viewer_3d.btn_3d_mode.setChecked(True)
        self.viewer_3d.btn_mpr_mode.setChecked(False)
        self.viewer_3d._switch_view_mode(0)
        self._go_root(self.viewer_3d)

    def _open_or_icu(self):
        """Navigate to OR/ICU Mode, passing the active patient and scan."""
        patient = getattr(self.viewer_3d, "current_patient", None) or getattr(self, "_last_patient", None)
        scan = getattr(self.viewer_3d, "current_scan", None)
        self.or_icu_mode.set_surgical_plan(self.viewer_3d.surgical_plan)
        if patient:
            self._last_patient = patient
            self._audit_view(patient, "view_or_icu")
            self.or_icu_mode.set_patient_and_scan(patient, scan)
            self._sync_icu_measurement_tracker(patient, scan)
            self._sync_icu_annotation_cf(patient, scan)
            self._sync_icu_what_changed(patient, scan)
            self._sync_icu_device_markers(patient, scan)
            self._on_icu_clear_handoff()
        self._go_root(self.or_icu_mode)

    def _on_icu_or_open_in_viewer(self, patient: dict, scan: dict):
        """Return from ICU/OR to the 3D Viewer with the active patient and scan."""
        if patient and scan:
            self.show_3d_direct(patient, scan)
        elif patient:
            self.show_scans(patient)
        else:
            self._go_root(self.viewer_3d)

    def _on_icu_or_set_entry(self):
        """Switches to Viewer3D in SET_ENTRY picking mode."""
        self._go_root(self.viewer_3d)
        self.viewer_3d.set_planning_picking_mode("SET_ENTRY")
        self.flash_status("Click 3D surface or 2D slice to set ENTRY")

    def _on_icu_or_set_target(self):
        """Switches to Viewer3D in SET_TARGET picking mode."""
        self._go_root(self.viewer_3d)
        self.viewer_3d.set_planning_picking_mode("SET_TARGET")
        self.flash_status("Click 3D surface or 2D slice to set TARGET")

    def _on_icu_or_view_entry(self):
        """Switches to Viewer3D and navigates to ENTRY."""
        self._go_root(self.viewer_3d)
        self.viewer_3d.view_entry_point()

    def _on_icu_or_view_target(self):
        """Switches to Viewer3D and navigates to TARGET."""
        self._go_root(self.viewer_3d)
        self.viewer_3d.view_target_point()

    def _on_icu_or_clear_entry(self):
        """Clears ENTRY landmark and updates UI cards."""
        self.viewer_3d.clear_entry_point()
        if hasattr(self.or_icu_mode, "entry_target_card") and self.or_icu_mode.entry_target_card:
            self.or_icu_mode.entry_target_card.update_landmarks(
                self.viewer_3d.surgical_plan.entry_point,
                self.viewer_3d.surgical_plan.target_point
            )
        if hasattr(self.or_icu_mode, "planned_route_card") and self.or_icu_mode.planned_route_card:
            self.or_icu_mode.planned_route_card.update_route(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "structures_to_avoid_card") and self.or_icu_mode.structures_to_avoid_card:
            self.or_icu_mode.structures_to_avoid_card.update_structures(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "surgical_corridor_card") and self.or_icu_mode.surgical_corridor_card:
            self.or_icu_mode.surgical_corridor_card.update_corridor(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "virtual_instrument_card") and self.or_icu_mode.virtual_instrument_card:
            self.or_icu_mode.virtual_instrument_card.update_instrument(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "live_deviation_card") and self.or_icu_mode.live_deviation_card:
            self.or_icu_mode.live_deviation_card.update_deviation(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "quick_views_card") and self.or_icu_mode.quick_views_card:
            self.or_icu_mode.quick_views_card.update_availability(self.viewer_3d.surgical_plan)

    def _on_icu_or_clear_target(self):
        """Clears TARGET landmark and updates UI cards."""
        self.viewer_3d.clear_target_point()
        if hasattr(self.or_icu_mode, "entry_target_card") and self.or_icu_mode.entry_target_card:
            self.or_icu_mode.entry_target_card.update_landmarks(
                self.viewer_3d.surgical_plan.entry_point,
                self.viewer_3d.surgical_plan.target_point
            )
        if hasattr(self.or_icu_mode, "planned_route_card") and self.or_icu_mode.planned_route_card:
            self.or_icu_mode.planned_route_card.update_route(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "structures_to_avoid_card") and self.or_icu_mode.structures_to_avoid_card:
            self.or_icu_mode.structures_to_avoid_card.update_structures(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "surgical_corridor_card") and self.or_icu_mode.surgical_corridor_card:
            self.or_icu_mode.surgical_corridor_card.update_corridor(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "virtual_instrument_card") and self.or_icu_mode.virtual_instrument_card:
            self.or_icu_mode.virtual_instrument_card.update_instrument(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "live_deviation_card") and self.or_icu_mode.live_deviation_card:
            self.or_icu_mode.live_deviation_card.update_deviation(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "quick_views_card") and self.or_icu_mode.quick_views_card:
            self.or_icu_mode.quick_views_card.update_availability(self.viewer_3d.surgical_plan)

    def _on_icu_or_clear_both(self):
        """Clears both ENTRY and TARGET landmarks and planned route."""
        self.viewer_3d.clear_both_planning_points()
        if hasattr(self.or_icu_mode, "entry_target_card") and self.or_icu_mode.entry_target_card:
            self.or_icu_mode.entry_target_card.update_landmarks(None, None)
        if hasattr(self.or_icu_mode, "planned_route_card") and self.or_icu_mode.planned_route_card:
            self.or_icu_mode.planned_route_card.update_route(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "structures_to_avoid_card") and self.or_icu_mode.structures_to_avoid_card:
            self.or_icu_mode.structures_to_avoid_card.update_structures(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "surgical_corridor_card") and self.or_icu_mode.surgical_corridor_card:
            self.or_icu_mode.surgical_corridor_card.update_corridor(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "virtual_instrument_card") and self.or_icu_mode.virtual_instrument_card:
            self.or_icu_mode.virtual_instrument_card.update_instrument(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "live_deviation_card") and self.or_icu_mode.live_deviation_card:
            self.or_icu_mode.live_deviation_card.update_deviation(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "quick_views_card") and self.or_icu_mode.quick_views_card:
            self.or_icu_mode.quick_views_card.update_availability(self.viewer_3d.surgical_plan)

    def _on_icu_or_view_route(self):
        """Switches to Viewer3D and navigates to Planned Route midpoint."""
        self._go_root(self.viewer_3d)
        self.viewer_3d.view_planned_route()

    def _on_icu_or_clear_route(self):
        """Clears planned route (and landmarks)."""
        self.viewer_3d.clear_planned_route()
        if hasattr(self.or_icu_mode, "entry_target_card") and self.or_icu_mode.entry_target_card:
            self.or_icu_mode.entry_target_card.update_landmarks(None, None)
        if hasattr(self.or_icu_mode, "planned_route_card") and self.or_icu_mode.planned_route_card:
            self.or_icu_mode.planned_route_card.update_route(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "structures_to_avoid_card") and self.or_icu_mode.structures_to_avoid_card:
            self.or_icu_mode.structures_to_avoid_card.update_structures(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "surgical_corridor_card") and self.or_icu_mode.surgical_corridor_card:
            self.or_icu_mode.surgical_corridor_card.update_corridor(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "virtual_instrument_card") and self.or_icu_mode.virtual_instrument_card:
            self.or_icu_mode.virtual_instrument_card.update_instrument(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "live_deviation_card") and self.or_icu_mode.live_deviation_card:
            self.or_icu_mode.live_deviation_card.update_deviation(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "quick_views_card") and self.or_icu_mode.quick_views_card:
            self.or_icu_mode.quick_views_card.update_availability(self.viewer_3d.surgical_plan)

    def _on_icu_or_add_structure(self):
        """Switches to Viewer3D and enters ADD_STRUCTURE picking mode."""
        self._go_root(self.viewer_3d)
        self.viewer_3d.set_planning_picking_mode("ADD_STRUCTURE")
        self.flash_status("Click 3D surface or 2D slice to mark structure to avoid")

    def _on_icu_or_view_structure(self, structure_id: str):
        """Switches to Viewer3D and navigates to structure physical position."""
        self._go_root(self.viewer_3d)
        self.viewer_3d.view_avoid_structure(structure_id)

    def _on_icu_or_remove_structure(self, structure_id: str):
        """Removes an individual structure to avoid and updates UI cards."""
        self.viewer_3d.remove_avoid_structure(structure_id)
        if hasattr(self.or_icu_mode, "structures_to_avoid_card") and self.or_icu_mode.structures_to_avoid_card:
            self.or_icu_mode.structures_to_avoid_card.update_structures(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "surgical_corridor_card") and self.or_icu_mode.surgical_corridor_card:
            self.or_icu_mode.surgical_corridor_card.update_corridor(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "virtual_instrument_card") and self.or_icu_mode.virtual_instrument_card:
            self.or_icu_mode.virtual_instrument_card.update_instrument(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "live_deviation_card") and self.or_icu_mode.live_deviation_card:
            self.or_icu_mode.live_deviation_card.update_deviation(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "quick_views_card") and self.or_icu_mode.quick_views_card:
            self.or_icu_mode.quick_views_card.update_availability(self.viewer_3d.surgical_plan)

    def _on_icu_or_clear_structures(self):
        """Removes all structures to avoid and updates UI cards."""
        self.viewer_3d.clear_avoid_structures()
        if hasattr(self.or_icu_mode, "structures_to_avoid_card") and self.or_icu_mode.structures_to_avoid_card:
            self.or_icu_mode.structures_to_avoid_card.update_structures(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "surgical_corridor_card") and self.or_icu_mode.surgical_corridor_card:
            self.or_icu_mode.surgical_corridor_card.update_corridor(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "virtual_instrument_card") and self.or_icu_mode.virtual_instrument_card:
            self.or_icu_mode.virtual_instrument_card.update_instrument(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "live_deviation_card") and self.or_icu_mode.live_deviation_card:
            self.or_icu_mode.live_deviation_card.update_deviation(self.viewer_3d.surgical_plan)
        if hasattr(self.or_icu_mode, "quick_views_card") and self.or_icu_mode.quick_views_card:
            self.or_icu_mode.quick_views_card.update_availability(self.viewer_3d.surgical_plan)

    def _on_icu_or_corridor_visibility_changed(self, visible: bool):
        """Updates 3D and 2D surgical corridor visibility."""
        self.viewer_3d.set_surgical_corridor_enabled(visible)

    def _on_icu_or_corridor_radius_changed(self, radius_mm: float):
        """Updates 3D and 2D surgical corridor radius."""
        self.viewer_3d.set_surgical_corridor_radius(radius_mm)

    def _on_icu_or_instrument_visibility_changed(self, visible: bool):
        """Updates 3D and 2D virtual instrument visibility."""
        self.viewer_3d.set_virtual_instrument_visible(visible)
        if hasattr(self.or_icu_mode, "quick_views_card") and self.or_icu_mode.quick_views_card:
            self.or_icu_mode.quick_views_card.update_availability(self.viewer_3d.surgical_plan)

    def _on_icu_or_instrument_depth_changed(self, depth_mm: float):
        """Updates 3D and 2D virtual instrument insertion depth."""
        self.viewer_3d.set_virtual_instrument_depth(depth_mm)

    def _on_icu_or_instrument_diameter_changed(self, diameter_mm: float):
        """Updates 3D and 2D virtual instrument diameter."""
        self.viewer_3d.set_virtual_instrument_diameter(diameter_mm)

    def _on_icu_or_view_instrument(self):
        """Switches to Viewer3D and centers camera/slices on virtual instrument tip."""
        self._go_root(self.viewer_3d)
        self.viewer_3d.view_virtual_instrument()

    def _on_icu_or_deviation_visibility_changed(self, visible: bool):
        """Updates 3D and 2D live deviation visibility."""
        self.viewer_3d.set_live_deviation_visible(visible)
        if hasattr(self.or_icu_mode, "quick_views_card") and self.or_icu_mode.quick_views_card:
            self.or_icu_mode.quick_views_card.update_availability(self.viewer_3d.surgical_plan)

    def _on_icu_or_deviation_offsets_changed(self, dx: float, dy: float, dz: float):
        """Updates 3D and 2D live deviation offsets in mm."""
        self.viewer_3d.set_live_deviation_offsets(dx, dy, dz)
        if hasattr(self.or_icu_mode, "live_deviation_card") and self.or_icu_mode.live_deviation_card:
            self.or_icu_mode.live_deviation_card.update_deviation(self.viewer_3d.surgical_plan)

    def _on_icu_or_deviation_angles_changed(self, yaw_deg: float, pitch_deg: float):
        """Updates 3D and 2D live deviation angles in degrees."""
        self.viewer_3d.set_live_deviation_angles(yaw_deg, pitch_deg)
        if hasattr(self.or_icu_mode, "live_deviation_card") and self.or_icu_mode.live_deviation_card:
            self.or_icu_mode.live_deviation_card.update_deviation(self.viewer_3d.surgical_plan)

    def _on_icu_or_reset_deviation(self):
        """Resets simulated current instrument deviation to the planned trajectory."""
        self.viewer_3d.reset_live_deviation_to_planned()
        if hasattr(self.or_icu_mode, "live_deviation_card") and self.or_icu_mode.live_deviation_card:
            self.or_icu_mode.live_deviation_card.update_deviation(self.viewer_3d.surgical_plan)

    def _on_icu_or_save_plan(self):
        """Saves current surgical plan to active version or creates a new version."""
        scan = self.or_icu_mode.current_scan or getattr(self.viewer_3d, "current_scan", {})
        scan_id = (scan.get("file_path") if scan else "") or str(scan.get("id", "") if scan else "")
        if not scan_id and hasattr(self.viewer_3d, "surgical_plan") and self.viewer_3d.surgical_plan.scan_id:
            scan_id = self.viewer_3d.surgical_plan.scan_id
        if not scan_id:
            self.flash_status("No active scan selected")
            return

        session = self.viewer_3d.surgical_plan
        patient = self.or_icu_mode.current_patient or getattr(self.viewer_3d, "current_patient", {})
        mrn = patient.get("mrn")

        # If there is already an active saved version, update it
        if session.active_version_id:
            existing_v = get_surgical_plan_version(session.active_version_id)
            name = existing_v["name"] if existing_v else "Plan 1"
            snapshot = session.create_snapshot()
            v = save_surgical_plan_version(scan_id, name, snapshot, version_id=session.active_version_id, patient_mrn=mrn)
            session.mark_saved(v["id"])
            if hasattr(self.or_icu_mode, "plan_versions_card") and self.or_icu_mode.plan_versions_card:
                self.or_icu_mode.plan_versions_card.update_versions(session, scan_id)
            self.flash_status(f"Updated {name}")
        else:
            self._on_icu_or_save_as_new_plan()

    def _on_icu_or_save_as_new_plan(self):
        """Saves current surgical plan as a new named version."""
        scan = self.or_icu_mode.current_scan or getattr(self.viewer_3d, "current_scan", {})
        scan_id = (scan.get("file_path") if scan else "") or str(scan.get("id", "") if scan else "")
        if not scan_id and hasattr(self.viewer_3d, "surgical_plan") and self.viewer_3d.surgical_plan.scan_id:
            scan_id = self.viewer_3d.surgical_plan.scan_id
        if not scan_id:
            self.flash_status("No active scan selected")
            return

        session = self.viewer_3d.surgical_plan
        patient = self.or_icu_mode.current_patient or getattr(self.viewer_3d, "current_patient", {})
        mrn = patient.get("mrn")

        existing_versions = get_surgical_plan_versions(scan_id)
        default_name = f"Plan {len(existing_versions) + 1}"

        name = default_name
        try:
            from PyQt6.QtWidgets import QInputDialog
            text, ok = QInputDialog.getText(self, "Save Plan As New Version", "Plan name:", text=default_name)
            if ok and text.strip():
                name = text.strip()
            elif not ok:
                return  # Cancelled by user
        except Exception:
            name = default_name

        snapshot = session.create_snapshot()
        v = save_surgical_plan_version(scan_id, name, snapshot, patient_mrn=mrn)
        session.mark_saved(v["id"])
        if hasattr(self.or_icu_mode, "plan_versions_card") and self.or_icu_mode.plan_versions_card:
            self.or_icu_mode.plan_versions_card.update_versions(session, scan_id)
        self.flash_status(f"Saved {name}")

    def _on_icu_or_restore_plan(self, version_id: str):
        """Restores a saved plan version after verifying scan safety."""
        v = get_surgical_plan_version(version_id)
        if not v:
            self.flash_status("Plan version not found")
            return

        # Scan safety verification
        scan = self.or_icu_mode.current_scan or getattr(self.viewer_3d, "current_scan", {})
        current_scan_id = (scan.get("file_path") if scan else "") or str(scan.get("id", "") if scan else "")
        if not current_scan_id and hasattr(self.viewer_3d, "surgical_plan") and self.viewer_3d.surgical_plan.scan_id:
            current_scan_id = self.viewer_3d.surgical_plan.scan_id

        if str(v.get("scan_id", "")) != str(current_scan_id):
            self.flash_status("Plan belongs to a different scan")
            return

        # Deterministic restoration via viewer_3d
        success = self.viewer_3d.restore_surgical_plan_snapshot(v["snapshot"])
        if success:
            self.viewer_3d.surgical_plan.mark_saved(version_id)
            # Refresh all OR cards
            if hasattr(self.or_icu_mode, "entry_target_card") and self.or_icu_mode.entry_target_card:
                self.or_icu_mode.entry_target_card.update_landmarks(
                    self.viewer_3d.surgical_plan.entry_point,
                    self.viewer_3d.surgical_plan.target_point
                )
            if hasattr(self.or_icu_mode, "planned_route_card") and self.or_icu_mode.planned_route_card:
                self.or_icu_mode.planned_route_card.update_route(self.viewer_3d.surgical_plan)
            if hasattr(self.or_icu_mode, "structures_to_avoid_card") and self.or_icu_mode.structures_to_avoid_card:
                self.or_icu_mode.structures_to_avoid_card.update_structures(self.viewer_3d.surgical_plan)
            if hasattr(self.or_icu_mode, "surgical_corridor_card") and self.or_icu_mode.surgical_corridor_card:
                self.or_icu_mode.surgical_corridor_card.update_corridor(self.viewer_3d.surgical_plan)
            if hasattr(self.or_icu_mode, "virtual_instrument_card") and self.or_icu_mode.virtual_instrument_card:
                self.or_icu_mode.virtual_instrument_card.update_instrument(self.viewer_3d.surgical_plan)
            if hasattr(self.or_icu_mode, "live_deviation_card") and self.or_icu_mode.live_deviation_card:
                self.or_icu_mode.live_deviation_card.update_deviation(self.viewer_3d.surgical_plan)
            if hasattr(self.or_icu_mode, "plan_versions_card") and self.or_icu_mode.plan_versions_card:
                self.or_icu_mode.plan_versions_card.update_versions(self.viewer_3d.surgical_plan, current_scan_id)
            if hasattr(self.or_icu_mode, "quick_views_card") and self.or_icu_mode.quick_views_card:
                self.or_icu_mode.quick_views_card.update_availability(self.viewer_3d.surgical_plan)
            self.flash_status(f"Restored {v.get('name', 'plan')}")
        else:
            self.flash_status("Failed to restore plan")

    def _on_icu_or_delete_plan(self, version_id: str):
        """Deletes a saved plan version from database."""
        deleted = delete_surgical_plan_version(version_id)
        if deleted:
            if self.viewer_3d.surgical_plan.active_version_id == version_id:
                self.viewer_3d.surgical_plan.active_version_id = None
            scan = self.or_icu_mode.current_scan or getattr(self.viewer_3d, "current_scan", {})
            scan_id = (scan.get("file_path") if scan else "") or str(scan.get("id", "") if scan else "")
            if hasattr(self.or_icu_mode, "plan_versions_card") and self.or_icu_mode.plan_versions_card:
                self.or_icu_mode.plan_versions_card.update_versions(self.viewer_3d.surgical_plan, scan_id)
            self.flash_status("Deleted plan version")

    def _on_icu_or_rename_plan(self, version_id: str, new_name: str):
        """Renames a saved plan version in database."""
        renamed = rename_surgical_plan_version(version_id, new_name)
        if renamed:
            scan = self.or_icu_mode.current_scan or getattr(self.viewer_3d, "current_scan", {})
            scan_id = (scan.get("file_path") if scan else "") or str(scan.get("id", "") if scan else "")
            if hasattr(self.or_icu_mode, "plan_versions_card") and self.or_icu_mode.plan_versions_card:
                self.or_icu_mode.plan_versions_card.update_versions(self.viewer_3d.surgical_plan, scan_id)
            self.flash_status(f"Renamed to {new_name}")

    def _on_icu_or_show_plan_versions(self):
        """Navigates to OR mode and focuses plan versions."""
        self._open_or_icu()
        if hasattr(self.or_icu_mode, "set_mode"):
            self.or_icu_mode.set_mode("OR")

    def _on_icu_or_start_comparison(self, mode: str):
        """Initiates Before vs After comparison on Viewer3D."""
        if hasattr(self.or_icu_mode, "before_after_card") and self.or_icu_mode.before_after_card:
            card = self.or_icu_mode.before_after_card
            if card.before_scan and card.after_scan:
                success = self.viewer_3d.start_before_after_comparison(card.before_scan, card.after_scan, mode)
                if not success and card.lbl_error.text():
                    self.flash_status(card.lbl_error.text())
                else:
                    self.flash_status(f"Before vs After comparison active [{mode}]")

    def _on_icu_or_exit_comparison(self):
        """Exits Before vs After comparison on Viewer3D."""
        self.viewer_3d.exit_before_after_comparison()
        self.flash_status("Comparison exited")

    def _on_icu_or_toggle_comparison_view(self, view: str):
        """Toggles between Before and After in comparison mode."""
        self.viewer_3d.toggle_before_after_view(view)
        self.flash_status(f"Comparison view: {view}")

    def _on_icu_or_comparison_mode_changed(self, mode: str):
        """Updates comparison display mode on Viewer3D."""
        self.viewer_3d.set_comparison_mode(mode)
        self.flash_status(f"Comparison mode: {mode}")

    def _on_icu_or_overlay_opacity_changed(self, opacity: float):
        """Updates comparison overlay opacity on Viewer3D."""
        self.viewer_3d.set_comparison_overlay_opacity(opacity)

    def _on_icu_or_quick_view(self, preset: str):
        """Switches to Viewer3D and applies the quick surgical view preset."""
        self._go_root(self.viewer_3d)
        res = self.viewer_3d.apply_quick_surgical_view(preset)
        if res and hasattr(self.or_icu_mode, "quick_views_card") and self.or_icu_mode.quick_views_card:
            self.or_icu_mode.quick_views_card.set_active_preset(preset)

    def _on_viewer_quick_view_applied(self, preset: str):
        """Synchronizes the active preset indicator on the Quick Surgical Views card."""
        if hasattr(self.or_icu_mode, "quick_views_card") and self.or_icu_mode.quick_views_card:
            self.or_icu_mode.quick_views_card.set_active_preset(preset)

    def _on_icu_view_previous(self, prev_scan: dict, curr_scan: dict):
        """Switches to Viewer3D and displays the immediately previous scan for this patient."""
        self._go_root(self.viewer_3d)
        if prev_scan and curr_scan:
            if not self.viewer_3d.comparison.is_active:
                self.viewer_3d.start_before_after_comparison(prev_scan, curr_scan, "TOGGLE")
            self.viewer_3d.toggle_before_after_view("BEFORE")
            if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
                self.or_icu_mode.current_previous_card.set_active_view("PREVIOUS")
            patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
            self._sync_icu_device_markers(patient, prev_scan)
            self._mark_icu_handoff_stale()
            self.flash_status("Viewing Previous Scan")

    def _on_icu_view_current(self):
        """Switches to Viewer3D and displays the current active scan."""
        self._go_root(self.viewer_3d)
        if self.viewer_3d.comparison.is_active:
            self.viewer_3d.toggle_before_after_view("AFTER")
        if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
            self.or_icu_mode.current_previous_card.set_active_view("CURRENT")
        patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
        curr_scan = getattr(self.or_icu_mode, "current_scan", None) or getattr(self.viewer_3d, "current_scan", {}) or {}
        self._sync_icu_device_markers(patient, curr_scan)
        self._mark_icu_handoff_stale()
        self.flash_status("Viewing Current Scan")

    def _on_icu_toggle(self, prev_scan: dict, curr_scan: dict):
        """Toggles between Current and Previous scan on Viewer3D."""
        self._go_root(self.viewer_3d)
        if prev_scan and curr_scan:
            if not self.viewer_3d.comparison.is_active:
                self.viewer_3d.start_before_after_comparison(prev_scan, curr_scan, "TOGGLE")
                self.viewer_3d.toggle_before_after_view("BEFORE")
                new_view = "PREVIOUS"
            else:
                self.viewer_3d.toggle_before_after_view()
                new_view = "PREVIOUS" if self.viewer_3d.comparison.current_view == "BEFORE" else "CURRENT"
            if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
                self.or_icu_mode.current_previous_card.set_active_view(new_view)
            patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
            active_scan = prev_scan if new_view == "PREVIOUS" else curr_scan
            self._sync_icu_device_markers(patient, active_scan)
            self._mark_icu_handoff_stale()
            self.flash_status(f"Toggled to {new_view} Scan")

    def _on_icu_exit_comparison(self):
        """Exits comparison mode and restores normal single-scan display."""
        self.viewer_3d.exit_before_after_comparison()
        if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
            self.or_icu_mode.current_previous_card.set_active_view("CURRENT")
        patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
        curr_scan = getattr(self.or_icu_mode, "current_scan", None) or getattr(self.viewer_3d, "current_scan", {}) or {}
        self._sync_icu_device_markers(patient, curr_scan)
        self._mark_icu_handoff_stale()
        self.flash_status("Comparison exited")

    # ── ICU Feature 2: Same-Location Review Handlers ─────────────────────────
    def _on_icu_mark_same_location(self):
        """Activates 2D slice picking for same-location review and ensures 2D viewer is visible."""
        self._go_root(self.viewer_3d)
        if hasattr(self.viewer_3d, "set_2d_panel_visible"):
            self.viewer_3d.set_2d_panel_visible(True)
        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.set_same_location_picking_mode(True)
            self.flash_status("Click on 2D slice to mark same location")

    def _on_same_location_selected(self, x_mm: float, y_mm: float, z_mm: float):
        """Processes 2D slice click: stores canonical physical location and updates all viewers."""
        current_geom = None
        previous_geom = None

        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer and self.viewer_3d.slice_viewer.vol_data is not None:
            sv = self.viewer_3d.slice_viewer
            current_geom = {
                "dims": sv.vol_data.shape,
                "spacing": sv.pixel_spacing,
                "thickness": sv.slice_thickness,
                "origin": (0.0, 0.0, 0.0)
            }

        comp = getattr(self.viewer_3d, "comparison", None)
        if comp and comp.before_volume is not None:
            previous_geom = {
                "dims": comp.before_volume.shape,
                "spacing": comp.before_pixel_spacing or (1.0, 1.0),
                "thickness": comp.before_slice_thickness or 1.0,
                "origin": (0.0, 0.0, 0.0)
            }

        curr_scan = getattr(self.or_icu_mode, "current_scan", None) or getattr(self.viewer_3d, "current_scan", {}) or {}
        curr_patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
        curr_id = str(curr_scan.get("id", "") or curr_scan.get("file_path", ""))
        prev_id = str(comp.before_scan.get("id", "") or comp.before_scan.get("file_path", "")) if comp and comp.before_scan else ""
        mrn = str(curr_patient.get("mrn", ""))

        self.icu_same_location.set_location(
            x_mm, y_mm, z_mm,
            current_geom=current_geom,
            previous_geom=previous_geom,
            current_scan_id=curr_id,
            previous_scan_id=prev_id,
            patient_mrn=mrn
        )

        # Update card
        if hasattr(self.or_icu_mode, "same_location_card") and self.or_icu_mode.same_location_card:
            self.or_icu_mode.same_location_card.update_location(self.icu_same_location)

        # Update 2D viewer
        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            loc_data = {
                "is_active": True,
                "x": x_mm, "y": y_mm, "z": z_mm,
                "current_geom": current_geom,
                "before_geom": previous_geom
            }
            self.viewer_3d.slice_viewer.set_same_location(loc_data)

        # Update MPR
        if hasattr(self.viewer_3d, "mpr_view") and self.viewer_3d.mpr_view:
            self.viewer_3d.mpr_view.set_same_location(x_mm, y_mm, z_mm)

        # Update 3D
        self.viewer_3d.set_same_location_marker(x_mm, y_mm, z_mm)
        self._mark_icu_handoff_stale()

        self.flash_status(f"Marked Location ({x_mm:.1f}, {y_mm:.1f}, {z_mm:.1f}) mm")

    def _on_icu_view_same_location_current(self):
        """Switches to Current scan and centers 2D and MPR on the same location."""
        self._on_icu_view_current()
        coords = self.icu_same_location.get_physical_coordinates()
        if coords:
            x, y, z = coords
            if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
                self.viewer_3d.slice_viewer.navigate_to_physical_point(x, y, z)
            if hasattr(self.viewer_3d, "mpr_view") and self.viewer_3d.mpr_view:
                self.viewer_3d.mpr_view.snap_to_volume_point(x, y, z)

    def _on_icu_view_same_location_previous(self):
        """Switches to Previous scan and centers 2D and MPR on the corresponding location."""
        if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
            prev_scan = self.or_icu_mode.current_previous_card.previous_scan
            curr_scan = getattr(self.or_icu_mode, "current_scan", None) or getattr(self.viewer_3d, "current_scan", {}) or {}
            if prev_scan:
                self._on_icu_view_previous(prev_scan, curr_scan)
        coords = self.icu_same_location.get_physical_coordinates()
        if coords:
            x, y, z = coords
            if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
                self.viewer_3d.slice_viewer.navigate_to_physical_point(x, y, z)
            if hasattr(self.viewer_3d, "mpr_view") and self.viewer_3d.mpr_view:
                self.viewer_3d.mpr_view.snap_to_volume_point(x, y, z)

    def _on_icu_clear_same_location(self):
        """Clears same-location state from model, card, and viewers."""
        self.icu_same_location.clear()
        if hasattr(self.or_icu_mode, "same_location_card") and self.or_icu_mode.same_location_card:
            self.or_icu_mode.same_location_card.clear_location()
        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.clear_same_location()
        if hasattr(self.viewer_3d, "mpr_view") and self.viewer_3d.mpr_view:
            self.viewer_3d.mpr_view.clear_same_location()
        self.viewer_3d.clear_same_location_marker()
        self._mark_icu_handoff_stale()
        self.flash_status("Same Location cleared")

    # ── ICU Feature 3: Measurement Tracking Handlers ─────────────────────────

    def _sync_icu_measurement_tracker(self, patient: dict, scan: dict):
        """Loads and synchronizes tracked measurements for the active patient and scans."""
        mrn = str(patient.get("mrn", "")) if patient else ""
        if not mrn:
            self.measurement_tracker.clear()
            self._update_icu_measurement_tracking_ui()
            return

        self.measurement_tracker.set_patient(mrn)

        curr_id = str(scan.get("id", "") or scan.get("file_path", "")) if scan else ""

        prev_scan = None
        if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
            prev_scan = self.or_icu_mode.current_previous_card.previous_scan
        if not prev_scan and mrn:
            prev_scan = get_previous_scan_for_patient(mrn, scan)
        prev_id = str(prev_scan.get("id", "") or prev_scan.get("file_path", "")) if prev_scan else ""

        self.measurement_tracker.set_scans(curr_id, prev_id)

        if curr_id:
            curr_db_list = get_tracked_measurements_for_scan(curr_id, mrn)
            for d in curr_db_list:
                m = TrackedMeasurement.from_dict(d)
                self.measurement_tracker.add_measurement(m)

        if prev_id:
            prev_db_list = get_tracked_measurements_for_scan(prev_id, mrn)
            for d in prev_db_list:
                m = TrackedMeasurement.from_dict(d)
                self.measurement_tracker.add_measurement(m)

        self._update_icu_measurement_tracking_ui()

    def _on_icu_track_measurement(self):
        """Extracts the active/latest caliper measurement from 3D or 2D and tracks it."""
        curr_patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
        mrn = str(curr_patient.get("mrn", ""))
        if not mrn:
            self.flash_status("No active patient to track measurement")
            return

        # Determine target scan based on comparison state
        comp = getattr(self.viewer_3d, "comparison", None)
        if comp and comp.is_active and comp.current_view == "BEFORE" and comp.before_scan:
            target_scan = comp.before_scan
            source_tag = "Previous"
        else:
            target_scan = getattr(self.or_icu_mode, "current_scan", None) or getattr(self.viewer_3d, "current_scan", {}) or {}
            source_tag = "Current"

        scan_id = str(target_scan.get("id", "") or target_scan.get("file_path", ""))
        if not scan_id:
            self.flash_status("No active scan to track measurement")
            return

        # Find latest measurement from 3D, 2D, or MPR
        meas_data = None
        # 1. Check 3D measurements
        m3d = self.viewer_3d.get_measurements_3d()
        if m3d:
            latest_3d = m3d[-1]
            dist = float(latest_3d.get("distance_mm", 0.0))
            meas_data = {
                "source": "3D",
                "value_mm": dist,
                "metadata": {"source": "3D", "pt1": latest_3d.get("pt1"), "pt2": latest_3d.get("pt2")}
            }
        # 2. Check 2D measurements if no 3D
        if not meas_data and hasattr(self.viewer_3d, "panel_2d") and self.viewer_3d.panel_2d:
            m2d = self.viewer_3d.panel_2d.get_measurements()
            if m2d:
                latest_2d = m2d[-1]
                dist = float(latest_2d.distance_mm)
                orient = getattr(latest_2d, "orientation", "2D").capitalize()
                meas_data = {
                    "source": f"2D {orient}",
                    "value_mm": dist,
                    "metadata": {
                        "source": f"2D {orient}",
                        "slice_idx": getattr(latest_2d, "slice_idx", 0),
                        "physical_start": getattr(latest_2d, "physical_start", (0, 0, 0)),
                        "physical_end": getattr(latest_2d, "physical_end", (0, 0, 0)),
                    }
                }
        # 3. Check MPR if no 3D or 2D
        if not meas_data and hasattr(self.viewer_3d, "mpr_view") and self.viewer_3d.mpr_view:
            for plane_name, widget in [("Axial", getattr(self.viewer_3d.mpr_view, "w_axial", None)),
                                       ("Coronal", getattr(self.viewer_3d.mpr_view, "w_coronal", None)),
                                       ("Sagittal", getattr(self.viewer_3d.mpr_view, "w_sagittal", None))]:
                if widget and hasattr(widget, "get_measurements"):
                    m_list = widget.get_measurements()
                    if m_list:
                        latest_mpr = m_list[-1]
                        dist = float(latest_mpr.distance_mm)
                        meas_data = {
                            "source": f"MPR {plane_name}",
                            "value_mm": dist,
                            "metadata": {"source": f"MPR {plane_name}"}
                        }
                        break

        if not meas_data:
            self.flash_status("No active caliper measurement to track")
            return

        import uuid
        meas_id = f"meas_{uuid.uuid4().hex[:8]}"
        value_mm = round(meas_data["value_mm"], 2)
        label = f"[{meas_data['source']}]"

        tracked = TrackedMeasurement(
            id=meas_id,
            scan_id=scan_id,
            patient_mrn=mrn,
            label=label,
            value_mm=value_mm,
            unit="mm",
            metadata=meas_data.get("metadata", {})
        )

        save_tracked_measurement(
            measurement_id=meas_id,
            scan_id=scan_id,
            patient_mrn=mrn,
            label=label,
            value_mm=value_mm,
            unit="mm",
            metadata=meas_data.get("metadata", {})
        )

        self.measurement_tracker.add_measurement(tracked)
        self._update_icu_measurement_tracking_ui()
        self._mark_icu_handoff_stale()
        self.flash_status(f"Tracked measurement: {value_mm:.1f} mm ({source_tag})")

    def _on_icu_delete_tracked_measurement(self, measurement_id: str):
        """Deletes a tracked measurement from database and tracker."""
        curr_patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
        mrn = str(curr_patient.get("mrn", ""))
        delete_tracked_measurement(measurement_id, mrn if mrn else None)
        self.measurement_tracker.remove_measurement(measurement_id)
        self._update_icu_measurement_tracking_ui()
        self._mark_icu_handoff_stale()
        self.flash_status("Tracked measurement removed")

    def _on_icu_measurement_selection_changed(self, current_id: str, prev_id: str):
        """Updates selection in MeasurementTracker and updates side-by-side comparison readout."""
        self.measurement_tracker.select_current(current_id if current_id else None)
        self.measurement_tracker.select_previous(prev_id if prev_id else None)
        self._update_icu_measurement_tracking_ui()

    def _update_icu_measurement_tracking_ui(self):
        """Refreshes the measurement tracking card in ICU mode."""
        if hasattr(self.or_icu_mode, "measurement_tracking_card") and self.or_icu_mode.measurement_tracking_card:
            self.or_icu_mode.measurement_tracking_card.update_tracking(self.measurement_tracker)

    # ── ICU Feature 4: Annotation Carry-Forward Handlers ──────────────────────

    def _sync_icu_annotation_cf(self, patient: dict, scan: dict):
        """Loads and synchronizes annotations and geometries for the active patient and scans."""
        mrn = str(patient.get("mrn", "")) if patient else ""
        if not mrn:
            self.annotation_cf_manager.clear()
            self._update_icu_annotation_cf_ui()
            return

        self.annotation_cf_manager.set_patient(mrn)

        curr_id = str(scan.get("id", "") or scan.get("file_path", "")) if scan else ""

        prev_scan = None
        if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
            prev_scan = self.or_icu_mode.current_previous_card.previous_scan
        if not prev_scan and mrn:
            prev_scan = get_previous_scan_for_patient(mrn, scan)
        prev_id = str(prev_scan.get("id", "") or prev_scan.get("file_path", "")) if prev_scan else ""

        # Extract geometries
        curr_geom = None
        prev_geom = None

        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer and self.viewer_3d.slice_viewer.vol_data is not None:
            sv = self.viewer_3d.slice_viewer
            curr_geom = {
                "dims": sv.vol_data.shape,
                "spacing": sv.pixel_spacing,
                "thickness": sv.slice_thickness,
                "origin": (0.0, 0.0, 0.0)
            }

        comp = getattr(self.viewer_3d, "comparison", None)
        if comp and comp.before_volume is not None:
            prev_geom = {
                "dims": comp.before_volume.shape,
                "spacing": comp.before_pixel_spacing or (1.0, 1.0),
                "thickness": comp.before_slice_thickness or 1.0,
                "origin": (0.0, 0.0, 0.0)
            }

        self.annotation_cf_manager.set_scans(
            current_scan_id=curr_id,
            previous_scan_id=prev_id,
            current_geom=curr_geom,
            previous_geom=prev_geom
        )

        # Load persisted annotations for this patient and both scans
        if curr_id:
            curr_annots = get_scan_annotations_for_scan(curr_id, mrn)
            for d in curr_annots:
                a = ScanAnnotation.from_dict(d)
                self.annotation_cf_manager.add_annotation(a)

        if prev_id:
            prev_annots = get_scan_annotations_for_scan(prev_id, mrn)
            for d in prev_annots:
                a = ScanAnnotation.from_dict(d)
                self.annotation_cf_manager.add_annotation(a)

        self._update_icu_annotation_cf_ui()

    def _on_icu_create_annotation(self):
        """Switches to 2D slice picking mode to create a new spatial annotation."""
        self._go_root(self.viewer_3d)
        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.set_annotation_picking_mode(True)
            self.flash_status("Click on 2D slice to place annotation")

    def _on_annotation_point_selected(self, x_mm: float, y_mm: float, z_mm: float):
        """Creates a new user annotation at the clicked 2D slice location and persists it."""
        import uuid
        curr_patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
        mrn = str(curr_patient.get("mrn", ""))
        if not mrn:
            self.flash_status("No active patient for annotation")
            return

        # Determine active scan (Current vs Previous)
        comp = getattr(self.viewer_3d, "comparison", None)
        if comp and comp.is_active and comp.current_view == "BEFORE" and comp.before_scan:
            active_scan = comp.before_scan
        else:
            active_scan = getattr(self.or_icu_mode, "current_scan", None) or getattr(self.viewer_3d, "current_scan", {}) or {}

        scan_id = str(active_scan.get("id", "") or active_scan.get("file_path", ""))
        if not scan_id:
            self.flash_status("No active scan for annotation")
            return

        annot_count = len(self.annotation_cf_manager.get_annotations_for_patient(mrn)) + 1
        label = f"Note {annot_count}"
        annot_id = f"annot_{uuid.uuid4().hex[:8]}"

        annot = ScanAnnotation(
            id=annot_id,
            patient_mrn=mrn,
            scan_id=scan_id,
            label=label,
            text="",
            physical_x_mm=round(x_mm, 2),
            physical_y_mm=round(y_mm, 2),
            physical_z_mm=round(z_mm, 2),
            metadata={"created_via": "2D_slice_click"}
        )

        save_scan_annotation(
            annotation_id=annot.id,
            patient_mrn=annot.patient_mrn,
            scan_id=annot.scan_id,
            label=annot.label,
            text=annot.text,
            physical_x_mm=annot.physical_x_mm,
            physical_y_mm=annot.physical_y_mm,
            physical_z_mm=annot.physical_z_mm,
            metadata=annot.metadata
        )

        self.annotation_cf_manager.add_annotation(annot)
        self.annotation_cf_manager.select_annotation(annot.id)
        self._update_icu_annotation_cf_ui()
        self._mark_icu_handoff_stale()
        self.flash_status(f"Annotation created: {label} ({x_mm:.1f}, {y_mm:.1f}, {z_mm:.1f}) mm")

    def _on_icu_select_annotation(self, annot_id: str):
        """Selects an annotation and navigates 2D slice, MPR, and 3D marker."""
        self.annotation_cf_manager.select_annotation(annot_id)
        annot = self.annotation_cf_manager.get_selected_annotation()
        if annot:
            x, y, z = annot.physical_coordinates
            derived_slice = self.annotation_cf_manager.get_derived_slice(annot.scan_id, "axial")
            if derived_slice is not None and hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
                self.viewer_3d.slice_viewer.set_slice_index(derived_slice)

            if hasattr(self.viewer_3d, "mpr_view") and self.viewer_3d.mpr_view:
                self.viewer_3d.mpr_view.set_same_location(x, y, z)

            self.viewer_3d.set_annotation_marker(x, y, z, annot.label, annot.is_carried_forward())
            self.flash_status(f"Selected: {annot.label} ({x:.1f}, {y:.1f}, {z:.1f}) mm")

        self._update_icu_annotation_cf_ui()

    def _on_icu_carry_forward(self, target_scan_id: str):
        """Carries forward selected annotation to target scan and persists target record."""
        target_annot = self.annotation_cf_manager.carry_forward(target_scan_id)
        if target_annot:
            save_scan_annotation(
                annotation_id=target_annot.id,
                patient_mrn=target_annot.patient_mrn,
                scan_id=target_annot.scan_id,
                label=target_annot.label,
                text=target_annot.text,
                physical_x_mm=target_annot.physical_x_mm,
                physical_y_mm=target_annot.physical_y_mm,
                physical_z_mm=target_annot.physical_z_mm,
                metadata=target_annot.metadata
            )
            self._update_icu_annotation_cf_ui()
            x, y, z = target_annot.physical_coordinates
            self.viewer_3d.set_annotation_marker(x, y, z, target_annot.label, is_carried=True)
            self._mark_icu_handoff_stale()
            self.flash_status(f"Carried forward to {target_scan_id} (Approx. Correspondence)")
        else:
            _, status = self.annotation_cf_manager.can_carry_forward(target_scan_id)
            self.flash_status(f"Cannot carry forward: {status}")

    def _on_icu_view_annotation_source(self, annot_id: str):
        """Switches to Viewer3D and aligns view with source annotation's scan."""
        annot = self.annotation_cf_manager._annotations.get(annot_id)
        if not annot:
            return
        self._go_root(self.viewer_3d)
        src_scan_id = annot.scan_id
        is_prev = (src_scan_id == self.annotation_cf_manager.previous_scan_id)
        if is_prev:
            if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
                prev_scan = self.or_icu_mode.current_previous_card.previous_scan
                curr_scan = self.or_icu_mode.current_scan
                if prev_scan and curr_scan:
                    self._on_icu_view_previous(prev_scan, curr_scan)
        else:
            self._on_icu_view_current()

        slice_idx = self.annotation_cf_manager.get_derived_slice(src_scan_id, "axial")
        if slice_idx is not None and hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.set_slice_index(slice_idx)
        x, y, z = annot.physical_coordinates
        self.viewer_3d.set_annotation_marker(x, y, z, annot.label, annot.is_carried_forward())

    def _on_icu_view_annotation_target(self, annot_id: str):
        """Switches to Viewer3D and aligns view with target scan's derived slice."""
        annot = self.annotation_cf_manager._annotations.get(annot_id)
        if not annot:
            return
        self._go_root(self.viewer_3d)
        tgt_scan_id = self.annotation_cf_manager.target_scan_id
        if not tgt_scan_id:
            return
        is_prev = (tgt_scan_id == self.annotation_cf_manager.previous_scan_id)
        if is_prev:
            if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
                prev_scan = self.or_icu_mode.current_previous_card.previous_scan
                curr_scan = self.or_icu_mode.current_scan
                if prev_scan and curr_scan:
                    self._on_icu_view_previous(prev_scan, curr_scan)
        else:
            self._on_icu_view_current()

        slice_idx = self.annotation_cf_manager.get_derived_slice(tgt_scan_id, "axial")
        if slice_idx is not None and hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.set_slice_index(slice_idx)
        x, y, z = annot.physical_coordinates
        self.viewer_3d.set_annotation_marker(x, y, z, annot.label, is_carried=True)

    def _on_icu_delete_annotation(self, annot_id: str):
        """Deletes an annotation from database and manager."""
        curr_patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
        mrn = str(curr_patient.get("mrn", ""))
        delete_scan_annotation(annot_id, mrn if mrn else None)
        self.annotation_cf_manager.remove_annotation(annot_id)
        self.viewer_3d.clear_annotation_marker()
        self._update_icu_annotation_cf_ui()
        self._mark_icu_handoff_stale()
        self.flash_status("Annotation deleted")

    def _on_icu_clear_annotation_selection(self):
        """Clears the active annotation selection and 3D marker."""
        self.annotation_cf_manager.clear_selection()
        self.viewer_3d.clear_annotation_marker()
        self._update_icu_annotation_cf_ui()
        self.flash_status("Annotation selection cleared")

    def _update_icu_annotation_cf_ui(self):
        """Refreshes the annotation carry-forward card in ICU mode and updates 2D viewer."""
        if hasattr(self.or_icu_mode, "annotation_carry_forward_card") and self.or_icu_mode.annotation_carry_forward_card:
            self.or_icu_mode.annotation_carry_forward_card.update_card(self.annotation_cf_manager)

        # Update 2D viewer annotations
        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            annots_list = []
            for a in self.annotation_cf_manager._annotations.values():
                annots_list.append({
                    "id": a.id,
                    "label": a.label,
                    "x": a.physical_x_mm,
                    "y": a.physical_y_mm,
                    "z": a.physical_z_mm,
                    "scan_id": a.scan_id,
                    "is_carried": a.is_carried_forward()
                })
            self.viewer_3d.slice_viewer.set_active_annotations(annots_list)

    # ── ICU Feature 5: What Changed? Handlers ────────────────────────────────
    def _sync_icu_what_changed(self, patient: dict, scan: dict):
        """Synchronizes What Changed state with current and previous scan context."""
        mrn = str(patient.get("mrn", ""))
        curr_id = str(scan.get("id", "") or scan.get("file_path", "")) if scan else ""

        prev_scan = None
        if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
            prev_scan = self.or_icu_mode.current_previous_card.previous_scan
        if not prev_scan and mrn and scan:
            prev_scan = get_previous_scan_for_patient(mrn, scan)
        prev_id = str(prev_scan.get("id", "") or prev_scan.get("file_path", "")) if prev_scan else ""

        # Invalidate or reset if patient or scan context changed
        if (self.change_review.current_patient_mrn != mrn or
            self.change_review.current_scan_id != curr_id or
            self.change_review.previous_scan_id != prev_id):
            self.change_review.reset()
            self.change_review.set_patient_and_scans(mrn, mrn, curr_id, prev_id)

        self._update_icu_what_changed_ui()

    def _on_icu_review_differences(self):
        """Activates objective difference review between Current and Previous scans."""
        patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
        curr_scan = getattr(self.or_icu_mode, "current_scan", None) or getattr(self.viewer_3d, "current_scan", {}) or {}
        prev_scan = None
        if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
            prev_scan = self.or_icu_mode.current_previous_card.previous_scan
        if not prev_scan and patient and curr_scan:
            prev_scan = get_previous_scan_for_patient(patient.get("mrn", ""), curr_scan)

        if not prev_scan or not curr_scan:
            self.change_review.set_difference_unavailable("Current and Previous scans must both be available.")
            self._update_icu_what_changed_ui()
            self.flash_status("Difference map unavailable: Both scans required")
            return

        c_mrn = str(patient.get("mrn", "") or curr_scan.get("patient_mrn", ""))
        p_mrn = str(prev_scan.get("patient_mrn", "") or prev_scan.get("mrn", "") or c_mrn)
        if c_mrn and p_mrn and c_mrn != p_mrn:
            self.change_review.set_difference_unavailable("Scans belong to different patients.")
            self._update_icu_what_changed_ui()
            self.flash_status("Difference map unavailable: Cross-patient comparison disallowed")
            return

        # Ensure before volume is loaded in comparison model
        if not self.viewer_3d.comparison.is_active or self.viewer_3d.comparison.before_volume is None:
            self.viewer_3d.start_before_after_comparison(prev_scan, curr_scan, "TOGGLE")

        curr_vol = getattr(self.viewer_3d.slice_viewer, "vol_data", None)
        if curr_vol is None and hasattr(self.viewer_3d, "mpr_view"):
            curr_vol = getattr(self.viewer_3d.mpr_view, "vol_data", None)
        prev_vol = getattr(self.viewer_3d.comparison, "before_volume", None)

        curr_spacing = getattr(self.viewer_3d.slice_viewer, "pixel_spacing", (1.0, 1.0))
        curr_thickness = getattr(self.viewer_3d.slice_viewer, "slice_thickness", 1.0)
        prev_spacing = getattr(self.viewer_3d.comparison, "before_pixel_spacing", (1.0, 1.0))
        prev_thickness = getattr(self.viewer_3d.comparison, "before_slice_thickness", 1.0)

        curr_id = str(curr_scan.get("id", "") or curr_scan.get("file_path", ""))
        prev_id = str(prev_scan.get("id", "") or prev_scan.get("file_path", ""))

        self.change_review.set_patient_and_scans(c_mrn, p_mrn, curr_id, prev_id)

        curr_geom = {
            "dims": curr_vol.shape if curr_vol is not None else None,
            "spacing": curr_spacing,
            "thickness": curr_thickness,
            "origin": (0.0, 0.0, 0.0)
        }
        prev_geom = {
            "dims": prev_vol.shape if prev_vol is not None else None,
            "spacing": prev_spacing,
            "thickness": prev_thickness,
            "origin": (0.0, 0.0, 0.0)
        }

        if curr_vol is None or prev_vol is None:
            self.change_review.set_difference_unavailable("Volume data not loaded.")
            self._update_icu_what_changed_ui()
            self.flash_status("Difference map unavailable: Volume data not loaded")
            return

        is_compat, reason = self.change_review.check_compatibility(curr_vol, prev_vol, curr_geom, prev_geom)
        if not is_compat:
            self.change_review.set_difference_unavailable(reason)
            self._update_icu_what_changed_ui()
            self.flash_status(f"Difference map unavailable: {reason}")
            return

        diff_vol = self.change_review.compute_difference(curr_vol, prev_vol, curr_geom, prev_geom)
        if diff_vol is None:
            self.change_review.set_difference_unavailable("Failed to compute difference map.")
            self._update_icu_what_changed_ui()
            return

        self.change_review.enabled = True
        self._update_icu_what_changed_ui()

        # Update 2D and MPR overlays
        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.set_difference_overlay(True, diff_vol, self.change_review.threshold)
        if hasattr(self.viewer_3d, "mpr_view") and self.viewer_3d.mpr_view:
            self.viewer_3d.mpr_view.set_difference_volume(diff_vol, self.change_review.threshold, True)

        self._go_root(self.viewer_3d)
        cnt = self.change_review.get_changed_voxel_count()
        self._mark_icu_handoff_stale()
        self.flash_status(f"Difference review active ({cnt:,} changed voxels)")

    def _on_icu_show_current(self):
        """Shows current scan in viewer."""
        self._on_icu_view_current()

    def _on_icu_show_previous(self):
        """Shows previous scan in viewer."""
        patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
        curr_scan = getattr(self.or_icu_mode, "current_scan", None) or getattr(self.viewer_3d, "current_scan", {}) or {}
        prev_scan = None
        if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
            prev_scan = self.or_icu_mode.current_previous_card.previous_scan
        if not prev_scan and patient and curr_scan:
            prev_scan = get_previous_scan_for_patient(patient.get("mrn", ""), curr_scan)
        if prev_scan and curr_scan:
            self._on_icu_view_previous(prev_scan, curr_scan)

    def _on_icu_difference_threshold_changed(self, threshold: float):
        """Adjusts the difference visualization threshold without re-reading DICOM files."""
        self.change_review.set_threshold(threshold)
        if self.change_review.enabled and self.change_review.difference_volume is not None:
            if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
                self.viewer_3d.slice_viewer.set_difference_overlay(True, self.change_review.difference_volume, threshold)
            if hasattr(self.viewer_3d, "mpr_view") and self.viewer_3d.mpr_view:
                self.viewer_3d.mpr_view.set_difference_volume(self.change_review.difference_volume, threshold, True)
        self._update_icu_what_changed_ui()
        self._mark_icu_handoff_stale()

    def _on_icu_clear_difference(self):
        """Clears active difference review and removes overlays."""
        self.change_review.clear_review()
        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.clear_difference_overlay()
        if hasattr(self.viewer_3d, "mpr_view") and self.viewer_3d.mpr_view:
            self.viewer_3d.mpr_view.clear_difference_volume()
        self._update_icu_what_changed_ui()
        self._mark_icu_handoff_stale()
        self.flash_status("Difference review cleared")

    def _update_icu_what_changed_ui(self):
        """Updates the What Changed card in ICU mode."""
        if hasattr(self.or_icu_mode, "what_changed_card") and self.or_icu_mode.what_changed_card:
            self.or_icu_mode.what_changed_card.update_from_review(self.change_review)

    # ── ICU Feature 6: Device Markers Handlers ───────────────────────────────
    def _sync_icu_device_markers(self, patient: dict, scan: dict):
        """Loads and synchronizes device markers for the active patient and scan."""
        mrn = str(patient.get("mrn", "")) if patient else ""
        scan_id = str(scan.get("id", "") or scan.get("file_path", "")) if scan else ""

        current_geom = None
        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer and self.viewer_3d.slice_viewer.vol_data is not None:
            sv = self.viewer_3d.slice_viewer
            current_geom = {
                "dims": sv.vol_data.shape,
                "spacing": sv.pixel_spacing,
                "thickness": sv.slice_thickness,
                "origin": (0.0, 0.0, 0.0)
            }

        self.device_marker_manager.set_context(mrn, scan_id, current_geom)

        if not mrn or not scan_id:
            self.device_marker_manager.clear()
            self._update_icu_device_markers_ui()
            return

        db_markers = get_device_markers_for_scan(scan_id, mrn)
        self.device_marker_manager.clear()
        for m in db_markers:
            marker = DeviceMarker(
                id=m["id"],
                patient_mrn=m["patient_mrn"],
                scan_id=m["scan_id"],
                device_type=m["device_type"],
                label=m["label"],
                physical_x_mm=m["physical_x_mm"],
                physical_y_mm=m["physical_y_mm"],
                physical_z_mm=m["physical_z_mm"],
                created_at=m.get("created_at", ""),
                metadata=m.get("metadata", {})
            )
            self.device_marker_manager.add_marker(marker)

        self._update_icu_device_markers_ui()

    def _on_icu_mark_device(self, device_type: str, label: str):
        """Switches to Viewer3D and activates 2D slice device marker picking mode."""
        self._go_root(self.viewer_3d)
        if hasattr(self.viewer_3d, "set_2d_panel_visible"):
            self.viewer_3d.set_2d_panel_visible(True)
        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.set_device_marker_picking_mode(True)
            self._pending_device_type = device_type
            self._pending_device_label = label
            self.flash_status(f"Click on 2D slice to place marker for: {device_type}")

    def _on_device_marker_point_selected(self, x_mm: float, y_mm: float, z_mm: float):
        """Processes 2D slice click: creates canonical device marker and persists to database."""
        import uuid
        patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
        scan = getattr(self.or_icu_mode, "current_scan", None) or getattr(self.viewer_3d, "current_scan", {}) or {}
        mrn = str(patient.get("mrn", ""))
        scan_id = str(scan.get("id", "") or scan.get("file_path", ""))

        device_type = getattr(self, "_pending_device_type", "Other") or "Other"
        label = getattr(self, "_pending_device_label", "") or device_type

        marker_id = f"dm_{uuid.uuid4().hex[:10]}"
        marker = DeviceMarker(
            id=marker_id,
            patient_mrn=mrn,
            scan_id=scan_id,
            device_type=device_type,
            label=label,
            physical_x_mm=float(x_mm),
            physical_y_mm=float(y_mm),
            physical_z_mm=float(z_mm),
        )

        save_device_marker(marker.to_dict())
        self.device_marker_manager.add_marker(marker)
        self.device_marker_manager.select_marker(marker_id)
        self._update_icu_device_markers_ui()
        self._mark_icu_handoff_stale()
        self.flash_status(f"Placed device marker: {device_type} ({label})")

    def _on_icu_view_device_marker(self, marker_id: str):
        """Switches to Viewer3D and navigates 2D slice, MPR, and 3D camera to the device marker."""
        marker = self.device_marker_manager.get_marker(marker_id)
        if not marker:
            return
        self._go_root(self.viewer_3d)
        x, y, z = marker.physical_coordinates

        slice_idx = self.device_marker_manager.get_derived_slice(marker_id, "axial")
        if slice_idx is not None and hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.set_slice_index(slice_idx)
            self.viewer_3d.slice_viewer.navigate_to_physical_point(x, y, z)

        if hasattr(self.viewer_3d, "mpr_view") and self.viewer_3d.mpr_view:
            self.viewer_3d.mpr_view.snap_to_volume_point(x, y, z)

        self.device_marker_manager.select_marker(marker_id)
        self._update_icu_device_markers_ui()
        self.flash_status(f"Navigated to {marker.device_type} at ({x:.1f}, {y:.1f}, {z:.1f}) mm")

    def _on_icu_delete_device_marker(self, marker_id: str):
        """Deletes a device marker from database, manager, and viewers."""
        patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
        mrn = str(patient.get("mrn", ""))
        delete_device_marker(marker_id, mrn if mrn else None)
        self.device_marker_manager.remove_marker(marker_id)
        if hasattr(self.viewer_3d, "remove_device_marker"):
            self.viewer_3d.remove_device_marker(marker_id)
        self._update_icu_device_markers_ui()
        self._mark_icu_handoff_stale()
        self.flash_status("Device marker deleted")

    def _on_icu_clear_device_marker_selection(self):
        """Clears active selection for device markers."""
        self.device_marker_manager.clear_selection()
        self._update_icu_device_markers_ui()
        self.flash_status("Device marker selection cleared")

    def _update_icu_device_markers_ui(self):
        """Refreshes the Device Markers card in ICU mode and updates 2D, MPR, and 3D viewers."""
        if hasattr(self.or_icu_mode, "device_markers_card") and self.or_icu_mode.device_markers_card:
            self.or_icu_mode.device_markers_card.update_card(self.device_marker_manager)

        markers_list = [m.to_dict() for m in self.device_marker_manager.get_markers_for_active_scan()]

        if hasattr(self.viewer_3d, "slice_viewer") and self.viewer_3d.slice_viewer:
            self.viewer_3d.slice_viewer.set_active_device_markers(markers_list)

        if hasattr(self.viewer_3d, "mpr_view") and self.viewer_3d.mpr_view:
            self.viewer_3d.mpr_view.set_active_device_markers(markers_list)

        if hasattr(self.viewer_3d, "set_device_markers"):
            self.viewer_3d.set_device_markers(markers_list)

    # ── ICU Feature 7: Quick Handoff ──────────────────────────────────────────

    def _on_icu_generate_handoff(self):
        """Generates a factual Quick Handoff snapshot from current workspace state."""
        patient = getattr(self.or_icu_mode, "current_patient", None) or getattr(self.viewer_3d, "current_patient", {}) or {}
        current_scan = getattr(self.or_icu_mode, "current_scan", None) or getattr(self.viewer_3d, "current_scan", {}) or {}

        previous_scan = None
        if hasattr(self.or_icu_mode, "current_previous_card") and self.or_icu_mode.current_previous_card:
            previous_scan = self.or_icu_mode.current_previous_card.previous_scan
        if not previous_scan and patient.get("mrn"):
            previous_scan = get_previous_scan_for_patient(patient.get("mrn"), current_scan=current_scan)

        self.quick_handoff_manager.generate_handoff(
            patient=patient,
            current_scan=current_scan,
            previous_scan=previous_scan,
            same_location_review=getattr(self, "icu_same_location", None),
            measurement_tracker=getattr(self, "measurement_tracker", None),
            annotation_cf_manager=getattr(self, "annotation_cf_manager", getattr(self, "annotation_carry_forward_manager", None)),
            change_review=getattr(self, "change_review", None),
            device_marker_manager=getattr(self, "device_marker_manager", None),
        )

        if hasattr(self.or_icu_mode, "quick_handoff_card") and self.or_icu_mode.quick_handoff_card:
            self.or_icu_mode.quick_handoff_card.update_card(self.quick_handoff_manager.get_snapshot())

        self.flash_status("Quick handoff generated")

    def _on_icu_copy_handoff(self):
        """Copies plain-text Quick Handoff summary to system clipboard."""
        snapshot = self.quick_handoff_manager.get_snapshot()
        if not snapshot:
            self.flash_status("No handoff snapshot to copy")
            return
        text = snapshot.generate_text_summary()
        cb = QApplication.clipboard()
        if cb:
            cb.setText(text)
        self.flash_status("Handoff summary copied to clipboard")

    def _on_icu_view_handoff(self):
        """Displays full Quick Handoff summary in a modal dialog."""
        snapshot = self.quick_handoff_manager.get_snapshot()
        if not snapshot:
            self.flash_status("No handoff snapshot to view")
            return
        from screens.or_icu_mode import QuickHandoffDialog
        dlg = QuickHandoffDialog(snapshot.generate_text_summary(), self)
        dlg.exec()

    def _on_icu_clear_handoff(self):
        """Clears the active Quick Handoff snapshot."""
        self.quick_handoff_manager.clear()
        if hasattr(self.or_icu_mode, "quick_handoff_card") and self.or_icu_mode.quick_handoff_card:
            self.or_icu_mode.quick_handoff_card.reset_ui()
        self.flash_status("Quick handoff cleared")

    def _mark_icu_handoff_stale(self):
        """Marks active handoff snapshot as stale due to workspace changes."""
        if hasattr(self, "quick_handoff_manager") and self.quick_handoff_manager:
            self.quick_handoff_manager.mark_stale()
        if hasattr(self.or_icu_mode, "quick_handoff_card") and self.or_icu_mode.quick_handoff_card:
            self.or_icu_mode.quick_handoff_card.mark_stale()




    # \u2500\u2500 Privacy auto-lock \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # A workstation left open in an OR/ICU is exposed PHI. After
    # AUTO_LOCK_MS of no gesture activity, blank the screen until dismissed.

    def _reset_lock_timer(self, *_):
        if self._locked:
            return
        self._lock_timer.start(self.AUTO_LOCK_MS)

    def _engage_lock(self):
        if self._locked:
            return
        self._locked = True
        self._pre_lock_index = self.stack.currentIndex()
        self.lock_screen.setVisible(True)
        self.stack.setVisible(False)
        log_action("auto_lock", None, "inactivity lock engaged")

    def _release_lock(self):
        self._locked = False
        self.lock_screen.setVisible(False)
        self.stack.setVisible(True)
        self._lock_timer.start(self.AUTO_LOCK_MS)
        log_action("auto_unlock", None, "unlocked by user")

    def _toggle_camera_hud(self):
        self.cam_hud.toggle_visibility()
        if self.cam_hud.isVisible():
            self.cam_btn.setText("Camera: ON")
            self.cam_btn.setStyleSheet(
                "QPushButton { color: #aaa; border: 1px solid #444; padding: 4px 10px; }"
            )
        else:
            self.cam_btn.setText("Camera: OFF")
            self.cam_btn.setStyleSheet(
                "QPushButton { color: #555; border: 1px solid #333; padding: 4px 10px; }"
            )

    def resizeEvent(self, event):
        """Keep the HUD pinned to the bottom-right whenever the window is resized."""
        super().resizeEvent(event)
        if hasattr(self, 'cam_hud'):
            self.cam_hud._reposition()

    def closeEvent(self, event):
        self.worker.stop()
        if hasattr(self, "voice_worker"):
            self.voice_worker.stop()
            self.voice_worker.wait(2000)
        event.accept()

if __name__ == "__main__":
    from database import init_db
    init_db()
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    screen = app.primaryScreen().availableGeometry()
    window.resize(screen.width(), screen.height())
    window.move(screen.x(), screen.y())
    window.show()

    sys.exit(app.exec())
