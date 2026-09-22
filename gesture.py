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

        # Contrast-Limited Adaptive Histogram Equalization (CLAHE) for robust edge detection
        # against dark backgrounds, dark clothing, and exhibition lighting without skin blowout:
        self.clahe = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))

        # Exhibition multi-person presenter tracking & trajectory hysteresis state:
        self._presenter_palm_pos = None      # (x, y) normalized coordinates of locked operator
        self._presenter_label = None         # "Right" or "Left"
        self._presenter_last_seen = 0.0      # monotonic timestamp
        self._presenter_lock_radius = 0.22   # normalized radius for movement continuity
        self._presenter_lock_timeout = 0.50  # seconds before lock expires when hand leaves

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
        # num_hands=4 enables detecting multiple hands simultaneously so bystanders
        # in the audience never steal the single detection slot from the presenter.
        base_opts = python.BaseOptions(model_asset_path="hand_landmarker.task")
        opts = vision.HandLandmarkerOptions(
            base_options=base_opts,
            num_hands=4,
            min_hand_detection_confidence=0.40,
            min_tracking_confidence=0.40,
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

    def _rank_candidates(self, candidates: list, cur_time: float) -> list:
        """
        Ranks detected hands to select the primary foreground presenter.
        Rejects distant background bystanders and peripheral audience members.
        Enforces sticky temporal locking so spectators waving in the background
        cannot hijack the cursor or trigger false clicks.
        """
        if not candidates:
            # Check if presenter lock should expire
            if (cur_time - self._presenter_last_seen) > self._presenter_lock_timeout:
                self._presenter_palm_pos = None
                self._presenter_label = None
            return []

        lock_active = (
            self._presenter_palm_pos is not None and
            (cur_time - self._presenter_last_seen) < self._presenter_lock_timeout
        )

        scored = []
        for cand in candidates:
            hand_lms, raw, label, score = cand
            # 1. Palm size (depth/proximity metric)
            # Foreground operator has large hand scale; distant spectator has tiny hand.
            palm_len = float(np.linalg.norm(raw[9, :2] - raw[0, :2]))
            palm_width = float(np.linalg.norm(raw[5, :2] - raw[17, :2]))
            hand_area = palm_len * palm_width

            # Reject tiny hands in the background (< 0.055 normalized palm length)
            if palm_len < 0.055:
                continue

            # Scale proximity score (saturates smoothly for close hands)
            size_score = min(hand_area / 0.022, 3.5)

            # 2. Central interaction zone weight
            # Presenter stands directly in front; bystanders stand at left/right margins.
            palm_x, palm_y = float(raw[9, 0]), float(raw[9, 1])
            dist_center_x = abs(palm_x - 0.50)
            center_weight = max(0.20, 1.0 - 1.4 * dist_center_x)

            # 3. Upward arm orientation bonus
            # Presenter raises hand from bottom of frame (wrist_y > knuckle_y).
            is_upward = (raw[0, 1] - raw[9, 1]) > -0.02
            orientation_weight = 1.2 if is_upward else 0.75

            # 4. Handedness priority (if Air Mouse is on, Right hand preferred)
            hand_pref = 1.35 if (self.air_mouse_enabled and label == self.AIR_MOUSE_HAND) else 1.0

            cand_score = score * size_score * center_weight * orientation_weight * hand_pref

            # 5. Temporal Continuity Bonus (Hysteresis / Sticky Tracking)
            continuity_bonus = 0.0
            dist_to_lock = 999.0
            if lock_active:
                lx, ly = self._presenter_palm_pos
                dist_to_lock = float(np.hypot(palm_x - lx, palm_y - ly))
                if dist_to_lock < self._presenter_lock_radius:
                    # Candidate is continuing the locked presenter's trajectory
                    continuity_bonus += 5.0 * (1.0 - dist_to_lock / self._presenter_lock_radius)
                    if label == self._presenter_label:
                        continuity_bonus += 2.0

            total_score = cand_score + continuity_bonus
            scored.append((total_score, cand))

        if not scored:
            if (cur_time - self._presenter_last_seen) > self._presenter_lock_timeout:
                self._presenter_palm_pos = None
                self._presenter_label = None
            return []

        # Sort descending by total score
        scored.sort(key=lambda s: s[0], reverse=True)

        # Primary candidate is the highest scorer
        best_cand = scored[0][1]

        # Update or establish presenter lock
        raw_best = best_cand[1]
        self._presenter_palm_pos = (float(raw_best[9, 0]), float(raw_best[9, 1]))
        self._presenter_label = best_cand[2]
        self._presenter_last_seen = cur_time

        # Return all valid scored candidates (primary presenter first, followed by others)
        return [s[1] for s in scored]

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
            frame = np.ascontiguousarray(frame)
            h, w = frame.shape[:2]

            now_ms = int(time.time() * 1000)
            ts = max(self._last_ts_ms + 1, now_ms)
            self._last_ts_ms = ts

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # Adaptive low-light / dark-background contrast enhancement:
            # Uses YUV CLAHE (Contrast Limited Adaptive Histogram Equalization) on the luminance (Y) channel.
            # Sharpens skin contours against dark clothing and varying exhibition lighting
            # without blowing out skin tones or creating digital glare.
            yuv = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2YUV)
            yuv[:, :, 0] = self.clahe.apply(yuv[:, :, 0])
            mp_rgb = cv2.cvtColor(yuv, cv2.COLOR_YUV2RGB)

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

            # Exhibition multi-person filter: rank candidates to lock onto primary foreground presenter
            cur_sec = time.time()
            ranked_candidates = self._rank_candidates(valid_candidates, cur_sec)

            num_detected = len(ranked_candidates)
            signal_bus.tracking_confidence.emit(
                1.0 if num_detected > 0 else 0.0
            )

            any_thumbs_up = False
            any_thumbs_down = False
            thumbs_up_px = None
            thumbs_down_px = None

            # Primary presenter hand is ranked_candidates[0]
            mouse_candidate_idx = 0 if (self.air_mouse_enabled and ranked_candidates) else None

            if num_detected > 0:
                # Render secondary/bystander hands in faint gray so spectators and judges can see the system
                # actively tracking and safely ignoring them
                for b_idx in range(1, len(ranked_candidates)):
                    b_raw = ranked_candidates[b_idx][1]
                    b_px = [(int(b_raw[i, 0] * w), int(b_raw[i, 1] * h)) for i in range(21)]
                    for pt in b_px:
                        cv2.circle(frame, pt, 2, (100, 100, 100), -1)
                    for a, b in self.connections:
                        cv2.line(frame, b_px[a], b_px[b], (65, 65, 65), 1)

                # Process primary presenter hand (ranked candidate 0)
                hand_lms, raw, label, score = ranked_candidates[0]
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

                # Determine air mouse hand: primary presenter drives mouse
                is_mouse_hand = bool(self.air_mouse_enabled and mouse_candidate_idx is not None)

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
                ring_ratio = d_ring_2d / ref_2d

                # 3D normalized distances for camera perspective invariance:
                d_3d       = float(np.linalg.norm(sm[4] - sm[8]))
                d_mid_3d   = float(np.linalg.norm(sm[4] - sm[12]))
                d_ring_3d  = float(np.linalg.norm(sm[4] - sm[16]))
                d_pinky_3d = float(np.linalg.norm(sm[4] - sm[20]))

                # ── Multi-Finger Exclusivity with Natural Pinch Physics ──
                # Strictly ensure ONLY index and thumb are tracked; reject middle/ring/pinky pinches.
                # Allows a calibrated tolerance margin (+ ref_2d * 0.06) for dark clothing / shadows where
                # contrast boundaries can slightly shift index fingertip landmark or cause middle knuckle to drift.
                is_index_closest = (
                    (d_2d <= d_mid_2d + ref_2d * 0.06 or d_3d <= d_mid_3d + 0.04) and
                    (d_2d <= d_ring_2d + ref_2d * 0.07) and
                    (d_2d <= d_pinky_2d + ref_2d * 0.07)
                )
                # Reject fist / bunched hand (where middle/ring/pinky are all bunched touching thumb)
                is_not_fist = (d_pinky_2d > ref_2d * 0.09 or d_ring_2d > ref_2d * 0.09)

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

                    elif (d_ring_2d < d_2d) and (ring_ratio < 0.18):    # thumb+ring pinch → 2D pan
                        if label in self.prev_palm:
                            px0, py0 = self.prev_palm[label]
                            dx = float(np.clip(palm_x - px0, -0.06, 0.06))
                            dy = float(np.clip(palm_y - py0, -0.06, 0.06))
                            if abs(dx) > 0.003 or abs(dy) > 0.003:
                                signal_bus.pan_command.emit(dx, dy)
                                cv2.putText(frame, "PAN / DRAG",
                                            (palm_px_pos[0]-40, palm_px_pos[1]-20),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                            (0,255,100), 2)
                        self.prev_palm[label] = (palm_x, palm_y)

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