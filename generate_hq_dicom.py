import os
import requests
import sqlite3
import numpy as np
from PIL import Image
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import UID
import datetime

# Reliable Wikipedia image sources without the thumbnail blocker
images = [
    {
        'url': 'https://upload.wikimedia.org/wikipedia/commons/c/c8/Chest_Xray_PA_3-8-2010.png',
        'name': 'Chest X-Ray (Adult PA)', 'patient': 'Elena Martinez', 'modality': 'CR'
    },
    {
        'url': 'https://upload.wikimedia.org/wikipedia/commons/a/a2/Chest_X-ray_of_a_patient_with_a_foreign_body.jpg',
        'name': 'Chest X-Ray (Foreign Body)', 'patient': 'Michael Chang', 'modality': 'CR'
    },
    {
        'url': 'https://upload.wikimedia.org/wikipedia/commons/4/41/Normal_AP_Chest.jpg',
        'name': 'Chest X-Ray (Normal AP)', 'patient': 'Sarah Williams', 'modality': 'CR'
    }
]

db = sqlite3.connect('aegis.db')
c = db.cursor()
c.execute("DELETE FROM patients WHERE mrn LIKE 'HQ-%'")
c.execute("DELETE FROM scans WHERE patient_mrn LIKE 'HQ-%'")

for i, data in enumerate(images):
    print(f"Generating HQ DICOM {i+1}/3...")
    folder = f'hq_2d_{i}'
    os.makedirs(folder, exist_ok=True)
    
    # Download image using requests
    img_path = f"{folder}/temp.jpg"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    response = requests.get(data['url'], headers=headers)
    with open(img_path, 'wb') as out_file:
        out_file.write(response.content)
        
    # Convert to grayscale 16-bit array
    img = Image.open(img_path).convert('L')
    pixel_array = np.array(img, dtype=np.uint16)
    pixel_array = pixel_array * 257 # Scale 0-255 to 0-65535 for high contrast depth
    
    # Create DICOM
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = UID('1.2.840.10008.5.1.4.1.1.1') # CR Image Storage
    file_meta.MediaStorageSOPInstanceUID = UID(f"1.2.3.4.5.6.800.{i}")
    file_meta.ImplementationClassUID = UID("1.2.3.4")
    file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    
    ds = FileDataset(f'{folder}/scan.dcm', {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.PatientName = data['patient']
    ds.PatientID = f"HQ-800{i}"
    ds.Modality = data['modality']
    ds.StudyDate = datetime.datetime.now().strftime('%Y%m%d')
    ds.StudyTime = datetime.datetime.now().strftime('%H%M%S')
    ds.StudyInstanceUID = UID(f"1.2.3.4.5.6.7.{i}")
    ds.SeriesInstanceUID = UID(f"1.2.3.4.5.6.7.8.{i}")
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelRepresentation = 0
    ds.HighBit = 15
    ds.BitsStored = 16
    ds.BitsAllocated = 16
    ds.Columns = pixel_array.shape[1]
    ds.Rows = pixel_array.shape[0]
    
    # Proper Window/Level for 16-bit
    ds.WindowCenter = 32768
    ds.WindowWidth = 65535
    ds.RescaleIntercept = "0.0"
    ds.RescaleSlope = "1.0"
    
    ds.PixelData = pixel_array.tobytes()
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.save_as(f'{folder}/scan.dcm')
    os.remove(img_path)
    
    # Add to DB
    mrn = ds.PatientID
    c.execute('INSERT OR IGNORE INTO patients (mrn, name, sex, age) VALUES (?, ?, ?, 40)', (mrn, data['patient'], 'F' if i%2==0 else 'M'))
    c.execute('''INSERT INTO scans (patient_mrn, study_date, modality, description, file_path, slice_count) 
                 VALUES (?, date('now'), ?, ?, ?, 1)''', 
              (mrn, data['modality'], data['name'], os.path.abspath(folder)))

db.commit()
db.close()
print("High-Quality DICOMs generated and loaded successfully!")
