# test_or_plan_versions.py
"""
Unit and integration tests for OR Feature 7: PLAN SAVING / VERSIONS.

Covers:
 1. Empty plan can be detected.
 2. Snapshot is JSON serializable.
 3. Snapshot contains schema version.
 4. Entry is captured.
 5. Target is captured.
 6. Structures are captured.
 7. Corridor settings are captured.
 8. Virtual Instrument settings are captured.
 9. Live Deviation settings are captured.
10. Planned Route is derivable from snapshot Entry/Target.
11. Derived geometry is not unnecessarily duplicated.
12. Save creates a persistent version.
13. Multiple versions can coexist.
14. Version IDs are unique.
15. Version names are preserved.
16. Versions are associated with scan ID.
17. Versions can be listed for a scan.
18. Version can be retrieved.
19. Same-scan version restores correctly.
20. Entry/Target restore correctly.
21. Structures restore correctly.
22. Corridor settings restore correctly.
23. Virtual Instrument settings restore correctly.
24. Live Deviation settings restore correctly.
25. Restore recomputes derived geometry.
26. Restore does not duplicate actors.
27. Restore does not create caliper measurements.
28. Restore does not modify existing caliper measurements.
29. Restore does not reload DICOM for same scan.
30. Cross-scan restore is rejected.
31. Scan change filters version list.
32. Delete removes only selected version.
33. Renaming preserves version ID.
34. Unsaved changes are detected after editing.
35. Restore returns to Saved state.
36. Save As New Version preserves previous version.
37. OR <-> ICU does not duplicate state.
38. PlanVersionsCard UI components and status.
39. Voice commands trigger plan version actions.
40. Snapshot comparison ignores timestamp noise.
"""

import os
import json
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication

from surgical_plan import (
    PlanningPoint,
    PlannedRoute,
    AvoidStructure,
    SurgicalCorridor,
    VirtualInstrument,
    CurrentInstrumentPose,
    SurgicalPlanSession,
    SurgicalPlanVersion,
)
import database
from screens.viewer_3d import Viewer3D
from screens.or_icu_mode import OrIcuMode, PlanVersionsCard
from main import MainWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Provides a fresh isolated SQLite database for each test."""
    db_file = str(tmp_path / "test_aegis.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    with open("schema.sql") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture
def populated_session():
    """Returns a SurgicalPlanSession populated with all planning features (Features 1-6)."""
    session = SurgicalPlanSession()
    session.scan_id = "scan_test_101"
    session.set_entry(10.0, 20.0, 30.0, source_view="3D")
    session.set_target(40.0, 60.0, 80.0, source_view="3D")
    session.add_avoid_structure(15.0, 25.0, 35.0, radius_mm=6.0, name="Carotid Artery", structure_id="struct_1")
    session.add_avoid_structure(25.0, 35.0, 45.0, radius_mm=4.0, name="Optic Nerve", structure_id="struct_2")
    session.set_corridor_radius(7.5)
    session.set_corridor_enabled(True)
    session.set_insertion_depth(25.0)
    session.set_instrument_diameter(3.0)
    session.set_instrument_visible(True)
    session.set_deviation_offsets(2.0, -1.5, 3.0)
    session.set_deviation_angles(5.0, -2.5)
    session.set_deviation_visible(True)
    return session


# ── 1. Empty plan detection ──────────────────────────────────────────────────

def test_empty_plan_detection():
    session = SurgicalPlanSession()
    assert session.is_empty() is True

    session.set_entry(0.0, 0.0, 0.0)
    assert session.is_empty() is False

    session.clear_entry()
    assert session.is_empty() is True

    session.add_avoid_structure(1.0, 1.0, 1.0)
    assert session.is_empty() is False


# ── 2 & 3. Snapshot format & JSON serializability ───────────────────────────

def test_snapshot_json_serializability(populated_session):
    snapshot = populated_session.create_snapshot()
    assert isinstance(snapshot, dict)
    # Must be JSON serializable
    json_str = json.dumps(snapshot)
    assert isinstance(json_str, str)
    loaded = json.loads(json_str)
    assert loaded["schema_version"] == 1


# ── 4 - 9. Captured planning fields ─────────────────────────────────────────

def test_snapshot_captures_all_features(populated_session):
    snapshot = populated_session.create_snapshot()

    # 4. Entry is captured
    assert snapshot["entry_point"] is not None
    assert snapshot["entry_point"]["x_mm"] == 10.0
    assert snapshot["entry_point"]["y_mm"] == 20.0
    assert snapshot["entry_point"]["z_mm"] == 30.0

    # 5. Target is captured
    assert snapshot["target_point"] is not None
    assert snapshot["target_point"]["x_mm"] == 40.0
    assert snapshot["target_point"]["y_mm"] == 60.0
    assert snapshot["target_point"]["z_mm"] == 80.0

    # 6. Structures to avoid are captured
    assert len(snapshot["avoid_structures"]) == 2
    s_names = [s["name"] for s in snapshot["avoid_structures"]]
    assert "Carotid Artery" in s_names
    assert "Optic Nerve" in s_names

    # 7. Corridor settings are captured
    assert snapshot["corridor"]["radius_mm"] == 7.5
    assert snapshot["corridor"]["is_enabled"] is True

    # 8. Virtual Instrument settings are captured
    assert snapshot["virtual_instrument"]["insertion_depth_mm"] == 25.0
    assert snapshot["virtual_instrument"]["diameter_mm"] == 3.0
    assert snapshot["virtual_instrument"]["is_visible"] is True

    # 9. Live Deviation settings are captured
    assert snapshot["live_deviation"]["offset_x_mm"] == 2.0
    assert snapshot["live_deviation"]["offset_y_mm"] == -1.5
    assert snapshot["live_deviation"]["offset_z_mm"] == 3.0
    assert snapshot["live_deviation"]["yaw_deg"] == 5.0
    assert snapshot["live_deviation"]["pitch_deg"] == -2.5
    assert snapshot["live_deviation"]["is_visible"] is True


# ── 10 & 11. Derived geometry is not duplicated ──────────────────────────────

def test_derived_geometry_not_duplicated_in_snapshot(populated_session):
    snapshot = populated_session.create_snapshot()
    # Route is NOT explicitly stored as a top-level duplicate key
    assert "planned_route" not in snapshot
    # Corridor geometry (volume, cylinder triangles) is not duplicated
    assert "volume_mm3" not in snapshot.get("corridor", {})
    # Instrument tip position coordinates are not duplicated
    assert "tip_position_mm" not in snapshot.get("virtual_instrument", {})
    # Deviation connector points are not duplicated
    assert "nearest_planned_point_mm" not in snapshot.get("live_deviation", {})

    # But PlannedRoute is fully derivable from Entry + Target
    ep = PlanningPoint(**snapshot["entry_point"])
    tp = PlanningPoint(**snapshot["target_point"])
    route = PlannedRoute(entry_point=ep, target_point=tp)
    assert route.length_mm > 0.0


# ── 12 - 18. Database Persistence & Version Model ────────────────────────────

def test_database_save_and_versions(test_db, populated_session):
    snapshot = populated_session.create_snapshot()
    scan_id = "scan_test_101"

    # 12. Save creates persistent version
    v1 = database.save_surgical_plan_version(
        scan_id=scan_id,
        name="Plan 1",
        snapshot=snapshot,
        patient_mrn="MRN_99"
    )
    assert v1["id"] is not None
    assert v1["name"] == "Plan 1"
    assert v1["scan_id"] == scan_id
    assert v1["patient_mrn"] == "MRN_99"

    # 13 & 14. Multiple versions coexist with unique IDs
    v2 = database.save_surgical_plan_version(
        scan_id=scan_id,
        name="Plan 2 - Lateral",
        snapshot=snapshot,
        patient_mrn="MRN_99"
    )
    assert v2["id"] != v1["id"]

    # 15. Version names are preserved
    assert v2["name"] == "Plan 2 - Lateral"

    # 16 & 17. Versions are associated with scan_id and can be listed
    versions = database.get_surgical_plan_versions(scan_id)
    assert len(versions) == 2
    assert versions[0]["id"] == v1["id"]
    assert versions[1]["id"] == v2["id"]

    # 18. Version can be retrieved by ID
    retrieved = database.get_surgical_plan_version(v1["id"])
    assert retrieved is not None
    assert retrieved["id"] == v1["id"]
    assert retrieved["snapshot"]["schema_version"] == 1


# ── 19 - 25. Same-scan Restoration & Derived Geometry Recomputation ─────────

def test_same_scan_restoration(populated_session):
    snapshot = populated_session.create_snapshot()

    # Create fresh empty session for same scan
    new_session = SurgicalPlanSession()
    new_session.scan_id = populated_session.scan_id

    # 19. Same-scan version restores correctly
    success = new_session.restore_snapshot(snapshot)
    assert success is True

    # 20. Entry and Target restored
    assert new_session.entry_point.coordinates == (10.0, 20.0, 30.0)
    assert new_session.target_point.coordinates == (40.0, 60.0, 80.0)

    # 21. Structures restored
    assert len(new_session.avoid_structures) == 2
    assert "struct_1" in new_session.avoid_structures
    assert new_session.avoid_structures["struct_1"].name == "Carotid Artery"
    assert new_session.avoid_structures["struct_1"].radius_mm == 6.0

    # 22. Corridor restored
    assert new_session.corridor_radius_mm == 7.5
    assert new_session.corridor_enabled is True

    # 23. Virtual Instrument restored
    assert new_session.instrument_depth_mm == 25.0
    assert new_session.instrument_diameter_mm == 3.0
    assert new_session.instrument_visible is True

    # 24. Live Deviation restored
    assert (new_session.deviation_offset_x_mm, new_session.deviation_offset_y_mm, new_session.deviation_offset_z_mm) == (2.0, -1.5, 3.0)
    assert (new_session.deviation_yaw_deg, new_session.deviation_pitch_deg) == (5.0, -2.5)
    assert new_session.deviation_visible is True

    # 25. Recomputes derived geometry
    route = new_session.get_planned_route()
    assert route is not None
    assert route.length_mm == populated_session.get_planned_route().length_mm

    corridor = new_session.get_surgical_corridor()
    assert corridor is not None
    assert corridor.volume_mm3 > 0.0

    inst = new_session.get_virtual_instrument()
    assert inst is not None
    assert inst.tip_position_mm != inst.route.entry_point.coordinates

    pose = new_session.get_current_instrument_pose()
    assert pose is not None
    assert pose.lateral_deviation_mm > 0.0


# ── 26. Viewer3D Actor Restoration (No duplicates) ──────────────────────────

def test_viewer3d_restore_snapshot_no_duplicates(qapp, populated_session):
    viewer = Viewer3D()
    viewer.surgical_plan.scan_id = "scan_test_101"
    snapshot = populated_session.create_snapshot()

    # First restore
    success = viewer.restore_surgical_plan_snapshot(snapshot)
    assert success is True

    # Check actors
    actor_count_1 = len(viewer.plotter.actors)
    assert "or_entry_marker" in viewer.plotter.actors
    assert "or_target_marker" in viewer.plotter.actors
    assert "or_planned_route" in viewer.plotter.actors
    assert "or_surgical_corridor" in viewer.plotter.actors
    assert "or_virtual_instrument" in viewer.plotter.actors
    assert "or_live_instrument" in viewer.plotter.actors

    # Second restore of the same snapshot must not duplicate actors
    success_2 = viewer.restore_surgical_plan_snapshot(snapshot)
    assert success_2 is True
    actor_count_2 = len(viewer.plotter.actors)
    assert actor_count_1 == actor_count_2


# ── 27 & 28. Caliper Measurement Isolation ───────────────────────────────────

def test_measurement_isolation(populated_session):
    snapshot = populated_session.create_snapshot()

    # Ensure no caliper measurement properties exist in snapshot
    assert "_measurements_3d" not in snapshot
    assert "calipers" not in snapshot

    # Restore on a session with dummy measurement data
    session = SurgicalPlanSession()
    session.scan_id = "scan_test_101"
    session._dummy_measurements = [{"id": "m1", "length_mm": 12.5}]

    session.restore_snapshot(snapshot)
    # Existing measurements remain intact and uncorrupted
    assert session._dummy_measurements == [{"id": "m1", "length_mm": 12.5}]


# ── 29. No DICOM reload for same scan ────────────────────────────────────────

def test_restore_does_not_reload_dicom(qapp, populated_session):
    viewer = Viewer3D()
    viewer.load_scan = MagicMock()
    snapshot = populated_session.create_snapshot()

    viewer.restore_surgical_plan_snapshot(snapshot)
    # load_scan should NOT be called
    viewer.load_scan.assert_not_called()


# ── 30. Cross-scan restore rejection ─────────────────────────────────────────

def test_cross_scan_restore_rejection(qapp, test_db, populated_session):
    with patch("main.GestureWorker"):
        win = MainWindow()
        try:
            win.viewer_3d.surgical_plan.scan_id = "scan_A"
            win.or_icu_mode.current_scan = {"file_path": "scan_A", "id": "scan_A"}

            # Save plan on scan_B
            snapshot = populated_session.create_snapshot()
            v = database.save_surgical_plan_version(
                scan_id="scan_B",
                name="Plan on Scan B",
                snapshot=snapshot
            )

            # Attempt to restore version from scan_B onto scan_A
            win.flash_status = MagicMock()
            win._on_icu_or_restore_plan(v["id"])

            # Must be rejected with warning
            win.flash_status.assert_called_with("Plan belongs to a different scan")
        finally:
            if hasattr(win, "worker") and hasattr(win.worker, "stop"):
                win.worker.stop()


# ── 31. Scan change filters version list ─────────────────────────────────────

def test_scan_change_filters_version_list(test_db, populated_session):
    snapshot = populated_session.create_snapshot()
    database.save_surgical_plan_version("scan_alpha", "Alpha 1", snapshot)
    database.save_surgical_plan_version("scan_alpha", "Alpha 2", snapshot)
    database.save_surgical_plan_version("scan_beta", "Beta 1", snapshot)

    alpha_versions = database.get_surgical_plan_versions("scan_alpha")
    beta_versions = database.get_surgical_plan_versions("scan_beta")

    assert len(alpha_versions) == 2
    assert len(beta_versions) == 1
    assert alpha_versions[0]["name"] == "Alpha 1"
    assert beta_versions[0]["name"] == "Beta 1"


# ── 32. Delete version ───────────────────────────────────────────────────────

def test_delete_plan_version(test_db, populated_session):
    snapshot = populated_session.create_snapshot()
    v1 = database.save_surgical_plan_version("scan_X", "Plan X1", snapshot)
    v2 = database.save_surgical_plan_version("scan_X", "Plan X2", snapshot)

    assert len(database.get_surgical_plan_versions("scan_X")) == 2

    # Delete v1
    deleted = database.delete_surgical_plan_version(v1["id"])
    assert deleted is True

    versions = database.get_surgical_plan_versions("scan_X")
    assert len(versions) == 1
    assert versions[0]["id"] == v2["id"]


# ── 33. Rename version ───────────────────────────────────────────────────────

def test_rename_plan_version(test_db, populated_session):
    snapshot = populated_session.create_snapshot()
    v = database.save_surgical_plan_version("scan_Y", "Old Name", snapshot)

    renamed = database.rename_surgical_plan_version(v["id"], "New Superior Approach")
    assert renamed is True

    updated_v = database.get_surgical_plan_version(v["id"])
    assert updated_v["name"] == "New Superior Approach"
    assert updated_v["id"] == v["id"]


# ── 34 & 35. Unsaved changes detection ──────────────────────────────────────

def test_unsaved_changes_detection(populated_session):
    session = populated_session
    # Initially mark saved
    session.mark_saved("plan_init")
    assert session.has_unsaved_changes() is False

    # 34. Modify Entry -> Unsaved Changes
    session.set_entry(12.0, 20.0, 30.0)
    assert session.has_unsaved_changes() is True

    # Revert to exact saved -> Saved
    session.set_entry(10.0, 20.0, 30.0)
    assert session.has_unsaved_changes() is False

    # Modify corridor radius -> Unsaved Changes
    session.set_corridor_radius(10.0)
    assert session.has_unsaved_changes() is True
    session.set_corridor_radius(7.5)
    assert session.has_unsaved_changes() is False

    # Modify virtual instrument depth -> Unsaved Changes
    session.set_insertion_depth(30.0)
    assert session.has_unsaved_changes() is True
    session.set_insertion_depth(25.0)
    assert session.has_unsaved_changes() is False

    # Modify deviation -> Unsaved Changes
    session.set_deviation_offsets(5.0, 0.0, 0.0)
    assert session.has_unsaved_changes() is True

    # 35. Restoring returns to Saved state
    snap = session.create_snapshot()
    session.restore_snapshot(snap)
    assert session.has_unsaved_changes() is False


# ── 36. Save As New Version preserves previous version ──────────────────────

def test_save_as_new_version_preserves_previous(test_db, populated_session):
    scan_id = "scan_ver_1"
    snap1 = populated_session.create_snapshot()
    v1 = database.save_surgical_plan_version(scan_id, "Plan 1", snap1)

    # Edit planning state
    populated_session.set_entry(99.0, 99.0, 99.0)
    snap2 = populated_session.create_snapshot()
    v2 = database.save_surgical_plan_version(scan_id, "Plan 2", snap2)

    # Both versions exist independently
    retrieved_v1 = database.get_surgical_plan_version(v1["id"])
    retrieved_v2 = database.get_surgical_plan_version(v2["id"])

    assert retrieved_v1["snapshot"]["entry_point"]["x_mm"] == 10.0
    assert retrieved_v2["snapshot"]["entry_point"]["x_mm"] == 99.0


# ── 37. OR <-> ICU does not duplicate state ──────────────────────────────────

def test_or_icu_shared_session_state(qapp, populated_session):
    or_icu = OrIcuMode()
    or_icu.set_surgical_plan(populated_session)

    # Switch to ICU
    or_icu.set_mode("ICU")
    assert or_icu.surgical_plan == populated_session

    # Switch to OR
    or_icu.set_mode("OR")
    assert or_icu.surgical_plan == populated_session
    assert or_icu.surgical_plan.entry_point.coordinates == (10.0, 20.0, 30.0)


# ── 38. PlanVersionsCard UI ─────────────────────────────────────────────────

def test_plan_versions_card_ui(qapp, test_db, populated_session):
    card = PlanVersionsCard()
    scan_id = "scan_ui_test"
    snap = populated_session.create_snapshot()
    database.save_surgical_plan_version(scan_id, "Plan A", snap)
    database.save_surgical_plan_version(scan_id, "Plan B", snap)

    card.update_versions(populated_session, scan_id)
    assert card.lbl_status.text() == "Unsaved Changes"

    populated_session.mark_saved("plan_A")
    card.update_versions(populated_session, scan_id)
    assert card.lbl_status.text() == "Saved"

    # Two version rows present in container
    assert card.versions_layout.count() == 2


# ── 39. Voice commands ───────────────────────────────────────────────────────

def test_plan_version_voice_commands(qapp):
    viewer = Viewer3D()
    viewer.state_stack.setCurrentIndex(viewer._PAGE_SCENE)
    viewer.plan_save_requested = MagicMock()
    viewer.plan_versions_requested = MagicMock()

    viewer.handle_voice_command("save plan")
    viewer.plan_save_requested.emit.assert_called_once()

    viewer.handle_voice_command("show plan versions")
    viewer.plan_versions_requested.emit.assert_called_once()


# ── 40. Snapshot compare ignores timestamp noise ────────────────────────────

def test_snapshot_comparison_ignores_timestamps(populated_session):
    snap1 = populated_session.create_snapshot()
    snap2 = populated_session.create_snapshot()

    # Artificially alter timestamps
    snap2["entry_point"]["creation_time"] += 100.0
    snap2["target_point"]["creation_time"] += 200.0
    for s in snap2["avoid_structures"]:
        s["creation_time"] += 300.0

    # Despite different timestamps, _compare_snapshots returns False (no change)
    assert populated_session._compare_snapshots(snap1, snap2) is False
