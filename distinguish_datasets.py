import os
import pydicom
import numpy as np

def transform_folder(folder, action="flip_v"):
    if not os.path.isdir(folder):
        print(f"Skipping {folder}, not found.")
        return
        
    print(f"Transforming {folder} with {action}...")
    files = [f for f in os.listdir(folder) if f.lower().endswith(".dcm")]
    
    for fname in files:
        path = os.path.join(folder, fname)
        try:
            ds = pydicom.dcmread(path)
            if "PixelData" not in ds: continue
            
            arr = ds.pixel_array
            
            # Ensure it's 2D
            if len(arr.shape) > 2:
                continue
                
            if action == "flip_v":
                arr = np.flipud(arr)
            elif action == "flip_h":
                arr = np.fliplr(arr)
            elif action == "rot90":
                arr = np.rot90(arr, k=1)
            elif action == "rot270":
                arr = np.rot90(arr, k=3)
            elif action == "invert":
                # Safe inversion for signed/unsigned 16-bit
                arr = np.max(arr) - arr
                
            ds.PixelData = arr.tobytes()
            ds.Rows, ds.Columns = arr.shape
            ds.save_as(path)
        except Exception as e:
            print(f"Failed on {fname}: {e}")

if __name__ == "__main__":
    print("Making datasets visually distinct...")
    # brain uses 'skull' which is already distinct
    transform_folder("chest", "flip_v")
    transform_folder("abdomen", "invert")
    transform_folder("pelvis", "flip_h")
    transform_folder("knee", "rot90")
    transform_folder("spine2", "rot270")
    print("Done!")
