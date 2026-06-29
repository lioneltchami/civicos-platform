"""
Shared utility functions for the Forms building block.
"""
import ipaddress


def _mask_ip(ip: str) -> str:
    """
    Mask an IP address for privacy (PIPEDA compliance).

    IPv4: return first two octets only, e.g. "192.168.x.x"
    IPv6: return first two groups (32 bits), mask the rest.
    Returns empty string for empty input, "masked" for unparseable values.
    """
    if not ip:
        return ""
    try:
        addr = ipaddress.ip_address(ip)
        if addr.version == 4:
            parts = ip.split(".")
            return ".".join(parts[:2]) + ".x.x"
        else:
            # IPv6: show first 32 bits (2 groups), mask the rest
            parts = ip.split(":")
            return ":".join(parts[:2]) + ":xxxx:xxxx:xxxx:xxxx:xxxx:xxxx"
    except ValueError:
        return "masked"
