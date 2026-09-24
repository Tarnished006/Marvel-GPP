import sys
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QComboBox, QLabel, QFrame, QPushButton, QScrollArea,
    QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal

from dicom_engine import detect_scan_anatomy

try:
    from database import get_patients_for_ui
except Exception:
    def get_patients_for_ui():
        return []

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _local_scan_cards():
    """Return a list of patient dicts for any local DICOM folders not already in database."""
    cards = []
    known_paths = set()
    try:
        from database import get_connection
        conn = get_connection()
        rows = conn.execute("SELECT file_path FROM scans").fetchall()
        known_paths = {os.path.normpath(r[0]).lower() for r in rows if r[0]}
        conn.close()
    except Exception:
        pass

    try:
        for entry in os.listdir(_ROOT):
            path = os.path.join(_ROOT, entry)
            if os.path.isdir(path) and entry not in (".git", ".venv", ".cache", "__pycache__", "reports", "captures", "tests"):
                if os.path.normpath(path).lower() in known_paths:
                    continue
                dcm_count = sum(1 for f in os.listdir(path) if f.lower().endswith((".dcm", ".ima")))
                if dcm_count > 1:
                    preset = "skull" if "skull" in entry.lower() else "body"
                    display = f"{entry.replace('_', ' ').capitalize()} CT"
                    cards.append({
                        "_is_local": True,
                        "name": display,
                        "mrn": f"LOCAL-{entry.upper()[:8]}",
                        "age": "Unk",
                        "sex": "U",
                        "scans": dcm_count,
                        "scan_desc": f"{preset.capitalize()} CT",
                        "scan_date": "Local Dataset",
                        "slice_count": dcm_count,
                        "_scan": {
                            "type": display,
                            "date": "Local dataset",
                            "description": f"{dcm_count} DICOM slices",
                            "file_path": path,
                            "slice_count": dcm_count,
                        }
                    })
    except Exception:
        pass
    return cards


class PatientCard(QFrame):
    view_records_clicked  = pyqtSignal(dict)
    view_scans_clicked    = pyqtSignal(dict)
    view_3d_direct_clicked = pyqtSignal(dict, dict)   # patient, scan

    def __init__(self, patient: dict):
        super().__init__()
        self.patient = patient
        is_imported = patient.get("_is_imported", False)
        
        scan_desc = patient.get("scan_desc", patient.get("_scan", {}).get("description", "Unknown Scan"))
        scan_date = patient.get("scan_date", patient.get("_scan", {}).get("date", ""))
        slice_cnt = patient.get("slice_count", patient.get("scans", 1))
        
        is_3d = slice_cnt > 30 or is_imported

        self.setObjectName("PatientCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet('''
            QFrame#PatientCard {
                background-color: #0d0d0d;
                border: 1px solid #222;
                border-radius: 8px;
            }
            QFrame#PatientCard:hover {
                background-color: #161616;
                border: 1px solid #444;
            }
        ''')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        # Header badge
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

        # Info Line
        age_sex = f"{patient.get('age', 'Unk')} {patient.get('sex', 'U')}"
        info_str = f"{patient.get('mrn')}  ·  {age_sex}  ·  {scan_date}"
        if is_imported:
            info_str = f"{patient.get('mrn')}  ·  {slice_cnt} slices"
            
        info_label = QLabel(info_str)
        info_label.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(info_label)
        
        layout.addSpacing(6)
        
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        
        base_btn_style = (
            "QPushButton { background: #1a1a1a; color: #ddd; border: 1px solid #333; "
            "border-radius: 4px; padding: 6px; font-size: 11px; font-weight: bold; }"
            "QPushButton:hover { background: #2a2a2a; color: #fff; border-color: #555; }"
        )
        
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
        
        layout.addLayout(btn_layout)


class Dashboard(QWidget):
    view_records_clicked   = pyqtSignal(dict)
    view_scans_clicked     = pyqtSignal(dict)
    view_3d_direct_clicked = pyqtSignal(dict, dict)

    def __init__(self):
        super().__init__()
        self.imported_cards = []
        self.recent_history = []
        self.card_widgets = []

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Search row
        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search patients by name or MRN…")
        self.search_box.textChanged.connect(self._apply_filters)
        
        self.filter_box = QComboBox()
        self.filter_box.addItems(["All Patients", "Database Records", "Imported Scans"])
        self.filter_box.currentTextChanged.connect(self._apply_filters)
        
        search_row.addWidget(self.search_box)
        search_row.addWidget(self.filter_box)
        layout.addLayout(search_row)

        # Ingestion Panel
        self.ingestion_frame = QFrame()
        self.ingestion_frame.setObjectName("DicomIngestionFrame")
        self.ingestion_frame.setStyleSheet(
            "QFrame#DicomIngestionFrame { background: #0b1410; border: 1px solid #1a3d28; "
            "border-radius: 6px; padding: 6px; }"
        )
        ingest_layout = QVBoxLayout(self.ingestion_frame)
        ingest_layout.setSpacing(6)

        title_row = QHBoxLayout()
        lbl_ingest_title = QLabel("CLINICAL DICOM INGESTION & 3D/MPR GENERATION")
        lbl_ingest_title.setStyleSheet("color: #7cfc00; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;")
        title_row.addWidget(lbl_ingest_title)
        title_row.addStretch()

        self.lbl_ingest_status = QLabel("Ready to ingest patient DICOM CT/MRI scans")
        self.lbl_ingest_status.setStyleSheet("color: #888; font-size: 10px; font-style: italic;")
        title_row.addWidget(self.lbl_ingest_status)
        ingest_layout.addLayout(title_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        btn_import_dir = QPushButton("Import DICOM Folder")
        btn_import_dir.setFixedHeight(28)
        btn_import_dir.setStyleSheet(
            "QPushButton { background: #142318; color: #7cfc00; border: 1px solid #2e593a; "
            "border-radius: 4px; padding: 0 12px; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background: #1f3825; color: #a5ff4d; border-color: #448052; }"
        )
        btn_import_dir.clicked.connect(self._on_import_folder_clicked)
        action_row.addWidget(btn_import_dir)

        btn_import_files = QPushButton("Import DICOM File(s)")
        btn_import_files.setFixedHeight(28)
        btn_import_files.setStyleSheet(
            "QPushButton { background: #0f1c24; color: #00e5ff; border: 1px solid #1c455c; "
            "border-radius: 4px; padding: 0 12px; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background: #172d3b; color: #66efff; border-color: #2b6b8f; }"
        )
        btn_import_files.clicked.connect(self._on_import_files_clicked)
        action_row.addWidget(btn_import_files)

        self.btn_direct_launch = QPushButton("Generate 3D & MPR Model")
        self.btn_direct_launch.setFixedHeight(28)
        self.btn_direct_launch.setVisible(False)
        self.btn_direct_launch.setStyleSheet(
            "QPushButton { background: #003333; color: #00e5ff; border: 1px solid #008080; "
            "border-radius: 4px; padding: 0 14px; font-size: 11px; font-weight: 700; }"
            "QPushButton:hover { background: #004d4d; color: #ffffff; border-color: #00cccc; }"
        )
        action_row.addWidget(self.btn_direct_launch)
        action_row.addStretch()

        ingest_layout.addLayout(action_row)
        layout.addWidget(self.ingestion_frame)

        # Recent Activity row
        self.recent_title = QLabel("RECENTLY VIEWED")
        self.recent_title.setStyleSheet("color: #666; font-size: 10px; font-weight: 600;")
        layout.addWidget(self.recent_title)
        
        self.recent_row = QHBoxLayout()
        self._render_recents()
        layout.addLayout(self.recent_row)

        # Patient directory
        self.dir_label = QLabel("PATIENT DIRECTORY")
        self.dir_label.setStyleSheet("color: #666; font-size: 10px; font-weight: 600;")
        layout.addWidget(self.dir_label)

        # Container for scroll arrows and grid
        scroll_layout = QHBoxLayout()
        
        # Left-side gesture scroll buttons
        scroll_controls = QVBoxLayout()
        self.btn_scroll_up = QPushButton("▲")
        self.btn_scroll_up.setFixedSize(34, 60)
        self.btn_scroll_up.setStyleSheet("QPushButton { background: #161b22; color: #8b949e; border: 1px solid #30363d; border-radius: 4px; font-weight: bold; font-size: 16px; } QPushButton:hover { color: #00e5ff; border-color: #00e5ff; }")
        
        self.btn_scroll_down = QPushButton("▼")
        self.btn_scroll_down.setFixedSize(34, 60)
        self.btn_scroll_down.setStyleSheet("QPushButton { background: #161b22; color: #8b949e; border: 1px solid #30363d; border-radius: 4px; font-weight: bold; font-size: 16px; } QPushButton:hover { color: #00e5ff; border-color: #00e5ff; }")
        
        scroll_controls.addWidget(self.btn_scroll_up)
        scroll_controls.addStretch()
        scroll_controls.addWidget(self.btn_scroll_down)
        
        scroll_layout.addLayout(scroll_controls)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        # Hide standard scrollbars to enforce gesture button usage
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Wire up the scroll logic (adjust by 200px per pinch/click)
        self.btn_scroll_up.clicked.connect(lambda: self.scroll_area.verticalScrollBar().setValue(max(0, self.scroll_area.verticalScrollBar().value() - 200)))
        self.btn_scroll_down.clicked.connect(lambda: self.scroll_area.verticalScrollBar().setValue(min(self.scroll_area.verticalScrollBar().maximum(), self.scroll_area.verticalScrollBar().value() + 200)))


        self.scroll_content = QWidget()
        # Restore content direction to normal
        self.scroll_content.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        
        self.grid = QGridLayout(self.scroll_content)
        self.grid.setSpacing(10)
        self.grid.setContentsMargins(0, 0, 0, 245) # Extra bottom margin so camera HUD doesn't block cards

        self.scroll_area.setWidget(self.scroll_content)
        scroll_layout.addWidget(self.scroll_area, stretch=1)
        layout.addLayout(scroll_layout)

        self._refresh_patient_grid()

    def _apply_filters(self):
        query = self.search_box.text().lower()
        filter_type = self.filter_box.currentText()
        
        for card in self.card_widgets:
            patient = card.patient
            
            matches_search = (query in str(patient.get('name', '')).lower() or 
                              query in str(patient.get('mrn', '')).lower())
            
            is_imported = patient.get('_is_imported', False)
            
            matches_filter = True
            if filter_type == "Database Records":
                matches_filter = not is_imported
            elif filter_type == "Imported Scans":
                matches_filter = is_imported
            
            card._matches_search = (matches_search and matches_filter)
            card.setVisible(card._matches_search)
            
        self._relayout_grid()

    def _add_to_recents(self, patient):
        self.recent_history = [p for p in self.recent_history if p['mrn'] != patient['mrn']]
        self.recent_history.insert(0, patient)
        if len(self.recent_history) > 4:
            self.recent_history.pop()
        self._render_recents()
        
    def _render_recents(self):
        while self.recent_row.count():
            item = self.recent_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
        if not self.recent_history:
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
                
                def make_handler(p):
                    return lambda: self.view_3d_direct_clicked.emit(p, p.get('_scan', {}))
                
                btn.clicked.connect(make_handler(patient))
                self.recent_row.addWidget(btn)
            
            self.recent_row.addStretch()

    def _refresh_patient_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.card_widgets = []
        db_patients = get_patients_for_ui()
        if not db_patients:
            try:
                from ingest import seed_demo_database
                seed_demo_database()
                db_patients = get_patients_for_ui()
            except Exception:
                pass

        local_datasets = _local_scan_cards()
        all_cards = self.imported_cards + local_datasets + db_patients

        self.dir_label.setText(
            f"PATIENT DIRECTORY  ({len(all_cards)} total scans/patients)"
            + (f"  ·  {len(self.imported_cards)} IMPORTED" if self.imported_cards else "")
            + ("  ·  LOCAL 3D DATASETS AVAILABLE" if local_datasets else "")
        )

        if not all_cards:
            empty = QLabel(
                "No patients found in database.\n"
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
        
        visible_cards = [c for c in self.card_widgets if getattr(c, '_matches_search', True)]
        
        for i in reversed(range(self.grid.count())):
            item = self.grid.itemAt(i)
            if item and item.widget():
                self.grid.removeItem(item)
                
        for index, card in enumerate(visible_cards):
            row, col = divmod(index, cols)
            self.grid.addWidget(card, row, col)
            card.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_grid()

    def _on_import_folder_clicked(self):
        folder = QFileDialog.getExistingDirectory(self, "Select DICOM Directory", _ROOT)
        if folder:
            self.ingest_dicom_path(folder)

    def _on_import_files_clicked(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select DICOM Slice Files", _ROOT, "DICOM Files (*.dcm *.ima);;All Files (*)"
        )
        if files:
            folder = os.path.dirname(files[0])
            self.ingest_dicom_path(folder)

    def ingest_dicom_path(self, folder_path: str):
        if not os.path.isdir(folder_path):
            return
        dcm_files = [f for f in os.listdir(folder_path) if f.lower().endswith((".dcm", ".ima"))]
        if not dcm_files:
            QMessageBox.warning(
                self,
                "No DICOM Slices Found",
                f"No DICOM (.dcm / .ima) slice files were found in:\n{folder_path}"
            )
            return

        anatomy = detect_scan_anatomy(folder_path)
        pat_name = "Imported Patient"
        mrn = f"IMP-{os.path.basename(folder_path)[:8].upper()}"
        age = "Unk"
        sex = "U"
        modality = "CT"

        try:
            import pydicom
            ds = pydicom.dcmread(os.path.join(folder_path, dcm_files[0]), stop_before_pixels=True)
            raw_name = getattr(ds, "PatientName", None)
            if raw_name:
                pat_name = str(raw_name).replace("^", " ").strip() or "Imported Patient"
            mrn = str(getattr(ds, "PatientID", mrn)).strip() or mrn
            
            raw_age = str(getattr(ds, "PatientAge", age)).strip()
            if raw_age and raw_age not in ("U", "U U", "None"):
                age = raw_age.upper().rstrip("YMD ")
                if not age.isdigit(): age = raw_age
            else:
                age = "Unk"
                
            raw_sex = str(getattr(ds, "PatientSex", sex)).strip()
            if raw_sex and raw_sex not in ("U U", "None"):
                sex = raw_sex
            else:
                sex = "U"
                
            modality = str(getattr(ds, "Modality", modality)).strip() or modality
        except Exception as exc:
            pass

        dcm_count = len(dcm_files)
        scan_info = {
            "type": f"{anatomy.capitalize()} {modality}",
            "date": "Imported Scan",
            "description": f"{dcm_count} DICOM slices · {anatomy.capitalize()} {modality}",
            "file_path": folder_path,
            "slice_count": dcm_count,
        }
        card_info = {
            "_is_local": False,
            "_is_imported": True,
            "name": pat_name,
            "mrn": mrn,
            "age": age,
            "sex": sex,
            "scans": dcm_count,
            "scan_desc": f"{anatomy.capitalize()} {modality}",
            "scan_date": "Imported",
            "slice_count": dcm_count,
            "_scan": scan_info,
        }

        self.imported_cards.insert(0, card_info)
        self._refresh_patient_grid()

        self.lbl_ingest_status.setText(f"✔ Ready: {pat_name} · {dcm_count} slices ({anatomy.capitalize()} {modality})")
        self.btn_direct_launch.setText(f"Generate 3D & MPR Model for '{pat_name}'")
        self.btn_direct_launch.setVisible(True)
        try:
            self.btn_direct_launch.clicked.disconnect()
        except Exception:
            pass
        self.btn_direct_launch.clicked.connect(
            lambda: (self._add_to_recents(card_info), self.view_3d_direct_clicked.emit(card_info, scan_info))
        )
