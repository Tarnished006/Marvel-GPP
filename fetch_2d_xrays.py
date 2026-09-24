import os
import requests
import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian
from PIL import Image, ImageDraw, ImageFont
import io
import time

def create_fallback_image(text):
    img = Image.new('L', (512, 512), color=128)
    draw = ImageDraw.Draw(img)
    draw.text((100, 250), text, fill=255)
    return img

def download_and_convert(url, output_dir, patient_name, patient_id, description):
    os.makedirs(output_dir, exist_ok=True)
    dcm_path = os.path.join(output_dir, "image.dcm")
    
    print(f"Downloading {description} from {url}...")
    headers = {"User-Agent": "MarvelGPP-Bot/1.0 (test@example.com)"}
    
    try:
        response = requests.get(url, stream=True, headers=headers)
        response.raise_for_status()
        img = Image.open(io.BytesIO(response.content)).convert("L")
    except Exception as e:
        print(f"Download failed: {e}. Generating fallback image.")
        img = create_fallback_image(f"{description} (Fallback)")
    
    pixel_array = np.array(img, dtype=np.uint16)
    
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
    ds.SeriesDescription = description
    
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelRepresentation = 0
    ds.HighBit = 15
    ds.BitsStored = 16
    ds.BitsAllocated = 16
    ds.Rows, ds.Columns = pixel_array.shape
    ds.PixelData = pixel_array.tobytes()
    
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    pydicom.filewriter.dcmwrite(dcm_path, ds, write_like_original=False)
    print(f"Saved to {dcm_path}")

urls = {
    "shoulder": (
        "https://upload.wikimedia.org/wikipedia/commons/2/23/Y-projection_X-ray_of_a_normal_shoulder.jpg",
        "shoulder_xray", "Smith, John", "XRAY-001", "Shoulder X-Ray"
    ),
    "hand": (
        "https://upload.wikimedia.org/wikipedia/commons/a/a2/X-ray_of_normal_hand_by_dorsoplantar_projection.jpg",
        "hand_xray", "Doe, Jane", "XRAY-002", "Hand X-Ray"
    ),
    "chest": (
        "https://upload.wikimedia.org/wikipedia/commons/c/c8/Chest_Xray_PA_3-8-2010.png",
        "chest_xray", "Williams, David", "XRAY-003", "Chest X-Ray"
    )
}

for key, (url, folder, name, mrn, desc) in urls.items():
    download_and_convert(url, folder, name, mrn, desc)
    time.sleep(1) # Be nice to servers
