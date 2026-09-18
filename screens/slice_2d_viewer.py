# screens/slice_2d_viewer.py
"""
Interactive 2D CT Slice Viewer for Aegis-Touch / Marvel-GPP.

Features:
  - Real-time Orthogonal Slicing (Axial, Coronal, Sagittal) directly from loaded DicomVolume.
  - Zero-copy volume reuse without redundant DICOM reads or 3D mesh regeneration.
  - True Hounsfield Unit (HU) calibrated pixel values and hover inspection.
  - Interactive Window/Level controls with clinical presets (Bone, Soft Tissue, Lung)
    and continuous manual sliders.
  - Calibrated physical aspect-ratio preservation based on voxel spacing (dx, dy, dz).
  - Interactive slice scrubbing with slider and step buttons.
  - Crosshair indicator and coordinate/HU readout.
  - Seamless side-by-side coexistence with the 3D viewer.
  - Non-diagnostic disclaimer and safe empty-state handling.
"""

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QFrame, QSizePolicy, QButtonGroup
)
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush, QFont, QImage, QPixmap


from dataclasses import dataclass, field
import uuid


@dataclass
class Measurement2D:
    """Represents a completed 2D physical distance measurement on a CT slice."""
    id: str
    start_u: float
    start_v: float
    end_u: float
    end_v: float
    start_px: int
    start_py: int
    end_px: int
    end_py: int
    distance_mm: float
    orientation: str = "axial"
    slice_idx: int = 0
    physical_start: tuple[float, float, float] = (0.0, 0.0, 0.0)
    physical_end: tuple[float, float, float] = (0.0, 0.0, 0.0)


# ── Standard Clinical Window/Level Presets ────────────────────────────────────
WL_PRESETS = {
    "Bone":        (1800.0,  400.0, "Cortical & trabecular bone"),
    "Soft Tissue": ( 400.0,   40.0, "Abdominal & muscular soft tissue"),
    "Lung":        (1500.0, -600.0, "Pulmonary parenchyma & bronchial airways"),
}


class CTSliceCanvas(QWidget):
    """Interactive 2D viewport rendering an orthogonal CT slice with aspect-ratio preservation."""
    pixel_hovered = pyqtSignal(int, int, float)   # pixel_x, pixel_y, hu_val
    crosshair_moved = pyqtSignal(float, float)     # normalized (u, v) in [0, 1]
    measurement_point_selected = pyqtSignal(int, float, float)  # point_num (1 or 2), u, v
    measurement_added = pyqtSignal(object)         # Measurement2D
    measurement_state_changed = pyqtSignal(str)    # status string

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background: #000; border: 1px solid #222; border-radius: 4px;")
        self.setMouseTracking(True)

        self._pixmap: QPixmap | None = None
        self._raw_slice: np.ndarray | None = None
        self.mm_per_pixel_x: float = 1.0
        self.mm_per_pixel_y: float = 1.0

        self.cross_u: float = 0.5
        self.cross_v: float = 0.5
        self.plane_name: str = "Axial"
        self.current_slice_idx: int = 0
        self.show_crosshair: bool = True

        # Measurement state
        self.measuring_active: bool = False
        self.show_measurements: bool = True
        self.measurements: list[Measurement2D] = []
        self.pending_point: tuple[float, float] | None = None
        self.current_hover_u_v: tuple[float, float] | None = None

        self._hover_pixel: tuple[int, int] | None = None
        self._hover_hu: float | None = None

    def set_measuring(self, active: bool):
        """Enable or disable distance measurement mode."""
        self.measuring_active = bool(active)
        if not self.measuring_active:
            self.cancel_pending_measurement()
            self.measurement_state_changed.emit("Idle")
        else:
            self.measurement_state_changed.emit("Select first point")
        self.update()

    def is_measuring(self) -> bool:
        return bool(self.measuring_active)

    def set_measurements_visible(self, visible: bool):
        self.show_measurements = bool(visible)
        self.update()

    def is_measurements_visible(self) -> bool:
        return bool(self.show_measurements)

    def cancel_pending_measurement(self):
        """Cancels an in-progress first point selection."""
        if self.pending_point is not None:
            self.pending_point = None
            self.current_hover_u_v = None
            if self.measuring_active:
                self.measurement_state_changed.emit("Select first point")
            self.update()

    def clear_measurements(self):
        """Removes all completed measurements and active pending points."""
        self.measurements.clear()
        self.pending_point = None
        self.current_hover_u_v = None
        self.measurement_state_changed.emit("Idle" if not self.measuring_active else "Select first point")
        self.update()

    def get_measurements(self) -> list[Measurement2D]:
        return list(self.measurements)

    def set_crosshair_visible(self, visible: bool):
        self.show_crosshair = bool(visible)
        self.update()

    def set_slice_data(
        self,
        pixmap: QPixmap | None,
        raw_slice: np.ndarray | None,
        mm_x: float,
        mm_y: float,
        plane_name: str = "Axial"
    ):
        self._pixmap = pixmap
        self._raw_slice = raw_slice
        self.mm_per_pixel_x = max(1e-4, float(mm_x))
        self.mm_per_pixel_y = max(1e-4, float(mm_y))
        self.plane_name = plane_name
        self.update()

    def set_crosshairs(self, u: float, v: float):
        self.cross_u = float(np.clip(u, 0.0, 1.0))
        self.cross_v = float(np.clip(v, 0.0, 1.0))
        self.update()

    def _get_target_rect(self) -> QRectF:
        """Returns bounding box maintaining true physical aspect ratio (width_mm : height_mm)."""
        if not self._pixmap or self._pixmap.isNull():
            return QRectF(self.rect())

        pw = self._pixmap.width()
        ph = self._pixmap.height()
        ww = max(10, self.width() - 8)
        wh = max(10, self.height() - 8)

        phys_w = pw * self.mm_per_pixel_x
        phys_h = ph * self.mm_per_pixel_y

        scale = min(ww / (phys_w + 1e-5), wh / (phys_h + 1e-5))
        rw = phys_w * scale
        rh = phys_h * scale
        rx = (self.width() - rw) / 2.0
        ry = (self.height() - rh) / 2.0
        return QRectF(rx, ry, rw, rh)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = self._get_target_rect()

        if self._pixmap and not self._pixmap.isNull():
            painter.drawPixmap(rect.toRect(), self._pixmap)
        else:
            painter.setPen(QColor(100, 100, 100))
            painter.setFont(QFont("sans-serif", 10))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No CT Scan Loaded")
            return

        # 1. Crosshair lines (dashed cyan) - drawn only when enabled
        if getattr(self, "show_crosshair", True):
            cx = rect.left() + self.cross_u * rect.width()
            cy = rect.top()  + self.cross_v * rect.height()

            pen_cross = QPen(QColor(0, 229, 255, 140), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen_cross)
            painter.drawLine(int(rect.left()), int(cy), int(rect.right()), int(cy))
            painter.drawLine(int(cx), int(rect.top()), int(cx), int(rect.bottom()))

        # 2. Orientation Badge
        painter.setPen(QColor(0, 229, 255))
        painter.setFont(QFont("sans-serif", 9, QFont.Weight.Bold))
        badge_rect = QRectF(rect.left() + 8, rect.top() + 8, 140, 16)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignLeft, self.plane_name.upper())

        # 3. Hover coordinate point & readout
        if self._hover_pixel is not None and self._hover_hu is not None:
            px, py = self._hover_pixel
            hx = rect.left() + (px / max(1, self._raw_slice.shape[1])) * rect.width()
            hy = rect.top()  + (py / max(1, self._raw_slice.shape[0])) * rect.height()

            painter.setPen(QPen(QColor(255, 235, 59), 1))
            painter.setBrush(QBrush(QColor(255, 235, 59, 180)))
            painter.drawEllipse(QPointF(hx, hy), 3, 3)

        # 4. Completed Measurements
        if getattr(self, "show_measurements", True):
            cur_orient = self.plane_name.lower()
            cur_slice = getattr(self, "current_slice_idx", None)
            for m in getattr(self, "measurements", []):
                if m.orientation == cur_orient:
                    if cur_slice is not None and m.slice_idx is not None and m.slice_idx != cur_slice:
                        continue

                    x1 = rect.left() + m.start_u * rect.width()
                    y1 = rect.top()  + m.start_v * rect.height()
                    x2 = rect.left() + m.end_u   * rect.width()
                    y2 = rect.top()  + m.end_v   * rect.height()

                    # Measurement line
                    pen_meas = QPen(QColor(255, 234, 0, 220), 2, Qt.PenStyle.SolidLine)
                    painter.setPen(pen_meas)
                    painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

                    # Endpoint markers
                    painter.setPen(QPen(QColor(255, 214, 0), 1))
                    painter.setBrush(QBrush(QColor(255, 234, 0)))
                    painter.drawEllipse(QPointF(x1, y1), 3.5, 3.5)
                    painter.drawEllipse(QPointF(x2, y2), 3.5, 3.5)

                    # Distance label badge
                    mx = (x1 + x2) / 2.0
                    my = (y1 + y2) / 2.0
                    lbl_str = f"{m.distance_mm:.1f} mm"
                    font_lbl = QFont("sans-serif", 8, QFont.Weight.Bold)
                    painter.setFont(font_lbl)
                    fm = painter.fontMetrics()
                    tw = fm.horizontalAdvance(lbl_str) + 8
                    th = fm.height() + 4

                    badge_rect = QRectF(mx - tw / 2.0, my - th / 2.0, tw, th)
                    painter.setPen(QPen(QColor(255, 234, 0, 180), 1))
                    painter.setBrush(QBrush(QColor(15, 15, 15, 220)))
                    painter.drawRoundedRect(badge_rect, 3, 3)

                    painter.setPen(QColor(255, 255, 255))
                    painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, lbl_str)

            # In-progress measurement (pending 1st point)
            if self.measuring_active and self.pending_point is not None:
                u1, v1 = self.pending_point
                x1 = rect.left() + u1 * rect.width()
                y1 = rect.top()  + v1 * rect.height()

                painter.setPen(QPen(QColor(255, 234, 0), 1))
                painter.setBrush(QBrush(QColor(255, 234, 0)))
                painter.drawEllipse(QPointF(x1, y1), 4, 4)

                if self.current_hover_u_v is not None:
                    u2, v2 = self.current_hover_u_v
                    x2 = rect.left() + u2 * rect.width()
                    y2 = rect.top()  + v2 * rect.height()

                    pen_dash = QPen(QColor(255, 234, 0, 180), 1.5, Qt.PenStyle.DashLine)
                    painter.setPen(pen_dash)
                    painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
                    painter.drawEllipse(QPointF(x2, y2), 3, 3)

                    if self._raw_slice is not None:
                        sh = self._raw_slice.shape
                        px1 = int(round(u1 * (sh[1] - 1)))
                        py1 = int(round(v1 * (sh[0] - 1)))
                        px2 = int(round(u2 * (sh[1] - 1)))
                        py2 = int(round(v2 * (sh[0] - 1)))
                        dx_mm = (px2 - px1) * self.mm_per_pixel_x
                        dy_mm = (py2 - py1) * self.mm_per_pixel_y
                        p_dist = float(np.sqrt(dx_mm ** 2 + dy_mm ** 2))
                        p_str = f"{p_dist:.1f} mm"

                        pmx = (x1 + x2) / 2.0
                        pmy = (y1 + y2) / 2.0
                        pfm = painter.fontMetrics()
                        ptw = pfm.horizontalAdvance(p_str) + 6
                        pth = pfm.height() + 2
                        p_badge = QRectF(pmx - ptw / 2.0, pmy - pth / 2.0, ptw, pth)
                        painter.setPen(QPen(QColor(255, 234, 0, 140), 1))
                        painter.setBrush(QBrush(QColor(10, 10, 10, 200)))
                        painter.drawRoundedRect(p_badge, 2, 2)
                        painter.setPen(QColor(255, 255, 255))
                        painter.drawText(p_badge, Qt.AlignmentFlag.AlignCenter, p_str)

    def mouseMoveEvent(self, event):
        rect = self._get_target_rect()
        if rect.width() <= 0 or rect.height() <= 0 or self._raw_slice is None:
            return

        pos = event.position()
        if not rect.contains(pos):
            self._hover_pixel = None
            self._hover_hu = None
            self.update()
            return

        u = float(np.clip((pos.x() - rect.left()) / rect.width(), 0.0, 1.0))
        v = float(np.clip((pos.y() - rect.top()) / rect.height(), 0.0, 1.0))

        sh = self._raw_slice.shape
        px = int(np.clip(u * sh[1], 0, sh[1] - 1))
        py = int(np.clip(v * sh[0], 0, sh[0] - 1))

        hu = float(self._raw_slice[py, px])
        self._hover_pixel = (px, py)
        self._hover_hu = hu
        self.pixel_hovered.emit(px, py, hu)

        if self.measuring_active and self.pending_point is not None:
            self.current_hover_u_v = (u, v)
            if self._raw_slice is not None:
                px1 = int(round(self.pending_point[0] * (sh[1] - 1)))
                py1 = int(round(self.pending_point[1] * (sh[0] - 1)))
                dx_mm = (px - px1) * self.mm_per_pixel_x
                dy_mm = (py - py1) * self.mm_per_pixel_y
                live_dist = float(np.sqrt(dx_mm ** 2 + dy_mm ** 2))
                self.measurement_state_changed.emit(f"Select second point ({live_dist:.1f} mm)")
            self.update()
            return

        if event.buttons() & Qt.MouseButton.LeftButton:
            self.cross_u = u
            self.cross_v = v
            self.crosshair_moved.emit(u, v)

        self.update()

    def mousePressEvent(self, event):
        rect = self._get_target_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return

        if event.button() == Qt.MouseButton.LeftButton:
            u = float(np.clip((event.position().x() - rect.left()) / rect.width(), 0.0, 1.0))
            v = float(np.clip((event.position().y() - rect.top()) / rect.height(), 0.0, 1.0))

            if self.measuring_active and self._raw_slice is not None:
                if self.pending_point is None:
                    # Select first point
                    self.pending_point = (u, v)
                    self.measurement_point_selected.emit(1, u, v)
                    self.measurement_state_changed.emit("Select second point")
                    self.update()
                else:
                    # Select second point & complete measurement
                    u1, v1 = self.pending_point
                    u2, v2 = u, v
                    sh = self._raw_slice.shape
                    px1 = int(round(u1 * (sh[1] - 1)))
                    py1 = int(round(v1 * (sh[0] - 1)))
                    px2 = int(round(u2 * (sh[1] - 1)))
                    py2 = int(round(v2 * (sh[0] - 1)))

                    dx_mm = (px2 - px1) * self.mm_per_pixel_x
                    dy_mm = (py2 - py1) * self.mm_per_pixel_y
                    dist_mm = float(np.sqrt(dx_mm ** 2 + dy_mm ** 2))

                    m = Measurement2D(
                        id=str(uuid.uuid4())[:8],
                        start_u=u1,
                        start_v=v1,
                        end_u=u2,
                        end_v=v2,
                        start_px=px1,
                        start_py=py1,
                        end_px=px2,
                        end_py=py2,
                        distance_mm=dist_mm,
                        orientation=self.plane_name.lower(),
                        slice_idx=getattr(self, "current_slice_idx", 0)
                    )
                    self.measurements.append(m)
                    self.pending_point = None
                    self.current_hover_u_v = None
                    self.measurement_point_selected.emit(2, u2, v2)
                    self.measurement_added.emit(m)
                    self.measurement_state_changed.emit(f"Distance: {dist_mm:.1f} mm")
                    self.update()
                return

            self.cross_u = u
            self.cross_v = v
            self.crosshair_moved.emit(u, v)
            self.update()

        elif event.button() == Qt.MouseButton.RightButton:
            if self.measuring_active and self.pending_point is not None:
                self.cancel_pending_measurement()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hover_pixel = None
        self._hover_hu = None
        self.current_hover_u_v = None
        self.update()


class Slice2DViewerWidget(QWidget):
    """Compact 2D CT slice viewer panel designed to coexist side-by-side with 3D viewer."""
    slice_changed = pyqtSignal(str, int)  # orientation, slice_index
    reference_position_changed = pyqtSignal(float, float, float)  # physical x_mm, y_mm, z_mm
    closed = pyqtSignal()
    measurement_added = pyqtSignal(object)  # Measurement2D
    measurement_cleared = pyqtSignal()
    measurement_state_changed = pyqtSignal(str)
    window_level_changed = pyqtSignal(float, float)  # window_width, window_level
    orientation_changed = pyqtSignal(str)  # orientation ("axial", "coronal", "sagittal")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vol_data: np.ndarray | None = None
        self.pixel_spacing: tuple[float, float] = (1.0, 1.0)  # (dy, dx)
        self.slice_thickness: float = 1.0                     # dz

        self.current_orientation: str = "axial"
        self.slice_indices: dict[str, int] = {
            "axial": 0,
            "coronal": 0,
            "sagittal": 0,
        }
        self.show_crosshair: bool = True

        self.cur_wl_preset: str = "Bone"
        self.window_width: float = 1800.0
        self.window_level: float = 400.0
        self._wl_lut: np.ndarray | None = None
        self._build_wl_lut()

        self._build_ui()

    def _build_wl_lut(self):
        """Precomputes 1D lookup table mapping HU range [-1024, 3071] to 8-bit [0, 255]."""
        hu = np.arange(-1024, 3072, dtype=np.float32)
        low = self.window_level - (self.window_width / 2.0)
        norm = np.clip((hu - low) / (self.window_width + 1e-5) * 255.0, 0, 255).astype(np.uint8)
        self._wl_lut = norm

    def _build_ui(self):
        self.setStyleSheet("background: #0d0d0d; border-left: 1px solid #202020;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        # ── 1. Header Bar: Title, Orientation Buttons, Close ─────────────────
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(6)

        title = QLabel("🖼 2D CT SLICE")
        title.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;")
        top_bar.addWidget(title)
        top_bar.addSpacing(6)

        self.btn_group_orient = QButtonGroup(self)
        self.btn_axial = QPushButton("Axial")
        self.btn_coronal = QPushButton("Coronal")
        self.btn_sagittal = QPushButton("Sagittal")

        for idx, btn in enumerate((self.btn_axial, self.btn_coronal, self.btn_sagittal)):
            btn.setCheckable(True)
            btn.setFixedHeight(22)
            btn.setStyleSheet(
                "QPushButton { background: #181818; color: #888; border: 1px solid #2e2e2e; "
                "border-radius: 3px; font-size: 9px; font-weight: 600; padding: 0 8px; }"
                "QPushButton:checked { background: #00222a; color: #00e5ff; border-color: #00b4d8; }"
                "QPushButton:hover { background: #222; color: #ccc; }"
            )
            self.btn_group_orient.addButton(btn, idx)
            top_bar.addWidget(btn)

        self.btn_axial.setChecked(True)
        self.btn_axial.clicked.connect(lambda: self.set_orientation("axial"))
        self.btn_coronal.clicked.connect(lambda: self.set_orientation("coronal"))
        self.btn_sagittal.clicked.connect(lambda: self.set_orientation("sagittal"))

        top_bar.addStretch()

        self.btn_crosshair_toggle = QPushButton("🎯 Crosshair: ON")
        self.btn_crosshair_toggle.setCheckable(True)
        self.btn_crosshair_toggle.setChecked(True)
        self.btn_crosshair_toggle.setFixedHeight(20)
        self.btn_crosshair_toggle.setStyleSheet(
            "QPushButton { background: #181818; color: #888; border: 1px solid #2e2e2e; "
            "border-radius: 3px; font-size: 8px; font-weight: 600; padding: 0 6px; }"
            "QPushButton:checked { background: #00222a; color: #00e5ff; border-color: #00b4d8; }"
            "QPushButton:hover { background: #222; color: #ccc; }"
        )
        self.btn_crosshair_toggle.clicked.connect(self._on_crosshair_btn_clicked)
        top_bar.addWidget(self.btn_crosshair_toggle)

        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(20, 20)
        self.btn_close.setToolTip("Hide 2D Slice Viewer")
        self.btn_close.setStyleSheet(
            "QPushButton { background: transparent; color: #666; border: none; font-size: 11px; font-weight: bold; }"
            "QPushButton:hover { color: #ff5555; }"
        )
        self.btn_close.clicked.connect(self._on_close_clicked)
        top_bar.addWidget(self.btn_close)

        layout.addLayout(top_bar)

        # ── 2. Slice Navigation Bar ───────────────────────────────────────────
        nav_bar = QHBoxLayout()
        nav_bar.setContentsMargins(0, 0, 0, 0)
        nav_bar.setSpacing(4)

        lbl_s = QLabel("Slice:")
        lbl_s.setStyleSheet("color: #666; font-size: 9px; font-weight: 600; text-transform: uppercase;")
        nav_bar.addWidget(lbl_s)

        self.btn_prev_slice = QPushButton("◀")
        self.btn_prev_slice.setFixedSize(20, 22)
        self.btn_prev_slice.setStyleSheet(
            "QPushButton { background: #181818; color: #888; border: 1px solid #282828; border-radius: 3px; font-size: 8px; }"
            "QPushButton:hover { background: #222; color: #fff; border-color: #444; }"
        )
        self.btn_prev_slice.clicked.connect(lambda: self.step_slice(-1))
        nav_bar.addWidget(self.btn_prev_slice)

        self.slider_slice = QSlider(Qt.Orientation.Horizontal)
        self.slider_slice.setRange(0, 0)
        self.slider_slice.setValue(0)
        self.slider_slice.setFixedHeight(22)
        self.slider_slice.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #222; border-radius: 2px; }"
            "QSlider::sub-page:horizontal { background: #0088a8; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #00e5ff; border: 1px solid #00b4d8; width: 12px; "
            "margin-top: -4px; margin-bottom: -4px; border-radius: 6px; }"
            "QSlider::handle:horizontal:hover { background: #fff; border-color: #00e5ff; }"
        )
        self.slider_slice.valueChanged.connect(self._on_slice_slider_changed)
        nav_bar.addWidget(self.slider_slice, stretch=1)

        self.btn_next_slice = QPushButton("▶")
        self.btn_next_slice.setFixedSize(20, 22)
        self.btn_next_slice.setStyleSheet(
            "QPushButton { background: #181818; color: #888; border: 1px solid #282828; border-radius: 3px; font-size: 8px; }"
            "QPushButton:hover { background: #222; color: #fff; border-color: #444; }"
        )
        self.btn_next_slice.clicked.connect(lambda: self.step_slice(1))
        nav_bar.addWidget(self.btn_next_slice)

        self.lbl_slice_info = QLabel("0 / 0")
        self.lbl_slice_info.setStyleSheet("color: #00e5ff; font-size: 9px; font-weight: 600; min-width: 90px;")
        nav_bar.addWidget(self.lbl_slice_info)

        layout.addLayout(nav_bar)

        # ── 2b. Distance Measurement Bar ──────────────────────────────────────
        measure_bar = QHBoxLayout()
        measure_bar.setContentsMargins(0, 0, 0, 0)
        measure_bar.setSpacing(4)

        self.btn_measure = QPushButton("📏 Measure: OFF")
        self.btn_measure.setCheckable(True)
        self.btn_measure.setChecked(False)
        self.btn_measure.setFixedHeight(20)
        self.btn_measure.setStyleSheet(
            "QPushButton { background: #141414; color: #888; border: 1px solid #282828; "
            "border-radius: 3px; font-size: 8px; font-weight: 600; padding: 0 6px; }"
            "QPushButton:checked { background: #2a2200; color: #ffea00; border-color: #ffd600; }"
            "QPushButton:hover { background: #222; color: #ccc; }"
        )
        self.btn_measure.clicked.connect(self._on_measure_btn_clicked)
        measure_bar.addWidget(self.btn_measure)

        self.btn_measure_vis = QPushButton("👁 Show")
        self.btn_measure_vis.setCheckable(True)
        self.btn_measure_vis.setChecked(True)
        self.btn_measure_vis.setFixedHeight(20)
        self.btn_measure_vis.setStyleSheet(
            "QPushButton { background: #141414; color: #888; border: 1px solid #282828; "
            "border-radius: 3px; font-size: 8px; font-weight: 600; padding: 0 6px; }"
            "QPushButton:checked { background: #181818; color: #00e5ff; border-color: #00b4d8; }"
            "QPushButton:hover { background: #222; color: #ccc; }"
        )
        self.btn_measure_vis.clicked.connect(self._on_measure_vis_clicked)
        measure_bar.addWidget(self.btn_measure_vis)

        self.btn_clear_measure = QPushButton("🗑 Clear")
        self.btn_clear_measure.setFixedHeight(20)
        self.btn_clear_measure.setStyleSheet(
            "QPushButton { background: #141414; color: #888; border: 1px solid #282828; "
            "border-radius: 3px; font-size: 8px; font-weight: 600; padding: 0 6px; }"
            "QPushButton:hover { background: #222; color: #ff5555; border-color: #ff5555; }"
        )
        self.btn_clear_measure.clicked.connect(self.clear_measurements)
        measure_bar.addWidget(self.btn_clear_measure)

        self.lbl_measure_status = QLabel("Idle")
        self.lbl_measure_status.setStyleSheet("color: #aaa; font-size: 8px; font-weight: 500;")
        measure_bar.addWidget(self.lbl_measure_status, stretch=1)

        layout.addLayout(measure_bar)

        # ── 3. Central Slice Canvas ───────────────────────────────────────────
        self.canvas = CTSliceCanvas(self)
        self.canvas.pixel_hovered.connect(self._on_canvas_hover)
        self.canvas.crosshair_moved.connect(self._on_canvas_crosshair)
        self.canvas.measurement_added.connect(self._on_canvas_measurement_added)
        self.canvas.measurement_state_changed.connect(self._on_canvas_measurement_state_changed)
        layout.addWidget(self.canvas, stretch=1)

        # ── 4. Window / Level Controls & Presets ──────────────────────────────
        wl_box = QVBoxLayout()
        wl_box.setSpacing(4)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(4)
        lbl_wl = QLabel("Preset:")
        lbl_wl.setStyleSheet("color: #666; font-size: 9px; font-weight: 600;")
        preset_row.addWidget(lbl_wl)

        self.wl_btn_group = QButtonGroup(self)
        self.btn_wl_bone = QPushButton("Bone")
        self.btn_wl_soft = QPushButton("Soft Tissue")
        self.btn_wl_lung = QPushButton("Lung")

        for idx, (btn, name) in enumerate((
            (self.btn_wl_bone, "Bone"),
            (self.btn_wl_soft, "Soft Tissue"),
            (self.btn_wl_lung, "Lung")
        )):
            btn.setCheckable(True)
            btn.setFixedHeight(20)
            btn.setStyleSheet(
                "QPushButton { background: #141414; color: #777; border: 1px solid #262626; "
                "border-radius: 3px; font-size: 8px; font-weight: 600; padding: 0 6px; }"
                "QPushButton:checked { background: #1a2a1a; color: #7cfc00; border-color: #3a5a3a; }"
                "QPushButton:hover { background: #1e1e1e; color: #bbb; }"
            )
            self.wl_btn_group.addButton(btn, idx)
            preset_row.addWidget(btn)
            btn.clicked.connect(lambda checked, n=name: self.set_window_preset(n))

        self.btn_wl_bone.setChecked(True)
        preset_row.addStretch()

        self.lbl_wl_values = QLabel("W: 1800  L: 400")
        self.lbl_wl_values.setStyleSheet("color: #888; font-size: 9px; font-family: monospace;")
        preset_row.addWidget(self.lbl_wl_values)
        wl_box.addLayout(preset_row)

        # Continuous W / L Sliders
        sliders_row = QHBoxLayout()
        sliders_row.setSpacing(6)

        lbl_w = QLabel("W:")
        lbl_w.setStyleSheet("color: #555; font-size: 8px; font-weight: 600;")
        sliders_row.addWidget(lbl_w)

        self.slider_window = QSlider(Qt.Orientation.Horizontal)
        self.slider_window.setRange(100, 3000)
        self.slider_window.setValue(1800)
        self.slider_window.setFixedHeight(18)
        self.slider_window.setStyleSheet(
            "QSlider::groove:horizontal { height: 3px; background: #222; border-radius: 1px; }"
            "QSlider::handle:horizontal { background: #aaa; width: 8px; margin: -3px 0; border-radius: 4px; }"
        )
        self.slider_window.valueChanged.connect(self._on_window_slider_changed)
        sliders_row.addWidget(self.slider_window, stretch=1)

        lbl_l = QLabel("L:")
        lbl_l.setStyleSheet("color: #555; font-size: 8px; font-weight: 600;")
        sliders_row.addWidget(lbl_l)

        self.slider_level = QSlider(Qt.Orientation.Horizontal)
        self.slider_level.setRange(-1000, 1000)
        self.slider_level.setValue(400)
        self.slider_level.setFixedHeight(18)
        self.slider_level.setStyleSheet(
            "QSlider::groove:horizontal { height: 3px; background: #222; border-radius: 1px; }"
            "QSlider::handle:horizontal { background: #aaa; width: 8px; margin: -3px 0; border-radius: 4px; }"
        )
        self.slider_level.valueChanged.connect(self._on_level_slider_changed)
        sliders_row.addWidget(self.slider_level, stretch=1)

        wl_box.addLayout(sliders_row)
        layout.addLayout(wl_box)

        # ── 5. Status / HU Information Readout ────────────────────────────────
        self.lbl_cursor_info = QLabel("Cursor: (X: --, Y: --) · HU: --")
        self.lbl_cursor_info.setStyleSheet("color: #00e5ff; font-size: 9px; font-family: monospace; font-weight: 600;")
        layout.addWidget(self.lbl_cursor_info)

        disclaimer = QLabel("Heuristic CT 2D viewer — not for primary diagnostic interpretation.")
        disclaimer.setStyleSheet("color: #444; font-size: 8px; font-style: italic;")
        layout.addWidget(disclaimer)

    def _on_close_clicked(self):
        self.setVisible(False)
        self.closed.emit()

    def _on_crosshair_btn_clicked(self):
        self.set_crosshair_visible(self.btn_crosshair_toggle.isChecked())

    def _on_measure_btn_clicked(self):
        self.set_measurement_mode(self.btn_measure.isChecked())

    def _on_measure_vis_clicked(self):
        self.set_measurements_visible(self.btn_measure_vis.isChecked())

    def _on_canvas_measurement_state_changed(self, status: str):
        if hasattr(self, "lbl_measure_status"):
            self.lbl_measure_status.setText(status)
        self.measurement_state_changed.emit(status)

    def _on_canvas_measurement_added(self, m: Measurement2D):
        m.physical_start = self.calc_physical_coords(m.orientation, m.slice_idx, m.start_u, m.start_v)
        m.physical_end = self.calc_physical_coords(m.orientation, m.slice_idx, m.end_u, m.end_v)
        self.measurement_added.emit(m)

    def start_measurement(self):
        self.set_measurement_mode(True)

    def finish_measurement(self):
        self.set_measurement_mode(False)

    def set_measurement_mode(self, active: bool):
        active = bool(active)
        if hasattr(self, "btn_measure"):
            self.btn_measure.blockSignals(True)
            self.btn_measure.setChecked(active)
            self.btn_measure.setText("📏 Measure: ON" if active else "📏 Measure: OFF")
            self.btn_measure.blockSignals(False)
        if hasattr(self, "canvas"):
            self.canvas.set_measuring(active)

    def is_measuring(self) -> bool:
        if hasattr(self, "canvas"):
            return self.canvas.is_measuring()
        return False

    def clear_measurements(self):
        if hasattr(self, "canvas"):
            self.canvas.clear_measurements()
        self.measurement_cleared.emit()

    def set_measurements_visible(self, visible: bool):
        visible = bool(visible)
        if hasattr(self, "btn_measure_vis"):
            self.btn_measure_vis.blockSignals(True)
            self.btn_measure_vis.setChecked(visible)
            self.btn_measure_vis.setText("👁 Show" if visible else "👁 Hide")
            self.btn_measure_vis.blockSignals(False)
        if hasattr(self, "canvas"):
            self.canvas.set_measurements_visible(visible)

    def is_measurements_visible(self) -> bool:
        if hasattr(self, "canvas"):
            return self.canvas.is_measurements_visible()
        return True

    def get_measurements(self) -> list[Measurement2D]:
        if hasattr(self, "canvas"):
            return self.canvas.get_measurements()
        return []

    def calc_2d_distance(self, p1: tuple[int, int], p2: tuple[int, int], orientation: str | None = None) -> float:
        """Calculates physical distance in mm between two pixel coordinates (col, row)
        using the appropriate physical spacing (dx, dy, dz) for the specified or current orientation.
        """
        orient = (orientation or self.current_orientation).lower()
        px1, py1 = p1
        px2, py2 = p2
        dy = float(self.pixel_spacing[0])
        dx = float(self.pixel_spacing[1])
        dz = float(self.slice_thickness)
        if orient == "axial":
            dx_mm = (px2 - px1) * dx
            dy_mm = (py2 - py1) * dy
            return float(np.sqrt(dx_mm ** 2 + dy_mm ** 2))
        elif orient == "coronal":
            dx_mm = (px2 - px1) * dx
            dz_mm = (py2 - py1) * dz
            return float(np.sqrt(dx_mm ** 2 + dz_mm ** 2))
        else:  # sagittal
            dy_mm = (px2 - px1) * dy
            dz_mm = (py2 - py1) * dz
            return float(np.sqrt(dy_mm ** 2 + dz_mm ** 2))

    def calc_physical_coords(self, orientation: str, slice_idx: int, u: float, v: float) -> tuple[float, float, float]:
        """Calculates 3D physical coordinates (X, Y, Z) in mm from slice orientation and (u, v)."""
        if self.vol_data is None or self.vol_data.ndim != 3:
            return (0.0, 0.0, 0.0)
        H, W, D = self.vol_data.shape
        dy = float(self.pixel_spacing[0])
        dx = float(self.pixel_spacing[1])
        dz = float(self.slice_thickness)
        orient = orientation.lower()
        if orient == "axial":
            z = slice_idx * dz
            x = u * (W - 1) * dx
            y = v * (H - 1) * dy
        elif orient == "coronal":
            y = slice_idx * dy
            x = u * (W - 1) * dx
            z = (1.0 - v) * (D - 1) * dz
        else:  # sagittal
            x = slice_idx * dx
            y = u * (H - 1) * dy
            z = (1.0 - v) * (D - 1) * dz
        return (float(x), float(y), float(z))

    def set_crosshair_visible(self, visible: bool):
        """Sets visibility of the in-plane crosshair overlay."""
        self.show_crosshair = bool(visible)
        if hasattr(self, "btn_crosshair_toggle"):
            self.btn_crosshair_toggle.blockSignals(True)
            self.btn_crosshair_toggle.setChecked(self.show_crosshair)
            self.btn_crosshair_toggle.setText("🎯 Crosshair: ON" if self.show_crosshair else "🎯 Crosshair: OFF")
            self.btn_crosshair_toggle.blockSignals(False)
        if hasattr(self, "canvas"):
            self.canvas.set_crosshair_visible(self.show_crosshair)

    def is_crosshair_visible(self) -> bool:
        """Returns True if the 2D crosshair is currently visible."""
        return getattr(self, "show_crosshair", True)

    def load_volume(
        self,
        vol_data: np.ndarray,
        pixel_spacing: tuple[float, float],
        slice_thickness: float
    ):
        """Reuses loaded CT volume directly without redundant DICOM reads."""
        self.vol_data = vol_data
        self.pixel_spacing = (float(pixel_spacing[0]), float(pixel_spacing[1]))
        self.slice_thickness = float(slice_thickness)

        if self.vol_data is not None and self.vol_data.ndim == 3:
            H, W, D = self.vol_data.shape
            self.slice_indices["axial"] = D // 2
            self.slice_indices["coronal"] = H // 2
            self.slice_indices["sagittal"] = W // 2
        else:
            self.slice_indices = {"axial": 0, "coronal": 0, "sagittal": 0}

        if hasattr(self, "canvas"):
            self.canvas.current_slice_idx = self.slice_indices.get(self.current_orientation, 0)
            self.canvas.cancel_pending_measurement()

        self._sync_slider_range()
        self._render_current_slice()

    def set_orientation(self, orientation: str):
        """Switches orientation ('axial', 'coronal', 'sagittal')."""
        orient = orientation.lower().strip()
        if orient not in ("axial", "coronal", "sagittal"):
            orient = "axial"

        self.current_orientation = orient

        # Sync button checks
        if hasattr(self, "btn_axial"):
            self.btn_axial.setChecked(orient == "axial")
            self.btn_coronal.setChecked(orient == "coronal")
            self.btn_sagittal.setChecked(orient == "sagittal")

        if hasattr(self, "canvas"):
            self.canvas.plane_name = orient
            self.canvas.current_slice_idx = self.slice_indices.get(orient, 0)
            self.canvas.cancel_pending_measurement()

        if self.vol_data is not None and self.vol_data.ndim == 3:
            H, W, D = self.vol_data.shape
            ix = self.slice_indices.get("sagittal", 0)
            iy = self.slice_indices.get("coronal", 0)
            iz = self.slice_indices.get("axial", 0)
            if orient == "axial":
                u = ix / max(1, W - 1)
                v = iy / max(1, H - 1)
            elif orient == "coronal":
                u = ix / max(1, W - 1)
                v = 1.0 - (iz / max(1, D - 1))
            else:  # sagittal
                u = iy / max(1, H - 1)
                v = 1.0 - (iz / max(1, D - 1))
            self.canvas.set_crosshairs(u, v)

        self._sync_slider_range()
        self._render_current_slice()
        self.orientation_changed.emit(orient)

    def get_slice_count(self) -> int:
        """Returns total slices available for the active orientation."""
        if self.vol_data is None or self.vol_data.ndim != 3:
            return 0
        H, W, D = self.vol_data.shape
        if self.current_orientation == "axial":
            return D
        elif self.current_orientation == "coronal":
            return H
        else:
            return W

    def get_current_slice_index(self) -> int:
        """Returns 0-based slice index for current orientation."""
        return self.slice_indices.get(self.current_orientation, 0)

    def _sync_slider_range(self):
        count = self.get_slice_count()
        if count <= 0:
            self.slider_slice.blockSignals(True)
            self.slider_slice.setRange(0, 0)
            self.slider_slice.setValue(0)
            self.slider_slice.blockSignals(False)
            self.lbl_slice_info.setText("0 / 0")
            return

        cur_idx = self.slice_indices.get(self.current_orientation, 0)
        cur_idx = int(np.clip(cur_idx, 0, count - 1))
        self.slice_indices[self.current_orientation] = cur_idx

        self.slider_slice.blockSignals(True)
        self.slider_slice.setRange(0, count - 1)
        self.slider_slice.setValue(cur_idx)
        self.slider_slice.blockSignals(False)

        self._update_slice_label()

    def _update_slice_label(self):
        count = self.get_slice_count()
        if count <= 0:
            self.lbl_slice_info.setText("0 / 0")
            return
        cur_idx = self.slice_indices.get(self.current_orientation, 0)

        # Calculate anatomical position
        if self.current_orientation == "axial":
            pos_mm = cur_idx * self.slice_thickness
            desc = f"Depth: {pos_mm:.1f}mm"
        elif self.current_orientation == "coronal":
            pos_mm = cur_idx * self.pixel_spacing[0]
            desc = f"Y: {pos_mm:.1f}mm"
        else:
            pos_mm = cur_idx * self.pixel_spacing[1]
            desc = f"X: {pos_mm:.1f}mm"

        self.lbl_slice_info.setText(f"{cur_idx + 1} / {count} ({desc})")

    def _on_slice_slider_changed(self, value: int):
        self.set_slice_index(value)

    def set_slice_index(self, index: int):
        """Sets the active slice index for the current orientation."""
        count = self.get_slice_count()
        if count <= 0:
            return
        idx = int(np.clip(index, 0, count - 1))
        if hasattr(self, "canvas"):
            self.canvas.current_slice_idx = idx
        if self.slice_indices[self.current_orientation] != idx:
            self.slice_indices[self.current_orientation] = idx
            if self.slider_slice.value() != idx:
                self.slider_slice.blockSignals(True)
                self.slider_slice.setValue(idx)
                self.slider_slice.blockSignals(False)
            self._update_slice_label()
            self._render_current_slice()
            self.slice_changed.emit(self.current_orientation, idx)
            pos = self.get_current_physical_position()
            self.reference_position_changed.emit(pos[0], pos[1], pos[2])

    def step_slice(self, delta: int):
        """Increment or decrement slice by delta."""
        cur = self.get_current_slice_index()
        self.set_slice_index(cur + delta)

    def go_to_slice(self, slice_num: int):
        """Go to 1-based slice number."""
        self.set_slice_index(slice_num - 1)

    def set_window_preset(self, preset_name: str):
        """Applies Window/Level preset (e.g. 'Bone', 'Soft Tissue', 'Lung')."""
        if preset_name in WL_PRESETS:
            self.cur_wl_preset = preset_name
            w, l = WL_PRESETS[preset_name][0], WL_PRESETS[preset_name][1]
            self.set_window_level(w, l)

            if hasattr(self, "btn_wl_bone"):
                self.btn_wl_bone.setChecked(preset_name == "Bone")
                self.btn_wl_soft.setChecked(preset_name == "Soft Tissue")
                self.btn_wl_lung.setChecked(preset_name == "Lung")

    def set_window_level(self, width: float, level: float):
        """Sets Window and Level and re-renders the current slice."""
        self.window_width = max(1.0, float(width))
        self.window_level = float(level)
        self._build_wl_lut()

        if hasattr(self, "lbl_wl_values"):
            self.lbl_wl_values.setText(f"W: {self.window_width:.0f}  L: {self.window_level:.0f}")

        if hasattr(self, "slider_window"):
            w_int = int(np.clip(round(self.window_width), 100, 3000))
            if self.slider_window.value() != w_int:
                self.slider_window.blockSignals(True)
                self.slider_window.setValue(w_int)
                self.slider_window.blockSignals(False)

        if hasattr(self, "slider_level"):
            l_int = int(np.clip(round(self.window_level), -1000, 1000))
            if self.slider_level.value() != l_int:
                self.slider_level.blockSignals(True)
                self.slider_level.setValue(l_int)
                self.slider_level.blockSignals(False)

        self._render_current_slice()
        self.window_level_changed.emit(self.window_width, self.window_level)

    def _on_window_slider_changed(self, val: int):
        self.window_width = float(val)
        self._build_wl_lut()
        self.lbl_wl_values.setText(f"W: {self.window_width:.0f}  L: {self.window_level:.0f}")
        self._render_current_slice()
        self.window_level_changed.emit(self.window_width, self.window_level)

    def _on_level_slider_changed(self, val: int):
        self.window_level = float(val)
        self._build_wl_lut()
        self.lbl_wl_values.setText(f"W: {self.window_width:.0f}  L: {self.window_level:.0f}")
        self._render_current_slice()
        self.window_level_changed.emit(self.window_width, self.window_level)

    def _extract_raw_slice(self) -> tuple[np.ndarray | None, float, float]:
        """Extracts 2D HU slice and physical millimeter spacings (mm_x, mm_y)."""
        if self.vol_data is None or self.vol_data.ndim != 3:
            return None, 1.0, 1.0

        H, W, D = self.vol_data.shape
        orient = self.current_orientation

        if orient == "axial":
            idx = int(np.clip(self.slice_indices["axial"], 0, D - 1))
            raw = self.vol_data[:, :, idx]
            mm_x = float(self.pixel_spacing[1])
            mm_y = float(self.pixel_spacing[0])
        elif orient == "coronal":
            idx = int(np.clip(self.slice_indices["coronal"], 0, H - 1))
            # Anatomical upright alignment: rows correspond to Z top-down
            raw = np.flipud(self.vol_data[idx, :, :].T)
            mm_x = float(self.pixel_spacing[1])
            mm_y = float(self.slice_thickness)
        else:  # sagittal
            idx = int(np.clip(self.slice_indices["sagittal"], 0, W - 1))
            raw = np.flipud(self.vol_data[:, idx, :].T)
            mm_x = float(self.pixel_spacing[0])
            mm_y = float(self.slice_thickness)

        return raw, mm_x, mm_y

    def _render_current_slice(self):
        """Converts calibrated HU slice into 8-bit QPixmap using pre-baked LUT."""
        raw, mm_x, mm_y = self._extract_raw_slice()
        if raw is None or self._wl_lut is None:
            self.canvas.set_slice_data(None, None, 1.0, 1.0, self.current_orientation)
            return

        # Map true HU [-1024..3071] to 8-bit display indices
        indices = np.clip(raw.astype(np.int32) + 1024, 0, 4095)
        arr_8bit = np.ascontiguousarray(self._wl_lut[indices], dtype=np.uint8)

        sh = arr_8bit.shape
        qimg = QImage(arr_8bit.data, sh[1], sh[0], sh[1], QImage.Format.Format_Grayscale8)
        pixmap = QPixmap.fromImage(qimg.copy())

        self.canvas.set_slice_data(pixmap, raw, mm_x, mm_y, self.current_orientation)

    def _on_canvas_hover(self, px: int, py: int, hu: float):
        """Updates cursor readout bar with pixel position and genuine HU value."""
        self.lbl_cursor_info.setText(f"Position: ({px}, {py})  ·  HU: {hu:+.0f}")

    def _on_canvas_crosshair(self, u: float, v: float):
        """Receives crosshair movements from canvas, updates internal slice indices and emits 3D coordinate."""
        if self.vol_data is None or self.vol_data.ndim != 3:
            return

        H, W, D = self.vol_data.shape
        orient = self.current_orientation
        if orient == "axial":
            self.slice_indices["sagittal"] = int(np.clip(round(u * (W - 1)), 0, W - 1))
            self.slice_indices["coronal"]  = int(np.clip(round(v * (H - 1)), 0, H - 1))
        elif orient == "coronal":
            self.slice_indices["sagittal"] = int(np.clip(round(u * (W - 1)), 0, W - 1))
            self.slice_indices["axial"]    = int(np.clip(round((1.0 - v) * (D - 1)), 0, D - 1))
        else:  # sagittal
            self.slice_indices["coronal"]  = int(np.clip(round(u * (H - 1)), 0, H - 1))
            self.slice_indices["axial"]    = int(np.clip(round((1.0 - v) * (D - 1)), 0, D - 1))

        pos = self.get_current_physical_position()
        self.reference_position_changed.emit(pos[0], pos[1], pos[2])

    def get_current_physical_position(self) -> tuple[float, float, float]:
        """Computes physical (X, Y, Z) in mm from the active slice index and crosshair position.
        Returns (x_mm, y_mm, z_mm).
        """
        if self.vol_data is None or self.vol_data.ndim != 3:
            return (0.0, 0.0, 0.0)

        H, W, D = self.vol_data.shape
        dy = float(self.pixel_spacing[0])
        dx = float(self.pixel_spacing[1])
        dz = float(self.slice_thickness)

        u = getattr(self.canvas, "cross_u", 0.5)
        v = getattr(self.canvas, "cross_v", 0.5)

        orient = self.current_orientation
        if orient == "axial":
            iz = self.slice_indices.get("axial", 0)
            z = iz * dz
            x = u * (W - 1) * dx
            y = v * (H - 1) * dy
        elif orient == "coronal":
            iy = self.slice_indices.get("coronal", 0)
            y = iy * dy
            x = u * (W - 1) * dx
            z = (1.0 - v) * (D - 1) * dz
        else:  # sagittal
            ix = self.slice_indices.get("sagittal", 0)
            x = ix * dx
            y = u * (H - 1) * dy
            z = (1.0 - v) * (D - 1) * dz

        return (float(x), float(y), float(z))

    def set_physical_position(
        self,
        x_mm: float,
        y_mm: float,
        z_mm: float,
        update_orientation_slice: bool = True
    ):
        """Snaps 2D slice index and in-plane crosshair to physical position (X, Y, Z) in mm."""
        if self.vol_data is None or self.vol_data.ndim != 3:
            return

        H, W, D = self.vol_data.shape
        dy = max(1e-4, float(self.pixel_spacing[0]))
        dx = max(1e-4, float(self.pixel_spacing[1]))
        dz = max(1e-4, float(self.slice_thickness))

        ix = int(np.clip(round(x_mm / dx), 0, W - 1))
        iy = int(np.clip(round(y_mm / dy), 0, H - 1))
        iz = int(np.clip(round(z_mm / dz), 0, D - 1))

        self.slice_indices["axial"] = iz
        self.slice_indices["coronal"] = iy
        self.slice_indices["sagittal"] = ix

        # Update crosshairs for current orientation
        orient = self.current_orientation
        if orient == "axial":
            u = ix / max(1, W - 1)
            v = iy / max(1, H - 1)
        elif orient == "coronal":
            u = ix / max(1, W - 1)
            v = 1.0 - (iz / max(1, D - 1))
        else:  # sagittal
            u = iy / max(1, H - 1)
            v = 1.0 - (iz / max(1, D - 1))

        self.canvas.set_crosshairs(u, v)

        if update_orientation_slice:
            cur_idx = self.slice_indices.get(orient, 0)
            if self.slider_slice.value() != cur_idx:
                self.slider_slice.blockSignals(True)
                self.slider_slice.setValue(cur_idx)
                self.slider_slice.blockSignals(False)
            self._update_slice_label()
            self._render_current_slice()

    def get_current_hu_at(self, px: int, py: int) -> float | None:
        """Query HU value at specific slice matrix coordinates."""
        raw, _, _ = self._extract_raw_slice()
        if raw is not None and 0 <= py < raw.shape[0] and 0 <= px < raw.shape[1]:
            return float(raw[py, px])
        return None
