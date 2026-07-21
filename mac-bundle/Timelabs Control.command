#!/bin/bash
# Timelabs Control — one click to connect to your server and open everything.
# Double-click this. It connects, then your browser opens two tabs:
#   1. Hermes (your AI agent's control panel)
#   2. Ops Dashboard (your business numbers)

SERVER="root@217.216.79.31"
KEY="$HOME/.ssh/timelabs_hermes"

echo ""
echo "  ⌚ Timelabs Control"
echo "  ─────────────────────────────"

if [ ! -f "$KEY" ]; then
  echo "  ❌ Setup key not found. Please run 'Install Timelabs Control' first."
  echo ""
  read -n 1 -s -r -p "  Press any key to close..."
  exit 1
fi

# Close any previous Timelabs tunnel so we always start fresh
pkill -f "ssh.*timelabs_hermes" 2>/dev/null && sleep 1

echo "  Connecting to your server..."
ssh -f -N \
  -i "$KEY" \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ConnectTimeout=15 \
  -o StrictHostKeyChecking=accept-new \
  -L 9119:127.0.0.1:9119 \
  -L 8123:127.0.0.1:80 \
  "$SERVER"

if [ $? -ne 0 ]; then
  echo ""
  echo "  ❌ Could not connect. Check your internet and try again."
  echo "     If it keeps failing, the server may be down."
  echo ""
  read -n 1 -s -r -p "  Press any key to close..."
  exit 1
fi

sleep 1
echo "  ✅ Connected."
echo ""
echo "  Opening in your browser:"
echo "    • Hermes:        http://localhost:9119"
echo "    • Ops Dashboard: http://localhost:8123/ops/"
echo ""

open "http://localhost:9119"
sleep 1
open "http://localhost:8123/ops/"

echo "  You can close this window — the connection stays on"
echo "  until you shut down or run 'Disconnect Timelabs'."
sleep 4
exit 0
