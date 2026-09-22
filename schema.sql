CREATE TABLE IF NOT EXISTS patients (
    mrn TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sex TEXT,
    age TEXT
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_mrn TEXT NOT NULL,
    modality TEXT,
    study_date TEXT,
    description TEXT,
    file_path TEXT NOT NULL,
    slice_count INTEGER DEFAULT 1,
    FOREIGN KEY (patient_mrn) REFERENCES patients(mrn)
);

CREATE TABLE IF NOT EXISTS surgical_plan_versions (
    id TEXT PRIMARY KEY,
    scan_id TEXT NOT NULL,
    patient_mrn TEXT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    notes TEXT DEFAULT '',
    FOREIGN KEY (patient_mrn) REFERENCES patients(mrn)
);

CREATE TABLE IF NOT EXISTS tracked_measurements (
    id TEXT PRIMARY KEY,
    scan_id TEXT NOT NULL,
    patient_mrn TEXT NOT NULL,
    label TEXT NOT NULL,
    value_mm REAL NOT NULL,
    unit TEXT DEFAULT 'mm',
    created_at TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}',
    FOREIGN KEY (patient_mrn) REFERENCES patients(mrn)
);