# screens/study_info_panel.py
"""
DICOM Metadata and Study Information Panel for Aegis-Touch / Marvel-GPP.

Provides a polished, read-only technical summary of the currently loaded CT scan
without exposing sensitive patient-identifying information.

Privacy Safeguards:
- NEVER displays PatientName, PatientBirthDate, PatientAddress, PatientTelephone,
  AccessionNumber, ReferringPhysicianName, InstitutionName, or raw filenames.
- Prominently displays privacy protection and non-clinical inspection notices.
"""

import os
import re
import numpy as np
import pydicom
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QScrollArea, QGroupBox, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPalette


def _format_dicom_date(val: str) -> str:
    """Formats YYYYMMDD to YYYY-MM-DD."""
    if not val or not isinstance(val, str):
        return "Not available"
    val = val.strip()
    if len(val) == 8 and val.isdigit():
        return f"{val[:4]}-{val[4:6]}-{val[6:]}"
    return val or "Not available"


def _format_dicom_time(val: str) -> str:
    """Formats HHMMSS.frac to HH:MM:SS."""
    if not val or not isinstance(val, str):
        return "Not available"
    val = val.strip().split(".")[0]
    if len(val) >= 6 and val[:6].isdigit():
        return f"{val[:2]}:{val[2:4]}:{val[4:6]}"
    return val or "Not available"


def _format_orientation(iop) -> str:
    """Interprets ImageOrientationPatient vectors into descriptive text."""
    if iop is None:
        return "Not available"
    try:
        arr = [round(float(v), 2) for v in iop]
        if len(arr) == 6:
            if arr == [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]:
                return "Axial (Standard Transverse)"
            elif arr == [1.0, 0.0, 0.0, 0.0, 0.0, -1.0]:
                return "Coronal (Standard Frontal)"
            elif arr == [0.0, 1.0, 0.0, 0.0, 0.0, -1.0]:
                return "Sagittal (Standard Lateral)"
            return f"[{arr[0]}, {arr[1]}, {arr[2]}, {arr[3]}, {arr[4]}, {arr[5]}]"
    except Exception:
        pass
    return "Not available"


def extract_safe_metadata(viewer) -> dict:
    """Extracts non-sensitive technical metadata from the loaded scan and viewer state.
    Strictly excludes all patient, physician, and institutional identifiers.
    """
    meta = {
        # 1. Scan Information
        "modality": "Not available",
        "study_date": "Not available",
        "study_time": "Not available",
        "series_description": "Not available",
        "body_part": "Not available",
        "num_slices": "Not available",
        "series_status": "No scan loaded",

        # 2. Image Geometry
        "dimensions": "Not available",
        "rows": "Not available",
        "columns": "Not available",
        "slices": "Not available",
        "pixel_spacing": "Not available",
        "slice_thickness": "Not available",
        "spacing_between_slices": "Not available",
        "volume_dimensions_mm": "Not available",
        "orientation_info": "Not available",

        # 3. Intensity / HU Information
        "rescale_slope": "Not available",
        "rescale_intercept": "Not available",
        "hu_min": "Not available",
        "hu_max": "Not available",
        "current_window_width": "Not available",
        "current_window_level": "Not available",
        "current_wl_preset": "Not available",

        # 4. Current Viewer State
        "current_orientation": "Not available",
        "current_slice_index": "Not available",
        "current_physical_pos": "Not available",
        "heatmap_mode": "Disabled (Normal Bone)",
        "clipping_mode": "Disabled",
        "opacity_pct": "100%",
        "sync_mode": "Disabled",
        "measurement_count": "0",
    }

    if viewer is None:
        return meta

    # Retrieve cached DICOM header tags if available, or read first header once safely
    headers = getattr(viewer, "_safe_dicom_headers", None)
    active_path = getattr(viewer, "_active_path", "")

    if headers is None and active_path and os.path.isdir(active_path):
        try:
            dcm_files = [f for f in os.listdir(active_path) if f.lower().endswith(".dcm")]
            if dcm_files:
                # Read first slice header ONLY (stop_before_pixels=True uses <0.1ms and negligible RAM)
                first_path = os.path.join(active_path, sorted(dcm_files)[0])
                hdr = pydicom.dcmread(first_path, stop_before_pixels=True)
                headers = {
                    "Modality": getattr(hdr, "Modality", "CT"),
                    "StudyDate": getattr(hdr, "StudyDate", None),
                    "StudyTime": getattr(hdr, "StudyTime", None),
                    "SeriesDescription": getattr(hdr, "SeriesDescription", None),
                    "StudyDescription": getattr(hdr, "StudyDescription", None),
                    "BodyPartExamined": getattr(hdr, "BodyPartExamined", None),
                    "RescaleSlope": getattr(hdr, "RescaleSlope", 1.0),
                    "RescaleIntercept": getattr(hdr, "RescaleIntercept", 0.0),
                    "SpacingBetweenSlices": getattr(hdr, "SpacingBetweenSlices", None),
                    "ImageOrientationPatient": getattr(hdr, "ImageOrientationPatient", None),
                    "SliceCount": len(dcm_files),
                }
                viewer._safe_dicom_headers = headers
        except Exception:
            pass

    # Populate Scan Information from safe headers
    if headers:
        meta["modality"] = str(headers.get("Modality") or "CT").strip()
        meta["study_date"] = _format_dicom_date(str(headers.get("StudyDate") or ""))
        meta["study_time"] = _format_dicom_time(str(headers.get("StudyTime") or ""))

        s_desc = headers.get("SeriesDescription") or headers.get("StudyDescription")
        if s_desc:
            meta["series_description"] = str(s_desc).strip()

        bp = headers.get("BodyPartExamined")
        if bp and str(bp).strip() and str(bp).strip().upper() != "N/A":
            meta["body_part"] = str(bp).strip().upper()
        elif hasattr(viewer, "mpr_view") and getattr(viewer.mpr_view, "current_anatomy", None):
            meta["body_part"] = viewer.mpr_view.current_anatomy.upper()

        if headers.get("RescaleSlope") is not None:
            meta["rescale_slope"] = f"{float(headers['RescaleSlope']):.2f}"
        if headers.get("RescaleIntercept") is not None:
            meta["rescale_intercept"] = f"{float(headers['RescaleIntercept']):.1f} HU"

        if headers.get("SpacingBetweenSlices") is not None:
            try:
                meta["spacing_between_slices"] = f"{float(headers['SpacingBetweenSlices']):.2f} mm"
            except Exception:
                pass

        if headers.get("ImageOrientationPatient") is not None:
            meta["orientation_info"] = _format_orientation(headers.get("ImageOrientationPatient"))

        if headers.get("SliceCount"):
            meta["num_slices"] = f"{headers['SliceCount']} slices"

    # Volume data source (panel_2d or mpr_view)
    vol_data = None
    pixel_spacing = None
    slice_thickness = None

    if hasattr(viewer, "panel_2d") and getattr(viewer.panel_2d, "vol_data", None) is not None:
        vol_data = viewer.panel_2d.vol_data
        pixel_spacing = viewer.panel_2d.pixel_spacing
        slice_thickness = viewer.panel_2d.slice_thickness
    elif hasattr(viewer, "mpr_view") and getattr(viewer.mpr_view, "vol_data", None) is not None:
        vol_data = viewer.mpr_view.vol_data
        pixel_spacing = viewer.mpr_view.pixel_spacing
        slice_thickness = viewer.mpr_view.slice_thickness

    if vol_data is not None and vol_data.ndim == 3:
        H, W, D = vol_data.shape
        meta["series_status"] = "Loaded (Active Volume)"
        meta["rows"] = f"{H} px"
        meta["columns"] = f"{W} px"
        meta["slices"] = f"{D} slices"
        meta["dimensions"] = f"{H} × {W} × {D} voxels"
        if meta["num_slices"] == "Not available":
            meta["num_slices"] = f"{D} slices"

        if pixel_spacing:
            dy, dx = float(pixel_spacing[0]), float(pixel_spacing[1])
            meta["pixel_spacing"] = f"{dy:.2f} mm × {dx:.2f} mm (dy × dx)"
        else:
            dy, dx = 1.0, 1.0

        if slice_thickness:
            dz = float(slice_thickness)
            meta["slice_thickness"] = f"{dz:.2f} mm"
            if meta["spacing_between_slices"] == "Not available":
                meta["spacing_between_slices"] = f"{dz:.2f} mm"
        else:
            dz = 1.0

        span_x = W * dx
        span_y = H * dy
        span_z = D * dz
        meta["volume_dimensions_mm"] = f"{span_x:.1f} × {span_y:.1f} × {span_z:.1f} mm³"

        # Actual HU min and max from loaded volumetric array
        try:
            h_min = float(np.min(vol_data))
            h_max = float(np.max(vol_data))
            meta["hu_min"] = f"{h_min:.1f} HU"
            meta["hu_max"] = f"{h_max:.1f} HU"
        except Exception:
            pass

    elif getattr(viewer, "meshset", None) is not None:
        meta["series_status"] = "Loaded (3D Mesh)"
    else:
        meta["series_status"] = "No scan loaded"

    # Window / Level Information
    if hasattr(viewer, "panel_2d"):
        w = getattr(viewer.panel_2d, "window_width", None)
        l = getattr(viewer.panel_2d, "window_level", None)
        if w is not None:
            meta["current_window_width"] = f"{float(w):.0f} HU"
        if l is not None:
            meta["current_window_level"] = f"{float(l):.0f} HU"
        preset = getattr(viewer.panel_2d, "current_preset", "")
        if preset:
            meta["current_wl_preset"] = preset.capitalize()

    # Current Viewer State
    if hasattr(viewer, "panel_2d") and getattr(viewer.panel_2d, "vol_data", None) is not None:
        orient = getattr(viewer.panel_2d, "current_orientation", "axial")
        meta["current_orientation"] = orient.capitalize()

        idx = viewer.panel_2d.get_current_slice_index()
        cnt = viewer.panel_2d.get_slice_count()
        meta["current_slice_index"] = f"{idx + 1} / {cnt}"

        pos = viewer.panel_2d.get_current_physical_position()
        meta["current_physical_pos"] = f"X: {pos[0]:.1f} mm, Y: {pos[1]:.1f} mm, Z: {pos[2]:.1f} mm"

    # 3D Display States
    if getattr(viewer, "_density_active", False) or getattr(viewer, "_density_mode", False):
        meta["heatmap_mode"] = "Enabled (Hounsfield Heatmap)"
    else:
        meta["heatmap_mode"] = "Disabled (Normal Bone)"

    if getattr(viewer, "_clip_active", False):
        axis = str(getattr(viewer, "_clip_axis", "Y")).upper()
        frac = int(getattr(viewer, "_clip_fraction", 0.5) * 100)
        meta["clipping_mode"] = f"Enabled ({axis}-axis @ {frac}%)"
    else:
        meta["clipping_mode"] = "Disabled"

    op = getattr(viewer, "_bone_opacity", getattr(viewer, "_opacity", 1.0))
    meta["opacity_pct"] = f"{int(round(op * 100))}%"

    if getattr(viewer, "_sync_2d_3d_enabled", False):
        meta["sync_mode"] = "Enabled (2D↔3D Synchronized)"
    else:
        meta["sync_mode"] = "Disabled"

    # Measurement Counts
    cnt_2d = 0
    cnt_3d = 0
    if hasattr(viewer, "panel_2d"):
        cnt_2d = len(viewer.panel_2d.get_measurements())
    if hasattr(viewer, "get_measurements_3d"):
        cnt_3d = len(viewer.get_measurements_3d())
    total_m = cnt_2d + cnt_3d
    if total_m > 0:
        meta["measurement_count"] = f"2D: {cnt_2d} · 3D: {cnt_3d} (Total: {total_m})"
    else:
        meta["measurement_count"] = "0 active measurements"

    return meta


class StudyInfoDialog(QDialog):
    """Compact, dark-themed non-modal dialog displaying technical DICOM metadata
    and real-time viewer state while safeguarding patient privacy.
    """
    closed = pyqtSignal()

    def __init__(self, viewer=None, parent=None):
        super().__init__(parent or viewer)
        self.viewer = viewer
        self.setWindowTitle("📋 Study Information & Scan Metadata")
        self.setModal(False)
        self.resize(480, 620)
        self.setMinimumSize(420, 500)
        self.setStyleSheet("""
            QDialog {
                background-color: #0d0d0d;
                color: #e0e0e0;
                border: 1px solid #222222;
            }
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: #111;
                width: 6px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #333;
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #00e5ff;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        self.field_labels: dict[str, QLabel] = {}
        self._build_ui()
        self.update_metadata()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)

        # Header Bar
        header_row = QHBoxLayout()
        header_title = QLabel("📋 Scan Metadata & Study Details")
        header_title.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: bold;")
        header_row.addWidget(header_title)
        header_row.addStretch()

        btn_refresh = QPushButton("⟳ Refresh")
        btn_refresh.setFixedHeight(22)
        btn_refresh.setStyleSheet("""
            QPushButton {
                background: #181818; color: #aaa; border: 1px solid #333;
                border-radius: 3px; font-size: 9px; font-weight: 600; padding: 0 8px;
            }
            QPushButton:hover { background: #222; color: #00e5ff; border-color: #00b4d8; }
        """)
        btn_refresh.clicked.connect(lambda: self.update_metadata())
        header_row.addWidget(btn_refresh)

        btn_close_top = QPushButton("✕")
        btn_close_top.setFixedSize(22, 22)
        btn_close_top.setStyleSheet("""
            QPushButton {
                background: #181818; color: #888; border: 1px solid #333;
                border-radius: 3px; font-size: 10px; font-weight: bold;
            }
            QPushButton:hover { background: #331111; color: #ff5555; border-color: #aa2222; }
        """)
        btn_close_top.clicked.connect(self.hide)
        header_row.addWidget(btn_close_top)
        main_layout.addLayout(header_row)

        # Privacy Badge Banner
        privacy_box = QFrame()
        privacy_box.setStyleSheet("""
            QFrame {
                background-color: #06151c;
                border: 1px solid #0a3d4f;
                border-radius: 4px;
                padding: 6px;
            }
        """)
        p_layout = QHBoxLayout(privacy_box)
        p_layout.setContentsMargins(8, 4, 8, 4)
        lbl_privacy = QLabel("🔒 Patient-identifying information hidden for privacy.")
        lbl_privacy.setStyleSheet("color: #00e5ff; font-size: 10px; font-weight: 600;")
        p_layout.addWidget(lbl_privacy)
        main_layout.addWidget(privacy_box)

        # Scroll Area for sections
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 4, 0, 4)
        content_layout.setSpacing(12)

        # 1. Scan Information Group
        content_layout.addWidget(self._create_group(
            "🏥 SCAN INFORMATION",
            [
                ("Modality", "modality"),
                ("Study Date", "study_date"),
                ("Study Time", "study_time"),
                ("Series Description", "series_description"),
                ("Body Part Examined", "body_part"),
                ("Number of Slices", "num_slices"),
                ("Series Status", "series_status"),
            ]
        ))

        # 2. Image Geometry Group
        content_layout.addWidget(self._create_group(
            "📐 IMAGE GEOMETRY",
            [
                ("Voxel Matrix", "dimensions"),
                ("Matrix (R × C × S)", "rows"),
                ("Pixel Spacing", "pixel_spacing"),
                ("Slice Thickness", "slice_thickness"),
                ("Spacing Between Slices", "spacing_between_slices"),
                ("Physical Volume Span", "volume_dimensions_mm"),
                ("Orientation Reference", "orientation_info"),
            ]
        ))

        # 3. Intensity & Calibration Group
        content_layout.addWidget(self._create_group(
            "🌡 INTENSITY & HU CALIBRATION",
            [
                ("Rescale Slope", "rescale_slope"),
                ("Rescale Intercept", "rescale_intercept"),
                ("Volume HU Minimum", "hu_min"),
                ("Volume HU Maximum", "hu_max"),
                ("Window Width", "current_window_width"),
                ("Window Level (Center)", "current_window_level"),
                ("Active W/L Preset", "current_wl_preset"),
            ]
        ))

        # 4. Current Viewer State Group
        content_layout.addWidget(self._create_group(
            "🖥 CURRENT VIEWER STATE",
            [
                ("2D CT Orientation", "current_orientation"),
                ("Current Slice Index", "current_slice_index"),
                ("Physical Reference Pos", "current_physical_pos"),
                ("3D Heatmap Mode", "heatmap_mode"),
                ("3D Mesh Clipping", "clipping_mode"),
                ("Bone Mesh Opacity", "opacity_pct"),
                ("2D↔3D Synchronization", "sync_mode"),
                ("Active Measurements", "measurement_count"),
            ]
        ))

        scroll.setWidget(content_widget)
        main_layout.addWidget(scroll, stretch=1)

        # Non-Clinical Educational Disclaimer
        disclaimer_box = QFrame()
        disclaimer_box.setStyleSheet("""
            QFrame {
                background-color: #161208;
                border: 1px solid #3d2c0b;
                border-radius: 4px;
                padding: 6px;
            }
        """)
        d_layout = QHBoxLayout(disclaimer_box)
        d_layout.setContentsMargins(8, 4, 8, 4)
        lbl_disclaimer = QLabel(
            "⚠️ Patient-identifying fields are hidden. "
            "This application is for visualization and educational/demo purposes only, not diagnosis."
        )
        lbl_disclaimer.setWordWrap(True)
        lbl_disclaimer.setStyleSheet("color: #e5a530; font-size: 9px; font-weight: 500;")
        d_layout.addWidget(lbl_disclaimer)
        main_layout.addWidget(disclaimer_box)

        # Bottom Close Button
        btn_close = QPushButton("Close Metadata Panel")
        btn_close.setFixedHeight(26)
        btn_close.setStyleSheet("""
            QPushButton {
                background: #181818; color: #ccc; border: 1px solid #333;
                border-radius: 4px; font-size: 10px; font-weight: 600;
            }
            QPushButton:hover { background: #222; color: #fff; border-color: #444; }
        """)
        btn_close.clicked.connect(self.hide)
        main_layout.addWidget(btn_close)

    def _create_group(self, title: str, fields: list[tuple[str, str]]) -> QGroupBox:
        """Creates a styled collapsible-look QGroupBox with label/value pairs."""
        grp = QGroupBox(title)
        grp.setStyleSheet("""
            QGroupBox {
                background-color: #121212;
                border: 1px solid #202020;
                border-radius: 5px;
                margin-top: 18px;
                font-size: 10px;
                font-weight: bold;
                color: #00e5ff;
                padding: 14px 10px 8px 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 4px;
                background-color: #121212;
            }
        """)

        grid = QGridLayout(grp)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(5)

        for row, (label_text, key) in enumerate(fields):
            lbl_key = QLabel(label_text)
            lbl_key.setStyleSheet("color: #777777; font-size: 9px; font-weight: 600;")
            lbl_key.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

            lbl_val = QLabel("Not available")
            lbl_val.setStyleSheet("color: #e0e0e0; font-size: 9px; font-weight: 500;")
            lbl_val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lbl_val.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

            grid.addWidget(lbl_key, row, 0)
            grid.addWidget(lbl_val, row, 1)
            self.field_labels[key] = lbl_val

        return grp

    def update_metadata(self, metadata: dict = None, *args):
        """Refreshes all displayed fields with latest safe metadata."""
        if not isinstance(metadata, dict):
            metadata = extract_safe_metadata(self.viewer)

        self._cached_meta = metadata
        for key, lbl in self.field_labels.items():
            val = metadata.get(key, "Not available")
            lbl.setText(str(val) if val is not None else "Not available")

    def get_field_value(self, key: str) -> str:
        """Returns the current string displayed for a given field key."""
        if key in self.field_labels:
            return self.field_labels[key].text()
        return "Not available"

    def get_current_metadata(self) -> dict:
        """Returns the cached safe metadata dictionary."""
        return getattr(self, "_cached_meta", extract_safe_metadata(self.viewer))

    def hideEvent(self, event):
        super().hideEvent(event)
        self.closed.emit()

    def closeEvent(self, event):
        super().closeEvent(event)
        self.closed.emit()


# Alias for backwards compatibility / alternate naming
StudyInfoPanel = StudyInfoDialog
