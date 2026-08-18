import math
import numpy as np

class EMAFilter:
    """
    A lightweight, vectorized Exponential Moving Average (EMA) filter.
    Used for smoothing 3D coordinate arrays (like MediaPipe hand landmarks).
    """
    def __init__(self, alpha: float):
        self.alpha = max(0.0, min(1.0, float(alpha)))
        self.previous_state = None

    def filter(self, current_data):
        current_data = np.asarray(current_data, dtype=float)
        if self.previous_state is None:
            self.previous_state = current_data
            return current_data

        smoothed_data = (self.alpha * current_data) + ((1.0 - self.alpha) * self.previous_state)
        self.previous_state = smoothed_data
        return smoothed_data

    def reset(self):
        self.previous_state = None


class LowPassFilter:
    """Helper low-pass filter used by the 1€ Filter."""
    def __init__(self):
        self._y = None
        self._s = None

    def filter(self, value, alpha: float):
        if self._y is None:
            s = value
        else:
            s = alpha * value + (1.0 - alpha) * self._s
        self._y = value
        self._s = s
        return s

    def last_value(self):
        return self._y

    def reset(self):
        self._y = None
        self._s = None


class OneEuroFilter:
    """
    1€ Filter (Casiez et al., CHI 2012)
    The gold standard filter for pointer and gesture tracking in HCI.
    
    Eliminates high-frequency hand tremors/shaking when holding still or hovering,
    while scaling up cutoff frequency dynamically during movement for a natural mouse feel.
    """
    def __init__(
        self,
        freq: float = 30.0,
        mincutoff: float = 1.10,
        beta: float = 0.12,
        dcutoff: float = 1.0,
    ):
        self.freq = freq
        self.mincutoff = mincutoff
        self.beta = beta
        self.dcutoff = dcutoff
        self.x_filter = LowPassFilter()
        self.dx_filter = LowPassFilter()
        self.last_time = None

    def _calc_alpha(self, cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def filter(self, x, timestamp: float = None):
        x = np.asarray(x, dtype=float)

        if self.last_time is None:
            self.last_time = timestamp if timestamp is not None else 0.0
            self.x_filter.filter(x, 1.0)
            self.dx_filter.filter(np.zeros_like(x), 1.0)
            return x

        if timestamp is not None and timestamp > self.last_time:
            dt = max(1e-4, timestamp - self.last_time)
            self.last_time = timestamp
        else:
            dt = 1.0 / self.freq

        # Estimate derivative (speed of movement)
        prev_x = self.x_filter.last_value()
        dx = (x - prev_x) / dt if prev_x is not None else np.zeros_like(x)
        edx = self.dx_filter.filter(dx, self._calc_alpha(self.dcutoff, dt))

        # Dynamic cutoff frequency based on movement velocity
        speed = float(np.linalg.norm(edx))
        cutoff = self.mincutoff + self.beta * speed

        # Filter the position signal
        alpha = self._calc_alpha(cutoff, dt)
        return self.x_filter.filter(x, alpha)

    def reset(self):
        self.x_filter.reset()
        self.dx_filter.reset()
        self.last_time = None
