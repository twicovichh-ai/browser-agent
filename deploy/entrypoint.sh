#!/bin/sh
# Virtual display for a real (headful) Chrome; the web UI streams its picture.
set -e
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x860x24 -nolisten tcp &
sleep 1
mkdir -p "$PROFILE_DIR"
rm -f "$PROFILE_DIR"/Singleton*   # stale Chrome lock after a container restart
exec python -m webapp.server
