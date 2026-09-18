# report_export.py
"""
Clinical Case Report Generator for Aegis-Touch / Marvel-GPP.
Generates a formal surgical/radiological case report as a PDF (and companion HTML)
incorporating patient demographics, scan metadata, calibrated measurements (Caliper & Cobb),
3D lesion/structure volumetrics, clinical notes, and scene snapshots.
"""

import os
import time
from datetime import datetime
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QTextDocument, QPdfWriter, QPageSize, QPageLayout
from PyQt6.QtCore import QMarginsF


def build_case_report(
    patient: dict,
    scan: dict = None,
    measurements: list = None,
    notes: list = None,
    screenshot_path: str = "",
) -> str:
    """
    Builds a clinical case report PDF and returns the file path.
    """
    os.makedirs("reports", exist_ok=True)

    mrn = patient.get("mrn", "UNKNOWN_MRN")
    name = patient.get("name", "Unknown Patient")
    age = patient.get("age", "N/A")
    sex = patient.get("sex", "N/A")
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    display_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    pdf_filename = f"Case_Report_{mrn}_{ts_str}.pdf"
    pdf_path = os.path.join("reports", pdf_filename)
    abs_pdf_path = os.path.abspath(pdf_path)

    # Scan details
    scan = scan or {}
    modality = scan.get("type") or scan.get("modality") or "CT"
    study_date = scan.get("date") or scan.get("study_date") or "N/A"
    description = scan.get("description") or "N/A"
    slice_count = scan.get("slice_count") or "N/A"

    # Measurements
    measurements = measurements or []
    meas_rows = ""
    if measurements:
        for m in measurements:
            plane = m.get("plane", "")
            m_type = m.get("type", "Caliper")
            label = m.get("label", "")
            val = m.get("value", "")
            meas_rows += f"""
            <tr>
                <td style="padding: 6px; border-bottom: 1px solid #ddd;">{label}</td>
                <td style="padding: 6px; border-bottom: 1px solid #ddd;">{m_type}</td>
                <td style="padding: 6px; border-bottom: 1px solid #ddd;">{plane}</td>
                <td style="padding: 6px; border-bottom: 1px solid #ddd; font-weight: bold; color: #006680;">{val}</td>
            </tr>
            """
    else:
        meas_rows = """
        <tr>
            <td colspan="4" style="padding: 8px; text-align: center; color: #888; font-style: italic;">
                No calibrated measurements recorded for this session.
            </td>
        </tr>
        """

    # Notes
    notes = notes or []
    notes_html = ""
    if notes:
        for n in notes:
            author = n.get("author", "Clinician")
            ts = n.get("timestamp", "")
            content = n.get("content", "")
            notes_html += f"""
            <div style="margin-bottom: 8px; padding: 6px 10px; background: #f8f9fa; border-left: 3px solid #00a0b0;">
                <div style="font-size: 11px; color: #666;"><b>{author}</b> · {ts}</div>
                <div style="font-size: 12px; margin-top: 3px;">{content}</div>
            </div>
            """
    else:
        notes_html = "<p style='color: #888; font-style: italic; font-size: 11px;'>No pre-existing clinical notes recorded.</p>"

    # Screenshot HTML
    screenshot_html = ""
    if screenshot_path and os.path.exists(screenshot_path):
        abs_shot = os.path.abspath(screenshot_path).replace("\\", "/")
        screenshot_html = f"""
        <div style="margin-top: 15px; text-align: center;">
            <h4 style="color: #333; margin-bottom: 8px; text-align: left;">3D / Multi-Planar Reconstruction Snapshot</h4>
            <img src="{abs_shot}" width="500" style="border: 1px solid #ccc; border-radius: 4px;" />
        </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Clinical Case Summary - {mrn}</title>
    </head>
    <body style="font-family: Arial, Helvetica, sans-serif; color: #222; margin: 20px; line-height: 1.4;">
        <table style="width: 100%; border-bottom: 2px solid #006680; padding-bottom: 8px; margin-bottom: 15px;">
            <tr>
                <td>
                    <h2 style="color: #006680; margin: 0;">AEGIS-TOUCH CLINICAL WORKSTATION</h2>
                    <div style="font-size: 11px; color: #666; letter-spacing: 0.5px;">STERILE TOUCHLESS OR / ICU DIAGNOSTIC PLATFORM</div>
                </td>
                <td style="text-align: right;">
                    <div style="font-size: 14px; font-weight: bold; color: #333;">CASE REPORT</div>
                    <div style="font-size: 11px; color: #666;">Date: {display_date}</div>
                </td>
            </tr>
        </table>

        <table style="width: 100%; margin-bottom: 15px; border-collapse: collapse;">
            <tr style="background: #f0f7f9;">
                <td style="padding: 10px; width: 50%; vertical-align: top; border: 1px solid #d0e4ea;">
                    <h4 style="margin: 0 0 8px 0; color: #006680;">PATIENT DEMOGRAPHICS</h4>
                    <table style="width: 100%; font-size: 12px;">
                        <tr><td style="color: #666; width: 80px;">Name:</td><td><b>{name}</b></td></tr>
                        <tr><td style="color: #666;">MRN:</td><td><b>{mrn}</b></td></tr>
                        <tr><td style="color: #666;">Age / Sex:</td><td>{age} / {sex}</td></tr>
                    </table>
                </td>
                <td style="padding: 10px; width: 50%; vertical-align: top; border: 1px solid #d0e4ea;">
                    <h4 style="margin: 0 0 8px 0; color: #006680;">IMAGING STUDY</h4>
                    <table style="width: 100%; font-size: 12px;">
                        <tr><td style="color: #666; width: 80px;">Modality:</td><td><b>{modality}</b></td></tr>
                        <tr><td style="color: #666;">Study Date:</td><td>{study_date}</td></tr>
                        <tr><td style="color: #666;">Series:</td><td>{description} ({slice_count} slices)</td></tr>
                    </table>
                </td>
            </tr>
        </table>

        <h4 style="color: #006680; margin: 15px 0 6px 0; border-bottom: 1px solid #ddd; padding-bottom: 4px;">
            CALIBRATED RADIOGRAPHIC MEASUREMENTS
        </h4>
        <table style="width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 15px;">
            <thead>
                <tr style="background: #e6f2f5; text-align: left;">
                    <th style="padding: 6px; border-bottom: 2px solid #006680;">Label</th>
                    <th style="padding: 6px; border-bottom: 2px solid #006680;">Modality</th>
                    <th style="padding: 6px; border-bottom: 2px solid #006680;">View</th>
                    <th style="padding: 6px; border-bottom: 2px solid #006680;">Measurement</th>
                </tr>
            </thead>
            <tbody>
                {meas_rows}
            </tbody>
        </table>

        <h4 style="color: #006680; margin: 15px 0 6px 0; border-bottom: 1px solid #ddd; padding-bottom: 4px;">
            CLINICAL & OR FINDINGS
        </h4>
        {notes_html}

        {screenshot_html}

        <div style="margin-top: 25px; border-top: 1px solid #ccc; padding-top: 8px; font-size: 10px; color: #888; text-align: center;">
            Generated by Aegis-Touch Touchless Surgical Suite · Calibrated with native DICOM pixel spacing · Confidential Medical Document
        </div>
    </body>
    </html>
    """

    _app = QApplication.instance() or QApplication([])
    doc = QTextDocument()
    doc.setHtml(html_content)

    writer = QPdfWriter(abs_pdf_path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter)
    writer.setResolution(300)

    doc.print(writer)

    html_path = os.path.splitext(abs_pdf_path)[0] + ".html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return abs_pdf_path
