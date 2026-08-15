import sys
import pyautogui
from PyQt6.QtGui import QCursor, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QStackedWidget, QLabel, QSizePolicy
)
from PyQt6.QtCore import Qt

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
    MARGIN = 14          # gap from the window edges
    BORDER_RADIUS = 10

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(self.HUD_W, self.HUD_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Always render on top of sibling widgets
        self.raise_()

        # Outer card with rounded corners + subtle border
        card = QWidget(self)
        card.setObjectName("CameraHUDCard")
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
        header = QLabel("📷  Gesture Camera")
        header.setStyleSheet(
            "color: #8a8a8a; font-size: 10px; font-weight: 600;"
            " letter-spacing: 0.5px;"
        )
        header.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        inner.addWidget(header)

        # Camera feed label — fills remaining space
        self.feed = QLabel()
        self.feed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.feed.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.feed.setStyleSheet(
            "background: #000; border-radius: 6px;"
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
        pix = QPixmap.fromImage(qimage).scaled(
            self.feed.width(),
            self.feed.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
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
        self.air_mouse_btn = QPushButton("🖱 Air Mouse: OFF")
        self.air_mouse_btn.setCheckable(True)
        self.air_mouse_btn.setChecked(False)
        self.air_mouse_btn.setStyleSheet(
            "QPushButton { color: #888; border: 1px solid #555; padding: 4px 10px; }"
        )
        self.air_mouse_btn.clicked.connect(self._toggle_air_mouse)

        self.back_btn = QPushButton("← Back")
        self.back_btn.setVisible(False)
        self.back_btn.clicked.connect(self._go_back)
        self._nav_history = []

        # Camera HUD toggle — shown in nav bar
        self.cam_btn = QPushButton("📷 Camera: ON")
        self.cam_btn.setStyleSheet(
            "QPushButton { color: #aaa; border: 1px solid #444; padding: 4px 10px; }"
        )

        for btn in (self.back_btn, self.clinical_btn, self.viewer_btn, self.or_icu_btn):
            nav_bar.addWidget(btn)
        nav_bar.addStretch()
        nav_bar.addWidget(self.cam_btn)
        nav_bar.addWidget(self.air_mouse_btn)
        outer_layout.addLayout(nav_bar)

        # --- Stacked screens ---
        self.stack = QStackedWidget()
        outer_layout.addWidget(self.stack)

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

        self.clinical_btn.clicked.connect(lambda: self._go_root(self.dashboard))
        self.viewer_btn.clicked.connect(lambda: self._go_root(self.viewer_3d))
        self.or_icu_btn.clicked.connect(lambda: self._go_root(self.or_icu_mode))

        # --- Air Mouse Signal Connections ---
        signal_bus.cursor_moved.connect(self.move_os_cursor)
        signal_bus.pinch_started.connect(self.trigger_os_click)

        # --- Start Gesture Engine Thread ---
        self.worker = GestureWorker()
        self.worker.start()

    def move_os_cursor(self, norm_x: float, norm_y: float):
        """Moves the OS cursor from the UI thread via QCursor (no OS throttling)."""
        screen = QApplication.primaryScreen().geometry()
        self.cursor_x = screen.left() + int(norm_x * screen.width())
        self.cursor_y = screen.top()  + int(norm_y * screen.height())
        QCursor.setPos(self.cursor_x, self.cursor_y)

    def trigger_os_click(self):
        """Fires a real OS click from the UI thread at the cursor's current position."""
        try:
            # Let Qt actually process the QCursor.setPos() call above before
            # clicking, so the click can't race ahead of the cursor move.
            QApplication.processEvents()

            # FIX: previously called pyautogui.click(self.cursor_x, self.cursor_y).
            # Passing explicit coordinates makes PyAutoGUI do its own internal
            # moveTo() using ITS OWN screen-coordinate system, which can
            # disagree with Qt's QCursor coordinate system on displays with
            # OS scaling (125%/150% on Windows, any HiDPI setup) -- the click
            # can then land at a different point than where the cursor
            # visually is, even though the cursor itself looks correctly
            # placed. QCursor.setPos() already put the cursor exactly where
            # it needs to be, so just click there -- no coordinates needed.
            pyautogui.click()
        except Exception as e:
            print(f"[MainWindow] click failed: {e}")

    def _go_root(self, widget):
        self._nav_history.clear()
        self.back_btn.setVisible(False)
        self.stack.setCurrentWidget(widget)

    def _go_back(self):
        if self._nav_history:
            prev = self._nav_history.pop()
            self.stack.setCurrentWidget(prev)
        self.back_btn.setVisible(len(self._nav_history) > 0)

    def _push_screen(self, widget):
        self._nav_history.append(self.stack.currentWidget())
        self.back_btn.setVisible(True)
        self.stack.addWidget(widget)
        self.stack.setCurrentWidget(widget)

    def _toggle_air_mouse(self):
        self._air_mouse_on = not self._air_mouse_on
        signal_bus.air_mouse_toggle.emit(self._air_mouse_on)
        if self._air_mouse_on:
            self.air_mouse_btn.setText("🖱 Air Mouse: ON")
            self.air_mouse_btn.setStyleSheet(
                "QPushButton { color: #00e5ff; border: 1px solid #00e5ff; "
                "padding: 4px 10px; font-weight: bold; }"
            )
        else:
            self.air_mouse_btn.setText("🖱 Air Mouse: OFF")
            self.air_mouse_btn.setStyleSheet(
                "QPushButton { color: #888; border: 1px solid #555; padding: 4px 10px; }"
            )

    def show_record(self, patient: dict):
        record_screen = PatientRecord(patient)
        record_screen.view_scans_clicked.connect(self.show_scans)
        self._push_screen(record_screen)

    def show_scans(self, patient: dict):
        scans_screen = ScanGallery(patient)
        scans_screen.view_in_3d_clicked.connect(self.show_3d_viewer)
        self._push_screen(scans_screen)

    def show_3d_viewer(self, patient: dict, scan: dict):
        self.viewer_3d.load_scan(patient, scan)
        self._push_screen(self.viewer_3d)

    def _toggle_camera_hud(self):
        self.cam_hud.toggle_visibility()
        if self.cam_hud.isVisible():
            self.cam_btn.setText("📷 Camera: ON")
            self.cam_btn.setStyleSheet(
                "QPushButton { color: #aaa; border: 1px solid #444; padding: 4px 10px; }"
            )
        else:
            self.cam_btn.setText("📷 Camera: OFF")
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
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    screen = app.primaryScreen().availableGeometry()
    window.resize(screen.width(), screen.height())
    window.move(screen.x(), screen.y())
    window.show()

    sys.exit(app.exec())