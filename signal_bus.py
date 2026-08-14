from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

class SignalBus(QObject):
    # Air Mouse & Gesture Signals
    cursor_moved = pyqtSignal(float, float)        # x, y (normalized 0.0-1.0)
    pinch_started = pyqtSignal()
    pinch_ended = pyqtSignal()

    # Air Mouse on/off toggle (emitted by UI nav button)
    air_mouse_toggle = pyqtSignal(bool)            # True = enable, False = disable

    # 3D Manipulation Signals
    hand_rotation = pyqtSignal(float, float, float) # pitch (delta_x), yaw (delta_y), roll
    zoom_command = pyqtSignal(int)                 # +1 (Zoom In) or -1 (Zoom Out)
    tissue_melt = pyqtSignal(float)                # melt_factor (0.0 - 1.0)

    # System & Camera Overlay Signals
    tracking_confidence = pyqtSignal(float)        # 0.0-1.0
    camera_frame = pyqtSignal(QImage)              # Live camera HUD frame

# Shared singleton instance
signal_bus = SignalBus()