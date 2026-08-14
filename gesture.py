import os
import time
import cv2
import pydicom
import numpy as np
import pyvista as pv
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from ema_filter import EMAFilter

class Aegis3DEngine:
    def __init__(self, folder_path):
        self.plotter = pv.Plotter(window_size=[1024, 768], title="Aegis-Touch 3D Viewer")
        self.plotter.set_background("black")
        self.volume_data = None
        self.current_melt = 0.0  # Tracks current opacity to prevent laggy micro-updates

        # Cumulative elevation WE apply via gestures (VTK's camera.elevation is a
        # relative call, it doesn't expose a readable absolute angle). We clamp
        # this ourselves in rotate_camera() to keep the camera away from the
        # poles -- see the docstring there for why that matters.
        self.elevation_angle = 0.0

        self._load_dicom_volume(folder_path)

        print("[3D Engine] Pre-calculating Bone mesh (Please wait...)")
        self.bone_mesh = self.volume_data.contour(isosurfaces=[400.0]).decimate(0.90)

        print("[3D Engine] Pre-calculating Skin/Tissue mesh (Please wait...)")
        self.skin_mesh = self.volume_data.contour(isosurfaces=[-100.0]).decimate(0.90)

        print("[3D Engine] Generating scene...")
        self.plotter.enable_depth_peeling(10)

        self.bone_actor = self.plotter.add_mesh(self.bone_mesh, color="ivory", smooth_shading=True, specular=0.3)
        self.skin_actor = self.plotter.add_mesh(self.skin_mesh, color="pink", smooth_shading=True, opacity=1.0)

    def _load_dicom_volume(self, folder_path):
        """Ingests DICOM slices, calculates physical Z-spacing, and caches the volume."""
        print(f"[3D Engine] Loading DICOM files from: {folder_path}...")
        files = [pydicom.dcmread(os.path.join(folder_path, f))
                 for f in os.listdir(folder_path) if f.endswith('.dcm')]

        if not files:
            raise FileNotFoundError(f"No .dcm files found in folder: {folder_path}")

        files.sort(key=lambda x: float(x.ImagePositionPatient[2]))

        slice_shape = list(files[0].pixel_array.shape)
        slice_shape.append(len(files))
        volume3d = np.zeros(slice_shape, dtype=np.float32)

        for i, dcm in enumerate(files):
            slope = getattr(dcm, 'RescaleSlope', 1.0)
            intercept = getattr(dcm, 'RescaleIntercept', 0.0)
            volume3d[:, :, i] = (dcm.pixel_array * slope) + intercept

        self.volume_data = pv.wrap(volume3d)

        spacing = getattr(files[0], 'PixelSpacing', [1.0, 1.0])
        z_spacing = abs(float(files[1].ImagePositionPatient[2]) - float(files[0].ImagePositionPatient[2])) if len(files) > 1 else 1.0
        self.volume_data.spacing = (spacing[0], spacing[1], z_spacing)

        self.volume_data = self.volume_data.gaussian_smooth(radius_factor=1.0)
        print("[3D Engine] Volume successfully loaded and cached.")

    def set_tissue_melt(self, melt_factor):
        """Instantly adjusts skin opacity via GPU, with hysteresis to prevent lag."""
        if abs(melt_factor - self.current_melt) < 0.05:
            return

        self.current_melt = melt_factor
        new_opacity = max(0.0, min(1.0, 1.0 - melt_factor))
        self.skin_actor.GetProperty().SetOpacity(new_opacity)

    def rotate_camera(self, delta_x, delta_y):
        """Direct rotation mapping, with per-frame clamping AND pole clamping.

        Two independent safeguards live here:

        1. Per-frame delta clamp (unchanged): caps how much a single noisy
           frame can move the camera.

        2. Elevation/pole clamp (the fix for the "sometimes spins out of
           control" report): elevation has no natural limit, so enough
           accumulated vertical hand motion eventually points the camera
           almost straight down (or up) the Z axis. At that point the view
           direction becomes nearly parallel to the up vector we force to
           (0,0,1) below -- a degenerate camera basis -- and VTK resolves
           it by snapping to an arbitrary orientation. That snap is the
           "uncontrollable" jump. It's a geometry issue, not a noise issue,
           so smoothing/deadzone/outlier-rejection alone can't fix it.
           Clamping cumulative elevation keeps the camera away from that
           degenerate zone entirely.
        """
        max_delta = 0.05  # normalized-coordinate units; caps max spin per frame
        delta_x = max(-max_delta, min(max_delta, delta_x))
        delta_y = max(-max_delta, min(max_delta, delta_y))

        azimuth_step = -delta_x * 250.0
        elevation_step = -delta_y * 250.0

        # Clamp cumulative elevation to +/-80 degrees so we never reach the
        # +/-90 degree pole where up and view-direction go parallel.
        elevation_limit = 80.0
        proposed = self.elevation_angle + elevation_step
        if proposed > elevation_limit:
            elevation_step = elevation_limit - self.elevation_angle
        elif proposed < -elevation_limit:
            elevation_step = -elevation_limit - self.elevation_angle
        self.elevation_angle += elevation_step

        self.plotter.camera.azimuth += azimuth_step
        self.plotter.camera.elevation += elevation_step

        # Re-lock the up vector after every rotation so azimuth/elevation
        # calls can't accumulate floating-point roll drift over time.
        self.plotter.camera.up = (0.0, 0.0, 1.0)

    def zoom_camera(self, zoom_direction):
        """Zooms the 3D camera in or out."""
        if zoom_direction > 0:
            self.plotter.camera.zoom(1.03)
        elif zoom_direction < 0:
            self.plotter.camera.zoom(0.97)

# =====================================================================
# 2. MEDIAPIPE ASYNC CALLBACK & SETUP
# =====================================================================
latest_hand_result = None

def update_result(result, output_image, timestamp_ms):
    global latest_hand_result
    latest_hand_result = result

# =====================================================================
# 3. MAIN INTEGRATED EXECUTION LOOP
# =====================================================================
def main():
    DICOM_FOLDER = "DICOM"

    if not os.path.exists(DICOM_FOLDER):
        print(f"Error: Folder path '{DICOM_FOLDER}' does not exist.")
        return

    engine = Aegis3DEngine(DICOM_FOLDER)
    engine.plotter.show(interactive_update=True)

    base_options = python.BaseOptions(model_asset_path='hand_landmarker.task')
    options = vision.HandLandmarkerOptions(
        base_options=base_options,
        num_hands=2,
        min_hand_detection_confidence=0.7,
        min_tracking_confidence=0.7,
        running_mode=vision.RunningMode.LIVE_STREAM,
        result_callback=update_result
    )

    detector = vision.HandLandmarker.create_from_options(options)

    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    hand_filters = {
        "Left": EMAFilter(alpha=0.3),
        "Right": EMAFilter(alpha=0.3)
    }

    connections = [
        (0, 1), (1, 2), (2, 3), (3, 4),        # Thumb
        (0, 5), (5, 6), (6, 7), (7, 8),        # Index
        (5, 9), (9, 10), (10, 11), (11, 12),   # Middle
        (9, 13), (13, 14), (14, 15), (15, 16), # Ring
        (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)  # Pinky
    ]

    prev_palm = {}

    print("\n--- Aegis-Touch Active Gesture Controls ---")
    print("1. Move Palm Left/Right/Up/Down  -> Rotate 3D DICOM View")
    print("2. Move Hand Height (Y-axis)     -> Live Tissue Melting (Opacity Fade)")
    print("3. Pinch Thumb (#4) & Index (#8) -> Zoom IN")
    print("4. Pinch Thumb (#4) & Middle(#12)-> Zoom OUT")
    print("5. Press 'q' in OpenCV Window    -> Exit\n")

    while cap.isOpened():
        success, frame = cap.read()
        if not success:
            print("Empty frame")
            continue

        frame = cv2.flip(frame, 1)
        h, w, _ = frame.shape

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        timestamp_ms = int(time.time() * 1000)
        detector.detect_async(mp_image, timestamp_ms)

        active_labels = set()

        if latest_hand_result and latest_hand_result.hand_landmarks:
            for idx, hand_landmarks in enumerate(latest_hand_result.hand_landmarks):
                pixel_landmarks = []
                try:
                    hand_label = latest_hand_result.handedness[idx][0].category_name
                except IndexError:
                    hand_label = f"Unknown_{idx}"

                active_labels.add(hand_label)

                if hand_label not in hand_filters:
                    hand_filters[hand_label] = EMAFilter(alpha=0.3)

                active_filter = hand_filters[hand_label]

                raw_coords = np.array([[lm.x, lm.y, lm.z] for lm in hand_landmarks])
                smoothed_coords = active_filter.filter(raw_coords)

                for coord in smoothed_coords:
                    cx, cy = int(coord[0] * w), int(coord[1] * h)
                    pixel_landmarks.append((cx, cy))
                    cv2.circle(frame, (cx, cy), 5, (0, 255, 0), cv2.FILLED)

                for start_idx, end_idx in connections:
                    cv2.line(frame, pixel_landmarks[start_idx], pixel_landmarks[end_idx], (255, 255, 255), 2)

                # =====================================================
                # 3D DICOM GESTURE CONTROL MAPPINGS
                # =====================================================
                palm_x, palm_y = smoothed_coords[9][0], smoothed_coords[9][1]

                wrist_pt = np.array(pixel_landmarks[0])
                palm_pt = np.array(pixel_landmarks[9])
                hand_size = np.linalg.norm(wrist_pt - palm_pt)

                thumb_pt = np.array(pixel_landmarks[4])
                index_pt = np.array(pixel_landmarks[8])
                middle_pt = np.array(pixel_landmarks[12])

                index_ratio = (np.linalg.norm(thumb_pt - index_pt) / hand_size) if hand_size > 0 else 999
                middle_ratio = (np.linalg.norm(thumb_pt - middle_pt) / hand_size) if hand_size > 0 else 999

                if index_ratio < 0.25:          # Thumb + Index -> Zoom In
                    engine.zoom_camera(1)
                    prev_palm.pop(hand_label, None)  # avoid a jump when rotation resumes
                elif middle_ratio < 0.25:       # Thumb + Middle -> Zoom Out
                    engine.zoom_camera(-1)
                    prev_palm.pop(hand_label, None)
                else:
                    # Rotation Logic with Outlier Rejection
                    if hand_label in prev_palm:
                        prev_x, prev_y = prev_palm[hand_label]
                        delta_x = palm_x - prev_x
                        delta_y = palm_y - prev_y

                        # 1. OUTLIER REJECTION: If jump is massive (> 5% of screen), ignore it!
                        if abs(delta_x) > 0.05 or abs(delta_y) > 0.05:
                            pass
                        # 2. DEADZONE: If it's a valid, deliberate movement, apply it.
                        elif abs(delta_x) > 0.008 or abs(delta_y) > 0.008:
                            engine.rotate_camera(delta_x, delta_y)

                    prev_palm[hand_label] = (palm_x, palm_y)

                # Tissue Melting (Hand Height -> Opacity Fade)
                melt_factor = 1.0 - palm_y
                engine.set_tissue_melt(melt_factor)

                cv2.putText(frame, f"Tissue Melt: {int(melt_factor*100)}% ({hand_label})",
                            (20, 40 + 30 * idx), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        # Reset state for ANY hand that dropped out of frame this frame,
        # so it doesn't jump or swoop when it reappears at a new position.
        for label in list(prev_palm.keys()):
            if label not in active_labels:
                prev_palm.pop(label, None)
                if label in hand_filters:
                    hand_filters[label].reset()

        engine.plotter.update()

        cv2.imshow("Aegis-Touch: Tasks API Tracking", frame)
        if cv2.waitKey(5) & 0xFF == ord('q'):
            break

    cap.release()
    detector.close()
    engine.plotter.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()