import os
import numpy as np
import pydicom
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QSlider, QLabel, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                             QSplitter, QComboBox)
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

        self.pixel_arrays_2 = []
        self.dcm_files_2 = []
        self.current_idx_2 = 0
        
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
        main_win = self.window()
        if hasattr(main_win, 'stack') and main_win.stack.currentWidget() != self:
            return
            
        # dx, dy are typically -0.06 to 0.06. Scale up for scrolling.
        sensitivity = 5000
        h_bar = self.view.horizontalScrollBar()
        v_bar = self.view.verticalScrollBar()
        
        # Translate the view natively if scrollbars are inactive, otherwise scroll
        self.view.translate(-dx * sensitivity * 0.01, -dy * sensitivity * 0.01)
        h_bar.setValue(h_bar.value() - int(dx * sensitivity))
        v_bar.setValue(v_bar.value() - int(dy * sensitivity))

    def _handle_gesture_zoom(self, direction: int):
        """Called by the gesture thread when a zoom gesture is detected."""
        main_win = self.window()
        if hasattr(main_win, 'stack') and main_win.stack.currentWidget() != self:
            return
            
        zoom_in_factor = 1.03
        zoom_out_factor = 1.0 / zoom_in_factor
        
        if direction > 0:
            zoom_factor = zoom_in_factor
        else:
            zoom_factor = zoom_out_factor
            
        # Set anchor to center so it zooms smoothly
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
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

        self.scan_selector = QComboBox()
        self.scan_selector.setStyleSheet(btn_style)
        self.scan_selector.currentIndexChanged.connect(self._on_scan_selected)
        
        toolbar.addWidget(QLabel("Select X-Ray:"))
        toolbar.addWidget(self.scan_selector)
        toolbar.addSpacing(15)
        toolbar.addWidget(self.btn_invert)
        toolbar.addWidget(self.btn_rotate)
        toolbar.addWidget(self.btn_reset)
        toolbar.addWidget(self.btn_cine)
        self.btn_compare = QPushButton("Compare Mode")
        self.btn_compare.setStyleSheet(btn_style)
        self.btn_compare.setCheckable(True)
        self.btn_compare.clicked.connect(self.toggle_compare)
        
        self.scan_selector_2 = QComboBox()
        self.scan_selector_2.setStyleSheet(btn_style)
        self.scan_selector_2.currentIndexChanged.connect(self._on_scan_selected_2)
        self.scan_selector_2.setVisible(False)
        self.lbl_compare = QLabel("Compare with:")
        self.lbl_compare.setVisible(False)
        
        toolbar.addSpacing(15)
        toolbar.addWidget(self.btn_compare)
        toolbar.addWidget(self.lbl_compare)
        toolbar.addWidget(self.scan_selector_2)

        
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

        self.view2 = ClinicalGraphicsView()
        self.view2.setVisible(False)
        
        self.slice_scrollbar2 = QSlider(Qt.Orientation.Vertical)
        self.slice_scrollbar2.setInvertedAppearance(True)
        self.slice_scrollbar2.setStyleSheet("""
            QSlider::groove:vertical { background: #222; width: 8px; border-radius: 4px; }
            QSlider::handle:vertical { background: #7cfc00; height: 30px; margin: 0 -4px; border-radius: 4px; }
        """)
        self.slice_scrollbar2.valueChanged.connect(self._on_slice_scroll_2)
        self.slice_scrollbar2.setVisible(False)
        
        view_layout.addWidget(self.view2, stretch=1)
        view_layout.addWidget(self.slice_scrollbar2)
        main_layout.addLayout(view_layout, stretch=1)

    def update_scan_list(self):
        from database import get_patients_for_ui
        patients = get_patients_for_ui()
        
        current_path = None
        if self.scan_selector.currentIndex() > 0:
            data = self.scan_selector.itemData(self.scan_selector.currentIndex())
            if data: current_path = data[0]

        self.scan_selector.blockSignals(True)
        self.scan_selector.clear()
        self.scan_selector.addItem("--- Select a 2D Scan ---", None)
        
        has_2d = False
        target_idx = 1
        
        for p in patients:
            s = p.get("_scan", {})
            if s and s.get("slice_count", 0) <= 30:
                has_2d = True
                path = s.get("file_path")
                self.scan_selector.addItem(f"{p['name']} - {s.get('description', 'Scan')}", (path, p['name']))
                if path == current_path:
                    target_idx = self.scan_selector.count() - 1
                    
        self.scan_selector.blockSignals(False)
        
        self.scan_selector_2.blockSignals(True)
        self.scan_selector_2.clear()
        self.scan_selector_2.addItem("--- Select a Scan to Compare ---", None)
        for i in range(1, self.scan_selector.count()):
            self.scan_selector_2.addItem(self.scan_selector.itemText(i), self.scan_selector.itemData(i))
        self.scan_selector_2.blockSignals(False)

        # Always trigger a load so it's never blank
        if has_2d:
            self.scan_selector.setCurrentIndex(target_idx)
            # If the index didn't change (e.g. was already target_idx), the signal won't fire. Force it:
            self._on_scan_selected(target_idx)

    def _on_scan_selected(self, index):
        data = self.scan_selector.itemData(index)
        if data:
            folder_path, name = data
            self.load_scan(folder_path, name)

    def load_scan(self, folder_path: str, patient_name: str = "Unknown"):
        from config import resolve_scan_path
        folder_path = resolve_scan_path(folder_path)
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
        except:
            pass # Use defaults
            
        self.default_window_width = self.window_width
        self.default_window_center = self.window_center
        
        self.slider_w.blockSignals(True)
        self.slider_w.setValue(self.window_width)
        self.slider_w.blockSignals(False)
        
        self.slider_l.blockSignals(True)
        self.slider_l.setValue(self.window_center)
        self.slider_l.blockSignals(False)
            
        # Update HUD
        self.view.hud_data["top_left"] = f"Patient: {patient_name}"
        self.view.hud_data["top_right"] = f"Modality: {getattr(first, 'Modality', 'CT')}"
        
        # Setup Scrollbar
        self.slice_scrollbar.setRange(0, len(self.pixel_arrays) - 1)
        self.slice_scrollbar.setValue(0)
        
        self.current_idx = 0

        self.pixel_arrays_2 = []
        self.dcm_files_2 = []
        self.current_idx_2 = 0
        self.update_image()
        if hasattr(self, "view2") and self.view2.isVisible(): self.update_image_2()
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
            
        # Ensure memory is contiguous before passing to C++ QImage
        img = np.ascontiguousarray(img)
            
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
        if hasattr(self, "view2") and self.view2.isVisible(): self.update_image_2()
        
    def _on_slider_l(self, val):
        self.window_center = val
        self.update_image()
        if hasattr(self, "view2") and self.view2.isVisible(): self.update_image_2()
        
    def _on_slice_scroll(self, val):
        self.current_idx = val
        self.update_image()
        if hasattr(self, "view2") and self.view2.isVisible(): self.update_image_2()

    def _next_slice(self):
        nxt = self.current_idx + 1
        if nxt >= len(self.pixel_arrays):
            nxt = 0
        self.slice_scrollbar.setValue(nxt)

    def toggle_invert(self):
        self.is_inverted = not self.is_inverted
        self.update_image()
        if hasattr(self, "view2") and self.view2.isVisible(): self.update_image_2()
        
    def rotate_image(self):
        self.rotation_angle = (self.rotation_angle + 90) % 360
        self.update_image()
        if hasattr(self, "view2") and self.view2.isVisible(): self.update_image_2()
        
    def reset_view(self):
        self.rotation_angle = 0
        self.is_inverted = False
        
        # Reset window/level
        if hasattr(self, 'default_window_width'):
            self.window_width = self.default_window_width
            self.window_center = self.default_window_center
            
            self.slider_w.blockSignals(True)
            self.slider_w.setValue(self.window_width)
            self.slider_w.blockSignals(False)
            
            self.slider_l.blockSignals(True)
            self.slider_l.setValue(self.window_center)
            self.slider_l.blockSignals(False)
            
        self.update_image()
        if hasattr(self, "view2") and self.view2.isVisible(): self.update_image_2()
        self.view.fitInView(self.view.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def toggle_cine(self):
        if self.btn_cine.isChecked():
            self.cine_timer.start(100) # 10 fps
        else:
            self.cine_timer.stop()

    def toggle_compare(self):
        is_comparing = self.btn_compare.isChecked()
        self.scan_selector_2.setVisible(is_comparing)
        self.lbl_compare.setVisible(is_comparing)
        self.view2.setVisible(is_comparing)
        if is_comparing and self.pixel_arrays_2:
            self.slice_scrollbar2.setVisible(True)
        else:
            self.slice_scrollbar2.setVisible(False)

    def _on_scan_selected_2(self, index):
        data = self.scan_selector_2.itemData(index)
        if data:
            folder_path, name = data
            self.load_scan_2(folder_path, name)

    def load_scan_2(self, folder_path: str, patient_name: str = "Unknown"):
        from config import resolve_scan_path
        import os, pydicom, numpy as np
        folder_path = resolve_scan_path(folder_path)
        self.dcm_files_2 = []
        self.pixel_arrays_2 = []
        
        if not os.path.exists(folder_path): return

        files = [f for f in os.listdir(folder_path) if f.lower().endswith(".dcm")]
        if not files: return
            
        slices = []
        for f in files:
            try:
                ds = pydicom.dcmread(os.path.join(folder_path, f))
                slices.append(ds)
            except: pass
                
        slices.sort(key=lambda x: float(getattr(x, "InstanceNumber", 0)))
        self.dcm_files_2 = slices
        
        for ds in self.dcm_files_2:
            arr = ds.pixel_array
            slope = float(getattr(ds, "RescaleSlope", 1.0))
            intercept = float(getattr(ds, "RescaleIntercept", 0.0))
            self.pixel_arrays_2.append(arr * slope + intercept)
            
        first = self.dcm_files_2[0]
        self.view2.hud_data["top_left"] = f"Patient: {patient_name}"
        self.view2.hud_data["top_right"] = f"Modality: {getattr(first, 'Modality', 'CT')}"
        
        self.slice_scrollbar2.setRange(0, len(self.pixel_arrays_2) - 1)
        self.slice_scrollbar2.setValue(0)
        self.slice_scrollbar2.setVisible(True)
        
        self.current_idx_2 = 0
        self.update_image_2()
        self.view2.fitInView(self.view2.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def update_image_2(self):
        import numpy as np
        from PyQt6.QtGui import QImage, QPixmap
        if not self.pixel_arrays_2: return
            
        arr = self.pixel_arrays_2[self.current_idx_2]
        img_min = self.window_center - self.window_width / 2.0
        img_max = self.window_center + self.window_width / 2.0
        
        img = np.clip(arr, img_min, img_max)
        img = (img - img_min) / (img_max - img_min) * 255.0
        img = img.astype(np.uint8)
        
        if self.is_inverted: img = 255 - img
            
        img = np.ascontiguousarray(img)
        h, w = img.shape
        qimg = QImage(img.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
        
        pixmap = QPixmap.fromImage(qimg)
        self.view2.pixmap_item.setPixmap(pixmap)
        
        self.view2.pixmap_item.setTransformOriginPoint(pixmap.width()/2, pixmap.height()/2)
        self.view2.pixmap_item.setRotation(self.rotation_angle)
        
        self.view2.hud_data["bottom_left"] = f"W: {self.window_width} L: {self.window_center}"
        self.view2.hud_data["bottom_right"] = f"Slice: {self.current_idx_2 + 1}/{len(self.pixel_arrays_2)}"
        self.view2.viewport().update()

    def _on_slice_scroll_2(self, val):
        self.current_idx_2 = val
        self.update_image_2()
