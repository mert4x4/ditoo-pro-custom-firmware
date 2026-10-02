#!/bin/sh
# Copy the core API into the Home Assistant integration. The copy is generated:
# edit client/ditoo_api.py and run this script again.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
SRC="$HERE/../client/ditoo_api.py"
DST="$HERE/custom_components/ditoo_pro/ditoo_api.py"
{ echo "# Generated from client/ditoo_api.py by homeassistant/sync_core.sh. Do not edit here."; cat "$SRC"; } > "$DST"
echo "copied $(wc -l < "$SRC") lines to $DST"
