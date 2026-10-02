#!/bin/sh
# One-time setup on Linux: checks Python and Bluetooth, makes .venv with the dependencies.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

command -v python3 >/dev/null || { echo "python3 is missing (Debian/Ubuntu: sudo apt install python3 python3-venv)"; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "Python 3.10 or newer is needed"; exit 1; }
python3 -c 'import venv, ensurepip' 2>/dev/null || { echo "python3-venv is missing (Debian/Ubuntu: sudo apt install python3-venv)"; exit 1; }

if ! command -v bluetoothctl >/dev/null; then
    echo "warning: bluetoothctl not found. Install BlueZ (Debian/Ubuntu: sudo apt install bluez)."
elif ! bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
    echo "warning: no powered Bluetooth adapter found. Try: bluetoothctl power on"
fi

python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r client/requirements.txt
chmod +x ditoo
echo "done. Next: ./ditoo scan"
