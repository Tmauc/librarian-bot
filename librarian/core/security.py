"""SSRF protection helpers (moved from utils.py)."""

import ipaddress
import socket
from urllib.parse import urlparse

_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def _ip_is_safe(ip_str: str) -> bool:
    """Return True only for a public, routable IP address.

    On NAT64 / DNS64 networks ``getaddrinfo`` returns an IPv6 address that embeds the
    real IPv4 (e.g. ``64:ff9b::b32b:a7a4`` for 179.43.167.164). The raw v6 form is
    flagged ``is_reserved`` and would be wrongly rejected, so when we recognise an
    IPv4-mapped (``::ffff:0:0/96``) or NAT64 (``64:ff9b::/96``) address we validate the
    EMBEDDED IPv4 instead — which still blocks a NAT64 wrapping 127.0.0.1 / a private IP.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped
        if embedded is None and ip in _NAT64_PREFIX:
            embedded = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            ip = embedded
    return not (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _is_safe_url(url: str) -> bool:
    """Return False if the URL targets an internal/private resource (SSRF protection).

    IP literals are validated directly. Hostnames are RESOLVED and rejected if any
    resolved address is internal — this closes the DNS-based bypass where a name
    like ``metadata.internal`` or ``x.attacker.com`` points at 169.254.169.254 /
    127.0.0.1.

    Caveat: a determined attacker can still race DNS (the resolver here and httpx's
    own resolution at connect time are separate lookups — TOCTOU). Fully closing
    that needs IP pinning at the transport layer; this check blocks the practical
    cases. Resolution is a blocking call, kept acceptable at this scale.
    """
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        host = p.hostname or ""
        if not host or host.lower() == "localhost":
            return False

        # IP literal: validate without a DNS lookup.
        try:
            ipaddress.ip_address(host)
            return _ip_is_safe(host)
        except ValueError:
            pass

        # Hostname: resolve every address and require all of them to be public.
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
        if not infos:
            return False
        return all(_ip_is_safe(info[4][0]) for info in infos)
    except Exception:
        return False
