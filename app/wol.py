"""
wol.py — Wake-on-LAN magic packet utility.

Sends a standard 102-byte magic packet (6x 0xFF + 16x target MAC)
via UDP broadcast to 255.255.255.255:9.  Uses only stdlib.
"""

from app.err_text import public as _err_public
import logging
import re
import socket

logger = logging.getLogger(__name__)

_MAC_RE = re.compile(r'^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$')


def is_valid_mac(mac: str) -> bool:
    """Validate MAC address format (AA:BB:CC:DD:EE:FF or AA-BB-CC-DD-EE-FF)."""
    return bool(_MAC_RE.match(mac.strip())) if mac else False


def send_wol(mac_address: str) -> dict:
    """Send a Wake-on-LAN magic packet.

    Returns: {"ok": bool, "detail": str}
    """
    mac = mac_address.strip()
    if not is_valid_mac(mac):
        return {"ok": False, "detail": f"Invalid MAC address: {mac}"}

    # Normalize to bare hex
    mac_bytes = bytes.fromhex(mac.replace(':', '').replace('-', ''))
    # Magic packet: 6x 0xFF + 16x MAC
    packet = b'\xff' * 6 + mac_bytes * 16

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, ('255.255.255.255', 9))
        sock.close()
        logger.info("WoL magic packet sent to %s", mac)
        return {"ok": True, "detail": f"WoL magic packet sent to {mac}"}
    except Exception as e:
        logger.error("WoL send failed for %s: %s", mac, e)
        return {"ok": False, "detail": _err_public(e, "WoL send failed", "wol")}
