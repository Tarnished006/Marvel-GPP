import codecs

with codecs.open('screens/dashboard.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if "def _on_import_files_clicked(self):" in line:
        skip = False
    if "def _refresh_patient_grid(self):" in line:
        skip = True
        
        # INSERT NEW LOGIC
        new_lines.append('''    def _refresh_patient_grid(self):
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
            for patient in all_cards:
                card = PatientCard(patient)
                
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
            
        width = self.scroll_area.viewport().width()
        cols = max(1, width // 230)
        
        visible_cards = [c for c in self.card_widgets if c.isVisibleTo(self.scroll_content) or not c.isHidden()]
        
        for i in reversed(range(self.grid.count())):
            item = self.grid.itemAt(i)
            if item and item.widget():
                self.grid.removeItem(item)
                
        for index, card in enumerate(visible_cards):
            row, col = divmod(index, cols)
            self.grid.addWidget(card, row, col)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_grid()

    def _on_import_folder_clicked(self):
        folder = QFileDialog.getExistingDirectory(self, "Select DICOM Directory", _ROOT)
        if folder:
            self.ingest_dicom_path(folder)

''')
    if not skip:
        new_lines.append(line)

with codecs.open('screens/dashboard.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
