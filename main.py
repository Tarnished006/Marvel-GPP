import sys
import pyautogui
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QStackedWidget
)

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

        for btn in (self.back_btn, self.clinical_btn, self.viewer_btn, self.or_icu_btn):
            nav_bar.addWidget(btn)
        nav_bar.addStretch()
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
        """Fires a real OS click from the UI thread."""
        try:
            QApplication.processEvents()
            if hasattr(self, 'cursor_x') and hasattr(self, 'cursor_y'):
                pyautogui.click(self.cursor_x, self.cursor_y)
            else:
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

    def closeEvent(self, event):
        self.worker.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    window.showMaximized()

    sys.exit(app.exec())