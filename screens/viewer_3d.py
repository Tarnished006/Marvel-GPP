# screens/3d_viewer.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QStackedWidget
)
from PyQt6.QtCore import Qt

from database import get_scans_for_ui
from screens.scans import dicom_to_qpixmap


class Viewer3D(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)

        # An internal stack: page 0 = empty/search state, page 1 = loaded state
        self.state_stack = QStackedWidget()
        outer.addWidget(self.state_stack)

        self.state_stack.addWidget(self._build_empty_state())
        self.state_stack.addWidget(self._build_loaded_state())

        self.state_stack.setCurrentIndex(0)  # start empty

    def _build_empty_state(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        search_box = QLineEdit()
        search_box.setPlaceholderText("Search patient name...")
        layout.addWidget(search_box)

        layout.addStretch()
        message = QLabel("No scan loaded\nSearch a patient or open a scan from Gallery")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(message)
        layout.addStretch()

        return page

    def _build_loaded_state(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        self.scan_label = QLabel("")
        outer.addWidget(self.scan_label)

        content_row = QHBoxLayout()
        outer.addLayout(content_row)

        # --- Render area (placeholder for now) ---
        self.render_area = QLabel("[ live 3D render ]")
        self.render_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.render_area.setFrameShape(QFrame.Shape.StyledPanel)
        content_row.addWidget(self.render_area, stretch=1)

        # --- Collapsible sidebar ---
        self.sidebar = QWidget()
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        content_row.addWidget(self.sidebar)

        # --- Toggle tab (always visible, sits at the edge) ---
        self.toggle_btn = QPushButton(">")
        self.toggle_btn.setFixedWidth(24)
        self.toggle_btn.clicked.connect(self._toggle_sidebar)
        content_row.addWidget(self.toggle_btn)

        return page

    def _toggle_sidebar(self):
        visible = self.sidebar.isVisible()
        self.sidebar.setVisible(not visible)
        self.toggle_btn.setText("<" if not visible else ">")

    def load_scan(self, patient: dict, scan: dict):
        self.scan_label.setText(
            f"{patient['name']} ({patient['mrn']}) · {scan['type']} · {scan['date']}"
        )
        pixmap = dicom_to_qpixmap(scan["file_path"])
        if pixmap:
            self.render_area.setPixmap(pixmap.scaled(
            600, 600, Qt.AspectRatioMode.KeepAspectRatio
        ))
        else:
            self.render_area.setText("[ could not load image ]")

        # Rebuild sidebar thumbnails for this patient's scans
        while self.sidebar_layout.count():
            item = self.sidebar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.sidebar_layout.addWidget(QLabel("Scans"))
        for s in get_scans_for_ui(patient["mrn"]):
            label = QLabel(f"[img] {s['type']}")
            if s == scan:
                label.setStyleSheet("border: 1px solid #4caf50;")  # highlight active scan
            self.sidebar_layout.addWidget(label)

        self.sidebar.setVisible(True)
        self.toggle_btn.setText(">")
        self.state_stack.setCurrentIndex(1)  # switch to loaded state


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    from theme import DARK_STYLESHEET

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = Viewer3D()
    window.resize(800, 550)
    window.show()

    # quick manual test: load a scan after 1 second
    mock_patient = {"mrn": "84729-A", "name": "Doe, John", "age": 45, "sex": "M", "scans": 2}
    mock_scan = {"type": "CT", "date": "2026-07-14"}
    window.load_scan(mock_patient, mock_scan)

    sys.exit(app.exec())