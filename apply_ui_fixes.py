import codecs

# 1. Update theme.py to make scrollbars sleeker and smaller
with codecs.open('theme.py', 'r', encoding='utf-8') as f:
    theme_content = f.read()

scrollbar_css = """
QScrollBar:vertical {
    border: none;
    background: #121212;
    width: 8px;
    margin: 0px 0px 0px 0px;
}
QScrollBar::handle:vertical {
    background: #333333;
    min-height: 20px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #555555;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}
"""
if "QScrollBar" not in theme_content:
    with codecs.open('theme.py', 'a', encoding='utf-8') as f:
        f.write(scrollbar_css)

# 2. Shift CameraHUD over in main.py so it doesn't overlap
with codecs.open('main.py', 'r', encoding='utf-8') as f:
    main_content = f.read()

main_content = main_content.replace('MARGIN = 14          # gap from the window edges', 'MARGIN = 24          # gap from the window edges (clears scrollbar)')

with codecs.open('main.py', 'w', encoding='utf-8') as f:
    f.write(main_content)

# 3. Add dynamic Recent logic to dashboard.py
with codecs.open('screens/dashboard.py', 'r', encoding='utf-8') as f:
    dash_content = f.read()

# Replace __init__ recent row
old_recents = '''        # ── Recent / flagged row ──────────────────────────────────────────────
        layout.addWidget(QLabel("RECENT / FLAGGED"))
        recent_row = QHBoxLayout()
        for _ in range(3):
            ph = QLabel("")
            ph.setFrameShape(QFrame.Shape.StyledPanel)
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setFixedHeight(40)
            recent_row.addWidget(ph)
        layout.addLayout(recent_row)'''

new_recents = '''        # ── Recent activity row ──────────────────────────────────────────────
        self.recent_title = QLabel("RECENTLY VIEWED")
        self.recent_title.setStyleSheet("color: #666; font-size: 10px; font-weight: 600;")
        layout.addWidget(self.recent_title)
        
        self.recent_history = []
        self.recent_row = QHBoxLayout()
        self._render_recents()
        layout.addLayout(self.recent_row)'''

dash_content = dash_content.replace(old_recents, new_recents)

# Insert the methods right before _refresh_patient_grid
methods = '''    def _add_to_recents(self, patient):
        # Remove if already in history to move to front
        self.recent_history = [p for p in self.recent_history if p['mrn'] != patient['mrn']]
        self.recent_history.insert(0, patient)
        if len(self.recent_history) > 4:
            self.recent_history.pop()
        self._render_recents()
        
    def _render_recents(self):
        # Clear existing
        while self.recent_row.count():
            item = self.recent_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
        if not hasattr(self, 'recent_history') or not self.recent_history:
            ph = QLabel("No recent activity")
            ph.setStyleSheet("color: #444; font-style: italic; font-size: 12px;")
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setFixedHeight(40)
            self.recent_row.addWidget(ph)
        else:
            for patient in self.recent_history:
                btn = QPushButton(f"{patient['name']} ({patient['mrn']})")
                btn.setFixedHeight(40)
                btn.setStyleSheet(
                    "QPushButton { background: #1a1a1a; color: #00e5ff; border: 1px solid #333; "
                    "border-radius: 4px; padding: 5px 15px; font-size: 11px; font-weight: bold; }"
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
            
            self.recent_row.addStretch()

    '''

dash_content = dash_content.replace('    def _refresh_patient_grid(self):', methods + '    def _refresh_patient_grid(self):')

with codecs.open('screens/dashboard.py', 'w', encoding='utf-8') as f:
    f.write(dash_content)

print("Patch applied successfully.")
