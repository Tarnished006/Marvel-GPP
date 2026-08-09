#!/bin/bash
# One-time setup for a fresh Codespace.
# Run with: bash setup.sh

set -e

echo "Installing system libraries needed by PyQt6..."
sudo apt update
sudo apt install -y \
    libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-render-util0 libdbus-1-3 libfontconfig1 libxrender1 \
    libxi6 libsm6 libice6 xvfb x11vnc

echo "Installing Python dependencies..."
pip install -r requirements.txt

echo "Setup complete. Run 'bash run.sh' to start the app."