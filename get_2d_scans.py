import requests
import json
import urllib.request
import os
import zipfile
import sqlite3

def download_2d_scans():
    url = "https://services.cancerimagingarchive.net/nbia-api/services/v1/getSeries"
    print("Searching for 2D X-Ray (DX/CR) scans...")
    # Get CR modalities (Computed Radiography - standard X-Rays)
    r = requests.get(url, params={"Modality": "CR"})
    
    if r.status_code != 200:
        print("Failed to get series")
        return

    series_list = r.json()
    
    # Filter for series with only 1 image (typical X-Ray)
    candidates = [s for s in series_list if s.get('ImageCount', 0) == 1]
    
    selected_series = candidates[:5]
    print(f"Found {len(selected_series)} suitable 2D scans.")
    
    db_conn = sqlite3.connect('aegis.db')
    c = db_conn.cursor()
    
    patients = [
        ("David Miller", "M", "55", "Chest X-Ray"),
        ("Emma Wilson", "F", "29", "Knee Radiograph"),
        ("Robert Brown", "M", "42", "Abdomen AP"),
        ("Sophia Garcia", "F", "61", "Pelvis X-Ray"),
        ("James Anderson", "M", "19", "Hand X-Ray")
    ]
    
    for i, s in enumerate(selected_series):
        uid = s['SeriesInstanceUID']
        print(f"Downloading {uid}...")
        
        dl_url = f"https://services.cancerimagingarchive.net/nbia-api/services/v1/getImage?SeriesInstanceUID={uid}"
        
        folder_name = f"2d_scan_{i+1}"
        zip_name = f"{folder_name}.zip"
        
        if not os.path.exists(folder_name):
            os.makedirs(folder_name)
            
        try:
            urllib.request.urlretrieve(dl_url, zip_name)
            with zipfile.ZipFile(zip_name, 'r') as zip_ref:
                zip_ref.extractall(folder_name)
            os.remove(zip_name)
            
            # Insert into database
            p_name, gender, age, desc = patients[i]
            mrn = f"X-{1000+i}"
            
            # Insert Patient
            c.execute('''INSERT OR IGNORE INTO patients (mrn, name, gender, age)
                         VALUES (?, ?, ?, ?)''', (mrn, p_name, gender, age))
                         
            # Insert Scan
            c.execute('''INSERT INTO scans (patient_mrn, scan_date, modality, description, file_path, slice_count)
                         VALUES (?, date('now'), 'CR', ?, ?, ?)''',
                      (mrn, desc, os.path.abspath(folder_name), 1))
            
            print(f"Added {p_name} to database.")
            
        except Exception as e:
            print(f"Failed to download/process {uid}: {e}")
            
    db_conn.commit()
    db_conn.close()
    print("Done!")

if __name__ == "__main__":
    download_2d_scans()
