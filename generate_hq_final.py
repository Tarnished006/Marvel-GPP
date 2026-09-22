import os
import sqlite3
import pydicom
import numpy as np
from pydicom.uid import UID

db = sqlite3.connect('aegis.db')
c = db.cursor()
c.execute("DELETE FROM patients WHERE mrn LIKE 'HQ-%'")
c.execute("DELETE FROM scans WHERE patient_mrn LIKE 'HQ-%'")

names = ['Elena Martinez', 'Michael Chang', 'Sarah Williams']
desc = ['Chest X-Ray (High Res)', 'Chest X-Ray (Contrast Enhanced)', 'Chest X-Ray (Flipped)']

for i in range(3):
    folder = f'hq_2d_{i}'
    os.makedirs(folder, exist_ok=True)
    
    ds = pydicom.dcmread('hq_2d_0/scan.dcm')
    ds.PatientName = names[i]
    ds.PatientID = f'HQ-800{i}'
    ds.StudyInstanceUID = UID(f'1.2.3.4.5.6.7.{i}')
    ds.SeriesInstanceUID = UID(f'1.2.3.4.5.6.7.8.{i}')
    ds.SOPInstanceUID = UID(f'1.2.3.4.5.6.800.{i}')
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    
    # Modify image data slightly so they look different
    arr = ds.pixel_array
    if i == 1:
        arr = (arr * 0.8).astype(np.uint16)
    elif i == 2:
        arr = np.fliplr(arr)
        
    ds.PixelData = arr.tobytes()
    ds.save_as(f'{folder}/scan.dcm')
    
    c.execute('INSERT OR IGNORE INTO patients (mrn, name, sex, age) VALUES (?, ?, ?, 40)', (ds.PatientID, str(ds.PatientName), 'F' if i!=1 else 'M'))
    c.execute('''INSERT INTO scans (patient_mrn, study_date, modality, description, file_path, slice_count) 
                 VALUES (?, date('now'), ?, ?, ?, 1)''', 
              (ds.PatientID, 'CR', desc[i], os.path.abspath(folder)))

db.commit()
db.close()
print('HQ DICOMs fully generated!')
