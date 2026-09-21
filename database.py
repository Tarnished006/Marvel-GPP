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
    if not age or str(age).strip() in ("U", "U U", "None", ""):
        return "Unk"
    digits = str(age).strip().upper().rstrip("YMD ")
    return str(int(digits)) if digits.isdigit() else digits

def _format_date(study_date: str) -> str:
    """DICOM StudyDate comes as YYYYMMDD -> return YYYY-MM-DD."""
    if study_date and len(study_date) == 8 and study_date.isdigit():
        return f"{study_date[0:4]}-{study_date[4:6]}-{study_date[6:8]}"
    return study_date or "Unknown Date"

def get_patients_for_ui() -> list[dict]:
    """Shaped for dashboard.py: includes latest scan data for the card."""
    result = []
    for p in get_all_patients():
        scans = get_scans_for_patient(p["mrn"])
        scan_count = len(scans)
        
        # Determine primary scan details for the card
        primary_scan = scans[0] if scans else {}
        modality = primary_scan.get("modality", "CT")
        slice_cnt = primary_scan.get("slice_count", 0)
        desc = primary_scan.get("description", "Scan")
        date = _format_date(primary_scan.get("study_date", ""))
        
        # Clean sex field
        sex = str(p["sex"]).strip() if p["sex"] else "U"
        if sex in ("U U", "None"): sex = "U"
        
        result.append({
            "mrn": p["mrn"],
            "name": p["name"],
            "age": _format_age(p["age"]),
            "sex": sex,
            "scans": scan_count,
            "scan_desc": desc,
            "scan_type": modality,
            "scan_date": date,
            "slice_count": slice_cnt,
            "_scan": {
                "type": modality,
                "date": date,
                "description": desc,
                "file_path": primary_scan.get("file_path", ""),
                "slice_count": slice_cnt,
            } if primary_scan else {}
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

def get_patient(mrn: str) -> dict | None:
    """Fetch single patient record by MRN."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM patients WHERE mrn = ?", (mrn,)).fetchone()
    conn.close()
    return dict(row) if row else None

def touch_patient(mrn: str):
    """Stamp last-viewed timestamp for a patient (safe if column absent)."""
    try:
        conn = get_connection()
        try:
            conn.execute("ALTER TABLE patients ADD COLUMN last_viewed TEXT")
            conn.commit()
        except Exception:
            pass
        conn.execute("UPDATE patients SET last_viewed = datetime('now') WHERE mrn = ?", (mrn,))
        conn.commit()
        conn.close()
    except Exception:
        pass

def log_action(action: str, mrn: str = None, details: str = ""):
    """Audit log entry for HIPAA / clinical compliance."""
    try:
        conn = get_connection()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS audit_log ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "  action TEXT,"
            "  mrn TEXT,"
            "  details TEXT"
            ")"
        )
        conn.execute(
            "INSERT INTO audit_log (action, mrn, details) VALUES (?, ?, ?)",
            (action, mrn, str(details))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[audit_log] {action} (mrn={mrn}): {details}")

def get_notes_for_patient(mrn: str) -> list[dict]:
    """Retrieve clinical notes for a patient."""
    try:
        conn = get_connection()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS notes ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  patient_mrn TEXT,"
            "  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "  author TEXT,"
            "  content TEXT,"
            "  FOREIGN KEY (patient_mrn) REFERENCES patients(mrn)"
            ")"
        )
        rows = conn.execute("SELECT * FROM notes WHERE patient_mrn = ?", (mrn,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []