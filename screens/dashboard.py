import sys
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QComboBox, QLabel, QFrame, QPushButton, QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal

try:
    from database import get_patients_for_ui
except Exception:
    def get_patients_for_ui():
        return []

# ── Discover local DICOM folders (same logic as viewer_3d) ────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _local_scan_cards():
    """Return a list of fake patient dicts for each local DICOM folder found."""
    cards = []
    for folder_name, display, preset in [
        ("skull", "Skull CT",  "skull"),
        ("DICOM", "Chest / Body CT", "body"),
    ]:
        path = os.path.join(_ROOT, folder_name)
        if os.path.isdir(path):
            dcm_count = sum(1 for f in os.listdir(path) if f.lower().endswith(".dcm"))
            if dcm_count > 1:
                cards.append({
                    # shaped like a real patient + scan so the same signal chain works
                    "_is_local": True,
                    "name":  display,
                    "mrn":   f"LOCAL-{folder_name.upper()}",
                    "age":   "—",
                    "sex":   "—",
                    "scans": dcm_count,
                    # scan dict embedded for direct 3D-viewer launch
                    "_scan": {
                        "type":        display,
                        "date":        "Local dataset",
                        "description": f"{dcm_count} DICOM slices",
                        "file_path":   path,
                        "slice_count": dcm_count,
                    },
                })
    return cards


class PatientCard(QFrame):
    view_records_clicked  = pyqtSignal(dict)
    view_scans_clicked    = pyqtSignal(dict)
    view_3d_direct_clicked = pyqtSignal(dict, dict)   # patient, scan

    def __init__(self, patient: dict):
        super().__init__()
        self.patient = patient
        is_local = patient.get("_is_local", False)

        self.setObjectName("PatientCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # Header icon / badge
        badge = QLabel("[LOCAL SCAN]" if is_local else "[PATIENT]")
        badge.setStyleSheet(
            f"color: {'#5a9' if is_local else '#777'};"
            " font-size: 9px; font-weight: 700; letter-spacing: 0.8px;"
        )
        layout.addWidget(badge)

        name_label = QLabel(f"<b>{patient['name']}</b>")
        name_label.setStyleSheet("font-size: 13px;")
        layout.addWidget(name_label)

        info_label = QLabel(
            f"MRN: {patient['mrn']}  ·  {patient['scans']} slices"
            if is_local else
            f"MRN: {patient['mrn']} · {patient['age']} {patient['sex']} · {patient['scans']} scans"
        )
        info_label.setStyleSheet("color: #777; font-size: 10px;")
        layout.addWidget(info_label)

        if is_local:
            open_btn = QPushButton("Open in 3D Viewer ->")
            open_btn.setStyleSheet(
                "QPushButton { background: #1a2a1a; color: #7cfc00;"
                " border: 1px solid #3a5a3a; border-radius: 4px; padding: 5px; font-size: 11px; }"
                "QPushButton:hover { background: #223022; }"
            )
            open_btn.clicked.connect(
                lambda: self.view_3d_direct_clicked.emit(self.patient, self.patient["_scan"])
            )
            layout.addWidget(open_btn)
        else:
            records_btn = QPushButton("View Records")
            scans_btn   = QPushButton("View Scans")
            records_btn.clicked.connect(lambda: self.view_records_clicked.emit(self.patient))
            scans_btn.clicked.connect(lambda: self.view_scans_clicked.emit(self.patient))
            layout.addWidget(records_btn)
            layout.addWidget(scans_btn)


class Dashboard(QWidget):
    view_records_clicked   = pyqtSignal(dict)
    view_scans_clicked     = pyqtSignal(dict)
    view_3d_direct_clicked = pyqtSignal(dict, dict)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Search + filter row ───────────────────────────────────────────────
        search_row = QHBoxLayout()
        search_box = QLineEdit()
        search_box.setPlaceholderText("Search patients…")
        filter_box = QComboBox()
        filter_box.addItems(["All Patients"])
        search_row.addWidget(search_box)
        search_row.addWidget(filter_box)
        layout.addLayout(search_row)

        # ── Recent / flagged row ──────────────────────────────────────────────
        layout.addWidget(QLabel("RECENT / FLAGGED"))
        recent_row = QHBoxLayout()
        for _ in range(3):
            ph = QLabel("—")
            ph.setFrameShape(QFrame.Shape.StyledPanel)
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setFixedHeight(40)
            recent_row.addWidget(ph)
        layout.addLayout(recent_row)

        # ── Patient directory + local datasets ────────────────────────────────
        db_patients   = get_patients_for_ui()
        local_datasets = _local_scan_cards()
        all_cards     = local_datasets + db_patients   # locals first so they're always visible

        dir_label = QLabel(
            f"PATIENT DIRECTORY  ({len(db_patients)} patients)"
            + ("  ·  LOCAL DATASETS AVAILABLE" if local_datasets else "")
        )
        dir_label.setStyleSheet("color: #666; font-size: 10px; font-weight: 600;")
        layout.addWidget(dir_label)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        scroll_content = QWidget()
        grid = QGridLayout(scroll_content)
        grid.setSpacing(10)

        if not all_cards:
            empty = QLabel(
                "No patients found and no local DICOM folders detected.\n"
                "Add a skull/ or DICOM/ folder to the project root to get started."
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #444; font-size: 12px;")
            grid.addWidget(empty, 0, 0)
        else:
            for index, patient in enumerate(all_cards):
                card = PatientCard(patient)
                card.view_records_clicked.connect(self.view_records_clicked.emit)
                card.view_scans_clicked.connect(self.view_scans_clicked.emit)
                card.view_3d_direct_clicked.connect(self.view_3d_direct_clicked.emit)
                row, col = divmod(index, 2)
                grid.addWidget(card, row, col)

        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area)


if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication
    try:
        from theme import DARK_STYLESHEET
    except ImportError:
        DARK_STYLESHEET = ""

    app = QApplication(sys.argv)
    if DARK_STYLESHEET:
        app.setStyleSheet(DARK_STYLESHEET)
    window = Dashboard()
    window.resize(700, 500)
    window.show()
    sys.exit(app.exec())