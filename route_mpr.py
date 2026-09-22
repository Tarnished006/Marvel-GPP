import codecs

with codecs.open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('''    def show_scans(self, patient: dict):
        scans_screen = ScanGallery(patient)
        scans_screen.view_in_3d_clicked.connect(self.show_3d_viewer)
        self._push_screen(scans_screen)''', '''    def show_scans(self, patient: dict):
        # Open MPR instead of gallery
        from database import get_scans_for_ui
        scans = get_scans_for_ui(patient["mrn"])
        scan = scans[0] if scans else patient.get("_scan", {})
        
        self.viewer_3d.load_scan(patient, scan)
        self.viewer_3d.btn_3d_mode.setChecked(False)
        self.viewer_3d.btn_mpr_mode.setChecked(True)
        self.viewer_3d._switch_view_mode(1)
        self._go_root(self.viewer_3d)''')

# Ensure show_3d_direct defaults to 3D tab
content = content.replace('''    def show_3d_direct(self, patient: dict, scan: dict):
        """Called by local-scan cards on the dashboard — skip gallery, go straight to 3D."""
        self.viewer_3d.load_scan(patient, scan)
        self._go_root(self.viewer_3d)''', '''    def show_3d_direct(self, patient: dict, scan: dict):
        """Called by local-scan cards on the dashboard — skip gallery, go straight to 3D."""
        self.viewer_3d.load_scan(patient, scan)
        self.viewer_3d.btn_3d_mode.setChecked(True)
        self.viewer_3d.btn_mpr_mode.setChecked(False)
        self.viewer_3d._switch_view_mode(0)
        self._go_root(self.viewer_3d)''')

with codecs.open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)
