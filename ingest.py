# ingest.py
import os
import pydicom
from database import init_db, add_patient, add_scan

def safe_str(value, default=""):
    return str(value) if value is not None else default

def ingest_single_slice_patients(folder: str):
    """Each .dcm file in this folder is one separate patient, one slice each."""
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

def ingest_multi_slice_volume(folder: str):
    """All .dcm files in this folder belong to ONE patient's full volume."""
    dcm_files = [f for f in os.listdir(folder) if f.endswith(".dcm")]
    if not dcm_files:
        print(f"No .dcm files found in {folder}")
        return

    # Read the first slice just to get patient/study metadata
    first_ds = pydicom.dcmread(os.path.join(folder, dcm_files[0]))

    mrn = safe_str(first_ds.get("PatientID"), "CRANIAL-001")
    name = safe_str(first_ds.get("PatientName"), "Cranial CT Patient")
    sex = safe_str(first_ds.get("PatientSex"))
    age = safe_str(first_ds.get("PatientAge"))

    add_patient(mrn=mrn, name=name, sex=sex, age=age)
    add_scan(
        patient_mrn=mrn,
        modality=safe_str(first_ds.get("Modality"), "CT"),
        study_date=safe_str(first_ds.get("StudyDate")),
        description="Cranial CT (full volume)",
        file_path=folder,  # NOTE: points to the whole folder, not one file
        slice_count=len(dcm_files)
    )

    print(f"Ingested multi-slice volume from {folder} ({len(dcm_files)} slices)")


if __name__ == "__main__":
    init_db()
    ingest_single_slice_patients("raw_downloads/single_slice/ct_subset")
    ingest_multi_slice_volume("raw_downloads/multi_slice/cranial_ct_data/Cranial CT")
    print("Done.")