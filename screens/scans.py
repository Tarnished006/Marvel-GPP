# screens/scans.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal

MOCK_SCANS = {
    "84729-A": [
        {"type": "CT", "date": "2026-07-14"},
        {"type": "MRI", "date": "2026-08-02"},
    ],
    "55910-B": [
        {"type": "CT", "date": "2026-06-20"},
    ],
}

class ScanCard(QFrame):
    view_in_3d_clicked = pyqtSignal(dict)

    def __init__(self, scan: dict):
        super().__init__()
        self.scan = scan
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        thumb = QLabel("[thumbnail]")
        thumb.setFixedHeight(80)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)

        info = QLabel(f"{scan['type']} · {scan['date']}")
        btn = QPushButton("View in 3D")
        btn.clicked.connect(lambda: self.view_in_3d_clicked.emit(self.scan))

        for widget in (thumb, info, btn):
            layout.addWidget(widget)


class ScanGallery(QWidget):
    view_in_3d_clicked = pyqtSignal(dict, dict)  # (patient, scan)

    def __init__(self, patient: dict):
        super().__init__()
        self.patient = patient
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"<b>SCANS — {patient['name']}</b>"))

        grid = QGridLayout()
        scans = MOCK_SCANS.get(patient["mrn"], [])
        for index, scan in enumerate(scans):
            card = ScanCard(scan)
            card.view_in_3d_clicked.connect(
                lambda scan, p=patient: self.view_in_3d_clicked.emit(p, scan)
            )
            row, col = divmod(index, 2)
            grid.addWidget(card, row, col)
        layout.addLayout(grid)

        # --- pagination placeholder ---
        pagination = QHBoxLayout()
        pagination.addWidget(QLabel("<-- Swipe Left"))
        pagination.addWidget(QLabel("PAGE 1 OF 1"))
        pagination.addWidget(QLabel("Swipe Right -->"))
        layout.addLayout(pagination)


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    from theme import DARK_STYLESHEET

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    mock_patient = {"mrn": "84729-A", "name": "Doe, John", "age": 45, "sex": "M", "scans": 2}
    window = ScanGallery(mock_patient)
    window.resize(700, 500)
    window.show()

    sys.exit(app.exec())