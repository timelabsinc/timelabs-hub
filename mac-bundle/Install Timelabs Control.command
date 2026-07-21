#!/bin/bash
# One-time installer. Double-click me first.
# Puts your secure key in place and copies the launchers to your Desktop.

HERE="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "  ⌚ Timelabs Control — Setup"
echo "  ─────────────────────────────"

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

if [ ! -f "$HERE/timelabs_hermes" ]; then
  echo "  ❌ Key file missing from this folder. Re-download the setup zip."
  read -n 1 -s -r -p "  Press any key to close..."
  exit 1
fi

cp "$HERE/timelabs_hermes" "$HOME/.ssh/timelabs_hermes"
chmod 600 "$HOME/.ssh/timelabs_hermes"
echo "  ✅ Secure key installed."

cp "$HERE/Timelabs Control.command" "$HOME/Desktop/Timelabs Control.command"
cp "$HERE/Disconnect Timelabs.command" "$HOME/Desktop/Disconnect Timelabs.command"
chmod +x "$HOME/Desktop/Timelabs Control.command" "$HOME/Desktop/Disconnect Timelabs.command"
xattr -d com.apple.quarantine "$HOME/Desktop/Timelabs Control.command" 2>/dev/null
xattr -d com.apple.quarantine "$HOME/Desktop/Disconnect Timelabs.command" 2>/dev/null
echo "  ✅ 'Timelabs Control' added to your Desktop."
echo "  ✅ 'Disconnect Timelabs' added to your Desktop."

echo ""
echo "  All set! From now on, just double-click 'Timelabs Control'"
echo "  on your Desktop to open Hermes and your dashboard."
echo ""
echo "  You can delete the downloaded zip and this folder now."
echo ""
read -n 1 -s -r -p "  Press any key to close..."
exit 0
