"""Network address helpers."""

from __future__ import annotations

from urllib.parse import urlparse


def is_private_url(url: str) -> bool:
    """True if the URL's host is loopback or an RFC1918 private address.

    Parses the hostname (not a substring match) so a path or query containing
    something like "10." can't produce a false positive, and "https://10.x" is
    correctly detected. On parse failure we fail closed (treat as private).
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return True
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return True
    if host.startswith("127."):
        return True
    if host.startswith("10.") or host.startswith("192.168."):
        return True
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
            if 16 <= second <= 31:
                return True
        except (IndexError, ValueError):
            pass
    return False
