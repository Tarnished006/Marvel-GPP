# screens/or_icu_mode.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QPushButton, QFrame, QStackedWidget
)
from PyQt6.QtCore import Qt
from database import get_patients_for_ui, get_scans_for_ui




class PatientSelectCard(QFrame):
    def __init__(self, patient: dict, on_select):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{patient['name']}</b>"))
        layout.addWidget(QLabel(f"MRN: {patient['mrn']}"))
        layout.addWidget(QLabel(f"{patient['age']} {patient['sex']} - {patient['scans']} scans"))

        btn = QPushButton("Select")
        btn.clicked.connect(lambda: on_select(patient))
        layout.addWidget(btn)


class OrIcuMode(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)

        self.state_stack = QStackedWidget()
        outer.addWidget(self.state_stack)

        self.state_stack.addWidget(self._build_select_state())
        self.state_stack.addWidget(self._build_session_state())

        self.state_stack.setCurrentIndex(0)

    def _build_select_state(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        search_box = QLineEdit()
        search_box.setPlaceholderText("Search patient name or MRN...")
        layout.addWidget(search_box)

        grid = QGridLayout()
        patients = get_patients_for_ui()
        for index, patient in enumerate(patients):
            card = PatientSelectCard(patient, on_select=self.start_session)
            row, col = divmod(index, 3)
            grid.addWidget(card, row, col)
        layout.addLayout(grid)

        return page

    def _build_session_state(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)

        self.left_toggle_btn = QPushButton("<")
        self.left_toggle_btn.setFixedWidth(24)
        self.left_toggle_btn.clicked.connect(self._toggle_left)
        row.addWidget(self.left_toggle_btn)

        left = QVBoxLayout()
        header_row = QHBoxLayout()
        self.patient_name_label = QLabel("")
        switch_btn = QPushButton("Switch Patient")
        switch_btn.clicked.connect(self.switch_patient)
        header_row.addWidget(self.patient_name_label)
        header_row.addWidget(switch_btn)
        left.addLayout(header_row)

        self.patient_info_label = QLabel("")
        left.addWidget(self.patient_info_label)
        left.addWidget(QLabel("<b>RECORD</b>"))
        left.addWidget(QLabel("Allergies: NKDA"))
        left.addWidget(QLabel("Blood: O+"))
        left.addWidget(QLabel('Notes: "Post-op review, stable vitals..."'))
        left.addStretch()

        self.left_panel = QWidget()
        self.left_panel.setLayout(left)
        row.addWidget(self.left_panel, stretch=1)

        self.middle_render = QLabel("[ live 3D render ]")
        self.middle_render.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.middle_render.setFrameShape(QFrame.Shape.StyledPanel)
        row.addWidget(self.middle_render, stretch=3)

        self.right_sidebar_layout = QVBoxLayout()
        self.right_panel = QWidget()
        self.right_panel.setLayout(self.right_sidebar_layout)
        row.addWidget(self.right_panel, stretch=1)

        self.right_toggle_btn = QPushButton(">")
        self.right_toggle_btn.setFixedWidth(24)
        self.right_toggle_btn.clicked.connect(self._toggle_right)
        row.addWidget(self.right_toggle_btn)

        return page

    def _toggle_left(self):
        visible = self.left_panel.isVisible()
        self.left_panel.setVisible(not visible)
        self.left_toggle_btn.setText(">" if visible else "<")

    def _toggle_right(self):
        visible = self.right_panel.isVisible()
        self.right_panel.setVisible(not visible)
        self.right_toggle_btn.setText("<" if visible else ">")

    def start_session(self, patient: dict):
        self.current_patient = patient
        self.patient_name_label.setText(f"<b>{patient['name']}</b>")
        self.patient_info_label.setText(
            f"MRN: {patient['mrn']} - {patient['age']} {patient['sex']}"
        )

        while self.right_sidebar_layout.count():
            item = self.right_sidebar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.right_sidebar_layout.addWidget(QLabel("<b>SCANS</b>"))
        scans = get_scans_for_ui(patient["mrn"])
        for scan in scans:
            btn = QPushButton(f"[img] {scan['type']}")
            btn.clicked.connect(lambda checked, s=scan: self.show_scan_in_middle(s))
            self.right_sidebar_layout.addWidget(btn)

        if scans:
            self.show_scan_in_middle(scans[0])

        self.state_stack.setCurrentIndex(1)

    def show_scan_in_middle(self, scan: dict):
        self.middle_render.setText(f"[ live 3D render - {scan['type']} ]")

    def switch_patient(self):
        self.state_stack.setCurrentIndex(0)


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    from theme import DARK_STYLESHEET

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = OrIcuMode()
    window.resize(900, 600)
    window.show()

    sys.exit(app.exec())