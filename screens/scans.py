# screens/scans.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from database import get_scans_for_ui
import os
import numpy as np
import pydicom
from PyQt6.QtGui import QPixmap, QImage

def dicom_to_qpixmap(file_path: str) -> QPixmap | None:
    try:
        if os.path.isdir(file_path):
            dcm_files = [f for f in os.listdir(file_path) if f.endswith(".dcm")]
            if not dcm_files:
                return None
            file_path = os.path.join(file_path, sorted(dcm_files)[0])

        ds = pydicom.dcmread(file_path)
        pixels = ds.pixel_array.astype(float)

        pixels -= pixels.min()
        if pixels.max() > 0:
            pixels = (pixels / pixels.max()) * 255.0
        pixels = pixels.astype(np.uint8)

        height, width = pixels.shape
        image = QImage(pixels.data, width, height, width, QImage.Format.Format_Grayscale8)
        return QPixmap.fromImage(image.copy())

    except Exception as e:
        print(f"Could not load DICOM preview for {file_path}: {e}")
        return None

class ScanCard(QFrame):
    view_in_3d_clicked = pyqtSignal(dict)

    def __init__(self, scan: dict):
        super().__init__()
        self.scan = scan
        self.setFixedWidth(160)

        layout = QVBoxLayout(self)
        thumb = QLabel()
        thumb.setFixedSize(120, 120)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = dicom_to_qpixmap(scan["file_path"])
        thumb.setPixmap(
        pixmap.scaled(120, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if pixmap else QPixmap()
)

        info = QLabel(f"{scan['type']} · {scan['date']}")
        btn = QPushButton("View in 3D")
        btn.clicked.connect(lambda: self.view_in_3d_clicked.emit(self.scan))
        info.setWordWrap(True)
        for widget in (thumb, info, btn):
            layout.addWidget(widget)


class ScanGallery(QWidget):
    view_in_3d_clicked = pyqtSignal(dict, dict)  # (patient, scan)

    def __init__(self, patient: dict):
        super().__init__()
        self.patient = patient
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        layout.addWidget(QLabel(f"<b>SCANS — {patient['name']}</b>"))

        grid = QGridLayout()
        scans = get_scans_for_ui(patient["mrn"])
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