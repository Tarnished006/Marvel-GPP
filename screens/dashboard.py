from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QComboBox, QLabel
)
from database import get_patients_for_ui
# screens/dashboard.py


class PatientCard(QFrame):
    view_records_clicked = pyqtSignal(dict)
    view_scans_clicked = pyqtSignal(dict)

    def __init__(self, patient: dict):
        super().__init__()
        self.patient = patient

        self.setObjectName("PatientCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)

        photo = QLabel("[photo]")
        photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        photo.setFixedHeight(60)

        name_label = QLabel(f"<b>{patient['name']}</b>")
        info_label = QLabel(
            f"MRN: {patient['mrn']} · {patient['age']} {patient['sex']} · {patient['scans']} scans"
        )

        records_btn = QPushButton("View Records")
        scans_btn = QPushButton("View Scans")

        records_btn.clicked.connect(lambda: self.view_records_clicked.emit(self.patient))
        scans_btn.clicked.connect(lambda: self.view_scans_clicked.emit(self.patient))

        for widget in (photo, name_label, info_label, records_btn, scans_btn):
            layout.addWidget(widget)

class Dashboard(QWidget):
    view_records_clicked = pyqtSignal(dict)
    view_scans_clicked = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)

        # --- Search + filter row ---
        search_row = QHBoxLayout()
        search_box = QLineEdit()
        search_box.setPlaceholderText("Search patients...")
        filter_box = QComboBox()
        filter_box.addItems(["All Patients"])
        search_row.addWidget(search_box)
        search_row.addWidget(filter_box)
        layout.addLayout(search_row)

        # --- Recent/flagged placeholder ---
        layout.addWidget(QLabel("RECENT / FLAGGED (placeholder)"))
        recent_row = QHBoxLayout()
        for _ in range(3):
            placeholder = QLabel("---")
            placeholder.setFrameShape(QFrame.Shape.StyledPanel)
            recent_row.addWidget(placeholder)
        layout.addLayout(recent_row)

        # --- Patient directory grid (loaded fresh from the DB each time) ---
        layout.addWidget(QLabel("PATIENT DIRECTORY"))
        grid = QGridLayout()
        patients = get_patients_for_ui()
        for index, patient in enumerate(patients):
            card = PatientCard(patient)
            card.view_records_clicked.connect(self.view_records_clicked.emit)
            card.view_scans_clicked.connect(self.view_scans_clicked.emit)
            row, col = divmod(index, 2)   # 2 cards per row
            grid.addWidget(card, row, col)
        layout.addLayout(grid)

if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    from theme import DARK_STYLESHEET

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = Dashboard()
    window.resize(700, 500)
    window.show()

    sys.exit(app.exec())