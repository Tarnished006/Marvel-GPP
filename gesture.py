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

    # ── Pinch thresholds (ratio = contact distance / palm scale) ──
    #   PINCH_TRIGGER  = 0.20 → highly responsive, natural physical thumb+index contact without requiring raised fingers
    #   PINCH_RELEASE  = 0.26 → clean, crisp release threshold with solid hysteresis
    #   CLICK_COOLDOWN = 0.10 → highly responsive clicking & double-clicking
    PINCH_TRIGGER  = 0.20
    PINCH_RELEASE  = 0.26
    CLICK_COOLDOWN = 0.10

    def __init__(self):
        super().__init__()
        self.running = True
        self.air_mouse_enabled = False

        # 1€ Filter tuned for zero-latency, lag-free real mouse agility:
        # mincutoff=1.0 : eliminates micro-jitter while staying zero-latency
        # beta=0.10      : smooth acceleration adapting to hand speed
        self.mouse_filter = OneEuroFilter(
            freq=30.0,
            mincutoff=1.0,
            beta=0.10,
            dcutoff=1.0,
        )
        self._last_cursor = None
        self._pinch_lock_pos = None
        self.single_hand_filter = EMAFilter(alpha=0.75)

        self.is_pinching   = False
        self.last_click_ts = 0.0
        self._last_ts_ms   = 0

        # ── Directional thumb gestures → Air Mouse ON / OFF ───────────────────
        # 👍 Thumbs-up  (held 1.0s) → turn Air Mouse ON
        # 👎 Thumbs-down (held 1.0s) → turn Air Mouse OFF
        # Works on EITHER hand with reliable 1.0s hold and 2.0s cooldown.
        self.THUMBS_HOLD_SECS  = 1.0    # 1.0s deliberate hold
        self.THUMBS_COOLDOWN   = 2.0    # 2.0s cooldown after switching

        # Thumbs-UP tracker
        self._up_start_ts  = 0.0
        self._up_fired     = False

        # Thumbs-DOWN tracker
        self._dn_start_ts  = 0.0
        self._dn_fired     = False

        # Shared last-fire timestamp (both directions respect the cooldown)
        self._thumbs_last_ts = 0.0

        # Hand landmark filters (alpha=0.80: fast response, low latency)
        self.hand_filters = {
            "Left":  EMAFilter(alpha=0.80),
            "Right": EMAFilter(alpha=0.80),
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
        if not enabled:
            self.last_click_ts = 0.0
            if self.is_pinching:
                self.is_pinching = False
                signal_bus.pinch_ended.emit()
            self._pinch_lock_pos = None
            self.mouse_filter.reset()

    # ─────────────────────────── Helpers ──────────────────────────────────────
    @staticmethod
    def _dist_to_segment(p: "np.ndarray", a: "np.ndarray", b: "np.ndarray") -> float:
        """Calculates distance from point p to line segment ab in 2D or 3D."""
        ab = b - a
        l2 = float(np.dot(ab, ab))
        if l2 < 1e-6:
            return float(np.linalg.norm(p - a))
        t = float(np.clip(np.dot(p - a, ab) / l2, 0.0, 1.0))
        return float(np.linalg.norm(p - (a + t * ab)))

    @staticmethod
    def _is_thumbs_up(sm: "np.ndarray") -> bool:
        """
        Thumbs-up: thumb tip is pointing up and is higher than all curled fingertips.
        In open palms (3D rotate) or zoom pinches, index/middle fingers are extended
        or level with the thumb, so they are naturally rejected.
        """
        thumb_tip = sm[4]
        thumb_mcp = sm[2]

        # 1. Thumb tip is pointing up relative to its MCP joint
        if not (thumb_mcp[1] - thumb_tip[1] > 0.03):
            return False

        # 2. Thumb tip is higher than all 4 other fingertips (in image coords y is down)
        for tip in (8, 12, 16, 20):
            if not (thumb_tip[1] < sm[tip, 1] - 0.02):
                return False

        # 3. Anti-pinch check: thumb tip is not touching index/middle tip
        if float(np.linalg.norm(thumb_tip[:2] - sm[8, :2])) < 0.06:
            return False

        return True

    @staticmethod
    def _is_thumbs_down(sm: "np.ndarray") -> bool:
        """
        Thumbs-down: thumb tip is pointing down and is lower than all other fingertips.
        """
        thumb_tip = sm[4]
        thumb_mcp = sm[2]

        # 1. Thumb tip is pointing down relative to its MCP joint
        if not (thumb_tip[1] - thumb_mcp[1] > 0.03):
            return False

        # 2. Thumb tip is lower than all 4 other fingertips
        for tip in (8, 12, 16, 20):
            if not (thumb_tip[1] > sm[tip, 1] + 0.02):
                return False

        # 3. Anti-pinch check
        if float(np.linalg.norm(thumb_tip[:2] - sm[8, :2])) < 0.06:
            return False

        return True

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
            num_detected = len(result.hand_landmarks) if (result and result.hand_landmarks) else 0
            signal_bus.tracking_confidence.emit(
                1.0 if num_detected > 0 else 0.0
            )

            any_thumbs_up = False
            any_thumbs_down = False
            thumbs_up_px = None
            thumbs_down_px = None

            if num_detected > 0:
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
                    # Use sticky single_hand_filter when only 1 hand is visible to prevent
                    # filter resets if MediaPipe's handedness label flickers on screen edges.
                    if num_detected == 1:
                        filt = self.single_hand_filter
                    else:
                        if label not in self.hand_filters:
                            self.hand_filters[label] = EMAFilter(alpha=0.75)
                        filt = self.hand_filters[label]

                    raw = np.array([[lm.x, lm.y, lm.z] for lm in hand_lms])
                    sm  = filt.filter(raw)  # shape (21,3), normalized 0-1

                    # ── Draw skeleton ───────────────────────────────────────
                    px = [(int(sm[i,0]*w), int(sm[i,1]*h)) for i in range(21)]
                    for pt in px:
                        cv2.circle(frame, pt, 4, (0,220,0), -1)
                    for a, b in self.connections:
                        cv2.line(frame, px[a], px[b], (255,255,255), 1)

                    # Determine air mouse hand: if 1 hand present, it drives the mouse
                    if self.air_mouse_enabled:
                        is_mouse_hand = True if num_detected == 1 else (label == self.AIR_MOUSE_HAND)
                    else:
                        is_mouse_hand = False

                    # ── Pure Index & Thumb Fingertip Pinch Measurement ────────
                    # Measures strictly between Thumb (tip 4 & distal pad) and Index (tip 8 & distal pad [8, 7]).
                    # Strictly excludes intermediate segment [7, 6] and PIP knuckle to prevent false clicks when fingers relax or curl.
                    wrist_px = np.array(px[0],  dtype=float)
                    palm_px  = np.array(px[9],  dtype=float)
                    thumb_px = np.array(px[4],  dtype=float)
                    thumb_ip = np.array(px[3],  dtype=float)
                    thumb_pad = 0.70 * thumb_px + 0.30 * thumb_ip

                    index_px = np.array(px[8],  dtype=float)
                    mid_px   = np.array(px[12], dtype=float)
                    ring_px  = np.array(px[16], dtype=float)
                    pinky_px = np.array(px[20], dtype=float)

                    p8 = index_px
                    p7 = np.array(px[7], dtype=float)

                    # 2D fingertip contact distance (index tip 8 and distal pad [8, 7])
                    d_2d = min(
                        float(np.linalg.norm(thumb_px - index_px)),
                        self._dist_to_segment(thumb_px, p8, p7),
                        self._dist_to_segment(thumb_pad, p8, p7),
                    )

                    # Rigid skeletal palm scale (invariant to finger curling or reach)
                    palm_w = float(np.linalg.norm(np.array(px[5], dtype=float) - np.array(px[17], dtype=float)))
                    palm_h = float(np.linalg.norm(wrist_px - palm_px))
                    ref_2d = max(palm_w * 1.25, palm_h * 1.1, 35.0)
                    ratio_2d = d_2d / ref_2d

                    # 3D contact distance (rotation/perspective invariant, especially in top-left/screen corners)
                    p3 = np.array([[sm[i, 0] * w, sm[i, 1] * h, sm[i, 2] * w] for i in range(21)])
                    p3_thumb_tip = p3[4]
                    p3_thumb_pad = 0.70 * p3[4] + 0.30 * p3[3]
                    p3_8, p3_7   = p3[8], p3[7]

                    d_3d = min(
                        float(np.linalg.norm(p3_thumb_tip - p3_8)),
                        self._dist_to_segment(p3_thumb_tip, p3_8, p3_7),
                        self._dist_to_segment(p3_thumb_pad, p3_8, p3_7),
                    )
                    palm_w_3d = float(np.linalg.norm(p3[5] - p3[17]))
                    palm_h_3d = float(np.linalg.norm(p3[0] - p3[9]))
                    ref_3d = max(palm_w_3d * 1.25, palm_h_3d * 1.1, 35.0)
                    ratio_3d = d_3d / ref_3d

                    idx_ratio = min(ratio_2d, ratio_3d)

                    # Distances from thumb to other fingertips in 2D and 3D:
                    d_mid_2d   = float(np.linalg.norm(thumb_px - mid_px))
                    d_ring_2d  = float(np.linalg.norm(thumb_px - ring_px))
                    d_pinky_2d = float(np.linalg.norm(thumb_px - pinky_px))
                    mid_ratio  = d_mid_2d / ref_2d

                    d_mid_3d   = float(np.linalg.norm(p3_thumb_tip - p3[12]))
                    d_ring_3d  = float(np.linalg.norm(p3_thumb_tip - p3[16]))
                    d_pinky_3d = float(np.linalg.norm(p3_thumb_tip - p3[20]))
                    mid_ratio_3d = d_mid_3d / ref_3d

                    # ── Multi-Finger Exclusivity with Profile-View Protection ──
                    # In leftmost screen positions, reaching across the chest presents the hand in profile.
                    # In 2D, the middle finger is physically behind the index finger in depth, compressing
                    # the 2D gap. Evaluating in both 2D and 3D metric space ensures effortless clicking
                    # without interference from fingers behind the index:
                    is_index_closest = (
                        (d_2d <= d_mid_2d + 3.0 or d_3d <= d_mid_3d) and
                        (d_2d <= d_ring_2d or d_3d <= d_ring_3d) and
                        (d_2d <= d_pinky_2d or d_3d <= d_pinky_3d)
                    )
                    is_other_fingers_clear = (
                        (mid_ratio >= 0.14 or mid_ratio_3d >= 0.16) and
                        (d_2d < d_mid_2d * 0.90 or d_3d < d_mid_3d * 0.80)
                    )
                    is_clean_index_pinch = is_index_closest and is_other_fingers_clear

                    if is_mouse_hand and self.air_mouse_enabled:
                        # Projected index MCP tracking anchor:
                        # sm[5] (index MCP knuckle) does NOT curl when making a pinch, eliminating
                        # the 322-pixel deflection caused by fingertip tracking.
                        # Projecting 15% along metacarpal axis provides natural, intuitive cursor reach.
                        hand_dir = sm[5] - sm[0]
                        track_pt = sm[5] + 0.15 * hand_dir

                        # Ergonomic interaction box tailored for natural right-arm range:
                        # - x_min = 0.28, x_max = 0.72
                        # - y_min = 0.20, y_max = 0.65
                        x_min, x_max = 0.28, 0.72
                        y_min, y_max = 0.20, 0.65

                        raw_x = float(np.clip((track_pt[0] - x_min) / (x_max - x_min), 0.0, 1.0))
                        raw_y = float(np.clip((track_pt[1] - y_min) / (y_max - y_min), 0.0, 1.0))

                        # Pinch Lock Damping:
                        # ONLY lock coordinates during an active click/drag (when is_pinching is True).
                        # Never clamp or jitter during hovering, completely eliminating cursor wobble.
                        if self.is_pinching:
                            if self._pinch_lock_pos is None:
                                self._pinch_lock_pos = (raw_x, raw_y)
                            raw_x = self._pinch_lock_pos[0]
                            raw_y = self._pinch_lock_pos[1]
                        else:
                            self._pinch_lock_pos = None

                        # 1€ Filter: zero lag, fluid tracking
                        cur_t = time.time()
                        smoothed = self.mouse_filter.filter(
                            np.array([raw_x, raw_y]), timestamp=cur_t
                        )
                        cx, cy = float(smoothed[0]), float(smoothed[1])

                        # Emit cursor movement FIRST so UI thread updates coordinates before pinch event
                        self._last_cursor = (cx, cy)
                        signal_bus.cursor_moved.emit(cx, cy)

                        # ── Pinch-to-click / drag state machine ──────────────────
                        now = cur_t
                        if is_clean_index_pinch and (idx_ratio < self.PINCH_TRIGGER):
                            if not self.is_pinching and (now - self.last_click_ts >= self.CLICK_COOLDOWN):
                                self.is_pinching = True
                                self.last_click_ts = now
                                signal_bus.pinch_started.emit()
                        elif idx_ratio > self.PINCH_RELEASE or not is_index_closest:
                            if self.is_pinching:
                                self.is_pinching = False
                                signal_bus.pinch_ended.emit()

                        # ── Visual HUD Ring / Pinch Depth Gauge ───────────
                        # Centered at midpoint between thumb tip & index tip for visual accuracy
                        hud_px = (int(0.5 * px[4][0] + 0.5 * px[8][0]), int(0.5 * px[4][1] + 0.5 * px[8][1]))
                        if self.is_pinching:
                            cv2.circle(frame, hud_px, 14, (0, 255, 0), 3)
                            cv2.putText(frame, "CLICK / DRAG",
                                        (hud_px[0] + 18, hud_px[1] - 8),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.60,
                                        (0, 255, 0), 2)
                        elif is_clean_index_pinch and idx_ratio < self.PINCH_RELEASE:
                            # Dynamic indicator: ring shrinks smoothly ONLY as index+thumb pinch closes
                            progress = float(np.clip(
                                (self.PINCH_RELEASE - idx_ratio) / (self.PINCH_RELEASE - self.PINCH_TRIGGER + 1e-5),
                                0.0, 1.0
                            ))
                            ring_r = int(12 - progress * 5)
                            g_val = int(150 + progress * 105)
                            b_val = int(255 - progress * 155)
                            cv2.circle(frame, hud_px, max(6, ring_r), (b_val, g_val, 0), 2)

                    # Accumulate Thumbs UP / DOWN gestures across all visible hands
                    if self._is_thumbs_up(sm):
                        any_thumbs_up = True
                        thumbs_up_px = px[4]
                    if self._is_thumbs_down(sm):
                        any_thumbs_down = True
                        thumbs_down_px = px[4]

                    # ══════════════════════════════════════════════════════════
                    # 3-D VIEWER CONTROL — rotation, zoom, tissue melt
                    # Routing logic:
                    #   Air mouse ON  → Mouse hand controls cursor ONLY
                    #                   (secondary hand controls 3D if 2 hands present)
                    #   Air mouse OFF → Both hands control 3-D viewer
                    # ══════════════════════════════════════════════════════════
                    if self.air_mouse_enabled:
                        drives_3d = (num_detected > 1 and not is_mouse_hand)
                    else:
                        drives_3d = True

                    if drives_3d:
                        palm_x = float(sm[9,0])
                        palm_y = float(sm[9,1])
                        palm_px_pos = px[9]

                        if is_clean_index_pinch and (idx_ratio < 0.18):        # thumb+index pinch → zoom in
                            signal_bus.zoom_command.emit(1)
                            self.prev_palm.pop(label, None)
                            cv2.putText(frame, "ZOOM IN (+)",
                                        (palm_px_pos[0]-40, palm_px_pos[1]-20),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                        (0,255,255), 2)

                        elif (d_mid_2d < d_2d) and (mid_ratio < 0.18):      # thumb+middle pinch → zoom out
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

            # ── 👍 Thumbs-UP  → Air Mouse ON  ──────────────────────
            # ── 👎 Thumbs-DOWN → Air Mouse OFF ──────────────────────
            # Evaluated once per frame across all visible hands.
            now_t       = time.time()
            cooldown_ok = (now_t - self._thumbs_last_ts) >= self.THUMBS_COOLDOWN

            # ── Thumbs-UP state machine ──────────────────────────────
            if any_thumbs_up and thumbs_up_px is not None:
                if self._up_start_ts == 0.0:
                    self._up_start_ts = now_t
                    self._up_fired    = False

                hold_elapsed = now_t - self._up_start_ts

                if not self._up_fired and cooldown_ok:
                    progress = min(hold_elapsed / self.THUMBS_HOLD_SECS, 1.0)
                    cv2.ellipse(
                        frame, thumbs_up_px, (18, 18), -90,
                        0, int(progress * 360), (0, 255, 100), 3,
                    )
                    cv2.putText(
                        frame, "ON",
                        (thumbs_up_px[0] + 22, thumbs_up_px[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 255, 100), 2,
                    )
                    if hold_elapsed >= self.THUMBS_HOLD_SECS:
                        signal_bus.air_mouse_gesture_set.emit(True)
                        self._thumbs_last_ts = now_t
                        self._up_fired       = True
                        cv2.putText(
                            frame, "AIR MOUSE: ON",
                            (thumbs_up_px[0] - 50, thumbs_up_px[1] - 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 100), 2,
                        )
                elif self._up_fired:
                    cv2.putText(
                        frame, "LOCKED",
                        (thumbs_up_px[0] + 22, thumbs_up_px[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (80, 80, 80), 1,
                    )
            else:
                self._up_start_ts = 0.0
                self._up_fired    = False

            # ── Thumbs-DOWN state machine ────────────────────────────
            if any_thumbs_down and thumbs_down_px is not None:
                if self._dn_start_ts == 0.0:
                    self._dn_start_ts = now_t
                    self._dn_fired    = False

                hold_elapsed = now_t - self._dn_start_ts

                if not self._dn_fired and cooldown_ok:
                    progress = min(hold_elapsed / self.THUMBS_HOLD_SECS, 1.0)
                    cv2.ellipse(
                        frame, thumbs_down_px, (18, 18), -90,
                        0, int(progress * 360), (0, 100, 255), 3,
                    )
                    cv2.putText(
                        frame, "OFF",
                        (thumbs_down_px[0] + 22, thumbs_down_px[1] + 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 100, 255), 2,
                    )
                    if hold_elapsed >= self.THUMBS_HOLD_SECS:
                        signal_bus.air_mouse_gesture_set.emit(False)
                        self._thumbs_last_ts = now_t
                        self._dn_fired       = True
                        cv2.putText(
                            frame, "AIR MOUSE: OFF",
                            (thumbs_down_px[0] - 55, thumbs_down_px[1] + 32),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 100, 255), 2,
                        )
                elif self._dn_fired:
                    cv2.putText(
                        frame, "LOCKED",
                        (thumbs_down_px[0] + 22, thumbs_down_px[1] + 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (80, 80, 80), 1,
                    )
            else:
                self._dn_start_ts = 0.0
                self._dn_fired    = False

            # ── Clean up state for hands that left the frame ─────────────────
            for gone in list(self.prev_palm):
                if gone not in active_labels:
                    del self.prev_palm[gone]
                    self.hand_filters.get(gone, EMAFilter(alpha=0.80)).reset()

            # If the air-mouse hand disappeared, release any held click & reset filter
            num_detected = len(result.hand_landmarks) if (result and result.hand_landmarks) else 0
            mouse_hand_present = (num_detected > 0) and (
                num_detected == 1 or self.AIR_MOUSE_HAND in active_labels
            )
            if not mouse_hand_present:
                if self.is_pinching:
                    self.is_pinching = False
                    signal_bus.pinch_ended.emit()
                self._pinch_lock_pos = None
                self.mouse_filter.reset()
                self.single_hand_filter.reset()

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