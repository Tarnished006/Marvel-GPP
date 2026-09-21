import os
import shutil
import urllib.request
import zipfile
from database import init_db, add_patient, add_scan

def ingest_volume(folder: str, name: str, mrn: str, desc: str, modality: str="CT"):
    if not os.path.isdir(folder):
        print(f"Skipping {folder} - not found")
        return
        
    dcm_files = [f for f in os.listdir(folder) if f.lower().endswith(".dcm")]
    if not dcm_files:
        print(f"No .dcm found in {folder}")
        return
        
    add_patient(mrn=mrn, name=name, sex="M", age="45")
    add_scan(
        patient_mrn=mrn,
        modality=modality,
        study_date="2023-01-01",
        description=desc,
        file_path=os.path.abspath(folder),
        slice_count=len(dcm_files)
    )
    print(f"Ingested {name} ({len(dcm_files)} slices)")

def download_and_extract(uid, target_folder):
    if os.path.exists(target_folder):
        shutil.rmtree(target_folder)
    os.makedirs(target_folder, exist_ok=True)
    
    zip_path = f"{target_folder}.zip"
    url = f"https://services.cancerimagingarchive.net/nbia-api/services/v1/getImage?SeriesInstanceUID={uid}"
    
    print(f"Downloading {target_folder}...")
    urllib.request.urlretrieve(url, zip_path)
    
    print(f"Extracting {target_folder}...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(target_folder)
        
    os.remove(zip_path)
    
    # Flatten extracted files to root of target_folder
    for root, dirs, files in os.walk(target_folder):
        for f in files:
            if f.lower().endswith('.dcm'):
                src = os.path.join(root, f)
                dst = os.path.join(target_folder, f)
                if src != dst:
                    shutil.move(src, dst)

    # Clean up empty subdirectories
    for root, dirs, files in os.walk(target_folder, topdown=False):
        for d in dirs:
            dir_path = os.path.join(root, d)
            if not os.listdir(dir_path):
                os.rmdir(dir_path)

def main():
    # 1. Clean up fake folders
    fakes = ['brain', 'chest', 'abdomen', 'pelvis', 'knee', 'spine2']
    for f in fakes:
        if os.path.exists(f):
            print(f"Deleting fake folder {f}...")
            shutil.rmtree(f)
            
    # 2. Reset database
    if os.path.exists('aegis.db'):
        os.remove('aegis.db')
    init_db()
    
    # 3. Download real data
    chest_uid = '1.3.6.1.4.1.14519.5.2.1.6279.6001.179049373636438705059720603192'
    abd_uid = '1.2.826.0.1.3680043.2.1125.1.77419915248783746629465382576486048'
    
    try:
        download_and_extract(chest_uid, 'chest_real')
        download_and_extract(abd_uid, 'abdomen_real')
    except Exception as e:
        print(f"Error downloading: {e}")
        return

    # 4. Ingest exactly 4 patients
    ingest_volume('skull', 'Okafor, James', 'MRN-847291', 'Head CT (Skull/Brain)')
    ingest_volume('DICOM', 'Chen, Sarah', 'MRN-229410', 'Lumbar Spine CT')
    ingest_volume('chest_real', 'Torres, Maria', 'MRN-119482', 'CT Chest (High Res)')
    ingest_volume('abdomen_real', 'Kim, Jin', 'MRN-774910', 'CT Abdomen/Pelvis')

    print("DONE! The app now has exactly 4 distinct real patients.")

if __name__ == "__main__":
    main()
