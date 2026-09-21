# ingest.py
import os
import pydicom
from database import init_db, add_patient, add_scan

def safe_str(value, default=""):
    return str(value) if value is not None else default

def ingest_single_slice_patients(folder: str):
    """Each .dcm file in this folder is one separate patient, one slice each."""
    if not os.path.isdir(folder):
        print(f"Skipping single-slice ingest: {folder} not found")
        return
    for fname in os.listdir(folder):
        if not fname.endswith(".dcm"):
            continue

        filepath = os.path.join(folder, fname)
        ds = pydicom.dcmread(filepath)

        mrn = safe_str(ds.get("PatientID"), fname)
        name = safe_str(ds.get("PatientName"), "Unknown")
        sex = safe_str(ds.get("PatientSex"))
        age = safe_str(ds.get("PatientAge"))

        add_patient(mrn=mrn, name=name, sex=sex, age=age)
        add_scan(
            patient_mrn=mrn,
            modality=safe_str(ds.get("Modality")),
            study_date=safe_str(ds.get("StudyDate")),
            description=safe_str(ds.get("SeriesDescription"), "CT Slice"),
            file_path=filepath,
            slice_count=1
        )
    print(f"Ingested single-slice patients from {folder}")

def ingest_multi_slice_volume(folder: str, override_name: str = None, override_mrn: str = None, description: str = "CT Scan"):
    """All .dcm files in this folder belong to ONE patient's full volume."""
    if not os.path.isdir(folder):
        print(f"Skipping multi-slice ingest: {folder} not found")
        return
        
    dcm_files = [f for f in os.listdir(folder) if f.lower().endswith(".dcm")]
    if not dcm_files:
        print(f"No .dcm files found in {folder}")
        return

    # Read the first slice just to get patient/study metadata
    first_ds = pydicom.dcmread(os.path.join(folder, dcm_files[0]), stop_before_pixels=True)

    mrn = override_mrn or safe_str(first_ds.get("PatientID"), "UNK-001")
    name = override_name or safe_str(first_ds.get("PatientName"), "Unknown Patient")
    sex = safe_str(first_ds.get("PatientSex"), "U")
    age = safe_str(first_ds.get("PatientAge"), "U")

    add_patient(mrn=mrn, name=name, sex=sex, age=age)
    add_scan(
        patient_mrn=mrn,
        modality=safe_str(first_ds.get("Modality"), "CT"),
        study_date=safe_str(first_ds.get("StudyDate")),
        description=description,
        file_path=os.path.abspath(folder),  # Must be absolute path for UI to find it properly, or relative to root
        slice_count=len(dcm_files)
    )

    print(f"Ingested multi-slice volume from {folder} ({len(dcm_files)} slices) -> Patient: {name}")

def seed_demo_database():
    print("Seeding database...")
    init_db()
    
    # Use realistic fictional names for our demo datasets
    ingest_multi_slice_volume(
        "skull", 
        override_name="Okafor, James", 
        override_mrn="MRN-847291", 
        description="Head CT (Skull/Brain)"
    )
    
    ingest_multi_slice_volume(
        "DICOM", 
        override_name="Chen, Sarah", 
        override_mrn="MRN-229410", 
        description="Lumbar Spine CT"
    )
    print("Database seeding complete.")

if __name__ == "__main__":
    seed_demo_database()