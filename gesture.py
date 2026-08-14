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
from signal_bus import signal_bus
from PyQt6.QtGui import QImage
from PyQt6.QtCore import QThread

# NOTE: No pyautogui here. All OS cursor movement and clicks are handled
# in the main UI thread (main.py) via QCursor.setPos() and pyautogui.click()
# to avoid OS throttling of background thread synthetic input.

class GestureWorker(QThread):
    AIR_MOUSE_HAND = "Right"

    # Robust Two-Tier Pinch Safeguards
    PINCH_THRESHOLD = 0.15          # Strict trigger threshold (firm, deliberate pinch)
    PINCH_RELEASE_THRESHOLD = 0.25  # Explicit release threshold (creates stable deadzone 0.15-0.25)
    CLICK_COOLDOWN = 0.5            # Minimum seconds required between consecutive clicks

    def __init__(self):
        super().__init__()
        self.running = True
        self.latest_hand_result = None

        self.air_mouse_enabled = False
        # alpha=0.15: stable smoothing in normalized space (0.0-1.0).
        # Main thread scales to screen pixels via QCursor, avoiding OS throttling.
        self.mouse_filter = EMAFilter(alpha=0.15)
        self.is_pinching = False
        self.last_click_time = 0.0  # Time-based cooldown tracker

        self.hand_filters = {
            "Left": EMAFilter(alpha=0.45),
            "Right": EMAFilter(alpha=0.45)
        }
        self.prev_palm = {}

        signal_bus.air_mouse_toggle.connect(self.set_air_mouse)

        self.connections = [
            (0, 1), (1, 2), (2, 3), (3, 4),        # Thumb
            (0, 5), (5, 6), (6, 7), (7, 8),        # Index
            (5, 9), (9, 10), (10, 11), (11, 12),   # Middle
            (9, 13), (13, 14), (14, 15), (15, 16), # Ring
            (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)  # Pinky
        ]

        base_options = python.BaseOptions(model_asset_path='hand_landmarker.task')
        options = vision.HandLandmarkerOptions(
            base_options=base_options,
            num_hands=2,
            min_hand_detection_confidence=0.7,
            min_tracking_confidence=0.7,
            running_mode=vision.RunningMode.LIVE_STREAM,
            result_callback=self.update_result
        )
        self.detector = vision.HandLandmarker.create_from_options(options)

    def set_air_mouse(self, enabled: bool):
        self.air_mouse_enabled = enabled
        if not enabled:
            self.mouse_filter.reset()
            self.last_click_time = 0.0
            if self.is_pinching:
                signal_bus.pinch_ended.emit()
                self.is_pinching = False

    def update_result(self, result, output_image, timestamp_ms):
        self.latest_hand_result = result
        if result.hand_landmarks:
            signal_bus.tracking_confidence.emit(1.0)
        else:
            signal_bus.tracking_confidence.emit(0.0)

    def run(self):
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        while self.running:
            success, frame = cap.read()
            if not success:
                continue

            frame = cv2.flip(frame, 1)
            h, w, _ = frame.shape

            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
            timestamp_ms = int(time.time() * 1000)
            self.detector.detect_async(mp_image, timestamp_ms)

            active_labels = set()

            if self.latest_hand_result and self.latest_hand_result.hand_landmarks:
                for idx, hand_landmarks in enumerate(self.latest_hand_result.hand_landmarks):
                    pixel_landmarks = []
                    try:
                        hand_label = self.latest_hand_result.handedness[idx][0].category_name
                    except IndexError:
                        hand_label = f"Unknown_{idx}"

                    if hand_label == "Right":
                        hand_label = "Left"
                    elif hand_label == "Left":
                        hand_label = "Right"

                    active_labels.add(hand_label)

                    if hand_label not in self.hand_filters:
                        self.hand_filters[hand_label] = EMAFilter(alpha=0.45)

                    active_filter = self.hand_filters[hand_label]
                    raw_coords = np.array([[lm.x, lm.y, lm.z] for lm in hand_landmarks])
                    smoothed_coords = active_filter.filter(raw_coords)

                    for coord in smoothed_coords:
                        cx, cy = int(coord[0] * w), int(coord[1] * h)
                        pixel_landmarks.append((cx, cy))
                        cv2.circle(frame, (cx, cy), 5, (0, 255, 0), cv2.FILLED)

                    for start_idx, end_idx in self.connections:
                        cv2.line(frame, pixel_landmarks[start_idx], pixel_landmarks[end_idx], (255, 255, 255), 2)

                    is_air_mouse_hand = (hand_label == self.AIR_MOUSE_HAND)

                    if is_air_mouse_hand and self.air_mouse_enabled:
                        index_tip = smoothed_coords[8]
                        # 8% margin — covers full screen without over-stretching
                        margin = 0.08
                        adj_x = np.clip((index_tip[0] - margin) / (1.0 - 2 * margin), 0.0, 1.0)
                        adj_y = np.clip((index_tip[1] - margin) / (1.0 - 2 * margin), 0.0, 1.0)
                        # Smooth in normalised space; main thread converts to pixels
                        smoothed_norm = self.mouse_filter.filter(
                            np.array([[adj_x, adj_y, 0]])
                        )[0]
                        # Emit normalised floats — main thread calls QCursor.setPos()
                        signal_bus.cursor_moved.emit(
                            float(smoothed_norm[0]), float(smoothed_norm[1])
                        )

                    # =====================================================
                    # 2. 3D DICOM GESTURE CONTROL MAPPINGS
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

                    is_3d_hand = (not self.air_mouse_enabled) or (not is_air_mouse_hand)

                    # =====================================================
                    # 2. AIR MOUSE CLICK (HYSTERESIS + TIME-BASED COOLDOWN)
                    # =====================================================
                    if is_air_mouse_hand and self.air_mouse_enabled:
                        current_time = time.time()

                        # Strict Click Trigger: requires deliberate physical pinch + cooldown
                        if index_ratio < self.PINCH_THRESHOLD:
                            if not self.is_pinching and (current_time - self.last_click_time >= self.CLICK_COOLDOWN):
                                self.is_pinching = True
                                self.last_click_time = current_time
                                signal_bus.pinch_started.emit()
                        # Explicit Release Threshold: deadzone between 0.15 and 0.25 prevents state flipping
                        elif index_ratio > self.PINCH_RELEASE_THRESHOLD:
                            if self.is_pinching:
                                self.is_pinching = False
                                signal_bus.pinch_ended.emit()

                        # Visual HUD indicator at index fingertip
                        tip_cx, tip_cy = pixel_landmarks[8]
                        if self.is_pinching:
                            cv2.circle(frame, (tip_cx, tip_cy), 14, (0, 255, 255), 3)
                            cv2.putText(frame, "CLICK", (tip_cx + 15, tip_cy - 10),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                        else:
                            cv2.circle(frame, (tip_cx, tip_cy), 8, (255, 100, 0), 2)

                    # =====================================================
                    # 3. 3D DICOM GESTURE CONTROL MAPPINGS
                    # =====================================================
                    if is_3d_hand:
                        if index_ratio < 0.22:          # Thumb + Index pinch -> Zoom In
                            signal_bus.zoom_command.emit(1)
                            self.prev_palm.pop(hand_label, None)
                        elif middle_ratio < 0.22:       # Thumb + Middle pinch -> Zoom Out
                            signal_bus.zoom_command.emit(-1)
                            self.prev_palm.pop(hand_label, None)
                        else:                           # Open hand rotation
                            if hand_label in self.prev_palm:
                                prev_x, prev_y = self.prev_palm[hand_label]
                                delta_x = palm_x - prev_x
                                delta_y = palm_y - prev_y
                                
                                if abs(delta_x) <= 0.05 and abs(delta_y) <= 0.05:
                                    if abs(delta_x) > 0.008 or abs(delta_y) > 0.008:
                                        # 3 floats — matches pyqtSignal(float, float, float)
                                        signal_bus.hand_rotation.emit(
                                            float(delta_x), float(delta_y), 0.0
                                        )
                            self.prev_palm[hand_label] = (palm_x, palm_y)

                        # Tissue Melting
                        melt_factor = 1.0 - palm_y
                        signal_bus.tissue_melt.emit(melt_factor)

            for label in list(self.prev_palm.keys()):
                if label not in active_labels:
                    self.prev_palm.pop(label, None)
                    if label in self.hand_filters:
                        self.hand_filters[label].reset()

            if self.AIR_MOUSE_HAND not in active_labels:
                if self.is_pinching:
                    signal_bus.pinch_ended.emit()
                    self.is_pinching = False
                self.mouse_filter.reset()

            annotated_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h_img, w_img, ch = annotated_rgb.shape
            qt_image = QImage(annotated_rgb.data, w_img, h_img, ch * w_img, QImage.Format.Format_RGB888)
            signal_bus.camera_frame.emit(qt_image)

        cap.release()
        self.detector.close()

    def stop(self):
        self.running = False
        self.wait()