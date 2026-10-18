"""v173 (Tester FF-7): the shared cloud-metadata block list.

Every outbound path that may otherwise reach the LAN (Import from Prowlarr, Torznab/self-hosted
trackers) refuses these, and the public-only paths (pasted links, trackers) refuse them on top of
their wider rules. Link-local (169.254.0.0/16, fe80::/10) is refused by those paths as a range."""
import ipaddress
from typing import Union

METADATA_ADDRESSES = frozenset({
    '169.254.169.254',      # AWS / GCP / Azure / OpenStack / DigitalOcean
    '169.254.170.2',        # AWS ECS task metadata
    'fd00:ec2::254',        # AWS IPv6 IMDS
    '100.100.100.200',      # Alibaba Cloud
})


def is_metadata(addr: Union[str, ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    """A cloud metadata address, also when written as IPv4-mapped IPv6 (::ffff:100.100.100.200)."""
    try:
        ip = ipaddress.ip_address(str(addr).split('%', 1)[0].strip('[]'))
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip) in METADATA_ADDRESSES
