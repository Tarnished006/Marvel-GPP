import os
import shutil
import sqlite3

files = [
    (r'C:\Users\rithy\.pydicom\data\RG1_UNCR.dcm', 'Knee X-Ray (CR)', 'Jane Doe'),
    (r'C:\Users\rithy\.pydicom\data\RG3_UNCR.dcm', 'Chest X-Ray (CR)', 'John Smith'),
    (r'C:\Users\rithy\.pydicom\data\OBXXXX1A.dcm', 'Pelvis X-Ray (CR)', 'Alice Johnson')
]

db = sqlite3.connect('aegis.db')
c = db.cursor()

c.execute("DELETE FROM patients WHERE mrn LIKE 'EXT-%'")
c.execute("DELETE FROM scans WHERE patient_mrn LIKE 'EXT-%'")

for i, (fpath, desc, pname) in enumerate(files):
    if not os.path.exists(fpath): continue
    
    folder = f'external_2d_{i}'
    os.makedirs(folder, exist_ok=True)
    shutil.copy2(fpath, f'{folder}/scan.dcm')
    
    mrn = f'EXT-900{i}'
    c.execute('INSERT OR IGNORE INTO patients (mrn, name, sex, age) VALUES (?, ?, ?, 40)', (mrn, pname, 'F' if i%2==0 else 'M'))
    c.execute('''INSERT INTO scans (patient_mrn, study_date, modality, description, file_path, slice_count) 
                 VALUES (?, date('now'), ?, ?, ?, 1)''', 
              (mrn, 'CR', desc, os.path.abspath(folder)))

db.commit()
db.close()
print('External DICOMs loaded successfully!')
