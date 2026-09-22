import pydicom
import os
import shutil
import sqlite3
from pydicom.data import get_testdata_files

# Get standard test files that come locally installed with pydicom
files = [f for f in get_testdata_files('*') if 'CR' in f or 'DX' in f or 'MR_small' in f or 'CT_small' in f][:3]

db = sqlite3.connect('aegis.db')
c = db.cursor()

# Cleanup previous X- patients
c.execute("DELETE FROM patients WHERE mrn LIKE 'X-%'")
c.execute("DELETE FROM scans WHERE patient_mrn LIKE 'X-%'")

patients = [
    ('CR Chest', 'M', 'CR'), 
    ('MR Knee', 'F', 'MR'), 
    ('CT Small', 'M', 'CT')
]

for i, f in enumerate(files):
    if i >= len(patients):
        break
        
    folder = f'real_2d_{i}'
    os.makedirs(folder, exist_ok=True)
    shutil.copy2(f, f'{folder}/scan.dcm')
    
    mrn = f'X-300{i}'
    pname, psex, pmodality = patients[i]
    
    c.execute('INSERT OR IGNORE INTO patients (mrn, name, sex, age) VALUES (?, ?, ?, 40)', (mrn, pname, psex))
    c.execute('''INSERT INTO scans (patient_mrn, study_date, modality, description, file_path, slice_count) 
                 VALUES (?, date('now'), ?, ?, ?, 1)''', 
              (mrn, pmodality, pname, os.path.abspath(folder)))

db.commit()
db.close()
print("Local DICOMs added successfully!")
