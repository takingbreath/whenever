#!/bin/sh
# Connects the cameras on this computer's network to Whenever. It puts what it needs in
# ~/whenever-cameras, finds the cameras, asks for each password, tests them, and starts sending.
# The cameras' passwords stay on this computer. Only pictures go to Whenever.
set -e
SERVER="__SERVER__"
DIR="$HOME/whenever-cameras"
say() { printf '%s\n' "$*"; }

command -v python3 >/dev/null 2>&1 || { say "Whenever needs Python 3. Get it from https://www.python.org/downloads/, then run this again."; exit 1; }
if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    say "Installing ffmpeg, which reads the cameras' video..."
    brew install ffmpeg
  elif command -v apt-get >/dev/null 2>&1; then
    say "Installing ffmpeg, which reads the cameras' video. It may ask for this computer's password."
    sudo apt-get install -y ffmpeg
  else
    say "Whenever needs ffmpeg. Get it from https://ffmpeg.org/download.html, then run this again."
    exit 1
  fi
fi

mkdir -p "$DIR"
cd "$DIR"
say "Getting Whenever's camera reader..."
curl -fsSL "$SERVER/connect/camera.py" -o camera.py
curl -fsSL "$SERVER/connect/camera_sources.py" -o camera_sources.py
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --quiet Pillow
say ""
.venv/bin/python camera.py --setup "$SERVER" < /dev/tty
