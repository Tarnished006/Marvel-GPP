import os
import pydicom

def check_dir(d):
    print(f'\n--- Checking {d} ---')
    if not os.path.exists(d):
        print('Folder does not exist!')
        return
    files = [f for f in os.listdir(d) if f.lower().endswith('.dcm')]
    print(f'Total DICOMs: {len(files)}')
    if files:
        ds = pydicom.dcmread(os.path.join(d, files[0]), stop_before_pixels=True)
        print(f'Patient ID: {getattr(ds, "PatientID", "Unknown")}')
        print(f'Body Part: {getattr(ds, "BodyPartExamined", "Unknown")}')
        print(f'Series UID: {getattr(ds, "SeriesInstanceUID", "Unknown")}')

check_dir('chest_real')
check_dir('abdomen_real')
check_dir('DICOM')
check_dir('skull')
