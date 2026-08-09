from pydicom.data import get_testdata_file
import pydicom

path = get_testdata_file("CT_small.dcm")
ds = pydicom.dcmread(path)
print(ds)