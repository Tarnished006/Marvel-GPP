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
from database import touch_patient, log_action, get_patient, get_notes_for_patient
from report_export import build_case_report


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
        self.or_icu_btn.clicked.connect(lambda: self._go_root(self.or_icu_mode))

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
    from database import init_db, get_all_patients
    init_db()
    if not get_all_patients():
        try:
            from ingest import seed_demo_database
            seed_demo_database()
        except Exception as e:
            print(f"[main] Auto-seeding database failed: {e}")
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    screen = app.primaryScreen().availableGeometry()
    window.resize(screen.width(), screen.height())
    window.move(screen.x(), screen.y())
    window.show()

    sys.exit(app.exec())
