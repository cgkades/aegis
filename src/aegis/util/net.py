"""Network address helpers."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def is_private_url(url: str) -> bool:
    """True if the URL host is not safe for remote MCP / outbound fetches.

    Covers loopback, RFC1918, link-local (incl. cloud metadata 169.254.0.0/16),
    ULA, CGNAT, and unspecified. On parse failure we fail closed (private).
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return True
    if not host:
        return True
    if host in {"localhost", "0.0.0.0", "::", "::1"}:
        return True
    # Strip IPv6 brackets if present (urlparse usually handles this).
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Hostname: check common private DNS names; literal IPs handled above.
        if host.endswith(".local") or host.endswith(".localhost"):
            return True
        # Non-literal hostnames are treated as public (caller may resolve later).
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
