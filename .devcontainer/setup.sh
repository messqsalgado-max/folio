#!/usr/bin/env bash
# Runs once when the Codespace is created: Folio's Python packages, Google Chrome, and a virtual screen
# (a visible Chrome on a virtual screen looks like a person's browser to Google; headless doesn't).
set -e
cd "$(dirname "$0")/.."
pip install --no-cache-dir -r requirements.txt playwright==1.64.0
sudo apt-get update
sudo apt-get install -y xvfb fonts-liberation fonts-noto-color-emoji
curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt-get install -y /tmp/chrome.deb
rm -f /tmp/chrome.deb
mkdir -p /workspaces/folio-data
