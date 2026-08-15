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