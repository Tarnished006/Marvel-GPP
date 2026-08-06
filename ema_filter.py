import numpy as np

class EMAFilter:
    """
    A lightweight, vectorized Exponential Moving Average (EMA) filter.
    Perfect for smoothing 3D coordinate arrays (like MediaPipe hand landmarks).
    """
    def __init__(self, alpha):
        """
        Initializes the EMA Filter.
        
        Args:
            alpha (float): The smoothing factor (0.0 to 1.0).
                           Lower values = more smoothing / less jitter / more lag.
                           Higher values = less smoothing / more jitter / less lag.
        """
        # Ensure alpha is strictly bounded
        self.alpha = max(0.0, min(1.0, alpha))
        
        # This will hold the previous frame's filtered values
        self.previous_state = None

    def filter(self, current_data):
        """
        Applies the EMA formula to the current frame's data.
        
        Args:
            current_data (numpy.ndarray): The raw, noisy data for the current frame.
                                          Expected to be a numpy array of shape (N, 3) 
                                          for N joints with x, y, z coordinates.
                                          
        Returns:
            numpy.ndarray: The smoothed data.
        """
        # Convert input to a numpy array (in case a standard python list was passed)
        current_data = np.asarray(current_data, dtype=float)

        # First frame initialization
        if self.previous_state is None:
            self.previous_state = current_data
            return current_data

        # Apply the EMA formula: y[i] = alpha * x[i] + (1 - alpha) * y[i-1]
        smoothed_data = (self.alpha * current_data) + ((1.0 - self.alpha) * self.previous_state)
        
        # Update the state for the next frame
        self.previous_state = smoothed_data
        
        return smoothed_data

    def reset(self):
        """
        Resets the internal state. Call this when a hand leaves the screen 
        and re-enters to prevent dragging artifacts.
        """
        self.previous_state = None

