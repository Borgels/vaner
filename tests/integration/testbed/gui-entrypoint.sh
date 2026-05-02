#!/usr/bin/env bash
#
# Phase D-2 entrypoint. Boots a real Ubuntu X11 session inside the
# container and exposes it via noVNC.
#
# Order of operations:
#   1. Xvfb on :99 (the testbed's "monitor").
#   2. dbus session bus (Tauri needs one for the tray + dialogs).
#   3. mutter --x11 — the standard GNOME compositor + window
#      manager, run standalone. We skip gnome-session entirely:
#      Ubuntu 24.04's gnome-session hard-requires systemd as a
#      user-session manager, which isn't available inside vanilla
#      Docker. mutter alone gives us window placement, focus, and
#      decorations against the same X stack the desktop ships
#      against.
#   4. x11vnc bridges :99 to a TCP VNC port.
#   5. websockify wraps that in WebSocket so the bundled noVNC HTML
#      page (served on :6080) can drive it from any browser.
#   6. If an AppImage was mounted at $APP_PATH (default
#      /home/vaner/vaner-desktop.AppImage), launch it once the
#      compositor is up. The wizard window appears inside the noVNC
#      view.
#
# `wait -n` keeps the container alive until the *first* of these
# subprocesses exits — which means a Tauri crash propagates as a
# container exit, useful for one-shot tests in CI.

set -e

mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# Xvfb wants /tmp/.X11-unix for the abstract socket; the container's
# /tmp is empty on first boot.
sudo mkdir -p /tmp/.X11-unix
sudo chmod 1777 /tmp/.X11-unix

# 1. Virtual display.
Xvfb :99 -screen 0 1280x800x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait for the X server to come up before any client tries to connect.
for _ in $(seq 1 30); do
  if xdpyinfo -display :99 >/dev/null 2>&1; then break; fi
  sleep 0.2
done

# 2. Session dbus.
eval "$(dbus-launch --sh-syntax)"
export DBUS_SESSION_BUS_ADDRESS DBUS_SESSION_BUS_PID

# 3. Compositor / window manager. Prefer mutter (the GNOME WM) —
# falls back to metacity which is rock-solid in containers.
if command -v metacity >/dev/null 2>&1; then
  metacity --replace &
  WM_PID=$!
elif command -v mutter >/dev/null 2>&1; then
  mutter --x11 --replace &
  WM_PID=$!
else
  echo "[gui-entrypoint] no window manager available — Tauri windows will be undecorated" >&2
fi

# 4. VNC bridge.
x11vnc -display :99 -nopw -forever -shared -quiet -rfbport 5900 &
VNC_PID=$!

# 5. noVNC over websockify.
websockify --web=/usr/share/novnc 6080 localhost:5900 &
WEB_PID=$!

# 6. Optional: launch the desktop AppImage when one is mounted.
APP_PATH=${APP_PATH:-/home/vaner/vaner-desktop.AppImage}
if [ -x "$APP_PATH" ]; then
  # Give GNOME a few seconds to settle so the Tauri tray icon has a
  # panel to attach to.
  ( sleep 4 && "$APP_PATH" --appimage-extract-and-run ) &
  APP_PID=$!
fi

echo "[gui-entrypoint] noVNC: open http://localhost:6080/vnc.html?autoconnect=1"
echo "[gui-entrypoint] AppImage: ${APP_PATH} ($([ -x "$APP_PATH" ] && echo running || echo not mounted))"

wait -n
