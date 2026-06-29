"""
Shared utility functions for the Forms building block.
"""
import ipaddress


def _mask_ip(ip: str) -> str:
    """
    Mask an IP address for privacy-preserving storage (PIPEDA compliance).

    IPv4: keeps first 3 octets, zeroes last octet.
          192.168.1.123 → 192.168.1.0
    IPv6: keeps first 32 bits (/32 prefix), zeroes the rest.
          2001:db8::1 → 2001:db8::
    Returns '0.0.0.0' on any parsing error.
    """
    import ipaddress as _ipaddress

    if not ip:
        return "0.0.0.0"
    try:
        addr = _ipaddress.ip_address(ip.strip())
        if isinstance(addr, _ipaddress.IPv4Address):
            parts = str(addr).split(".")
            return f"{parts[0]}.{parts[1]}.{parts[2]}.0"
        else:  # IPv6
            network = _ipaddress.ip_network(f"{addr}/32", strict=False)
            return str(network.network_address)
    except ValueError:
        return "0.0.0.0"
