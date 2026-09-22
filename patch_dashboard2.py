import codecs

with codecs.open('screens/dashboard.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Emojis & Text Polish
content = content.replace('"—"', '""')
content = content.replace('"— No recent activity —"', '"No recent activity"')
content = content.replace('📥 CLINICAL DICOM INGESTION & 3D/MPR GENERATION', 'CLINICAL DICOM INGESTION & 3D/MPR GENERATION')
content = content.replace('📁 Import DICOM Folder', 'Import DICOM Folder')
content = content.replace('📄 Import DICOM File(s)', 'Import DICOM File(s)')
content = content.replace('⚡ Generate 3D & MPR Model Now ->', 'Generate 3D & MPR Model')
content = content.replace('⚡ Generate 3D & MPR Model ->', 'Generate 3D & MPR Model')
content = content.replace('⚡ Generate 3D & MPR Model for \'{pat_name}\' ->', 'Generate 3D & MPR Model for \'{pat_name}\'')
content = content.replace('🕒 {patient[\'name\']}', '{patient[\'name\']}')

# 2. PatientCard Hover Styling
old_card_init = '''        self.setObjectName("PatientCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)'''

new_card_init = '''        self.setObjectName("PatientCard")
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
        """)'''
content = content.replace(old_card_init, new_card_init)

# 3. Dynamic Grid Logic
old_refresh = '''    def _refresh_patient_grid(self):
        """Clears and repopulates grid with imported scans, local datasets, and DB patients."""
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

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
            for index, patient in enumerate(all_cards):
                card = PatientCard(patient)
                card.view_records_clicked.connect(self.view_records_clicked.emit)
                card.view_scans_clicked.connect(self.view_scans_clicked.emit)
                card.view_3d_direct_clicked.connect(self.view_3d_direct_clicked.emit)
                row, col = divmod(index, 2)
                self.grid.addWidget(card, row, col)'''

new_refresh = '''    def _refresh_patient_grid(self):
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
            for patient in all_cards:
                card = PatientCard(patient)
                
                # Signal wrappers
                def make_scans_handler(pat):
                    def handler():
                        self._add_to_recents(pat)
                        self.view_scans_clicked.emit(pat)
                    return handler
                    
                def make_3d_handler(pat):
                    def handler(p, s):
                        self._add_to_recents(pat)
                        self.view_3d_direct_clicked.emit(p, s)
                    return handler
                    
                card.view_records_clicked.connect(self.view_records_clicked.emit)
                card.view_scans_clicked.connect(make_scans_handler(patient))
                card.view_3d_direct_clicked.connect(make_3d_handler(patient))
                
                self.card_widgets.append(card)
                
        self._relayout_grid()

    def _relayout_grid(self):
        if not hasattr(self, 'card_widgets') or not self.card_widgets:
            return
            
        # Determine cols based on width (each card is ~220px)
        width = self.scroll_area.viewport().width()
        cols = max(1, width // 230)
        
        # Reposition all visible widgets
        visible_cards = [c for c in self.card_widgets if c.isVisibleTo(self.scroll_content) or not c.isHidden()]
        
        # We need to remove them from layout first to move them safely
        for i in reversed(range(self.grid.count())):
            item = self.grid.itemAt(i)
            if item and item.widget():
                self.grid.removeItem(item)
                
        for index, card in enumerate(visible_cards):
            row, col = divmod(index, cols)
            self.grid.addWidget(card, row, col)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_grid()'''

content = content.replace(old_refresh, new_refresh)

# Fix filter applying relayout
content = content.replace('card.setVisible(matches_search and matches_filter)', 
                          'card.setVisible(matches_search and matches_filter)\\n        self._relayout_grid()')

with codecs.open('screens/dashboard.py', 'w', encoding='utf-8') as f:
    f.write(content)
