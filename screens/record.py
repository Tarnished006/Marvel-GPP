# screens/record.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal

class PatientRecord(QWidget):
    view_scans_clicked = pyqtSignal(dict)

    def __init__(self, patient: dict):
        super().__init__()
        self.patient = patient
        layout = QVBoxLayout(self)

        # --- Header: photo + identity ---
        header = QHBoxLayout()
        photo = QLabel("[photo]")
        photo.setFixedSize(60, 60)
        photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        photo.setFrameShape(QFrame.Shape.StyledPanel)

        identity = QVBoxLayout()
        identity.addWidget(QLabel(f"<b>{patient['name']}</b>"))
        identity.addWidget(QLabel(
            f"MRN: {patient['mrn']} · {patient['age']} {patient['sex']} · {patient['scans']} scans"
        ))

        header.addWidget(photo)
        header.addLayout(identity)
        layout.addLayout(header)

        # --- Patient info ---
        layout.addWidget(QLabel("<b>PATIENT INFO</b>"))
        layout.addWidget(QLabel("Allergies: NKDA"))
        layout.addWidget(QLabel("Blood Type: O+"))
        layout.addWidget(QLabel("Admitted: 2026-07-10"))

        # --- Notes ---
        layout.addWidget(QLabel("<b>NOTES</b>"))
        self.notes_box = QTextEdit()
        self.notes_box.setPlaceholderText("Post-op review, stable vitals, continue current medication...")
        layout.addWidget(self.notes_box)

        notes_btn_row = QHBoxLayout()
        start_note_btn = QPushButton("Start Note")
        end_note_btn = QPushButton("End Note")
        notes_btn_row.addWidget(start_note_btn)
        notes_btn_row.addWidget(end_note_btn)
        layout.addLayout(notes_btn_row)

        # --- Forward nav ---
        view_scans_btn = QPushButton("View Scans →")
        view_scans_btn.clicked.connect(lambda: self.view_scans_clicked.emit(self.patient))
        layout.addWidget(view_scans_btn)


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    from theme import DARK_STYLESHEET

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    mock_patient = {"mrn": "84729-A", "name": "Doe, John", "age": 45, "sex": "M", "scans": 2}
    window = PatientRecord(mock_patient)
    window.resize(700, 600)
    window.show()

    sys.exit(app.exec())