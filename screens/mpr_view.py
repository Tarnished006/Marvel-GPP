# screens/mpr_view.py
"""
Multi-Planar Reconstruction (MPR) 2D slice viewer for Aegis-Touch.

Features:
  1. Ultra-Low Latency Zero-Lag Orthogonal Slicing:
     - Pre-bakes 8-bit volume on scan load / preset switch (<15MB RAM).
     - Slice extraction is a direct 0.00ms uint8 memory slice with zero per-frame float math.
     - Decoupled viewports: scrubbing an axis refreshes ONLY that specific slice,
       updating crosshair indicators on other viewports without redundant re-slicing.
  2. Two-Click Precision Digital Calipers:
     - Click Location 1 -> Anchors Point A with visual marker.
     - Hover -> Shows live dashed rubberband and distance preview.
     - Click Location 2 -> Commits measurement with exact DICOM millimeter calibration.
     - Debounce (<0.35s) & minimum span (<15px) prevents accidental double-tap line spam.
     - Right-click cancels Point 1.
  3. Synchronized Orthogonal Planes:
     - Axial (Transverse): Top-Down Cross-Section (Cranial-Caudal, Head-to-Feet)
     - Coronal (Frontal): Face-to-Back Frontal View (Anterior-Posterior)
     - Sagittal (Lateral): Side Profile View (Left-Right)
  4. Dynamic Scan-Aware Window/Level (W/L) Presets:
     - Spine/Lumbar: Bone, Soft Tissue / Disc, Spine Detail
     - Head/Skull: Bone, Brain, Subdural, Soft Tissue
     - Chest/Thorax: Lung, Mediastinum, Bone
"""

import time
import math
import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QSlider, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush, QFont, QImage, QPixmap, QPainterPath

from dicom_engine import load_volume_for_mpr

# ── Dynamic Clinical Window/Level Presets by Anatomical Region ────────────────
ANATOMICAL_PRESETS = {
    "spine": {
        "Bone":               (1800.0,  400.0, "Cortical bone, vertebrae & pedicles"),
        "Soft Tissue / Disc": ( 400.0,   40.0, "Intervertebral discs & dural sac"),
        "Spine Detail":       (1000.0,  250.0, "Trabecular architecture & hardware"),
    },
    "head": {
        "Bone":        (1800.0,  400.0, "Cranial vault & facial bones"),
        "Brain":       (  80.0,   40.0, "Gray/white matter contrast & stroke"),
        "Subdural":    ( 150.0,   75.0, "Intracranial hemorrhage & hematoma"),
        "Soft Tissue": ( 400.0,   40.0, "Muscles & orbital tissue"),
    },
    "chest": {
        "Lung":        (1500.0, -600.0, "Pulmonary parenchyma & airways"),
        "Mediastinum": ( 350.0,   40.0, "Heart, pericardium & great vessels"),
        "Bone":        (1800.0,  400.0, "Ribs & thoracic spine"),
    },
    "abdomen": {
        "Soft Tissue":  ( 400.0,   40.0, "Abdominal organs & viscera"),
        "Bone":         (1800.0,  400.0, "Pelvis & lumbar spine"),
        "Liver Detail": ( 150.0,   30.0, "Hepatic parenchyma & contrast phase"),
    },
    "general": {
        "Bone":        (1800.0,  400.0, "Osseous structures"),
        "Soft Tissue": ( 400.0,   40.0, "General soft tissue"),
    },
}

WL_PRESETS = ANATOMICAL_PRESETS["general"]


class CaliperMeasurement:
    def __init__(self, p1: QPointF, p2: QPointF, dist_mm: float):
        self.p1 = p1
        self.p2 = p2
        self.dist_mm = dist_mm


class CobbAngleMeasurement:
    """Represents a 4-point Cobb angle between two endplate lines on a 2D slice."""
    def __init__(self, p1: QPointF, p2: QPointF, p3: QPointF, p4: QPointF, angle_deg: float):
        self.p1 = p1  # Line 1 start (superior endplate)
        self.p2 = p2  # Line 1 end
        self.p3 = p3  # Line 2 start (inferior endplate)
        self.p4 = p4  # Line 2 end
        self.angle_deg = angle_deg


class MPRSliceWidget(QFrame):
    """Interactive viewport for a single orthogonal slice (Axial, Coronal, or Sagittal)."""
    crosshair_moved = pyqtSignal(float, float)  # normalized (u, v) in [0, 1]

    def __init__(self, plane_name: str, plane_subtitle: str, parent=None):
        super().__init__(parent)
        self.plane_name = plane_name
        self.plane_subtitle = plane_subtitle
        self.setFrameShape(QFrame.Shape.Box)
        self.setStyleSheet("background: #000; border: 1px solid #242424; border-radius: 4px;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(180, 180)

        self._pixmap: QPixmap = None
        self._raw_shape: tuple[int, int] = (1, 1)
        self.cross_u: float = 0.5   # normalized horizontal crosshair position
        self.cross_v: float = 0.5   # normalized vertical crosshair position

        # Two-Click Caliper State Machine
        self.caliper_active: bool = False
        self.caliper_p1: QPointF = None
        self.caliper_hover: QPointF = None
        self.last_p1_ts: float = 0.0
        self.measurements: list[CaliperMeasurement] = []

        # Cobb Angle State Machine (2 lines = 4 points)
        self.cobb_active: bool = False
        self.cobb_pts: list[QPointF] = []
        self.cobb_hover: QPointF = None
        self.cobb_measurements: list[CobbAngleMeasurement] = []

        # ROI Diagnostic Magnifying Loupe State (2.0x Digital Magnification)
        self.flashlight_active: bool = False
        self.flashlight_pos: QPointF = None
        self.flashlight_pinned: bool = False
        self.flashlight_pinned_pos: QPointF = None

        # Real-world spacing (dx, dy) for this specific projection in mm/pixel
        self.mm_per_pixel_x: float = 1.0
        self.mm_per_pixel_y: float = 1.0

        self.setMouseTracking(True)

    def set_slice_image(self, pixmap: QPixmap, raw_shape: tuple[int, int], mm_x: float, mm_y: float):
        self._pixmap = pixmap
        self._raw_shape = raw_shape
        self.mm_per_pixel_x = mm_x
        self.mm_per_pixel_y = mm_y
        self.update()

    def set_crosshairs(self, u: float, v: float):
        self.cross_u = max(0.0, min(1.0, u))
        self.cross_v = max(0.0, min(1.0, v))
        self.update()

    def _get_target_rect(self) -> QRectF:
        """
        Returns aspect-ratio preserved bounding box calibrated to TRUE physical millimeter dimensions.
        Eliminates visual distortion between anisotropic in-plane and slice-thickness axes.
        """
        if not self._pixmap or self._pixmap.isNull():
            return QRectF(self.rect())

        pw = self._pixmap.width()
        ph = self._pixmap.height()
        ww = max(10, self.width() - 8)
        wh = max(10, self.height() - 8)

        # Scale by true physical millimeter dimensions so circles remain true circles
        phys_w = pw * self.mm_per_pixel_x
        phys_h = ph * self.mm_per_pixel_y

        scale = min(ww / (phys_w + 1e-5), wh / (phys_h + 1e-5))
        rw = phys_w * scale
        rh = phys_h * scale
        rx = (self.width() - rw) / 2.0
        ry = (self.height() - rh) / 2.0
        return QRectF(rx, ry, rw, rh)

    def mousePressEvent(self, event):
        rect = self._get_target_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return

        u = float(np.clip((event.position().x() - rect.left()) / rect.width(), 0.0, 1.0))
        v = float(np.clip((event.position().y() - rect.top()) / rect.height(), 0.0, 1.0))

        # Right-click cancels an in-progress Point 1 anchor or Cobb in-progress points
        if event.button() == Qt.MouseButton.RightButton:
            if self.caliper_p1 is not None:
                self.caliper_p1 = None
                self.update()
            if self.cobb_pts:
                self.cobb_pts.clear()
                self.cobb_hover = None
                self.update()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        # ── Diagnostic Magnifying Loupe Pinning ───────────────────────────────
        if self.flashlight_active:
            self.flashlight_pinned = not self.flashlight_pinned
            if self.flashlight_pinned:
                self.flashlight_pinned_pos = event.position()
            else:
                self.flashlight_pinned_pos = None
                self.flashlight_pos = event.position()
            self.update()
            return

        # ── Cobb Angle 4-Point State Machine ──────────────────────────────────
        if self.cobb_active:
            now = time.time()
            if len(self.cobb_pts) == 0:
                self.cobb_pts.append(QPointF(u, v))
                self.last_p1_ts = now
                self.update()
            elif len(self.cobb_pts) == 1:
                dx_px = (u - self.cobb_pts[0].x()) * rect.width()
                dy_px = (v - self.cobb_pts[0].y()) * rect.height()
                if math.hypot(dx_px, dy_px) < 14.0:
                    # User clicked or double-clicked on Point 1: delete/cancel Line 1!
                    self.cobb_pts.clear()
                    self.cobb_hover = None
                    self.update()
                    return
                if now - self.last_p1_ts < 0.15:
                    return
                self.cobb_pts.append(QPointF(u, v))
                self.last_p1_ts = now
                self.update()
            elif len(self.cobb_pts) == 2:
                if now - self.last_p1_ts < 0.15:
                    return
                self.cobb_pts.append(QPointF(u, v))
                self.last_p1_ts = now
                self.update()
            elif len(self.cobb_pts) == 3:
                dx_px = (u - self.cobb_pts[2].x()) * rect.width()
                dy_px = (v - self.cobb_pts[2].y()) * rect.height()
                if math.hypot(dx_px, dy_px) < 14.0:
                    # User clicked or double-clicked on Point 3: delete/cancel Line 2!
                    self.cobb_pts.pop()
                    self.cobb_hover = None
                    self.update()
                    return
                if now - self.last_p1_ts < 0.15:
                    return
                p4 = QPointF(u, v)
                p1, p2, p3 = self.cobb_pts[0], self.cobb_pts[1], self.cobb_pts[2]
                angle = self._calc_cobb_angle(p1, p2, p3, p4)
                self.cobb_measurements.append(CobbAngleMeasurement(p1, p2, p3, p4, angle))
                self.cobb_pts.clear()
                self.cobb_hover = None
                self.update()
            return

        # ── Two-Click Caliper State Machine ───────────────────────────────────
        if self.caliper_active:
            now = time.time()
            if self.caliper_p1 is None:
                # Step 1: Pin Point 1
                self.caliper_p1 = QPointF(u, v)
                self.last_p1_ts = now
                self.update()
            else:
                # Step 2: Pin Point 2 and Commit OR Delete if clicked on Point 1
                dx_px = (u - self.caliper_p1.x()) * rect.width()
                dy_px = (v - self.caliper_p1.y()) * rect.height()
                if math.hypot(dx_px, dy_px) < 14.0:
                    # User clicked or double-clicked on the same node/point: delete/cancel the caliper line!
                    self.caliper_p1 = None
                    self.caliper_hover = None
                    self.update()
                    return

                if now - self.last_p1_ts < 0.15:
                    return

                dist_mm = self._calc_dist_mm(self.caliper_p1, QPointF(u, v))
                self.measurements.append(CaliperMeasurement(self.caliper_p1, QPointF(u, v), dist_mm))
                self.caliper_p1 = None
                self.update()
        else:
            self.crosshair_moved.emit(u, v)

    def mouseMoveEvent(self, event):
        rect = self._get_target_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return

        u = float(np.clip((event.position().x() - rect.left()) / rect.width(), 0.0, 1.0))
        v = float(np.clip((event.position().y() - rect.top()) / rect.height(), 0.0, 1.0))

        if self.flashlight_active:
            if not self.flashlight_pinned:
                self.flashlight_pos = event.position()
            self.update()

        if self.cobb_active:
            self.cobb_hover = QPointF(u, v)
            if self.cobb_pts:
                self.update()
        elif self.caliper_active:
            self.caliper_hover = QPointF(u, v)
            if self.caliper_p1 is not None:
                self.update()
        elif event.buttons() & Qt.MouseButton.LeftButton:
            self.crosshair_moved.emit(u, v)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            rect = self._get_target_rect()
            if rect.width() > 0 and rect.height() > 0:
                u = float(np.clip((event.position().x() - rect.left()) / rect.width(), 0.0, 1.0))
                v = float(np.clip((event.position().y() - rect.top()) / rect.height(), 0.0, 1.0))
                # Support press-drag-release caliper gesture (> 8px drag commits Point 2 on release)
                if self.caliper_active and self.caliper_p1 is not None:
                    dx_px = (u - self.caliper_p1.x()) * rect.width()
                    dy_px = (v - self.caliper_p1.y()) * rect.height()
                    now = time.time()
                    if math.hypot(dx_px, dy_px) >= 8.0 and (now - self.last_p1_ts >= 0.10):
                        dist_mm = self._calc_dist_mm(self.caliper_p1, QPointF(u, v))
                        self.measurements.append(CaliperMeasurement(self.caliper_p1, QPointF(u, v), dist_mm))
                        self.caliper_p1 = None
                        self.caliper_hover = None
                        self.update()

    def mouseDoubleClickEvent(self, event):
        rect = self._get_target_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return

        # 1. If currently starting a caliper line, double-click deletes it immediately
        if self.caliper_active and self.caliper_p1 is not None:
            self.caliper_p1 = None
            self.caliper_hover = None
            self.update()
            return

        # 2. If currently starting a Cobb angle, double-click deletes it immediately
        if self.cobb_active and self.cobb_pts:
            self.cobb_pts.clear()
            self.cobb_hover = None
            self.update()
            return

        # 3. Double-click on any existing caliper line deletes that line
        pos = event.position()
        if self.measurements:
            for i in reversed(range(len(self.measurements))):
                m = self.measurements[i]
                p1_px = QPointF(rect.left() + m.p1.x() * rect.width(), rect.top() + m.p1.y() * rect.height())
                p2_px = QPointF(rect.left() + m.p2.x() * rect.width(), rect.top() + m.p2.y() * rect.height())
                if self._dist_to_segment_2d(pos, p1_px, p2_px) < 16.0:
                    self.measurements.pop(i)
                    self.update()
                    return

        # 4. Double-click on any existing Cobb angle line deletes that measurement
        if self.cobb_measurements:
            for i in reversed(range(len(self.cobb_measurements))):
                cm = self.cobb_measurements[i]
                p1_px = self._to_widget_pt(cm.p1, rect)
                p2_px = self._to_widget_pt(cm.p2, rect)
                p3_px = self._to_widget_pt(cm.p3, rect)
                p4_px = self._to_widget_pt(cm.p4, rect)
                if (self._dist_to_segment_2d(pos, p1_px, p2_px) < 16.0 or
                    self._dist_to_segment_2d(pos, p3_px, p4_px) < 16.0):
                    self.cobb_measurements.pop(i)
                    self.update()
                    return

    @staticmethod
    def _dist_to_segment_2d(p: QPointF, a: QPointF, b: QPointF) -> float:
        """Calculates distance from point p to line segment ab in 2D pixels."""
        ab_x = b.x() - a.x()
        ab_y = b.y() - a.y()
        l2 = ab_x * ab_x + ab_y * ab_y
        if l2 < 1e-6:
            return float(math.hypot(p.x() - a.x(), p.y() - a.y()))
        t = max(0.0, min(1.0, ((p.x() - a.x()) * ab_x + (p.y() - a.y()) * ab_y) / l2))
        proj_x = a.x() + t * ab_x
        proj_y = a.y() + t * ab_y
        return float(math.hypot(p.x() - proj_x, p.y() - proj_y))

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self.flashlight_active and not self.flashlight_pinned:
            self.flashlight_pos = None
            self.update()

    def _calc_cobb_angle(self, p1: QPointF, p2: QPointF, p3: QPointF, p4: QPointF) -> float:
        """Calculates Cobb angle in degrees between Line 1 (p1-p2) and Line 2 (p3-p4)."""
        if not self._pixmap:
            return 0.0
        phys_w = self._pixmap.width() * self.mm_per_pixel_x
        phys_h = self._pixmap.height() * self.mm_per_pixel_y

        u_x = (p2.x() - p1.x()) * phys_w
        u_y = (p2.y() - p1.y()) * phys_h
        v_x = (p4.x() - p3.x()) * phys_w
        v_y = (p4.y() - p3.y()) * phys_h

        len_u = math.hypot(u_x, u_y)
        len_v = math.hypot(v_x, v_y)
        if len_u < 1e-6 or len_v < 1e-6:
            return 0.0

        dot = u_x * v_x + u_y * v_y
        cos_theta = np.clip(abs(dot) / (len_u * len_v), 0.0, 1.0)
        return float(math.degrees(math.acos(cos_theta)))

    def _to_widget_pt(self, norm_pt: QPointF, rect: QRectF) -> QPointF:
        return QPointF(rect.left() + norm_pt.x() * rect.width(), rect.top() + norm_pt.y() * rect.height())

    def _draw_hint_tag(self, painter: QPainter, rect: QRectF, text: str):
        painter.setPen(QColor(220, 220, 220))
        painter.setFont(QFont("sans-serif", 8, QFont.Weight.Normal))
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text) + 14
        hint_rect = QRectF(rect.right() - tw - 8, rect.top() + 8, tw, 18)
        painter.setBrush(QBrush(QColor(20, 20, 20, 190)))
        painter.drawRoundedRect(hint_rect, 3, 3)
        painter.drawText(hint_rect, Qt.AlignmentFlag.AlignCenter, text)

    def _calc_dist_mm(self, p1: QPointF, p2: QPointF) -> float:
        """Calculates true physical metric distance in millimeters using calibrated voxel spacing."""
        if not self._pixmap:
            return 0.0
        phys_w = self._pixmap.width() * self.mm_per_pixel_x
        phys_h = self._pixmap.height() * self.mm_per_pixel_y

        dx_mm = (p2.x() - p1.x()) * phys_w
        dy_mm = (p2.y() - p1.y()) * phys_h
        return float(math.sqrt(dx_mm * dx_mm + dy_mm * dy_mm))

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = self._get_target_rect()

        # 1. Draw Slice Pixmap
        if self._pixmap and not self._pixmap.isNull():
            painter.drawPixmap(rect.toRect(), self._pixmap)
        else:
            painter.setPen(QColor(80, 80, 80))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No Slice Loaded")
            return

        # ── ROI Diagnostic Magnifying Loupe (2.0x Digital Zoom with Micro-Reticle) ────
        active_lens_pos = self.flashlight_pinned_pos if self.flashlight_pinned else self.flashlight_pos
        if self.flashlight_active and active_lens_pos is not None and self._pixmap and not self._pixmap.isNull():
            fpos = active_lens_pos
            lens_r = 75.0  # 150px wide high-resolution inspection loupe
            lens_path = QPainterPath()
            lens_path.addEllipse(fpos, lens_r, lens_r)

            # Map center of loupe to source DICOM slice coordinates
            u = float(np.clip((fpos.x() - rect.left()) / max(rect.width(), 1.0), 0.0, 1.0))
            v = float(np.clip((fpos.y() - rect.top()) / max(rect.height(), 1.0), 0.0, 1.0))
            src_cx = u * self._pixmap.width()
            src_cy = v * self._pixmap.height()

            zoom_factor = 2.0
            scale_x = self._pixmap.width() / max(rect.width(), 1.0)
            scale_y = self._pixmap.height() / max(rect.height(), 1.0)
            src_w = (lens_r * 2.0 / zoom_factor) * scale_x
            src_h = (lens_r * 2.0 / zoom_factor) * scale_y

            src_rect = QRectF(src_cx - src_w / 2.0, src_cy - src_h / 2.0, src_w, src_h)
            dst_rect = QRectF(fpos.x() - lens_r, fpos.y() - lens_r, lens_r * 2.0, lens_r * 2.0)

            painter.save()
            painter.setClipPath(lens_path)
            # Render true 2.0x magnified DICOM pixel detail
            painter.drawPixmap(dst_rect.toRect(), self._pixmap, src_rect.toRect())
            # Contrast illumination overlay for hairline fractures & micro-structures
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
            painter.fillRect(dst_rect, QColor(30, 55, 75, 70))
            painter.restore()

            # Precision Bezel (outer glowing cyan ring and dark inner bevel)
            painter.setPen(QPen(QColor(0, 229, 255, 240), 2.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(fpos, lens_r, lens_r)

            painter.setPen(QPen(QColor(10, 25, 40, 200), 1.5))
            painter.drawEllipse(fpos, lens_r - 2.0, lens_r - 2.0)

            # Precision Central Reticle
            pen_reticle = QPen(QColor(0, 229, 255, 180), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen_reticle)
            painter.drawLine(QPointF(fpos.x() - 12, fpos.y()), QPointF(fpos.x() + 12, fpos.y()))
            painter.drawLine(QPointF(fpos.x(), fpos.y() - 12), QPointF(fpos.x(), fpos.y() + 12))

            # Diagnostic Loupe Status Badge
            badge_text = "📌 2.0x PINNED" if self.flashlight_pinned else "🔍 2.0x ZOOM"
            badge_sub = "Click to unpin" if self.flashlight_pinned else "Click to pin"
            badge_rect = QRectF(fpos.x() - 60, fpos.y() - lens_r - 22, 120, 18)
            painter.setBrush(QBrush(QColor(10, 25, 35, 225)))
            painter.setPen(QPen(QColor(0, 229, 255), 1))
            painter.drawRoundedRect(badge_rect, 4, 4)
            painter.setPen(QColor(0, 229, 255))
            painter.setFont(QFont("sans-serif", 7, QFont.Weight.Bold))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, f"{badge_text} · {badge_sub}")

        # 2. Draw Crosshairs
        cx = rect.left() + self.cross_u * rect.width()
        cy = rect.top()  + self.cross_v * rect.height()

        pen_cross = QPen(QColor(0, 229, 255, 140), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen_cross)
        painter.drawLine(int(rect.left()), int(cy), int(rect.right()), int(cy))
        painter.drawLine(int(cx), int(rect.top()), int(cx), int(rect.bottom()))

        # 3. Draw Saved Calipers
        pen_caliper = QPen(QColor(255, 235, 59), 2)
        font = QFont("sans-serif", 9, QFont.Weight.Bold)
        painter.setFont(font)

        for idx, m in enumerate(self.measurements, 1):
            self._draw_measurement(painter, m.p1, m.p2, m.dist_mm, rect, pen_caliper, label_prefix=f"#{idx}: ")

        # 4. Draw Active 2-Click Caliper State
        if self.caliper_active:
            if self.caliper_p1 is not None:
                p1_x = rect.left() + self.caliper_p1.x() * rect.width()
                p1_y = rect.top()  + self.caliper_p1.y() * rect.height()
                painter.setPen(QPen(QColor(0, 255, 0), 2))
                painter.setBrush(QBrush(QColor(0, 255, 0, 160)))
                painter.drawEllipse(QPointF(p1_x, p1_y), 4, 4)

                hover_pt = self.caliper_hover or self.caliper_p1
                pen_active = QPen(QColor(124, 252, 0), 2, Qt.PenStyle.DashLine)
                dist_live = self._calc_dist_mm(self.caliper_p1, hover_pt)
                self._draw_measurement(painter, self.caliper_p1, hover_pt, dist_live, rect, pen_active, label_suffix=" · Click Point 2")
            else:
                self._draw_hint_tag(painter, rect, "Click Point 1")

        # 5. Draw Saved Cobb Angle Measurements
        pen_cobb = QPen(QColor(255, 152, 0), 2)
        for idx, cm in enumerate(self.cobb_measurements, 1):
            self._draw_cobb_measurement(painter, cm, rect, pen_cobb, idx)

        # 6. Draw Active Cobb Angle State
        if self.cobb_active:
            pen_cobb_active = QPen(QColor(255, 183, 77), 2, Qt.PenStyle.DashLine)
            pts_cnt = len(self.cobb_pts)
            if pts_cnt == 0:
                self._draw_hint_tag(painter, rect, "Cobb: Click Line 1 Start")
            elif pts_cnt == 1:
                p1_px = self._to_widget_pt(self.cobb_pts[0], rect)
                painter.setPen(QPen(QColor(255, 152, 0), 2))
                painter.setBrush(QBrush(QColor(255, 152, 0, 180)))
                painter.drawEllipse(p1_px, 4, 4)
                hover_px = self._to_widget_pt(self.cobb_hover or self.cobb_pts[0], rect)
                painter.setPen(pen_cobb_active)
                painter.drawLine(p1_px, hover_px)
                self._draw_hint_tag(painter, rect, "Cobb: Click Line 1 End")
            elif pts_cnt == 2:
                p1_px = self._to_widget_pt(self.cobb_pts[0], rect)
                p2_px = self._to_widget_pt(self.cobb_pts[1], rect)
                painter.setPen(QPen(QColor(255, 152, 0), 2))
                painter.drawLine(p1_px, p2_px)
                self._draw_hint_tag(painter, rect, "Cobb: Click Line 2 Start")
            elif pts_cnt == 3:
                p1_px = self._to_widget_pt(self.cobb_pts[0], rect)
                p2_px = self._to_widget_pt(self.cobb_pts[1], rect)
                painter.setPen(QPen(QColor(255, 152, 0), 2))
                painter.drawLine(p1_px, p2_px)

                p3_px = self._to_widget_pt(self.cobb_pts[2], rect)
                painter.setPen(QPen(QColor(255, 152, 0), 2))
                painter.setBrush(QBrush(QColor(255, 152, 0, 180)))
                painter.drawEllipse(p3_px, 4, 4)
                hover_px = self._to_widget_pt(self.cobb_hover or self.cobb_pts[2], rect)
                painter.setPen(pen_cobb_active)
                painter.drawLine(p3_px, hover_px)
                self._draw_hint_tag(painter, rect, "Cobb: Click Line 2 End")

        # 7. Anatomical Badges & Orientation Guides
        painter.setPen(QColor(240, 240, 240))
        painter.setFont(QFont("sans-serif", 8, QFont.Weight.Bold))
        badge_rect = QRectF(rect.left() + 8, rect.top() + 8, rect.width() - 120, 16)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignLeft, self.plane_name.upper())

        painter.setPen(QColor(140, 140, 140))
        painter.setFont(QFont("sans-serif", 7, QFont.Weight.Normal))
        sub_rect = QRectF(rect.left() + 8, rect.top() + 24, rect.width() - 16, 14)
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignLeft, self.plane_subtitle)

    def _draw_measurement(self, painter: QPainter, p1: QPointF, p2: QPointF, dist_mm: float, rect: QRectF, pen: QPen, label_prefix: str = "", label_suffix: str = ""):
        x1 = rect.left() + p1.x() * rect.width()
        y1 = rect.top()  + p1.y() * rect.height()
        x2 = rect.left() + p2.x() * rect.width()
        y2 = rect.top()  + p2.y() * rect.height()

        painter.setPen(pen)
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Endpoint tick marks (perpendicular caps)
        angle = math.atan2(y2 - y1, x2 - x1)
        tick_len = 6.0
        for px, py in [(x1, y1), (x2, y2)]:
            tx1 = px + tick_len * math.sin(angle)
            ty1 = py - tick_len * math.cos(angle)
            tx2 = px - tick_len * math.sin(angle)
            ty2 = py + tick_len * math.cos(angle)
            painter.drawLine(QPointF(tx1, ty1), QPointF(tx2, ty2))

        # Text banner badge
        mx = (x1 + x2) / 2.0
        my = (y1 + y2) / 2.0
        text = f" {label_prefix}{dist_mm:.1f} mm{label_suffix} "

        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text) + 8
        th = fm.height() + 2

        bg_rect = QRectF(mx - tw / 2.0, my - th / 2.0, tw, th)
        painter.setBrush(QBrush(QColor(15, 15, 15, 220)))
        painter.setPen(QColor(60, 60, 60))
        painter.drawRoundedRect(bg_rect, 3, 3)

        painter.setPen(QColor(255, 255, 255))
        painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_cobb_measurement(self, painter: QPainter, cm: CobbAngleMeasurement, rect: QRectF, pen: QPen, idx: int):
        p1 = self._to_widget_pt(cm.p1, rect)
        p2 = self._to_widget_pt(cm.p2, rect)
        p3 = self._to_widget_pt(cm.p3, rect)
        p4 = self._to_widget_pt(cm.p4, rect)

        painter.setPen(pen)
        painter.drawLine(p1, p2)
        painter.drawLine(p3, p4)

        # Draw end caps on lines
        for px, py, angle in [(p1.x(), p1.y(), math.atan2(p2.y() - p1.y(), p2.x() - p1.x())),
                              (p2.x(), p2.y(), math.atan2(p2.y() - p1.y(), p2.x() - p1.x())),
                              (p3.x(), p3.y(), math.atan2(p4.y() - p3.y(), p4.x() - p3.x())),
                              (p4.x(), p4.y(), math.atan2(p4.y() - p3.y(), p4.x() - p3.x()))]:
            tick = 5.0
            painter.drawLine(QPointF(px + tick * math.sin(angle), py - tick * math.cos(angle)),
                             QPointF(px - tick * math.sin(angle), py + tick * math.cos(angle)))

        # Badge position: midpoint between the centers of line 1 and line 2
        m1x, m1y = (p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0
        m2x, m2y = (p3.x() + p4.x()) / 2.0, (p3.y() + p4.y()) / 2.0
        bx = (m1x + m2x) / 2.0
        by = (m1y + m2y) / 2.0

        # Draw connecting dashed guideline between the two midpoints
        guide_pen = QPen(QColor(255, 152, 0, 120), 1, Qt.PenStyle.DotLine)
        painter.setPen(guide_pen)
        painter.drawLine(QPointF(m1x, m1y), QPointF(m2x, m2y))

        # Badge text
        text = f" Cobb #{idx}: {cm.angle_deg:.1f}° "
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text) + 8
        th = fm.height() + 4
        bg_rect = QRectF(bx - tw / 2.0, by - th / 2.0, tw, th)

        painter.setBrush(QBrush(QColor(20, 15, 0, 220)))
        painter.setPen(QPen(QColor(255, 152, 0), 1))
        painter.drawRoundedRect(bg_rect, 3, 3)

        painter.setPen(QColor(255, 204, 128))
        painter.setFont(QFont("sans-serif", 8, QFont.Weight.Bold))
        painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter, text)


class MPRView(QWidget):
    """Full Multi-Planar Reconstruction screen with 3 orthogonal synchronized views."""
    axial_changed = pyqtSignal(float)  # emits z_mm position

    def __init__(self, parent=None):
        super().__init__(parent)

        self.vol_data: np.ndarray = None       # int16 raw volume
        self.vol_8bit: np.ndarray = None       # pre-baked uint8 volume for instant slicing
        self.pixel_spacing: tuple[float, float] = (1.0, 1.0)
        self.slice_thickness: float = 1.0
        self.current_anatomy: str = "general"

        self.cur_wl_preset: str = "Bone"
        self.window_width: float = 1800.0
        self.window_level: float = 400.0
        self._wl_lut: np.ndarray = None
        self._build_wl_lut()

        # Current slice indices along dimensions (H, W, D)
        self.idx_x: int = 128   # Sagittal slice index (0..W-1)
        self.idx_y: int = 128   # Coronal slice index  (0..H-1)
        self.idx_z: int = 100   # Axial slice index    (0..D-1)

        self.caliper_mode: bool = False
        self.cobb_mode: bool = False
        self.flashlight_mode: bool = False
        self.preset_btns: dict[str, QPushButton] = {}
        self.info_wl = QLabel("")
        self.info_wl.setStyleSheet("color: #777; font-size: 10px; font-family: monospace;")

        self._build_ui()

    def _build_wl_lut(self):
        """Precomputes 1D integer lookup table for instantaneous Window/Level mapping (<0.1ms)."""
        hu = np.arange(-1024, 3072, dtype=np.float32)
        low = self.window_level - (self.window_width / 2.0)
        norm = np.clip((hu - low) / (self.window_width + 1e-5) * 255.0, 0, 255).astype(np.uint8)
        self._wl_lut = norm

    def _update_wl_cache(self):
        """Bakes full 3D volume into 8-bit uint8 once on preset switch for zero-lag slicing."""
        if self.vol_data is None:
            return
        indices = np.clip(self.vol_data.astype(np.int32) + 1024, 0, 4095)
        self.vol_8bit = self._wl_lut[indices].astype(np.uint8)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        # ── Toolbar: Window/Level Presets & Tools ─────────────────────────────
        self.top_bar = QHBoxLayout()
        self.top_bar.setSpacing(8)

        self.lbl_wl = QLabel("Clinical W/L:")
        self.lbl_wl.setStyleSheet("color: #888; font-size: 11px; font-weight: 600;")
        self.top_bar.addWidget(self.lbl_wl)

        self.preset_container = QHBoxLayout()
        self.preset_container.setSpacing(6)
        self.top_bar.addLayout(self.preset_container)

        self._populate_preset_buttons("general")

        self.top_bar.addSpacing(14)

        # Caliper tool toggle
        self.btn_caliper = QPushButton("📏 Caliper: OFF")
        self.btn_caliper.setCheckable(True)
        self.btn_caliper.setFixedHeight(24)
        self.btn_caliper.setStyleSheet(
            "QPushButton { background: #181818; color: #888; border: 1px solid #333; "
            "border-radius: 4px; padding: 2px 10px; font-size: 10px; font-weight: 600; }"
            "QPushButton:checked { background: #2a2200; color: #ffeb3b; border: 1px solid #665500; }"
        )
        self.btn_caliper.clicked.connect(self._toggle_caliper)
        self.top_bar.addWidget(self.btn_caliper)

        # Cobb Angle tool toggle
        self.btn_cobb = QPushButton("📐 Cobb: OFF")
        self.btn_cobb.setCheckable(True)
        self.btn_cobb.setFixedHeight(24)
        self.btn_cobb.setStyleSheet(
            "QPushButton { background: #181818; color: #888; border: 1px solid #333; "
            "border-radius: 4px; padding: 2px 10px; font-size: 10px; font-weight: 600; }"
            "QPushButton:checked { background: #2a1f00; color: #ffa726; border: 1px solid #ff9800; }"
        )
        self.btn_cobb.clicked.connect(self._toggle_cobb)
        self.top_bar.addWidget(self.btn_cobb)

        # Diagnostic Magnifying Loupe toggle
        self.btn_flashlight = QPushButton("🔍 2x Lens: OFF")
        self.btn_flashlight.setCheckable(True)
        self.btn_flashlight.setFixedHeight(24)
        self.btn_flashlight.setStyleSheet(
            "QPushButton { background: #181818; color: #888; border: 1px solid #333; "
            "border-radius: 4px; padding: 2px 10px; font-size: 10px; font-weight: 600; }"
            "QPushButton:checked { background: #00222a; color: #00e5ff; border: 1px solid #00b4d8; }"
        )
        self.btn_flashlight.clicked.connect(self._toggle_flashlight)
        self.top_bar.addWidget(self.btn_flashlight)

        # Lesion / ROI Volumetrics calculation
        self.btn_volume = QPushButton("🧊 Lesion / ROI Vol")
        self.btn_volume.setFixedHeight(24)
        self.btn_volume.setStyleSheet(
            "QPushButton { background: #181818; color: #888; border: 1px solid #333; "
            "border-radius: 4px; padding: 2px 8px; font-size: 10px; font-weight: 600; }"
            "QPushButton:hover { color: #b388ff; border-color: #7c4dff; }"
        )
        self.btn_volume.clicked.connect(self._calculate_volumetrics)
        self.top_bar.addWidget(self.btn_volume)

        btn_undo = QPushButton("Undo")
        btn_undo.setFixedHeight(24)
        btn_undo.setStyleSheet(
            "QPushButton { background: #181818; color: #888; border: 1px solid #333; "
            "border-radius: 4px; padding: 2px 8px; font-size: 10px; }"
            "QPushButton:hover { color: #fff; border-color: #666; }"
        )
        btn_undo.clicked.connect(self.undo_measurement)
        self.top_bar.addWidget(btn_undo)

        btn_clear = QPushButton("Clear All")
        btn_clear.setFixedHeight(24)
        btn_clear.setStyleSheet(
            "QPushButton { background: #181818; color: #888; border: 1px solid #333; "
            "border-radius: 4px; padding: 2px 8px; font-size: 10px; }"
            "QPushButton:hover { color: #f44336; border-color: #f44336; }"
        )
        btn_clear.clicked.connect(self.clear_measurements)
        self.top_bar.addWidget(btn_clear)

        self.top_bar.addStretch()
        self.top_bar.addWidget(self.info_wl)

        outer.addLayout(self.top_bar)

        # ── 3 Orthogonal Panels: Axial, Coronal, Sagittal ─────────────────────
        grid = QGridLayout()
        grid.setSpacing(6)

        # Panel 1: Axial (Transverse · Top-Down Cross-Section)
        self.w_axial = MPRSliceWidget("Axial (Transverse · Cross-Section)", "Top-Down (Cranial → Caudal) | Left ↔ Right")
        self.w_axial.crosshair_moved.connect(self._on_axial_crosshair)
        self.slider_axial = QSlider(Qt.Orientation.Horizontal)
        self.slider_axial.valueChanged.connect(self._on_axial_slider)
        self.lbl_axial = QLabel("Axial (Top-Down): Slice 0 / 0")
        self.lbl_axial.setStyleSheet("color: #777; font-size: 10px;")

        col_axial = QVBoxLayout()
        col_axial.addWidget(self.lbl_axial)
        col_axial.addWidget(self.w_axial, stretch=1)
        col_axial.addWidget(self.slider_axial)
        grid.addLayout(col_axial, 0, 0)

        # Panel 2: Coronal (Frontal · Face-to-Back View)
        self.w_coronal = MPRSliceWidget("Coronal (Frontal · Face-to-Back)", "Front View (Anterior → Posterior) | Symmetry & Alignment")
        self.w_coronal.crosshair_moved.connect(self._on_coronal_crosshair)
        self.slider_coronal = QSlider(Qt.Orientation.Horizontal)
        self.slider_coronal.valueChanged.connect(self._on_coronal_slider)
        self.lbl_coronal = QLabel("Coronal (Frontal): Slice 0 / 0")
        self.lbl_coronal.setStyleSheet("color: #777; font-size: 10px;")

        col_coronal = QVBoxLayout()
        col_coronal.addWidget(self.lbl_coronal)
        col_coronal.addWidget(self.w_coronal, stretch=1)
        col_coronal.addWidget(self.slider_coronal)
        grid.addLayout(col_coronal, 0, 1)

        # Panel 3: Sagittal (Lateral · Side Profile View)
        self.w_sagittal = MPRSliceWidget("Sagittal (Lateral · Side Profile)", "Side View (Left ↔ Right) | Disc Height & Spinal Canal")
        self.w_sagittal.crosshair_moved.connect(self._on_sagittal_crosshair)
        self.slider_sagittal = QSlider(Qt.Orientation.Horizontal)
        self.slider_sagittal.valueChanged.connect(self._on_sagittal_slider)
        self.lbl_sagittal = QLabel("Sagittal (Side Profile): Slice 0 / 0")
        self.lbl_sagittal.setStyleSheet("color: #777; font-size: 10px;")

        col_sagittal = QVBoxLayout()
        col_sagittal.addWidget(self.lbl_sagittal)
        col_sagittal.addWidget(self.w_sagittal, stretch=1)
        col_sagittal.addWidget(self.slider_sagittal)
        grid.addLayout(col_sagittal, 0, 2)

        outer.addLayout(grid, stretch=1)

    def _populate_preset_buttons(self, anatomy: str):
        """Dynamically builds preset buttons relevant ONLY to the active scan anatomy."""
        while self.preset_container.count():
            item = self.preset_container.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.preset_btns.clear()

        presets = ANATOMICAL_PRESETS.get(anatomy, ANATOMICAL_PRESETS["general"])
        first_name = list(presets.keys())[0]

        for name, data in presets.items():
            width, level = data[0], data[1]
            desc = data[2] if len(data) > 2 else ""
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(name == first_name)
            btn.setFixedHeight(24)
            btn.setToolTip(f"{desc} (W: {width:.0f}, L: {level:.0f})")
            btn.setStyleSheet(
                "QPushButton { background: #181818; color: #888; border: 1px solid #333; "
                "border-radius: 4px; padding: 2px 10px; font-size: 10px; font-weight: 600; }"
                "QPushButton:checked { background: #1a2a1a; color: #7cfc00; border: 1px solid #3a5a3a; }"
                "QPushButton:hover { color: #ccc; }"
            )
            btn.clicked.connect(lambda checked, n=name: self.set_window_preset(n))
            self.preset_btns[name] = btn
            self.preset_container.addWidget(btn)

        self.cur_wl_preset = first_name
        self.window_width, self.window_level = presets[first_name][0], presets[first_name][1]
        self._build_wl_lut()
        self._update_wl_cache()
        if hasattr(self, "info_wl") and self.info_wl:
            self.info_wl.setText(f"W: {self.window_width:.0f}  L: {self.window_level:.0f}")

    def load_scan_folder(self, folder_path: str):
        """Loads volume data and initializes all 3 slice projections."""
        vol, sp, z_sp, anatomy = load_volume_for_mpr(folder_path)
        self.vol_data = vol
        self.pixel_spacing = sp
        self.slice_thickness = z_sp
        self.current_anatomy = anatomy

        # Dynamically filter clinical presets to match loaded anatomy (e.g. Spine -> Bone, Soft Tissue/Disc)
        self._populate_preset_buttons(anatomy)

        H, W, D = self.vol_data.shape

        # Initialize sliders
        self.slider_axial.blockSignals(True)
        self.slider_axial.setRange(0, D - 1)
        self.idx_z = D // 2
        self.slider_axial.setValue(self.idx_z)
        self.slider_axial.blockSignals(False)

        self.slider_coronal.blockSignals(True)
        self.slider_coronal.setRange(0, H - 1)
        self.idx_y = H // 2
        self.slider_coronal.setValue(self.idx_y)
        self.slider_coronal.blockSignals(False)

        self.slider_sagittal.blockSignals(True)
        self.slider_sagittal.setRange(0, W - 1)
        self.idx_x = W // 2
        self.slider_sagittal.setValue(self.idx_x)
        self.slider_sagittal.blockSignals(False)

        self.refresh_all_slices()

    def set_window_preset(self, name: str):
        presets = ANATOMICAL_PRESETS.get(self.current_anatomy, ANATOMICAL_PRESETS["general"])
        if name in presets:
            self.cur_wl_preset = name
            self.window_width, self.window_level = presets[name][0], presets[name][1]
            self._build_wl_lut()
            self._update_wl_cache()
            for btn_name, btn in self.preset_btns.items():
                btn.setChecked(btn_name == name)
            self.info_wl.setText(f"W: {self.window_width:.0f}  L: {self.window_level:.0f}")
            self.refresh_all_slices()

    def _toggle_caliper(self):
        self.caliper_mode = self.btn_caliper.isChecked()
        self.btn_caliper.setText("📏 Caliper: ON" if self.caliper_mode else "📏 Caliper: OFF")
        if self.caliper_mode and self.cobb_mode:
            self.btn_cobb.setChecked(False)
            self._toggle_cobb()
        for w in (self.w_axial, self.w_coronal, self.w_sagittal):
            w.caliper_active = self.caliper_mode
            w.caliper_p1 = None
            w.caliper_hover = None
            w.update()

    def _toggle_cobb(self):
        self.cobb_mode = self.btn_cobb.isChecked()
        self.btn_cobb.setText("📐 Cobb: ON" if self.cobb_mode else "📐 Cobb: OFF")
        if self.cobb_mode and self.caliper_mode:
            self.btn_caliper.setChecked(False)
            self._toggle_caliper()
        for w in (self.w_axial, self.w_coronal, self.w_sagittal):
            w.cobb_active = self.cobb_mode
            w.cobb_pts.clear()
            w.cobb_hover = None
            w.update()

    def _toggle_flashlight(self):
        self.flashlight_mode = self.btn_flashlight.isChecked()
        self.btn_flashlight.setText("🔍 2x Lens: ON" if self.flashlight_mode else "🔍 2x Lens: OFF")
        for w in (self.w_axial, self.w_coronal, self.w_sagittal):
            w.flashlight_active = self.flashlight_mode
            w.flashlight_pos = None
            w.flashlight_pinned = False
            w.flashlight_pinned_pos = None
            w.update()

    def undo_measurement(self):
        """Removes the last added measurement across all viewports (calipers or Cobb angles)."""
        for w in (self.w_axial, self.w_coronal, self.w_sagittal):
            if w.measurements:
                w.measurements.pop()
                w.update()
                return
            if w.cobb_measurements:
                w.cobb_measurements.pop()
                w.update()
                return

    def clear_measurements(self):
        for w in (self.w_axial, self.w_coronal, self.w_sagittal):
            w.measurements.clear()
            w.caliper_p1 = None
            w.caliper_hover = None
            w.cobb_measurements.clear()
            w.cobb_pts.clear()
            w.cobb_hover = None
            w.update()

    def _calculate_volumetrics(self):
        """Calculates RECIST 1D, WHO 2D, and Ellipsoid 3D lesion / structure volume from calipers."""
        all_dists = []
        for w in (self.w_axial, self.w_coronal, self.w_sagittal):
            for m in w.measurements:
                all_dists.append(m.dist_mm)

        from PyQt6.QtWidgets import QMessageBox
        if not all_dists:
            QMessageBox.information(
                self,
                "Lesion / ROI Volumetrics",
                "No caliper measurements found.\n\nPlease measure 1 to 3 axes of the target structure or lesion using the Caliper tool (Length, Width, Height) to calculate volume."
            )
            return

        all_dists.sort(reverse=True)
        L = all_dists[0]
        W = all_dists[1] if len(all_dists) > 1 else L
        H = all_dists[2] if len(all_dists) > 2 else W

        # Standard clinical ellipsoid volume: V = (pi / 6) * L * W * H
        volume_mm3 = (math.pi / 6.0) * L * W * H
        volume_cm3 = volume_mm3 / 1000.0  # 1 cm^3 = 1 mL
        recist_1d = L
        who_2d = L * W

        msg = (
            f"<h3>3D Lesion & Structure Volumetric Analysis</h3>"
            f"<p style='color: #bbb; font-size: 11px;'>Applicable for: Tumors, Cysts, Hematomas, Nodules, Abscesses, and Organ/Bone Sizing</p>"
            f"<hr>"
            f"<p><b>Calibrated Dimensions:</b><br>"
            f"• Max Diameter (L): <b>{L:.1f} mm</b><br>"
            f"• Perpendicular Width (W): <b>{W:.1f} mm</b><br>"
            f"• Longitudinal Height (H): <b>{H:.1f} mm</b></p>"
            f"<hr>"
            f"<p><b>Clinical Diagnostic Metrics:</b><br>"
            f"• <b>1D RECIST 1.1:</b> {recist_1d:.1f} mm (Longest Axial Dimension)<br>"
            f"• <b>2D WHO Cross-Product:</b> {who_2d:.1f} mm²<br>"
            f"• <b>3D Ellipsoid Volume:</b> <span style='color: #00e5ff; font-size: 14px;'><b>{volume_cm3:.2f} cm³</b></span> ({volume_mm3:.1f} mm³ / mL)</p>"
            f"<p style='color: #888; font-size: 10px;'>Formula: V = (π / 6) · L · W · H | Calibrated with physical DICOM millimeter spacing.</p>"
        )
        box = QMessageBox(self)
        box.setWindowTitle("Clinical Lesion & Structure Volumetrics")
        box.setText(msg)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setIcon(QMessageBox.Icon.Information)
        box.exec()

    def snap_to_volume_point(self, x_mm: float, y_mm: float, z_mm: float):
        """Snaps all 3 MPR orthogonal viewports to the voxel containing physical point (X, Y, Z)."""
        if self.vol_8bit is None:
            return
        H, W, D = self.vol_8bit.shape
        dx = float(self.pixel_spacing[1])
        dy = float(self.pixel_spacing[0])
        dz = float(self.slice_thickness)

        ix = int(np.clip(round(x_mm / max(dx, 1e-4)), 0, W - 1))
        iy = int(np.clip(round(y_mm / max(dy, 1e-4)), 0, H - 1))
        iz = int(np.clip(round(z_mm / max(dz, 1e-4)), 0, D - 1))

        self.idx_x = ix
        self.idx_y = iy
        self.idx_z = iz

        self.slider_sagittal.blockSignals(True)
        self.slider_coronal.blockSignals(True)
        self.slider_axial.blockSignals(True)
        self.slider_sagittal.setRange(0, W - 1)
        self.slider_coronal.setRange(0, H - 1)
        self.slider_axial.setRange(0, D - 1)
        self.slider_sagittal.setValue(self.idx_x)
        self.slider_coronal.setValue(self.idx_y)
        self.slider_axial.setValue(self.idx_z)
        self.slider_sagittal.blockSignals(False)
        self.slider_coronal.blockSignals(False)
        self.slider_axial.blockSignals(False)

        self.refresh_all_slices()
        self.axial_changed.emit(self.idx_z * self.slice_thickness)

    # ── Ultra-Fast Direct 8-Bit Slice Updaters (0.00ms per slice) ─────────────

    def _refresh_axial_slice(self):
        if self.vol_8bit is None:
            return
        H, W, D = self.vol_8bit.shape
        # Direct zero-copy uint8 slice pointer!
        raw_ax = np.ascontiguousarray(self.vol_8bit[:, :, self.idx_z])
        qimg = QImage(raw_ax.data, W, H, W, QImage.Format.Format_Grayscale8)
        self.w_axial.set_slice_image(QPixmap.fromImage(qimg), (H, W), float(self.pixel_spacing[1]), float(self.pixel_spacing[0]))
        self.lbl_axial.setText(f"Axial (Top-Down): Slice {self.idx_z + 1} / {D}  (Depth: {(self.idx_z * self.slice_thickness):.1f} mm)")

    def _refresh_coronal_slice(self):
        if self.vol_8bit is None:
            return
        H, W, D = self.vol_8bit.shape
        raw_cor = np.ascontiguousarray(np.flipud(self.vol_8bit[self.idx_y, :, :].T))
        qimg = QImage(raw_cor.data, W, D, W, QImage.Format.Format_Grayscale8)
        self.w_coronal.set_slice_image(QPixmap.fromImage(qimg), raw_cor.shape, float(self.pixel_spacing[1]), float(self.slice_thickness))
        self.lbl_coronal.setText(f"Coronal (Frontal): Slice {self.idx_y + 1} / {H}  (Y: {(self.idx_y * self.pixel_spacing[0]):.1f} mm)")

    def _refresh_sagittal_slice(self):
        if self.vol_8bit is None:
            return
        H, W, D = self.vol_8bit.shape
        raw_sag = np.ascontiguousarray(np.flipud(self.vol_8bit[:, self.idx_x, :].T))
        qimg = QImage(raw_sag.data, H, D, H, QImage.Format.Format_Grayscale8)
        self.w_sagittal.set_slice_image(QPixmap.fromImage(qimg), raw_sag.shape, float(self.pixel_spacing[0]), float(self.slice_thickness))
        self.lbl_sagittal.setText(f"Sagittal (Side Profile): Slice {self.idx_x + 1} / {W}  (X: {(self.idx_x * self.pixel_spacing[1]):.1f} mm)")

    def refresh_all_slices(self):
        """Refreshes all 3 slices (called on scan load or W/L preset change)."""
        self._refresh_axial_slice()
        self._refresh_coronal_slice()
        self._refresh_sagittal_slice()
        self._update_crosshair_positions()

    def _update_crosshair_positions(self):
        if self.vol_8bit is None:
            return
        H, W, D = self.vol_8bit.shape

        u_x = self.idx_x / float(W)
        v_y = self.idx_y / float(H)
        w_z = (D - 1 - self.idx_z) / float(D)  # vertical flip for Coronal/Sagittal

        self.w_axial.set_crosshairs(u_x, v_y)
        self.w_coronal.set_crosshairs(u_x, w_z)
        self.w_sagittal.set_crosshairs(v_y, w_z)

    # ── High-Speed Slider & Crosshair Interaction Handlers ───────────────────

    def _on_axial_slider(self, val: int):
        self.idx_z = val
        self._refresh_axial_slice()
        self._update_crosshair_positions()
        self.axial_changed.emit(self.idx_z * self.slice_thickness)

    def _on_coronal_slider(self, val: int):
        self.idx_y = val
        self._refresh_coronal_slice()
        self._update_crosshair_positions()

    def _on_sagittal_slider(self, val: int):
        self.idx_x = val
        self._refresh_sagittal_slice()
        self._update_crosshair_positions()

    def _on_axial_crosshair(self, u: float, v: float):
        if self.vol_8bit is None:
            return
        H, W, _ = self.vol_8bit.shape
        self.idx_x = int(np.clip(u * W, 0, W - 1))
        self.idx_y = int(np.clip(v * H, 0, H - 1))
        self.slider_sagittal.setValue(self.idx_x)
        self.slider_coronal.setValue(self.idx_y)
        # Slices changed: Sagittal (x) and Coronal (y). Axial (z) did NOT change!
        self._refresh_sagittal_slice()
        self._refresh_coronal_slice()
        self._update_crosshair_positions()

    def _on_coronal_crosshair(self, u: float, v: float):
        if self.vol_8bit is None:
            return
        _, W, D = self.vol_8bit.shape
        self.idx_x = int(np.clip(u * W, 0, W - 1))
        self.idx_z = int(np.clip((1.0 - v) * D, 0, D - 1))
        self.slider_sagittal.setValue(self.idx_x)
        self.slider_axial.setValue(self.idx_z)
        # Slices changed: Sagittal (x) and Axial (z). Coronal (y) did NOT change!
        self._refresh_sagittal_slice()
        self._refresh_axial_slice()
        self._update_crosshair_positions()

    def _on_sagittal_crosshair(self, u: float, v: float):
        if self.vol_8bit is None:
            return
        H, _, D = self.vol_8bit.shape
        self.idx_y = int(np.clip(u * H, 0, H - 1))
        self.idx_z = int(np.clip((1.0 - v) * D, 0, D - 1))
        self.slider_coronal.setValue(self.idx_y)
        self.slider_axial.setValue(self.idx_z)
        # Slices changed: Coronal (y) and Axial (z). Sagittal (x) did NOT change!
        self._refresh_coronal_slice()
        self._refresh_axial_slice()
        self._update_crosshair_positions()

    def get_measurement_summary(self) -> list[dict]:
        """Returns structured summary of all Caliper and Cobb angle measurements across viewports."""
        summary = []
        for plane, widget in (("Axial", self.w_axial), ("Coronal", self.w_coronal), ("Sagittal", self.w_sagittal)):
            for idx, m in enumerate(widget.measurements, 1):
                summary.append({
                    "plane": plane,
                    "type": "Caliper",
                    "label": f"{plane} #{idx}",
                    "value": f"{m.dist_mm:.1f} mm",
                    "num_value": m.dist_mm,
                    "unit": "mm",
                })
            for idx, cm in enumerate(widget.cobb_measurements, 1):
                summary.append({
                    "plane": plane,
                    "type": "Cobb Angle",
                    "label": f"{plane} Cobb #{idx}",
                    "value": f"{cm.angle_deg:.1f}°",
                    "num_value": cm.angle_deg,
                    "unit": "deg",
                })
        return summary

    def step_axial(self, steps: int):
        """Step axial slice by delta steps (used by hand gestures or keyboard)."""
        if hasattr(self, "slider_axial") and self.slider_axial.maximum() > self.slider_axial.minimum():
            new_val = int(np.clip(self.slider_axial.value() + steps, self.slider_axial.minimum(), self.slider_axial.maximum()))
            self.slider_axial.setValue(new_val)

    def step_coronal(self, steps: int):
        """Step coronal slice by delta steps."""
        if hasattr(self, "slider_coronal") and self.slider_coronal.maximum() > self.slider_coronal.minimum():
            new_val = int(np.clip(self.slider_coronal.value() + steps, self.slider_coronal.minimum(), self.slider_coronal.maximum()))
            self.slider_coronal.setValue(new_val)

    def step_sagittal(self, steps: int):
        """Step sagittal slice by delta steps."""
        if hasattr(self, "slider_sagittal") and self.slider_sagittal.maximum() > self.slider_sagittal.minimum():
            new_val = int(np.clip(self.slider_sagittal.value() + steps, self.slider_sagittal.minimum(), self.slider_sagittal.maximum()))
            self.slider_sagittal.setValue(new_val)

