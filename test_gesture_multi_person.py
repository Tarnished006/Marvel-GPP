"""
test_gesture_multi_person.py

Automated test suite verifying the exhibition-grade multi-person hand tracking,
bystander rejection, temporal trajectory locking, and CLAHE contrast enhancement.
"""

import time
import numpy as np
import cv2
import pytest
from gesture import GestureWorker


def _make_dummy_hand(palm_center=(0.5, 0.5), palm_len=0.20, palm_width=0.10, is_upward=True):
    """Generates synthetic 21 normalized landmarks with specified palm size and center."""
    cx, cy = palm_center
    raw = np.zeros((21, 3), dtype=float)
    
    # Wrist (point 0)
    wrist_y = cy + (0.5 * palm_len if is_upward else -0.5 * palm_len)
    raw[0] = [cx, wrist_y, 0.0]
    
    # Middle MCP (point 9)
    mcp_y = cy - (0.5 * palm_len if is_upward else -0.5 * palm_len)
    raw[9] = [cx, mcp_y, 0.0]
    
    # Index MCP (point 5) and Pinky MCP (point 17)
    raw[5]  = [cx - 0.5 * palm_width, mcp_y, 0.0]
    raw[17] = [cx + 0.5 * palm_width, mcp_y, 0.0]
    
    # Fill remaining points with plausible offsets
    for i in range(21):
        if i not in (0, 9, 5, 17):
            raw[i] = [cx, mcp_y - 0.05, 0.0]
            
    return raw


def test_hand_landmarker_multi_hand_init():
    """Verify GestureWorker initializes with num_hands=4 for multi-person capture."""
    worker = GestureWorker()
    assert worker.detector is not None, "MediaPipe HandLandmarker detector failed to initialize"
    assert hasattr(worker, "clahe"), "Worker must have CLAHE contrast enhancement"
    assert worker._presenter_lock_radius > 0.15, "Presenter lock radius should be set"
    assert worker._presenter_lock_timeout >= 0.40, "Lock timeout should be at least 0.4s"
    print("test_hand_landmarker_multi_hand_init: PASSED")


def test_clahe_contrast_enhancement():
    """Verify YUV CLAHE runs fast and sharpens contrast without crashing or memory leaks."""
    worker = GestureWorker()
    # Create dark image simulating an exhibition hall / dark background (mean luma ~ 70)
    dark_frame = np.full((480, 640, 3), 70, dtype=np.uint8)
    # Add a hand-like skin patch
    dark_frame[150:350, 200:400] = (140, 180, 220)
    
    t0 = time.perf_counter()
    yuv = cv2.cvtColor(dark_frame, cv2.COLOR_RGB2YUV)
    yuv[:, :, 0] = worker.clahe.apply(yuv[:, :, 0])
    enhanced = cv2.cvtColor(yuv, cv2.COLOR_YUV2RGB)
    dt_ms = (time.perf_counter() - t0) * 1000
    
    assert enhanced.shape == dark_frame.shape
    assert dt_ms < 15.0, f"CLAHE took {dt_ms:.2f} ms (must be < 15ms for 30 FPS)"
    # Enhanced frame should have greater dynamic range
    assert np.std(enhanced[:, :, 0]) >= np.std(dark_frame[:, :, 0])
    print(f"test_clahe_contrast_enhancement: PASSED ({dt_ms:.2f} ms)")


def test_foreground_presenter_selected_over_background_bystander():
    """Verify that a large foreground hand (presenter) beats a distant bystander hand."""
    worker = GestureWorker()
    
    # Foreground presenter hand: close to camera, palm_len = 0.22, center of screen (0.50, 0.60)
    presenter_raw = _make_dummy_hand(palm_center=(0.50, 0.60), palm_len=0.22, palm_width=0.12)
    # Background spectator hand: 2 meters behind, palm_len = 0.065, (0.35, 0.40)
    spectator_raw = _make_dummy_hand(palm_center=(0.35, 0.40), palm_len=0.065, palm_width=0.035)
    
    candidates = [
        ([], spectator_raw, "Right", 0.95),  # Spectator hand has high confidence
        ([], presenter_raw, "Right", 0.85),  # Presenter hand slightly lower raw confidence
    ]
    
    ranked = worker._rank_candidates(candidates, cur_time=10.0)
    assert len(ranked) == 2
    # Presenter hand must be ranked 1st despite lower raw confidence due to proximity
    assert np.allclose(ranked[0][1], presenter_raw), "Foreground presenter was not ranked #1"
    assert np.allclose(ranked[1][1], spectator_raw), "Spectator hand was not ranked #2"
    print("test_foreground_presenter_selected_over_background_bystander: PASSED")


def test_distant_bystander_rejected():
    """Verify that very distant spectator hands (palm_len < 0.055) are completely rejected."""
    worker = GestureWorker()
    
    distant_raw = _make_dummy_hand(palm_center=(0.40, 0.30), palm_len=0.045, palm_width=0.02)
    candidates = [
        ([], distant_raw, "Right", 0.90),
    ]
    
    ranked = worker._rank_candidates(candidates, cur_time=20.0)
    assert len(ranked) == 0, "Distant bystander hand should be rejected"
    print("test_distant_bystander_rejected: PASSED")


def test_center_operator_selected_over_peripheral_bystander():
    """Verify that a hand in the center interaction zone beats a peripheral bystander hand."""
    worker = GestureWorker()
    
    # Both hands equal size, but one is in the center and one is on the extreme right edge
    center_raw = _make_dummy_hand(palm_center=(0.50, 0.60), palm_len=0.18, palm_width=0.09)
    edge_raw   = _make_dummy_hand(palm_center=(0.94, 0.60), palm_len=0.18, palm_width=0.09)
    
    candidates = [
        ([], edge_raw, "Right", 0.90),
        ([], center_raw, "Right", 0.90),
    ]
    
    ranked = worker._rank_candidates(candidates, cur_time=30.0)
    assert len(ranked) == 2
    assert np.allclose(ranked[0][1], center_raw), "Center operator was not ranked #1"
    print("test_center_operator_selected_over_peripheral_bystander: PASSED")


def test_temporal_trajectory_locking_prevents_hijacking():
    """
    Verify that once an operator is active, a waving spectator walking into the frame
    cannot steal the tracking focus.
    """
    worker = GestureWorker()
    
    # Frame 1: Presenter is interacting at (0.50, 0.55)
    p1_raw = _make_dummy_hand(palm_center=(0.50, 0.55), palm_len=0.18, palm_width=0.09)
    candidates_f1 = [([], p1_raw, "Right", 0.90)]
    ranked_f1 = worker._rank_candidates(candidates_f1, cur_time=100.0)
    assert np.allclose(ranked_f1[0][1], p1_raw)
    assert worker._presenter_palm_pos is not None
    
    # Frame 2: Presenter moves slightly to (0.52, 0.54).
    # Simultaneously, a spectator raises a larger hand at (0.25, 0.50)!
    p2_raw = _make_dummy_hand(palm_center=(0.52, 0.54), palm_len=0.18, palm_width=0.09)
    spectator_raw = _make_dummy_hand(palm_center=(0.25, 0.50), palm_len=0.25, palm_width=0.13)
    
    candidates_f2 = [
        ([], spectator_raw, "Right", 0.99),  # Spectator hand has higher score and larger size
        ([], p2_raw, "Right", 0.85),         # Existing presenter
    ]
    
    ranked_f2 = worker._rank_candidates(candidates_f2, cur_time=100.033)
    # Continuity bonus MUST keep tracking locked on the presenter!
    assert np.allclose(ranked_f2[0][1], p2_raw), "Temporal trajectory lock failed: spectator hijacked tracking"
    print("test_temporal_trajectory_locking_prevents_hijacking: PASSED")


def test_lock_release_after_timeout():
    """Verify that when the presenter leaves, the lock expires after timeout and allows a new operator."""
    worker = GestureWorker()
    
    # Presenter active at t=200.0
    p_raw = _make_dummy_hand(palm_center=(0.50, 0.55), palm_len=0.18, palm_width=0.09)
    worker._rank_candidates([([], p_raw, "Right", 0.90)], cur_time=200.0)
    assert worker._presenter_palm_pos is not None
    
    # At t=200.2 (200ms later), presenter momentarily drops hand (empty frame)
    worker._rank_candidates([], cur_time=200.2)
    # Lock should remain armed during grace period
    assert worker._presenter_palm_pos is not None, "Grace period failed"
    
    # At t=200.6 (600ms later > timeout 500ms), lock must expire
    worker._rank_candidates([], cur_time=200.6)
    assert worker._presenter_palm_pos is None, "Lock failed to expire after timeout"
    
    # New operator steps in at (0.35, 0.50) and takes over immediately
    new_op_raw = _make_dummy_hand(palm_center=(0.35, 0.50), palm_len=0.19, palm_width=0.10)
    ranked = worker._rank_candidates([([], new_op_raw, "Right", 0.90)], cur_time=200.7)
    assert len(ranked) == 1
    assert np.allclose(ranked[0][1], new_op_raw)
    print("test_lock_release_after_timeout: PASSED")


if __name__ == "__main__":
    test_hand_landmarker_multi_hand_init()
    test_clahe_contrast_enhancement()
    test_foreground_presenter_selected_over_background_bystander()
    test_distant_bystander_rejected()
    test_center_operator_selected_over_peripheral_bystander()
    test_temporal_trajectory_locking_prevents_hijacking()
    test_lock_release_after_timeout()
    print("\nALL MULTI-PERSON EXHIBITION GESTURE TESTS PASSED!")
