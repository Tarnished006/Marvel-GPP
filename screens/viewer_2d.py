import os
import numpy as np
import pydicom
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QSlider, QLabel, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                             QSplitter)
from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor, QFont
from signal_bus import signal_bus

class ClinicalGraphicsView(QGraphicsView):
    """A custom QGraphicsView that handles smooth panning and zooming, plus medical HUD text."""
    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)
        
        self.setBackgroundBrush(QColor("#000000"))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # HUD Data
        self.hud_data = {
            "top_left": "Patient: Unknown",
            "top_right": "Modality: -",
            "bottom_left": "W: 400 L: 40",
            "bottom_right": "Slice: 0/0"
        }

    def wheelEvent(self, event):
        """Zoom in and out with the mouse wheel (or equivalent gesture)."""
        zoom_in_factor = 1.15
        zoom_out_factor = 1.0 / zoom_in_factor
        if event.angleDelta().y() > 0:
            zoom_factor = zoom_in_factor
        else:
            zoom_factor = zoom_out_factor
        self.scale(zoom_factor, zoom_factor)

    def drawForeground(self, painter, rect):
        """Draw the medical HUD overlays directly over the view."""
        super().drawForeground(painter, rect)
        
        # Reset the painter's transform so it draws in absolute window pixels (ignoring zoom/pan)
        painter.resetTransform()
        
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#7cfc00")) # Neon green medical text
        painter.setFont(QFont("Consolas", 12, QFont.Weight.Bold))

        # Get the absolute rectangle of the viewport
        vp_rect = self.viewport().rect()
        margin = 15
        
        # Top Left
        painter.drawText(vp_rect.adjusted(margin, margin, 0, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, self.hud_data["top_left"])
        # Top Right
        painter.drawText(vp_rect.adjusted(0, margin, -margin, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, self.hud_data["top_right"])
        # Bottom Left
        painter.drawText(vp_rect.adjusted(margin, 0, 0, -margin), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, self.hud_data["bottom_left"])
        # Bottom Right
        painter.drawText(vp_rect.adjusted(0, 0, -margin, -margin), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight, self.hud_data["bottom_right"])


class Viewer2D(QWidget):
    """Dedicated 2D Clinical Workspace for X-Rays and low-slice-count MRI/CTs."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: #090909; color: #d0d0d0;")
        
        # State
        self.dcm_files = []
        self.pixel_arrays = []
        self.current_idx = 0
        
        self.window_width = 400
        self.window_center = 40
        self.is_inverted = False
        self.rotation_angle = 0
        
        self.cine_timer = QTimer(self)
        self.cine_timer.timeout.connect(self._next_slice)
        
        # Connect gesture zoom & pan
        signal_bus.zoom_command.connect(self._handle_gesture_zoom)
        signal_bus.pan_command.connect(self._handle_gesture_pan)
        
        self._build_ui()

    def _handle_gesture_pan(self, dx: float, dy: float):
        """Called by the gesture thread when a thumb+ring gesture is detected in Viewer Mode."""
        if not self.isVisible():
            return
            
        # dx, dy are typically -0.06 to 0.06. Scale up for scrolling.
        sensitivity = 3000
        h_bar = self.view.horizontalScrollBar()
        v_bar = self.view.verticalScrollBar()
        
        h_bar.setValue(h_bar.value() - int(dx * sensitivity))
        v_bar.setValue(v_bar.value() - int(dy * sensitivity))

    def _handle_gesture_zoom(self, direction: int):
        """Called by the gesture thread when a zoom gesture is detected."""
        # Ensure we only zoom if this widget is visible
        if not self.isVisible():
            return
            
        zoom_in_factor = 1.03
        zoom_out_factor = 1.0 / zoom_in_factor
        
        if direction > 0:
            zoom_factor = zoom_in_factor
        else:
            zoom_factor = zoom_out_factor
            
        self.view.scale(zoom_factor, zoom_factor)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # ─── TITLE BAR ────────────────────────────────────────────────────────
        title_lbl = QLabel("2D CLINICAL WORKSPACE")
        title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #7cfc00; letter-spacing: 2px;")
        main_layout.addWidget(title_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        # ─── TOOLBAR ──────────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        
        btn_style = (
            "QPushButton { background: #181818; border: 1px solid #333; border-radius: 4px; padding: 6px 12px; font-weight: bold;}"
            "QPushButton:hover { background: #252525; border: 1px solid #7cfc00; color: #7cfc00; }"
        )
        
        self.btn_invert = QPushButton("Invert")
        self.btn_invert.setStyleSheet(btn_style)
        self.btn_invert.clicked.connect(self.toggle_invert)
        
        self.btn_rotate = QPushButton("Rotate 90°")
        self.btn_rotate.setStyleSheet(btn_style)
        self.btn_rotate.clicked.connect(self.rotate_image)
        
        self.btn_reset = QPushButton("Reset View")
        self.btn_reset.setStyleSheet(btn_style)
        self.btn_reset.clicked.connect(self.reset_view)
        
        self.btn_cine = QPushButton("Cine Play")
        self.btn_cine.setStyleSheet(btn_style)
        self.btn_cine.setCheckable(True)
        self.btn_cine.clicked.connect(self.toggle_cine)
        
        toolbar.addWidget(self.btn_invert)
        toolbar.addWidget(self.btn_rotate)
        toolbar.addWidget(self.btn_reset)
        toolbar.addWidget(self.btn_cine)
        
        # Contrast Slider
        toolbar.addSpacing(20)
        toolbar.addWidget(QLabel("Contrast (W):"))
        self.slider_w = QSlider(Qt.Orientation.Horizontal)
        self.slider_w.setRange(1, 2000)
        self.slider_w.setValue(self.window_width)
        self.slider_w.setFixedWidth(150)
        self.slider_w.valueChanged.connect(self._on_slider_w)
        toolbar.addWidget(self.slider_w)
        
        # Brightness Slider
        toolbar.addSpacing(10)
        toolbar.addWidget(QLabel("Brightness (L):"))
        self.slider_l = QSlider(Qt.Orientation.Horizontal)
        self.slider_l.setRange(-1000, 1000)
        self.slider_l.setValue(self.window_center)
        self.slider_l.setFixedWidth(150)
        self.slider_l.valueChanged.connect(self._on_slider_l)
        toolbar.addWidget(self.slider_l)
        
        toolbar.addStretch()
        main_layout.addLayout(toolbar)

        # ─── MAIN VIEWPORT ────────────────────────────────────────────────────
        self.view = ClinicalGraphicsView()
        
        # Slice Scrollbar
        self.slice_scrollbar = QSlider(Qt.Orientation.Vertical)
        self.slice_scrollbar.setInvertedAppearance(True)
        self.slice_scrollbar.setStyleSheet("""
            QSlider::groove:vertical { background: #222; width: 8px; border-radius: 4px; }
            QSlider::handle:vertical { background: #7cfc00; height: 30px; margin: 0 -4px; border-radius: 4px; }
        """)
        self.slice_scrollbar.valueChanged.connect(self._on_slice_scroll)
        
        view_layout = QHBoxLayout()
        view_layout.addWidget(self.view, stretch=1)
        view_layout.addWidget(self.slice_scrollbar)
        main_layout.addLayout(view_layout, stretch=1)

    def load_scan(self, folder_path: str, patient_name: str = "Unknown"):
        self.dcm_files = []
        self.pixel_arrays = []
        
        if not os.path.exists(folder_path):
            return

        files = [f for f in os.listdir(folder_path) if f.lower().endswith(".dcm")]
        if not files:
            return
            
        # Parse and sort slices
        slices = []
        for f in files:
            try:
                ds = pydicom.dcmread(os.path.join(folder_path, f))
                slices.append(ds)
            except:
                pass
                
        # Sort by InstanceNumber or SliceLocation
        slices.sort(key=lambda x: float(getattr(x, "InstanceNumber", 0)))
        
        self.dcm_files = slices
        
        # Pre-extract arrays for speed
        for ds in self.dcm_files:
            arr = ds.pixel_array
            slope = float(getattr(ds, "RescaleSlope", 1.0))
            intercept = float(getattr(ds, "RescaleIntercept", 0.0))
            self.pixel_arrays.append(arr * slope + intercept)
            
        # Try to read default Window/Level from the first slice
        first = self.dcm_files[0]
        try:
            ww = first.WindowWidth
            wc = first.WindowCenter
            self.window_width = int(ww[0] if isinstance(ww, pydicom.multival.MultiValue) else ww)
            self.window_center = int(wc[0] if isinstance(wc, pydicom.multival.MultiValue) else wc)
            self.slider_w.setValue(self.window_width)
            self.slider_l.setValue(self.window_center)
        except:
            pass # Use defaults
            
        # Update HUD
        self.view.hud_data["top_left"] = f"Patient: {patient_name}"
        self.view.hud_data["top_right"] = f"Modality: {getattr(first, 'Modality', 'CT')}"
        
        # Setup Scrollbar
        self.slice_scrollbar.setRange(0, len(self.pixel_arrays) - 1)
        self.slice_scrollbar.setValue(0)
        
        self.current_idx = 0
        self.update_image()
        self.view.fitInView(self.view.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def update_image(self):
        if not self.pixel_arrays:
            return
            
        arr = self.pixel_arrays[self.current_idx]
        
        # Apply Window/Level (Contrast/Brightness)
        img_min = self.window_center - self.window_width / 2.0
        img_max = self.window_center + self.window_width / 2.0
        
        img = np.clip(arr, img_min, img_max)
        img = (img - img_min) / (img_max - img_min) * 255.0
        img = img.astype(np.uint8)
        
        if self.is_inverted:
            img = 255 - img
            
        h, w = img.shape
        # Crucial to copy() so memory isn't collected
        qimg = QImage(img.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
        
        pixmap = QPixmap.fromImage(qimg)
        self.view.pixmap_item.setPixmap(pixmap)
        
        # Apply rotation
        self.view.pixmap_item.setTransformOriginPoint(pixmap.width()/2, pixmap.height()/2)
        self.view.pixmap_item.setRotation(self.rotation_angle)
        
        # Update HUD
        self.view.hud_data["bottom_left"] = f"W: {self.window_width} L: {self.window_center}"
        self.view.hud_data["bottom_right"] = f"Slice: {self.current_idx + 1}/{len(self.pixel_arrays)}"
        self.view.viewport().update()

    def _on_slider_w(self, val):
        self.window_width = val
        self.update_image()
        
    def _on_slider_l(self, val):
        self.window_center = val
        self.update_image()
        
    def _on_slice_scroll(self, val):
        self.current_idx = val
        self.update_image()

    def _next_slice(self):
        nxt = self.current_idx + 1
        if nxt >= len(self.pixel_arrays):
            nxt = 0
        self.slice_scrollbar.setValue(nxt)

    def toggle_invert(self):
        self.is_inverted = not self.is_inverted
        self.update_image()
        
    def rotate_image(self):
        self.rotation_angle = (self.rotation_angle + 90) % 360
        self.update_image()
        
    def reset_view(self):
        self.rotation_angle = 0
        self.is_inverted = False
        self.view.fitInView(self.view.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.update_image()

    def toggle_cine(self):
        if self.btn_cine.isChecked():
            self.cine_timer.start(100) # 10 fps
        else:
            self.cine_timer.stop()
