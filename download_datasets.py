import os
import shutil
import pydicom

def create_mock_dataset(src_folder, dst_folder, patient_name, patient_id, description, body_part):
    print(f"Creating mock dataset for {body_part} in {dst_folder}...")
    if not os.path.exists(dst_folder):
        os.makedirs(dst_folder)
        
    for fname in os.listdir(src_folder):
        if not fname.lower().endswith('.dcm'):
            continue
            
        src_path = os.path.join(src_folder, fname)
        dst_path = os.path.join(dst_folder, fname)
        
        # Read and modify headers
        ds = pydicom.dcmread(src_path)
        ds.PatientName = patient_name
        ds.PatientID = patient_id
        ds.StudyDescription = description
        ds.SeriesDescription = description
        ds.BodyPartExamined = body_part
        
        # Save to new folder
        ds.save_as(dst_path)

if __name__ == "__main__":
    print("Generating 6 new demo datasets from base volumes to populate dashboard...")
    
    # We will clone 'DICOM' (which is actually a spine) and 'skull'
    base_body = "DICOM"
    base_head = "skull"
    
    if not os.path.exists(base_body) or not os.path.exists(base_head):
        print("Base folders not found! Run from project root.")
        exit(1)
        
    datasets = [
        (base_head, "brain", "Miller, David", "MRN-338291", "CT Brain (w/o contrast)", "BRAIN"),
        (base_body, "chest", "Torres, Maria", "MRN-119482", "CT Chest (High Res)", "CHEST"),
        (base_body, "abdomen", "Kim, Jin", "MRN-774910", "CT Abdomen/Pelvis", "ABDOMEN"),
        (base_body, "pelvis", "Smith, Robert", "MRN-228194", "CT Pelvis (Bone Window)", "PELVIS"),
        (base_body, "knee", "Johnson, Emily", "MRN-449102", "CT Knee Left", "KNEE"),
        (base_body, "spine2", "Garcia, Carlos", "MRN-993812", "CT Cervical Spine", "CSPINE"),
    ]
    
    for src, dst, name, mrn, desc, part in datasets:
        create_mock_dataset(src, dst, name, mrn, desc, part)
        
    print("Done generating mock datasets.")
    print("Now seeding them into the database...")
    
    # We'll run ingest.py's seed logic on them
    from ingest import ingest_multi_slice_volume, init_db
    init_db()
    
    for src, dst, name, mrn, desc, part in datasets:
        ingest_multi_slice_volume(dst, override_name=name, override_mrn=mrn, description=desc)
        
    print("All done! Launch the app to see the full dashboard.")
