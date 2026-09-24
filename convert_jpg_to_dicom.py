import os
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian
from PIL import Image
import numpy as np

def jpg_to_dcm(jpg_path, dcm_path, patient_name, patient_id, desc):
    if not os.path.exists(jpg_path):
        print(f"Skipping {jpg_path}, not found.")
        return
        
    print(f"Converting {jpg_path} -> {dcm_path}")
    img = Image.open(jpg_path).convert('L')
    pixel_array = np.array(img, dtype=np.uint16)

    # Scale 0-255 up to 0-65535 so contrast works well with standard medical windows
    pixel_array = (pixel_array.astype(np.float32) / 255.0 * 65535).astype(np.uint16)

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.1'
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    
    ds = FileDataset(dcm_path, {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.PatientSex = "O"
    ds.PatientAge = "030Y"
    ds.StudyDate = "20231001"
    ds.Modality = "CR"
    ds.SeriesDescription = desc
    
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelRepresentation = 0
    ds.HighBit = 15
    ds.BitsStored = 16
    ds.BitsAllocated = 16
    ds.Rows, ds.Columns = pixel_array.shape
    ds.PixelData = pixel_array.tobytes()
    
    # Store default Window/Level
    ds.WindowCenter = 32767
    ds.WindowWidth = 65535
    
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    pydicom.filewriter.dcmwrite(dcm_path, ds, write_like_original=False)

jpg_to_dcm('shoulder.jpg', r'shoulder_xray\image.dcm', 'Smith, John', 'XRAY-001', 'Shoulder X-Ray')
jpg_to_dcm('hand.jpg', r'hand_xray\image.dcm', 'Doe, Jane', 'XRAY-002', 'Hand X-Ray')
jpg_to_dcm('knee.jpg', r'knee_xray\image.dcm', 'Brown, Robert', 'XRAY-004', 'Knee X-Ray')
