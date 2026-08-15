# database.py
import sqlite3

DB_PATH = "aegis.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    with open("schema.sql") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

def add_patient(mrn: str, name: str, sex: str, age: str):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO patients (mrn, name, sex, age) VALUES (?, ?, ?, ?)",
        (mrn, name, sex, age)
    )
    conn.commit()
    conn.close()

def add_scan(patient_mrn: str, modality: str, study_date: str, description: str, file_path: str, slice_count: int = 1):
    conn = get_connection()
    conn.execute(
        "INSERT INTO scans (patient_mrn, modality, study_date, description, file_path, slice_count) VALUES (?, ?, ?, ?, ?, ?)",
        (patient_mrn, modality, study_date, description, file_path, slice_count)
    )
    conn.commit()
    conn.close()

def get_all_patients() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM patients").fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_scans_for_patient(mrn: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM scans WHERE patient_mrn = ?", (mrn,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]

# --- UI adapters ---------------------------------------------------------
# Convert raw DB rows into the exact shapes the PyQt6 screens expect.

def _format_age(age: str) -> str:
    """DICOM PatientAge comes as e.g. '082Y' -> return '82'."""
    if not age:
        return ""
    digits = age.rstrip("YMD")
    return str(int(digits)) if digits.isdigit() else age

def _format_date(study_date: str) -> str:
    """DICOM StudyDate comes as YYYYMMDD -> return YYYY-MM-DD."""
    if study_date and len(study_date) == 8 and study_date.isdigit():
        return f"{study_date[0:4]}-{study_date[4:6]}-{study_date[6:8]}"
    return study_date or ""

def get_patients_for_ui() -> list[dict]:
    """Shaped for dashboard.py / or_icu_mode.py: name, mrn, age, sex, scans (count)."""
    result = []
    for p in get_all_patients():
        scan_count = len(get_scans_for_patient(p["mrn"]))
        result.append({
            "mrn": p["mrn"],
            "name": p["name"],
            "age": _format_age(p["age"]),
            "sex": p["sex"] or "",
            "scans": scan_count,
        })
    return result

def get_scans_for_ui(mrn: str) -> list[dict]:
    """Shaped for scans.py / viewer_3d.py / or_icu_mode.py: type, date (+ extras for later)."""
    return [
        {
            "type": s["modality"] or "CT",
            "date": _format_date(s["study_date"]),
            "description": s["description"],
            "file_path": s["file_path"],
            "slice_count": s["slice_count"],
        }
        for s in get_scans_for_patient(mrn)
    ]