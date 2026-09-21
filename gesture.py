import sys
import time
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from ema_filter import EMAFilter, OneEuroFilter
from signal_bus import signal_bus
import threading
from PyQt6.QtGui import QImage
from PyQt6.QtCore import QThread

# All OS cursor movement and clicks happen in main.py (UI thread) via
# QCursor.setPos() + native mouse_event to avoid OS throttling of
# synthetic input from background threads.

class CameraStream:
    """
    High-speed threaded camera capture stream.
    Decouples OpenCV DirectShow I/O from MediaPipe inference so cap.read()
    never blocks or lags behind real-time hand motion.
    """
    def __init__(self, src=0, width=640, height=480, fps=30):
        self.cap = None
        self.running = False
        
        # Try multiple camera indices and backends
        def try_open_camera(idx):
            if sys.platform == "win32":
                # Try MSMF first, then DSHOW, then default
                for backend in [cv2.CAP_MSMF, cv2.CAP_DSHOW, cv2.CAP_ANY]:
                    cap = cv2.VideoCapture(idx, backend)
                    if cap.isOpened():
                        ok, _ = cap.read()
                        if ok:
                            return cap
                    cap.release()
            else:
                cap = cv2.VideoCapture(idx)
                if cap.isOpened():
                    ok, _ = cap.read()
                    if ok:
                        return cap
                cap.release()
            return None

        # Try to find a working camera
        for test_src in [src, 0, 1, 2]:
            self.cap = try_open_camera(test_src)
            if self.cap is not None:
                print(f"[CameraStream] Successfully opened camera index {test_src}")
                break
                
        if self.cap is None:
            print("[CameraStream] WARNING: Could not open any camera feed.")
            # Fallback to a dummy cap so the program doesn't crash
            self.cap = cv2.VideoCapture(src)

        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

        self.running = True
        self.lock = threading.Lock()
        self.latest_frame = None
        self.frame_ready = threading.Event()

        # Capture one frame synchronously to verify camera
        ok, frame = self.cap.read()
        if ok:
            self.latest_frame = frame
            self.frame_ready.set()

        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def _reader(self):
        while self.running:
            ok, frame = self.cap.read()
            if ok:
                with self.lock:
                    self.latest_frame = frame
                self.frame_ready.set()
            else:
                time.sleep(0.005)

    def read(self, timeout=0.04):
        """Returns the newest fresh camera frame without DirectShow queue lag."""
        signaled = self.frame_ready.wait(timeout=timeout)
        self.frame_ready.clear()
        with self.lock:
            if signaled and self.latest_frame is not None:
                frame = self.latest_frame
                self.latest_frame = None  # Consume frame so duplicate is never re-processed
                return True, frame
            return False, None

    def release(self):
        self.running = False
        self.thread.join(timeout=0.4)
        try:
            self.cap.release()
        except Exception:
            pass

class GestureWorker(QThread):
    # The right hand (after mirror flip) drives the air mouse.
    AIR_MOUSE_HAND = "Right"

    # ── Pinch thresholds (ratio = contact distance / palm scale) ──
    #   PINCH_TRIGGER  = 0.14 → requires actual physical touching of thumb and index pads
    #   PINCH_RELEASE  = 0.19 → immediate, crisp release as soon as fingers separate
    #   CLICK_COOLDOWN = 0.10 → highly responsive clicking & double-clicking
    PINCH_TRIGGER  = 0.190
    PINCH_RELEASE  = 0.220
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
        self._pinch_locked = False
        self.single_hand_filter = EMAFilter(alpha=0.75)

        self.is_pinching   = False
        self.last_click_ts = 0.0
        self._last_ts_ms   = 0
        self.cam           = None
        self._last_mouse_palm_pos = None
        self._last_mouse_time     = 0.0

        # Precomputed gamma lookup table for dark background / low-light contrast boost (gamma=0.70)
        # Lifts dark shadows between fingers so MediaPipe clearly distinguishes finger edges against black backdrops
        gamma = 0.70
        self._dark_bg_lut = np.array([np.clip(((i / 255.0) ** gamma) * 255.0, 0, 255) for i in range(256)], dtype=np.uint8)

        # ── Directional thumb gestures → Air Mouse ON / OFF ───────────────────
        # 👍 Thumbs-up  (held 0.65s) → turn Air Mouse ON
        # 👎 Thumbs-down (held 0.65s) → turn Air Mouse OFF
        # Deliberate 0.65s hold prevents accidental toggles when pointing or reaching down.
        self.THUMBS_HOLD_SECS  = 0.65
        self.THUMBS_COOLDOWN   = 1.5    # 1.5s cooldown after switching

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
            num_hands=1,
            min_hand_detection_confidence=0.45,
            min_tracking_confidence=0.45,
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
    def _is_valid_hand(raw: "np.ndarray", score: float) -> bool:
        """Biomechanical filter rejecting non-hand detection noise."""
        # 1. Palm length (wrist 0 -> middle MCP 9)
        palm_len = float(np.linalg.norm(raw[9, :2] - raw[0, :2]))
        if palm_len < 0.03 or palm_len > 0.60:
            return False

        # 2. Palm width (index MCP 5 -> pinky MCP 17)
        palm_width = float(np.linalg.norm(raw[5, :2] - raw[17, :2]))
        if palm_width < 0.015 or palm_width > 0.50:
            return False

        # 3. Palm aspect ratio (allows natural side-profile and angled hand poses)
        ratio = palm_len / (palm_width + 1e-5)
        if ratio < 0.20 or ratio > 10.0:
            return False

        return True

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
        Requires the other 4 fingers to be curled towards palm/wrist (true fist).
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

        # 3. At least 3 of the 4 non-thumb fingers are curled into palm/wrist
        curled_count = 0
        for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
            d_tip = float(np.linalg.norm(sm[tip, :2] - sm[0, :2]))
            d_pip = float(np.linalg.norm(sm[pip, :2] - sm[0, :2]))
            if d_tip < d_pip + 0.035:
                curled_count += 1
        if curled_count < 3:
            return False

        # 4. Anti-pinch check: thumb tip is not touching index/middle tip
        if float(np.linalg.norm(thumb_tip[:2] - sm[8, :2])) < 0.06:
            return False

        return True

    @staticmethod
    def _is_thumbs_down(sm: "np.ndarray") -> bool:
        """
        Thumbs-down: thumb tip is pointing down and is lower than all other fingertips.
        Requires the other 4 fingers to be curled towards palm/wrist (true fist).
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

        # 3. At least 3 of the 4 non-thumb fingers are curled into palm/wrist
        curled_count = 0
        for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
            d_tip = float(np.linalg.norm(sm[tip, :2] - sm[0, :2]))
            d_pip = float(np.linalg.norm(sm[pip, :2] - sm[0, :2]))
            if d_tip < d_pip + 0.035:
                curled_count += 1
        if curled_count < 3:
            return False

        # 4. Anti-pinch check
        if float(np.linalg.norm(thumb_tip[:2] - sm[8, :2])) < 0.06:
            return False

        return True

    # ─────────────────────────── Main loop ────────────────────────────────────
    def run(self):
        # High-speed threaded camera capture: completely eliminates DirectShow queue lag
        self.cam = CameraStream(0, 640, 480, 30)

        while self.running:
            ok, frame = self.cam.read(timeout=0.035)
            if not ok or frame is None:
                time.sleep(0.005)
                continue

            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]

            now_ms = int(time.time() * 1000)
            ts = max(self._last_ts_ms + 1, now_ms)
            self._last_ts_ms = ts

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # Adaptive low-light / dark-background contrast enhancement:
            # If the background is dark (mean frame brightness < 135), apply a precomputed
            # gamma-boost LUT (0.08 ms) to reveal skin silhouettes and eliminate finger-shadow blending.
            mean_lum = float(np.mean(rgb_frame))
            if mean_lum < 135.0:
                mp_rgb = cv2.LUT(rgb_frame, self._dark_bg_lut)
            else:
                mp_rgb = rgb_frame

            mp_img = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=mp_rgb,
            )

            # Synchronous per-frame detection: 100% synchronized with camera feed
            try:
                result = self.detector.detect_for_video(mp_img, ts)
            except Exception as e:
                print(f"[GestureWorker] detect error: {e}")
                continue

            active_labels = set()
            raw_detected = result.hand_landmarks if (result and result.hand_landmarks) else []
            valid_candidates = []
            for idx, hand_lms in enumerate(raw_detected):
                score = float(result.handedness[idx][0].score) if (result.handedness and idx < len(result.handedness)) else 0.5
                raw = np.array([[lm.x, lm.y, lm.z] for lm in hand_lms])
                if self._is_valid_hand(raw, score):
                    try:
                        raw_label = result.handedness[idx][0].category_name
                    except IndexError:
                        raw_label = f"Unknown_{idx}"
                    label = "Left" if raw_label == "Right" else "Right"
                    valid_candidates.append((hand_lms, raw, label, score))

            # Strictly enforce single-hand only: retain only the single best candidate
            if len(valid_candidates) > 1:
                # Prioritize continuity with last tracked palm if available
                if self._last_mouse_palm_pos is not None:
                    lx, ly = self._last_mouse_palm_pos
                    valid_candidates.sort(key=lambda c: (float(c[1][9, 0]) - lx)**2 + (float(c[1][9, 1]) - ly)**2)
                else:
                    valid_candidates.sort(key=lambda c: c[3], reverse=True)
                valid_candidates = valid_candidates[:1]

            num_detected = len(valid_candidates)
            signal_bus.tracking_confidence.emit(
                1.0 if num_detected > 0 else 0.0
            )

            any_thumbs_up = False
            any_thumbs_down = False
            thumbs_up_px = None
            thumbs_down_px = None

            # Single-hand tracking: the single candidate drives mouse if air mouse is enabled
            mouse_candidate_idx = 0 if (self.air_mouse_enabled and valid_candidates) else None

            if num_detected > 0:
                for cand_idx, (hand_lms, raw, label, score) in enumerate(valid_candidates):
                    active_labels.add(label)

                    # ── Landmark smoothing ──────────────────────────────────
                    # Dedicated single-hand filter ensures smooth tracking with zero filter-switching jitters
                    filt = self.single_hand_filter
                    sm = filt.filter(raw)  # shape (21,3), normalized 0-1

                    # ── Draw skeleton ───────────────────────────────────────
                    px = [(int(sm[i,0]*w), int(sm[i,1]*h)) for i in range(21)]
                    for pt in px:
                        cv2.circle(frame, pt, 4, (0,220,0), -1)
                    for a, b in self.connections:
                        cv2.line(frame, px[a], px[b], (255,255,255), 1)

                    # Determine air mouse hand: if enabled, matching candidate drives mouse
                    if self.air_mouse_enabled:
                        is_mouse_hand = (cand_idx == mouse_candidate_idx)
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
                    index_pad = 0.75 * index_px + 0.25 * np.array(px[7], dtype=float)
                    index_distal = 0.50 * index_px + 0.50 * np.array(px[7], dtype=float)
                    mid_px   = np.array(px[12], dtype=float)
                    ring_px  = np.array(px[16], dtype=float)
                    pinky_px = np.array(px[20], dtype=float)

                    # Pure physical fingertip contact distance:
                    # Measures true flesh-to-flesh contact between thumb pad and index pad.
                    # Robust against dark backgrounds where shadow boundaries can slightly retract the tip landmark.
                    d_2d = min(
                        float(np.linalg.norm(thumb_px - index_px)),
                        float(np.linalg.norm(thumb_pad - index_px)),
                        float(np.linalg.norm(thumb_px - index_pad)),
                        float(np.linalg.norm(thumb_pad - index_pad)),
                        float(np.linalg.norm(thumb_pad - index_distal)),
                    )

                    # Rigid skeletal palm scale (invariant to finger curling or reach)
                    palm_w = float(np.linalg.norm(np.array(px[5], dtype=float) - np.array(px[17], dtype=float)))
                    palm_h = float(np.linalg.norm(wrist_px - palm_px))
                    ref_2d = max(palm_w * 1.25, palm_h * 1.1, 35.0)
                    idx_ratio = d_2d / ref_2d

                    # Distances from thumb to other fingertips (2D pixels):
                    d_mid_2d   = min(float(np.linalg.norm(thumb_px - mid_px)), float(np.linalg.norm(thumb_pad - mid_px)))
                    d_ring_2d  = min(float(np.linalg.norm(thumb_px - ring_px)), float(np.linalg.norm(thumb_pad - ring_px)))
                    d_pinky_2d = min(float(np.linalg.norm(thumb_px - pinky_px)), float(np.linalg.norm(thumb_pad - pinky_px)))
                    mid_ratio  = d_mid_2d / ref_2d

                    # 3D normalized distances for camera perspective invariance:
                    d_3d       = float(np.linalg.norm(sm[4] - sm[8]))
                    d_mid_3d   = float(np.linalg.norm(sm[4] - sm[12]))
                    d_ring_3d  = float(np.linalg.norm(sm[4] - sm[16]))
                    d_pinky_3d = float(np.linalg.norm(sm[4] - sm[20]))

                    # ── Multi-Finger Exclusivity with Natural Pinch Physics ──
                    # Strictly ensure ONLY index and thumb are tracked; reject middle/ring/pinky pinches.
                    # Allows a small tolerance margin (+ ref_2d * 0.04) for dark backgrounds where shadow
                    # gradients can slightly shift index fingertip landmark or cause middle knuckle to drift.
                    is_index_closest = (
                        (d_2d <= d_mid_2d + ref_2d * 0.04 or d_3d <= d_mid_3d + 0.03) and
                        (d_2d <= d_ring_2d + ref_2d * 0.05) and
                        (d_2d <= d_pinky_2d + ref_2d * 0.05)
                    )
                    # Reject fist / bunched hand (where middle/ring/pinky are all bunched touching thumb)
                    is_not_fist = (d_pinky_2d > ref_2d * 0.10 or d_ring_2d > ref_2d * 0.10)

                    is_clean_index_pinch = is_index_closest and is_not_fist

                    if is_mouse_hand and self.air_mouse_enabled:
                        cur_t = time.time()
                        self._last_mouse_palm_pos = (float(sm[9, 0]), float(sm[9, 1]))
                        self._last_mouse_time = cur_t

                        # ── Rest-to-Sleep Zone ────────────────────────────────
                        # Only trigger if the entire hand has dropped to the absolute bottom margin of the frame
                        # (wrist > 0.94 AND palm > 0.88), indicating forearm resting flat on desk.
                        if sm[0, 1] > 0.94 and sm[9, 1] > 0.88:
                            if self.is_pinching:
                                self.is_pinching = False
                                signal_bus.pinch_ended.emit()
                            self._pinch_lock_pos = None
                            cv2.putText(
                                frame, "SLEEP (REST ZONE)",
                                (px[0][0] - 65, px[0][1] - 12),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (120, 120, 120), 1,
                            )
                        else:
                            # Projected index MCP tracking anchor:
                            # sm[5] (index MCP knuckle) does NOT curl when making a pinch, eliminating
                            # the 322-pixel deflection caused by fingertip tracking.
                            # Projecting 15% along metacarpal axis provides natural, intuitive cursor reach.
                            hand_dir = sm[5] - sm[0]
                            track_pt = sm[5] + 0.15 * hand_dir

                            # Ergonomic interaction box tailored for effortless reach across all panels:
                            x_min, x_max = 0.18, 0.78
                            y_min, y_max = 0.16, 0.72

                            raw_x = float(np.clip((track_pt[0] - x_min) / (x_max - x_min), 0.0, 1.0))
                            raw_y = float(np.clip((track_pt[1] - y_min) / (y_max - y_min), 0.0, 1.0))

                            # Pinch Lock Damping:
                            # Suppresses micro-tremor when clicking stationary buttons or calipers,
                            # but enables completely fluid dragging once deliberate motion occurs.
                            if self.is_pinching:
                                if self._pinch_lock_pos is None:
                                    self._pinch_lock_pos = (raw_x, raw_y)
                                    self._pinch_locked = True
                                if self._pinch_locked:
                                    ddx = raw_x - self._pinch_lock_pos[0]
                                    ddy = raw_y - self._pinch_lock_pos[1]
                                    if (ddx * ddx + ddy * ddy) > (0.018 * 0.018):
                                        # Deliberate drag motion: unlock permanently for this pinch
                                        self._pinch_locked = False
                                    else:
                                        # Stationary click: damp micro-tremor
                                        raw_x = self._pinch_lock_pos[0]
                                        raw_y = self._pinch_lock_pos[1]
                            else:
                                self._pinch_lock_pos = None
                                self._pinch_locked = False

                            # 1€ Filter: zero lag, fluid tracking with high-precision monotonic clock
                            cur_t = time.perf_counter()
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
                            elif (idx_ratio > self.PINCH_RELEASE) or (not is_clean_index_pinch and idx_ratio > self.PINCH_TRIGGER):
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
                    # Single-Hand Routing logic:
                    #   Air mouse ON  → Single hand controls OS cursor & pinch-to-click
                    #   Air mouse OFF → Single hand directly controls 3-D viewer (rotation/zoom/melt)
                    # ══════════════════════════════════════════════════════════
                    drives_3d = not self.air_mouse_enabled

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
            mouse_hand_present = (mouse_candidate_idx is not None)
            if not mouse_hand_present:
                if self.is_pinching:
                    self.is_pinching = False
                    signal_bus.pinch_ended.emit()
                self._pinch_lock_pos = None
                self.mouse_filter.reset()
                self.single_hand_filter.reset()

            # ── Emit lightweight annotated camera frame for HUD ─────────────
            # Downsample to 240x180 so Qt UI thread doesn't spend CPU scaling 640x480 images
            preview = cv2.resize(frame, (240, 180), interpolation=cv2.INTER_LINEAR)
            rgb = cv2.cvtColor(preview, cv2.COLOR_BGR2RGB)
            h2, w2, ch = rgb.shape
            qimg_copy = QImage(rgb.data, w2, h2, ch * w2, QImage.Format.Format_RGB888).copy()
            signal_bus.camera_frame.emit(qimg_copy)

            # Prevent CPU thread starvation
            time.sleep(0.001)

        if self.cam is not None:
            self.cam.release()
        try:
            self.detector.close()
        except Exception:
            pass

    def stop(self):
        self.running = False
        if self.cam is not None:
            self.cam.release()
        self.wait(1000)