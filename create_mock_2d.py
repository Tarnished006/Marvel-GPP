import os
import shutil
import sqlite3

def create_mock():
    # Source slices
    srcs = [
        ("skull", "David Miller", "M", "55", "Skull Radiograph"),
        ("chest_real", "Emma Wilson", "F", "29", "Chest AP X-Ray"),
        ("abdomen_real", "Robert Brown", "M", "42", "Abdomen X-Ray"),
    ]
    
    db_conn = sqlite3.connect('aegis.db')
    c = db_conn.cursor()
    
    for i, (src_dir, p_name, gender, age, desc) in enumerate(srcs):
        if not os.path.exists(src_dir):
            continue
            
        files = [f for f in os.listdir(src_dir) if f.endswith('.dcm')]
        if not files:
            continue
            
        # Get one slice
        middle_file = files[len(files)//2]
        src_path = os.path.join(src_dir, middle_file)
        
        # Create 2D folder
        dest_dir = f"mock_2d_{i+1}"
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, middle_file)
        
        shutil.copy2(src_path, dest_path)
        
        mrn = f"X-200{i}"
        # Insert Patient
        c.execute('''INSERT OR IGNORE INTO patients (mrn, name, sex, age)
                     VALUES (?, ?, ?, ?)''', (mrn, p_name, gender, age))
                     
        # Insert Scan
        c.execute('''INSERT INTO scans (patient_mrn, study_date, modality, description, file_path, slice_count)
                     VALUES (?, date('now'), 'CR', ?, ?, ?)''',
                  (mrn, desc, os.path.abspath(dest_dir), 1))
                  
        print(f"Created mock 2D scan for {p_name}")
        
    db_conn.commit()
    db_conn.close()
    
if __name__ == '__main__':
    create_mock()
