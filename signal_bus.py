# signal_bus.py
from PyQt6.QtCore import QObject, pyqtSignal

class SignalBus(QObject):
    cursor_moved = pyqtSignal(float, float)       # x, y (normalized 0.0-1.0)
    pinch_started = pyqtSignal()
    pinch_ended = pyqtSignal()
    palm_swipe = pyqtSignal(str)                   # "left" or "right"
    hand_rotation = pyqtSignal(float, float, float)  # pitch, yaw, roll
    voice_command = pyqtSignal(str)                # e.g. "start_note", "reset_view"
    pedal_pressed = pyqtSignal()
    tracking_confidence = pyqtSignal(float)         # 0.0-1.0

# One single shared instance — everyone imports and uses THIS,
# not their own SignalBus() copy.
signal_bus = SignalBus()