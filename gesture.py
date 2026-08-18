import sys
import time
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from ema_filter import EMAFilter, OneEuroFilter
from signal_bus import signal_bus
from PyQt6.QtGui import QImage
from PyQt6.QtCore import QThread

# All OS cursor movement and clicks happen in main.py (UI thread) via
# QCursor.setPos() + native mouse_event to avoid OS throttling of
# synthetic input from background threads.

class GestureWorker(QThread):
    # The right hand (after mirror flip) drives the air mouse.
    AIR_MOUSE_HAND = "Right"

    # ── Pinch thresholds (ratio = pinch distance / perspective-invariant palm scale) ──
    # Uses 2D pixel-space with multi-point knuckle reference to remain 100% reliable
    # across hand tilt, angle, and distance from the webcam.
    #
    # HYSTERESIS DESIGN:
    #   PINCH_TRIGGER  = 0.20 → firm natural pinch, easily reached from any angle
    #   PINCH_RELEASE  = 0.28 → clean release threshold creating an 0.08 deadzone
    #   CLICK_COOLDOWN = 0.25 → 250 ms cooldown prevents double-firing while
    #                           allowing responsive double-clicking.
    PINCH_TRIGGER  = 0.20
    PINCH_RELEASE  = 0.28
    CLICK_COOLDOWN = 0.25

    def __init__(self):
        super().__init__()
        self.running = True

        self.air_mouse_enabled = False

        # 1€ Filter for silky-smooth, zero-jitter, natural mouse feel:
        # mincutoff=1.10: natural agility without feeling sluggish or rigid.
        # beta=0.12: fluid acceleration matching natural hand velocity.
        self.mouse_filter = OneEuroFilter(
            freq=30.0,
            mincutoff=1.10,
            beta=0.12,
            dcutoff=1.0,
        )
        self._last_cursor = None
        self._lock_pos = None

        self.is_pinching   = False
        self.last_click_ts = 0.0
        self._last_ts_ms   = 0

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

        # Use synchronous VIDEO mode: ensures each frame is processed immediately
        # with zero queue backlog, zero callback delay, and zero stale-frame freezes.
        base_opts = python.BaseOptions(model_asset_path="hand_landmarker.task")
        opts = vision.HandLandmarkerOptions(
            base_options=base_opts,
            num_hands=2,
            min_hand_detection_confidence=0.60,
            min_tracking_confidence=0.60,
            running_mode=vision.RunningMode.VIDEO,
        )
        self.detector = vision.HandLandmarker.create_from_options(opts)

    # ─────────────────────────── Slots ────────────────────────────────────────
    def _on_air_mouse_toggle(self, enabled: bool):
        self.air_mouse_enabled = enabled
        self._lock_pos = None
        if not enabled:
            self.last_click_ts = 0.0
            if self.is_pinching:
                self.is_pinching = False
                signal_bus.pinch_ended.emit()
            self.mouse_filter.reset()

    # ─────────────────────────── Main loop ────────────────────────────────────
    def run(self):
        # On Windows, DirectShow with buffer_size=1 eliminates camera buffer lag entirely
        if sys.platform == "win32":
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(0)

        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)

        while self.running:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.01)
                continue

            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]

            now_ms = int(time.time() * 1000)
            ts = max(self._last_ts_ms + 1, now_ms)
            self._last_ts_ms = ts

            mp_img = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
            )

            # Synchronous per-frame detection: 100% synchronized with camera feed
            try:
                result = self.detector.detect_for_video(mp_img, ts)
            except Exception as e:
                print(f"[GestureWorker] detect error: {e}")
                continue

            active_labels = set()
            signal_bus.tracking_confidence.emit(
                1.0 if (result and result.hand_landmarks) else 0.0
            )

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

                    # ── Perspective-Invariant Pinch Measurement ────────────
                    # Compute multi-dimensional scale reference so hand tilt/pitch
                    # does not shrink the reference and prevent clicks from firing.
                    wrist_px = np.array(px[0],  dtype=float)
                    palm_px  = np.array(px[9],  dtype=float)
                    thumb_px = np.array(px[4],  dtype=float)
                    index_px = np.array(px[8],  dtype=float)
                    mid_px   = np.array(px[12], dtype=float)

                    palm_w = np.linalg.norm(np.array(px[5], dtype=float) - np.array(px[17], dtype=float))
                    palm_h = np.linalg.norm(wrist_px - palm_px)
                    idx_l  = (np.linalg.norm(np.array(px[5], dtype=float) - np.array(px[6], dtype=float)) +
                              np.linalg.norm(np.array(px[6], dtype=float) - np.array(px[7], dtype=float)) +
                              np.linalg.norm(np.array(px[7], dtype=float) - index_px))

                    ref = max(palm_w * 1.15, palm_h, idx_l * 0.75, 45.0)

                    # Multi-point pinch distance (tip-to-tip and tip-to-DIP)
                    d_tip = np.linalg.norm(thumb_px - index_px)
                    d_dip = np.linalg.norm(thumb_px - np.array(px[7], dtype=float))
                    pinch_dist = min(d_tip, d_dip)

                    idx_ratio = pinch_dist / ref
                    mid_ratio = np.linalg.norm(thumb_px - mid_px) / ref

                    if is_mouse_hand and self.air_mouse_enabled:
                        # Stable, nimble tracking point: blend of index fingertip (8) and knuckle (5)
                        track_pt = 0.70 * sm[8] + 0.30 * sm[5]

                        # Ergonomic interaction box tailored for natural arm reach:
                        # - Left bound is 0.22 (reaching right arm slightly left easily reaches leftmost edge 0.0)
                        # - Right bound is 0.80 (natural right extension reaches 1.0)
                        # - Top bound is 0.15, Bottom bound is 0.82
                        x_min, x_max = 0.22, 0.80
                        y_min, y_max = 0.15, 0.82

                        raw_x = float(np.clip((track_pt[0] - x_min) / (x_max - x_min), 0.0, 1.0))
                        raw_y = float(np.clip((track_pt[1] - y_min) / (y_max - y_min), 0.0, 1.0))

                        # 1€ Filter stabilizes position with natural mouse agility & zero jitter
                        cur_t = time.time()
                        smoothed = self.mouse_filter.filter(
                            np.array([raw_x, raw_y]), timestamp=cur_t
                        )
                        cx, cy = float(smoothed[0]), float(smoothed[1])

                        # ── Pinch-to-click state machine ──────────────────
                        now = cur_t
                        if idx_ratio < self.PINCH_TRIGGER:
                            if not self.is_pinching and (now - self.last_click_ts >= self.CLICK_COOLDOWN):
                                self.is_pinching = True
                                self.last_click_ts = now
                                self._lock_pos = (cx, cy)
                                signal_bus.pinch_started.emit()
                        elif idx_ratio > self.PINCH_RELEASE:
                            if self.is_pinching:
                                self.is_pinching = False
                                self._lock_pos = None
                                signal_bus.pinch_ended.emit()

                        # If currently clicking, lock position to eliminate click displacement
                        if self.is_pinching and self._lock_pos is not None:
                            emit_x, emit_y = self._lock_pos
                        else:
                            self._lock_pos = None
                            emit_x, emit_y = cx, cy

                        self._last_cursor = (emit_x, emit_y)
                        signal_bus.cursor_moved.emit(emit_x, emit_y)

                        # ── Visual HUD Ring / Pinch Depth Gauge ───────────
                        tip_px = px[8]
                        if self.is_pinching:
                            cv2.circle(frame, tip_px, 14, (0, 255, 0), 3)
                            cv2.putText(frame, "CLICK",
                                        (tip_px[0] + 18, tip_px[1] - 8),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.60,
                                        (0, 255, 0), 2)
                        else:
                            # Dynamic indicator: ring shrinks smoothly as pinch closes
                            progress = float(np.clip(
                                (self.PINCH_RELEASE - idx_ratio) / (self.PINCH_RELEASE - self.PINCH_TRIGGER + 1e-5),
                                0.0, 1.0
                            ))
                            ring_r = int(12 - progress * 5)
                            g_val = int(150 + progress * 105)
                            b_val = int(255 - progress * 155)
                            cv2.circle(frame, tip_px, max(6, ring_r), (b_val, g_val, 0), 2)

                    # ══════════════════════════════════════════════════════════
                    # 3-D VIEWER CONTROL — rotation, zoom, tissue melt
                    # Routing logic:
                    #   Air mouse ON  → RIGHT hand = cursor only
                    #                   LEFT  hand = 3-D control
                    #   Air mouse OFF → BOTH hands = 3-D control
                    # ══════════════════════════════════════════════════════════
                    if self.air_mouse_enabled:
                        drives_3d = (label != self.AIR_MOUSE_HAND)
                    else:
                        drives_3d = True

                    if drives_3d:
                        palm_x = float(sm[9,0])
                        palm_y = float(sm[9,1])
                        palm_px_pos = px[9]

                        if idx_ratio < 0.20:        # thumb+index → zoom in
                            signal_bus.zoom_command.emit(1)
                            self.prev_palm.pop(label, None)
                            cv2.putText(frame, "ZOOM IN (+)",
                                        (palm_px_pos[0]-40, palm_px_pos[1]-20),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                        (0,255,255), 2)

                        elif mid_ratio < 0.20:      # thumb+middle → zoom out
                            signal_bus.zoom_command.emit(-1)
                            self.prev_palm.pop(label, None)
                            cv2.putText(frame, "ZOOM OUT (-)",
                                        (palm_px_pos[0]-40, palm_px_pos[1]-20),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                        (0,255,255), 2)

                        else:                       # open palm → 3D camera rotation
                            if label in self.prev_palm:
                                px0, py0 = self.prev_palm[label]
                                dx = float(np.clip(palm_x - px0, -0.06, 0.06))
                                dy = float(np.clip(palm_y - py0, -0.06, 0.06))
                                
                                # Deadzone = 0.003 (filters micro-tremor)
                                if abs(dx) > 0.003 or abs(dy) > 0.003:
                                    signal_bus.hand_rotation.emit(dx, dy, 0.0)
                                    cv2.putText(frame, "3D ROTATE",
                                                (palm_px_pos[0]-35, palm_px_pos[1]-20),
                                                cv2.FONT_HERSHEY_SIMPLEX, 0.50,
                                                (0,230,255), 2)
                            self.prev_palm[label] = (palm_x, palm_y)

                        # Tissue melt driven by palm height
                        signal_bus.tissue_melt.emit(float(np.clip(1.0 - palm_y, 0.0, 1.0)))

            # ── Clean up state for hands that left the frame ─────────────────
            for gone in list(self.prev_palm):
                if gone not in active_labels:
                    del self.prev_palm[gone]
                    self.hand_filters.get(gone, EMAFilter(alpha=0.65)).reset()

            # If the air-mouse hand disappeared, release any held click & reset filter
            if self.AIR_MOUSE_HAND not in active_labels:
                if self.is_pinching:
                    self.is_pinching = False
                    signal_bus.pinch_ended.emit()
                self._lock_pos = None
                self.mouse_filter.reset()

            # ── Emit annotated camera frame ──────────────────────────────────
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h2, w2, ch = rgb.shape
            qimg_copy = QImage(rgb.data, w2, h2, ch * w2, QImage.Format.Format_RGB888).copy()
            signal_bus.camera_frame.emit(qimg_copy)

            # Prevent CPU thread starvation
            time.sleep(0.001)

        cap.release()
        try:
            self.detector.close()
        except Exception:
            pass

    def stop(self):
        self.running = False
        self.wait()