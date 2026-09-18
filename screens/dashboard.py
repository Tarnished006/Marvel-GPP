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

# ── Discover local DICOM folders (same logic as viewer_3d) ────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _local_scan_cards():
    """Return a list of fake patient dicts for each local DICOM folder found."""
    cards = []
    for folder_name, display, preset in [
        ("skull", "Skull CT",  "skull"),
        ("DICOM", "Chest / Body CT", "body"),
    ]:
        path = os.path.join(_ROOT, folder_name)
        if os.path.isdir(path):
            dcm_count = sum(1 for f in os.listdir(path) if f.lower().endswith(".dcm"))
            if dcm_count > 1:
                cards.append({
                    # shaped like a real patient + scan so the same signal chain works
                    "_is_local": True,
                    "name":  display,
                    "mrn":   f"LOCAL-{folder_name.upper()}",
                    "age":   "—",
                    "sex":   "—",
                    "scans": dcm_count,
                    # scan dict embedded for direct 3D-viewer launch
                    "_scan": {
                        "type":        display,
                        "date":        "Local dataset",
                        "description": f"{dcm_count} DICOM slices",
                        "file_path":   path,
                        "slice_count": dcm_count,
                    },
                })
    return cards


class PatientCard(QFrame):
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

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # Header icon / badge
        badge_text = "[IMPORTED SCAN]" if is_imported else ("[LOCAL SCAN]" if is_local else "[PATIENT]")
        badge_color = "#00e5ff" if is_imported else ("#5a9" if is_local else "#777")
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            f"color: {badge_color}; font-size: 9px; font-weight: 700;"
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
            btn_label = "⚡ Generate 3D & MPR Model ->" if is_imported else "Open in 3D Viewer ->"
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
            layout.addWidget(scans_btn)


class Dashboard(QWidget):
    view_records_clicked   = pyqtSignal(dict)
    view_scans_clicked     = pyqtSignal(dict)
    view_3d_direct_clicked = pyqtSignal(dict, dict)

    def __init__(self):
        super().__init__()
        self.imported_cards: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Search + filter row ───────────────────────────────────────────────
        search_row = QHBoxLayout()
        search_box = QLineEdit()
        search_box.setPlaceholderText("Search patients…")
        filter_box = QComboBox()
        filter_box.addItems(["All Patients"])
        search_row.addWidget(search_box)
        search_row.addWidget(filter_box)
        layout.addLayout(search_row)

        # ── Clinical DICOM Ingestion Panel ────────────────────────────────────
        self.ingestion_frame = QFrame()
        self.ingestion_frame.setObjectName("DicomIngestionFrame")
        self.ingestion_frame.setStyleSheet(
            "QFrame#DicomIngestionFrame { background: #0b1410; border: 1px solid #1a3d28; "
            "border-radius: 6px; padding: 6px; }"
        )
        ingest_layout = QVBoxLayout(self.ingestion_frame)
        ingest_layout.setSpacing(6)

        title_row = QHBoxLayout()
        lbl_ingest_title = QLabel("📥 CLINICAL DICOM INGESTION & 3D/MPR GENERATION")
        lbl_ingest_title.setStyleSheet("color: #7cfc00; font-size: 11px; font-weight: 700;")
        title_row.addWidget(lbl_ingest_title)
        title_row.addStretch()

        self.lbl_ingest_status = QLabel("Ready to ingest patient DICOM CT/MRI scans")
        self.lbl_ingest_status.setStyleSheet("color: #888; font-size: 10px; font-style: italic;")
        title_row.addWidget(self.lbl_ingest_status)
        ingest_layout.addLayout(title_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        btn_import_dir = QPushButton("📁 Import DICOM Folder")
        btn_import_dir.setFixedHeight(28)
        btn_import_dir.setStyleSheet(
            "QPushButton { background: #142318; color: #7cfc00; border: 1px solid #2e593a; "
            "border-radius: 4px; padding: 0 12px; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background: #1f3825; color: #a5ff4d; border-color: #448052; }"
        )
        btn_import_dir.clicked.connect(self._on_import_folder_clicked)
        action_row.addWidget(btn_import_dir)

        btn_import_files = QPushButton("📄 Import DICOM File(s)")
        btn_import_files.setFixedHeight(28)
        btn_import_files.setStyleSheet(
            "QPushButton { background: #0f1c24; color: #00e5ff; border: 1px solid #1c455c; "
            "border-radius: 4px; padding: 0 12px; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background: #172d3b; color: #66efff; border-color: #2b6b8f; }"
        )
        btn_import_files.clicked.connect(self._on_import_files_clicked)
        action_row.addWidget(btn_import_files)

        self.btn_direct_launch = QPushButton("⚡ Generate 3D & MPR Model Now ->")
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

        # ── Recent / flagged row ──────────────────────────────────────────────
        layout.addWidget(QLabel("RECENT / FLAGGED"))
        recent_row = QHBoxLayout()
        for _ in range(3):
            ph = QLabel("—")
            ph.setFrameShape(QFrame.Shape.StyledPanel)
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setFixedHeight(40)
            recent_row.addWidget(ph)
        layout.addLayout(recent_row)

        # ── Patient directory + local datasets ────────────────────────────────
        self.dir_label = QLabel("PATIENT DIRECTORY")
        self.dir_label.setStyleSheet("color: #666; font-size: 10px; font-weight: 600;")
        layout.addWidget(self.dir_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.scroll_content = QWidget()
        self.grid = QGridLayout(self.scroll_content)
        self.grid.setSpacing(10)

        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

        self._refresh_patient_grid()

    def _refresh_patient_grid(self):
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
                "No patients found and no local DICOM folders detected.\n"
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
                self.grid.addWidget(card, row, col)

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
        """Ingests a folder of DICOM slices and creates interactive cards for 3D/MPR generation."""
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

        # Parse metadata from first DICOM slice
        anatomy = detect_scan_anatomy(folder_path)
        pat_name = "Imported Patient"
        mrn = f"IMP-{os.path.basename(folder_path)[:8].upper()}"
        age = "—"
        sex = "—"
        modality = "CT"

        try:
            import pydicom
            ds = pydicom.dcmread(os.path.join(folder_path, dcm_files[0]), stop_before_pixels=True)
            raw_name = getattr(ds, "PatientName", None)
            if raw_name:
                pat_name = str(raw_name).replace("^", " ").strip() or "Imported Patient"
            mrn = str(getattr(ds, "PatientID", mrn)).strip() or mrn
            age = str(getattr(ds, "PatientAge", age)).strip() or age
            sex = str(getattr(ds, "PatientSex", sex)).strip() or sex
            modality = str(getattr(ds, "Modality", modality)).strip() or modality
        except Exception as exc:
            print(f"[Dashboard] DICOM header parse info: {exc}")

        dcm_count = len(dcm_files)
        scan_info = {
            "type": f"{anatomy.capitalize()} {modality}",
            "date": "Imported Scan",
            "description": f"{dcm_count} DICOM slices · {anatomy.capitalize()} {modality}",
            "file_path": folder_path,
            "slice_count": dcm_count,
        }
        card_info = {
            "_is_local": True,
            "_is_imported": True,
            "name": f"{pat_name} ({anatomy.capitalize()})",
            "mrn": mrn,
            "age": age,
            "sex": sex,
            "scans": dcm_count,
            "_scan": scan_info,
        }

        # Prepend to imported cards
        self.imported_cards.insert(0, card_info)
        self._refresh_patient_grid()

        # Update status banner & direct launch button
        self.lbl_ingest_status.setText(f"✔ Ready: {pat_name} · {dcm_count} slices ({anatomy.capitalize()} {modality})")
        self.btn_direct_launch.setText(f"⚡ Generate 3D & MPR Model for '{pat_name}' ->")
        self.btn_direct_launch.setVisible(True)
        try:
            self.btn_direct_launch.clicked.disconnect()
        except Exception:
            pass
        self.btn_direct_launch.clicked.connect(
            lambda: self.view_3d_direct_clicked.emit(card_info, scan_info)
        )


if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication
    try:
        from theme import DARK_STYLESHEET
    except ImportError:
        DARK_STYLESHEET = ""

    app = QApplication(sys.argv)
    if DARK_STYLESHEET:
        app.setStyleSheet(DARK_STYLESHEET)
    window = Dashboard()
    window.resize(700, 500)
    window.show()
    sys.exit(app.exec())