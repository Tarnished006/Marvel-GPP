#!/bin/bash
# Starts the virtual display + VNC server, then launches the app.
# Run with: bash run.sh

pkill -9 Xvfb 2>/dev/null
pkill -9 x11vnc 2>/dev/null
sleep 1

Xvfb :1 -screen 0 1920x1080x24 &
sleep 1

export DISPLAY=:1
x11vnc -display :1 -forever -nopw &
sleep 1

echo "Virtual display running. Open the forwarded VNC port to view the app."
python main.py