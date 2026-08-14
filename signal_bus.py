from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

class SignalBus(QObject):
    # Air Mouse & Gesture Signals
    cursor_moved = pyqtSignal(float, float)         # x, y (normalized 0.0-1.0)
    pinch_started = pyqtSignal()
    pinch_ended = pyqtSignal()
    air_mouse_toggle = pyqtSignal(bool)              # NEW: turns air-mouse control on/off

    # 3D Manipulation Signals
    hand_rotation = pyqtSignal(float, float, float)  # delta_x, delta_y, delta_z (WIDENED from 2 to 3 floats
                                                       # to match gesture.py's emit(dx, dy, 0.0) and
                                                       # Viewer3D.rotate_camera(delta_x, delta_y, delta_z=0.0))
    zoom_command = pyqtSignal(int)                    # +1 (Zoom In) or -1 (Zoom Out)
    tissue_melt = pyqtSignal(float)                   # melt_factor (0.0 - 1.0)

    # System & Camera Overlay Signals
    tracking_confidence = pyqtSignal(float)           # 0.0-1.0
    camera_frame = pyqtSignal(QImage)                 # Live camera HUD frame

# Shared singleton instance
signal_bus = SignalBus()