# screens/or_icu_mode.py
"""
ICU / OR Workspace for Aegis-Touch.

Serves as the dedicated clinical workflow shell for:
  - ICU Mode: Longitudinal imaging review, same-location tracking, device verification, handoffs.
  - OR Mode: Surgical entry/target planning, corridors, structure avoidance, virtual instrument.

Architecture:
  - Receives the active patient and scan directly from MainWindow / Viewer3D.
  - Does NOT instantiate a duplicate VTK renderer or reload DICOM volumes independently.
  - Connects back to Viewer3D via open_in_viewer_requested signal.
  - Displays real patient/scan data and real clinical notes from SQLite (zero fake data).
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QPushButton, QFrame, QStackedWidget, QScrollArea,
    QSizePolicy, QSlider, QInputDialog, QDialog, QListWidget,
    QListWidgetItem, QComboBox, QTextEdit
)

from PyQt6.QtCore import Qt, pyqtSignal
from typing import Optional, List, Dict, Any
from database import (
    get_patients_for_ui, get_scans_for_ui, get_notes_for_patient,
    get_previous_scan_for_patient, get_historical_scans_for_patient
)
from before_after import BeforeAfterComparison


class PatientSelectCard(QFrame):
    def __init__(self, patient: dict, on_select):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                background: #161616;
                border: 1px solid #282828;
                border-radius: 6px;
                padding: 8px;
            }
            QFrame:hover {
                background: #1f1f1f;
                border-color: #00e5ff;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        
        name_lbl = QLabel(f"<b>{patient.get('name', 'Unknown')}</b>")
        name_lbl.setStyleSheet("color: #ffffff; font-size: 13px;")
        layout.addWidget(name_lbl)
        
        mrn_lbl = QLabel(f"MRN: {patient.get('mrn', 'Unknown')}")
        mrn_lbl.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(mrn_lbl)
        
        info_lbl = QLabel(f"{patient.get('age', 'Unk')} {patient.get('sex', 'U')} · {patient.get('scans', 1)} scans")
        info_lbl.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        layout.addWidget(info_lbl)

        btn = QPushButton("Select Patient")
        btn.setFixedHeight(26)
        btn.setStyleSheet("""
            QPushButton {
                background: #002e3b; color: #00e5ff; border: 1px solid #00566e;
                border-radius: 4px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover {
                background: #004357; border-color: #00e5ff; color: #ffffff;
            }
        """)
        btn.clicked.connect(lambda: on_select(patient))
        layout.addWidget(btn)


class EntryTargetCard(QFrame):
    """OR Workflow Card for surgical planning landmarks (ENTRY and TARGET)."""
    set_entry_requested = pyqtSignal()
    set_target_requested = pyqtSignal()
    view_entry_requested = pyqtSignal()
    view_target_requested = pyqtSignal()
    clear_entry_requested = pyqtSignal()
    clear_target_requested = pyqtSignal()
    clear_both_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background: #161616;
                border: 1px solid #282828;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        title = QLabel("<b>Entry + Target Planning</b>")
        title.setStyleSheet("color: #7cfc00; font-size: 11px; font-weight: bold;")
        layout.addWidget(title)

        # ── ENTRY Block ──
        entry_header = QLabel("<b>ENTRY</b>")
        entry_header.setStyleSheet("color: #00ff7f; font-size: 10px; font-weight: bold;")
        layout.addWidget(entry_header)

        self.lbl_entry_status = QLabel("Not marked")
        self.lbl_entry_status.setStyleSheet("color: #888888; font-size: 10px;")
        layout.addWidget(self.lbl_entry_status)

        entry_btn_row = QHBoxLayout()
        entry_btn_row.setSpacing(4)
        self.btn_set_entry = QPushButton("Set Entry")
        self.btn_view_entry = QPushButton("View Entry")
        self.btn_clear_entry = QPushButton("Clear Entry")
        for btn in (self.btn_set_entry, self.btn_view_entry, self.btn_clear_entry):
            btn.setFixedHeight(22)
            btn.setStyleSheet("""
                QPushButton {
                    background: #1e1e1e; color: #ccc; border: 1px solid #333;
                    border-radius: 3px; font-size: 9px; padding: 0 4px;
                }
                QPushButton:hover { background: #2a2a2a; color: #00ff7f; border-color: #00ff7f; }
            """)
            entry_btn_row.addWidget(btn)
        layout.addLayout(entry_btn_row)

        self.btn_set_entry.clicked.connect(self.set_entry_requested.emit)
        self.btn_view_entry.clicked.connect(self.view_entry_requested.emit)
        self.btn_clear_entry.clicked.connect(self.clear_entry_requested.emit)

        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #242424; max-height: 1px;")
        layout.addWidget(sep)

        # ── TARGET Block ──
        target_header = QLabel("<b>TARGET</b>")
        target_header.setStyleSheet("color: #ff3366; font-size: 10px; font-weight: bold;")
        layout.addWidget(target_header)

        self.lbl_target_status = QLabel("Not marked")
        self.lbl_target_status.setStyleSheet("color: #888888; font-size: 10px;")
        layout.addWidget(self.lbl_target_status)

        target_btn_row = QHBoxLayout()
        target_btn_row.setSpacing(4)
        self.btn_set_target = QPushButton("Set Target")
        self.btn_view_target = QPushButton("View Target")
        self.btn_clear_target = QPushButton("Clear Target")
        for btn in (self.btn_set_target, self.btn_view_target, self.btn_clear_target):
            btn.setFixedHeight(22)
            btn.setStyleSheet("""
                QPushButton {
                    background: #1e1e1e; color: #ccc; border: 1px solid #333;
                    border-radius: 3px; font-size: 9px; padding: 0 4px;
                }
                QPushButton:hover { background: #2a2a2a; color: #ff3366; border-color: #ff3366; }
            """)
            target_btn_row.addWidget(btn)
        layout.addLayout(target_btn_row)

        self.btn_set_target.clicked.connect(self.set_target_requested.emit)
        self.btn_view_target.clicked.connect(self.view_target_requested.emit)
        self.btn_clear_target.clicked.connect(self.clear_target_requested.emit)

        # ── CLEAR BOTH ──
        self.btn_clear_both = QPushButton("Clear Both")
        self.btn_clear_both.setFixedHeight(22)
        self.btn_clear_both.setStyleSheet("""
            QPushButton {
                background: #181818; color: #aaa; border: 1px solid #333;
                border-radius: 3px; font-size: 9px;
            }
            QPushButton:hover { background: #281818; color: #ff5555; border-color: #ff5555; }
        """)
        self.btn_clear_both.clicked.connect(self.clear_both_requested.emit)
        layout.addWidget(self.btn_clear_both)

    def update_landmarks(self, entry_point, target_point):
        """Updates status labels with real physical coordinates."""
        if entry_point:
            ex, ey, ez = entry_point.coordinates if hasattr(entry_point, "coordinates") else entry_point
            self.lbl_entry_status.setText(f"Marked\nX: {ex:.1f}  Y: {ey:.1f}  Z: {ez:.1f} mm")
            self.lbl_entry_status.setStyleSheet("color: #00ff7f; font-size: 10px; font-weight: 600;")
        else:
            self.lbl_entry_status.setText("Not marked")
            self.lbl_entry_status.setStyleSheet("color: #888888; font-size: 10px;")

        if target_point:
            tx, ty, tz = target_point.coordinates if hasattr(target_point, "coordinates") else target_point
            self.lbl_target_status.setText(f"Marked\nX: {tx:.1f}  Y: {ty:.1f}  Z: {tz:.1f} mm")
            self.lbl_target_status.setStyleSheet("color: #ff3366; font-size: 10px; font-weight: 600;")
        else:
            self.lbl_target_status.setText("Not marked")
            self.lbl_target_status.setStyleSheet("color: #888888; font-size: 10px;")


class PlannedRouteCard(QFrame):
    """OR Workflow Card for Planned Route geometry and navigation."""
    view_route_requested = pyqtSignal()
    clear_route_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background: #161616;
                border: 1px solid #282828;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        title = QLabel("<b>Planned Route</b>")
        title.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: bold;")
        layout.addWidget(title)

        self.lbl_route_status = QLabel("Requires Entry + Target")
        self.lbl_route_status.setStyleSheet("color: #888888; font-size: 10px;")
        layout.addWidget(self.lbl_route_status)

        self.lbl_route_metrics = QLabel("")
        self.lbl_route_metrics.setStyleSheet("color: #e0e0e0; font-size: 10px; font-family: monospace;")
        self.lbl_route_metrics.setVisible(False)
        layout.addWidget(self.lbl_route_metrics)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.btn_view_route = QPushButton("View Route")
        self.btn_clear_route = QPushButton("Clear Route")
        for btn in (self.btn_view_route, self.btn_clear_route):
            btn.setFixedHeight(22)
            btn.setEnabled(False)
            btn.setStyleSheet("""
                QPushButton {
                    background: #1e1e1e; color: #666; border: 1px solid #282828;
                    border-radius: 3px; font-size: 9px; padding: 0 6px;
                }
                QPushButton:enabled {
                    color: #ccc; border-color: #333;
                }
                QPushButton:enabled:hover {
                    background: #2a2a2a; color: #00e5ff; border-color: #00e5ff;
                }
            """)
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        self.btn_view_route.clicked.connect(self.view_route_requested.emit)
        self.btn_clear_route.clicked.connect(self.clear_route_requested.emit)

    def update_route(self, surgical_plan):
        """Updates route metrics and button states from the active surgical plan."""
        route = surgical_plan.get_planned_route() if surgical_plan else None
        if route:
            self.lbl_route_status.setText("Planned Route defined")
            self.lbl_route_status.setStyleSheet("color: #00e5ff; font-size: 10px; font-weight: 600;")

            dx = route.delta_x
            dy = route.delta_y
            dz = route.delta_z
            length = route.length_mm

            self.lbl_route_metrics.setText(
                f"Length: {length:.1f} mm\n"
                f"ΔX: {dx:+.1f}  ΔY: {dy:+.1f}  ΔZ: {dz:+.1f} mm"
            )
            self.lbl_route_metrics.setVisible(True)
            self.btn_view_route.setEnabled(True)
            self.btn_clear_route.setEnabled(True)
        else:
            self.lbl_route_status.setText("Requires Entry + Target")
            self.lbl_route_status.setStyleSheet("color: #888888; font-size: 10px;")
            self.lbl_route_metrics.setVisible(False)
            self.lbl_route_metrics.setText("")
            self.btn_view_route.setEnabled(False)
            self.btn_clear_route.setEnabled(False)


class StructuresToAvoidCard(QFrame):
    """OR Workflow Card for marking and reviewing structures to avoid."""
    add_structure_requested = pyqtSignal()
    view_structure_requested = pyqtSignal(str)   # structure_id
    remove_structure_requested = pyqtSignal(str) # structure_id
    clear_structures_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background: #161616;
                border: 1px solid #282828;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(6)

        title = QLabel("<b>Structures to Avoid</b>")
        title.setStyleSheet("color: #ff9100; font-size: 11px; font-weight: bold;")
        self._layout.addWidget(title)

        self.lbl_status = QLabel("No structures marked")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
        self._layout.addWidget(self.lbl_status)

        # Top Button row: [ + Add Structure ]  [ Clear All ]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.btn_add = QPushButton("+ Add Structure")
        self.btn_clear_all = QPushButton("Clear All")
        for btn in (self.btn_add, self.btn_clear_all):
            btn.setFixedHeight(22)
            btn.setStyleSheet("""
                QPushButton {
                    background: #1e1e1e; color: #ccc; border: 1px solid #333;
                    border-radius: 3px; font-size: 9px; padding: 0 6px;
                }
                QPushButton:hover {
                    background: #2a2a2a; color: #ff9100; border-color: #ff9100;
                }
            """)
            btn_row.addWidget(btn)
        self._layout.addLayout(btn_row)

        self.btn_add.clicked.connect(self.add_structure_requested.emit)
        self.btn_clear_all.clicked.connect(self.clear_structures_requested.emit)

        # Container for structure list items
        self.items_container = QWidget()
        self.items_layout = QVBoxLayout(self.items_container)
        self.items_layout.setContentsMargins(0, 0, 0, 0)
        self.items_layout.setSpacing(4)
        self._layout.addWidget(self.items_container)

    def update_structures(self, surgical_plan):
        """Updates structure list, coordinates, and route distance from active surgical plan."""
        while self.items_layout.count():
            item = self.items_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        structures = surgical_plan.get_avoid_structures() if surgical_plan else []
        route = surgical_plan.get_planned_route() if surgical_plan else None

        if not structures:
            self.lbl_status.setText("No structures marked")
            self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
            self.lbl_status.setVisible(True)
            self.btn_clear_all.setEnabled(False)
            return

        self.lbl_status.setText(f"{len(structures)} structure{'s' if len(structures) > 1 else ''} marked")
        self.lbl_status.setStyleSheet("color: #ff9100; font-size: 10px; font-weight: 600;")
        self.lbl_status.setVisible(True)
        self.btn_clear_all.setEnabled(True)

        for struct in structures:
            item_frame = QFrame()
            item_frame.setStyleSheet("""
                QFrame {
                    background: #1a1a1a;
                    border: 1px solid #282828;
                    border-radius: 4px;
                    padding: 4px;
                }
            """)
            i_layout = QVBoxLayout(item_frame)
            i_layout.setContentsMargins(4, 4, 4, 4)
            i_layout.setSpacing(2)

            # Name and coords
            lbl_name = QLabel(f"<b>{struct.name}</b> — ({struct.center_x_mm:.1f}, {struct.center_y_mm:.1f}, {struct.center_z_mm:.1f}) mm")
            lbl_name.setStyleSheet("color: #e0e0e0; font-size: 10px;")
            i_layout.addWidget(lbl_name)

            # Route distance if Entry + Target + route exist
            if route:
                dist = route.distance_to_structure(struct)
                lbl_dist = QLabel(f"Route Distance: {dist:.1f} mm")
                lbl_dist.setStyleSheet("color: #00e5ff; font-size: 10px; font-family: monospace;")
                i_layout.addWidget(lbl_dist)

            # Buttons: View, Remove
            action_row = QHBoxLayout()
            action_row.setSpacing(4)
            btn_view = QPushButton("View")
            btn_remove = QPushButton("Remove")
            for b in (btn_view, btn_remove):
                b.setFixedHeight(18)
                b.setStyleSheet("""
                    QPushButton {
                        background: #222; color: #bbb; border: 1px solid #333;
                        border-radius: 2px; font-size: 8px; padding: 0 4px;
                    }
                    QPushButton:hover { background: #333; color: #fff; }
                """)
                action_row.addWidget(b)
            action_row.addStretch(1)
            i_layout.addLayout(action_row)

            sid = struct.structure_id
            btn_view.clicked.connect(lambda checked, s=sid: self.view_structure_requested.emit(s))
            btn_remove.clicked.connect(lambda checked, s=sid: self.remove_structure_requested.emit(s))

            self.items_layout.addWidget(item_frame)


class SurgicalCorridorCard(QFrame):
    """OR Workflow Card for configurable surgical corridor around planned route."""
    visibility_changed = pyqtSignal(bool)
    radius_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background: #161616;
                border: 1px solid #282828;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(6)

        title = QLabel("<b>Surgical Corridor</b>")
        title.setStyleSheet("color: #7c4dff; font-size: 11px; font-weight: bold;")
        self._layout.addWidget(title)

        self.lbl_status = QLabel("Requires Planned Route")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
        self._layout.addWidget(self.lbl_status)

        # Metrics: Corridor Radius & Route Length
        self.lbl_metrics = QLabel("")
        self.lbl_metrics.setStyleSheet("color: #e0e0e0; font-size: 10px; font-family: monospace;")
        self.lbl_metrics.setVisible(False)
        self._layout.addWidget(self.lbl_metrics)

        # Visibility Toggle: [ Show Corridor ] / [ Hide Corridor ]
        self.btn_toggle = QPushButton("Hide Corridor")
        self.btn_toggle.setFixedHeight(24)
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #ccc; border: 1px solid #333;
                border-radius: 3px; font-size: 10px; font-weight: 600; padding: 0 6px;
            }
            QPushButton:hover {
                background: #2a2a2a; color: #7c4dff; border-color: #7c4dff;
            }
        """)
        self.btn_toggle.clicked.connect(self._on_toggle_clicked)
        self._layout.addWidget(self.btn_toggle)

        # Controls Container
        self.controls_container = QWidget()
        ctrl_layout = QVBoxLayout(self.controls_container)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(4)

        # Radius Control Row: [Slider] [Label]
        radius_row = QHBoxLayout()
        radius_row.setSpacing(6)
        lbl_rad_title = QLabel("Radius:")
        lbl_rad_title.setStyleSheet("color: #aaa; font-size: 10px;")
        radius_row.addWidget(lbl_rad_title)

        self.radius_slider = QSlider(Qt.Orientation.Horizontal)
        self.radius_slider.setRange(1, 20)  # 1 to 20 mm
        self.radius_slider.setValue(5)
        self.radius_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 4px; background: #282828; border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #7c4dff; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #fff; width: 10px; margin-top: -3px; margin-bottom: -3px; border-radius: 5px;
            }
        """)
        self.radius_slider.valueChanged.connect(self._on_slider_changed)
        radius_row.addWidget(self.radius_slider, stretch=1)

        self.lbl_radius_val = QLabel("5.0 mm")
        self.lbl_radius_val.setStyleSheet("color: #7c4dff; font-size: 10px; font-weight: bold; font-family: monospace;")
        radius_row.addWidget(self.lbl_radius_val)
        ctrl_layout.addLayout(radius_row)

        self.controls_container.setVisible(False)
        self._layout.addWidget(self.controls_container)

        # Structures Relationship Container
        self.struct_container = QWidget()
        self.struct_layout = QVBoxLayout(self.struct_container)
        self.struct_layout.setContentsMargins(0, 0, 0, 0)
        self.struct_layout.setSpacing(3)
        self.struct_container.setVisible(False)
        self._layout.addWidget(self.struct_container)

        self._corridor_enabled = True

    def _on_toggle_clicked(self):
        self._corridor_enabled = not self._corridor_enabled
        self.btn_toggle.setText("Hide Corridor" if self._corridor_enabled else "Show Corridor")
        self.visibility_changed.emit(self._corridor_enabled)

    def _on_slider_changed(self, val):
        r = float(val)
        self.lbl_radius_val.setText(f"{r:.1f} mm")
        self.radius_changed.emit(r)

    def update_corridor(self, surgical_plan):
        """Updates metrics, controls, and structure relationships from active surgical plan."""
        route = surgical_plan.get_planned_route() if surgical_plan else None
        corridor = surgical_plan.get_surgical_corridor() if surgical_plan else None

        # Clean structure relationship widgets
        while self.struct_layout.count():
            item = self.struct_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not route or not corridor:
            self.lbl_status.setText("Requires Planned Route")
            self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
            self.lbl_metrics.setVisible(False)
            self.controls_container.setVisible(False)
            self.struct_container.setVisible(False)
            if surgical_plan:
                self._corridor_enabled = getattr(surgical_plan, "corridor_enabled", True)
                self.btn_toggle.setText("Hide Corridor" if self._corridor_enabled else "Show Corridor")
            return

        # Route exists
        self.lbl_status.setText("Configured")
        self.lbl_status.setStyleSheet("color: #7c4dff; font-size: 10px; font-weight: 600;")
        self.lbl_metrics.setText(
            f"Corridor Radius: {corridor.radius_mm:.1f} mm\n"
            f"Route Length: {corridor.length_mm:.1f} mm"
        )
        self.lbl_metrics.setVisible(True)
        self.controls_container.setVisible(True)

        self._corridor_enabled = corridor.is_enabled
        self.btn_toggle.setText("Hide Corridor" if self._corridor_enabled else "Show Corridor")
        self.radius_slider.blockSignals(True)
        self.radius_slider.setValue(int(round(corridor.radius_mm)))
        self.radius_slider.blockSignals(False)
        self.lbl_radius_val.setText(f"{corridor.radius_mm:.1f} mm")

        # Evaluate AvoidStructures relationship
        structures = surgical_plan.get_avoid_structures() if surgical_plan else []
        if structures:
            self.struct_container.setVisible(True)
            for struct in structures:
                dist = route.distance_to_structure(struct)
                item = QFrame()
                item.setStyleSheet("background: #1a1a1a; border-radius: 3px; padding: 3px;")
                i_lay = QVBoxLayout(item)
                i_lay.setContentsMargins(2, 2, 2, 2)
                i_lay.setSpacing(2)

                lbl_info = QLabel(
                    f"<b>{struct.name}</b>\n"
                    f"Center Distance: {dist:.1f} mm  ·  Corridor Radius: {corridor.radius_mm:.1f} mm"
                )
                lbl_info.setStyleSheet("color: #ccc; font-size: 9px; font-family: monospace;")
                i_lay.addWidget(lbl_info)

                if dist <= corridor.radius_mm:
                    lbl_geo = QLabel("Within Corridor Geometry")
                    lbl_geo.setStyleSheet("""
                        color: #ff9100; font-size: 9px; font-weight: bold;
                        background: #2a1a00; border: 1px solid #ff9100;
                        border-radius: 2px; padding: 1px 4px;
                    """)
                    i_lay.addWidget(lbl_geo)
                self.struct_layout.addWidget(item)
        else:
            self.struct_container.setVisible(False)


class VirtualInstrumentCard(QFrame):
    """OR Workflow Card for configurable virtual instrument along planned route."""
    visibility_changed = pyqtSignal(bool)
    depth_changed = pyqtSignal(float)
    diameter_changed = pyqtSignal(float)
    view_instrument_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background: #161616;
                border: 1px solid #282828;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(6)

        title = QLabel("<b>Virtual Instrument</b>")
        title.setStyleSheet("color: #ffd600; font-size: 11px; font-weight: bold;")
        self._layout.addWidget(title)

        self.lbl_status = QLabel("Requires Planned Route")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
        self._layout.addWidget(self.lbl_status)

        # Metrics: Diameter, Insertion Depth, Tip Position
        self.lbl_metrics = QLabel("")
        self.lbl_metrics.setStyleSheet("color: #e0e0e0; font-size: 10px; font-family: monospace;")
        self.lbl_metrics.setVisible(False)
        self._layout.addWidget(self.lbl_metrics)

        # Buttons Row: [ Show/Hide Instrument ] [ View Instrument ]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.btn_toggle = QPushButton("Show Instrument")
        self.btn_toggle.setFixedHeight(24)
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #ccc; border: 1px solid #333;
                border-radius: 3px; font-size: 10px; font-weight: 600; padding: 0 6px;
            }
            QPushButton:hover {
                background: #2a2a2a; color: #ffd600; border-color: #ffd600;
            }
        """)
        self.btn_toggle.clicked.connect(self._on_toggle_clicked)
        btn_row.addWidget(self.btn_toggle, stretch=1)

        self.btn_view = QPushButton("View Instrument")
        self.btn_view.setFixedHeight(24)
        self.btn_view.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #ccc; border: 1px solid #333;
                border-radius: 3px; font-size: 10px; padding: 0 6px;
            }
            QPushButton:hover {
                background: #2a2a2a; color: #ffd600; border-color: #ffd600;
            }
        """)
        self.btn_view.clicked.connect(self.view_instrument_requested.emit)
        btn_row.addWidget(self.btn_view, stretch=1)
        self._layout.addLayout(btn_row)

        # Controls Container
        self.controls_container = QWidget()
        ctrl_layout = QVBoxLayout(self.controls_container)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(4)

        # Depth Slider Row: [Depth:] [Slider] [Value]
        depth_row = QHBoxLayout()
        depth_row.setSpacing(6)
        lbl_d_title = QLabel("Depth:")
        lbl_d_title.setStyleSheet("color: #aaa; font-size: 10px;")
        depth_row.addWidget(lbl_d_title)

        self.depth_slider = QSlider(Qt.Orientation.Horizontal)
        self.depth_slider.setRange(0, 100)
        self.depth_slider.setValue(0)
        self.depth_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 4px; background: #282828; border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #ffd600; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #fff; width: 10px; margin-top: -3px; margin-bottom: -3px; border-radius: 5px;
            }
        """)
        self.depth_slider.valueChanged.connect(self._on_depth_slider_changed)
        depth_row.addWidget(self.depth_slider, stretch=1)

        self.lbl_depth_val = QLabel("0.0 mm")
        self.lbl_depth_val.setStyleSheet("color: #ffd600; font-size: 10px; font-weight: bold; font-family: monospace;")
        depth_row.addWidget(self.lbl_depth_val)
        ctrl_layout.addLayout(depth_row)

        # Diameter Control Row: [Diameter:] [Slider] [Value]
        diam_row = QHBoxLayout()
        diam_row.setSpacing(6)
        lbl_diam_title = QLabel("Diameter:")
        lbl_diam_title.setStyleSheet("color: #aaa; font-size: 10px;")
        diam_row.addWidget(lbl_diam_title)

        self.diam_slider = QSlider(Qt.Orientation.Horizontal)
        self.diam_slider.setRange(5, 100)  # 0.5 to 10.0 mm (in 0.1 mm units)
        self.diam_slider.setValue(20)       # 2.0 mm default
        self.diam_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 4px; background: #282828; border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #ffd600; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #fff; width: 10px; margin-top: -3px; margin-bottom: -3px; border-radius: 5px;
            }
        """)
        self.diam_slider.valueChanged.connect(self._on_diam_slider_changed)
        diam_row.addWidget(self.diam_slider, stretch=1)

        self.lbl_diam_val = QLabel("2.0 mm")
        self.lbl_diam_val.setStyleSheet("color: #ffd600; font-size: 10px; font-weight: bold; font-family: monospace;")
        diam_row.addWidget(self.lbl_diam_val)
        ctrl_layout.addLayout(diam_row)

        self.controls_container.setVisible(False)
        self._layout.addWidget(self.controls_container)

        # Structures Relationship Container
        self.struct_container = QWidget()
        self.struct_layout = QVBoxLayout(self.struct_container)
        self.struct_layout.setContentsMargins(0, 0, 0, 0)
        self.struct_layout.setSpacing(3)
        self.struct_container.setVisible(False)
        self._layout.addWidget(self.struct_container)

        self._instrument_visible = False
        self._route_length_mm = 0.0

    def _on_toggle_clicked(self):
        self._instrument_visible = not self._instrument_visible
        self.btn_toggle.setText("Hide Instrument" if self._instrument_visible else "Show Instrument")
        self.visibility_changed.emit(self._instrument_visible)

    def _on_depth_slider_changed(self, val):
        depth_mm = val / 10.0
        self.lbl_depth_val.setText(f"{depth_mm:.1f} / {self._route_length_mm:.1f} mm")
        self.depth_changed.emit(depth_mm)

    def _on_diam_slider_changed(self, val):
        diam_mm = val / 10.0
        self.lbl_diam_val.setText(f"{diam_mm:.1f} mm")
        self.diameter_changed.emit(diam_mm)

    def update_instrument(self, surgical_plan):
        """Updates metrics, controls, and structure relationships from active surgical plan."""
        route = surgical_plan.get_planned_route() if surgical_plan else None
        instrument = surgical_plan.get_virtual_instrument() if surgical_plan else None

        # Clean structure relationship widgets
        while self.struct_layout.count():
            item = self.struct_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not route or not instrument:
            self.lbl_status.setText("Requires Planned Route")
            self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
            self.lbl_metrics.setVisible(False)
            self.controls_container.setVisible(False)
            self.struct_container.setVisible(False)
            if surgical_plan:
                self._instrument_visible = getattr(surgical_plan, "instrument_visible", False)
                self.btn_toggle.setText("Hide Instrument" if self._instrument_visible else "Show Instrument")
            return

        self._route_length_mm = route.length_mm
        self.lbl_status.setText("Configured")
        self.lbl_status.setStyleSheet("color: #ffd600; font-size: 10px; font-weight: 600;")

        tip_x, tip_y, tip_z = instrument.tip_position_mm
        self.lbl_metrics.setText(
            f"Diameter: {instrument.diameter_mm:.1f} mm\n"
            f"Insertion Depth: {instrument.insertion_depth_mm:.1f} / {route.length_mm:.1f} mm\n"
            f"Tip Position: ({tip_x:.1f}, {tip_y:.1f}, {tip_z:.1f}) mm"
        )
        self.lbl_metrics.setVisible(True)
        self.controls_container.setVisible(True)

        self._instrument_visible = instrument.is_visible
        self.btn_toggle.setText("Hide Instrument" if self._instrument_visible else "Show Instrument")

        # Update depth slider range and value
        max_slider_val = max(1, int(round(route.length_mm * 10)))
        self.depth_slider.blockSignals(True)
        self.depth_slider.setRange(0, max_slider_val)
        self.depth_slider.setValue(int(round(instrument.insertion_depth_mm * 10)))
        self.depth_slider.blockSignals(False)
        self.lbl_depth_val.setText(f"{instrument.insertion_depth_mm:.1f} / {route.length_mm:.1f} mm")

        # Update diameter slider
        self.diam_slider.blockSignals(True)
        self.diam_slider.setValue(int(round(instrument.diameter_mm * 10)))
        self.diam_slider.blockSignals(False)
        self.lbl_diam_val.setText(f"{instrument.diameter_mm:.1f} mm")

        # Evaluate AvoidStructures relationship
        structures = surgical_plan.get_avoid_structures() if surgical_plan else []
        if structures:
            self.struct_container.setVisible(True)
            for struct in structures:
                dist = instrument.distance_to_structure(struct)
                item = QFrame()
                item.setStyleSheet("background: #1a1a1a; border-radius: 3px; padding: 3px;")
                i_lay = QVBoxLayout(item)
                i_lay.setContentsMargins(2, 2, 2, 2)
                i_lay.setSpacing(2)

                lbl_info = QLabel(
                    f"<b>{struct.name}</b>\n"
                    f"Instrument Distance: {dist:.1f} mm"
                )
                lbl_info.setStyleSheet("color: #ccc; font-size: 9px; font-family: monospace;")
                i_lay.addWidget(lbl_info)
                self.struct_layout.addWidget(item)
        else:
            self.struct_container.setVisible(False)


class LiveDeviationCard(QFrame):
    """OR Workflow Card for simulated current instrument live deviation from planned route."""
    visibility_changed = pyqtSignal(bool)
    offsets_changed = pyqtSignal(float, float, float)  # dx, dy, dz
    angles_changed = pyqtSignal(float, float)          # yaw, pitch
    reset_deviation_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background: #161616;
                border: 1px solid #282828;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(6)

        title = QLabel("<b>Live Deviation</b>")
        title.setStyleSheet("color: #ff5252; font-size: 11px; font-weight: bold;")
        self._layout.addWidget(title)

        subtitle = QLabel("Simulated Current Instrument (Software Only)")
        subtitle.setStyleSheet("color: #888888; font-size: 9px; font-style: italic;")
        self._layout.addWidget(subtitle)

        self.lbl_status = QLabel("Requires Planned Route")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
        self._layout.addWidget(self.lbl_status)

        # Metrics Readout
        self.lbl_metrics = QLabel("")
        self.lbl_metrics.setStyleSheet("color: #e0e0e0; font-size: 10px; font-family: monospace;")
        self.lbl_metrics.setVisible(False)
        self._layout.addWidget(self.lbl_metrics)

        # Buttons Row: [ Show/Hide Current Instrument ] [ Reset to Planned Route ]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.btn_toggle = QPushButton("Show Current Instrument")
        self.btn_toggle.setFixedHeight(24)
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #ccc; border: 1px solid #333;
                border-radius: 3px; font-size: 10px; font-weight: 600; padding: 0 6px;
            }
            QPushButton:hover {
                background: #2a2a2a; color: #ff5252; border-color: #ff5252;
            }
        """)
        self.btn_toggle.clicked.connect(self._on_toggle_clicked)
        btn_row.addWidget(self.btn_toggle, stretch=1)

        self.btn_reset = QPushButton("Reset to Planned Route")
        self.btn_reset.setFixedHeight(24)
        self.btn_reset.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #ccc; border: 1px solid #333;
                border-radius: 3px; font-size: 10px; padding: 0 6px;
            }
            QPushButton:hover {
                background: #2a2a2a; color: #ff5252; border-color: #ff5252;
            }
        """)
        self.btn_reset.clicked.connect(self._on_reset_clicked)
        btn_row.addWidget(self.btn_reset, stretch=1)
        self._layout.addLayout(btn_row)

        # Controls Container
        self.controls_container = QWidget()
        ctrl_layout = QVBoxLayout(self.controls_container)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(4)

        lbl_sim = QLabel("<b>SIMULATION CONTROLS</b>")
        lbl_sim.setStyleSheet("color: #aaa; font-size: 9px; margin-top: 4px;")
        ctrl_layout.addWidget(lbl_sim)

        # Offset Sliders: X, Y, Z (-20 to +20 mm, in 0.5 mm steps -> range -40 to 40)
        self.slider_x, self.lbl_x_val = self._create_slider_row(ctrl_layout, "Offset X:", -40, 40, 0, self._on_offset_slider_changed)
        self.slider_y, self.lbl_y_val = self._create_slider_row(ctrl_layout, "Offset Y:", -40, 40, 0, self._on_offset_slider_changed)
        self.slider_z, self.lbl_z_val = self._create_slider_row(ctrl_layout, "Offset Z:", -40, 40, 0, self._on_offset_slider_changed)

        # Angular Sliders: Yaw, Pitch (-30 to +30 deg)
        self.slider_yaw, self.lbl_yaw_val = self._create_slider_row(ctrl_layout, "Yaw Offset:", -30, 30, 0, self._on_angle_slider_changed, unit="°", scale=1.0)
        self.slider_pitch, self.lbl_pitch_val = self._create_slider_row(ctrl_layout, "Pitch Offset:", -30, 30, 0, self._on_angle_slider_changed, unit="°", scale=1.0)

        self.controls_container.setVisible(False)
        self._layout.addWidget(self.controls_container)

        # Structures Relationship Container
        self.struct_container = QWidget()
        self.struct_layout = QVBoxLayout(self.struct_container)
        self.struct_layout.setContentsMargins(0, 0, 0, 0)
        self.struct_layout.setSpacing(3)
        self.struct_container.setVisible(False)
        self._layout.addWidget(self.struct_container)

        self._instrument_visible = False

    def _create_slider_row(self, parent_layout, label_text, min_val, max_val, init_val, slot, unit=" mm", scale=0.5):
        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel(label_text)
        lbl.setFixedWidth(65)
        lbl.setStyleSheet("color: #aaa; font-size: 9px;")
        row.addWidget(lbl)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(init_val)
        slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 4px; background: #282828; border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #ff5252; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #fff; width: 10px; margin-top: -3px; margin-bottom: -3px; border-radius: 5px;
            }
        """)
        slider.valueChanged.connect(slot)
        row.addWidget(slider, stretch=1)

        val_text = f"{init_val * scale:+.1f}{unit}"
        lbl_val = QLabel(val_text)
        lbl_val.setFixedWidth(45)
        lbl_val.setStyleSheet("color: #ff5252; font-size: 9px; font-weight: bold; font-family: monospace;")
        row.addWidget(lbl_val)

        parent_layout.addLayout(row)
        return slider, lbl_val

    def _on_toggle_clicked(self):
        self._instrument_visible = not self._instrument_visible
        self.btn_toggle.setText("Hide Current Instrument" if self._instrument_visible else "Show Current Instrument")
        self.visibility_changed.emit(self._instrument_visible)

    def _on_offset_slider_changed(self):
        dx = self.slider_x.value() * 0.5
        dy = self.slider_y.value() * 0.5
        dz = self.slider_z.value() * 0.5
        self.lbl_x_val.setText(f"{dx:+.1f} mm")
        self.lbl_y_val.setText(f"{dy:+.1f} mm")
        self.lbl_z_val.setText(f"{dz:+.1f} mm")
        self.offsets_changed.emit(dx, dy, dz)

    def _on_angle_slider_changed(self):
        yaw = float(self.slider_yaw.value())
        pitch = float(self.slider_pitch.value())
        self.lbl_yaw_val.setText(f"{yaw:+.1f}°")
        self.lbl_pitch_val.setText(f"{pitch:+.1f}°")
        self.angles_changed.emit(yaw, pitch)

    def _on_reset_clicked(self):
        self.slider_x.blockSignals(True)
        self.slider_y.blockSignals(True)
        self.slider_z.blockSignals(True)
        self.slider_yaw.blockSignals(True)
        self.slider_pitch.blockSignals(True)

        self.slider_x.setValue(0)
        self.slider_y.setValue(0)
        self.slider_z.setValue(0)
        self.slider_yaw.setValue(0)
        self.slider_pitch.setValue(0)

        self.lbl_x_val.setText("+0.0 mm")
        self.lbl_y_val.setText("+0.0 mm")
        self.lbl_z_val.setText("+0.0 mm")
        self.lbl_yaw_val.setText("+0.0°")
        self.lbl_pitch_val.setText("+0.0°")

        self.slider_x.blockSignals(False)
        self.slider_y.blockSignals(False)
        self.slider_z.blockSignals(False)
        self.slider_yaw.blockSignals(False)
        self.slider_pitch.blockSignals(False)

        self.reset_deviation_requested.emit()

    def update_deviation(self, surgical_plan):
        """Updates metrics, controls, and structure relationships from active surgical plan."""
        route = surgical_plan.get_planned_route() if surgical_plan else None
        pose = surgical_plan.get_current_instrument_pose() if surgical_plan else None

        # Clean structure relationship widgets
        while self.struct_layout.count():
            item = self.struct_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not route or not pose:
            self.lbl_status.setText("Requires Planned Route")
            self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
            self.lbl_metrics.setVisible(False)
            self.controls_container.setVisible(False)
            self.struct_container.setVisible(False)
            if surgical_plan:
                self._instrument_visible = getattr(surgical_plan, "deviation_visible", False)
                self.btn_toggle.setText("Hide Current Instrument" if self._instrument_visible else "Show Current Instrument")
            return

        self.lbl_status.setText("Active Simulation")
        self.lbl_status.setStyleSheet("color: #ff5252; font-size: 10px; font-weight: 600;")

        cx, cy, cz = pose.current_tip_position_mm
        nx, ny, nz = pose.nearest_planned_point_mm
        lat_dev = pose.lateral_deviation_mm
        ang_dev = pose.angular_deviation_deg

        self.lbl_metrics.setText(
            f"Planned Length: {route.length_mm:.1f} mm\n"
            f"Current Tip: ({cx:.1f}, {cy:.1f}, {cz:.1f}) mm\n"
            f"Lateral Deviation: {lat_dev:.1f} mm\n"
            f"Angular Deviation: {ang_dev:.1f}°\n"
            f"Nearest Planned Point: ({nx:.1f}, {ny:.1f}, {nz:.1f}) mm"
        )
        self.lbl_metrics.setVisible(True)
        self.controls_container.setVisible(True)

        self._instrument_visible = pose.is_visible
        self.btn_toggle.setText("Hide Current Instrument" if self._instrument_visible else "Show Current Instrument")

        # Sync slider positions
        self.slider_x.blockSignals(True)
        self.slider_y.blockSignals(True)
        self.slider_z.blockSignals(True)
        self.slider_yaw.blockSignals(True)
        self.slider_pitch.blockSignals(True)

        self.slider_x.setValue(int(round(pose.offset_x_mm / 0.5)))
        self.slider_y.setValue(int(round(pose.offset_y_mm / 0.5)))
        self.slider_z.setValue(int(round(pose.offset_z_mm / 0.5)))
        self.slider_yaw.setValue(int(round(pose.yaw_deg)))
        self.slider_pitch.setValue(int(round(pose.pitch_deg)))

        self.lbl_x_val.setText(f"{pose.offset_x_mm:+.1f} mm")
        self.lbl_y_val.setText(f"{pose.offset_y_mm:+.1f} mm")
        self.lbl_z_val.setText(f"{pose.offset_z_mm:+.1f} mm")
        self.lbl_yaw_val.setText(f"{pose.yaw_deg:+.1f}°")
        self.lbl_pitch_val.setText(f"{pose.pitch_deg:+.1f}°")

        self.slider_x.blockSignals(False)
        self.slider_y.blockSignals(False)
        self.slider_z.blockSignals(False)
        self.slider_yaw.blockSignals(False)
        self.slider_pitch.blockSignals(False)

        # Evaluate AvoidStructures relationship
        structures = surgical_plan.get_avoid_structures() if surgical_plan else []
        if structures:
            self.struct_container.setVisible(True)
            for struct in structures:
                dist = pose.distance_to_structure(struct)
                item = QFrame()
                item.setStyleSheet("background: #1a1a1a; border-radius: 3px; padding: 3px;")
                i_lay = QVBoxLayout(item)
                i_lay.setContentsMargins(2, 2, 2, 2)
                i_lay.setSpacing(2)

                lbl_info = QLabel(
                    f"<b>{struct.name}</b>\n"
                    f"Current Instrument Distance: {dist:.1f} mm"
                )
                lbl_info.setStyleSheet("color: #ccc; font-size: 9px; font-family: monospace;")
                i_lay.addWidget(lbl_info)
                self.struct_layout.addWidget(item)
        else:
            self.struct_container.setVisible(False)


class PlanVersionsCard(QFrame):
    """OR Workflow Card for Plan Saving and Version Management."""
    save_plan_clicked = pyqtSignal()
    save_as_new_clicked = pyqtSignal()
    restore_plan_clicked = pyqtSignal(str)     # version_id
    delete_plan_clicked = pyqtSignal(str)      # version_id
    rename_plan_clicked = pyqtSignal(str, str) # version_id, new_name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background: #161616;
                border: 1px solid #282828;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(6)

        title = QLabel("<b>Plan Saving / Versions</b>")
        title.setStyleSheet("color: #7cfc00; font-size: 11px; font-weight: bold;")
        self._layout.addWidget(title)

        subtitle = QLabel("Persistent Plan Records (Active Scan)")
        subtitle.setStyleSheet("color: #888888; font-size: 9px; font-style: italic;")
        self._layout.addWidget(subtitle)

        # Plan status row
        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        lbl_status_title = QLabel("Current plan state:")
        lbl_status_title.setStyleSheet("color: #aaaaaa; font-size: 10px;")
        self.lbl_status = QLabel("Saved")
        self.lbl_status.setStyleSheet("color: #7cfc00; font-size: 10px; font-weight: bold;")
        status_row.addWidget(lbl_status_title)
        status_row.addWidget(self.lbl_status)
        status_row.addStretch(1)
        self._layout.addLayout(status_row)

        # Save actions row: [ Save Plan ] [ Save As New Version ]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.btn_save = QPushButton("Save Plan")
        self.btn_save.setFixedHeight(24)
        self.btn_save.setStyleSheet("""
            QPushButton {
                background: #1b2e1b; color: #7cfc00; border: 1px solid #7cfc00;
                border-radius: 3px; font-size: 9px; font-weight: bold; padding: 0 6px;
            }
            QPushButton:hover {
                background: #254225; color: #ffffff; border-color: #99ff33;
            }
        """)
        self.btn_save.clicked.connect(self.save_plan_clicked.emit)
        btn_row.addWidget(self.btn_save, stretch=1)

        self.btn_save_as = QPushButton("Save As New Version")
        self.btn_save_as.setFixedHeight(24)
        self.btn_save_as.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #ccc; border: 1px solid #333;
                border-radius: 3px; font-size: 9px; padding: 0 6px;
            }
            QPushButton:hover {
                background: #2a2a2a; color: #7cfc00; border-color: #7cfc00;
            }
        """)
        self.btn_save_as.clicked.connect(self.save_as_new_clicked.emit)
        btn_row.addWidget(self.btn_save_as, stretch=1)
        self._layout.addLayout(btn_row)

        # Versions list header
        lbl_list_hdr = QLabel("<b>SAVED VERSIONS</b>")
        lbl_list_hdr.setStyleSheet("color: #888888; font-size: 9px; margin-top: 4px;")
        self._layout.addWidget(lbl_list_hdr)

        # Versions container
        self.versions_container = QWidget()
        self.versions_layout = QVBoxLayout(self.versions_container)
        self.versions_layout.setContentsMargins(0, 0, 0, 0)
        self.versions_layout.setSpacing(4)
        self._layout.addWidget(self.versions_container)

        self.lbl_empty = QLabel("No saved plans")
        self.lbl_empty.setStyleSheet("color: #666666; font-size: 10px; font-style: italic;")
        self._layout.addWidget(self.lbl_empty)

    def update_versions(self, surgical_plan, scan_id: str):
        """Refreshes the saved versions list from database for the active scan."""
        # 1. Update unsaved changes status
        if surgical_plan:
            if surgical_plan.has_unsaved_changes():
                self.lbl_status.setText("Unsaved Changes")
                self.lbl_status.setStyleSheet("color: #ff9100; font-size: 10px; font-weight: bold;")
            else:
                self.lbl_status.setText("Saved")
                self.lbl_status.setStyleSheet("color: #7cfc00; font-size: 10px; font-weight: bold;")

        # 2. Clear current list items
        while self.versions_layout.count():
            item = self.versions_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not scan_id:
            self.lbl_empty.setText("No active scan")
            self.lbl_empty.setVisible(True)
            self.versions_container.setVisible(False)
            return

        try:
            from database import get_surgical_plan_versions
            versions = get_surgical_plan_versions(scan_id)
        except Exception:
            versions = []

        if not versions:
            self.lbl_empty.setText("No saved plans")
            self.lbl_empty.setVisible(True)
            self.versions_container.setVisible(False)
            return

        self.lbl_empty.setVisible(False)
        self.versions_container.setVisible(True)

        # Display versions chronologically
        for v in versions:
            vid = v["id"]
            vname = v["name"]
            created = v.get("created_at", "")[:16].replace("T", " ")
            row_frame = QFrame()
            row_frame.setStyleSheet("""
                QFrame {
                    background: #1a1a1a;
                    border: 1px solid #282828;
                    border-radius: 4px;
                    padding: 4px 6px;
                }
                QFrame:hover {
                    background: #222222;
                    border-color: #383838;
                }
            """)
            r_layout = QVBoxLayout(row_frame)
            r_layout.setContentsMargins(2, 2, 2, 2)
            r_layout.setSpacing(3)

            # Top row: Version name + Created timestamp
            hdr_layout = QHBoxLayout()
            hdr_layout.setSpacing(4)
            lbl_name = QLabel(f"<b>{vname}</b>")
            lbl_name.setStyleSheet("color: #e0e0e0; font-size: 10px;")
            lbl_time = QLabel(created)
            lbl_time.setStyleSheet("color: #888888; font-size: 9px;")
            hdr_layout.addWidget(lbl_name)
            hdr_layout.addStretch(1)
            hdr_layout.addWidget(lbl_time)
            r_layout.addLayout(hdr_layout)

            # Action buttons: [ Restore ] [ Rename ] [ Delete ]
            acts_layout = QHBoxLayout()
            acts_layout.setSpacing(4)

            btn_restore = QPushButton("Restore")
            btn_restore.setFixedHeight(20)
            btn_restore.setStyleSheet("""
                QPushButton {
                    background: #102610; color: #7cfc00; border: 1px solid #7cfc00;
                    border-radius: 2px; font-size: 9px; padding: 0 4px;
                }
                QPushButton:hover {
                    background: #1b401b; color: #ffffff;
                }
            """)
            btn_restore.clicked.connect(lambda checked, version_id=vid: self.restore_plan_clicked.emit(version_id))
            acts_layout.addWidget(btn_restore)

            btn_rename = QPushButton("Rename")
            btn_rename.setFixedHeight(20)
            btn_rename.setStyleSheet("""
                QPushButton {
                    background: #202020; color: #aaa; border: 1px solid #333;
                    border-radius: 2px; font-size: 9px; padding: 0 4px;
                }
                QPushButton:hover {
                    background: #2a2a2a; color: #fff;
                }
            """)
            btn_rename.clicked.connect(lambda checked, version_id=vid, current_name=vname: self._on_rename_clicked(version_id, current_name))
            acts_layout.addWidget(btn_rename)

            btn_delete = QPushButton("Delete")
            btn_delete.setFixedHeight(20)
            btn_delete.setStyleSheet("""
                QPushButton {
                    background: #261010; color: #ff5252; border: 1px solid #552222;
                    border-radius: 2px; font-size: 9px; padding: 0 4px;
                }
                QPushButton:hover {
                    background: #401b1b; color: #ffffff; border-color: #ff5252;
                }
            """)
            btn_delete.clicked.connect(lambda checked, version_id=vid: self.delete_plan_clicked.emit(version_id))
            acts_layout.addWidget(btn_delete)

            acts_layout.addStretch(1)
            r_layout.addLayout(acts_layout)

            self.versions_layout.addWidget(row_frame)

    def _on_rename_clicked(self, version_id: str, current_name: str):
        new_name, ok = QInputDialog.getText(
            self, "Rename Plan Version", "New version name:", text=current_name
        )
        if ok and new_name.strip():
            self.rename_plan_clicked.emit(version_id, new_name.strip())


class ScanSelectionDialog(QDialog):
    """Modal dialog allowing selection of a scan for Before or After comparison."""
    def __init__(self, title: str, scans: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        self.setStyleSheet("""
            QDialog {
                background: #141414;
                border: 1px solid #333333;
                border-radius: 6px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        lbl_header = QLabel(f"<b>{title}</b>")
        lbl_header.setStyleSheet("color: #00e5ff; font-size: 13px;")
        layout.addWidget(lbl_header)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                background: #1c1c1c;
                border: 1px solid #2d2d2d;
                border-radius: 4px;
                color: #e0e0e0;
                font-size: 11px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #252525;
            }
            QListWidget::item:selected {
                background: #103020;
                color: #7cfc00;
                border: 1px solid #7cfc00;
            }
        """)

        for s in scans:
            modality = s.get("type", "CT")
            date = s.get("date", "Unknown Date")
            desc = s.get("description", "") or f"{s.get('slice_count', 1)} slices"
            item_text = f"{modality} — {date} ({desc})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.list_widget.addItem(item)

        if scans:
            self.list_widget.setCurrentRow(0)
        layout.addWidget(self.list_widget)

        btn_box = QHBoxLayout()
        btn_box.setSpacing(8)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setFixedHeight(26)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background: #242424; color: #aaaaaa; border: 1px solid #383838;
                border-radius: 3px; font-size: 11px; padding: 0 12px;
            }
            QPushButton:hover { background: #2f2f2f; color: #ffffff; }
        """)
        btn_cancel.clicked.connect(self.reject)

        btn_select = QPushButton("Select Scan")
        btn_select.setFixedHeight(26)
        btn_select.setStyleSheet("""
            QPushButton {
                background: #102610; color: #7cfc00; border: 1px solid #7cfc00;
                border-radius: 3px; font-size: 11px; font-weight: bold; padding: 0 12px;
            }
            QPushButton:hover { background: #1b401b; color: #ffffff; }
        """)
        btn_select.clicked.connect(self.accept)

        btn_box.addStretch(1)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_select)
        layout.addLayout(btn_box)

    def get_selected_scan(self) -> Optional[dict]:
        item = self.list_widget.currentItem()
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None


class CurrentPreviousScanCard(QFrame):
    """ICU Feature 1: CURRENT vs PREVIOUS SCAN workflow card.

    Allows rapid visual review and comparison of the currently active scan
    with the immediately previous relevant scan for the SAME patient.
    Strictly isolated from surgical planning state, with zero clinical interpretation.
    """
    view_previous_requested = pyqtSignal()
    view_current_requested = pyqtSignal()
    toggle_requested = pyqtSignal()
    exit_comparison_requested = pyqtSignal()
    previous_scan_selected = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_patient: dict = {}
        self.current_scan: dict = {}
        self.previous_scan: Optional[dict] = None
        self.historical_scans: list[dict] = []
        self.active_view: str = "CURRENT"
        self.is_comparing: bool = False

        self.setObjectName("CurrentPreviousComparisonCard")
        self.setFrameShape(QFrame.Shape.Box)
        self.setStyleSheet("""
            #CurrentPreviousComparisonCard {
                background: #161616;
                border: 1px solid #242424;
                border-radius: 6px;
                padding: 6px 8px;
            }
            #CurrentPreviousComparisonCard:hover {
                border-color: #00e5ff;
            }
            QLabel {
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # 1. Title Header & Status
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(6)
        self.lbl_title = QLabel("<b>Current vs Previous Scan</b>")
        self.lbl_title.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: bold;")
        hdr_layout.addWidget(self.lbl_title)
        hdr_layout.addStretch(1)

        self.lbl_status = QLabel("No previous scan available")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
        hdr_layout.addWidget(self.lbl_status)
        layout.addLayout(hdr_layout)

        # 2. Current Scan Metadata Readout
        self.lbl_current = QLabel("Current: None")
        self.lbl_current.setStyleSheet("color: #7cfc00; font-size: 10px; font-weight: 600;")
        self.lbl_current.setWordWrap(True)
        layout.addWidget(self.lbl_current)

        # 3. Previous Scan Metadata Readout
        self.lbl_previous = QLabel("Previous: None")
        self.lbl_previous.setStyleSheet("color: #00e5ff; font-size: 10px; font-weight: 600;")
        self.lbl_previous.setWordWrap(True)
        layout.addWidget(self.lbl_previous)

        # 4. Optional Historical Scan Selector (visible if >1 historical scans exist)
        self.combo_previous = QComboBox()
        self.combo_previous.setFixedHeight(22)
        self.combo_previous.setStyleSheet("""
            QComboBox {
                background: #1c1c1c; color: #e0e0e0; border: 1px solid #333333;
                border-radius: 2px; font-size: 10px; padding: 0 4px;
            }
            QComboBox::drop-down { border: none; }
        """)
        self.combo_previous.setVisible(False)
        self.combo_previous.currentIndexChanged.connect(self._on_combo_previous_changed)
        layout.addWidget(self.combo_previous)

        # 5. Active View Indicator
        self.lbl_active_view = QLabel("View: CURRENT")
        self.lbl_active_view.setStyleSheet("color: #aaaaaa; font-size: 10px; font-weight: bold;")
        layout.addWidget(self.lbl_active_view)

        # 6. Primary Action Buttons Grid
        btn_layout = QGridLayout()
        btn_layout.setSpacing(4)

        self.btn_view_previous = QPushButton("View Previous")
        self.btn_view_previous.setFixedHeight(24)
        self.btn_view_previous.setEnabled(False)
        self.btn_view_previous.setStyleSheet("""
            QPushButton {
                background: #002e3b; color: #00e5ff; border: 1px solid #00566e;
                border-radius: 3px; font-size: 10px; font-weight: 600;
            }
            QPushButton:hover:enabled { background: #004357; border-color: #00e5ff; color: #ffffff; }
            QPushButton:disabled { background: #181818; color: #555555; border-color: #282828; }
        """)
        self.btn_view_previous.clicked.connect(self._on_view_previous_clicked)
        btn_layout.addWidget(self.btn_view_previous, 0, 0)

        self.btn_show_current = QPushButton("Show Current")
        self.btn_show_current.setFixedHeight(24)
        self.btn_show_current.setEnabled(False)
        self.btn_show_current.setStyleSheet("""
            QPushButton {
                background: #1e2e1e; color: #7cfc00; border: 1px solid #2e5e2e;
                border-radius: 3px; font-size: 10px; font-weight: 600;
            }
            QPushButton:hover:enabled { background: #284428; border-color: #7cfc00; color: #ffffff; }
            QPushButton:disabled { background: #181818; color: #555555; border-color: #282828; }
        """)
        self.btn_show_current.clicked.connect(self._on_show_current_clicked)
        btn_layout.addWidget(self.btn_show_current, 0, 1)

        self.btn_toggle = QPushButton("Toggle Current / Previous")
        self.btn_toggle.setFixedHeight(24)
        self.btn_toggle.setEnabled(False)
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                background: #1c1c1c; color: #e0e0e0; border: 1px solid #333333;
                border-radius: 3px; font-size: 10px; font-weight: bold;
            }
            QPushButton:hover:enabled { background: #282828; color: #00e5ff; border-color: #00e5ff; }
            QPushButton:disabled { background: #181818; color: #555555; border-color: #282828; }
        """)
        self.btn_toggle.clicked.connect(self._on_toggle_clicked)
        btn_layout.addWidget(self.btn_toggle, 1, 0, 1, 2)

        self.btn_exit = QPushButton("Exit Comparison")
        self.btn_exit.setFixedHeight(22)
        self.btn_exit.setEnabled(False)
        self.btn_exit.setStyleSheet("""
            QPushButton {
                background: #181818; color: #888888; border: 1px solid #282828;
                border-radius: 3px; font-size: 9px;
            }
            QPushButton:hover:enabled { background: #281818; color: #ff5555; border-color: #ff5555; }
            QPushButton:disabled { background: #141414; color: #444444; border-color: #222222; }
        """)
        self.btn_exit.clicked.connect(self._on_exit_clicked)
        btn_layout.addWidget(self.btn_exit, 2, 0, 1, 2)

        layout.addLayout(btn_layout)

    def _format_scan_label(self, scan: Optional[dict]) -> str:
        if not scan:
            return "None"
        s_type = scan.get("type") or scan.get("modality") or "CT"
        s_date = scan.get("date") or scan.get("study_date") or "Unknown Date"
        s_desc = scan.get("description") or "Scan"
        s_slices = scan.get("slice_count", "")
        slice_str = f" ({s_slices} slices)" if s_slices else ""
        return f"{s_type} · {s_date} · {s_desc}{slice_str}"

    def update_scans(self, patient: dict, current_scan: dict):
        """Recomputes previous scan and updates card labels and controls."""
        self.current_patient = dict(patient) if patient else {}
        self.current_scan = dict(current_scan) if current_scan else {}
        mrn = str(self.current_patient.get("mrn", "")).strip()

        if not mrn or not self.current_scan:
            self.lbl_current.setText("Current: None")
            self.lbl_previous.setText("Previous: None")
            self.lbl_status.setText("No previous scan available")
            self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
            self.lbl_active_view.setText("View: CURRENT")
            self.combo_previous.setVisible(False)
            self._set_buttons_enabled(False)
            self.previous_scan = None
            self.historical_scans = []
            self.is_comparing = False
            return

        self.lbl_current.setText(f"Current: {self._format_scan_label(self.current_scan)}")

        # Fetch historical scans for this patient strictly earlier than current_scan
        self.historical_scans = get_historical_scans_for_patient(mrn, self.current_scan)

        if self.historical_scans:
            self.previous_scan = self.historical_scans[0]
            self.lbl_previous.setText(f"Previous: {self._format_scan_label(self.previous_scan)}")
            self.lbl_status.setText("Previous scan available")
            self.lbl_status.setStyleSheet("color: #00e5ff; font-size: 10px; font-weight: bold;")
            self._set_buttons_enabled(True)

            if len(self.historical_scans) > 1:
                self.combo_previous.blockSignals(True)
                self.combo_previous.clear()
                for s in self.historical_scans:
                    self.combo_previous.addItem(self._format_scan_label(s), s)
                self.combo_previous.setCurrentIndex(0)
                self.combo_previous.blockSignals(False)
                self.combo_previous.setVisible(True)
            else:
                self.combo_previous.setVisible(False)
        else:
            self.previous_scan = None
            self.lbl_previous.setText("Previous: None")
            self.lbl_status.setText("No previous scan available")
            self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
            self.combo_previous.setVisible(False)
            self._set_buttons_enabled(False)
            self.is_comparing = False

        self.lbl_active_view.setText("View: CURRENT")

    def _set_buttons_enabled(self, enabled: bool):
        self.btn_view_previous.setEnabled(enabled)
        self.btn_show_current.setEnabled(enabled)
        self.btn_toggle.setEnabled(enabled)
        self.btn_exit.setEnabled(enabled and self.is_comparing)

    def set_active_view(self, view: str):
        """Updates active view label ('CURRENT' or 'PREVIOUS') and exit button availability."""
        view_clean = view.strip().upper()
        if view_clean in ("AFTER", "CURRENT"):
            self.active_view = "CURRENT"
            self.lbl_active_view.setText("View: CURRENT")
            self.lbl_active_view.setStyleSheet("color: #7cfc00; font-size: 10px; font-weight: bold;")
        elif view_clean in ("BEFORE", "PREVIOUS"):
            self.active_view = "PREVIOUS"
            self.lbl_active_view.setText("View: PREVIOUS")
            self.lbl_active_view.setStyleSheet("color: #00e5ff; font-size: 10px; font-weight: bold;")
        self.btn_exit.setEnabled(self.is_comparing)

    def _on_combo_previous_changed(self, index: int):
        if 0 <= index < len(self.historical_scans):
            self.previous_scan = self.historical_scans[index]
            self.lbl_previous.setText(f"Previous: {self._format_scan_label(self.previous_scan)}")
            self.previous_scan_selected.emit(self.previous_scan)

    def _on_view_previous_clicked(self):
        self.is_comparing = True
        self.set_active_view("PREVIOUS")
        self.view_previous_requested.emit()

    def _on_show_current_clicked(self):
        self.set_active_view("CURRENT")
        self.view_current_requested.emit()

    def _on_toggle_clicked(self):
        self.is_comparing = True
        new_view = "PREVIOUS" if self.active_view == "CURRENT" else "CURRENT"
        self.set_active_view(new_view)
        self.toggle_requested.emit()

    def _on_exit_clicked(self):
        self.is_comparing = False
        self.set_active_view("CURRENT")
        self.exit_comparison_requested.emit()


class SameLocationReviewCard(QFrame):
    """ICU Feature 2: SAME-LOCATION REVIEW workflow card.

    Allows inspecting approximately the same physical anatomical location
    in both CURRENT and PREVIOUS scans for the same patient.
    Canonical location is physical (X, Y, Z) in mm. Slice indices are derived values.
    Strictly isolated from surgical planning and caliper measurements, with zero clinical interpretation.
    """
    mark_location_requested = pyqtSignal()
    view_current_requested = pyqtSignal()
    view_previous_requested = pyqtSignal()
    clear_location_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SameLocationReviewCard")
        self.setFrameShape(QFrame.Shape.Box)
        self.setStyleSheet("""
            #SameLocationReviewCard {
                background: #161616;
                border: 1px solid #242424;
                border-radius: 6px;
                padding: 6px 8px;
            }
            #SameLocationReviewCard:hover {
                border-color: #00e5ff;
            }
            QLabel {
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # 1. Title Header & Status
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(6)
        self.lbl_title = QLabel("<b>Same-location Review</b>")
        self.lbl_title.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: bold;")
        hdr_layout.addWidget(self.lbl_title)
        hdr_layout.addStretch(1)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
        hdr_layout.addWidget(self.lbl_status)
        layout.addLayout(hdr_layout)

        # 2. Location Coordinate Readout
        self.lbl_location = QLabel("No location selected")
        self.lbl_location.setStyleSheet("color: #888888; font-size: 10px;")
        layout.addWidget(self.lbl_location)

        # 3. Derived Slices Readout
        self.lbl_slices = QLabel("")
        self.lbl_slices.setStyleSheet("color: #aaaaaa; font-size: 9px;")
        self.lbl_slices.setVisible(False)
        layout.addWidget(self.lbl_slices)

        # 4. Buttons Row: [ Mark Location ] [ Clear ]
        row1 = QHBoxLayout()
        row1.setSpacing(5)

        self.btn_mark = QPushButton("📍 Mark Location")
        self.btn_mark.setCheckable(True)
        self.btn_mark.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #00e5ff; border: 1px solid #00e5ff;
                border-radius: 3px; font-size: 9px; font-weight: bold; padding: 3px 6px;
            }
            QPushButton:checked {
                background: #003840; color: #00ffff; border: 1px solid #00ffff;
            }
            QPushButton:hover { background: #282828; }
        """)
        self.btn_mark.clicked.connect(self.mark_location_requested.emit)
        row1.addWidget(self.btn_mark)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setEnabled(False)
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #888888; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; padding: 3px 6px;
            }
            QPushButton:hover:enabled { background: #2a2a2a; color: #ffffff; border-color: #555555; }
        """)
        self.btn_clear.clicked.connect(self.clear_location_requested.emit)
        row1.addWidget(self.btn_clear)

        layout.addLayout(row1)

        # 5. Buttons Row: [ View Current ] [ View Previous ]
        row2 = QHBoxLayout()
        row2.setSpacing(5)

        self.btn_view_current = QPushButton("View Current")
        self.btn_view_current.setEnabled(False)
        self.btn_view_current.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #7cfc00; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; font-weight: 600; padding: 3px 6px;
            }
            QPushButton:hover:enabled { background: #2a2a2a; color: #ffffff; border-color: #7cfc00; }
        """)
        self.btn_view_current.clicked.connect(self.view_current_requested.emit)
        row2.addWidget(self.btn_view_current)

        self.btn_view_previous = QPushButton("View Previous")
        self.btn_view_previous.setEnabled(False)
        self.btn_view_previous.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #00e5ff; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; font-weight: 600; padding: 3px 6px;
            }
            QPushButton:hover:enabled { background: #2a2a2a; color: #ffffff; border-color: #00e5ff; }
        """)
        self.btn_view_previous.clicked.connect(self.view_previous_requested.emit)
        row2.addWidget(self.btn_view_previous)

        layout.addLayout(row2)

    def update_location(self, review):
        """Updates display from SameLocationReview instance."""
        if review is None or not review.is_active or not review.is_point_valid():
            self.clear_location()
            return

        coords = review.get_physical_coordinates()
        if not coords:
            self.clear_location()
            return

        x, y, z = coords
        self.lbl_location.setText(f"Location: ({x:.1f}, {y:.1f}, {z:.1f}) mm")
        self.lbl_location.setStyleSheet("color: #ffffff; font-size: 10px; font-weight: 600;")

        cur_axial = review.get_slice_index("current", "axial")
        prev_axial = review.get_slice_index("previous", "axial")

        cur_str = f"Slice {cur_axial}" if cur_axial is not None else "N/A"
        if review.correspondence_available and prev_axial is not None:
            prev_str = f"Slice {prev_axial} (Approx. Correspondence)"
            self.lbl_status.setText("Approx. Correspondence")
            self.lbl_status.setStyleSheet("color: #00e5ff; font-size: 10px; font-weight: bold;")
            self.btn_view_previous.setEnabled(True)
        else:
            prev_str = "Physical correspondence unavailable"
            self.lbl_status.setText("Physical correspondence unavailable")
            self.lbl_status.setStyleSheet("color: #ff5252; font-size: 10px; font-weight: bold;")
            self.btn_view_previous.setEnabled(False)

        self.lbl_slices.setText(f"Current: {cur_str}\nPrevious: {prev_str}")
        self.lbl_slices.setVisible(True)

        self.btn_clear.setEnabled(True)
        self.btn_view_current.setEnabled(True)
        self.btn_mark.setChecked(False)

    def clear_location(self):
        """Resets card to empty location state."""
        self.lbl_location.setText("No location selected")
        self.lbl_location.setStyleSheet("color: #888888; font-size: 10px;")
        self.lbl_slices.setText("")
        self.lbl_slices.setVisible(False)
        self.lbl_status.setText("")
        self.btn_clear.setEnabled(False)
        self.btn_view_current.setEnabled(False)
        self.btn_view_previous.setEnabled(False)
        self.btn_mark.setChecked(False)

    def set_marking_active(self, active: bool):
        """Updates mark button checked state."""
        self.btn_mark.setChecked(bool(active))

    def _on_view_previous_clicked(self):
        self.is_comparing = True
        self.set_active_view("PREVIOUS")
        self.view_previous_requested.emit()

    def _on_show_current_clicked(self):
        self.set_active_view("CURRENT")
        self.view_current_requested.emit()

    def _on_toggle_clicked(self):
        self.is_comparing = True
        new_view = "CURRENT" if self.active_view == "PREVIOUS" else "PREVIOUS"
        self.set_active_view(new_view)
        self.toggle_requested.emit()

    def _on_exit_clicked(self):
        self.is_comparing = False
        self.set_active_view("CURRENT")
        self.btn_exit.setEnabled(False)
        self.exit_comparison_requested.emit()


class MeasurementTrackingCard(QFrame):
    """ICU Feature 3: MEASUREMENT TRACKING workflow card.

    Allows saving and tracking clinically measured distances across Current and Previous scans
    for the same patient. Displays side-by-side numeric values and difference with zero clinical interpretation.
    """
    track_measurement_requested = pyqtSignal()
    delete_tracked_measurement_requested = pyqtSignal(str)  # measurement_id
    selection_changed = pyqtSignal(str, str)  # (current_meas_id, prev_meas_id)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MeasurementTrackingCard")
        self.setFrameShape(QFrame.Shape.Box)
        self.setStyleSheet("""
            #MeasurementTrackingCard {
                background: #161616;
                border: 1px solid #242424;
                border-radius: 6px;
                padding: 6px 8px;
            }
            #MeasurementTrackingCard:hover {
                border-color: #00e5ff;
            }
            QLabel {
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # 1. Header
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(6)
        self.lbl_title = QLabel("<b>Measurement Tracking</b>")
        self.lbl_title.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: bold;")
        hdr_layout.addWidget(self.lbl_title)
        hdr_layout.addStretch(1)

        self.lbl_status = QLabel("0 tracked")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
        hdr_layout.addWidget(self.lbl_status)
        layout.addLayout(hdr_layout)

        # 2. Current Scan Section
        lbl_curr_hdr = QLabel("Current Scan Measurements:")
        lbl_curr_hdr.setStyleSheet("color: #aaaaaa; font-size: 9px; font-weight: 600;")
        layout.addWidget(lbl_curr_hdr)

        self.combo_current = QComboBox()
        self.combo_current.setStyleSheet("""
            QComboBox {
                background: #1f1f1f; color: #ffffff; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; padding: 2px 5px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #1f1f1f; color: #ffffff; selection-background-color: #00e5ff;
                selection-color: #000000;
            }
        """)
        self.combo_current.currentIndexChanged.connect(self._on_selection_changed)
        layout.addWidget(self.combo_current)

        # 3. Previous Scan Section
        lbl_prev_hdr = QLabel("Previous Scan Measurements:")
        lbl_prev_hdr.setStyleSheet("color: #aaaaaa; font-size: 9px; font-weight: 600;")
        layout.addWidget(lbl_prev_hdr)

        self.combo_previous = QComboBox()
        self.combo_previous.setStyleSheet("""
            QComboBox {
                background: #1f1f1f; color: #ffffff; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; padding: 2px 5px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #1f1f1f; color: #ffffff; selection-background-color: #00e5ff;
                selection-color: #000000;
            }
        """)
        self.combo_previous.currentIndexChanged.connect(self._on_selection_changed)
        layout.addWidget(self.combo_previous)

        # 4. Side-by-side Readout Box (strictly neutral)
        readout_box = QFrame()
        readout_box.setStyleSheet("background: #111111; border: 1px solid #222222; border-radius: 3px; padding: 4px;")
        readout_layout = QVBoxLayout(readout_box)
        readout_layout.setContentsMargins(4, 4, 4, 4)
        readout_layout.setSpacing(2)

        self.lbl_current_val = QLabel("Current: None")
        self.lbl_current_val.setStyleSheet("color: #7cfc00; font-size: 10px; font-weight: 600;")
        readout_layout.addWidget(self.lbl_current_val)

        self.lbl_prev_val = QLabel("Previous: None")
        self.lbl_prev_val.setStyleSheet("color: #00e5ff; font-size: 10px; font-weight: 600;")
        readout_layout.addWidget(self.lbl_prev_val)

        self.lbl_diff_val = QLabel("Difference: N/A")
        self.lbl_diff_val.setStyleSheet("color: #ffffff; font-size: 10px; font-weight: bold;")
        readout_layout.addWidget(self.lbl_diff_val)

        layout.addWidget(readout_box)

        # 5. Buttons Row: [ ➕ Track Active Measurement ] [ 🗑 Remove Tracked ]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(5)

        self.btn_track = QPushButton("➕ Track Active")
        self.btn_track.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #00e5ff; border: 1px solid #00e5ff;
                border-radius: 3px; font-size: 9px; font-weight: bold; padding: 4px 6px;
            }
            QPushButton:hover { background: #003840; color: #00ffff; }
        """)
        self.btn_track.clicked.connect(self.track_measurement_requested.emit)
        btn_row.addWidget(self.btn_track)

        self.btn_remove = QPushButton("🗑 Remove")
        self.btn_remove.setEnabled(False)
        self.btn_remove.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #ff5252; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; padding: 4px 6px;
            }
            QPushButton:hover:enabled { background: #3a1c1c; border-color: #ff5252; }
        """)
        self.btn_remove.clicked.connect(self._on_remove_clicked)
        btn_row.addWidget(self.btn_remove)

        layout.addLayout(btn_row)

        self._updating = False

    def update_tracking(self, tracker):
        """Updates UI display and dropdowns from MeasurementTracker."""
        self._updating = True
        try:
            # Update Current Combo
            self.combo_current.clear()
            curr_list = tracker.get_current_measurements() if tracker else []
            if not curr_list:
                self.combo_current.addItem("(No measurements)", userData=None)
            else:
                for m in curr_list:
                    text = f"{m.label}: {m.value_mm:.1f} {m.unit}"
                    self.combo_current.addItem(text, userData=m.id)

            # Select tracked item in current
            if tracker and tracker.selected_current_id:
                idx = self.combo_current.findData(tracker.selected_current_id)
                if idx >= 0:
                    self.combo_current.setCurrentIndex(idx)

            # Update Previous Combo
            self.combo_previous.clear()
            prev_list = tracker.get_previous_measurements() if tracker else []
            if not prev_list:
                self.combo_previous.addItem("(No measurements)", userData=None)
            else:
                for m in prev_list:
                    text = f"{m.label}: {m.value_mm:.1f} {m.unit}"
                    self.combo_previous.addItem(text, userData=m.id)

            # Select tracked item in previous
            if tracker and tracker.selected_previous_id:
                idx = self.combo_previous.findData(tracker.selected_previous_id)
                if idx >= 0:
                    self.combo_previous.setCurrentIndex(idx)

            # Update Readouts
            comp = tracker.get_comparison() if tracker else {
                "current_text": "Current: None",
                "previous_text": "Previous: None",
                "difference_text": "Difference: N/A"
            }
            self.lbl_current_val.setText(comp["current_text"])
            self.lbl_prev_val.setText(comp["previous_text"])
            self.lbl_diff_val.setText(comp["difference_text"])

            # Status and Remove button state
            total = (len(curr_list) + len(prev_list)) if tracker else 0
            self.lbl_status.setText(f"{total} tracked")
            has_selection = bool(tracker and (tracker.selected_current_id or tracker.selected_previous_id))
            self.btn_remove.setEnabled(has_selection)
        finally:
            self._updating = False

    def clear_card(self):
        """Resets the card to empty state."""
        self._updating = True
        try:
            self.combo_current.clear()
            self.combo_current.addItem("(No measurements)", userData=None)
            self.combo_previous.clear()
            self.combo_previous.addItem("(No measurements)", userData=None)
            self.lbl_current_val.setText("Current: None")
            self.lbl_prev_val.setText("Previous: None")
            self.lbl_diff_val.setText("Difference: N/A")
            self.lbl_status.setText("0 tracked")
            self.btn_remove.setEnabled(False)
        finally:
            self._updating = False

    def _on_selection_changed(self, idx):
        if self._updating:
            return
        curr_id = self.combo_current.currentData()
        prev_id = self.combo_previous.currentData()
        self.selection_changed.emit(str(curr_id) if curr_id else "", str(prev_id) if prev_id else "")

    def _on_remove_clicked(self):
        curr_id = self.combo_current.currentData()
        prev_id = self.combo_previous.currentData()
        target_id = curr_id or prev_id
        if target_id:
            self.delete_tracked_measurement_requested.emit(str(target_id))


class AnnotationCarryForwardCard(QFrame):
    """ICU Feature 4: ANNOTATION CARRY-FORWARD workflow card.

    Allows selecting an existing user-created spatial annotation on one scan (Current or Previous)
    and carrying it forward to the corresponding physical location in the other scan for the same patient.
    Zero clinical interpretation; clearly indicates 'Approx. Correspondence' or 'Physical correspondence unavailable'.
    """
    create_annotation_requested = pyqtSignal()
    select_annotation_requested = pyqtSignal(str)  # annotation_id
    carry_forward_requested = pyqtSignal(str)      # target_scan_id
    view_source_requested = pyqtSignal(str)        # annotation_id
    view_target_requested = pyqtSignal(str)        # annotation_id
    delete_annotation_requested = pyqtSignal(str)  # annotation_id
    clear_selection_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AnnotationCarryForwardCard")
        self.setFrameShape(QFrame.Shape.Box)
        self.setStyleSheet("""
            #AnnotationCarryForwardCard {
                background: #161616;
                border: 1px solid #242424;
                border-radius: 6px;
                padding: 6px 8px;
            }
            #AnnotationCarryForwardCard:hover {
                border-color: #00e5ff;
            }
            QLabel {
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # 1. Header
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(6)
        self.lbl_title = QLabel("<b>Annotation Carry-forward</b>")
        self.lbl_title.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: bold;")
        hdr_layout.addWidget(self.lbl_title)
        hdr_layout.addStretch(1)

        self.lbl_status = QLabel("0 annotations")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
        hdr_layout.addWidget(self.lbl_status)
        layout.addLayout(hdr_layout)

        # 2. Selector Dropdown
        lbl_sel_hdr = QLabel("Select Annotation:")
        lbl_sel_hdr.setStyleSheet("color: #aaaaaa; font-size: 9px; font-weight: 600;")
        layout.addWidget(lbl_sel_hdr)

        self.combo_annotations = QComboBox()
        self.combo_annotations.setStyleSheet("""
            QComboBox {
                background: #1f1f1f; color: #ffffff; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; padding: 2px 5px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #1f1f1f; color: #ffffff; selection-background-color: #00e5ff;
                selection-color: #000000;
            }
        """)
        self.combo_annotations.currentIndexChanged.connect(self._on_combo_changed)
        layout.addWidget(self.combo_annotations)

        # 3. Readout Box
        readout_box = QFrame()
        readout_box.setStyleSheet("background: #111111; border: 1px solid #222222; border-radius: 3px; padding: 4px;")
        readout_layout = QVBoxLayout(readout_box)
        readout_layout.setContentsMargins(4, 4, 4, 4)
        readout_layout.setSpacing(2)

        self.lbl_annot = QLabel("Annotation: None")
        self.lbl_annot.setStyleSheet("color: #ffffff; font-size: 10px; font-weight: 600;")
        readout_layout.addWidget(self.lbl_annot)

        self.lbl_source = QLabel("No annotation selected")
        self.lbl_source.setStyleSheet("color: #ffb300; font-size: 9px;")
        readout_layout.addWidget(self.lbl_source)

        self.lbl_target = QLabel("Target: N/A")
        self.lbl_target.setStyleSheet("color: #b388ff; font-size: 9px;")
        readout_layout.addWidget(self.lbl_target)

        self.lbl_location = QLabel("Location: (0.0, 0.0, 0.0) mm")
        self.lbl_location.setStyleSheet("color: #aaaaaa; font-size: 9px;")
        readout_layout.addWidget(self.lbl_location)

        self.lbl_slices = QLabel("")
        self.lbl_slices.setStyleSheet("color: #888888; font-size: 9px;")
        self.lbl_slices.setVisible(False)
        readout_layout.addWidget(self.lbl_slices)

        self.lbl_correspondence = QLabel("Status: Physical correspondence unavailable")
        self.lbl_correspondence.setStyleSheet("color: #888888; font-size: 9px; font-weight: bold;")
        readout_layout.addWidget(self.lbl_correspondence)

        layout.addWidget(readout_box)

        # 4. Buttons Row 1: [ ➕ New Annotation ] [ ➡️ Carry Forward ]
        row1 = QHBoxLayout()
        row1.setSpacing(5)

        self.btn_new = QPushButton("➕ New Annotation")
        self.btn_new.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #ffb300; border: 1px solid #ffb300;
                border-radius: 3px; font-size: 9px; font-weight: bold; padding: 4px 6px;
            }
            QPushButton:hover { background: #332600; color: #ffd54f; }
        """)
        self.btn_new.clicked.connect(self.create_annotation_requested.emit)
        row1.addWidget(self.btn_new)

        self.btn_carry = QPushButton("➡️ Carry Forward")
        self.btn_carry.setEnabled(False)
        self.btn_carry.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #00e5ff; border: 1px solid #00e5ff;
                border-radius: 3px; font-size: 9px; font-weight: bold; padding: 4px 6px;
            }
            QPushButton:hover:enabled { background: #003840; color: #00ffff; }
            QPushButton:disabled { color: #555555; border-color: #333333; }
        """)
        self.btn_carry.clicked.connect(self._on_carry_clicked)
        row1.addWidget(self.btn_carry)

        layout.addLayout(row1)

        # 5. Buttons Row 2: [ 👁 View Source ] [ 👁 View Target ] [ 🗑 Clear ]
        row2 = QHBoxLayout()
        row2.setSpacing(5)

        self.btn_view_source = QPushButton("View Source")
        self.btn_view_source.setEnabled(False)
        self.btn_view_source.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #ffb300; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; padding: 3px 5px;
            }
            QPushButton:hover:enabled { background: #2a2a2a; color: #ffffff; border-color: #ffb300; }
            QPushButton:disabled { color: #555555; }
        """)
        self.btn_view_source.clicked.connect(self._on_view_source_clicked)
        row2.addWidget(self.btn_view_source)

        self.btn_view_target = QPushButton("View Target")
        self.btn_view_target.setEnabled(False)
        self.btn_view_target.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #b388ff; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; padding: 3px 5px;
            }
            QPushButton:hover:enabled { background: #2a2a2a; color: #ffffff; border-color: #b388ff; }
            QPushButton:disabled { color: #555555; }
        """)
        self.btn_view_target.clicked.connect(self._on_view_target_clicked)
        row2.addWidget(self.btn_view_target)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setEnabled(False)
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background: #1e1e1e; color: #888888; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; padding: 3px 5px;
            }
            QPushButton:hover:enabled { background: #2a2a2a; color: #ffffff; }
            QPushButton:disabled { color: #444444; }
        """)
        self.btn_clear.clicked.connect(self._on_clear_clicked)
        row2.addWidget(self.btn_clear)

        layout.addLayout(row2)

        self._updating = False

    def update_card(self, manager):
        """Updates UI display and dropdown from AnnotationCarryForwardManager."""
        self._updating = True
        try:
            self.combo_annotations.clear()
            annots = list(manager._annotations.values()) if manager else []
            if not annots:
                self.combo_annotations.addItem("(No annotations)", userData=None)
                self.clear_card()
                return

            for a in annots:
                prefix = "[Carried] " if a.is_carried_forward() else ""
                text = f"{prefix}{a.label} ({a.scan_id})"
                self.combo_annotations.addItem(text, userData=a.id)

            if manager.selected_annotation_id:
                idx = self.combo_annotations.findData(manager.selected_annotation_id)
                if idx >= 0:
                    self.combo_annotations.setCurrentIndex(idx)

            selected = manager.get_selected_annotation()
            if selected:
                self.lbl_annot.setText(f"Annotation: {selected.label}")
                self.lbl_source.setText(f"Source Scan: {selected.scan_id}")
                tgt_id = manager.target_scan_id or "None"
                self.lbl_target.setText(f"Target Scan: {tgt_id}")
                x, y, z = selected.physical_coordinates
                self.lbl_location.setText(f"Location: ({x:.1f}, {y:.1f}, {z:.1f}) mm")

                src_slice = manager.get_derived_slice(selected.scan_id, "axial")
                tgt_slice = manager.get_derived_slice(manager.target_scan_id, "axial") if manager.target_scan_id else None
                s_str = f"Source Slice: {src_slice}" if src_slice is not None else ""
                t_str = f"Target Slice: {tgt_slice} (Derived)" if tgt_slice is not None else ""
                self.lbl_slices.setText(f"{s_str} | {t_str}".strip(" |"))
                self.lbl_slices.setVisible(bool(s_str or t_str))

                can_cf, status = manager.can_carry_forward()
                self.lbl_correspondence.setText(f"Status: {status}")
                if status == "Approx. Correspondence":
                    self.lbl_correspondence.setStyleSheet("color: #00e5ff; font-size: 9px; font-weight: bold;")
                else:
                    self.lbl_correspondence.setStyleSheet("color: #ff5252; font-size: 9px; font-weight: bold;")

                self.btn_carry.setEnabled(can_cf)
                self.btn_view_source.setEnabled(True)
                self.btn_view_target.setEnabled(tgt_slice is not None)
                self.btn_clear.setEnabled(True)
            else:
                self.clear_card()

            self.lbl_status.setText(f"{len(annots)} annotations")
        finally:
            self._updating = False

    def clear_card(self):
        """Resets card to initial empty state: 'No annotation selected'."""
        self._updating = True
        try:
            self.lbl_annot.setText("Annotation: None")
            self.lbl_source.setText("No annotation selected")
            self.lbl_target.setText("Target: N/A")
            self.lbl_location.setText("Location: (0.0, 0.0, 0.0) mm")
            self.lbl_slices.setText("")
            self.lbl_slices.setVisible(False)
            self.lbl_correspondence.setText("Status: Physical correspondence unavailable")
            self.lbl_correspondence.setStyleSheet("color: #888888; font-size: 9px; font-weight: bold;")
            self.btn_carry.setEnabled(False)
            self.btn_view_source.setEnabled(False)
            self.btn_view_target.setEnabled(False)
            self.btn_clear.setEnabled(False)
            self.lbl_status.setText("0 annotations")
        finally:
            self._updating = False

    def _on_combo_changed(self, idx):
        if self._updating:
            return
        annot_id = self.combo_annotations.currentData()
        if annot_id:
            self.select_annotation_requested.emit(str(annot_id))
        else:
            self.clear_selection_requested.emit()

    def _on_carry_clicked(self):
        annot_id = self.combo_annotations.currentData()
        if annot_id:
            self.carry_forward_requested.emit(str(annot_id))

    def _on_view_source_clicked(self):
        annot_id = self.combo_annotations.currentData()
        if annot_id:
            self.view_source_requested.emit(str(annot_id))

    def _on_view_target_clicked(self):
        annot_id = self.combo_annotations.currentData()
        if annot_id:
            self.view_target_requested.emit(str(annot_id))

    def _on_clear_clicked(self):
        annot_id = self.combo_annotations.currentData()
        if annot_id:
            self.delete_annotation_requested.emit(str(annot_id))
        else:
            self.clear_selection_requested.emit()


class WhatChangedCard(QFrame):
    """ICU Feature 5: WHAT CHANGED? card.

    Provides objective visual difference review between Current and Previous scans.
    Strictly factual; no diagnosis, no lesion detection, no progression/regression claims.
    """
    review_differences_requested = pyqtSignal()
    show_current_requested = pyqtSignal()
    show_previous_requested = pyqtSignal()
    difference_threshold_changed = pyqtSignal(float)
    clear_difference_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("WhatChangedCard")
        self.setFrameShape(QFrame.Shape.Box)
        self.setStyleSheet("""
            #WhatChangedCard {
                background: #161616;
                border: 1px solid #242424;
                border-radius: 6px;
                padding: 6px 8px;
            }
            #WhatChangedCard:hover {
                border-color: #ff5722;
            }
            QLabel {
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)

        self.current_scan_id: str = "None"
        self.previous_scan_id: str = "None"
        self.is_active: bool = False
        self.difference_available: bool = False
        self.threshold: float = 50.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # 1. Header with title and status
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(6)
        self.lbl_title = QLabel("<b>What Changed?</b>")
        self.lbl_title.setStyleSheet("color: #ff5722; font-size: 11px; font-weight: bold;")
        hdr_layout.addWidget(self.lbl_title)
        hdr_layout.addStretch(1)

        self.lbl_header_status = QLabel("No difference review active")
        self.lbl_header_status.setStyleSheet("color: #888888; font-size: 10px;")
        hdr_layout.addWidget(self.lbl_header_status)
        layout.addLayout(hdr_layout)

        # 2. Readout Box
        readout_box = QFrame()
        readout_box.setStyleSheet("background: #111111; border: 1px solid #222222; border-radius: 3px; padding: 4px;")
        readout_layout = QVBoxLayout(readout_box)
        readout_layout.setContentsMargins(4, 4, 4, 4)
        readout_layout.setSpacing(2)

        self.lbl_current_scan = QLabel("Current: None")
        self.lbl_current_scan.setStyleSheet("color: #ffffff; font-size: 9px; font-weight: 500;")
        readout_layout.addWidget(self.lbl_current_scan)

        self.lbl_previous_scan = QLabel("Previous: None")
        self.lbl_previous_scan.setStyleSheet("color: #cccccc; font-size: 9px; font-weight: 500;")
        readout_layout.addWidget(self.lbl_previous_scan)

        self.lbl_status = QLabel("Status: No difference review active")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 9px;")
        readout_layout.addWidget(self.lbl_status)

        self.lbl_changed_voxels = QLabel("")
        self.lbl_changed_voxels.setStyleSheet("color: #ff7043; font-size: 9px; font-weight: bold;")
        self.lbl_changed_voxels.setVisible(False)
        readout_layout.addWidget(self.lbl_changed_voxels)

        layout.addWidget(readout_box)

        # 3. Difference Threshold Slider
        thresh_layout = QVBoxLayout()
        thresh_layout.setSpacing(2)
        thresh_hdr = QHBoxLayout()
        self.lbl_thresh_title = QLabel("Difference Threshold:")
        self.lbl_thresh_title.setStyleSheet("color: #aaaaaa; font-size: 9px; font-weight: 600;")
        thresh_hdr.addWidget(self.lbl_thresh_title)
        thresh_hdr.addStretch(1)

        self.lbl_thresh_val = QLabel(f"{int(self.threshold)} HU")
        self.lbl_thresh_val.setStyleSheet("color: #ff5722; font-size: 9px; font-weight: bold;")
        thresh_hdr.addWidget(self.lbl_thresh_val)
        thresh_layout.addLayout(thresh_hdr)

        self.slider_threshold = QSlider(Qt.Orientation.Horizontal)
        self.slider_threshold.setRange(10, 500)
        self.slider_threshold.setValue(int(self.threshold))
        self.slider_threshold.setStyleSheet("""
            QSlider::groove:horizontal { height: 4px; background: #222; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #d84315; border-radius: 2px; }
            QSlider::handle:horizontal { background: #ff5722; border: 1px solid #bf360c; width: 12px; margin-top: -4px; margin-bottom: -4px; border-radius: 6px; }
            QSlider::handle:horizontal:hover { background: #ff7043; }
        """)
        self.slider_threshold.valueChanged.connect(self._on_slider_changed)
        thresh_layout.addWidget(self.slider_threshold)
        layout.addLayout(thresh_layout)

        # 4. Action Buttons
        # Row 1: [ Review Differences ]
        self.btn_review = QPushButton("Review Differences")
        self.btn_review.setFixedHeight(24)
        self.btn_review.setStyleSheet("""
            QPushButton {
                background: #bf360c; color: #ffffff; border: 1px solid #e64a19;
                border-radius: 3px; font-size: 10px; font-weight: bold;
            }
            QPushButton:hover {
                background: #d84315; border-color: #ff5722;
            }
            QPushButton:disabled {
                background: #2a2a2a; color: #555555; border-color: #333333;
            }
        """)
        self.btn_review.clicked.connect(self.review_differences_requested.emit)
        layout.addWidget(self.btn_review)

        # Row 2: [ Show Current ] [ Show Previous ] [ Clear ]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.btn_show_current = QPushButton("Show Current")
        self.btn_show_current.setFixedHeight(22)
        self.btn_show_current.setStyleSheet("""
            QPushButton {
                background: #242424; color: #ffffff; border: 1px solid #3a3a3a;
                border-radius: 3px; font-size: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #333333; border-color: #00e5ff; }
        """)
        self.btn_show_current.clicked.connect(self.show_current_requested.emit)
        btn_row.addWidget(self.btn_show_current)

        self.btn_show_previous = QPushButton("Show Previous")
        self.btn_show_previous.setFixedHeight(22)
        self.btn_show_previous.setStyleSheet("""
            QPushButton {
                background: #242424; color: #ffffff; border: 1px solid #3a3a3a;
                border-radius: 3px; font-size: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #333333; border-color: #00e5ff; }
        """)
        self.btn_show_previous.clicked.connect(self.show_previous_requested.emit)
        btn_row.addWidget(self.btn_show_previous)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setFixedHeight(22)
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background: #1c1c1c; color: #888888; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #282828; color: #ff5555; border-color: #ff5555; }
        """)
        self.btn_clear.clicked.connect(self.clear_difference_requested.emit)
        btn_row.addWidget(self.btn_clear)

        layout.addLayout(btn_row)

    def _on_slider_changed(self, value: int):
        self.threshold = float(value)
        self.lbl_thresh_val.setText(f"{value} HU")
        self.difference_threshold_changed.emit(self.threshold)

    def set_scan_context(self, current_scan_id: str, previous_scan_id: str):
        self.current_scan_id = str(current_scan_id or "None")
        self.previous_scan_id = str(previous_scan_id or "None")
        self.lbl_current_scan.setText(f"Current: {self.current_scan_id}")
        self.lbl_previous_scan.setText(f"Previous: {self.previous_scan_id}")

    def update_from_review(self, change_review):
        """Updates UI state from a ChangeReview instance."""
        if change_review is None:
            self.reset_ui()
            return

        c_id = change_review.current_scan_id or "None"
        p_id = change_review.previous_scan_id or "None"
        self.set_scan_context(c_id, p_id)

        self.threshold = change_review.threshold
        self.slider_threshold.blockSignals(True)
        self.slider_threshold.setValue(int(self.threshold))
        self.slider_threshold.blockSignals(False)
        self.lbl_thresh_val.setText(f"{int(self.threshold)} HU")

        if change_review.enabled and change_review.difference_available:
            self.lbl_header_status.setText("Difference map active")
            self.lbl_header_status.setStyleSheet("color: #ff5722; font-size: 10px; font-weight: bold;")
            self.lbl_status.setText("Status: Difference map available")
            self.lbl_status.setStyleSheet("color: #7cfc00; font-size: 9px;")
            count = change_review.get_changed_voxel_count()
            self.lbl_changed_voxels.setText(f"Changed voxels: {count:,}  ·  Difference threshold: {int(self.threshold)} HU")
            self.lbl_changed_voxels.setVisible(True)
        elif not change_review.difference_available and change_review.status_message != "No difference review active":
            self.lbl_header_status.setText("Unavailable")
            self.lbl_header_status.setStyleSheet("color: #ffb300; font-size: 10px;")
            self.lbl_status.setText(f"Status: {change_review.status_message}")
            self.lbl_status.setStyleSheet("color: #ffb300; font-size: 9px;")
            self.lbl_changed_voxels.setVisible(False)
        else:
            self.reset_ui()

    def reset_ui(self):
        self.lbl_header_status.setText("No difference review active")
        self.lbl_header_status.setStyleSheet("color: #888888; font-size: 10px;")
        self.lbl_status.setText("Status: No difference review active")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 9px;")
        self.lbl_changed_voxels.setVisible(False)


class DeviceMarkersCard(QFrame):
    """ICU Feature 6: Device Markers card.

    Allows a clinician to place, view, and manage visual markers for medical devices
    visible on an imaging scan (e.g. Endotracheal Tube, Central Line, Feeding Tube,
    Drain, Catheter, Other) so their locations can be quickly identified.

    Zero clinical interpretation:
    - Never claims correct/incorrect position, displacement, migration, or complication.
    - Pure visual marker and navigation aid.
    """
    mark_device_requested = pyqtSignal(str, str)             # device_type, label
    view_device_marker_requested = pyqtSignal(str)           # marker_id
    delete_device_marker_requested = pyqtSignal(str)         # marker_id
    clear_device_marker_selection_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DeviceMarkersCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            #DeviceMarkersCard {
                background: #141414;
                border: 1px solid #2a2a2a;
                border-radius: 6px;
            }
            QLabel {
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)
        self.markers: list = []
        self.selected_marker_id: Optional[str] = None
        self.current_scan_id: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # 1. Header with title and status
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(6)
        self.lbl_title = QLabel("<b>Device Markers</b>")
        self.lbl_title.setStyleSheet("color: #00bfa5; font-size: 11px; font-weight: bold;")
        hdr_layout.addWidget(self.lbl_title)
        hdr_layout.addStretch(1)

        self.lbl_header_status = QLabel("No device markers")
        self.lbl_header_status.setStyleSheet("color: #888888; font-size: 10px;")
        hdr_layout.addWidget(self.lbl_header_status)
        layout.addLayout(hdr_layout)

        # 2. Controls Box: Device Type & Label input
        ctrl_box = QFrame()
        ctrl_box.setStyleSheet("background: #111111; border: 1px solid #222222; border-radius: 3px; padding: 4px;")
        ctrl_layout = QVBoxLayout(ctrl_box)
        ctrl_layout.setContentsMargins(4, 4, 4, 4)
        ctrl_layout.setSpacing(4)

        # Device Type Row
        type_row = QHBoxLayout()
        lbl_type = QLabel("Device Type:")
        lbl_type.setStyleSheet("color: #aaaaaa; font-size: 9px; font-weight: 600;")
        type_row.addWidget(lbl_type)

        self.combo_device_type = QComboBox()
        self.combo_device_type.setFixedHeight(22)
        from device_markers import CONTROLLED_DEVICE_TYPES
        for dt in CONTROLLED_DEVICE_TYPES:
            self.combo_device_type.addItem(dt)
        self.combo_device_type.setStyleSheet("""
            QComboBox {
                background: #1c1c1c; color: #ffffff; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; padding: 1px 4px;
            }
            QComboBox QAbstractItemView {
                background: #1c1c1c; color: #ffffff; selection-background-color: #00bfa5;
            }
        """)
        type_row.addWidget(self.combo_device_type, 1)
        ctrl_layout.addLayout(type_row)

        # Label Row
        lbl_row = QHBoxLayout()
        lbl_txt = QLabel("Label:")
        lbl_txt.setStyleSheet("color: #aaaaaa; font-size: 9px; font-weight: 600;")
        lbl_row.addWidget(lbl_txt)

        self.txt_label = QLineEdit()
        self.txt_label.setFixedHeight(22)
        self.txt_label.setPlaceholderText("Optional label (e.g. #1, Right)")
        self.txt_label.setStyleSheet("""
            QLineEdit {
                background: #1c1c1c; color: #ffffff; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; padding: 1px 4px;
            }
            QLineEdit:focus { border-color: #00bfa5; }
        """)
        lbl_row.addWidget(self.txt_label, 1)
        ctrl_layout.addLayout(lbl_row)

        # Mark Device Button
        self.btn_mark = QPushButton("Mark Device")
        self.btn_mark.setFixedHeight(24)
        self.btn_mark.setStyleSheet("""
            QPushButton {
                background: #004d40; color: #ffffff; border: 1px solid #00bfa5;
                border-radius: 3px; font-size: 10px; font-weight: bold;
            }
            QPushButton:hover {
                background: #00695c; border-color: #1de9b6;
            }
            QPushButton:disabled {
                background: #2a2a2a; color: #555555; border-color: #333333;
            }
        """)
        self.btn_mark.clicked.connect(self._on_mark_clicked)
        ctrl_layout.addWidget(self.btn_mark)

        layout.addWidget(ctrl_box)

        # 3. Markers List
        self.list_markers = QListWidget()
        self.list_markers.setFixedHeight(80)
        self.list_markers.setStyleSheet("""
            QListWidget {
                background: #0d0d0d; border: 1px solid #222222; border-radius: 3px;
                color: #dddddd; font-size: 9px;
            }
            QListWidget::item { padding: 3px 4px; border-bottom: 1px solid #1a1a1a; }
            QListWidget::item:selected { background: #004d40; color: #ffffff; }
            QListWidget::item:hover { background: #161616; }
        """)
        self.list_markers.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_markers)

        # 4. Action Buttons: [ View Selected ] [ Clear Selection ] [ Delete Marker ]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.btn_view_selected = QPushButton("View Selected")
        self.btn_view_selected.setFixedHeight(22)
        self.btn_view_selected.setEnabled(False)
        self.btn_view_selected.setStyleSheet("""
            QPushButton {
                background: #242424; color: #ffffff; border: 1px solid #3a3a3a;
                border-radius: 3px; font-size: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #333333; border-color: #00bfa5; }
            QPushButton:disabled { background: #1c1c1c; color: #555555; border-color: #2a2a2a; }
        """)
        self.btn_view_selected.clicked.connect(self._on_view_selected)
        btn_row.addWidget(self.btn_view_selected)

        self.btn_clear_selection = QPushButton("Clear Selection")
        self.btn_clear_selection.setFixedHeight(22)
        self.btn_clear_selection.setEnabled(False)
        self.btn_clear_selection.setStyleSheet("""
            QPushButton {
                background: #1c1c1c; color: #888888; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #282828; color: #ffffff; }
            QPushButton:disabled { background: #1c1c1c; color: #444444; border-color: #222222; }
        """)
        self.btn_clear_selection.clicked.connect(self._on_clear_selection)
        btn_row.addWidget(self.btn_clear_selection)

        self.btn_delete = QPushButton("Delete Marker")
        self.btn_delete.setFixedHeight(22)
        self.btn_delete.setEnabled(False)
        self.btn_delete.setStyleSheet("""
            QPushButton {
                background: #1c1c1c; color: #ff5252; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #2a1414; border-color: #ff5252; }
            QPushButton:disabled { background: #1c1c1c; color: #553333; border-color: #222222; }
        """)
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        btn_row.addWidget(self.btn_delete)

        layout.addLayout(btn_row)

    def _on_mark_clicked(self):
        dtype = self.combo_device_type.currentText()
        lbl = self.txt_label.text().strip()
        if not lbl and dtype != "Other":
            lbl = dtype
        elif not lbl and dtype == "Other":
            lbl = "Other Device"
        self.mark_device_requested.emit(dtype, lbl)

    def _on_selection_changed(self):
        items = self.list_markers.selectedItems()
        if not items:
            self.selected_marker_id = None
            self.btn_view_selected.setEnabled(False)
            self.btn_clear_selection.setEnabled(False)
            self.btn_delete.setEnabled(False)
            return

        item = items[0]
        self.selected_marker_id = item.data(Qt.ItemDataRole.UserRole)
        self.btn_view_selected.setEnabled(bool(self.selected_marker_id))
        self.btn_clear_selection.setEnabled(bool(self.selected_marker_id))
        self.btn_delete.setEnabled(bool(self.selected_marker_id))

    def _on_view_selected(self):
        if self.selected_marker_id:
            self.view_device_marker_requested.emit(self.selected_marker_id)

    def _on_clear_selection(self):
        self.list_markers.clearSelection()
        self.selected_marker_id = None
        self.btn_view_selected.setEnabled(False)
        self.btn_clear_selection.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.clear_device_marker_selection_requested.emit()

    def _on_delete_clicked(self):
        if self.selected_marker_id:
            self.delete_device_marker_requested.emit(self.selected_marker_id)

    def update_markers(self, markers: list, selected_id: Optional[str] = None):
        """Updates the list of device markers from DeviceMarker domain models or dicts."""
        self.markers = list(markers) if markers else []
        self.list_markers.blockSignals(True)
        self.list_markers.clear()

        selected_item = None
        for m in self.markers:
            if hasattr(m, "id"):
                mid = m.id
                dtype = m.device_type
                lbl = m.label
                sid = m.scan_id
                x, y, z = m.physical_coordinates
            else:
                mid = m.get("id", "")
                dtype = m.get("device_type", "Other")
                lbl = m.get("label", "Device")
                sid = m.get("scan_id", "")
                x = float(m.get("physical_x_mm", 0.0))
                y = float(m.get("physical_y_mm", 0.0))
                z = float(m.get("physical_z_mm", 0.0))

            text = f"• {dtype}: {lbl}\n  Scan: {sid} | Loc: ({x:.1f}, {y:.1f}, {z:.1f}) mm"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, mid)
            self.list_markers.addItem(item)
            if selected_id and mid == selected_id:
                selected_item = item

        self.list_markers.blockSignals(False)

        count = len(self.markers)
        if count == 0:
            self.lbl_header_status.setText("No device markers")
        else:
            self.lbl_header_status.setText(f"{count} device{'s' if count != 1 else ''} marked")

        if selected_item:
            self.list_markers.setCurrentItem(selected_item)
            self.selected_marker_id = selected_id
            self.btn_view_selected.setEnabled(True)
            self.btn_clear_selection.setEnabled(True)
            self.btn_delete.setEnabled(True)
        else:
            self.selected_marker_id = None
            self.btn_view_selected.setEnabled(False)
            self.btn_clear_selection.setEnabled(False)
            self.btn_delete.setEnabled(False)

    def update_card(self, manager):
        """Helper to update card from a DeviceMarkerManager instance."""
        if manager is None:
            self.reset_ui()
            return
        markers = manager.get_markers_for_active_scan()
        self.update_markers(markers, manager.selected_marker_id)

    def reset_ui(self):
        self.markers = []
        self.selected_marker_id = None
        self.list_markers.clear()
        self.lbl_header_status.setText("No device markers")
        self.txt_label.clear()
        self.btn_view_selected.setEnabled(False)
        self.btn_clear_selection.setEnabled(False)
        self.btn_delete.setEnabled(False)


class QuickHandoffCard(QFrame):
    """ICU Feature 7: Quick Handoff card.

    Provides a compact, deterministic review context tool that helps one clinician rapidly
    communicate the current imaging-review workspace context to another clinician.

    Zero clinical interpretation:
    - Never generates diagnosis, recommendations, treatment instructions, or claims of improvement/deterioration.
    - Purely summarizes existing application state.
    """
    generate_handoff_requested = pyqtSignal()
    copy_handoff_requested = pyqtSignal()
    view_handoff_requested = pyqtSignal()
    clear_handoff_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("QuickHandoffCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            #QuickHandoffCard {
                background: #141414;
                border: 1px solid #2a2a2a;
                border-radius: 6px;
            }
            QLabel {
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)
        self.handoff_snapshot = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # 1. Header with title and status
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(6)
        self.lbl_title = QLabel("<b>Quick Handoff</b>")
        self.lbl_title.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: bold;")
        hdr_layout.addWidget(self.lbl_title)
        hdr_layout.addStretch(1)

        self.lbl_header_status = QLabel("No handoff generated")
        self.lbl_header_status.setStyleSheet("color: #888888; font-size: 10px;")
        hdr_layout.addWidget(self.lbl_header_status)
        layout.addLayout(hdr_layout)

        # 2. Preview Box (Compact review context)
        self.preview_box = QFrame()
        self.preview_box.setStyleSheet("background: #111111; border: 1px solid #222222; border-radius: 3px; padding: 4px;")
        p_layout = QVBoxLayout(self.preview_box)
        p_layout.setContentsMargins(4, 4, 4, 4)
        p_layout.setSpacing(2)

        self.lbl_patient = QLabel("Patient: None")
        self.lbl_patient.setStyleSheet("color: #cccccc; font-size: 9px;")
        p_layout.addWidget(self.lbl_patient)

        self.lbl_scans = QLabel("Current: None | Previous: None")
        self.lbl_scans.setStyleSheet("color: #aaaaaa; font-size: 9px;")
        p_layout.addWidget(self.lbl_scans)

        self.lbl_context_hdr = QLabel("<b>Review Context:</b>")
        self.lbl_context_hdr.setStyleSheet("color: #00e5ff; font-size: 9px; margin-top: 2px;")
        p_layout.addWidget(self.lbl_context_hdr)

        self.lbl_diff_review = QLabel("• Difference Review: Inactive")
        self.lbl_diff_review.setStyleSheet("color: #888888; font-size: 9px;")
        p_layout.addWidget(self.lbl_diff_review)

        self.lbl_same_loc = QLabel("• Same Location: None")
        self.lbl_same_loc.setStyleSheet("color: #888888; font-size: 9px;")
        p_layout.addWidget(self.lbl_same_loc)

        self.lbl_meas = QLabel("• Tracked Measurements: None recorded")
        self.lbl_meas.setStyleSheet("color: #888888; font-size: 9px;")
        p_layout.addWidget(self.lbl_meas)

        self.lbl_annots = QLabel("• Annotations: None recorded")
        self.lbl_annots.setStyleSheet("color: #888888; font-size: 9px;")
        p_layout.addWidget(self.lbl_annots)

        self.lbl_devices = QLabel("• Device Markers: None recorded")
        self.lbl_devices.setStyleSheet("color: #888888; font-size: 9px;")
        p_layout.addWidget(self.lbl_devices)

        layout.addWidget(self.preview_box)

        # 3. Action Buttons: [ Generate Handoff ] [ Copy Summary ] [ View Summary ] [ Clear ]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.btn_generate = QPushButton("Generate Handoff")
        self.btn_generate.setFixedHeight(22)
        self.btn_generate.setStyleSheet("""
            QPushButton {
                background: #002e3b; color: #00e5ff; border: 1px solid #00e5ff;
                border-radius: 3px; font-size: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #004d61; }
        """)
        self.btn_generate.clicked.connect(self.generate_handoff_requested.emit)
        btn_row.addWidget(self.btn_generate)

        self.btn_copy = QPushButton("Copy Summary")
        self.btn_copy.setFixedHeight(22)
        self.btn_copy.setEnabled(False)
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background: #1c1c1c; color: #ffffff; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #282828; }
            QPushButton:disabled { background: #141414; color: #444444; border-color: #222222; }
        """)
        self.btn_copy.clicked.connect(self.copy_handoff_requested.emit)
        btn_row.addWidget(self.btn_copy)

        self.btn_view = QPushButton("View Summary")
        self.btn_view.setFixedHeight(22)
        self.btn_view.setEnabled(False)
        self.btn_view.setStyleSheet("""
            QPushButton {
                background: #1c1c1c; color: #ffffff; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #282828; }
            QPushButton:disabled { background: #141414; color: #444444; border-color: #222222; }
        """)
        self.btn_view.clicked.connect(self.view_handoff_requested.emit)
        btn_row.addWidget(self.btn_view)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setFixedHeight(22)
        self.btn_clear.setEnabled(False)
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background: #1c1c1c; color: #888888; border: 1px solid #333333;
                border-radius: 3px; font-size: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #282828; color: #ffffff; }
            QPushButton:disabled { background: #141414; color: #444444; border-color: #222222; }
        """)
        self.btn_clear.clicked.connect(self.clear_handoff_requested.emit)
        btn_row.addWidget(self.btn_clear)

        layout.addLayout(btn_row)

    def update_card(self, handoff):
        self.handoff_snapshot = handoff
        if handoff is None:
            self.reset_ui()
            return

        if handoff.is_stale:
            self.lbl_header_status.setText("Handoff snapshot is stale")
            self.lbl_header_status.setStyleSheet("color: #ff9800; font-size: 10px; font-weight: 600;")
        else:
            self.lbl_header_status.setText("Snapshot active")
            self.lbl_header_status.setStyleSheet("color: #7cfc00; font-size: 10px; font-weight: 600;")

        self.lbl_patient.setText(f"Patient: {handoff.patient_display_name or 'Not available'} ({handoff.patient_mrn or '—'})")
        prev_str = handoff.previous_scan_id or "None available"
        self.lbl_scans.setText(f"Current: {handoff.current_scan_id or 'Not available'} | Previous: {prev_str}")

        # Difference review
        diff = handoff.difference_review_summary
        if diff.get("active", False):
            self.lbl_diff_review.setText(f"• Difference Review: Active ({diff.get('changed_voxels', 0):,} voxels, {diff.get('threshold_hu', 50):.0f} HU)")
        elif diff.get("available", False):
            self.lbl_diff_review.setText("• Difference Review: Inactive")
        else:
            self.lbl_diff_review.setText(f"• Difference Review: {diff.get('status', 'Difference map unavailable')}")

        # Same location
        sl = handoff.same_location_summary
        if sl.get("is_valid", False):
            coords = sl.get("coordinates")
            c_str = f"({coords[0]:.1f}, {coords[1]:.1f}, {coords[2]:.1f}) mm" if coords else "Selected"
            self.lbl_same_loc.setText(f"• Same Location: {c_str} ({sl.get('status', 'Approx. Correspondence')})")
        else:
            self.lbl_same_loc.setText(f"• Same Location: None selected")

        # Measurements
        m_items = handoff.measurement_summary.get("items", [])
        self.lbl_meas.setText(f"• Tracked Measurements: {len(m_items)} recorded" if m_items else "• Tracked Measurements: None recorded")

        # Annotations
        a_items = handoff.annotation_summary.get("items", [])
        self.lbl_annots.setText(f"• Annotations: {len(a_items)} recorded" if a_items else "• Annotations: None recorded")

        # Devices
        d_items = handoff.device_marker_summary.get("items", [])
        self.lbl_devices.setText(f"• Device Markers: {len(d_items)} user-placed" if d_items else "• Device Markers: None recorded")

        self.btn_copy.setEnabled(True)
        self.btn_view.setEnabled(True)
        self.btn_clear.setEnabled(True)

    def mark_stale(self):
        if self.handoff_snapshot:
            self.handoff_snapshot.is_stale = True
            self.lbl_header_status.setText("Handoff snapshot is stale")
            self.lbl_header_status.setStyleSheet("color: #ff9800; font-size: 10px; font-weight: 600;")

    def reset_ui(self):
        self.handoff_snapshot = None
        self.lbl_header_status.setText("No handoff generated")
        self.lbl_header_status.setStyleSheet("color: #888888; font-size: 10px;")
        self.lbl_patient.setText("Patient: None")
        self.lbl_scans.setText("Current: None | Previous: None")
        self.lbl_diff_review.setText("• Difference Review: Inactive")
        self.lbl_same_loc.setText("• Same Location: None")
        self.lbl_meas.setText("• Tracked Measurements: None recorded")
        self.lbl_annots.setText("• Annotations: None recorded")
        self.lbl_devices.setText("• Device Markers: None recorded")
        self.btn_copy.setEnabled(False)
        self.btn_view.setEnabled(False)
        self.btn_clear.setEnabled(False)


class QuickHandoffDialog(QDialog):
    """Modal dialog displaying the full Quick Handoff summary text."""

    def __init__(self, summary_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quick Handoff — Workspace Summary")
        self.resize(560, 480)
        self.setStyleSheet("background: #141414; color: #ffffff;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(summary_text)
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background: #0d0d0d;
                color: #e0e0e0;
                font-family: monospace;
                font-size: 10px;
                border: 1px solid #2a2a2a;
                border-radius: 4px;
                padding: 6px;
            }
        """)
        layout.addWidget(self.text_edit)

        btn_box = QHBoxLayout()
        btn_box.addStretch(1)

        self.btn_copy = QPushButton("Copy to Clipboard")
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background: #002e3b; color: #00e5ff; border: 1px solid #00e5ff;
                border-radius: 3px; padding: 4px 12px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background: #004d61; }
        """)
        self.btn_copy.clicked.connect(self._copy_to_clipboard)
        btn_box.addWidget(self.btn_copy)

        self.btn_close = QPushButton("Close")
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: #1c1c1c; color: #888888; border: 1px solid #333333;
                border-radius: 3px; padding: 4px 12px; font-size: 11px;
            }
            QPushButton:hover { background: #282828; color: #ffffff; }
        """)
        self.btn_close.clicked.connect(self.accept)
        btn_box.addWidget(self.btn_close)

        layout.addLayout(btn_box)

    def _copy_to_clipboard(self):
        from PyQt6.QtWidgets import QApplication
        cb = QApplication.clipboard()
        if cb:
            cb.setText(self.text_edit.toPlainText())



class BeforeAfterCard(QFrame):


    """OR Feature 8: BEFORE vs AFTER comparison card.

    Allows visual review of imaging changes over time for the same patient.
    Strictly isolated from surgical planning state.
    """
    select_before_requested = pyqtSignal(dict)
    select_after_requested = pyqtSignal(dict)
    start_comparison_requested = pyqtSignal(str)   # display_mode
    exit_comparison_requested = pyqtSignal()
    toggle_comparison_view_requested = pyqtSignal(str)  # "BEFORE" or "AFTER"
    comparison_mode_changed = pyqtSignal(str)
    overlay_opacity_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_patient: dict = {}
        self.patient_scans: list[dict] = []
        self.before_scan: Optional[dict] = None
        self.after_scan: Optional[dict] = None
        self.is_active: bool = False
        self.display_mode: str = "TOGGLE"
        self.current_view: str = "AFTER"

        self.setObjectName("BeforeAfterComparisonCard")
        self.setFrameShape(QFrame.Shape.Box)
        self.setStyleSheet("""
            #BeforeAfterComparisonCard {
                background: #161616;
                border: 1px solid #242424;
                border-radius: 6px;
                padding: 6px 8px;
            }
            #BeforeAfterComparisonCard:hover {
                border-color: #333333;
            }
            QLabel {
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # 1. Header with title and neutral status
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(6)
        self.lbl_title = QLabel("<b>Before vs After</b>")
        self.lbl_title.setStyleSheet("color: #e0e0e0; font-size: 11px;")
        hdr_layout.addWidget(self.lbl_title)
        hdr_layout.addStretch(1)

        self.lbl_status = QLabel("Comparison Inactive")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
        hdr_layout.addWidget(self.lbl_status)
        layout.addLayout(hdr_layout)

        # 2. Summary text
        self.lbl_summary = QLabel("No comparison selected")
        self.lbl_summary.setStyleSheet("color: #888888; font-size: 10px;")
        layout.addWidget(self.lbl_summary)

        # 3. Scan selection readouts
        self.lbl_before = QLabel("Before: Not selected")
        self.lbl_before.setStyleSheet("color: #aaaaaa; font-size: 10px;")
        layout.addWidget(self.lbl_before)

        self.lbl_after = QLabel("After: Not selected")
        self.lbl_after.setStyleSheet("color: #aaaaaa; font-size: 10px;")
        layout.addWidget(self.lbl_after)

        # 4. Error/Mismatch banner (hidden by default)
        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet("color: #ff5252; font-size: 10px; font-weight: bold;")
        self.lbl_error.setVisible(False)
        self.lbl_error.setWordWrap(True)
        layout.addWidget(self.lbl_error)

        # 5. Selection buttons
        sel_box = QHBoxLayout()
        sel_box.setSpacing(4)
        self.btn_select_before = QPushButton("Select Before")
        self.btn_select_before.setFixedHeight(22)
        self.btn_select_before.setStyleSheet("""
            QPushButton {
                background: #1f1f1f; color: #00e5ff; border: 1px solid #00a0b2;
                border-radius: 2px; font-size: 10px; padding: 0 6px;
            }
            QPushButton:hover { background: #282828; color: #ffffff; }
        """)
        self.btn_select_before.clicked.connect(self._on_select_before)
        sel_box.addWidget(self.btn_select_before)

        self.btn_select_after = QPushButton("Select After")
        self.btn_select_after.setFixedHeight(22)
        self.btn_select_after.setStyleSheet("""
            QPushButton {
                background: #1f1f1f; color: #7cfc00; border: 1px solid #559900;
                border-radius: 2px; font-size: 10px; padding: 0 6px;
            }
            QPushButton:hover { background: #282828; color: #ffffff; }
        """)
        self.btn_select_after.clicked.connect(self._on_select_after)
        sel_box.addWidget(self.btn_select_after)
        layout.addLayout(sel_box)

        # 6. Mode selector & Opacity controls
        mode_box = QHBoxLayout()
        mode_box.setSpacing(4)
        lbl_mode = QLabel("Mode:")
        lbl_mode.setStyleSheet("color: #888888; font-size: 10px;")
        mode_box.addWidget(lbl_mode)

        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Toggle", "Side-by-Side", "Overlay"])
        self.combo_mode.setFixedHeight(20)
        self.combo_mode.setStyleSheet("""
            QComboBox {
                background: #1c1c1c; color: #e0e0e0; border: 1px solid #333333;
                border-radius: 2px; font-size: 10px; padding: 0 4px;
            }
        """)
        self.combo_mode.currentTextChanged.connect(self._on_mode_changed)
        mode_box.addWidget(self.combo_mode)
        mode_box.addStretch(1)
        layout.addLayout(mode_box)

        # 7. Toggle view control (visible in Toggle mode)
        self.toggle_frame = QFrame()
        tf_layout = QHBoxLayout(self.toggle_frame)
        tf_layout.setContentsMargins(0, 0, 0, 0)
        tf_layout.setSpacing(4)

        self.btn_toggle = QPushButton("Toggle Before / After")
        self.btn_toggle.setFixedHeight(22)
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                background: #182838; color: #00e5ff; border: 1px solid #00a0b2;
                border-radius: 2px; font-size: 10px; font-weight: bold; padding: 0 6px;
            }
            QPushButton:hover { background: #20354b; color: #ffffff; }
        """)
        self.btn_toggle.clicked.connect(self._on_toggle)
        tf_layout.addWidget(self.btn_toggle)

        self.lbl_current_view = QLabel("Current View: AFTER")
        self.lbl_current_view.setStyleSheet("color: #7cfc00; font-size: 10px; font-weight: bold;")
        tf_layout.addWidget(self.lbl_current_view)
        tf_layout.addStretch(1)
        layout.addWidget(self.toggle_frame)

        # 8. Overlay control (visible in Overlay mode)
        self.overlay_frame = QFrame()
        of_layout = QVBoxLayout(self.overlay_frame)
        of_layout.setContentsMargins(0, 0, 0, 0)
        of_layout.setSpacing(2)

        self.lbl_overlay_watermark = QLabel("Unregistered Overlay")
        self.lbl_overlay_watermark.setStyleSheet("color: #ffb300; font-size: 10px; font-weight: bold;")
        of_layout.addWidget(self.lbl_overlay_watermark)

        op_box = QHBoxLayout()
        op_box.setSpacing(4)
        lbl_op = QLabel("Opacity:")
        lbl_op.setStyleSheet("color: #888888; font-size: 10px;")
        op_box.addWidget(lbl_op)

        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(0, 100)
        self.slider_opacity.setValue(50)
        self.slider_opacity.setFixedHeight(16)
        self.slider_opacity.valueChanged.connect(self._on_opacity_changed)
        op_box.addWidget(self.slider_opacity)

        self.lbl_opacity_val = QLabel("50%")
        self.lbl_opacity_val.setStyleSheet("color: #aaaaaa; font-size: 10px;")
        op_box.addWidget(self.lbl_opacity_val)
        of_layout.addLayout(op_box)
        self.overlay_frame.setVisible(False)
        layout.addWidget(self.overlay_frame)

        # 9. Compare / Exit Action buttons
        act_box = QHBoxLayout()
        act_box.setSpacing(4)
        self.btn_compare = QPushButton("Compare")
        self.btn_compare.setFixedHeight(22)
        self.btn_compare.setEnabled(False)
        self.btn_compare.setStyleSheet("""
            QPushButton {
                background: #102610; color: #7cfc00; border: 1px solid #7cfc00;
                border-radius: 2px; font-size: 10px; font-weight: bold; padding: 0 8px;
            }
            QPushButton:hover:enabled { background: #1b401b; color: #ffffff; }
            QPushButton:disabled { background: #181818; color: #555555; border-color: #333333; }
        """)
        self.btn_compare.clicked.connect(self._on_compare)
        act_box.addWidget(self.btn_compare)

        self.btn_exit = QPushButton("Exit Comparison")
        self.btn_exit.setFixedHeight(22)
        self.btn_exit.setEnabled(False)
        self.btn_exit.setStyleSheet("""
            QPushButton {
                background: #242424; color: #aaaaaa; border: 1px solid #383838;
                border-radius: 2px; font-size: 10px; padding: 0 8px;
            }
            QPushButton:hover:enabled { background: #2f2f2f; color: #ffffff; }
            QPushButton:disabled { background: #181818; color: #555555; border-color: #333333; }
        """)
        self.btn_exit.clicked.connect(self._on_exit)
        act_box.addWidget(self.btn_exit)
        act_box.addStretch(1)
        layout.addLayout(act_box)

    def on_patient_changed(self, patient: dict, scan: dict = None):
        """Clears existing comparison state when patient changes and updates available scans."""
        new_mrn = str(patient.get("mrn", "") if patient else "").strip()
        curr_mrn = str(self.current_patient.get("mrn", "") if self.current_patient else "").strip()
        
        self.current_patient = dict(patient) if patient else {}
        if new_mrn != curr_mrn or not patient:
            self.reset_comparison()

        if new_mrn:
            self.patient_scans = get_scans_for_ui(new_mrn)
        else:
            self.patient_scans = []

    def set_before_scan(self, scan: dict):
        """Sets Before scan with same-patient verification."""
        self.lbl_error.setVisible(False)
        p_mrn = str(self.current_patient.get("mrn", "")).strip()
        scan_mrn = str(scan.get("patient_mrn", "") or scan.get("mrn", "")).strip() or p_mrn

        if self.after_scan:
            after_mrn = str(self.after_scan.get("patient_mrn", "") or self.after_scan.get("mrn", "")).strip() or p_mrn
            if scan_mrn and after_mrn and scan_mrn != after_mrn:
                self.lbl_error.setText("Before and After scans must belong to the same patient")
                self.lbl_error.setVisible(True)
                self.btn_compare.setEnabled(False)
                return False

        self.before_scan = dict(scan) if scan else None
        modality = scan.get("type", "CT")
        date = scan.get("date", "Unknown Date")
        desc = scan.get("description", "") or f"{scan.get('slice_count', 1)} slices"
        self.lbl_before.setText(f"Before: {modality} {date} ({desc})")
        self._check_ready()
        self.select_before_requested.emit(self.before_scan)
        return True

    def set_after_scan(self, scan: dict):
        """Sets After scan with same-patient verification."""
        self.lbl_error.setVisible(False)
        p_mrn = str(self.current_patient.get("mrn", "")).strip()
        scan_mrn = str(scan.get("patient_mrn", "") or scan.get("mrn", "")).strip() or p_mrn

        if self.before_scan:
            before_mrn = str(self.before_scan.get("patient_mrn", "") or self.before_scan.get("mrn", "")).strip() or p_mrn
            if scan_mrn and before_mrn and scan_mrn != before_mrn:
                self.lbl_error.setText("Before and After scans must belong to the same patient")
                self.lbl_error.setVisible(True)
                self.btn_compare.setEnabled(False)
                return False

        self.after_scan = dict(scan) if scan else None
        modality = scan.get("type", "CT")
        date = scan.get("date", "Unknown Date")
        desc = scan.get("description", "") or f"{scan.get('slice_count', 1)} slices"
        self.lbl_after.setText(f"After: {modality} {date} ({desc})")
        self._check_ready()
        self.select_after_requested.emit(self.after_scan)
        return True

    def _check_ready(self):
        if self.before_scan and self.after_scan:
            self.btn_compare.setEnabled(True)
            self.lbl_summary.setText("Ready for comparison")
        else:
            self.btn_compare.setEnabled(False)
            self.lbl_summary.setText("Select both scans to compare")

    def _on_select_before(self):
        scans = self.patient_scans or (get_scans_for_ui(self.current_patient.get("mrn", "")) if self.current_patient else [])
        if not scans:
            self.lbl_error.setText("No scans available for this patient")
            self.lbl_error.setVisible(True)
            return
        dlg = ScanSelectionDialog("Select Before Scan", scans, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected = dlg.get_selected_scan()
            if selected:
                self.set_before_scan(selected)

    def _on_select_after(self):
        scans = self.patient_scans or (get_scans_for_ui(self.current_patient.get("mrn", "")) if self.current_patient else [])
        if not scans:
            self.lbl_error.setText("No scans available for this patient")
            self.lbl_error.setVisible(True)
            return
        dlg = ScanSelectionDialog("Select After Scan", scans, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected = dlg.get_selected_scan()
            if selected:
                self.set_after_scan(selected)

    def _on_compare(self):
        if not self.before_scan or not self.after_scan:
            return
        mode_map = {"Toggle": "TOGGLE", "Side-by-Side": "SIDE_BY_SIDE", "Overlay": "OVERLAY"}
        mode = mode_map.get(self.combo_mode.currentText(), "TOGGLE")
        self.is_active = True
        self.display_mode = mode
        self.lbl_status.setText("Comparison Active")
        self.lbl_status.setStyleSheet("color: #7cfc00; font-size: 10px; font-weight: bold;")
        self.btn_compare.setEnabled(False)
        self.btn_exit.setEnabled(True)
        self.start_comparison_requested.emit(mode)

    def _on_exit(self):
        self.is_active = False
        self.lbl_status.setText("Comparison Inactive")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
        self.btn_compare.setEnabled(bool(self.before_scan and self.after_scan))
        self.btn_exit.setEnabled(False)
        self.exit_comparison_requested.emit()

    def _on_toggle(self):
        if self.current_view == "BEFORE":
            self.current_view = "AFTER"
            self.lbl_current_view.setText("Current View: AFTER")
            self.lbl_current_view.setStyleSheet("color: #7cfc00; font-size: 10px; font-weight: bold;")
        else:
            self.current_view = "BEFORE"
            self.lbl_current_view.setText("Current View: BEFORE")
            self.lbl_current_view.setStyleSheet("color: #00e5ff; font-size: 10px; font-weight: bold;")
        self.toggle_comparison_view_requested.emit(self.current_view)

    def _on_mode_changed(self, text: str):
        mode_map = {"Toggle": "TOGGLE", "Side-by-Side": "SIDE_BY_SIDE", "Overlay": "OVERLAY"}
        mode = mode_map.get(text, "TOGGLE")
        self.display_mode = mode
        self.toggle_frame.setVisible(mode == "TOGGLE")
        self.overlay_frame.setVisible(mode == "OVERLAY")
        if self.is_active:
            self.comparison_mode_changed.emit(mode)

    def _on_opacity_changed(self, val: int):
        opacity = val / 100.0
        self.lbl_opacity_val.setText(f"{val}%")
        self.overlay_opacity_changed.emit(opacity)

    def reset_comparison(self):
        """Resets comparison card state to inactive."""
        self.before_scan = None
        self.after_scan = None
        self.is_active = False
        self.current_view = "AFTER"
        self.lbl_status.setText("Comparison Inactive")
        self.lbl_status.setStyleSheet("color: #888888; font-size: 10px;")
        self.lbl_summary.setText("No comparison selected")
        self.lbl_before.setText("Before: Not selected")
        self.lbl_after.setText("After: Not selected")
        self.lbl_error.setText("")
        self.lbl_error.setVisible(False)
        self.lbl_current_view.setText("Current View: AFTER")
        self.lbl_current_view.setStyleSheet("color: #7cfc00; font-size: 10px; font-weight: bold;")
        self.btn_compare.setEnabled(False)
        self.btn_exit.setEnabled(False)


class QuickSurgicalViewsCard(QFrame):
    """OR Feature 9: QUICK SURGICAL VIEWS card.

    Compact card with 6 view presets for reviewing the existing surgical plan:
    ENTRY, TARGET, ROUTE, STRUCTURES, INSTRUMENT, FULL PLAN.
    Buttons are enabled only when corresponding planning geometry exists.
    """
    quick_view_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_preset: Optional[str] = None
        self._init_ui()

    def _init_ui(self):
        self.setFrameShape(QFrame.Shape.Box)
        self.setStyleSheet("""
            QFrame {
                background: #161616;
                border: 1px solid #242424;
                border-radius: 4px;
                padding: 6px 8px;
            }
            QFrame:hover {
                border-color: #333333;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # Header with title and active preset indicator
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(6)
        self.lbl_title = QLabel("<b>Quick Surgical Views</b>")
        self.lbl_title.setStyleSheet("color: #e0e0e0; font-size: 11px;")
        hdr_layout.addWidget(self.lbl_title)
        hdr_layout.addStretch(1)

        self.lbl_active_preset = QLabel("View: None")
        self.lbl_active_preset.setStyleSheet("color: #7cfc00; font-size: 10px;")
        hdr_layout.addWidget(self.lbl_active_preset)
        layout.addLayout(hdr_layout)

        # 2x3 Grid of Preset Buttons
        btn_grid = QGridLayout()
        btn_grid.setSpacing(4)

        btn_style = """
            QPushButton {
                background: #242424;
                color: #e0e0e0;
                border: 1px solid #333333;
                border-radius: 3px;
                padding: 4px 6px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:hover:!disabled {
                background: #2e2e2e;
                border-color: #7cfc00;
                color: #ffffff;
            }
            QPushButton:pressed:!disabled {
                background: #383838;
            }
            QPushButton:disabled {
                background: #181818;
                color: #555555;
                border-color: #222222;
            }
        """

        self.btn_entry = QPushButton("ENTRY")
        self.btn_target = QPushButton("TARGET")
        self.btn_route = QPushButton("ROUTE")
        self.btn_structures = QPushButton("STRUCTURES")
        self.btn_instrument = QPushButton("INSTRUMENT")
        self.btn_full_plan = QPushButton("FULL PLAN")

        self.preset_buttons = {
            "ENTRY": self.btn_entry,
            "TARGET": self.btn_target,
            "ROUTE": self.btn_route,
            "STRUCTURES": self.btn_structures,
            "INSTRUMENT": self.btn_instrument,
            "FULL PLAN": self.btn_full_plan,
        }

        for btn in self.preset_buttons.values():
            btn.setStyleSheet(btn_style)
            btn.setEnabled(False)

        self.btn_entry.clicked.connect(lambda: self._on_preset_clicked("ENTRY"))
        self.btn_target.clicked.connect(lambda: self._on_preset_clicked("TARGET"))
        self.btn_route.clicked.connect(lambda: self._on_preset_clicked("ROUTE"))
        self.btn_structures.clicked.connect(lambda: self._on_preset_clicked("STRUCTURES"))
        self.btn_instrument.clicked.connect(lambda: self._on_preset_clicked("INSTRUMENT"))
        self.btn_full_plan.clicked.connect(lambda: self._on_preset_clicked("FULL PLAN"))

        btn_grid.addWidget(self.btn_entry, 0, 0)
        btn_grid.addWidget(self.btn_target, 0, 1)
        btn_grid.addWidget(self.btn_route, 1, 0)
        btn_grid.addWidget(self.btn_structures, 1, 1)
        btn_grid.addWidget(self.btn_instrument, 2, 0)
        btn_grid.addWidget(self.btn_full_plan, 2, 1)

        layout.addLayout(btn_grid)

    def _on_preset_clicked(self, preset: str):
        self.set_active_preset(preset)
        self.quick_view_requested.emit(preset)

    def set_active_preset(self, preset: Optional[str]):
        self.active_preset = preset
        if preset:
            self.lbl_active_preset.setText(f"View: {preset.upper()}")
        else:
            self.lbl_active_preset.setText("View: None")

    def update_availability(self, plan_or_dict):
        if isinstance(plan_or_dict, dict):
            avail = plan_or_dict
        elif plan_or_dict is not None:
            has_entry = plan_or_dict.has_entry()
            has_target = plan_or_dict.has_target()
            has_route = plan_or_dict.has_planned_route()
            has_structures = len(plan_or_dict.get_avoid_structures()) > 0
            has_instrument = (
                (plan_or_dict.has_virtual_instrument() and getattr(plan_or_dict, "instrument_visible", False)) or
                (plan_or_dict.has_live_deviation() and getattr(plan_or_dict, "deviation_visible", False))
            )
            has_any = has_entry or has_target or has_structures or has_instrument
            avail = {
                "ENTRY": has_entry,
                "TARGET": has_target,
                "ROUTE": has_route,
                "STRUCTURES": has_structures,
                "INSTRUMENT": has_instrument,
                "FULL PLAN": has_any,
            }
        else:
            avail = {k: False for k in self.preset_buttons}

        for preset, btn in self.preset_buttons.items():
            btn.setEnabled(bool(avail.get(preset, False)))


class WorkflowRailItem(QFrame):
    """Touch-friendly workflow rail item with number, title, badge/status, and active state."""
    clicked = pyqtSignal(int)

    def __init__(self, index: int, step_num: str, title: str, status_text: str = "●", parent=None):
        super().__init__(parent)
        self.index = index
        self.is_active = False
        self._accent_color = "#00e5ff"
        self.setFixedHeight(46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        # Step Number (e.g., "01", "02")
        self.lbl_num = QLabel(step_num)
        self.lbl_num.setStyleSheet("color: #586069; font-size: 11px; font-weight: 700; font-family: monospace;")
        self.lbl_num.setFixedWidth(22)
        layout.addWidget(self.lbl_num)

        # Title Label (e.g., "Current vs Previous Scan")
        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("color: #c9d1d9; font-size: 12px; font-weight: 600;")
        layout.addWidget(self.lbl_title, stretch=1)

        # Status / Badge Label (e.g., "●", "2", "ON", "—")
        self.lbl_status = QLabel(status_text)
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet("""
            color: #8b949e; font-size: 10px; font-weight: bold;
            background: #161b22; border-radius: 3px; padding: 2px 6px;
        """)
        layout.addWidget(self.lbl_status)

        self._update_style()

    def set_active(self, active: bool, accent_color: str = None):
        self.is_active = active
        if accent_color:
            self._accent_color = accent_color
        self._update_style()

    def set_status(self, text: str, color: str = None, bg_color: str = None):
        self.lbl_status.setText(text)
        style = "font-size: 10px; font-weight: bold; border-radius: 3px; padding: 2px 6px;"
        if color:
            style += f" color: {color};"
        else:
            style += " color: #8b949e;"
        if bg_color:
            style += f" background: {bg_color};"
        else:
            style += " background: #161b22;"
        self.lbl_status.setStyleSheet(style)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)

    def _update_style(self):
        accent = self._accent_color
        if self.is_active:
            self.setStyleSheet(f"""
                WorkflowRailItem {{
                    background: #161b22;
                    border: 1px solid {accent};
                    border-left: 3px solid {accent};
                    border-radius: 4px;
                }}
            """)
            self.lbl_num.setStyleSheet(f"color: {accent}; font-size: 11px; font-weight: 700; font-family: monospace;")
            self.lbl_title.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: bold;")
        else:
            self.setStyleSheet("""
                WorkflowRailItem {{
                    background: transparent;
                    border: 1px solid transparent;
                    border-radius: 4px;
                }}
                WorkflowRailItem:hover {{
                    background: #161b22;
                    border: 1px solid #30363d;
                }}
            """)
            self.lbl_num.setStyleSheet("color: #586069; font-size: 11px; font-weight: 700; font-family: monospace;")
            self.lbl_title.setStyleSheet("color: #c9d1d9; font-size: 12px; font-weight: 600;")


class OrIcuMode(QWidget):
    # Signal emitted when clinician chooses to open/return to 3D Viewer
    open_in_viewer_requested = pyqtSignal(dict, dict)  # (patient, scan)

    # Surgical planning signals forwarded from cards
    set_entry_requested = pyqtSignal()
    set_target_requested = pyqtSignal()
    view_entry_requested = pyqtSignal()
    view_target_requested = pyqtSignal()
    clear_entry_requested = pyqtSignal()
    clear_target_requested = pyqtSignal()
    clear_both_requested = pyqtSignal()
    view_route_requested = pyqtSignal()
    clear_route_requested = pyqtSignal()
    add_structure_requested = pyqtSignal()
    view_structure_requested = pyqtSignal(str)   # structure_id
    remove_structure_requested = pyqtSignal(str) # structure_id
    clear_structures_requested = pyqtSignal()
    corridor_visibility_changed = pyqtSignal(bool)
    corridor_radius_changed = pyqtSignal(float)
    instrument_visibility_changed = pyqtSignal(bool)
    instrument_depth_changed = pyqtSignal(float)
    instrument_diameter_changed = pyqtSignal(float)
    view_instrument_requested = pyqtSignal()
    deviation_visibility_changed = pyqtSignal(bool)
    deviation_offsets_changed = pyqtSignal(float, float, float)
    deviation_angles_changed = pyqtSignal(float, float)
    reset_deviation_requested = pyqtSignal()
    save_plan_requested = pyqtSignal()
    save_as_new_requested = pyqtSignal()
    restore_plan_requested = pyqtSignal(str)     # version_id
    delete_plan_requested = pyqtSignal(str)      # version_id
    rename_plan_requested = pyqtSignal(str, str) # version_id, new_name

    # Before vs After comparison signals
    select_before_requested = pyqtSignal(dict)
    select_after_requested = pyqtSignal(dict)
    start_comparison_requested = pyqtSignal(str)   # display_mode
    exit_comparison_requested = pyqtSignal()
    toggle_comparison_view_requested = pyqtSignal(str)  # "BEFORE" or "AFTER"
    comparison_mode_changed = pyqtSignal(str)
    overlay_opacity_changed = pyqtSignal(float)

    # Quick surgical views signals (OR Mode: Feature 9)
    quick_view_requested = pyqtSignal(str)

    # ICU Feature 1: Current vs Previous Scan signals
    icu_view_previous_requested = pyqtSignal(dict, dict)
    icu_view_current_requested = pyqtSignal()
    icu_toggle_requested = pyqtSignal(dict, dict)
    icu_exit_comparison_requested = pyqtSignal()

    # ICU Feature 2: Same-Location Review signals
    icu_mark_same_location_requested = pyqtSignal()
    icu_view_same_location_current_requested = pyqtSignal()
    icu_view_same_location_previous_requested = pyqtSignal()
    icu_clear_same_location_requested = pyqtSignal()

    # ICU Feature 3: Measurement Tracking signals
    icu_track_measurement_requested = pyqtSignal()
    icu_delete_tracked_measurement_requested = pyqtSignal(str)
    icu_measurement_selection_changed = pyqtSignal(str, str)

    # ICU Feature 4: Annotation Carry-forward signals
    icu_create_annotation_requested = pyqtSignal()
    icu_select_annotation_requested = pyqtSignal(str)
    icu_carry_forward_requested = pyqtSignal(str)
    icu_view_annotation_source_requested = pyqtSignal(str)
    icu_view_annotation_target_requested = pyqtSignal(str)
    icu_delete_annotation_requested = pyqtSignal(str)
    icu_clear_annotation_selection_requested = pyqtSignal()

    # ICU Feature 5: What Changed? signals
    icu_review_differences_requested = pyqtSignal()
    icu_show_current_requested = pyqtSignal()
    icu_show_previous_requested = pyqtSignal()
    icu_difference_threshold_changed = pyqtSignal(float)
    icu_clear_difference_requested = pyqtSignal()

    # ICU Feature 6: Device Markers signals
    icu_mark_device_requested = pyqtSignal(str, str)
    icu_view_device_marker_requested = pyqtSignal(str)
    icu_delete_device_marker_requested = pyqtSignal(str)
    icu_clear_device_marker_selection_requested = pyqtSignal()

    # ICU Feature 7: Quick Handoff signals
    icu_generate_handoff_requested = pyqtSignal()
    icu_copy_handoff_requested = pyqtSignal()
    icu_view_handoff_requested = pyqtSignal()
    icu_clear_handoff_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_patient: dict = {}
        self.current_scan: dict = {}
        self.current_mode: str = "ICU"  # "ICU" or "OR"
        self.surgical_plan = None
        self._active_icu_index: int = 0
        self._active_or_index: int = 0

        self.entry_target_card = None
        self.planned_route_card = None
        self.current_previous_card = None
        self.same_location_card = None
        self.measurement_tracking_card = None
        self.annotation_carry_forward_card = None
        self.what_changed_card = None
        self.device_markers_card = None
        self.structures_to_avoid_card = None
        self.surgical_corridor_card = None
        self.virtual_instrument_card = None
        self.live_deviation_card = None
        self.plan_versions_card = None
        self.before_after_card = None
        self.quick_views_card = None
        self.quick_handoff_card = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.state_stack = QStackedWidget()
        outer.addWidget(self.state_stack)

        # Page 0: Patient Selection State (shown when no patient is currently active)
        self.state_stack.addWidget(self._build_select_state())
        # Page 1: Active ICU / OR Workspace Session
        self.state_stack.addWidget(self._build_session_state())

        self.state_stack.setCurrentIndex(0)

    # ── Public State Handoff ──────────────────────────────────────────────────

    def set_patient_and_scan(self, patient: dict, scan: dict = None):
        """Called by MainWindow to hand off the active patient and scan from 3D Viewer."""
        if not patient:
            self.current_patient = {}
            self.current_scan = {}
            if hasattr(self, "before_after_card") and self.before_after_card:
                self.before_after_card.on_patient_changed({}, {})
            if hasattr(self, "current_previous_card") and self.current_previous_card:
                self.current_previous_card.update_scans({}, {})
            self.state_stack.setCurrentIndex(0)
            return

        self.current_patient = dict(patient)

        # Resolve scan if not provided directly
        if not scan:
            if "_scan" in self.current_patient and self.current_patient["_scan"]:
                scan = self.current_patient["_scan"]
            else:
                scans = get_scans_for_ui(self.current_patient.get("mrn", ""))
                scan = scans[0] if scans else {}
        self.current_scan = dict(scan) if scan else {}

        if hasattr(self, "before_after_card") and self.before_after_card:
            self.before_after_card.on_patient_changed(self.current_patient, self.current_scan)

        if hasattr(self, "current_previous_card") and self.current_previous_card:
            self.current_previous_card.update_scans(self.current_patient, self.current_scan)

        if self.surgical_plan:
            scan_id = (self.current_scan.get("file_path") if self.current_scan else "") or str(self.current_scan.get("id", "") if self.current_scan else "")
            self.surgical_plan.reset_for_scan(scan_id)
            if hasattr(self, "plan_versions_card") and self.plan_versions_card:
                self.plan_versions_card.update_versions(self.surgical_plan, scan_id)
            if hasattr(self, "entry_target_card") and self.entry_target_card:
                self.entry_target_card.update_landmarks(
                    self.surgical_plan.entry_point,
                    self.surgical_plan.target_point
                )

        if hasattr(self, "quick_views_card") and self.quick_views_card:
            self.quick_views_card.set_active_preset(None)
            self.quick_views_card.update_availability(self.surgical_plan)

        if hasattr(self, "quick_handoff_card") and self.quick_handoff_card:
            self.quick_handoff_card.reset_ui()

        self._update_session_ui()
        self.state_stack.setCurrentIndex(1)

    def set_mode(self, mode: str):
        """Switch between ICU Mode and OR Mode without reloading data."""
        if mode not in ("ICU", "OR"):
            return
        self.current_mode = mode
        self._update_mode_appearance()

    def set_surgical_plan(self, plan):
        """Attaches the session surgical plan and synchronizes the OR workflow cards."""
        self.surgical_plan = plan
        if hasattr(self, "entry_target_card") and self.entry_target_card is not None:
            self.entry_target_card.update_landmarks(
                plan.entry_point if plan else None,
                plan.target_point if plan else None
            )
        if hasattr(self, "planned_route_card") and self.planned_route_card is not None:
            self.planned_route_card.update_route(plan)
        if hasattr(self, "structures_to_avoid_card") and self.structures_to_avoid_card is not None:
            self.structures_to_avoid_card.update_structures(plan)
        if hasattr(self, "surgical_corridor_card") and self.surgical_corridor_card is not None:
            self.surgical_corridor_card.update_corridor(plan)
        if hasattr(self, "virtual_instrument_card") and self.virtual_instrument_card is not None:
            self.virtual_instrument_card.update_instrument(plan)
        if hasattr(self, "live_deviation_card") and self.live_deviation_card is not None:
            self.live_deviation_card.update_deviation(plan)
        if hasattr(self, "plan_versions_card") and self.plan_versions_card is not None:
            scan_id = (self.current_scan.get("file_path") if self.current_scan else "") or str(self.current_scan.get("id", "") if self.current_scan else "")
            self.plan_versions_card.update_versions(plan, scan_id)
        if hasattr(self, "quick_views_card") and self.quick_views_card is not None:
            self.quick_views_card.update_availability(plan)

        self._sync_rail_badges()

    # ── Page Builders ─────────────────────────────────────────────────────────

    def _build_select_state(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: #090a0d;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        header = QLabel("<b>AEGIS-TOUCH — WORKSPACE SELECTION</b>")
        header.setStyleSheet("font-size: 16px; color: #00e5ff; font-weight: bold; letter-spacing: 0.5px;")
        layout.addWidget(header)

        sub = QLabel("Select an active clinical case below to enter the ICU / OR workspace.")
        sub.setStyleSheet("font-size: 12px; color: #8b949e;")
        layout.addWidget(sub)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search patient name or MRN...")
        self.search_box.setFixedHeight(38)
        self.search_box.setStyleSheet("""
            QLineEdit {
                background: #161b22; color: #fff; border: 1px solid #30363d;
                border-radius: 4px; padding: 0 12px; font-size: 12px;
            }
            QLineEdit:focus { border-color: #00e5ff; }
        """)
        self.search_box.textChanged.connect(self._filter_patients)
        layout.addWidget(self.search_box)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        self.patient_grid = QGridLayout(scroll_content)
        self.patient_grid.setSpacing(12)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, stretch=1)

        self._populate_patient_grid()
        return page

    def _populate_patient_grid(self):
        while self.patient_grid.count():
            item = self.patient_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        patients = get_patients_for_ui()
        self._all_patient_cards = []
        for index, patient in enumerate(patients):
            card = PatientSelectCard(patient, on_select=self.start_session)
            card._patient = patient
            row, col = divmod(index, 3)
            self.patient_grid.addWidget(card, row, col)
            self._all_patient_cards.append(card)

    def _filter_patients(self, text: str):
        query = text.lower().strip()
        visible_idx = 0
        for card in getattr(self, "_all_patient_cards", []):
            pat = card._patient
            matches = query in pat.get("name", "").lower() or query in pat.get("mrn", "").lower()
            card.setVisible(matches)
            if matches:
                row, col = divmod(visible_idx, 3)
                self.patient_grid.addWidget(card, row, col)
                visible_idx += 1

    def _build_session_state(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: #090a0d;")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 1. Compact Top Bar ────────────────────────────────────────────────
        top_bar = QFrame()
        top_bar.setFixedHeight(50)
        top_bar.setStyleSheet("background: #0d0f14; border-bottom: 1px solid #1c212b;")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(14, 0, 14, 0)
        top_layout.setSpacing(14)

        # Brand block
        brand_block = QVBoxLayout()
        brand_block.setSpacing(0)
        brand_block.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        brand_title = QLabel("AEGIS-TOUCH")
        brand_title.setStyleSheet("color: #00e5ff; font-size: 13px; font-weight: 900; letter-spacing: 1.5px;")
        brand_sub = QLabel("WORKSTATION")
        brand_sub.setStyleSheet("color: #586069; font-size: 8px; font-weight: 700; letter-spacing: 1px;")
        brand_block.addWidget(brand_title)
        brand_block.addWidget(brand_sub)
        top_layout.addLayout(brand_block)

        div1 = QFrame()
        div1.setFrameShape(QFrame.Shape.VLine)
        div1.setStyleSheet("color: #21262d;")
        top_layout.addWidget(div1)

        # Patient Info Block
        patient_block = QVBoxLayout()
        patient_block.setSpacing(1)
        patient_block.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.top_patient_name = QLabel("<b>No Patient Loaded</b>")
        self.top_patient_name.setStyleSheet("font-size: 13px; color: #ffffff;")
        self.top_patient_meta = QLabel("MRN: —  ·  Age/Sex: —")
        self.top_patient_meta.setStyleSheet("font-size: 11px; color: #8b949e;")
        patient_block.addWidget(self.top_patient_name)
        patient_block.addWidget(self.top_patient_meta)
        top_layout.addLayout(patient_block)

        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.VLine)
        div2.setStyleSheet("color: #21262d;")
        top_layout.addWidget(div2)

        # Study Info Block
        study_block = QVBoxLayout()
        study_block.setSpacing(1)
        study_block.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.top_study_desc = QLabel("Study: —")
        self.top_study_desc.setStyleSheet("font-size: 12px; color: #58a6ff; font-weight: 600;")
        self.top_study_meta = QLabel("Modality: —  ·  Date: —  ·  Slices: —")
        self.top_study_meta.setStyleSheet("font-size: 11px; color: #8b949e;")
        study_block.addWidget(self.top_study_desc)
        study_block.addWidget(self.top_study_meta)
        top_layout.addLayout(study_block)

        div3 = QFrame()
        div3.setFrameShape(QFrame.Shape.VLine)
        div3.setStyleSheet("color: #21262d;")
        top_layout.addWidget(div3)

        # Current vs Previous Scan Info
        scan_block = QVBoxLayout()
        scan_block.setSpacing(1)
        scan_block.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.top_scan_info = QLabel("CURRENT: —")
        self.top_scan_info.setStyleSheet("font-size: 11px; color: #c9d1d9; font-weight: 600;")
        self.top_prev_info = QLabel("PREVIOUS: None on file")
        self.top_prev_info.setStyleSheet("font-size: 10px; color: #6e7681;")
        scan_block.addWidget(self.top_scan_info)
        scan_block.addWidget(self.top_prev_info)
        top_layout.addLayout(scan_block)

        top_layout.addStretch(1)

        # Mode Selector Buttons: [ VIEWER ]  [ ICU ]  [ OR ]
        mode_btn_container = QHBoxLayout()
        mode_btn_container.setSpacing(4)

        self.btn_viewer_mode = QPushButton("👁 VIEWER")
        self.btn_viewer_mode.setFixedHeight(32)
        self.btn_viewer_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_viewer_mode.setStyleSheet("""
            QPushButton {
                background: #161b22; color: #c9d1d9; border: 1px solid #30363d;
                border-radius: 4px; padding: 0 12px; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background: #21262d; color: #ffffff; border-color: #58a6ff; }
        """)
        self.btn_viewer_mode.clicked.connect(self._on_open_viewer_clicked)
        mode_btn_container.addWidget(self.btn_viewer_mode)

        self.btn_icu_mode = QPushButton("🏥 ICU")
        self.btn_icu_mode.setFixedHeight(32)
        self.btn_icu_mode.setCheckable(True)
        self.btn_icu_mode.setChecked(True)
        self.btn_icu_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_icu_mode.clicked.connect(lambda: self.set_mode("ICU"))
        mode_btn_container.addWidget(self.btn_icu_mode)

        self.btn_or_mode = QPushButton("🔪 OR")
        self.btn_or_mode.setFixedHeight(32)
        self.btn_or_mode.setCheckable(True)
        self.btn_or_mode.setChecked(False)
        self.btn_or_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_or_mode.clicked.connect(lambda: self.set_mode("OR"))
        mode_btn_container.addWidget(self.btn_or_mode)

        top_layout.addLayout(mode_btn_container)

        div4 = QFrame()
        div4.setFrameShape(QFrame.Shape.VLine)
        div4.setStyleSheet("color: #21262d;")
        top_layout.addWidget(div4)

        # Status Pills
        status_layout = QHBoxLayout()
        status_layout.setSpacing(6)

        self.lbl_voice_status = QLabel("VOICE ●")
        self.lbl_voice_status.setStyleSheet("""
            color: #3fb950; font-size: 10px; font-weight: bold;
            background: #0d2116; border: 1px solid #238636; border-radius: 3px; padding: 2px 6px;
        """)
        status_layout.addWidget(self.lbl_voice_status)

        self.lbl_cam_status = QLabel("CAM ●")
        self.lbl_cam_status.setStyleSheet("""
            color: #3fb950; font-size: 10px; font-weight: bold;
            background: #0d2116; border: 1px solid #238636; border-radius: 3px; padding: 2px 6px;
        """)
        status_layout.addWidget(self.lbl_cam_status)

        self.lbl_sys_status = QLabel("SYS ●")
        self.lbl_sys_status.setStyleSheet("""
            color: #3fb950; font-size: 10px; font-weight: bold;
            background: #0d2116; border: 1px solid #238636; border-radius: 3px; padding: 2px 6px;
        """)
        status_layout.addWidget(self.lbl_sys_status)

        top_layout.addLayout(status_layout)

        # Switch Patient Button
        switch_btn = QPushButton("Switch Patient")
        switch_btn.setFixedHeight(30)
        switch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        switch_btn.setStyleSheet("""
            QPushButton {
                background: #161b22; color: #8b949e; border: 1px solid #30363d;
                border-radius: 4px; padding: 0 10px; font-size: 11px;
            }
            QPushButton:hover { background: #21262d; color: #fff; border-color: #586069; }
        """)
        switch_btn.clicked.connect(self.switch_patient)
        top_layout.addWidget(switch_btn)

        outer.addWidget(top_bar)

        # ── 2. Workspace Body ─────────────────────────────────────────────────
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Collapsible Case Drawer: Patient Details, Scan List, Clinical Notes
        self.left_panel = QWidget()
        self.left_panel.setFixedWidth(240)
        self.left_panel.setVisible(False)  # default collapsed to maximize viewer
        self.left_panel.setStyleSheet("background: #0d0f14; border-right: 1px solid #1c212b;")
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(10)

        lbl_summary_title = QLabel("CLINICAL SUMMARY")
        lbl_summary_title.setStyleSheet("color: #8b949e; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;")
        left_layout.addWidget(lbl_summary_title)

        self.patient_details_card = QFrame()
        self.patient_details_card.setStyleSheet("background: #161b22; border: 1px solid #21262d; border-radius: 4px; padding: 6px;")
        card_layout = QVBoxLayout(self.patient_details_card)
        card_layout.setSpacing(4)
        self.lbl_card_name = QLabel("—")
        self.lbl_card_name.setStyleSheet("color: #fff; font-size: 12px; font-weight: bold;")
        self.lbl_card_mrn = QLabel("MRN: —")
        self.lbl_card_mrn.setStyleSheet("color: #8b949e; font-size: 11px;")
        self.lbl_card_demographics = QLabel("Demographics: —")
        self.lbl_card_demographics.setStyleSheet("color: #8b949e; font-size: 11px;")
        card_layout.addWidget(self.lbl_card_name)
        card_layout.addWidget(self.lbl_card_mrn)
        card_layout.addWidget(self.lbl_card_demographics)
        left_layout.addWidget(self.patient_details_card)

        lbl_scans_title = QLabel("AVAILABLE STUDIES")
        lbl_scans_title.setStyleSheet("color: #8b949e; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;")
        left_layout.addWidget(lbl_scans_title)

        self.scans_scroll = QScrollArea()
        self.scans_scroll.setWidgetResizable(True)
        self.scans_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scans_container = QWidget()
        self.scans_list_layout = QVBoxLayout(self.scans_container)
        self.scans_list_layout.setContentsMargins(0, 0, 0, 0)
        self.scans_list_layout.setSpacing(4)
        self.scans_scroll.setWidget(self.scans_container)
        left_layout.addWidget(self.scans_scroll, stretch=1)

        lbl_notes_title = QLabel("CLINICAL NOTES")
        lbl_notes_title.setStyleSheet("color: #8b949e; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;")
        left_layout.addWidget(lbl_notes_title)

        self.notes_scroll = QScrollArea()
        self.notes_scroll.setWidgetResizable(True)
        self.notes_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.notes_container = QWidget()
        self.notes_layout = QVBoxLayout(self.notes_container)
        self.notes_layout.setContentsMargins(0, 0, 0, 0)
        self.notes_layout.setSpacing(4)
        self.notes_scroll.setWidget(self.notes_container)
        left_layout.addWidget(self.notes_scroll, stretch=1)

        body.addWidget(self.left_panel)

        # Case Drawer Toggle
        self.left_toggle_btn = QPushButton(">")
        self.left_toggle_btn.setToolTip("Toggle Clinical Case Info")
        self.left_toggle_btn.setFixedWidth(18)
        self.left_toggle_btn.setStyleSheet("""
            QPushButton { background: #0b0d11; color: #586069; border: none; font-size: 11px; font-weight: bold; }
            QPushButton:hover { color: #00e5ff; background: #161b22; }
        """)
        self.left_toggle_btn.clicked.connect(self._toggle_left)
        body.addWidget(self.left_toggle_btn)

        # ── Left Workflow Rail Panel ──────────────────────────────────────────
        self.workflow_rail_panel = QWidget()
        self.workflow_rail_panel.setFixedWidth(230)
        self.workflow_rail_panel.setStyleSheet("background: #0d0f14; border-right: 1px solid #1c212b;")
        rail_layout = QVBoxLayout(self.workflow_rail_panel)
        rail_layout.setContentsMargins(10, 12, 10, 12)
        rail_layout.setSpacing(8)

        self.workflow_header = QLabel("ICU WORKFLOW")
        self.workflow_header.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: 800; letter-spacing: 0.8px;")
        rail_layout.addWidget(self.workflow_header)

        self.workflow_scroll = QScrollArea()
        self.workflow_scroll.setWidgetResizable(True)
        self.workflow_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.workflow_scroll.setStyleSheet("QScrollArea { background: transparent; }")

        self.workflow_container = QWidget()
        self.workflow_container.setStyleSheet("background: transparent;")
        self.workflow_items_layout = QVBoxLayout(self.workflow_container)
        self.workflow_items_layout.setContentsMargins(0, 0, 0, 245)  # Extra bottom margin so camera HUD does not block cards
        self.workflow_items_layout.setSpacing(4)

        # Container for ICU Rail Items
        self.icu_rail_container = QWidget()
        self.icu_rail_container.setStyleSheet("background: transparent;")
        self.icu_rail_layout = QVBoxLayout(self.icu_rail_container)
        self.icu_rail_layout.setContentsMargins(0, 0, 0, 0)
        self.icu_rail_layout.setSpacing(4)
        self.workflow_items_layout.addWidget(self.icu_rail_container)

        # Container for OR Rail Items
        self.or_rail_container = QWidget()
        self.or_rail_container.setStyleSheet("background: transparent;")
        self.or_rail_layout = QVBoxLayout(self.or_rail_container)
        self.or_rail_layout.setContentsMargins(0, 0, 0, 0)
        self.or_rail_layout.setSpacing(4)
        self.workflow_items_layout.addWidget(self.or_rail_container)

        self.workflow_items_layout.addStretch(1)
        self.workflow_scroll.setWidget(self.workflow_container)
        rail_layout.addWidget(self.workflow_scroll, stretch=1)

        body.addWidget(self.workflow_rail_panel)

        # ── Center Dominant Main Viewer Workspace ─────────────────────────────
        center_widget = QWidget()
        center_widget.setStyleSheet("background: #040507;")
        center_main_layout = QVBoxLayout(center_widget)
        center_main_layout.setContentsMargins(16, 14, 16, 14)
        center_main_layout.setSpacing(0)

        viewport_frame = QFrame()
        viewport_frame.setStyleSheet("""
            QFrame {
                background: #07090d;
                border: 1px solid #161b22;
                border-radius: 6px;
            }
        """)
        viewport_layout = QVBoxLayout(viewport_frame)
        viewport_layout.setContentsMargins(14, 12, 14, 12)
        viewport_layout.setSpacing(0)

        # HUD Top Overlay (Top-Left: Patient metadata, Top-Right: Study metadata)
        hud_top = QHBoxLayout()
        hud_top.setSpacing(12)

        hud_tl = QVBoxLayout()
        hud_tl.setSpacing(1)
        self.hud_patient_name = QLabel("—")
        self.hud_patient_name.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: bold; font-family: monospace;")
        self.hud_patient_meta = QLabel("MRN: —")
        self.hud_patient_meta.setStyleSheet("color: #8b949e; font-size: 10px; font-family: monospace;")
        hud_tl.addWidget(self.hud_patient_name)
        hud_tl.addWidget(self.hud_patient_meta)
        hud_top.addLayout(hud_tl)

        # Anatomical Marker Top: Superior (S)
        self.lbl_anat_s = QLabel("S")
        self.lbl_anat_s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_anat_s.setStyleSheet("color: #30363d; font-size: 13px; font-weight: bold; font-family: monospace;")
        hud_top.addStretch(1)
        hud_top.addWidget(self.lbl_anat_s)
        hud_top.addStretch(1)

        hud_tr = QVBoxLayout()
        hud_tr.setSpacing(1)
        hud_tr.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.hud_study_desc = QLabel("—")
        self.hud_study_desc.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.hud_study_desc.setStyleSheet("color: #58a6ff; font-size: 11px; font-weight: bold; font-family: monospace;")
        self.hud_study_meta = QLabel("Modality: CT")
        self.hud_study_meta.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.hud_study_meta.setStyleSheet("color: #8b949e; font-size: 10px; font-family: monospace;")
        hud_tr.addWidget(self.hud_study_desc)
        hud_tr.addWidget(self.hud_study_meta)
        hud_top.addLayout(hud_tr)

        viewport_layout.addLayout(hud_top)

        # Viewport Mid Area: Anatomical Left (R) - Center Command Hub - Anatomical Right (L)
        hud_mid = QHBoxLayout()
        hud_mid.setSpacing(0)

        # Anatomical Marker: Right (R)
        self.lbl_anat_r = QLabel("R")
        self.lbl_anat_r.setStyleSheet("color: #30363d; font-size: 13px; font-weight: bold; font-family: monospace;")
        hud_mid.addWidget(self.lbl_anat_r)

        hud_mid.addStretch(1)

        # Center Interactive Command Hub
        self.center_card = QFrame()
        self.center_card.setStyleSheet("""
            QFrame {
                background: rgba(13, 17, 23, 0.9);
                border: 1px solid #21262d;
                border-radius: 8px;
                padding: 24px;
                max-width: 520px;
            }
        """)
        card_inner = QVBoxLayout(self.center_card)
        card_inner.setSpacing(12)
        card_inner.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.center_icon = QLabel("🏥")
        self.center_icon.setStyleSheet("font-size: 32px;")
        self.center_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_inner.addWidget(self.center_icon)

        self.center_title = QLabel("Imaging Review Workspace")
        self.center_title.setStyleSheet("color: #ffffff; font-size: 17px; font-weight: bold;")
        self.center_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_inner.addWidget(self.center_title)

        self.center_desc = QLabel(
            "Detailed slice-by-slice multi-planar reconstruction (MPR) and 3D volume "
            "visualization are handled in the high-performance 3D Viewer."
        )
        self.center_desc.setWordWrap(True)
        self.center_desc.setStyleSheet("color: #8b949e; font-size: 12px; line-height: 1.4;")
        self.center_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_inner.addWidget(self.center_desc)

        # Main Open in 3D Viewer Touch Button (44px min height)
        self.btn_open_in_viewer = QPushButton("🔍 Open in 3D Viewer")
        self.btn_open_in_viewer.setFixedHeight(44)
        self.btn_open_in_viewer.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_in_viewer.setStyleSheet("""
            QPushButton {
                background: #003647; color: #00e5ff; border: 1px solid #00e5ff;
                border-radius: 4px; padding: 0 24px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover {
                background: #004d66; color: #ffffff; border-color: #66efff;
            }
        """)
        self.btn_open_in_viewer.clicked.connect(self._on_open_viewer_clicked)
        card_inner.addWidget(self.btn_open_in_viewer)

        quick_views_layout = QHBoxLayout()
        quick_views_layout.setSpacing(8)
        quick_views_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        for mode_name in ("2D Axial", "MPR Ortho", "3D Volume"):
            btn_q = QPushButton(mode_name)
            btn_q.setFixedHeight(30)
            btn_q.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_q.setStyleSheet("""
                QPushButton {
                    background: #161b22; color: #8b949e; border: 1px solid #30363d;
                    border-radius: 4px; padding: 0 12px; font-size: 11px;
                }
                QPushButton:hover { background: #21262d; color: #c9d1d9; border-color: #58a6ff; }
            """)
            btn_q.clicked.connect(self._on_open_viewer_clicked)
            quick_views_layout.addWidget(btn_q)

        card_inner.addLayout(quick_views_layout)
        hud_mid.addWidget(self.center_card)
        hud_mid.addStretch(1)

        # Anatomical Marker: Left (L)
        self.lbl_anat_l = QLabel("L")
        self.lbl_anat_l.setStyleSheet("color: #30363d; font-size: 13px; font-weight: bold; font-family: monospace;")
        hud_mid.addWidget(self.lbl_anat_l)

        viewport_layout.addLayout(hud_mid, stretch=1)

        # HUD Bottom Overlay (Bottom-Left: W/L info, Bottom-Right: Engine info)
        hud_bot = QHBoxLayout()
        hud_bot.setSpacing(12)

        hud_bl = QVBoxLayout()
        hud_bl.setSpacing(1)
        self.hud_viewport_info = QLabel("WL: 40  WW: 400 (Brain/Soft Tissue)")
        self.hud_viewport_info.setStyleSheet("color: #6e7681; font-size: 10px; font-family: monospace;")
        self.hud_slice_info = QLabel("Thickness: 1.0mm  ·  Matrix: 512×512")
        self.hud_slice_info.setStyleSheet("color: #6e7681; font-size: 10px; font-family: monospace;")
        hud_bl.addWidget(self.hud_viewport_info)
        hud_bl.addWidget(self.hud_slice_info)
        hud_bot.addLayout(hud_bl)

        # Anatomical Marker Bottom: Inferior (I)
        self.lbl_anat_i = QLabel("I")
        self.lbl_anat_i.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_anat_i.setStyleSheet("color: #30363d; font-size: 13px; font-weight: bold; font-family: monospace;")
        hud_bot.addStretch(1)
        hud_bot.addWidget(self.lbl_anat_i)
        hud_bot.addStretch(1)

        hud_br = QVBoxLayout()
        hud_br.setSpacing(1)
        hud_br.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.hud_system_info = QLabel("Aegis-Touch Imaging Engine")
        self.hud_system_info.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.hud_system_info.setStyleSheet("color: #6e7681; font-size: 10px; font-family: monospace;")
        self.hud_render_mode = QLabel("Touch Interface Active")
        self.hud_render_mode.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.hud_render_mode.setStyleSheet("color: #6e7681; font-size: 10px; font-family: monospace;")
        hud_br.addWidget(self.hud_system_info)
        hud_br.addWidget(self.hud_render_mode)
        hud_bot.addLayout(hud_br)

        viewport_layout.addLayout(hud_bot)
        center_main_layout.addWidget(viewport_frame, stretch=1)
        body.addWidget(center_widget, stretch=4)

        # Inspector Toggle Button
        self.right_toggle_btn = QPushButton(">")
        self.right_toggle_btn.setToolTip("Toggle Inspector Panel")
        self.right_toggle_btn.setFixedWidth(18)
        self.right_toggle_btn.setStyleSheet("""
            QPushButton { background: #0b0d11; color: #586069; border: none; font-size: 11px; font-weight: bold; }
            QPushButton:hover { color: #00e5ff; background: #161b22; }
        """)
        self.right_toggle_btn.clicked.connect(self._toggle_right)
        body.addWidget(self.right_toggle_btn)

        # ── Right Inspector Panel ─────────────────────────────────────────────
        self.right_panel = QWidget()
        self.right_panel.setFixedWidth(380)
        self.right_panel.setStyleSheet("background: #0d0f14; border-left: 1px solid #1c212b;")
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(12, 12, 12, 245)  # Bottom margin ensures inspector cards do not get behind Camera HUD
        self.right_layout.setSpacing(8)

        # Inspector Header
        insp_hdr = QHBoxLayout()
        insp_hdr.setSpacing(6)

        self.inspector_breadcrumb = QLabel("ICU / 01")
        self.inspector_breadcrumb.setStyleSheet("color: #586069; font-size: 10px; font-weight: 700; font-family: monospace;")
        insp_hdr.addWidget(self.inspector_breadcrumb)

        insp_hdr.addStretch(1)

        self.inspector_status = QPushButton("● ACTIVE")
        self.inspector_status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.inspector_status.setStyleSheet("""
            QPushButton {
                color: #00e5ff; font-size: 10px; font-weight: bold;
                background: #002e3b; border: 1px solid #00e5ff; border-radius: 3px; padding: 2px 8px;
            }
            QPushButton:hover {
                background: #004357;
            }
        """)
        self.inspector_status.clicked.connect(self._on_inspector_status_clicked)
        insp_hdr.addWidget(self.inspector_status)

        self.right_layout.addLayout(insp_hdr)

        self.inspector_title = QLabel("CURRENT VS PREVIOUS SCAN")
        self.inspector_title.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: 800; letter-spacing: 0.5px;")
        self.right_layout.addWidget(self.inspector_title)

        insp_div = QFrame()
        insp_div.setFrameShape(QFrame.Shape.HLine)
        insp_div.setStyleSheet("color: #21262d; margin-top: 2px; margin-bottom: 4px;")
        self.right_layout.addWidget(insp_div)

        # Inspector Scroll Area hosting the Stack of Cards
        self.inspector_scroll = QScrollArea()
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.inspector_scroll.setStyleSheet("QScrollArea { background: transparent; }")

        self.inspector_stack = QStackedWidget()
        self.inspector_stack.setStyleSheet("QStackedWidget { background: transparent; }")
        self.inspector_scroll.setWidget(self.inspector_stack)
        self.right_layout.addWidget(self.inspector_scroll, stretch=1)

        body.addWidget(self.right_panel)

        outer.addLayout(body, stretch=1)

        # ── 3. Bottom Action Bar ──────────────────────────────────────────────
        bottom_bar = QFrame()
        bottom_bar.setFixedHeight(40)
        bottom_bar.setStyleSheet("background: #0d0f14; border-top: 1px solid #1c212b;")
        bot_layout = QHBoxLayout(bottom_bar)
        bot_layout.setContentsMargins(14, 0, 14, 0)
        bot_layout.setSpacing(12)

        # Left: Current Mode & Feature
        self.bottom_mode_lbl = QLabel("ICU WORKFLOW  ·  01 CURRENT VS PREVIOUS SCAN")
        self.bottom_mode_lbl.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: bold; font-family: monospace;")
        bot_layout.addWidget(self.bottom_mode_lbl)

        bot_div1 = QFrame()
        bot_div1.setFrameShape(QFrame.Shape.VLine)
        bot_div1.setStyleSheet("color: #21262d;")
        bot_layout.addWidget(bot_div1)

        # Center: Contextual Case Info
        self.bottom_context_lbl = QLabel("Patient: —  ·  Study: —")
        self.bottom_context_lbl.setStyleSheet("color: #8b949e; font-size: 11px;")
        bot_layout.addWidget(self.bottom_context_lbl)

        bot_layout.addStretch(1)

        # Right: Quick View Actions
        self.btn_reset_view = QPushButton("⟲ Reset View")
        self.btn_reset_view.setFixedHeight(28)
        self.btn_reset_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_view.setStyleSheet("""
            QPushButton {
                background: #161b22; color: #8b949e; border: 1px solid #30363d;
                border-radius: 4px; padding: 0 10px; font-size: 11px;
            }
            QPushButton:hover { background: #21262d; color: #c9d1d9; border-color: #58a6ff; }
        """)
        self.btn_reset_view.clicked.connect(self._on_open_viewer_clicked)
        bot_layout.addWidget(self.btn_reset_view)

        self.btn_2d_view = QPushButton("2D")
        self.btn_2d_view.setFixedHeight(28)
        self.btn_2d_view.setFixedWidth(40)
        self.btn_2d_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_2d_view.setStyleSheet("""
            QPushButton {
                background: #161b22; color: #8b949e; border: 1px solid #30363d;
                border-radius: 4px; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background: #21262d; color: #00e5ff; border-color: #00e5ff; }
        """)
        self.btn_2d_view.clicked.connect(self._on_open_viewer_clicked)
        bot_layout.addWidget(self.btn_2d_view)

        self.btn_mpr_view = QPushButton("MPR")
        self.btn_mpr_view.setFixedHeight(28)
        self.btn_mpr_view.setFixedWidth(46)
        self.btn_mpr_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mpr_view.setStyleSheet("""
            QPushButton {
                background: #161b22; color: #8b949e; border: 1px solid #30363d;
                border-radius: 4px; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background: #21262d; color: #00e5ff; border-color: #00e5ff; }
        """)
        self.btn_mpr_view.clicked.connect(self._on_open_viewer_clicked)
        bot_layout.addWidget(self.btn_mpr_view)

        self.btn_3d_view = QPushButton("3D")
        self.btn_3d_view.setFixedHeight(28)
        self.btn_3d_view.setFixedWidth(40)
        self.btn_3d_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_3d_view.setStyleSheet("""
            QPushButton {
                background: #161b22; color: #8b949e; border: 1px solid #30363d;
                border-radius: 4px; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background: #21262d; color: #00e5ff; border-color: #00e5ff; }
        """)
        self.btn_3d_view.clicked.connect(self._on_open_viewer_clicked)
        bot_layout.addWidget(self.btn_3d_view)

        outer.addWidget(bottom_bar)

        self._init_workflow_cards()
        self._update_mode_appearance()
        return page

    # ── UI State Update Helpers ───────────────────────────────────────────────

    def _update_session_ui(self):
        """Refreshes all labels and panels with actual data from current_patient and current_scan."""
        pat = self.current_patient or {}
        scan = self.current_scan or {}

        # 1. Top Bar
        name = pat.get("name", "Unknown Patient")
        mrn = pat.get("mrn", "—")
        age = pat.get("age", "Unk")
        sex = pat.get("sex", "U")
        self.top_patient_name.setText(f"<b>{name}</b>")
        self.top_patient_meta.setText(f"MRN: {mrn}  ·  {age} {sex}")

        desc = scan.get("description", scan.get("type", "CT Scan"))
        modality = scan.get("type", "CT")
        date = scan.get("date", "Unknown Date")
        slice_cnt = scan.get("slice_count", pat.get("slice_count", "—"))
        self.top_study_desc.setText(f"Study: {desc}")
        self.top_study_meta.setText(f"Modality: {modality}  ·  Date: {date}  ·  {slice_cnt} slices")

        if hasattr(self, "top_scan_info"):
            self.top_scan_info.setText(f"CURRENT: {modality} ({date})")
        prev_scan = get_previous_scan_for_patient(mrn, scan) if mrn != "—" else None
        if hasattr(self, "top_prev_info"):
            if prev_scan:
                p_date = prev_scan.get("date", "Date Unk")
                p_type = prev_scan.get("type", "Scan")
                self.top_prev_info.setText(f"PREVIOUS: {p_type} ({p_date})")
            else:
                self.top_prev_info.setText("PREVIOUS: None on file")

        # 2. Viewport HUD Overlays
        if hasattr(self, "hud_patient_name"):
            self.hud_patient_name.setText(name)
            self.hud_patient_meta.setText(f"MRN: {mrn}  ·  {age} {sex}")
            self.hud_study_desc.setText(f"{modality}: {desc}")
            self.hud_study_meta.setText(f"Date: {date}  ·  {slice_cnt} slices")

        # 3. Bottom Context
        if hasattr(self, "bottom_context_lbl"):
            self.bottom_context_lbl.setText(f"Patient: {name} ({mrn})  ·  Study: {desc} ({date})")

        # 4. Left Panel Details
        self.lbl_card_name.setText(name)
        self.lbl_card_mrn.setText(f"MRN: {mrn}")
        self.lbl_card_demographics.setText(f"{age} {sex} · {modality}")

        # 5. Left Panel Studies
        while self.scans_list_layout.count():
            item = self.scans_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        scans = get_scans_for_ui(mrn) if mrn != "—" else []
        if not scans and scan:
            scans = [scan]

        for s in scans:
            s_type = s.get("type", "Scan")
            s_date = s.get("date", "")
            btn = QPushButton(f"📁 {s_type} ({s_date})")
            btn.setFixedHeight(28)
            is_active = (s.get("file_path") == scan.get("file_path"))
            btn_style = (
                "QPushButton { background: #002e3b; color: #00e5ff; border: 1px solid #00e5ff; "
                "border-radius: 4px; text-align: left; padding-left: 8px; font-size: 11px; font-weight: 600; }"
                if is_active else
                "QPushButton { background: #161b22; color: #8b949e; border: 1px solid #21262d; "
                "border-radius: 4px; text-align: left; padding-left: 8px; font-size: 11px; }"
                "QPushButton:hover { background: #21262d; color: #fff; border-color: #30363d; }"
            )
            btn.setStyleSheet(btn_style)
            btn.clicked.connect(lambda checked, selected_scan=s: self._select_scan(selected_scan))
            self.scans_list_layout.addWidget(btn)

        if not scans:
            empty_lbl = QLabel("No other studies on file")
            empty_lbl.setStyleSheet("color: #555; font-size: 11px; font-style: italic;")
            self.scans_list_layout.addWidget(empty_lbl)

        # 6. Left Panel Real Notes
        while self.notes_layout.count():
            item = self.notes_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        notes = get_notes_for_patient(mrn) if mrn != "—" else []
        if notes:
            for n in notes:
                note_card = QFrame()
                note_card.setStyleSheet("background: #161b22; border: 1px solid #21262d; border-radius: 4px; padding: 6px;")
                n_layout = QVBoxLayout(note_card)
                n_layout.setSpacing(2)
                lbl_author = QLabel(f"<b>{n.get('author', 'Clinician')}</b> · {n.get('timestamp', '')[:10]}")
                lbl_author.setStyleSheet("color: #00e5ff; font-size: 10px;")
                lbl_content = QLabel(str(n.get("content", "")))
                lbl_content.setWordWrap(True)
                lbl_content.setStyleSheet("color: #c9d1d9; font-size: 11px;")
                n_layout.addWidget(lbl_author)
                n_layout.addWidget(lbl_content)
                self.notes_layout.addWidget(note_card)
        else:
            empty_notes = QLabel("No clinical notes on file.")
            empty_notes.setStyleSheet("color: #555; font-size: 11px; font-style: italic;")
            self.notes_layout.addWidget(empty_notes)

        self._update_mode_appearance()

    def _init_workflow_cards(self):
        """Initializes all ICU and OR workflow cards once to ensure persistent object lifecycles."""
        self.icu_cards_group = []
        self.or_cards_group = []
        self.icu_rail_items = []
        self.or_rail_items = []

        # ── 1. ICU Feature Cards & Rail Items (7 items) ───────────────────────
        icu_defs = [
            ("01", "Current vs Previous Scan", "● READY"),
            ("02", "Same-location Review", "●"),
            ("03", "Measurement Tracking", "—"),
            ("04", "Annotation Carry-forward", "—"),
            ("05", "What Changed?", "—"),
            ("06", "Device Markers", "—"),
            ("07", "Quick Handoff", "—"),
        ]
        for idx, (num, title, status) in enumerate(icu_defs):
            rail_item = WorkflowRailItem(idx, num, title, status)
            rail_item.clicked.connect(self._select_icu_item)
            self.icu_rail_items.append(rail_item)
            self.icu_rail_layout.addWidget(rail_item)

        # Card 0: Current vs Previous Scan
        self.current_previous_card = CurrentPreviousScanCard()
        self.current_previous_card.view_previous_requested.connect(
            lambda: self.icu_view_previous_requested.emit(
                self.current_previous_card.previous_scan or {}, self.current_scan or {}
            )
        )
        self.current_previous_card.view_current_requested.connect(
            self.icu_view_current_requested.emit
        )
        self.current_previous_card.toggle_requested.connect(
            lambda: self.icu_toggle_requested.emit(
                self.current_previous_card.previous_scan or {}, self.current_scan or {}
            )
        )
        self.current_previous_card.exit_comparison_requested.connect(
            self.icu_exit_comparison_requested.emit
        )
        self.icu_cards_group.append(self.current_previous_card)
        self.inspector_stack.addWidget(self.current_previous_card)

        # Card 1: Same-location Review
        self.same_location_card = SameLocationReviewCard()
        self.same_location_card.mark_location_requested.connect(
            self.icu_mark_same_location_requested.emit
        )
        self.same_location_card.view_current_requested.connect(
            self.icu_view_same_location_current_requested.emit
        )
        self.same_location_card.view_previous_requested.connect(
            self.icu_view_same_location_previous_requested.emit
        )
        self.same_location_card.clear_location_requested.connect(
            self.icu_clear_same_location_requested.emit
        )
        self.icu_cards_group.append(self.same_location_card)
        self.inspector_stack.addWidget(self.same_location_card)

        # Card 2: Measurement Tracking
        self.measurement_tracking_card = MeasurementTrackingCard()
        self.measurement_tracking_card.track_measurement_requested.connect(
            self.icu_track_measurement_requested.emit
        )
        self.measurement_tracking_card.delete_tracked_measurement_requested.connect(
            self.icu_delete_tracked_measurement_requested.emit
        )
        self.measurement_tracking_card.selection_changed.connect(
            self.icu_measurement_selection_changed.emit
        )
        self.icu_cards_group.append(self.measurement_tracking_card)
        self.inspector_stack.addWidget(self.measurement_tracking_card)

        # Card 3: Annotation Carry-forward
        self.annotation_carry_forward_card = AnnotationCarryForwardCard()
        self.annotation_carry_forward_card.create_annotation_requested.connect(
            self.icu_create_annotation_requested.emit
        )
        self.annotation_carry_forward_card.select_annotation_requested.connect(
            self.icu_select_annotation_requested.emit
        )
        self.annotation_carry_forward_card.carry_forward_requested.connect(
            self.icu_carry_forward_requested.emit
        )
        self.annotation_carry_forward_card.view_source_requested.connect(
            self.icu_view_annotation_source_requested.emit
        )
        self.annotation_carry_forward_card.view_target_requested.connect(
            self.icu_view_annotation_target_requested.emit
        )
        self.annotation_carry_forward_card.delete_annotation_requested.connect(
            self.icu_delete_annotation_requested.emit
        )
        self.annotation_carry_forward_card.clear_selection_requested.connect(
            self.icu_clear_annotation_selection_requested.emit
        )
        self.icu_cards_group.append(self.annotation_carry_forward_card)
        self.inspector_stack.addWidget(self.annotation_carry_forward_card)

        # Card 4: What Changed?
        self.what_changed_card = WhatChangedCard()
        self.what_changed_card.review_differences_requested.connect(
            self.icu_review_differences_requested.emit
        )
        self.what_changed_card.show_current_requested.connect(
            self.icu_show_current_requested.emit
        )
        self.what_changed_card.show_previous_requested.connect(
            self.icu_show_previous_requested.emit
        )
        self.what_changed_card.difference_threshold_changed.connect(
            self.icu_difference_threshold_changed.emit
        )
        self.what_changed_card.clear_difference_requested.connect(
            self.icu_clear_difference_requested.emit
        )
        self.icu_cards_group.append(self.what_changed_card)
        self.inspector_stack.addWidget(self.what_changed_card)

        # Card 5: Device Markers
        self.device_markers_card = DeviceMarkersCard()
        self.device_markers_card.mark_device_requested.connect(
            self.icu_mark_device_requested.emit
        )
        self.device_markers_card.view_device_marker_requested.connect(
            self.icu_view_device_marker_requested.emit
        )
        self.device_markers_card.delete_device_marker_requested.connect(
            self.icu_delete_device_marker_requested.emit
        )
        self.device_markers_card.clear_device_marker_selection_requested.connect(
            self.icu_clear_device_marker_selection_requested.emit
        )
        self.icu_cards_group.append(self.device_markers_card)
        self.inspector_stack.addWidget(self.device_markers_card)

        # Card 6: Quick Handoff
        self.quick_handoff_card = QuickHandoffCard()
        self.quick_handoff_card.generate_handoff_requested.connect(
            self.icu_generate_handoff_requested.emit
        )
        self.quick_handoff_card.copy_handoff_requested.connect(
            self.icu_copy_handoff_requested.emit
        )
        self.quick_handoff_card.view_handoff_requested.connect(
            self.icu_view_handoff_requested.emit
        )
        self.quick_handoff_card.clear_handoff_requested.connect(
            self.icu_clear_handoff_requested.emit
        )
        self.icu_cards_group.append(self.quick_handoff_card)
        self.inspector_stack.addWidget(self.quick_handoff_card)

        # ── 2. OR Feature Cards & Rail Items (9 items with progression headers)
        or_defs = [
            ("PLAN", [
                ("01", "Entry + Target", "●"),
                ("02", "Planned Route", "—"),
                ("03", "Structures to Avoid", "—"),
            ]),
            ("VERIFY", [
                ("04", "Surgical Corridor", "—"),
            ]),
            ("REHEARSE", [
                ("05", "Virtual Instrument", "—"),
            ]),
            ("MONITOR", [
                ("06", "Live Deviation", "—"),
            ]),
            ("REVIEW", [
                ("07", "Plan Saving / Versions", "—"),
                ("08", "Before vs After", "—"),
                ("09", "Quick Surgical Views", "—"),
            ]),
        ]

        or_item_idx = 0
        for group_name, items in or_defs:
            lbl_grp = QLabel(group_name)
            lbl_grp.setStyleSheet("color: #484f58; font-size: 10px; font-weight: 800; letter-spacing: 1px; padding-top: 6px; padding-bottom: 2px; padding-left: 4px;")
            self.or_rail_layout.addWidget(lbl_grp)
            for num, title, status in items:
                rail_item = WorkflowRailItem(or_item_idx, num, title, status)
                rail_item.clicked.connect(self._select_or_item)
                self.or_rail_items.append(rail_item)
                self.or_rail_layout.addWidget(rail_item)
                or_item_idx += 1

        # Card 7: Entry + Target
        self.entry_target_card = EntryTargetCard()
        self.entry_target_card.set_entry_requested.connect(self.set_entry_requested.emit)
        self.entry_target_card.set_target_requested.connect(self.set_target_requested.emit)
        self.entry_target_card.view_entry_requested.connect(self.view_entry_requested.emit)
        self.entry_target_card.view_target_requested.connect(self.view_target_requested.emit)
        self.entry_target_card.clear_entry_requested.connect(self.clear_entry_requested.emit)
        self.entry_target_card.clear_target_requested.connect(self.clear_target_requested.emit)
        self.entry_target_card.clear_both_requested.connect(self.clear_both_requested.emit)
        self.or_cards_group.append(self.entry_target_card)
        self.inspector_stack.addWidget(self.entry_target_card)

        # Card 8: Planned Route
        self.planned_route_card = PlannedRouteCard()
        self.planned_route_card.view_route_requested.connect(self.view_route_requested.emit)
        self.planned_route_card.clear_route_requested.connect(self.clear_route_requested.emit)
        self.or_cards_group.append(self.planned_route_card)
        self.inspector_stack.addWidget(self.planned_route_card)

        # Card 9: Structures to Avoid
        self.structures_to_avoid_card = StructuresToAvoidCard()
        self.structures_to_avoid_card.add_structure_requested.connect(self.add_structure_requested.emit)
        self.structures_to_avoid_card.view_structure_requested.connect(self.view_structure_requested.emit)
        self.structures_to_avoid_card.remove_structure_requested.connect(self.remove_structure_requested.emit)
        self.structures_to_avoid_card.clear_structures_requested.connect(self.clear_structures_requested.emit)
        self.or_cards_group.append(self.structures_to_avoid_card)
        self.inspector_stack.addWidget(self.structures_to_avoid_card)

        # Card 10: Surgical Corridor
        self.surgical_corridor_card = SurgicalCorridorCard()
        self.surgical_corridor_card.visibility_changed.connect(self._on_corridor_card_visibility)
        self.surgical_corridor_card.radius_changed.connect(self.corridor_radius_changed.emit)
        self.or_cards_group.append(self.surgical_corridor_card)
        self.inspector_stack.addWidget(self.surgical_corridor_card)

        # Card 11: Virtual Instrument
        self.virtual_instrument_card = VirtualInstrumentCard()
        self.virtual_instrument_card.visibility_changed.connect(self._on_instrument_card_visibility)
        self.virtual_instrument_card.depth_changed.connect(self.instrument_depth_changed.emit)
        self.virtual_instrument_card.diameter_changed.connect(self.instrument_diameter_changed.emit)
        self.virtual_instrument_card.view_instrument_requested.connect(self.view_instrument_requested.emit)
        self.or_cards_group.append(self.virtual_instrument_card)
        self.inspector_stack.addWidget(self.virtual_instrument_card)

        # Card 12: Live Deviation
        self.live_deviation_card = LiveDeviationCard()
        self.live_deviation_card.visibility_changed.connect(self._on_deviation_card_visibility)
        self.live_deviation_card.offsets_changed.connect(self.deviation_offsets_changed.emit)
        self.live_deviation_card.angles_changed.connect(self.deviation_angles_changed.emit)
        self.live_deviation_card.reset_deviation_requested.connect(self.reset_deviation_requested.emit)
        self.or_cards_group.append(self.live_deviation_card)
        self.inspector_stack.addWidget(self.live_deviation_card)

        # Card 13: Plan Versions
        self.plan_versions_card = PlanVersionsCard()
        self.plan_versions_card.save_plan_clicked.connect(self.save_plan_requested.emit)
        self.plan_versions_card.save_as_new_clicked.connect(self.save_as_new_requested.emit)
        self.plan_versions_card.restore_plan_clicked.connect(self.restore_plan_requested.emit)
        self.plan_versions_card.delete_plan_clicked.connect(self.delete_plan_requested.emit)
        self.plan_versions_card.rename_plan_clicked.connect(self.rename_plan_requested.emit)
        self.or_cards_group.append(self.plan_versions_card)
        self.inspector_stack.addWidget(self.plan_versions_card)

        # Card 14: Before vs After
        self.before_after_card = BeforeAfterCard()
        self.before_after_card.select_before_requested.connect(self.select_before_requested.emit)
        self.before_after_card.select_after_requested.connect(self.select_after_requested.emit)
        self.before_after_card.start_comparison_requested.connect(self.start_comparison_requested.emit)
        self.before_after_card.exit_comparison_requested.connect(self.exit_comparison_requested.emit)
        self.before_after_card.toggle_comparison_view_requested.connect(self.toggle_comparison_view_requested.emit)
        self.before_after_card.comparison_mode_changed.connect(self.comparison_mode_changed.emit)
        self.before_after_card.overlay_opacity_changed.connect(self.overlay_opacity_changed.emit)
        self.or_cards_group.append(self.before_after_card)
        self.inspector_stack.addWidget(self.before_after_card)

        # Card 15: Quick Surgical Views
        self.quick_views_card = QuickSurgicalViewsCard()
        self.quick_views_card.quick_view_requested.connect(self.quick_view_requested.emit)
        self.or_cards_group.append(self.quick_views_card)
        self.inspector_stack.addWidget(self.quick_views_card)

    def _update_mode_appearance(self):
        """Updates styling and workflow rail/inspector entries for active mode (ICU vs OR)."""
        is_icu = (self.current_mode == "ICU")

        active_icu_style = (
            "QPushButton { background: #002e3b; color: #00e5ff; border: 1px solid #00e5ff; "
            "border-radius: 4px; padding: 0 14px; font-size: 11px; font-weight: bold; }"
        )
        active_or_style = (
            "QPushButton { background: #0d2818; color: #00e676; border: 1px solid #00e676; "
            "border-radius: 4px; padding: 0 14px; font-size: 11px; font-weight: bold; }"
        )
        inactive_btn_style = (
            "QPushButton { background: #161b22; color: #8b949e; border: 1px solid #30363d; "
            "border-radius: 4px; padding: 0 14px; font-size: 11px; }"
            "QPushButton:hover { background: #21262d; color: #c9d1d9; border-color: #58a6ff; }"
        )

        self.btn_icu_mode.setChecked(is_icu)
        self.btn_or_mode.setChecked(not is_icu)
        self.btn_icu_mode.setStyleSheet(active_icu_style if is_icu else inactive_btn_style)
        self.btn_or_mode.setStyleSheet(inactive_btn_style if is_icu else active_or_style)

        # Center Card Configuration
        if is_icu:
            self.center_icon.setText("🏥")
            self.center_title.setText("Imaging Review Workspace")
            self.center_desc.setText(
                "Multi-planar reconstruction (MPR) and 3D volume review for ICU monitoring, "
                "cross-study comparison, and clinical handoff."
            )
            self.workflow_header.setText("ICU WORKFLOW")
            self.workflow_header.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: 800; letter-spacing: 0.8px;")
        else:
            self.center_icon.setText("🔪")
            self.center_title.setText("Surgical Planning Workspace")
            self.center_desc.setText(
                "Trajectory planning, surgical corridor visualization, structures-to-avoid "
                "clearance, and virtual instrument verification."
            )
            self.workflow_header.setText("OR SURGICAL WORKFLOW")
            self.workflow_header.setStyleSheet("color: #00e676; font-size: 11px; font-weight: 800; letter-spacing: 0.8px;")

        # Toggle rail container visibility
        if hasattr(self, "icu_rail_container"):
            self.icu_rail_container.setVisible(is_icu)
        if hasattr(self, "or_rail_container"):
            self.or_rail_container.setVisible(not is_icu)

        # Switch to active rail selection
        if is_icu:
            self._select_icu_item(getattr(self, "_active_icu_index", 0))
        else:
            self._select_or_item(getattr(self, "_active_or_index", 0))

        # Toggle visibility of cards in groups to maintain compatibility
        if hasattr(self, "icu_cards_group"):
            for card in self.icu_cards_group:
                card.setVisible(is_icu)

        if hasattr(self, "or_cards_group"):
            for card in self.or_cards_group:
                card.setVisible(not is_icu)

        if hasattr(self, "workflow_scroll") and self.workflow_scroll:
            self.workflow_scroll.verticalScrollBar().setValue(0)

        # Refresh state on active mode cards
        if is_icu:
            if hasattr(self, "current_previous_card") and self.current_previous_card:
                self.current_previous_card.update_scans(self.current_patient, self.current_scan)
        else:
            if self.surgical_plan:
                if hasattr(self, "entry_target_card") and self.entry_target_card:
                    self.entry_target_card.update_landmarks(
                        self.surgical_plan.entry_point,
                        self.surgical_plan.target_point
                    )
                if hasattr(self, "planned_route_card") and self.planned_route_card:
                    self.planned_route_card.update_route(self.surgical_plan)
                if hasattr(self, "structures_to_avoid_card") and self.structures_to_avoid_card:
                    self.structures_to_avoid_card.update_structures(self.surgical_plan)
                if hasattr(self, "surgical_corridor_card") and self.surgical_corridor_card:
                    self.surgical_corridor_card.update_corridor(self.surgical_plan)
                if hasattr(self, "virtual_instrument_card") and self.virtual_instrument_card:
                    self.virtual_instrument_card.update_instrument(self.surgical_plan)
                if hasattr(self, "live_deviation_card") and self.live_deviation_card:
                    self.live_deviation_card.update_deviation(self.surgical_plan)
                if hasattr(self, "quick_views_card") and self.quick_views_card:
                    self.quick_views_card.update_availability(self.surgical_plan)
            else:
                if hasattr(self, "entry_target_card") and self.entry_target_card:
                    self.entry_target_card.update_landmarks(None, None)
                if hasattr(self, "planned_route_card") and self.planned_route_card:
                    self.planned_route_card.update_route(None)
                if hasattr(self, "structures_to_avoid_card") and self.structures_to_avoid_card:
                    self.structures_to_avoid_card.update_structures(None)
                if hasattr(self, "surgical_corridor_card") and self.surgical_corridor_card:
                    self.surgical_corridor_card.update_corridor(None)
                if hasattr(self, "virtual_instrument_card") and self.virtual_instrument_card:
                    self.virtual_instrument_card.update_instrument(None)
                if hasattr(self, "live_deviation_card") and self.live_deviation_card:
                    self.live_deviation_card.update_deviation(None)
                if hasattr(self, "quick_views_card") and self.quick_views_card:
                    self.quick_views_card.update_availability(None)

            scan_id = (self.current_scan.get("file_path") if self.current_scan else "") or str(self.current_scan.get("id", "") if self.current_scan else "")
            if hasattr(self, "plan_versions_card") and self.plan_versions_card:
                self.plan_versions_card.update_versions(self.surgical_plan, scan_id)
            if hasattr(self, "before_after_card") and self.before_after_card:
                self.before_after_card.on_patient_changed(self.current_patient, self.current_scan)

        self._sync_rail_badges()

    def _select_icu_item(self, idx: int):
        if idx < 0 or idx >= len(self.icu_rail_items):
            return
        self._active_icu_index = idx
        for i, item in enumerate(self.icu_rail_items):
            item.set_active(i == idx, accent_color="#00e5ff")

        icu_titles = [
            ("ICU / 01", "CURRENT VS PREVIOUS SCAN", "● READY"),
            ("ICU / 02", "SAME-LOCATION REVIEW", "● AVAILABLE"),
            ("ICU / 03", "MEASUREMENT TRACKING", "● ACTIVE"),
            ("ICU / 04", "ANNOTATION CARRY-FORWARD", "● ACTIVE"),
            ("ICU / 05", "WHAT CHANGED? (DIFF REVIEW)", "● READY"),
            ("ICU / 06", "DEVICE MARKERS", "● ACTIVE"),
            ("ICU / 07", "QUICK HANDOFF SUMMARY", "● READY"),
        ]
        bc, title, status = icu_titles[idx]
        self.inspector_breadcrumb.setText(bc)
        self.inspector_title.setText(title)
        self.inspector_status.setText(status)
        self.inspector_status.setStyleSheet("""
            color: #00e5ff; font-size: 10px; font-weight: bold;
            background: #002e3b; border: 1px solid #00e5ff; border-radius: 3px; padding: 2px 6px;
        """)
        self.inspector_stack.setCurrentIndex(idx)
        self.bottom_mode_lbl.setText(f"ICU WORKFLOW  ·  {bc.split('/')[-1].strip()} {title}")
        self.bottom_mode_lbl.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: bold; font-family: monospace;")
        if hasattr(self, "inspector_scroll") and self.inspector_scroll:
            self.inspector_scroll.verticalScrollBar().setValue(0)
        self._update_inspector_status_for_current()

    def _select_or_item(self, idx: int):
        if idx < 0 or idx >= len(self.or_rail_items):
            return

        # If user clicks an already active toggleable item (04, 05, 06), toggle it!
        if getattr(self, "_active_or_index", None) == idx and idx in (3, 4, 5):
            self._on_inspector_status_clicked()
            return

        self._active_or_index = idx
        for i, item in enumerate(self.or_rail_items):
            item.set_active(i == idx, accent_color="#00e676")

        or_titles = [
            ("OR / 01", "ENTRY + TARGET LANDMARKS", "● ACTIVE"),
            ("OR / 02", "PLANNED ROUTE TRAJECTORY", "● ACTIVE"),
            ("OR / 03", "STRUCTURES TO AVOID", "● ACTIVE"),
            ("OR / 04", "SURGICAL CORRIDOR", "● ON"),
            ("OR / 05", "VIRTUAL INSTRUMENT", "● OFF"),
            ("OR / 06", "LIVE DEVIATION MONITOR", "● OFF"),
            ("OR / 07", "PLAN SAVING & VERSIONS", "● ACTIVE"),
            ("OR / 08", "BEFORE VS AFTER COMPARISON", "● ACTIVE"),
            ("OR / 09", "QUICK SURGICAL VIEWS", "● ACTIVE"),
        ]
        bc, title, _ = or_titles[idx]
        self.inspector_breadcrumb.setText(bc)
        self.inspector_title.setText(title)
        self.inspector_stack.setCurrentIndex(7 + idx)
        self.bottom_mode_lbl.setText(f"OR SURGICAL WORKFLOW  ·  {bc.split('/')[-1].strip()} {title}")
        self.bottom_mode_lbl.setStyleSheet("color: #00e676; font-size: 11px; font-weight: bold; font-family: monospace;")
        if hasattr(self, "inspector_scroll") and self.inspector_scroll:
            self.inspector_scroll.verticalScrollBar().setValue(0)

        self._update_inspector_status_for_current()

    def _sync_rail_badges(self):
        """Synchronizes status badges on rail items with current card/session states."""
        if not hasattr(self, "icu_rail_items") or not hasattr(self, "or_rail_items"):
            return

        is_icu = (self.current_mode == "ICU")
        if is_icu and len(self.icu_rail_items) >= 7:
            # ICU 0: Current vs Previous
            if hasattr(self, "current_previous_card") and self.current_previous_card and getattr(self.current_previous_card, "previous_scan", None):
                self.icu_rail_items[0].set_status("● READY", "#00e5ff")
            else:
                self.icu_rail_items[0].set_status("—", "#8b949e")

            # ICU 1: Same-location Review
            if hasattr(self, "same_location_card") and self.same_location_card and getattr(self.same_location_card, "active_location", None):
                self.icu_rail_items[1].set_status("● MARKED", "#00e5ff")
            else:
                self.icu_rail_items[1].set_status("—", "#8b949e")

            # ICU 2: Measurement Tracking
            if hasattr(self, "measurement_tracking_card") and self.measurement_tracking_card:
                mc = getattr(self.measurement_tracking_card, "tracked_count", 0)
                self.icu_rail_items[2].set_status(str(mc) if mc else "—", "#00e5ff" if mc else "#8b949e")

            # ICU 3: Annotation Carry-forward
            if hasattr(self, "annotation_carry_forward_card") and self.annotation_carry_forward_card:
                ac = getattr(self.annotation_carry_forward_card, "annotation_count", 0)
                self.icu_rail_items[3].set_status(str(ac) if ac else "—", "#00e5ff" if ac else "#8b949e")

            # ICU 4: What Changed?
            self.icu_rail_items[4].set_status("READY", "#00e5ff")

            # ICU 5: Device Markers
            if hasattr(self, "device_markers_card") and self.device_markers_card:
                dc = len(getattr(self.device_markers_card, "markers", []))
                self.icu_rail_items[5].set_status(str(dc) if dc else "—", "#00e5ff" if dc else "#8b949e")

            # ICU 6: Quick Handoff
            self.icu_rail_items[6].set_status("READY", "#00e5ff")
        elif not is_icu and len(self.or_rail_items) >= 9:
            plan = self.surgical_plan
            # OR 0: Entry + Target
            has_entry = bool(plan and plan.entry_point is not None)
            has_target = bool(plan and plan.target_point is not None)
            if has_entry and has_target:
                self.or_rail_items[0].set_status("● BOTH", "#00e676")
            elif has_entry or has_target:
                self.or_rail_items[0].set_status("● 1/2", "#f59e0b")
            else:
                self.or_rail_items[0].set_status("—", "#8b949e")

            # OR 1: Planned Route
            has_route = bool(plan and (plan.has_planned_route() if hasattr(plan, "has_planned_route") else (plan.entry_point is not None and plan.target_point is not None)))
            self.or_rail_items[1].set_status("● READY" if has_route else "—", "#00e676" if has_route else "#8b949e")

            # OR 2: Structures to Avoid
            struct_count = len(plan.get_avoid_structures()) if (plan and hasattr(plan, "get_avoid_structures")) else 0
            self.or_rail_items[2].set_status(str(struct_count) if struct_count else "0", "#00e676" if struct_count else "#8b949e")

            # OR 3: Surgical Corridor
            corr_vis = bool(plan and getattr(plan, "corridor_enabled", getattr(plan, "corridor_visible", False)))
            self.or_rail_items[3].set_status("ON" if corr_vis else "OFF", "#00e676" if corr_vis else "#8b949e")

            # OR 4: Virtual Instrument
            inst_vis = bool(plan and getattr(plan, "instrument_visible", False))
            self.or_rail_items[4].set_status("ON" if inst_vis else "OFF", "#00e676" if inst_vis else "#8b949e")

            # OR 5: Live Deviation
            dev_vis = bool(plan and getattr(plan, "deviation_visible", False))
            self.or_rail_items[5].set_status("ON" if dev_vis else "OFF", "#00e676" if dev_vis else "#8b949e")

            # OR 6: Plan Versions
            self.or_rail_items[6].set_status("●", "#8b949e")

            # OR 7: Before vs After
            self.or_rail_items[7].set_status("●", "#8b949e")

            # OR 8: Quick Views
            self.or_rail_items[8].set_status("●", "#8b949e")

        self._update_inspector_status_for_current()

    def _set_toggle_button_visual(self, btn: QPushButton, is_on: bool):
        if is_on:
            btn.setText("● ON")
            btn.setStyleSheet("""
                QPushButton {
                    color: #00e676; font-size: 10px; font-weight: bold;
                    background: #0d2818; border: 1px solid #00e676; border-radius: 3px; padding: 2px 8px;
                }
                QPushButton:hover {
                    background: #143d25;
                }
            """)
        else:
            btn.setText("● OFF")
            btn.setStyleSheet("""
                QPushButton {
                    color: #8b949e; font-size: 10px; font-weight: bold;
                    background: #161b22; border: 1px solid #30363d; border-radius: 3px; padding: 2px 8px;
                }
                QPushButton:hover {
                    background: #21262d; color: #c9d1d9; border-color: #58a6ff;
                }
            """)

    def _update_inspector_status_for_current(self):
        """Updates inspector header status button text and style according to active mode and selection."""
        if not hasattr(self, "inspector_status") or self.inspector_status is None:
            return

        if self.current_mode == "ICU":
            idx = getattr(self, "_active_icu_index", 0)
            icu_statuses = ["● READY", "● AVAILABLE", "● ACTIVE", "● ACTIVE", "● READY", "● ACTIVE", "● READY"]
            st = icu_statuses[idx] if idx < len(icu_statuses) else "● ACTIVE"
            self.inspector_status.setText(st)
            self.inspector_status.setStyleSheet("""
                QPushButton {
                    color: #00e5ff; font-size: 10px; font-weight: bold;
                    background: #002e3b; border: 1px solid #00e5ff; border-radius: 3px; padding: 2px 8px;
                }
                QPushButton:hover {
                    background: #004357;
                }
            """)
            return

        # OR Mode
        idx = getattr(self, "_active_or_index", 0)
        plan = self.surgical_plan

        if idx == 3:  # 04 Surgical Corridor
            is_on = bool(plan and getattr(plan, "corridor_enabled", getattr(plan, "corridor_visible", True)))
            self._set_toggle_button_visual(self.inspector_status, is_on)
        elif idx == 4:  # 05 Virtual Instrument
            is_on = bool(plan and getattr(plan, "instrument_visible", False))
            self._set_toggle_button_visual(self.inspector_status, is_on)
        elif idx == 5:  # 06 Live Deviation Monitor
            is_on = bool(plan and getattr(plan, "deviation_visible", False))
            self._set_toggle_button_visual(self.inspector_status, is_on)
        else:
            self.inspector_status.setText("● ACTIVE")
            self.inspector_status.setStyleSheet("""
                QPushButton {
                    color: #00e676; font-size: 10px; font-weight: bold;
                    background: #0d2818; border: 1px solid #00e676; border-radius: 3px; padding: 2px 8px;
                }
                QPushButton:hover {
                    background: #143d25;
                }
            """)

    def _on_inspector_status_clicked(self):
        """Toggles the currently selected OR feature (Corridor, Instrument, Live Deviation)."""
        if self.current_mode != "OR":
            return

        idx = getattr(self, "_active_or_index", 0)
        plan = self.surgical_plan

        if idx == 3:  # 04 Surgical Corridor
            curr = bool(plan and getattr(plan, "corridor_enabled", getattr(plan, "corridor_visible", True)))
            new_val = not curr
            if plan:
                plan.set_corridor_enabled(new_val)
            if hasattr(self, "surgical_corridor_card") and self.surgical_corridor_card:
                self.surgical_corridor_card._corridor_enabled = new_val
                self.surgical_corridor_card.btn_toggle.setText("Hide Corridor" if new_val else "Show Corridor")
            self.corridor_visibility_changed.emit(new_val)

        elif idx == 4:  # 05 Virtual Instrument
            curr = bool(plan and getattr(plan, "instrument_visible", False))
            new_val = not curr
            if plan:
                plan.set_instrument_visible(new_val)
            if hasattr(self, "virtual_instrument_card") and self.virtual_instrument_card:
                self.virtual_instrument_card._instrument_visible = new_val
                self.virtual_instrument_card.btn_toggle.setText("Hide Instrument" if new_val else "Show Instrument")
            self.instrument_visibility_changed.emit(new_val)

        elif idx == 5:  # 06 Live Deviation Monitor
            curr = bool(plan and getattr(plan, "deviation_visible", False))
            new_val = not curr
            if plan:
                plan.set_deviation_visible(new_val)
            if hasattr(self, "live_deviation_card") and self.live_deviation_card:
                self.live_deviation_card._instrument_visible = new_val
                self.live_deviation_card.btn_toggle.setText("Hide Current Instrument" if new_val else "Show Current Instrument")
            self.deviation_visibility_changed.emit(new_val)
        else:
            return

        self._sync_rail_badges()
        self._update_inspector_status_for_current()

    def _on_corridor_card_visibility(self, visible: bool):
        if self.surgical_plan:
            self.surgical_plan.set_corridor_enabled(visible)
        self.corridor_visibility_changed.emit(visible)
        self._sync_rail_badges()
        self._update_inspector_status_for_current()

    def _on_instrument_card_visibility(self, visible: bool):
        if self.surgical_plan:
            self.surgical_plan.set_instrument_visible(visible)
        self.instrument_visibility_changed.emit(visible)
        self._sync_rail_badges()
        self._update_inspector_status_for_current()

    def _on_deviation_card_visibility(self, visible: bool):
        if self.surgical_plan:
            self.surgical_plan.set_deviation_visible(visible)
        self.deviation_visibility_changed.emit(visible)
        self._sync_rail_badges()
        self._update_inspector_status_for_current()

    def _add_workflow_action_card(self, title: str, status_text: str, accent_color: str):
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: #161616;
                border: 1px solid #242424;
                border-radius: 4px;
                padding: 6px 8px;
            }
            QFrame:hover {
                background: #1c1c1c;
                border-color: #333333;
            }
        """)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(2)

        lbl_title = QLabel(f"<b>{title}</b>")
        lbl_title.setStyleSheet("color: #e0e0e0; font-size: 11px;")
        c_layout.addWidget(lbl_title)

        lbl_status = QLabel(status_text)
        lbl_status.setStyleSheet(f"color: {accent_color}; font-size: 10px; opacity: 0.8;")
        c_layout.addWidget(lbl_status)

        self.workflow_items_layout.addWidget(card)

    # ── Actions & Handlers ───────────────────────────────────────────────────

    def _select_scan(self, scan: dict):
        self.current_scan = dict(scan)
        scan_id = (scan.get("file_path") if scan else "") or str(scan.get("id", "") if scan else "")
        if self.surgical_plan:
            self.surgical_plan.reset_for_scan(scan_id)
        if hasattr(self, "plan_versions_card") and self.plan_versions_card:
            self.plan_versions_card.update_versions(self.surgical_plan, scan_id)
        if hasattr(self, "quick_views_card") and self.quick_views_card:
            self.quick_views_card.set_active_preset(None)
            self.quick_views_card.update_availability(self.surgical_plan)
        if hasattr(self, "current_previous_card") and self.current_previous_card:
            self.current_previous_card.update_scans(self.current_patient, self.current_scan)
        self._update_session_ui()

    def _on_open_viewer_clicked(self):
        """Dispatches request to MainWindow to navigate to 3D Viewer with active case."""
        self.open_in_viewer_requested.emit(self.current_patient or {}, self.current_scan or {})

    def _toggle_left(self):
        visible = self.left_panel.isVisible()
        self.left_panel.setVisible(not visible)
        self.left_toggle_btn.setText("<" if not visible else ">")

    def _toggle_right(self):
        visible = self.right_panel.isVisible()
        self.right_panel.setVisible(not visible)
        self.right_toggle_btn.setText("<" if visible else ">")

    def start_session(self, patient: dict):
        """Called when a patient is selected from the select state grid."""
        self.set_patient_and_scan(patient)

    def switch_patient(self):
        """Return to the patient selection grid."""
        self.state_stack.setCurrentIndex(0)


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    from theme import DARK_STYLESHEET

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = OrIcuMode()
    window.resize(1100, 700)
    window.show()

    sys.exit(app.exec())