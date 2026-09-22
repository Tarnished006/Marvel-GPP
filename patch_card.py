import codecs

with codecs.open('screens/dashboard.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update PatientCard UI
old_patient_card = '''class PatientCard(QFrame):
    view_records_clicked  = pyqtSignal(dict)
    view_scans_clicked    = pyqtSignal(dict)
    view_3d_direct_clicked = pyqtSignal(dict, dict)   # patient, scan

    def __init__(self, patient: dict):
        super().__init__()
        self.patient = patient
        is_imported = patient.get("_is_imported", False)
        is_local = patient.get("_is_local", False)

        self.setObjectName("PatientCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            QFrame#PatientCard {
                background-color: transparent;
                border: 1px solid #222;
                border-radius: 6px;
            }
            QFrame#PatientCard:hover {
                background-color: #1a1a1a;
                border: 1px solid #444;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # Header icon / badge
        badge_text = "[IMPORTED SCAN]" if is_imported else ("[LOCAL SCAN]" if is_local else "[PATIENT]")
        badge_color = "#00e5ff" if is_imported else ("#5a9" if is_local else "#777")
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            f"color: {badge_color}; font-size: 9px; font-weight: 700; letter-spacing: 0.8px;"
        )
        layout.addWidget(badge)

        name_label = QLabel(f"<b>{patient['name']}</b>")
        name_label.setStyleSheet("font-size: 13px;")
        layout.addWidget(name_label)

        info_label = QLabel(
            f"MRN: {patient['mrn']}  ·  {patient['scans']} slices"
            if (is_local or is_imported) else
            f"MRN: {patient['mrn']} · {patient['age']} {patient['sex']} · {patient['scans']} scans"
        )
        info_label.setStyleSheet("color: #777; font-size: 10px;")
        layout.addWidget(info_label)

        if is_local or is_imported:
            btn_label = "Generate 3D & MPR Model" if is_imported else "Open in 3D Viewer ->"
            open_btn = QPushButton(btn_label)
            btn_bg = "#002b33" if is_imported else "#1a2a1a"
            btn_fg = "#00e5ff" if is_imported else "#7cfc00"
            btn_border = "#006680" if is_imported else "#3a5a3a"
            open_btn.setStyleSheet(
                f"QPushButton {{ background: {btn_bg}; color: {btn_fg};"
                f" border: 1px solid {btn_border}; border-radius: 4px; padding: 5px; font-size: 11px; font-weight: 600; }}"
                "QPushButton:hover { background: #003d4d; color: #fff; }"
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
            layout.addWidget(scans_btn)'''

new_patient_card = '''class PatientCard(QFrame):
    view_records_clicked  = pyqtSignal(dict)
    view_scans_clicked    = pyqtSignal(dict)
    view_3d_direct_clicked = pyqtSignal(dict, dict)   # patient, scan

    def __init__(self, patient: dict):
        super().__init__()
        self.patient = patient
        is_imported = patient.get("_is_imported", False)
        
        # Determine scan details
        scan_desc = patient.get("scan_desc", patient.get("_scan", {}).get("description", "Unknown Scan"))
        scan_date = patient.get("scan_date", patient.get("_scan", {}).get("date", ""))
        slice_cnt = patient.get("slice_count", patient.get("scans", 1))
        
        # Decide if eligible for 3D (threshold ~30 slices)
        is_3d = slice_cnt > 30 or is_imported

        self.setObjectName("PatientCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            QFrame#PatientCard {
                background-color: #0d0d0d;
                border: 1px solid #222;
                border-radius: 8px;
            }
            QFrame#PatientCard:hover {
                background-color: #161616;
                border: 1px solid #444;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        # Header badge: Scan Description / Type
        badge_text = "IMPORTED DICOM" if is_imported else str(scan_desc).upper()
        badge_color = "#00e5ff" if is_imported else "#55aa99"
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            f"color: {badge_color}; font-size: 9px; font-weight: bold; letter-spacing: 0.5px;"
        )
        layout.addWidget(badge)

        # Name
        name_label = QLabel(f"<b>{patient.get('name', 'Unknown')}</b>")
        name_label.setStyleSheet("font-size: 14px; color: #ffffff;")
        layout.addWidget(name_label)

        # Info Line: MRN | Age Sex | Date
        age_sex = f"{patient.get('age', 'Unk')} {patient.get('sex', 'U')}"
        info_str = f"{patient.get('mrn')}  ·  {age_sex}  ·  {scan_date}"
        if is_imported:
            info_str = f"{patient.get('mrn')}  ·  {slice_cnt} slices"
            
        info_label = QLabel(info_str)
        info_label.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(info_label)
        
        # Spacer before buttons
        layout.addSpacing(6)
        
        # Buttons Row
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        
        # Base button styling
        base_btn_style = (
            "QPushButton { background: #1a1a1a; color: #ddd; border: 1px solid #333; "
            "border-radius: 4px; padding: 6px; font-size: 11px; font-weight: bold; }"
            "QPushButton:hover { background: #2a2a2a; color: #fff; border-color: #555; }"
        )
        
        # Primary button styling
        primary_btn_style = (
            "QPushButton { background: #112d22; color: #7cfc00; border: 1px solid #2e593a; "
            "border-radius: 4px; padding: 6px; font-size: 11px; font-weight: bold; }"
            "QPushButton:hover { background: #1a4233; color: #a5ff4d; border-color: #448052; }"
        )

        if is_3d:
            btn_3d = QPushButton("View in 3D")
            btn_3d.setStyleSheet(primary_btn_style)
            btn_3d.clicked.connect(lambda: self.view_3d_direct_clicked.emit(self.patient, self.patient.get("_scan", {})))
            btn_layout.addWidget(btn_3d)

        btn_2d = QPushButton("View in 2D")
        btn_2d.setStyleSheet(base_btn_style if is_3d else primary_btn_style)
        btn_2d.clicked.connect(lambda: self.view_scans_clicked.emit(self.patient))
        btn_layout.addWidget(btn_2d)
        
        layout.addLayout(btn_layout)'''

content = content.replace(old_patient_card, new_patient_card)

# 2. Update _refresh_patient_grid (remove local cards logic)
old_refresh = '''    def _refresh_patient_grid(self):
        """Clears and repopulates grid with imported scans, local datasets, and DB patients."""
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.card_widgets = []
        db_patients = get_patients_for_ui()
        local_datasets = _local_scan_cards()
        all_cards = self.imported_cards + local_datasets + db_patients

        self.dir_label.setText(
            f"PATIENT DIRECTORY  ({len(all_cards)} total scans/patients)"
            + (f"  ·  {len(self.imported_cards)} IMPORTED" if self.imported_cards else "")
            + ("  ·  LOCAL DATASETS AVAILABLE" if local_datasets else "")
        )

        if not all_cards:
            empty = QLabel(
                "No patients found and no local DICOM folders detected.\\n"
                "Use the 'Import DICOM Folder' button above to get started."
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #444; font-size: 12px;")
            self.grid.addWidget(empty, 0, 0)
        else:
            for patient in all_cards:'''

new_refresh = '''    def _refresh_patient_grid(self):
        """Clears and repopulates grid with imported scans and DB patients."""
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.card_widgets = []
        db_patients = get_patients_for_ui()
        all_cards = self.imported_cards + db_patients

        self.dir_label.setText(
            f"PATIENT DIRECTORY  ({len(all_cards)} total patients)"
            + (f"  ·  {len(self.imported_cards)} IMPORTED" if self.imported_cards else "")
        )

        if not all_cards:
            empty = QLabel(
                "No patients found in database.\\n"
                "Use the 'Import DICOM Folder' button above to get started."
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #444; font-size: 12px;")
            self.grid.addWidget(empty, 0, 0)
        else:
            for patient in all_cards:'''

content = content.replace(old_refresh, new_refresh)

# Also remove `def _local_scan_cards():` entirely if it exists to be fully clean, but not strictly necessary since it is no longer called.

with codecs.open('screens/dashboard.py', 'w', encoding='utf-8') as f:
    f.write(content)
