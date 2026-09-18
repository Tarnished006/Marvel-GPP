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

# --- Compatibility functions for main.py ------------------------------------

def get_patient(mrn: str) -> dict | None:
    """Return patient record matching mrn from patients table, or None if not found."""
    try:
        conn = get_connection()
        row = conn.execute("SELECT * FROM patients WHERE mrn = ?", (mrn,)).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None

def get_notes_for_patient(mrn: str) -> list[dict]:
    """Return notes for patient if notes table exists; empty list safely otherwise."""
    try:
        conn = get_connection()
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notes'"
        ).fetchone()
        if table_check:
            rows = conn.execute(
                "SELECT * FROM notes WHERE patient_mrn = ? ORDER BY created_at DESC", (mrn,)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        conn.close()
    except Exception:
        pass
    return []

def touch_patient(mrn: str):
    """Stamp a patient as just-viewed if schema supports it; harmless no-op otherwise."""
    try:
        conn = get_connection()
        cols = [info[1] for info in conn.execute("PRAGMA table_info(patients)").fetchall()]
        if "last_viewed_at" in cols:
            import datetime
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE patients SET last_viewed_at = ? WHERE mrn = ?", (now_str, mrn))
            conn.commit()
        conn.close()
    except Exception:
        pass

def log_action(action: str, patient_mrn: str = None, detail: str = ""):
    """Record an access/modification event if audit table exists; silent safe fallback otherwise."""
    try:
        conn = get_connection()
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('audit_log', 'activity_log')"
        ).fetchone()
        if table_check:
            table_name = table_check[0]
            import datetime
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                f"INSERT INTO {table_name} (patient_mrn, action, detail, created_at) VALUES (?, ?, ?, ?)",
                (patient_mrn, action, str(detail), now_str)
            )
            conn.commit()
        conn.close()
    except Exception:
        pass