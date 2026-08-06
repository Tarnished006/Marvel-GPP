import cv2
import time
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from ema_filter import EMAFilter # Assumes your class name is 'ema' inside ema_filter.py

# 1. Global variable to store asynchronous results
latest_hand_result = None

# Callback function to capture async output
def update_result(result, output_image, timestamp_ms):
    global latest_hand_result
    latest_hand_result = result

# 2. Configure MediaPipe Hand Landmarker Task
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

# 3. Setup OpenCV, CLAHE, and the EMA Filter OUTSIDE the loop
clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
cap = cv2.VideoCapture(0)

# Instantiate the EMA filter ONCE here so it can maintain history across frames
hand_filters = {
    "Left": EMAFilter(alpha=0.4),
    "Right": EMAFilter(alpha=0.4)
}
while cap.isOpened():
    success, frame = cap.read()
    if not success:
        print("Empty frame")
        continue

    # Apply CLAHE to L channel in LAB color space
    # lab_colours = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    # l, a, b = cv2.split(lab_colours)
    # lab_clahe = clahe.apply(l)
    # lab_colours = cv2.merge((lab_clahe, a, b))
    # frame_clahe = cv2.cvtColor(lab_colours, cv2.COLOR_LAB2BGR)
    
    # Convert to RGB & create MediaPipe Image
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
    
    # Run detector asynchronously
    timestamp_ms = int(time.time() * 1000)
    detector.detect_async(mp_image, timestamp_ms)

    # 4. Draw & Filter Landmarks
    if latest_hand_result and latest_hand_result.hand_landmarks:
        h, w, _ = frame.shape
        
        connections = [
            (0, 1), (1, 2), (2, 3), (3, 4),        # Thumb
            (0, 5), (5, 6), (6, 7), (7, 8),        # Index
            (5, 9), (9, 10), (10, 11), (11, 12),   # Middle
            (9, 13), (13, 14), (14, 15), (15, 16), # Ring
            (13, 17), (0, 17), (17, 18), (18, 19), (19, 20) # Pinky
        ]

        for idx, hand_landmarks in enumerate(latest_hand_result.hand_landmarks):
            pixel_landmarks = []
            try:
                hand_label = latest_hand_result.handedness[idx][0].category_name
            except IndexError:
                # If MediaPipe fails to classify the hand, assign a safe fallback name
                hand_label = f"Unknown_{idx}"
            
            # Dynamically ensure a filter exists for this label
            if hand_label not in hand_filters:
                hand_filters[hand_label] = EMAFilter(alpha=0.4)
                
            active_filter = hand_filters[hand_label]
            # Extract raw landmarks into Nx3 array [x, y, z]
            raw_coords = np.array([[lm.x, lm.y, lm.z] for lm in hand_landmarks])
            
            # Apply EMA smoothing filter
            smoothed_coords = active_filter.filter(raw_coords)
            
            # Draw joints using SMOOTHED coordinates
            for coord in smoothed_coords:
                cx, cy = int(coord[0] * w), int(coord[1] * h)
                pixel_landmarks.append((cx, cy))
                
                # Green circles for joints
                cv2.circle(frame, (cx, cy), 5, (0, 255, 0), cv2.FILLED)

            # Draw connecting white lines
            for start_idx, end_idx in connections:
                start_point = pixel_landmarks[start_idx]
                end_point = pixel_landmarks[end_idx]
                cv2.line(frame, start_point, end_point, (255, 255, 255), 2)
    else:
        # Reset filter state when no hand is visible to prevent "dragging" artifacts when a hand reappears
        hand_filters["Left"].reset()
        hand_filters["Right"].reset()
    
    cv2.imshow("Aegis-Touch: Tasks API Tracking", frame)
    if cv2.waitKey(5) & 0xFF == ord('q'):
        break

# Cleanup
cap.release()
detector.close()
cv2.destroyAllWindows()