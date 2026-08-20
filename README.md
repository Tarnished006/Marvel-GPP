# Aegis-Touch 🏥✋

> **Touchless medical imaging workstation** — Control DICOM 3D scans with hand gestures and voice commands. No physical contact required.

[![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)](https://python.org)
[![PyQt6](https://img.shields.io/badge/UI-PyQt6-41CD52?logo=qt)](https://riverbankcomputing.com/software/pyqt/)
[![MediaPipe](https://img.shields.io/badge/AI-MediaPipe-FF6F00?logo=google)](https://mediapipe.dev)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Overview

**Aegis-Touch** is a touchless medical imaging workstation designed for surgical and ICU environments where sterile technique prohibits physical contact with keyboards and mice. Surgeons and clinicians can interact with 3D DICOM scans entirely through:

- **Hand gestures** captured via webcam (MediaPipe AI)
- **Air mouse** — move the OS cursor with your hand mid-air
- **Voice commands** for preset camera views (offline, no internet required)

Built with PyQt6 for the UI, PyVista/VTK for 3D rendering, and optimized to run on both full workstations and edge devices like the **NVIDIA Jetson Nano**.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🖐 **Gesture Air Mouse** | Control OS cursor using your right hand; pinch thumb+index to click |
| 🫀 **3D DICOM Viewer** | Render bone isosurfaces from CT scan slices (DICOM `.dcm` format) |
| 🔄 **3D Camera Control** | Rotate, zoom, and orbit the 3D model with open-palm gestures |
| 🎙 **Voice Commands** | Push-to-talk offline voice recognition (Vosk) for preset views |
| 🏥 **OR/ICU Mode** | Dedicated sterile-environment layout for intraoperative use |
| 📋 **Patient Dashboard** | SQLite-backed patient registry with scan gallery and records |
| 📷 **Camera HUD** | Floating picture-in-picture camera feed with real-time hand skeleton |
| 🗂 **Auto-Ingest** | Watched-folder ingestion: drop DICOM folders and they appear automatically |
| 🐋 **Docker Support** | Containerized deployment for headless/server environments |
| 🤖 **Jetson Nano Optimized** | Smart downsampling and decimation tuned for ARM64 edge hardware |

---

## 🏗 Architecture

```
aegis-touch/
├── main.py              # App entry point — MainWindow, CameraHUD, navigation
├── gesture.py           # GestureWorker (QThread) — MediaPipe AI + air mouse engine
├── dicom_engine.py      # DICOM → 3D mesh pipeline (PyVista / VTK)
├── scan_index.py        # Watched-folder ingestion + JSON patient index
├── database.py          # SQLite patient/scan registry (aegis.db)
├── voice_commands.py    # Push-to-talk offline voice recognition (Vosk)
├── signal_bus.py        # App-wide Qt signal bus (gesture → UI decoupling)
├── ema_filter.py        # EMA + 1€ filter for smooth hand tracking
├── ingest.py            # CLI tool: manually ingest a DICOM folder into the DB
├── theme.py             # Global dark stylesheet
├── schema.sql           # SQLite schema
├── screens/
│   ├── dashboard.py     # Patient directory with local dataset cards
│   ├── viewer_3d.py     # 3D DICOM viewer with gesture camera control
│   ├── scans.py         # Scan gallery for a patient
│   ├── record.py        # Patient medical record view
│   └── or_icu_mode.py   # OR/ICU sterile-environment screen
├── DICOM/               # Drop body CT slices here (auto-detected)
├── skull/               # Drop skull CT slices here (auto-detected)
├── Dockerfile
├── setup.sh             # One-time environment setup
└── run.sh               # Launch with virtual display + VNC
```

---

## 🖐 Gesture Controls

### Air Mouse (Right Hand)
Activate with the **"Air Mouse: OFF"** toggle in the nav bar.

| Gesture | Action |
|---|---|
| Move open hand | Move OS cursor |
| Pinch thumb + index finger | Left click |
| Release pinch | Release click |

> The air mouse uses a **1€ filter** for silky-smooth, zero-jitter cursor movement with natural hand velocity feel.

### 3D Viewer (Left Hand, or both hands when Air Mouse is OFF)

| Gesture | Action |
|---|---|
| Open palm — move left/right | Azimuth orbit (rotate around Y axis) |
| Open palm — move up/down | Elevation orbit (rotate around X axis) |
| Pinch thumb + **index** | Zoom In |
| Pinch thumb + **middle** | Zoom Out |
| Palm height (up = high) | Tissue melt intensity (reserved for skin mesh) |

> A deadzone of 0.003 filters micro-tremor. Elevation is clamped to ±80° to prevent gimbal lock.

---

## 🎙 Voice Commands

Voice recognition uses **Vosk** (fully offline — no cloud, no latency).

**Supported commands:**

| Phrase | Action |
|---|---|
| `anterior` | Jump to anterior (front) view |
| `posterior` | Jump to posterior (back) view |
| `lateral` | Jump to lateral (side) view |
| `reset` / `reset view` | Reset camera to default position |

**Trigger:** Push-to-talk via **Enter key** (swappable for USB foot pedal in `voice_commands.py`).

### Voice Setup (one-time)
```bash
pip install vosk sounddevice
# Download the small English model from https://alphacephei.com/vosk/models
# Unzip as: ./vosk-model-small-en-us-0.15/
```

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Webcam (for gesture control)
- DICOM `.dcm` files (CT scan slices)

### 1. Clone the repository
```bash
git clone https://github.com/Tarnished006/Marvel-GPP.git
cd Marvel-GPP
```

### 2. Create a virtual environment
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Add DICOM data
Place your DICOM scan folders in the project root:
```
Marvel-GPP/
├── skull/        ← skull CT slices (.dcm files)
│   ├── slice001.dcm
│   └── ...
└── DICOM/        ← body/chest CT slices (.dcm files)
    ├── slice001.dcm
    └── ...
```

### 5. Run
```bash
python main.py
```

---

## 🐧 Linux / Codespace Setup

```bash
# Install system dependencies (PyQt6 + display)
bash setup.sh

# Launch with virtual display + VNC
bash run.sh
```

Then open the forwarded VNC port to view the app in your browser.

---

## 🐋 Docker Deployment

```bash
docker build -t aegis-touch .
docker run --rm \
  -e DISPLAY=$DISPLAY \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v $(pwd)/DICOM:/app/DICOM \
  aegis-touch
```

> The Dockerfile uses Ubuntu 22.04 + Python 3.11 with full OpenGL support.

---

## 🤖 Jetson Nano Optimization

Set `JETSON_OPTIMIZED = True` in `dicom_engine.py` (enabled by default) to activate:

| Optimization | Value | Effect |
|---|---|---|
| Volume pre-downsample | 0.50× | ~8× fewer voxels, preserves thin bone |
| Gaussian smooth | Skipped | Saves 10–20s per scan load |
| Mesh decimation | 88% removed | Keeps ~12% of triangles (~50–80k) |
| Post-decimation cleanup | `clean()` + `extract_largest()` | Removes floating fragments |

Set `JETSON_OPTIMIZED = False` for full-resolution desktop rendering.

---

## 🗄 Database

Patient and scan data is stored in **SQLite** (`aegis.db`), initialized from `schema.sql`.

### Manual ingest
```bash
python ingest.py
```

### Watched-folder auto-ingest
`scan_index.py` watches `incoming_scans/` for new DICOM subfolders. Drop a folder of `.dcm` slices and the app registers them automatically by reading DICOM headers (patient ID, name, modality, study date).

---

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `PyQt6` | Desktop UI framework |
| `mediapipe` | Hand landmark detection (AI) |
| `opencv-python` | Webcam capture + frame annotation |
| `pyvista` / `pyvistaqt` / `vtk` | 3D mesh rendering |
| `pydicom` | DICOM file reading + HU conversion |
| `numpy` | Array math |
| `vosk` | Offline speech recognition |
| `sounddevice` | Microphone audio capture |
| `watchdog` | Folder watching for auto-ingest |

---

## 🖥 Screens

| Screen | Description |
|---|---|
| **Clinical View (Dashboard)** | Patient grid with local DICOM dataset cards and DB-backed patients |
| **3D Viewer** | Interactive PyVista viewport with gesture camera, sidebar scan switcher |
| **OR/ICU Mode** | Split-panel layout: patient record (left), 3D render (center), scan list (right) |
| **Scan Gallery** | Thumbnail list of all scans for a selected patient |
| **Patient Record** | Full medical record view for a patient |

---

## 🔧 Signal Bus

All gesture events are decoupled from the UI via `signal_bus.py`:

| Signal | Type | Description |
|---|---|---|
| `cursor_moved` | `float, float` | Normalized (x, y) for air mouse |
| `pinch_started` | — | Thumb+index pinch detected |
| `pinch_ended` | — | Pinch released |
| `hand_rotation` | `float, float, float` | Δx, Δy, Δz for 3D camera orbit |
| `zoom_command` | `int` | +1 zoom in, -1 zoom out |
| `tissue_melt` | `float` | Palm height 0–1 (future skin mesh) |
| `camera_frame` | `QImage` | Annotated webcam frame for HUD |
| `air_mouse_toggle` | `bool` | Air mouse on/off |
| `tracking_confidence` | `float` | 0.0 or 1.0 — hand detected? |

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

---

## 👥 Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

---

*Built for sterile environments where touching a keyboard is not an option.*
