# database.py
import sqlite3
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Any, List, Dict

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
    try:
        conn = get_connection()
        rows = conn.execute("SELECT * FROM patients").fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception:
        return []

def get_scans_for_patient(mrn: str) -> list[dict]:
    try:
        conn = get_connection()
        rows = conn.execute("SELECT * FROM scans WHERE patient_mrn = ?", (mrn,)).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception:
        return []

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

def _normalize_study_date(study_date: str) -> str:
    """Normalizes various study date formats (YYYYMMDD, YYYY-MM-DD) into comparable YYYY-MM-DD."""
    if not study_date:
        return ""
    s = str(study_date).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s

def get_scans_for_ui(mrn: str) -> list[dict]:
    """Shaped for scans.py / viewer_3d.py / or_icu_mode.py: type, date (+ extras for later)."""
    return [
        {
            "id": s["id"],
            "patient_mrn": s["patient_mrn"],
            "type": s["modality"] or "CT",
            "modality": s["modality"] or "CT",
            "date": _format_date(s["study_date"]),
            "study_date": s["study_date"],
            "description": s["description"],
            "file_path": s["file_path"],
            "slice_count": s["slice_count"],
        }
        for s in get_scans_for_patient(mrn)
    ]

def get_historical_scans_for_patient(mrn: str, current_scan: dict) -> list[dict]:
    """Returns all scans for the patient that are chronologically earlier than current_scan.

    Ordered from most recent to oldest (descending).
    Requires both scans to belong to the same patient.
    """
    if not mrn or not current_scan:
        return []

    curr_mrn = str(current_scan.get("patient_mrn", "") or current_scan.get("mrn", "") or mrn).strip()
    if curr_mrn != str(mrn).strip():
        # Cross-patient pairing is strictly rejected
        return []

    curr_path = str(current_scan.get("file_path", "")).strip()
    curr_id = current_scan.get("id")
    curr_date_raw = current_scan.get("study_date") or current_scan.get("date") or ""
    curr_date = _normalize_study_date(curr_date_raw)

    all_scans = get_scans_for_ui(mrn)
    if not all_scans:
        return []

    def _scan_sort_key(s):
        d_raw = s.get("study_date") or s.get("date") or ""
        d_norm = _normalize_study_date(d_raw)
        s_id = s.get("id") or 0
        return (d_norm, s_id)

    sorted_scans = sorted(all_scans, key=_scan_sort_key)

    match_idx = -1
    for idx, s in enumerate(sorted_scans):
        if curr_path and s.get("file_path") == curr_path:
            match_idx = idx
            break
        if curr_id is not None and s.get("id") == curr_id:
            match_idx = idx
            break

    earlier_scans = []
    if match_idx >= 0:
        earlier_scans = sorted_scans[:match_idx]
    else:
        curr_key = (curr_date, curr_id or 0)
        earlier_scans = [s for s in sorted_scans if _scan_sort_key(s) < curr_key]

    return list(reversed(earlier_scans))

def get_previous_scan_for_patient(mrn: str, current_scan: Optional[dict] = None) -> Optional[dict]:
    """Returns the immediately previous scan for the patient, or None if no earlier scan exists."""
    if not mrn:
        return None
    if current_scan:
        historical = get_historical_scans_for_patient(mrn, current_scan)
        return historical[0] if historical else None

    # Fallback when current_scan is not specified: return second most recent study if available
    scans = get_scans_for_ui(mrn)
    if scans and len(scans) >= 2:
        return scans[1]
    return None

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


# --- Surgical Plan Versions Persistence (OR Mode: Feature 7) -------------

def _ensure_plan_versions_table(conn):
    """Defensively ensures surgical_plan_versions table exists."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS surgical_plan_versions ("
        "  id TEXT PRIMARY KEY,"
        "  scan_id TEXT NOT NULL,"
        "  patient_mrn TEXT,"
        "  name TEXT NOT NULL,"
        "  created_at TEXT NOT NULL,"
        "  updated_at TEXT NOT NULL,"
        "  snapshot_json TEXT NOT NULL,"
        "  notes TEXT DEFAULT '',"
        "  FOREIGN KEY (patient_mrn) REFERENCES patients(mrn)"
        ")"
    )

def save_surgical_plan_version(
    scan_id: str,
    name: str,
    snapshot: dict,
    version_id: Optional[str] = None,
    patient_mrn: Optional[str] = None,
    notes: str = ""
) -> dict:
    """Saves or updates a surgical plan version record in the database."""
    conn = get_connection()
    _ensure_plan_versions_table(conn)
    now_str = datetime.now(timezone.utc).isoformat()

    if version_id:
        existing = conn.execute("SELECT id, created_at FROM surgical_plan_versions WHERE id = ?", (str(version_id),)).fetchone()
        if existing:
            snapshot_json = json.dumps(snapshot)
            conn.execute(
                "UPDATE surgical_plan_versions SET name = ?, updated_at = ?, snapshot_json = ?, notes = ? WHERE id = ?",
                (name, now_str, snapshot_json, notes, str(version_id))
            )
            conn.commit()
            conn.close()
            return {
                "id": str(version_id),
                "scan_id": str(scan_id),
                "patient_mrn": patient_mrn,
                "name": name,
                "created_at": existing["created_at"],
                "updated_at": now_str,
                "snapshot": snapshot,
                "notes": notes,
            }

    new_id = str(version_id) if version_id else f"plan_{uuid.uuid4().hex[:8]}"
    snapshot_json = json.dumps(snapshot)
    conn.execute(
        "INSERT INTO surgical_plan_versions (id, scan_id, patient_mrn, name, created_at, updated_at, snapshot_json, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id, str(scan_id), patient_mrn, name, now_str, now_str, snapshot_json, notes)
    )
    conn.commit()
    conn.close()
    return {
        "id": new_id,
        "scan_id": str(scan_id),
        "patient_mrn": patient_mrn,
        "name": name,
        "created_at": now_str,
        "updated_at": now_str,
        "snapshot": snapshot,
        "notes": notes,
    }

def get_surgical_plan_versions(scan_id: str) -> list[dict]:
    """Retrieves all surgical plan versions associated with a scan, ordered chronologically."""
    try:
        conn = get_connection()
        _ensure_plan_versions_table(conn)
        rows = conn.execute(
            "SELECT * FROM surgical_plan_versions WHERE scan_id = ? ORDER BY created_at ASC",
            (str(scan_id),)
        ).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["snapshot"] = json.loads(d["snapshot_json"])
            except Exception:
                d["snapshot"] = {}
            results.append(d)
        return results
    except Exception:
        return []

def get_surgical_plan_version(version_id: str) -> Optional[dict]:
    """Retrieves a single surgical plan version by its ID."""
    try:
        conn = get_connection()
        _ensure_plan_versions_table(conn)
        row = conn.execute(
            "SELECT * FROM surgical_plan_versions WHERE id = ?",
            (str(version_id),)
        ).fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        try:
            d["snapshot"] = json.loads(d["snapshot_json"])
        except Exception:
            d["snapshot"] = {}
        return d
    except Exception:
        return None

def delete_surgical_plan_version(version_id: str) -> bool:
    """Deletes a surgical plan version by its ID."""
    try:
        conn = get_connection()
        _ensure_plan_versions_table(conn)
        cursor = conn.execute("DELETE FROM surgical_plan_versions WHERE id = ?", (str(version_id),))
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted
    except Exception:
        return False

def rename_surgical_plan_version(version_id: str, new_name: str) -> bool:
    """Renames an existing surgical plan version."""
    try:
        conn = get_connection()
        _ensure_plan_versions_table(conn)
        now_str = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "UPDATE surgical_plan_versions SET name = ?, updated_at = ? WHERE id = ?",
            (new_name, now_str, str(version_id))
        )
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        return updated
    except Exception:
        return False


# --- Tracked Measurements Persistence (ICU Mode: Feature 3) -------------

def _ensure_tracked_measurements_table(conn):
    """Defensively ensures tracked_measurements table exists."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tracked_measurements ("
        "  id TEXT PRIMARY KEY,"
        "  scan_id TEXT NOT NULL,"
        "  patient_mrn TEXT NOT NULL,"
        "  label TEXT NOT NULL,"
        "  value_mm REAL NOT NULL,"
        "  unit TEXT DEFAULT 'mm',"
        "  created_at TEXT NOT NULL,"
        "  metadata_json TEXT DEFAULT '{}',"
        "  FOREIGN KEY (patient_mrn) REFERENCES patients(mrn)"
        ")"
    )

def save_tracked_measurement(
    measurement_id: str,
    scan_id: str,
    patient_mrn: str,
    label: str,
    value_mm: float,
    unit: str = "mm",
    metadata: Optional[dict] = None
) -> dict:
    """Saves or updates a tracked measurement in the database."""
    conn = get_connection()
    _ensure_tracked_measurements_table(conn)
    now_str = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(metadata or {})
    conn.execute(
        "INSERT OR REPLACE INTO tracked_measurements (id, scan_id, patient_mrn, label, value_mm, unit, created_at, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(measurement_id), str(scan_id), str(patient_mrn), str(label), float(value_mm), str(unit), now_str, meta_json)
    )
    conn.commit()
    conn.close()
    return {
        "id": str(measurement_id),
        "scan_id": str(scan_id),
        "patient_mrn": str(patient_mrn),
        "label": str(label),
        "value_mm": float(value_mm),
        "unit": str(unit),
        "created_at": now_str,
        "metadata": metadata or {}
    }

def get_tracked_measurements_for_scan(scan_id: str, patient_mrn: Optional[str] = None) -> list[dict]:
    """Retrieves all tracked measurements for a scan and optionally patient MRN."""
    try:
        conn = get_connection()
        _ensure_tracked_measurements_table(conn)
        if patient_mrn:
            rows = conn.execute(
                "SELECT * FROM tracked_measurements WHERE scan_id = ? AND patient_mrn = ? ORDER BY created_at ASC",
                (str(scan_id), str(patient_mrn))
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tracked_measurements WHERE scan_id = ? ORDER BY created_at ASC",
                (str(scan_id),)
            ).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d.get("metadata_json", "{}"))
            except Exception:
                d["metadata"] = {}
            results.append(d)
        return results
    except Exception:
        return []

def get_tracked_measurements_for_patient(patient_mrn: str) -> list[dict]:
    """Retrieves all tracked measurements for a patient MRN."""
    try:
        conn = get_connection()
        _ensure_tracked_measurements_table(conn)
        rows = conn.execute(
            "SELECT * FROM tracked_measurements WHERE patient_mrn = ? ORDER BY created_at ASC",
            (str(patient_mrn),)
        ).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d.get("metadata_json", "{}"))
            except Exception:
                d["metadata"] = {}
            results.append(d)
        return results
    except Exception:
        return []

def delete_tracked_measurement(measurement_id: str, patient_mrn: Optional[str] = None) -> bool:
    """Deletes a tracked measurement by ID, optionally scoped to patient MRN."""
    try:
        conn = get_connection()
        _ensure_tracked_measurements_table(conn)
        if patient_mrn:
            cursor = conn.execute(
                "DELETE FROM tracked_measurements WHERE id = ? AND patient_mrn = ?",
                (str(measurement_id), str(patient_mrn))
            )
        else:
            cursor = conn.execute(
                "DELETE FROM tracked_measurements WHERE id = ?",
                (str(measurement_id),)
            )
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted
    except Exception:
        return False


# --- Scan Annotations Persistence (ICU Mode: Feature 4) -----------------

def _ensure_scan_annotations_table(conn):
    """Defensively ensures scan_annotations table exists."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS scan_annotations ("
        "  id TEXT PRIMARY KEY,"
        "  patient_mrn TEXT NOT NULL,"
        "  scan_id TEXT NOT NULL,"
        "  label TEXT NOT NULL,"
        "  text TEXT DEFAULT '',"
        "  physical_x_mm REAL NOT NULL,"
        "  physical_y_mm REAL NOT NULL,"
        "  physical_z_mm REAL NOT NULL,"
        "  created_at TEXT NOT NULL,"
        "  metadata_json TEXT DEFAULT '{}',"
        "  FOREIGN KEY (patient_mrn) REFERENCES patients(mrn)"
        ")"
    )

def save_scan_annotation(
    annotation_id: str,
    patient_mrn: str,
    scan_id: str,
    label: str,
    text: str = "",
    physical_x_mm: float = 0.0,
    physical_y_mm: float = 0.0,
    physical_z_mm: float = 0.0,
    metadata: Optional[dict] = None
) -> dict:
    """Saves or updates a spatial scan annotation in the database."""
    conn = get_connection()
    _ensure_scan_annotations_table(conn)
    now_str = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(metadata or {})
    conn.execute(
        "INSERT OR REPLACE INTO scan_annotations ("
        "  id, patient_mrn, scan_id, label, text, physical_x_mm, physical_y_mm, physical_z_mm, created_at, metadata_json"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(annotation_id), str(patient_mrn), str(scan_id), str(label), str(text),
            float(physical_x_mm), float(physical_y_mm), float(physical_z_mm), now_str, meta_json
        )
    )
    conn.commit()
    conn.close()
    return {
        "id": str(annotation_id),
        "patient_mrn": str(patient_mrn),
        "scan_id": str(scan_id),
        "label": str(label),
        "text": str(text),
        "physical_x_mm": float(physical_x_mm),
        "physical_y_mm": float(physical_y_mm),
        "physical_z_mm": float(physical_z_mm),
        "created_at": now_str,
        "metadata": metadata or {}
    }

def get_scan_annotations_for_scan(scan_id: str, patient_mrn: Optional[str] = None) -> list[dict]:
    """Retrieves all annotations for a scan and optionally patient MRN."""
    try:
        conn = get_connection()
        _ensure_scan_annotations_table(conn)
        if patient_mrn:
            rows = conn.execute(
                "SELECT * FROM scan_annotations WHERE scan_id = ? AND patient_mrn = ? ORDER BY created_at ASC",
                (str(scan_id), str(patient_mrn))
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scan_annotations WHERE scan_id = ? ORDER BY created_at ASC",
                (str(scan_id),)
            ).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d.get("metadata_json", "{}"))
            except Exception:
                d["metadata"] = {}
            results.append(d)
        return results
    except Exception:
        return []

def get_scan_annotations_for_patient(patient_mrn: str) -> list[dict]:
    """Retrieves all annotations for a patient MRN."""
    try:
        conn = get_connection()
        _ensure_scan_annotations_table(conn)
        rows = conn.execute(
            "SELECT * FROM scan_annotations WHERE patient_mrn = ? ORDER BY created_at ASC",
            (str(patient_mrn),)
        ).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d.get("metadata_json", "{}"))
            except Exception:
                d["metadata"] = {}
            results.append(d)
        return results
    except Exception:
        return []

def delete_scan_annotation(annotation_id: str, patient_mrn: Optional[str] = None) -> bool:
    """Deletes a scan annotation by ID, optionally scoped to patient MRN."""
    try:
        conn = get_connection()
        _ensure_scan_annotations_table(conn)
        if patient_mrn:
            cursor = conn.execute(
                "DELETE FROM scan_annotations WHERE id = ? AND patient_mrn = ?",
                (str(annotation_id), str(patient_mrn))
            )
        else:
            cursor = conn.execute(
                "DELETE FROM scan_annotations WHERE id = ?",
                (str(annotation_id),)
            )
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted
    except Exception:
        return False


# --- Device Markers Persistence (ICU Mode: Feature 6) -------------------

def _ensure_device_markers_table(conn):
    """Defensively ensures device_markers table exists."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS device_markers ("
        "  id TEXT PRIMARY KEY,"
        "  patient_mrn TEXT NOT NULL,"
        "  scan_id TEXT NOT NULL,"
        "  device_type TEXT NOT NULL,"
        "  label TEXT NOT NULL,"
        "  physical_x_mm REAL NOT NULL,"
        "  physical_y_mm REAL NOT NULL,"
        "  physical_z_mm REAL NOT NULL,"
        "  created_at TEXT NOT NULL,"
        "  metadata_json TEXT DEFAULT '{}',"
        "  FOREIGN KEY (patient_mrn) REFERENCES patients(mrn)"
        ")"
    )

def save_device_marker(
    marker_id_or_data: Any,
    patient_mrn: Optional[str] = None,
    scan_id: Optional[str] = None,
    device_type: Optional[str] = None,
    label: Optional[str] = None,
    physical_x_mm: float = 0.0,
    physical_y_mm: float = 0.0,
    physical_z_mm: float = 0.0,
    metadata: Optional[dict] = None
) -> dict:
    """Saves or updates a device marker in the database. Supports dict, model object, or keyword arguments."""
    conn = get_connection()
    _ensure_device_markers_table(conn)

    if isinstance(marker_id_or_data, dict):
        d = marker_id_or_data
        m_id = str(d.get("id", ""))
        p_mrn = str(d.get("patient_mrn", ""))
        s_id = str(d.get("scan_id", ""))
        d_type = str(d.get("device_type", "Other"))
        lbl = str(d.get("label", "Device"))
        x = float(d.get("physical_x_mm", 0.0))
        y = float(d.get("physical_y_mm", 0.0))
        z = float(d.get("physical_z_mm", 0.0))
        meta = d.get("metadata", {})
    elif hasattr(marker_id_or_data, "id") and hasattr(marker_id_or_data, "patient_mrn"):
        obj = marker_id_or_data
        m_id = str(obj.id)
        p_mrn = str(obj.patient_mrn)
        s_id = str(obj.scan_id)
        d_type = str(getattr(obj, "device_type", "Other"))
        lbl = str(getattr(obj, "label", "Device"))
        x = float(getattr(obj, "physical_x_mm", 0.0))
        y = float(getattr(obj, "physical_y_mm", 0.0))
        z = float(getattr(obj, "physical_z_mm", 0.0))
        meta = getattr(obj, "metadata", {})
    else:
        m_id = str(marker_id_or_data)
        p_mrn = str(patient_mrn or "")
        s_id = str(scan_id or "")
        d_type = str(device_type or "Other")
        lbl = str(label or "Device")
        x = float(physical_x_mm)
        y = float(physical_y_mm)
        z = float(physical_z_mm)
        meta = metadata or {}

    now_str = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(meta or {})
    conn.execute(
        "INSERT OR REPLACE INTO device_markers (id, patient_mrn, scan_id, device_type, label, physical_x_mm, physical_y_mm, physical_z_mm, created_at, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (m_id, p_mrn, s_id, d_type, lbl, x, y, z, now_str, meta_json)
    )
    conn.commit()
    conn.close()
    return {
        "id": m_id,
        "patient_mrn": p_mrn,
        "scan_id": s_id,
        "device_type": d_type,
        "label": lbl,
        "physical_x_mm": x,
        "physical_y_mm": y,
        "physical_z_mm": z,
        "created_at": now_str,
        "metadata": meta or {}
    }

def get_device_markers_for_scan(scan_id: str, patient_mrn: Optional[str] = None) -> list[dict]:
    """Retrieves all device markers for a scan and optionally patient MRN."""
    try:
        conn = get_connection()
        _ensure_device_markers_table(conn)
        if patient_mrn:
            rows = conn.execute(
                "SELECT * FROM device_markers WHERE scan_id = ? AND patient_mrn = ? ORDER BY created_at ASC",
                (str(scan_id), str(patient_mrn))
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM device_markers WHERE scan_id = ? ORDER BY created_at ASC",
                (str(scan_id),)
            ).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d.get("metadata_json", "{}"))
            except Exception:
                d["metadata"] = {}
            results.append(d)
        return results
    except Exception:
        return []

def get_device_markers_for_patient(patient_mrn: str) -> list[dict]:
    """Retrieves all device markers for a patient MRN."""
    try:
        conn = get_connection()
        _ensure_device_markers_table(conn)
        rows = conn.execute(
            "SELECT * FROM device_markers WHERE patient_mrn = ? ORDER BY created_at ASC",
            (str(patient_mrn),)
        ).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d.get("metadata_json", "{}"))
            except Exception:
                d["metadata"] = {}
            results.append(d)
        return results
    except Exception:
        return []

def delete_device_marker(marker_id: str, patient_mrn: Optional[str] = None) -> bool:
    """Deletes a device marker by ID, optionally scoped to patient MRN."""
    try:
        conn = get_connection()
        _ensure_device_markers_table(conn)
        if patient_mrn:
            cursor = conn.execute(
                "DELETE FROM device_markers WHERE id = ? AND patient_mrn = ?",
                (str(marker_id), str(patient_mrn))
            )
        else:
            cursor = conn.execute(
                "DELETE FROM device_markers WHERE id = ?",
                (str(marker_id),)
            )
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted
    except Exception:
        return False