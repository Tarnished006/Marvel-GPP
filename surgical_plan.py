# surgical_plan.py
"""
Surgical Planning Data Model for Aegis-Touch.

Holds session-level planning landmarks (ENTRY and TARGET) in physical
coordinates (millimeters). Independent of database persistence for Step 2.
"""

from dataclasses import dataclass, field
import time
from typing import Optional


@dataclass
class PlanningPoint:
    """Represents an individual surgical planning landmark (ENTRY or TARGET).

    All coordinates are stored in physical millimeters (mm), calibrated to
    the DICOM volume spacing, rather than discrete slice indices.
    """
    point_type: str  # "ENTRY" or "TARGET"
    x_mm: float
    y_mm: float
    z_mm: float
    source_view: str = "3D"  # "3D", "axial", "coronal", "sagittal", "2D"
    creation_time: float = field(default_factory=time.time)

    @property
    def coordinates(self) -> tuple[float, float, float]:
        """Returns physical coordinates as (X, Y, Z) in mm."""
        return (float(self.x_mm), float(self.y_mm), float(self.z_mm))

    def to_dict(self) -> dict:
        return {
            "point_type": self.point_type,
            "x_mm": float(self.x_mm),
            "y_mm": float(self.y_mm),
            "z_mm": float(self.z_mm),
            "source_view": self.source_view,
            "creation_time": self.creation_time,
        }


@dataclass
class AvoidStructure:
    """Represents an anatomical structure or region to avoid during surgical planning.

    Coordinates and geometric dimensions are stored in physical millimeters (mm).
    """
    structure_id: str
    name: str
    center_x_mm: float
    center_y_mm: float
    center_z_mm: float
    radius_mm: float = 5.0
    source_view: str = "3D"  # "3D", "axial", "coronal", "sagittal", "2D"
    creation_time: float = field(default_factory=time.time)

    @property
    def center(self) -> tuple[float, float, float]:
        """Returns physical coordinates of the structure center as (X, Y, Z) in mm."""
        return (float(self.center_x_mm), float(self.center_y_mm), float(self.center_z_mm))

    def to_dict(self) -> dict:
        return {
            "structure_id": self.structure_id,
            "name": self.name,
            "center_x_mm": float(self.center_x_mm),
            "center_y_mm": float(self.center_y_mm),
            "center_z_mm": float(self.center_z_mm),
            "radius_mm": float(self.radius_mm),
            "source_view": self.source_view,
            "creation_time": self.creation_time,
        }


@dataclass
class PlannedRoute:
    """Represents the geometric planning path between ENTRY and TARGET.

    All metrics are calculated directly from physical coordinates in mm.
    """
    entry_point: PlanningPoint
    target_point: PlanningPoint

    @property
    def delta_x(self) -> float:
        """Delta X in mm (Target - Entry)."""
        return float(self.target_point.x_mm - self.entry_point.x_mm)

    @property
    def delta_y(self) -> float:
        """Delta Y in mm (Target - Entry)."""
        return float(self.target_point.y_mm - self.entry_point.y_mm)

    @property
    def delta_z(self) -> float:
        """Delta Z in mm (Target - Entry)."""
        return float(self.target_point.z_mm - self.entry_point.z_mm)

    @property
    def length_mm(self) -> float:
        """Geometric path length in mm: sqrt(delta_x^2 + delta_y^2 + delta_z^2)."""
        import math
        return float(math.sqrt(self.delta_x ** 2 + self.delta_y ** 2 + self.delta_z ** 2))

    def distance_to_point(self, point: tuple[float, float, float]) -> float:
        """Calculates shortest 3D distance from a physical point (X, Y, Z) in mm to the route line segment."""
        import math
        px, py, pz = point
        ex, ey, ez = self.entry_point.x_mm, self.entry_point.y_mm, self.entry_point.z_mm
        tx, ty, tz = self.target_point.x_mm, self.target_point.y_mm, self.target_point.z_mm

        # Segment vector V = T - E
        vx = tx - ex
        vy = ty - ey
        vz = tz - ez
        seg_len_sq = vx ** 2 + vy ** 2 + vz ** 2

        if seg_len_sq < 1e-9:
            # Entry and Target are virtually identical
            return float(math.sqrt((px - ex) ** 2 + (py - ey) ** 2 + (pz - ez) ** 2))

        # Vector from Entry to Point: W = P - E
        wx = px - ex
        wy = py - ey
        wz = pz - ez

        # Projection parameter t = (W . V) / |V|^2
        t = (wx * vx + wy * vy + wz * vz) / seg_len_sq
        t_clamped = max(0.0, min(1.0, t))

        # Closest point on the segment
        cx = ex + t_clamped * vx
        cy = ey + t_clamped * vy
        cz = ez + t_clamped * vz

        # Distance from P to closest point C
        return float(math.sqrt((px - cx) ** 2 + (py - cy) ** 2 + (pz - cz) ** 2))

    def distance_to_structure(self, structure: AvoidStructure) -> float:
        """Calculates shortest 3D distance from the center of an AvoidStructure to the route line segment in mm."""
        return self.distance_to_point(structure.center)

    def to_dict(self) -> dict:
        return {
            "entry": self.entry_point.to_dict(),
            "target": self.target_point.to_dict(),
            "delta_x": self.delta_x,
            "delta_y": self.delta_y,
            "delta_z": self.delta_z,
            "length_mm": self.length_mm,
        }


@dataclass
class SurgicalCorridor:
    """Represents a software-defined cylindrical corridor around the Planned Route.

    All geometry is derived directly from the canonical Planned Route and stored in
    physical millimeters (mm).
    """
    route: PlannedRoute
    radius_mm: float = 5.0
    is_enabled: bool = True

    @property
    def length_mm(self) -> float:
        """Corridor cylinder length in mm, matching the Planned Route length."""
        return self.route.length_mm

    @property
    def volume_mm3(self) -> float:
        """Derived volume of the cylindrical corridor in mm^3: pi * r^2 * L."""
        import math
        return float(math.pi * (self.radius_mm ** 2) * self.length_mm)

    def to_dict(self) -> dict:
        return {
            "route": self.route.to_dict(),
            "radius_mm": float(self.radius_mm),
            "length_mm": self.length_mm,
            "volume_mm3": self.volume_mm3,
            "is_enabled": bool(self.is_enabled),
        }


@dataclass
class VirtualInstrument:
    """Represents a software-defined geometric virtual instrument along the Planned Route.

    All geometry is derived directly from the canonical Planned Route and physical coordinates (mm).
    Parameter t in [0.0, 1.0]:
      0.0 = Entry
      1.0 = Target
      Tip(t) = Entry + t * (Target - Entry)
      Insertion Depth = t * route_length
    """
    route: PlannedRoute
    insertion_depth_mm: float = 0.0
    diameter_mm: float = 2.0
    is_visible: bool = False

    @property
    def fraction_t(self) -> float:
        """Normalized parameter t along the route in [0.0, 1.0]."""
        route_len = self.route.length_mm
        if route_len < 1e-6:
            return 0.0
        return max(0.0, min(1.0, float(self.insertion_depth_mm / route_len)))

    @property
    def tip_position_mm(self) -> tuple[float, float, float]:
        """Physical coordinates of the virtual instrument tip (X, Y, Z) in mm."""
        t = self.fraction_t
        ex, ey, ez = self.route.entry_point.coordinates
        tx, ty, tz = self.route.target_point.coordinates
        return (
            float(ex + t * (tx - ex)),
            float(ey + t * (ty - ey)),
            float(ez + t * (tz - ez)),
        )

    @property
    def direction_vector(self) -> tuple[float, float, float]:
        """Unit direction vector from Entry toward Target."""
        route_len = self.route.length_mm
        if route_len < 1e-6:
            return (0.0, 0.0, 1.0)
        return (
            float(self.route.delta_x / route_len),
            float(self.route.delta_y / route_len),
            float(self.route.delta_z / route_len),
        )

    def distance_to_point(self, point: tuple[float, float, float]) -> float:
        """Calculates shortest 3D distance from a physical point (X, Y, Z) in mm to the virtual instrument segment (Entry -> Tip)."""
        import math
        px, py, pz = point
        ex, ey, ez = self.route.entry_point.coordinates
        tip_x, tip_y, tip_z = self.tip_position_mm

        vx = tip_x - ex
        vy = tip_y - ey
        vz = tip_z - ez
        seg_len_sq = vx ** 2 + vy ** 2 + vz ** 2

        if seg_len_sq < 1e-9:
            return float(math.sqrt((px - ex) ** 2 + (py - ey) ** 2 + (pz - ez) ** 2))

        wx = px - ex
        wy = py - ey
        wz = pz - ez

        t = (wx * vx + wy * vy + wz * vz) / seg_len_sq
        t_clamped = max(0.0, min(1.0, t))

        cx = ex + t_clamped * vx
        cy = ey + t_clamped * vy
        cz = ez + t_clamped * vz

        return float(math.sqrt((px - cx) ** 2 + (py - cy) ** 2 + (pz - cz) ** 2))

    def distance_to_structure(self, structure: AvoidStructure) -> float:
        """Calculates shortest 3D distance from the center of an AvoidStructure to the active instrument segment (Entry -> Tip) in mm."""
        return self.distance_to_point(structure.center)

    def to_dict(self) -> dict:
        return {
            "route": self.route.to_dict(),
            "insertion_depth_mm": float(self.insertion_depth_mm),
            "diameter_mm": float(self.diameter_mm),
            "is_visible": bool(self.is_visible),
            "fraction_t": self.fraction_t,
            "tip_position_mm": self.tip_position_mm,
            "direction_vector": self.direction_vector,
        }


@dataclass
class CurrentInstrumentPose:
    """Represents a simulated current instrument pose with geometric deviation from the Planned Route.

    Used strictly as a software-only simulation proxy for rehearsing and visualizing live deviation.
    Does NOT represent hardware tracking or clinical navigation.
    """
    route: PlannedRoute
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    offset_z_mm: float = 0.0
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    diameter_mm: float = 2.0
    is_visible: bool = False

    @property
    def nominal_tip_position_mm(self) -> tuple[float, float, float]:
        """Nominal target point on the planned route (X, Y, Z) in mm."""
        return self.route.target_point.coordinates

    @property
    def current_tip_position_mm(self) -> tuple[float, float, float]:
        """Physical coordinates of the simulated current instrument tip (X, Y, Z) in mm."""
        tx, ty, tz = self.nominal_tip_position_mm
        return (
            float(tx + self.offset_x_mm),
            float(ty + self.offset_y_mm),
            float(tz + self.offset_z_mm),
        )

    @property
    def nearest_planned_point_mm(self) -> tuple[float, float, float]:
        """Closest point on the finite Planned Route segment [Entry, Target] to the current tip in mm."""
        cx, cy, cz = self.current_tip_position_mm
        ex, ey, ez = self.route.entry_point.coordinates
        tx, ty, tz = self.route.target_point.coordinates

        vx = tx - ex
        vy = ty - ey
        vz = tz - ez
        seg_len_sq = vx ** 2 + vy ** 2 + vz ** 2

        if seg_len_sq < 1e-9:
            return (ex, ey, ez)

        wx = cx - ex
        wy = cy - ey
        wz = cz - ez

        t = (wx * vx + wy * vy + wz * vz) / seg_len_sq
        t_clamped = max(0.0, min(1.0, t))

        return (
            float(ex + t_clamped * vx),
            float(ey + t_clamped * vy),
            float(ez + t_clamped * vz),
        )

    @property
    def lateral_deviation_mm(self) -> float:
        """Shortest 3D distance from current tip to the finite Planned Route segment in mm."""
        import math
        cx, cy, cz = self.current_tip_position_mm
        nx, ny, nz = self.nearest_planned_point_mm
        return float(math.sqrt((cx - nx) ** 2 + (cy - ny) ** 2 + (cz - nz) ** 2))

    @property
    def tip_offset_vector_mm(self) -> tuple[float, float, float]:
        """Tip displacement vector (Current Tip - Nearest Planned Point) in mm."""
        cx, cy, cz = self.current_tip_position_mm
        nx, ny, nz = self.nearest_planned_point_mm
        return (
            float(cx - nx),
            float(cy - ny),
            float(cz - nz),
        )

    @property
    def planned_direction_vector(self) -> tuple[float, float, float]:
        """Unit direction vector of the Planned Route."""
        route_len = self.route.length_mm
        if route_len < 1e-6:
            return (0.0, 0.0, 1.0)
        return (
            float(self.route.delta_x / route_len),
            float(self.route.delta_y / route_len),
            float(self.route.delta_z / route_len),
        )

    @property
    def current_direction_vector(self) -> tuple[float, float, float]:
        """Unit direction vector of the simulated current instrument."""
        import math
        fx, fy, fz = self.planned_direction_vector
        if abs(self.yaw_deg) < 1e-4 and abs(self.pitch_deg) < 1e-4:
            return (fx, fy, fz)

        # Build orthonormal basis around forward vector f
        if abs(fz) < 0.9:
            up_x, up_y, up_z = 0.0, 0.0, 1.0
        else:
            up_x, up_y, up_z = 0.0, 1.0, 0.0

        # Right vector r = f x up
        rx = fy * up_z - fz * up_y
        ry = fz * up_x - fx * up_z
        rz = fx * up_y - fy * up_x
        r_len = math.sqrt(rx ** 2 + ry ** 2 + rz ** 2)
        if r_len < 1e-6:
            return (fx, fy, fz)
        rx /= r_len
        ry /= r_len
        rz /= r_len

        # True up vector u = r x f
        ux = ry * fz - rz * fy
        uy = rz * fx - rx * fz
        uz = rx * fy - ry * fx

        yaw_rad = math.radians(self.yaw_deg)
        pitch_rad = math.radians(self.pitch_deg)

        # Local vector in (r, u, f) basis
        vr = math.sin(yaw_rad) * math.cos(pitch_rad)
        vu = math.sin(pitch_rad)
        vf = math.cos(yaw_rad) * math.cos(pitch_rad)

        dx = vr * rx + vu * ux + vf * fx
        dy = vr * ry + vu * uy + vf * fy
        dz = vr * rz + vu * uz + vf * fz
        d_len = math.sqrt(dx ** 2 + dy ** 2 + dz ** 2)
        if d_len < 1e-6:
            return (fx, fy, fz)
        return (float(dx / d_len), float(dy / d_len), float(dz / d_len))

    @property
    def angular_deviation_deg(self) -> float:
        """Angular deviation between planned route and current instrument in degrees."""
        import math
        px, py, pz = self.planned_direction_vector
        cx, cy, cz = self.current_direction_vector
        p_len = math.sqrt(px ** 2 + py ** 2 + pz ** 2)
        c_len = math.sqrt(cx ** 2 + cy ** 2 + cz ** 2)
        if p_len < 1e-6 or c_len < 1e-6:
            return 0.0

        dot = px * cx + py * cy + pz * cz
        clamped_dot = max(-1.0, min(1.0, dot / (p_len * c_len)))
        return float(math.degrees(math.acos(clamped_dot)))

    def distance_to_structure(self, structure: AvoidStructure) -> float:
        """Calculates shortest 3D distance from the simulated current instrument segment to an AvoidStructure center."""
        import math
        px, py, pz = structure.center
        cx, cy, cz = self.current_tip_position_mm
        dx, dy, dz = self.current_direction_vector
        length = self.route.length_mm

        # Segment goes from tip backwards by route length: [Tip - length * dir, Tip]
        sx = cx - length * dx
        sy = cy - length * dy
        sz = cz - length * dz

        vx = cx - sx
        vy = cy - sy
        vz = cz - sz
        seg_len_sq = vx ** 2 + vy ** 2 + vz ** 2

        if seg_len_sq < 1e-9:
            return float(math.sqrt((px - cx) ** 2 + (py - cy) ** 2 + (pz - cz) ** 2))

        wx = px - sx
        wy = py - sy
        wz = pz - sz

        t = (wx * vx + wy * vy + wz * vz) / seg_len_sq
        t_clamped = max(0.0, min(1.0, t))

        closest_x = sx + t_clamped * vx
        closest_y = sy + t_clamped * vy
        closest_z = sz + t_clamped * vz

        return float(math.sqrt((px - closest_x) ** 2 + (py - closest_y) ** 2 + (pz - closest_z) ** 2))

    def to_dict(self) -> dict:
        return {
            "route": self.route.to_dict(),
            "offset_x_mm": float(self.offset_x_mm),
            "offset_y_mm": float(self.offset_y_mm),
            "offset_z_mm": float(self.offset_z_mm),
            "yaw_deg": float(self.yaw_deg),
            "pitch_deg": float(self.pitch_deg),
            "diameter_mm": float(self.diameter_mm),
            "is_visible": bool(self.is_visible),
            "nominal_tip_position_mm": self.nominal_tip_position_mm,
            "current_tip_position_mm": self.current_tip_position_mm,
            "nearest_planned_point_mm": self.nearest_planned_point_mm,
            "lateral_deviation_mm": self.lateral_deviation_mm,
            "tip_offset_vector_mm": self.tip_offset_vector_mm,
            "planned_direction_vector": self.planned_direction_vector,
            "current_direction_vector": self.current_direction_vector,
            "angular_deviation_deg": self.angular_deviation_deg,
        }


@dataclass
class SurgicalPlanVersion:
    """Represents a persistent surgical planning version record for a scan."""
    version_id: str
    scan_id: str
    name: str
    created_at: str
    updated_at: str
    snapshot: dict
    patient_mrn: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "version_id": self.version_id,
            "scan_id": self.scan_id,
            "patient_mrn": self.patient_mrn,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "snapshot": self.snapshot,
            "notes": self.notes,
        }


class SurgicalPlanSession:
    """Session-level container holding Entry, Target, Planned Route, Avoid Structures, Surgical Corridor, Virtual Instrument, and Live Deviation for the active scan."""

    def __init__(self):
        self.entry_point: Optional[PlanningPoint] = None
        self.target_point: Optional[PlanningPoint] = None
        self.avoid_structures: dict[str, AvoidStructure] = {}
        self.corridor_radius_mm: float = 5.0
        self.corridor_enabled: bool = True
        self.instrument_depth_mm: float = 0.0
        self.instrument_diameter_mm: float = 2.0
        self.instrument_visible: bool = False
        self.deviation_offset_x_mm: float = 0.0
        self.deviation_offset_y_mm: float = 0.0
        self.deviation_offset_z_mm: float = 0.0
        self.deviation_yaw_deg: float = 0.0
        self.deviation_pitch_deg: float = 0.0
        self.deviation_visible: bool = False
        self.scan_id: Optional[str] = None
        self._structure_counter: int = 0
        self.active_version_id: Optional[str] = None
        self.last_saved_snapshot: Optional[dict] = None


    def set_entry(self, x: float, y: float, z: float, source_view: str = "3D") -> PlanningPoint:
        """Sets or replaces the ENTRY planning landmark."""
        self.entry_point = PlanningPoint(
            point_type="ENTRY",
            x_mm=float(x),
            y_mm=float(y),
            z_mm=float(z),
            source_view=source_view
        )
        return self.entry_point

    def set_target(self, x: float, y: float, z: float, source_view: str = "3D") -> PlanningPoint:
        """Sets or replaces the TARGET planning landmark."""
        self.target_point = PlanningPoint(
            point_type="TARGET",
            x_mm=float(x),
            y_mm=float(y),
            z_mm=float(z),
            source_view=source_view
        )
        return self.target_point

    def clear_entry(self):
        """Removes the ENTRY landmark, leaving TARGET and avoid structures intact."""
        self.entry_point = None

    def clear_target(self):
        """Removes the TARGET landmark, leaving ENTRY and avoid structures intact."""
        self.target_point = None

    def clear_both(self):
        """Removes both landmarks and the planned route, leaving avoid structures intact."""
        self.entry_point = None
        self.target_point = None

    def has_entry(self) -> bool:
        return self.entry_point is not None

    def has_target(self) -> bool:
        return self.target_point is not None

    def has_planned_route(self) -> bool:
        """Returns True if both Entry and Target exist, enabling the planned route."""
        return self.entry_point is not None and self.target_point is not None

    def get_planned_route(self) -> Optional[PlannedRoute]:
        """Returns the PlannedRoute connecting Entry and Target if both exist, otherwise None."""
        if self.entry_point is not None and self.target_point is not None:
            return PlannedRoute(entry_point=self.entry_point, target_point=self.target_point)
        return None

    # ── Surgical Corridor Management ─────────────────────────────────────────

    def get_surgical_corridor(self) -> Optional[SurgicalCorridor]:
        """Returns the SurgicalCorridor around the active Planned Route if both Entry and Target exist."""
        route = self.get_planned_route()
        if route is not None:
            return SurgicalCorridor(
                route=route,
                radius_mm=self.corridor_radius_mm,
                is_enabled=self.corridor_enabled
            )
        return None

    def has_surgical_corridor(self) -> bool:
        """Returns True if a valid Planned Route exists and corridor is configured."""
        return self.has_planned_route() and self.corridor_enabled

    def set_corridor_radius(self, radius_mm: float) -> float:
        """Updates the configured corridor radius in millimeters."""
        self.corridor_radius_mm = max(0.5, float(radius_mm))
        return self.corridor_radius_mm

    def set_corridor_enabled(self, enabled: bool) -> bool:
        """Enables or disables 3D/2D rendering of the surgical corridor."""
        self.corridor_enabled = bool(enabled)
        return self.corridor_enabled

    def clear_surgical_corridor(self):
        """Disables the surgical corridor."""
        self.corridor_enabled = False

    # ── Avoid Structures Management ──────────────────────────────────────────

    def add_avoid_structure(
        self,
        x: float,
        y: float,
        z: float,
        radius_mm: float = 5.0,
        name: str = "",
        source_view: str = "3D",
        structure_id: Optional[str] = None
    ) -> AvoidStructure:
        """Adds a new anatomical structure to avoid during surgical planning."""
        self._structure_counter += 1
        sid = structure_id or f"struct_{int(time.time()*1000)}_{self._structure_counter}"
        sname = name.strip() if name and name.strip() else f"Structure {self._structure_counter}"

        struct = AvoidStructure(
            structure_id=sid,
            name=sname,
            center_x_mm=float(x),
            center_y_mm=float(y),
            center_z_mm=float(z),
            radius_mm=float(radius_mm),
            source_view=source_view
        )
        self.avoid_structures[sid] = struct
        return struct

    def remove_avoid_structure(self, structure_id: str) -> bool:
        """Removes a single avoid structure by its identifier."""
        if structure_id in self.avoid_structures:
            del self.avoid_structures[structure_id]
            return True
        return False

    def clear_avoid_structures(self):
        """Removes all avoid structures."""
        self.avoid_structures.clear()

    def get_avoid_structures(self) -> list[AvoidStructure]:
        """Returns list of all active avoid structures."""
        return list(self.avoid_structures.values())

    def get_avoid_structure(self, structure_id: str) -> Optional[AvoidStructure]:
        """Retrieves an avoid structure by id, or None if not found."""
        return self.avoid_structures.get(structure_id)

    def has_avoid_structures(self) -> bool:
        """Returns True if at least one avoid structure is defined."""
        return len(self.avoid_structures) > 0

    def reset_for_scan(self, scan_id: str):
        """Resets planning landmarks, route, avoid structures, corridor, virtual instrument, and live deviation if the active scan changes."""
        if self.scan_id != scan_id:
            self.scan_id = scan_id
            self.clear_both()
            self.clear_avoid_structures()
            self.corridor_radius_mm = 5.0
            self.corridor_enabled = True
            self.instrument_depth_mm = 0.0
            self.instrument_diameter_mm = 2.0
            self.instrument_visible = False
            self.deviation_offset_x_mm = 0.0
            self.deviation_offset_y_mm = 0.0
            self.deviation_offset_z_mm = 0.0
            self.deviation_yaw_deg = 0.0
            self.deviation_pitch_deg = 0.0
            self.deviation_visible = False
            self._structure_counter = 0
            self.active_version_id = None
            self.last_saved_snapshot = None

    # ── Virtual Instrument Management ────────────────────────────────────────

    def get_virtual_instrument(self) -> Optional[VirtualInstrument]:
        """Returns the VirtualInstrument along the active Planned Route if both Entry and Target exist."""
        route = self.get_planned_route()
        if route is not None:
            # Clamp depth to current route length
            clamped_depth = max(0.0, min(self.instrument_depth_mm, route.length_mm))
            return VirtualInstrument(
                route=route,
                insertion_depth_mm=clamped_depth,
                diameter_mm=self.instrument_diameter_mm,
                is_visible=self.instrument_visible
            )
        return None

    def has_virtual_instrument(self) -> bool:
        """Returns True if a valid Planned Route exists to support a virtual instrument."""
        return self.has_planned_route()

    def set_insertion_depth(self, depth_mm: float) -> float:
        """Sets insertion depth in mm, clamped to [0.0, route.length_mm]."""
        route = self.get_planned_route()
        max_depth = route.length_mm if route is not None else 0.0
        self.instrument_depth_mm = max(0.0, min(float(depth_mm), max_depth))
        return self.instrument_depth_mm

    def set_insertion_fraction(self, t: float) -> float:
        """Sets insertion depth as a normalized fraction t in [0.0, 1.0] of route length."""
        route = self.get_planned_route()
        if route is not None and route.length_mm > 0.0:
            t_clamped = max(0.0, min(1.0, float(t)))
            self.instrument_depth_mm = t_clamped * route.length_mm
        else:
            self.instrument_depth_mm = 0.0
        return self.instrument_depth_mm

    def set_instrument_diameter(self, diameter_mm: float) -> float:
        """Sets the virtual instrument diameter in mm."""
        self.instrument_diameter_mm = max(0.5, min(float(diameter_mm), 50.0))
        return self.instrument_diameter_mm

    def set_instrument_visible(self, visible: bool) -> bool:
        """Enables or disables 3D/2D rendering of the virtual instrument."""
        self.instrument_visible = bool(visible)
        return self.instrument_visible

    def clear_virtual_instrument(self):
        """Resets virtual instrument insertion depth and visibility."""
        self.instrument_depth_mm = 0.0
        self.instrument_visible = False

    # ── Live Deviation Management ────────────────────────────────────────────

    def get_current_instrument_pose(self) -> Optional[CurrentInstrumentPose]:
        """Returns the CurrentInstrumentPose relative to the active Planned Route if both Entry and Target exist."""
        route = self.get_planned_route()
        if route is not None:
            return CurrentInstrumentPose(
                route=route,
                offset_x_mm=self.deviation_offset_x_mm,
                offset_y_mm=self.deviation_offset_y_mm,
                offset_z_mm=self.deviation_offset_z_mm,
                yaw_deg=self.deviation_yaw_deg,
                pitch_deg=self.deviation_pitch_deg,
                diameter_mm=self.instrument_diameter_mm,
                is_visible=self.deviation_visible,
            )
        return None

    def has_live_deviation(self) -> bool:
        """Returns True if a valid Planned Route exists to support simulated live deviation."""
        return self.has_planned_route()

    def set_deviation_offsets(self, dx: float, dy: float, dz: float) -> tuple[float, float, float]:
        """Sets simulation offsets in physical mm relative to the planned target point."""
        self.deviation_offset_x_mm = float(dx)
        self.deviation_offset_y_mm = float(dy)
        self.deviation_offset_z_mm = float(dz)
        return (self.deviation_offset_x_mm, self.deviation_offset_y_mm, self.deviation_offset_z_mm)

    def set_deviation_angles(self, yaw_deg: float, pitch_deg: float) -> tuple[float, float]:
        """Sets simulation yaw and pitch in degrees relative to the planned trajectory."""
        self.deviation_yaw_deg = float(yaw_deg)
        self.deviation_pitch_deg = float(pitch_deg)
        return (self.deviation_yaw_deg, self.deviation_pitch_deg)

    def set_deviation_visible(self, visible: bool) -> bool:
        """Enables or disables 3D/2D rendering of the simulated current instrument and deviation connector."""
        self.deviation_visible = bool(visible)
        return self.deviation_visible

    def reset_deviation_to_planned(self):
        """Resets all simulation offsets and angles to 0.0, aligning the current instrument with the Planned Route."""
        self.deviation_offset_x_mm = 0.0
        self.deviation_offset_y_mm = 0.0
        self.deviation_offset_z_mm = 0.0
        self.deviation_yaw_deg = 0.0
        self.deviation_pitch_deg = 0.0

    # ── Plan Versions & Snapshot Management (OR Mode: Feature 7) ─────────────

    def create_snapshot(self) -> dict:
        """Generates a canonical JSON-serializable snapshot of the current planning state.

        Planned route, corridor geometry, virtual instrument geometry, and deviation pose
        are derived and will be reconstructed on restore rather than duplicated.
        """
        return {
            "schema_version": 1,
            "entry_point": self.entry_point.to_dict() if self.entry_point else None,
            "target_point": self.target_point.to_dict() if self.target_point else None,
            "avoid_structures": [s.to_dict() for s in self.avoid_structures.values()],
            "corridor": {
                "radius_mm": float(self.corridor_radius_mm),
                "is_enabled": bool(self.corridor_enabled),
            },
            "virtual_instrument": {
                "insertion_depth_mm": float(self.instrument_depth_mm),
                "diameter_mm": float(self.instrument_diameter_mm),
                "is_visible": bool(self.instrument_visible),
            },
            "live_deviation": {
                "offset_x_mm": float(self.deviation_offset_x_mm),
                "offset_y_mm": float(self.deviation_offset_y_mm),
                "offset_z_mm": float(self.deviation_offset_z_mm),
                "yaw_deg": float(self.deviation_yaw_deg),
                "pitch_deg": float(self.deviation_pitch_deg),
                "is_visible": bool(self.deviation_visible),
            },
        }

    def restore_snapshot(self, snapshot: dict) -> bool:
        """Restores planning state from a snapshot dictionary.

        Validates schema version and reconstructs Entry, Target, Avoid Structures,
        Corridor, Virtual Instrument, and Live Deviation parameters in deterministic order.
        Derived geometry is recomputed automatically.
        """
        if not isinstance(snapshot, dict):
            return False
        if snapshot.get("schema_version") != 1:
            return False

        # 1. Clear current planning state
        self.clear_both()
        self.clear_avoid_structures()

        # 2. Restore Entry
        entry_data = snapshot.get("entry_point")
        if entry_data:
            self.entry_point = PlanningPoint(
                point_type="ENTRY",
                x_mm=float(entry_data["x_mm"]),
                y_mm=float(entry_data["y_mm"]),
                z_mm=float(entry_data["z_mm"]),
                source_view=entry_data.get("source_view", "3D"),
                creation_time=entry_data.get("creation_time", time.time())
            )

        # 3. Restore Target
        target_data = snapshot.get("target_point")
        if target_data:
            self.target_point = PlanningPoint(
                point_type="TARGET",
                x_mm=float(target_data["x_mm"]),
                y_mm=float(target_data["y_mm"]),
                z_mm=float(target_data["z_mm"]),
                source_view=target_data.get("source_view", "3D"),
                creation_time=target_data.get("creation_time", time.time())
            )

        # 4. Restore Structures to Avoid
        avoid_data = snapshot.get("avoid_structures", [])
        for s_dict in avoid_data:
            struct = AvoidStructure(
                structure_id=s_dict["structure_id"],
                name=s_dict["name"],
                center_x_mm=float(s_dict["center_x_mm"]),
                center_y_mm=float(s_dict["center_y_mm"]),
                center_z_mm=float(s_dict["center_z_mm"]),
                radius_mm=float(s_dict.get("radius_mm", 5.0)),
                source_view=s_dict.get("source_view", "3D"),
                creation_time=s_dict.get("creation_time", time.time())
            )
            self.avoid_structures[struct.structure_id] = struct
        self._structure_counter = len(self.avoid_structures)

        # 5. Restore Corridor parameters
        corridor_data = snapshot.get("corridor", {})
        self.corridor_radius_mm = float(corridor_data.get("radius_mm", 5.0))
        self.corridor_enabled = bool(corridor_data.get("is_enabled", True))

        # 6. Restore Virtual Instrument parameters
        vi_data = snapshot.get("virtual_instrument", {})
        self.instrument_depth_mm = float(vi_data.get("insertion_depth_mm", 0.0))
        self.instrument_diameter_mm = float(vi_data.get("diameter_mm", 2.0))
        self.instrument_visible = bool(vi_data.get("is_visible", False))

        # 7. Restore Live Deviation parameters
        dev_data = snapshot.get("live_deviation", {})
        self.deviation_offset_x_mm = float(dev_data.get("offset_x_mm", 0.0))
        self.deviation_offset_y_mm = float(dev_data.get("offset_y_mm", 0.0))
        self.deviation_offset_z_mm = float(dev_data.get("offset_z_mm", 0.0))
        self.deviation_yaw_deg = float(dev_data.get("yaw_deg", 0.0))
        self.deviation_pitch_deg = float(dev_data.get("pitch_deg", 0.0))
        self.deviation_visible = bool(dev_data.get("is_visible", False))

        # Update last saved snapshot to match restored state
        self.last_saved_snapshot = self.create_snapshot()
        return True

    def mark_saved(self, version_id: str):
        """Marks current state as saved under version_id."""
        self.active_version_id = str(version_id)
        self.last_saved_snapshot = self.create_snapshot()

    def is_empty(self) -> bool:
        """Returns True if no planning landmarks or avoid structures exist."""
        return (
            self.entry_point is None
            and self.target_point is None
            and len(self.avoid_structures) == 0
        )

    def has_unsaved_changes(self) -> bool:
        """Returns True if the current planning state differs from the last saved/restored snapshot."""
        if self.last_saved_snapshot is None:
            return not self.is_empty()
        current_snap = self.create_snapshot()
        return self._compare_snapshots(current_snap, self.last_saved_snapshot)

    def _compare_snapshots(self, snap1: dict, snap2: dict) -> bool:
        """Returns True if snap1 differs from snap2 in meaningful planning fields (ignoring timestamps)."""
        # Compare Entry
        e1 = snap1.get("entry_point")
        e2 = snap2.get("entry_point")
        if (e1 is None) != (e2 is None):
            return True
        if e1 and e2:
            if (abs(e1["x_mm"] - e2["x_mm"]) > 1e-4 or
                abs(e1["y_mm"] - e2["y_mm"]) > 1e-4 or
                abs(e1["z_mm"] - e2["z_mm"]) > 1e-4):
                return True

        # Compare Target
        t1 = snap1.get("target_point")
        t2 = snap2.get("target_point")
        if (t1 is None) != (t2 is None):
            return True
        if t1 and t2:
            if (abs(t1["x_mm"] - t2["x_mm"]) > 1e-4 or
                abs(t1["y_mm"] - t2["y_mm"]) > 1e-4 or
                abs(t1["z_mm"] - t2["z_mm"]) > 1e-4):
                return True

        # Compare Avoid Structures
        s1 = snap1.get("avoid_structures", [])
        s2 = snap2.get("avoid_structures", [])
        if len(s1) != len(s2):
            return True
        d2 = {s["structure_id"]: s for s in s2}
        for item1 in s1:
            sid = item1["structure_id"]
            if sid not in d2:
                return True
            item2 = d2[sid]
            if (abs(item1["center_x_mm"] - item2["center_x_mm"]) > 1e-4 or
                abs(item1["center_y_mm"] - item2["center_y_mm"]) > 1e-4 or
                abs(item1["center_z_mm"] - item2["center_z_mm"]) > 1e-4 or
                abs(item1["radius_mm"] - item2["radius_mm"]) > 1e-4 or
                item1.get("name") != item2.get("name")):
                return True

        # Compare Corridor
        c1 = snap1.get("corridor", {})
        c2 = snap2.get("corridor", {})
        if (abs(c1.get("radius_mm", 5.0) - c2.get("radius_mm", 5.0)) > 1e-4 or
            c1.get("is_enabled", True) != c2.get("is_enabled", True)):
            return True

        # Compare Virtual Instrument
        v1 = snap1.get("virtual_instrument", {})
        v2 = snap2.get("virtual_instrument", {})
        if (abs(v1.get("insertion_depth_mm", 0.0) - v2.get("insertion_depth_mm", 0.0)) > 1e-4 or
            abs(v1.get("diameter_mm", 2.0) - v2.get("diameter_mm", 2.0)) > 1e-4 or
            v1.get("is_visible", False) != v2.get("is_visible", False)):
            return True

        # Compare Live Deviation
        ld1 = snap1.get("live_deviation", {})
        ld2 = snap2.get("live_deviation", {})
        if (abs(ld1.get("offset_x_mm", 0.0) - ld2.get("offset_x_mm", 0.0)) > 1e-4 or
            abs(ld1.get("offset_y_mm", 0.0) - ld2.get("offset_y_mm", 0.0)) > 1e-4 or
            abs(ld1.get("offset_z_mm", 0.0) - ld2.get("offset_z_mm", 0.0)) > 1e-4 or
            abs(ld1.get("yaw_deg", 0.0) - ld2.get("yaw_deg", 0.0)) > 1e-4 or
            abs(ld1.get("pitch_deg", 0.0) - ld2.get("pitch_deg", 0.0)) > 1e-4 or
            ld1.get("is_visible", False) != ld2.get("is_visible", False)):
            return True

        return False



