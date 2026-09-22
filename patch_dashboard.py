import re
import codecs

with codecs.open('screens/dashboard.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update __init__ search row
old_search = '''        search_row = QHBoxLayout()
        search_box = QLineEdit()
        search_box.setPlaceholderText("Search patients…")
        filter_box = QComboBox()
        filter_box.addItems(["All Patients"])
        search_row.addWidget(search_box)
        search_row.addWidget(filter_box)
        layout.addLayout(search_row)'''

new_search = '''        self.recent_history = []
        
        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search patients by name or MRN…")
        self.search_box.textChanged.connect(self._apply_filters)
        
        self.filter_box = QComboBox()
        self.filter_box.addItems(["All Patients", "Local Datasets", "Database Records", "Imported Scans"])
        self.filter_box.currentTextChanged.connect(self._apply_filters)
        
        search_row.addWidget(self.search_box)
        search_row.addWidget(self.filter_box)
        layout.addLayout(search_row)'''

content = content.replace(old_search, new_search)

# 2. Update __init__ recents row
old_recents = '''        # ── Recent / flagged row ──────────────────────────────────────────────
        layout.addWidget(QLabel("RECENT / FLAGGED"))
        recent_row = QHBoxLayout()
        for _ in range(3):
            ph = QLabel("—")
            ph.setFrameShape(QFrame.Shape.StyledPanel)
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setFixedHeight(40)
            recent_row.addWidget(ph)
        layout.addLayout(recent_row)'''

new_recents = '''        # ── Recent activity row ──────────────────────────────────────────────
        self.recent_title = QLabel("RECENTLY VIEWED")
        self.recent_title.setStyleSheet("color: #888; font-size: 10px; font-weight: 600;")
        layout.addWidget(self.recent_title)
        
        self.recent_row = QHBoxLayout()
        self._render_recents()
        layout.addLayout(self.recent_row)'''

content = content.replace(old_recents, new_recents)

# 3. Add methods to Dashboard
methods = '''
    def _apply_filters(self):
        query = self.search_box.text().lower()
        filter_type = self.filter_box.currentText()
        
        for i in range(self.grid.count()):
            item = self.grid.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), PatientCard):
                card = item.widget()
                patient = card.patient
                
                # Check search query
                matches_search = (query in str(patient.get('name', '')).lower() or 
                                  query in str(patient.get('mrn', '')).lower())
                
                # Check filter type
                is_local = patient.get('_is_local', False)
                is_imported = patient.get('_is_imported', False)
                
                matches_filter = True
                if filter_type == "Local Datasets":
                    matches_filter = is_local and not is_imported
                elif filter_type == "Database Records":
                    matches_filter = not is_local and not is_imported
                elif filter_type == "Imported Scans":
                    matches_filter = is_imported
                
                card.setVisible(matches_search and matches_filter)

    def _add_to_recents(self, patient):
        # Remove if already in history to move to front
        self.recent_history = [p for p in self.recent_history if p['mrn'] != patient['mrn']]
        self.recent_history.insert(0, patient)
        if len(self.recent_history) > 3:
            self.recent_history.pop()
        self._render_recents()
        
    def _render_recents(self):
        # Clear existing
        while self.recent_row.count():
            item = self.recent_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
        if not self.recent_history:
            ph = QLabel("— No recent activity —")
            ph.setStyleSheet("color: #555; font-style: italic;")
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setFixedHeight(40)
            self.recent_row.addWidget(ph)
        else:
            for patient in self.recent_history:
                btn = QPushButton(f"🕒 {patient['name']} ({patient['mrn']})")
                btn.setFixedHeight(40)
                btn.setStyleSheet(
                    "QPushButton { background: #1a1a1a; color: #00e5ff; border: 1px solid #333; "
                    "border-radius: 4px; padding: 5px; font-size: 11px; }"
                    "QPushButton:hover { background: #222; border-color: #00e5ff; }"
                )
                
                # Capture current patient in closure
                def make_handler(p):
                    if p.get('_is_local') or p.get('_is_imported'):
                        return lambda: self.view_3d_direct_clicked.emit(p, p.get('_scan', {}))
                    else:
                        return lambda: self.view_scans_clicked.emit(p)
                
                btn.clicked.connect(make_handler(patient))
                self.recent_row.addWidget(btn)
            
            # Add stretch to left-align cards
            self.recent_row.addStretch()

'''

# Insert methods before _refresh_patient_grid
content = content.replace('    def _refresh_patient_grid(self):', methods + '    def _refresh_patient_grid(self):')

# 4. Update signal connection in _refresh_patient_grid
old_signals = '''                card.view_records_clicked.connect(self.view_records_clicked.emit)
                card.view_scans_clicked.connect(self.view_scans_clicked.emit)
                card.view_3d_direct_clicked.connect(self.view_3d_direct_clicked.emit)'''

new_signals = '''                card.view_records_clicked.connect(self.view_records_clicked.emit)
                
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
                
                card.view_scans_clicked.connect(make_scans_handler(patient))
                card.view_3d_direct_clicked.connect(make_3d_handler(patient))'''

content = content.replace(old_signals, new_signals)

# 5. Fix direct launch in ingest to also add to recents
old_direct = '''self.btn_direct_launch.clicked.connect(
            lambda: self.view_3d_direct_clicked.emit(card_info, scan_info)
        )'''
        
new_direct = '''self.btn_direct_launch.clicked.connect(
            lambda: (self._add_to_recents(card_info), self.view_3d_direct_clicked.emit(card_info, scan_info))
        )'''
        
content = content.replace(old_direct, new_direct)

with codecs.open('screens/dashboard.py', 'w', encoding='utf-8') as f:
    f.write(content)
