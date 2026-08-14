import time
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from ema_filter import EMAFilter
from signal_bus import signal_bus
from PyQt6.QtGui import QImage
from PyQt6.QtCore import QThread

# All OS cursor movement and clicks happen in main.py (UI thread) via
# QCursor.setPos() + pyautogui.click() to avoid OS throttling of
# synthetic input from background threads.

class GestureWorker(QThread):
    # The right hand (after mirror flip) drives the air mouse.
    AIR_MOUSE_HAND = "Right"

    # ── Pinch thresholds (ratio = thumb-index distance / wrist-palm distance) ──
    # Using 2D pixel-space is more reliable than 3D because MediaPipe's Z axis
    # is estimated and noisy. 2D ratios are consistent across hand distances.
    #
    # HYSTERESIS DESIGN:
    #   PINCH_TRIGGER  = 0.12 → requires a deliberate firm physical pinch to fire.
    #   PINCH_RELEASE  = 0.20 → fingers must separate to this ratio before the
    #                           state resets.  The 0.08-wide deadzone between the
    #                           two thresholds prevents the state machine from
    #                           bouncing rapidly between CLICK and RELEASE when
    #                           the hand hovers near the trigger boundary.
    #   CLICK_COOLDOWN = 0.50 → 500 ms minimum between consecutive clicks;
    #                           guards against double-firing on a single pinch.
    PINCH_TRIGGER  = 0.12   # Firm pinch required  → below this = click fires
    PINCH_RELEASE  = 0.20   # Clear separation needed → above this = release
    CLICK_COOLDOWN = 0.50   # Seconds between consecutive clicks (prevents spam)

    def __init__(self):
        super().__init__()
        self.running = True
        self.latest_hand_result = None

        self.air_mouse_enabled = False

        # alpha=0.72 → very responsive cursor.  High alpha = follows hand quickly.
        # EMAFilter formula: output = alpha*new + (1-alpha)*prev
        # 0.72 gives ~1.5 frame lag at 30 fps, which is imperceptible.
        self.mouse_filter = EMAFilter(alpha=0.72)
        self._last_cursor = None   # last emitted cursor pos; avoids jump on re-detect

        self.is_pinching   = False
        self.last_click_ts = 0.0

        # Hand landmark filters (alpha=0.65: smooth skeleton, fast response)
        self.hand_filters = {
            "Left":  EMAFilter(alpha=0.65),
            "Right": EMAFilter(alpha=0.65),
        }
        # Previous palm positions for per-hand rotation delta
        self.prev_palm = {}

        signal_bus.air_mouse_toggle.connect(self._on_air_mouse_toggle)

        self.connections = [
            (0,1),(1,2),(2,3),(3,4),               # Thumb
            (0,5),(5,6),(6,7),(7,8),               # Index
            (5,9),(9,10),(10,11),(11,12),           # Middle
            (9,13),(13,14),(14,15),(15,16),         # Ring
            (13,17),(0,17),(17,18),(18,19),(19,20), # Pinky
        ]

        base_opts = python.BaseOptions(model_asset_path="hand_landmarker.task")
        opts = vision.HandLandmarkerOptions(
            base_options=base_opts,
            num_hands=2,
            min_hand_detection_confidence=0.65,
            min_tracking_confidence=0.65,
            running_mode=vision.RunningMode.LIVE_STREAM,
            result_callback=self._on_result,
        )
        self.detector = vision.HandLandmarker.create_from_options(opts)

    # ─────────────────────────── Slots ────────────────────────────────────────
    def _on_air_mouse_toggle(self, enabled: bool):
        self.air_mouse_enabled = enabled
        if not enabled:
            # Don't reset mouse_filter here — causes cursor jump on re-enable.
            self.last_click_ts = 0.0
            if self.is_pinching:
                self.is_pinching = False
                signal_bus.pinch_ended.emit()

    def _on_result(self, result, _image, _ts):
        self.latest_hand_result = result
        signal_bus.tracking_confidence.emit(
            1.0 if result.hand_landmarks else 0.0
        )

    # ─────────────────────────── Main loop ────────────────────────────────────
    def run(self):
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)

        while self.running:
            ok, frame = cap.read()
            if not ok:
                continue

            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]

            mp_img = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
            )
            self.detector.detect_async(mp_img, int(time.time() * 1000))

            active_labels = set()
            result = self.latest_hand_result

            if result and result.hand_landmarks:
                for idx, hand_lms in enumerate(result.hand_landmarks):
                    # ── Label + mirror correction ──────────────────────────
                    try:
                        raw_label = result.handedness[idx][0].category_name
                    except IndexError:
                        raw_label = f"Unknown_{idx}"
                    # Camera is mirrored → swap Left/Right
                    label = "Left" if raw_label == "Right" else "Right"
                    active_labels.add(label)

                    # ── Landmark smoothing ──────────────────────────────────
                    if label not in self.hand_filters:
                        self.hand_filters[label] = EMAFilter(alpha=0.65)
                    filt = self.hand_filters[label]
                    raw = np.array([[lm.x, lm.y, lm.z] for lm in hand_lms])
                    sm  = filt.filter(raw)  # shape (21,3), normalized 0-1

                    # ── Draw skeleton ───────────────────────────────────────
                    px = [(int(sm[i,0]*w), int(sm[i,1]*h)) for i in range(21)]
                    for pt in px:
                        cv2.circle(frame, pt, 4, (0,220,0), -1)
                    for a, b in self.connections:
                        cv2.line(frame, px[a], px[b], (255,255,255), 1)

                    is_mouse_hand = (label == self.AIR_MOUSE_HAND)

                    # ── 2-D pinch ratio (wrist→palm = reference length) ─────
                    # We use pixel-space 2D only — Z is too noisy for distance.
                    wrist_px = np.array(px[0],  dtype=float)
                    palm_px  = np.array(px[9],  dtype=float)
                    thumb_px = np.array(px[4],  dtype=float)
                    index_px = np.array(px[8],  dtype=float)
                    mid_px   = np.array(px[12], dtype=float)

                    ref = np.linalg.norm(wrist_px - palm_px)
                    if ref < 1.0:
                        ref = 1.0  # safety guard for zero-division

                    idx_ratio = np.linalg.norm(thumb_px - index_px) / ref
                    mid_ratio = np.linalg.norm(thumb_px - mid_px)   / ref

                    # ══════════════════════════════════════════════════════════
                    # AIR MOUSE — cursor movement + pinch-to-click
                    # ══════════════════════════════════════════════════════════
                    if is_mouse_hand and self.air_mouse_enabled:
                        tip = sm[8]  # index fingertip (normalized)

                        # 10 % border margin → full-screen reachability
                        m = 0.10
                        ax = float(np.clip((tip[0] - m) / (1.0 - 2*m), 0.0, 1.0))
                        ay = float(np.clip((tip[1] - m) / (1.0 - 2*m), 0.0, 1.0))

                        smoothed = self.mouse_filter.filter(
                            np.array([[ax, ay, 0.0]])
                        )[0]
                        cx, cy = float(smoothed[0]), float(smoothed[1])
                        self._last_cursor = (cx, cy)

                        # Always emit current position (no locking — locking
                        # is what made the cursor appear "stuck")
                        signal_bus.cursor_moved.emit(cx, cy)

                        # ── Pinch-to-click state machine ──────────────────
                        now = time.time()
                        if idx_ratio < self.PINCH_TRIGGER:
                            if (not self.is_pinching
                                    and now - self.last_click_ts >= self.CLICK_COOLDOWN):
                                self.is_pinching   = True
                                self.last_click_ts = now
                                signal_bus.pinch_started.emit()
                        elif idx_ratio > self.PINCH_RELEASE:
                            if self.is_pinching:
                                self.is_pinching = False
                                signal_bus.pinch_ended.emit()

                        # HUD ring at fingertip
                        tip_px = px[8]
                        if self.is_pinching:
                            cv2.circle(frame, tip_px, 14, (0,255,255), 3)
                            cv2.putText(frame, "CLICK",
                                        (tip_px[0]+15, tip_px[1]-10),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                        (0,255,255), 2)
                        else:
                            cv2.circle(frame, tip_px, 9, (255,100,0), 2)

                    # ══════════════════════════════════════════════════════════
                    # 3-D VIEWER CONTROL — rotation, zoom, tissue melt
                    # Routing logic:
                    #   Air mouse ON  → RIGHT hand = cursor only
                    #                   LEFT  hand = 3-D control
                    #   Air mouse OFF → EITHER hand = 3-D control (first hand wins)
                    # ══════════════════════════════════════════════════════════
                    drives_3d = False
                    if self.air_mouse_enabled:
                        drives_3d = (label != self.AIR_MOUSE_HAND)
                    else:
                        # Without air mouse, use first hand detected (idx==0)
                        drives_3d = (idx == 0)

                    if drives_3d:
                        palm_x = float(sm[9,0])
                        palm_y = float(sm[9,1])

                        if idx_ratio < 0.22:        # thumb+index → zoom in
                            signal_bus.zoom_command.emit(1)
                            self.prev_palm.pop(label, None)

                        elif mid_ratio < 0.22:      # thumb+middle → zoom out
                            signal_bus.zoom_command.emit(-1)
                            self.prev_palm.pop(label, None)

                        else:                       # open palm → rotate
                            if label in self.prev_palm:
                                px0, py0 = self.prev_palm[label]
                                dx = float(np.clip(palm_x - px0, -0.06, 0.06))
                                dy = float(np.clip(palm_y - py0, -0.06, 0.06))
                                # Deadzone = 0.003 (filters micro-tremor)
                                if abs(dx) > 0.003 or abs(dy) > 0.003:
                                    signal_bus.hand_rotation.emit(dx, dy, 0.0)
                            self.prev_palm[label] = (palm_x, palm_y)

                        # Tissue melt driven by palm height
                        signal_bus.tissue_melt.emit(float(np.clip(1.0 - palm_y, 0.0, 1.0)))

            # ── Clean up state for hands that left the frame ─────────────────
            for gone in list(self.prev_palm):
                if gone not in active_labels:
                    del self.prev_palm[gone]
                    self.hand_filters.get(gone, EMAFilter(alpha=0.65)).reset()

            # If the air-mouse hand disappeared, release any held click
            if self.AIR_MOUSE_HAND not in active_labels:
                if self.is_pinching:
                    self.is_pinching = False
                    signal_bus.pinch_ended.emit()
                # Do NOT reset mouse_filter here — that causes a cursor jump
                # the next time the hand re-enters the frame.

            # ── Emit annotated camera frame ──────────────────────────────────
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h2, w2, ch = rgb.shape
            signal_bus.camera_frame.emit(
                QImage(rgb.data, w2, h2, ch * w2, QImage.Format.Format_RGB888)
            )

        cap.release()
        self.detector.close()

    def stop(self):
        self.running = False
        self.wait()