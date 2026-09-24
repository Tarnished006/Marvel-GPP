"""
seed_db.py — Sanketa Database Seeder
=========================================
Run this script once on any machine to build (or rebuild) sanketa.db
from scratch using the DICOM folders in your DICOM_ROOT.

Usage:
    python seed_db.py                          # Seed all known folders
    python seed_db.py --add path/to/new_scan   # Add a single new scan folder

Set DICOM_ROOT in config.py or as an environment variable before running.
See config.py for instructions.
"""

import os
import sys
import pydicom
from config import DICOM_ROOT, make_relative_path
from database import init_db, add_patient, add_scan, get_connection

# ─────────────────────────────────────────────────────────────────────────────
# Known scan folders — add new ones here as your library grows
# These are RELATIVE to DICOM_ROOT
# ─────────────────────────────────────────────────────────────────────────────
KNOWN_SCANS = [
    {
        "folder":      "skull",
        "name":        "Okafor, James",
        "mrn":         "MRN-847291",
        "description": "Head CT (Skull/Brain)",
        "modality":    "CT",
    },
    {
        "folder":      "DICOM",
        "name":        "Chen, Sarah",
        "mrn":         "MRN-229410",
        "description": "Lumbar Spine CT",
        "modality":    "CT",
    },
    {
        "folder":      "chest_real",
        "name":        "Torres, Maria",
        "mrn":         "MRN-119482",
        "description": "CT Chest (High Res)",
        "modality":    "CT",
    },
    {
        "folder":      "abdomen_real",
        "name":        "Kim, Jin",
        "mrn":         "MRN-774910",
        "description": "CT Abdomen/Pelvis",
        "modality":    "CT",
    },
    {
        "folder":      "shoulder_xray",
        "name":        "Smith, John",
        "mrn":         "XRAY-001",
        "description": "Shoulder X-Ray",
        "modality":    "CR",
    },
    {
        "folder":      "hand_xray",
        "name":        "Doe, Jane",
        "mrn":         "XRAY-002",
        "description": "Hand X-Ray",
        "modality":    "CR",
    },
    {
        "folder":      "chest_xray",
        "name":        "Williams, David",
        "mrn":         "XRAY-003",
        "description": "Chest X-Ray",
        "modality":    "CR",
    },
    {
        "folder":      "knee_xray",
        "name":        "Brown, Robert",
        "mrn":         "XRAY-004",
        "description": "Knee X-Ray",
        "modality":    "CR",
    }
]


def _count_dcm(folder_path: str) -> int:
    """Count DICOM files in a folder."""
    try:
        return len([f for f in os.listdir(folder_path) if f.lower().endswith((".dcm", ".ima"))])
    except Exception:
        return 0


def _already_ingested(mrn: str) -> bool:
    """Check if this MRN already has a scan in the DB."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM scans WHERE patient_mrn = ?", (mrn,)
        ).fetchone()
        conn.close()
        return row[0] > 0
    except Exception:
        return False


def seed_known_scans(force: bool = False):
    """Seed all known scans from KNOWN_SCANS list."""
    print(f"\n[INFO] DICOM_ROOT = {DICOM_ROOT}\n")
    init_db()

    seeded = 0
    skipped = 0
    missing = 0

    for entry in KNOWN_SCANS:
        folder_abs = os.path.join(DICOM_ROOT, entry["folder"])

        if not os.path.isdir(folder_abs):
            print(f"  [WARN] Skipping '{entry['folder']}' — folder not found in DICOM_ROOT")
            missing += 1
            continue

        if not force and _already_ingested(entry["mrn"]):
            print(f"  [OK] Already in DB: {entry['name']} ({entry['mrn']})")
            skipped += 1
            continue

        dcm_count = _count_dcm(folder_abs)
        if dcm_count == 0:
            print(f"  [WARN] Skipping '{entry['folder']}' — no .dcm files found")
            missing += 1
            continue

        add_patient(mrn=entry["mrn"], name=entry["name"], sex="U", age="Unk")
        add_scan(
            patient_mrn=entry["mrn"],
            modality=entry.get("modality", "CT"),
            study_date="2023-01-01",
            description=entry["description"],
            file_path=folder_abs,   # add_scan will convert this to relative automatically
            slice_count=dcm_count,
        )
        print(f"  [SEEDED] {entry['name']} ({dcm_count} slices) -> '{entry['folder']}'")
        seeded += 1

    print(f"\n{'-'*50}")
    print(f"Done. Seeded: {seeded}  |  Already existed: {skipped}  |  Missing: {missing}")
    print(f"{'-'*50}\n")


def add_new_scan(folder_path: str):
    """
    Add a single new DICOM folder to the database.
    Reads patient info directly from the DICOM headers.
    Usage: python seed_db.py --add /path/to/new_scan_folder
    """
    folder_abs = os.path.abspath(folder_path)

    if not os.path.isdir(folder_abs):
        print(f"❌ Folder not found: {folder_abs}")
        return

    dcm_files = [f for f in os.listdir(folder_abs) if f.lower().endswith((".dcm", ".ima"))]
    if not dcm_files:
        print(f"❌ No DICOM files found in: {folder_abs}")
        return

    # Read metadata from first slice
    try:
        ds = pydicom.dcmread(os.path.join(folder_abs, dcm_files[0]), stop_before_pixels=True)
        mrn  = str(getattr(ds, "PatientID",   f"IMP-{os.path.basename(folder_abs)[:8].upper()}")).strip()
        name = str(getattr(ds, "PatientName", "Imported Patient")).replace("^", " ").strip()
        sex  = str(getattr(ds, "PatientSex",  "U")).strip()
        age  = str(getattr(ds, "PatientAge",  "Unk")).strip()
        mod  = str(getattr(ds, "Modality",    "CT")).strip()
        desc = str(getattr(ds, "SeriesDescription", f"{mod} Scan")).strip()
        date = str(getattr(ds, "StudyDate",   "")).strip()
    except Exception as e:
        print(f"⚠️  Could not read DICOM header: {e}")
        mrn  = f"IMP-{os.path.basename(folder_abs)[:8].upper()}"
        name = "Imported Patient"
        sex, age, mod, desc, date = "U", "Unk", "CT", "Imported Scan", ""

    init_db()
    add_patient(mrn=mrn, name=name, sex=sex, age=age)
    add_scan(
        patient_mrn=mrn,
        modality=mod,
        study_date=date,
        description=desc,
        file_path=folder_abs,
        slice_count=len(dcm_files),
    )
    print(f"\n[ADDED] {name} (MRN: {mrn})")
    print(f"   Modality: {mod}  |  Slices: {len(dcm_files)}")
    print(f"   Folder:   {folder_abs}\n")


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--add" in sys.argv:
        idx = sys.argv.index("--add")
        if idx + 1 >= len(sys.argv):
            print("Usage: python seed_db.py --add <path/to/dicom/folder>")
            sys.exit(1)
        add_new_scan(sys.argv[idx + 1])
    elif "--force" in sys.argv:
        seed_known_scans(force=True)
    else:
        seed_known_scans()
