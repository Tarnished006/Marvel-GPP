# scan_index.py
"""
Watched-folder ingestion + JSON index for Aegis-Touch.

Drop a folder of .dcm slices into the incoming directory (one subfolder per
scan, e.g. incoming_scans/<any_name>/*.dcm). This module notices it,
reads patient + scan metadata straight from the DICOM headers (not the
folder name -- clinical data should never depend on someone naming a
folder correctly), and writes/updates a JSON index that the rest of the
app (dashboard, scan gallery, 3D viewer) can query instead of touching
the filesystem directly.

Index shape (patient_index.json):
{
  "patients": {
    "<PatientID>": {
      "name": "...",
      "sex": "...",
      "birth_date": "...",
      "scans": [
        {
          "scan_id": "<folder name>",
          "modality": "CT",
          "study_date": "20260714",
          "num_slices": 100,
          "folder_path": "/abs/path/to/scan",
          "indexed_at": "2026-08-12T10:00:00"
        },
        ...
      ]
    }
  }
}
"""

import os
import json
import threading
import datetime
from pathlib import Path

import pydicom
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class ScanIndex:
    """Thread-safe read/write wrapper around the JSON index file."""

    def __init__(self, index_path: str):
        self.index_path = Path(index_path)
        self._lock = threading.Lock()
        if not self.index_path.exists():
            self._write({"patients": {}})

    def _read(self) -> dict:
        with self._lock:
            if not self.index_path.exists():
                return {"patients": {}}
            with open(self.index_path, "r") as f:
                return json.load(f)

    def _write(self, data: dict):
        with self._lock:
            tmp_path = self.index_path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
            tmp_path.replace(self.index_path)  # atomic on POSIX

    def upsert_scan(self, patient_id: str, patient_meta: dict, scan_entry: dict):
        """Add or update one scan under one patient. Skips duplicate scan_ids."""
        data = self._read()
        patients = data.setdefault("patients", {})

        patient = patients.setdefault(patient_id, {
            "name": patient_meta.get("name", "Unknown"),
            "sex": patient_meta.get("sex", ""),
            "birth_date": patient_meta.get("birth_date", ""),
            "scans": [],
        })
        # keep header data fresh in case it was incomplete before
        patient["name"] = patient_meta.get("name") or patient["name"]
        patient["sex"] = patient_meta.get("sex") or patient["sex"]
        patient["birth_date"] = patient_meta.get("birth_date") or patient["birth_date"]

        existing_ids = {s["scan_id"] for s in patient["scans"]}
        if scan_entry["scan_id"] not in existing_ids:
            patient["scans"].append(scan_entry)

        self._write(data)

    def get_patients(self) -> dict:
        return self._read().get("patients", {})

    def get_scans(self, patient_id: str) -> list:
        return self._read().get("patients", {}).get(patient_id, {}).get("scans", [])


def read_scan_metadata(scan_folder: str) -> tuple[str, dict, dict] | None:
    """
    Reads the first .dcm file in scan_folder for header metadata.
    Returns (patient_id, patient_meta, scan_entry) or None if no valid DICOM found.
    """
    dcm_files = [f for f in os.listdir(scan_folder) if f.lower().endswith(".dcm")]
    if not dcm_files:
        return None

    first = pydicom.dcmread(os.path.join(scan_folder, dcm_files[0]), stop_before_pixels=True)

    patient_id = str(getattr(first, "PatientID", "") or Path(scan_folder).name)
    patient_meta = {
        "name": str(getattr(first, "PatientName", "Unknown")),
        "sex": str(getattr(first, "PatientSex", "")),
        "birth_date": str(getattr(first, "PatientBirthDate", "")),
    }
    scan_entry = {
        "scan_id": Path(scan_folder).name,
        "modality": str(getattr(first, "Modality", "UNKNOWN")),
        "study_date": str(getattr(first, "StudyDate", "")),
        "num_slices": len(dcm_files),
        "folder_path": str(Path(scan_folder).resolve()),
        "indexed_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    return patient_id, patient_meta, scan_entry


class _NewScanHandler(FileSystemEventHandler):
    """
    Fires when a .dcm file shows up; indexes the whole containing folder.

    A folder copy/USB transfer isn't instantaneous -- files land one at a
    time, so indexing on the very first .dcm event undercounts slices.
    Each event instead (re)schedules a debounce timer per folder; the
    folder is only actually indexed once no new file has landed in it for
    `settle_seconds`. This trades a small delay for a correct slice count.
    """

    def __init__(self, index: ScanIndex, on_new_scan=None, settle_seconds: float = 1.5):
        self.index = index
        self.on_new_scan = on_new_scan
        self.settle_seconds = settle_seconds
        self._seen_folders = set()
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _index_folder(self, scan_folder: str):
        with self._lock:
            self._timers.pop(scan_folder, None)
        if scan_folder in self._seen_folders:
            return  # already indexed this scan folder this session

        result = read_scan_metadata(scan_folder)
        if result is None:
            return
        patient_id, patient_meta, scan_entry = result
        self.index.upsert_scan(patient_id, patient_meta, scan_entry)
        self._seen_folders.add(scan_folder)
        print(f"[ScanIndex] Indexed scan '{scan_entry['scan_id']}' "
              f"({scan_entry['modality']}, {scan_entry['num_slices']} slices) "
              f"-> patient {patient_id}")
        if self.on_new_scan:
            self.on_new_scan(patient_id, scan_entry)

    def _maybe_index(self, path: str):
        if not path.lower().endswith(".dcm"):
            return
        scan_folder = str(Path(path).parent)
        if scan_folder in self._seen_folders:
            return

        with self._lock:
            existing = self._timers.get(scan_folder)
            if existing:
                existing.cancel()
            timer = threading.Timer(self.settle_seconds, self._index_folder, args=(scan_folder,))
            timer.daemon = True
            self._timers[scan_folder] = timer
            timer.start()

    def on_created(self, event):
        if not event.is_directory:
            self._maybe_index(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._maybe_index(event.src_path)


def scan_existing_folders(watch_dir: str, index: ScanIndex):
    """One-time sweep so scans already present before the watcher started get indexed too."""
    watch_path = Path(watch_dir)
    if not watch_path.exists():
        return
    for entry in watch_path.iterdir():
        if entry.is_dir():
            result = read_scan_metadata(str(entry))
            if result:
                patient_id, patient_meta, scan_entry = result
                index.upsert_scan(patient_id, patient_meta, scan_entry)


def start_watcher(watch_dir: str, index_path: str, on_new_scan=None) -> Observer:
    """
    Starts watching `watch_dir` for new scan subfolders in the background.
    Returns the running Observer (call .stop() + .join() to shut it down cleanly).
    """
    os.makedirs(watch_dir, exist_ok=True)
    index = ScanIndex(index_path)
    scan_existing_folders(watch_dir, index)  # catch anything dropped in before startup

    handler = _NewScanHandler(index, on_new_scan=on_new_scan)
    observer = Observer()
    observer.schedule(handler, watch_dir, recursive=True)
    observer.start()
    print(f"[ScanIndex] Watching '{watch_dir}' -> index at '{index_path}'")
    return observer


if __name__ == "__main__":
    # Quick manual test: watches ./incoming_scans, indexes anything already there,
    # then keeps running so you can drop in a new scan folder and watch it get picked up.
    import time

    WATCH_DIR = "incoming_scans"
    INDEX_PATH = "patient_index.json"

    observer = start_watcher(WATCH_DIR, INDEX_PATH)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        observer.join()