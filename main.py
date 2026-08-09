# main.py
import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QStackedWidget
)

from theme import DARK_STYLESHEET
from screens.dashboard import Dashboard
from screens.record import PatientRecord
from screens.scans import ScanGallery
from screens.viewer_3d import Viewer3D
from screens.or_icu_mode import OrIcuMode


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Aegis-Touch")
        self.resize(1000, 700)

        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)

        # --- Top-level nav bar ---
        nav_bar = QHBoxLayout()
        self.clinical_btn = QPushButton("Clinical View")
        self.viewer_btn = QPushButton("3D Viewer")
        self.or_icu_btn = QPushButton("OR/ICU Mode")
        for btn in (self.clinical_btn, self.viewer_btn, self.or_icu_btn):
            nav_bar.addWidget(btn)
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

        # --- Nav bar wiring ---
        self.clinical_btn.clicked.connect(lambda: self.stack.setCurrentWidget(self.dashboard))
        self.viewer_btn.clicked.connect(lambda: self.stack.setCurrentWidget(self.viewer_3d))
        self.or_icu_btn.clicked.connect(lambda: self.stack.setCurrentWidget(self.or_icu_mode))

    def show_record(self, patient: dict):
        record_screen = PatientRecord(patient)
        record_screen.view_scans_clicked.connect(self.show_scans)
        self.stack.addWidget(record_screen)
        self.stack.setCurrentWidget(record_screen)

    def show_scans(self, patient: dict):
        scans_screen = ScanGallery(patient)
        scans_screen.view_in_3d_clicked.connect(self.show_3d_viewer)
        self.stack.addWidget(scans_screen)
        self.stack.setCurrentWidget(scans_screen)

    def show_3d_viewer(self, patient: dict, scan: dict):
        self.viewer_3d.load_scan(patient, scan)
        self.stack.setCurrentWidget(self.viewer_3d)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    window.showMaximized()

    sys.exit(app.exec())