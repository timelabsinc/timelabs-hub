#!/bin/bash
# Closes the Timelabs server connection.
pkill -f "ssh.*timelabs_hermes" 2>/dev/null
echo ""
echo "  ⌚ Timelabs connection closed."
sleep 2
exit 0
