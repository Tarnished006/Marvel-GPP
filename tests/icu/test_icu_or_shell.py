# test_icu_or_shell.py
"""
Unit and integration tests for the ICU / OR Workspace Shell (Step 1).

Verifies:
  1. OrIcuMode initializes safely in headless environment.
  2. Empty state handling when no patient is loaded.
  3. set_patient_and_scan() correctly populates real patient and scan metadata.
  4. Zero fake clinical data ("NKDA", "O+", fake post-op notes) in the UI.
  5. Switching between ICU Mode and OR Mode preserves active patient/scan state.
  6. All 7 ICU workflow entry placeholders are present.
  7. All 9 OR workflow entry placeholders are present.
  8. "Open in 3D Viewer" emits open_in_viewer_requested with exact patient and scan.
  9. MainWindow navigation flow handoff preserves patient and scan.
"""

import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

import os
import pytest
from PyQt6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from screens.or_icu_mode import OrIcuMode, PatientSelectCard


@pytest.fixture(scope="session")
def qapp():
    from database import init_db
    init_db()
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def sample_patient():
    return {
        "mrn": "TEST-84729",
        "name": "Alex Mercer",
        "age": "45",
        "sex": "M",
        "scans": 2,
    }


@pytest.fixture
def sample_scan():
    return {
        "type": "Head CT",
        "date": "2026-08-15",
        "description": "Pre-operative Cranial CT",
        "file_path": "/fake/path/to/scan",
        "slice_count": 120,
    }


# ── Test 1: OrIcuMode initializes safely ─────────────────────────────────────
def test_1_or_icu_initializes_safely(qapp):
    widget = OrIcuMode()
    assert widget is not None
    assert widget.state_stack.count() == 2
    assert widget.state_stack.currentIndex() == 0  # starts at patient select if empty
    assert widget.current_patient == {}
    assert widget.current_scan == {}


# ── Test 2: Empty state handling ─────────────────────────────────────────────
def test_2_empty_state_handling(qapp):
    widget = OrIcuMode()
    widget.set_patient_and_scan(None, None)
    assert widget.state_stack.currentIndex() == 0
    assert widget.current_patient == {}


# ── Test 3: set_patient_and_scan populates real data ─────────────────────────
def test_3_set_patient_and_scan_populates_real_data(qapp, sample_patient, sample_scan):
    widget = OrIcuMode()
    widget.set_patient_and_scan(sample_patient, sample_scan)

    assert widget.state_stack.currentIndex() == 1  # active session
    assert widget.current_patient["mrn"] == "TEST-84729"
    assert widget.current_scan["type"] == "Head CT"

    # Check top bar text
    assert "Alex Mercer" in widget.top_patient_name.text()
    assert "TEST-84729" in widget.top_patient_meta.text()
    assert "Pre-operative Cranial CT" in widget.top_study_desc.text()
    assert "Head CT" in widget.top_study_meta.text()
    assert "2026-08-15" in widget.top_study_meta.text()
    assert "120 slices" in widget.top_study_meta.text()

    # Check card text
    assert "Alex Mercer" in widget.lbl_card_name.text()
    assert "TEST-84729" in widget.lbl_card_mrn.text()


# ── Test 4: Zero fake clinical data ──────────────────────────────────────────
def test_4_zero_fake_clinical_data(qapp, sample_patient, sample_scan):
    widget = OrIcuMode()
    widget.set_patient_and_scan(sample_patient, sample_scan)

    # Search entire widget for forbidden fake strings
    full_text = ""
    for label in widget.findChildren(type(widget.top_patient_name)):
        full_text += " " + label.text()

    assert "NKDA" not in full_text
    assert "Blood: O+" not in full_text
    assert "Post-op review, stable vitals" not in full_text
    assert "[ live 3D render ]" not in full_text


# ── Test 5: Mode switching preserves state ───────────────────────────────────
def test_5_mode_switching_preserves_state(qapp, sample_patient, sample_scan):
    widget = OrIcuMode()
    widget.set_patient_and_scan(sample_patient, sample_scan)

    # Default is ICU
    assert widget.current_mode == "ICU"
    assert widget.btn_icu_mode.isChecked() is True
    assert widget.btn_or_mode.isChecked() is False
    assert "ICU WORKFLOW" in widget.workflow_header.text()

    # Switch to OR
    widget.set_mode("OR")
    assert widget.current_mode == "OR"
    assert widget.btn_icu_mode.isChecked() is False
    assert widget.btn_or_mode.isChecked() is True
    assert "OR SURGICAL WORKFLOW" in widget.workflow_header.text()
    assert "Surgical Planning Workspace" in widget.center_title.text()

    # Patient & scan state remains intact
    assert widget.current_patient["mrn"] == "TEST-84729"
    assert widget.current_scan["slice_count"] == 120

    # Switch back to ICU
    widget.set_mode("ICU")
    assert widget.current_mode == "ICU"
    assert "ICU WORKFLOW" in widget.workflow_header.text()
    assert "Imaging Review Workspace" in widget.center_title.text()


# ── Test 6: All 7 ICU workflow entries present ───────────────────────────────
def test_6_all_icu_workflow_entries_present(qapp, sample_patient, sample_scan):
    widget = OrIcuMode()
    widget.set_patient_and_scan(sample_patient, sample_scan)
    widget.set_mode("ICU")

    expected_icu_items = [
        "Current vs Previous Scan",
        "Same-location Review",
        "Measurement Tracking",
        "Annotation Carry-forward",
        "What Changed?",
        "Device Markers",
        "Quick Handoff",
    ]

    item_texts = [
        label.text()
        for label in widget.workflow_container.findChildren(type(widget.top_patient_name))
    ]
    all_combined = " ".join(item_texts)

    for item in expected_icu_items:
        assert item in all_combined, f"Missing ICU workflow item: {item}"


# ── Test 7: All 9 OR workflow entries present ────────────────────────────────
def test_7_all_or_workflow_entries_present(qapp, sample_patient, sample_scan):
    widget = OrIcuMode()
    widget.set_patient_and_scan(sample_patient, sample_scan)
    widget.set_mode("OR")

    expected_or_items = [
        "Entry + Target",
        "Planned Route",
        "Structures to Avoid",
        "Surgical Corridor",
        "Virtual Instrument",
        "Live Deviation",
        "Plan Saving / Versions",
        "Before vs After",
        "Quick Surgical Views",
    ]

    item_texts = [
        label.text()
        for label in widget.workflow_container.findChildren(type(widget.top_patient_name))
    ]
    all_combined = " ".join(item_texts)

    for item in expected_or_items:
        assert item in all_combined, f"Missing OR workflow item: {item}"


# ── Test 8: Open in 3D Viewer signal ─────────────────────────────────────────
def test_8_open_in_3d_viewer_signal(qapp, sample_patient, sample_scan):
    widget = OrIcuMode()
    widget.set_patient_and_scan(sample_patient, sample_scan)

    received_payload = []

    def on_requested(pat, scn):
        received_payload.append((pat, scn))

    widget.open_in_viewer_requested.connect(on_requested)
    widget.btn_open_in_viewer.click()

    assert len(received_payload) == 1
    assert received_payload[0][0]["mrn"] == "TEST-84729"
    assert received_payload[0][1]["type"] == "Head CT"


# ── Test 9: MainWindow navigation handoff ───────────────────────────────────
def test_9_mainwindow_navigation_handoff(qapp, sample_patient, sample_scan):
    from main import MainWindow

    main_win = MainWindow()
    # Simulate scan loaded in viewer_3d
    main_win.viewer_3d.current_patient = sample_patient
    main_win.viewer_3d.current_scan = sample_scan

    # User clicks OR/ICU Mode
    main_win._open_or_icu()

    # Verify OrIcuMode received the exact same patient and scan
    assert main_win.stack.currentWidget() is main_win.or_icu_mode
    assert main_win.or_icu_mode.current_patient["mrn"] == "TEST-84729"
    assert main_win.or_icu_mode.current_scan["slice_count"] == 120
    assert "Alex Mercer" in main_win.or_icu_mode.top_patient_name.text()

    # User clicks "Open in 3D Viewer" from ICU/OR
    main_win.or_icu_mode.btn_open_in_viewer.click()

    # Verify MainWindow returns to 3D Viewer
    assert main_win.stack.currentWidget() is main_win.viewer_3d
    assert main_win.viewer_3d.current_patient["mrn"] == "TEST-84729"

    main_win.close()
