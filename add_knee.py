import os
import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian
from PIL import Image, ImageDraw, ImageFont

def generate_knee_dicom():
    folder = "knee_xray"
    os.makedirs(folder, exist_ok=True)
    dcm_path = os.path.join(folder, "image.dcm")
    
    # Create a dummy image
    img = Image.new('L', (512, 512), color=100)
    draw = ImageDraw.Draw(img)
    draw.text((150, 250), "Knee X-Ray (Sample)", fill=255)
    
    pixel_array = np.array(img, dtype=np.uint16)
    
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.1'
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    
    ds = FileDataset(dcm_path, {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.PatientName = "Brown, Robert"
    ds.PatientID = "XRAY-004"
    ds.PatientSex = "M"
    ds.PatientAge = "042Y"
    ds.StudyDate = "20231002"
    ds.Modality = "CR"
    ds.SeriesDescription = "Knee X-Ray"
    
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
    print(f"✅ Generated {dcm_path}")

if __name__ == "__main__":
    generate_knee_dicom()
