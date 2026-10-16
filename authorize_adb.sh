#!/bin/bash
# Script to authorize ADB connection to a device
# Usage: ./authorize_adb.sh <IP> [PORT]
# Run this once to accept the USB debugging prompt on your TV

echo "=========================================="
echo "ADB Authorization Helper"
echo "=========================================="
echo ""

if [ -z "$1" ]; then
    echo "Usage: $0 <DEVICE_IP> [ADB_PORT]"
    echo "  Example: $0 192.168.1.10 5555"
    exit 1
fi

DEVICE_IP=$1
ADB_PORT=${2:-5555}

echo "Connecting to device at $DEVICE_IP:$ADB_PORT..."
docker exec media-search adb connect ${DEVICE_IP}:${ADB_PORT}

echo ""
echo "Testing connection..."
docker exec media-search adb -s ${DEVICE_IP}:${ADB_PORT} shell getprop ro.product.model

echo ""
echo "=========================================="
echo "NEXT STEPS:"
echo "=========================================="
echo ""
echo "If you see 'device unauthorized' above:"
echo "  1. Look at your TV screen"
echo "  2. You should see a prompt: 'Allow USB debugging?'"
echo "  3. Check 'Always allow from this computer'"
echo "  4. Click 'OK'"
echo "  5. Run this script again to test"
echo ""
echo "If you see the device model name above:"
echo "  ADB is authorized and working!"
echo "  Playback should now launch Plex correctly"
echo ""
