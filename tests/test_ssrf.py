"""Regression lock for the SSRF hardening in utils._is_safe_url.

Before the fix, only IP *literals* were checked; any hostname was trusted, so a
name resolving to an internal address (DNS-based SSRF) slipped through.
"""

import socket

import pytest

from librarian.core.security import _ip_is_safe, _is_safe_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://8.8.8.8/x", True),           # public IP literal
        ("https://1.1.1.1/x", True),
        ("http://127.0.0.1/x", False),        # loopback
        ("http://169.254.169.254/x", False),  # cloud metadata / link-local
        ("http://10.0.0.5/x", False),         # private
        ("http://192.168.1.1/x", False),
        ("http://172.16.0.1/x", False),
        ("http://0.0.0.0/x", False),          # unspecified
        ("http://localhost/x", False),
        ("ftp://8.8.8.8/x", False),           # non-http scheme
        ("not-a-url", False),
        ("", False),
    ],
)
def test_ip_literals_and_schemes(url, expected):
    assert _is_safe_url(url) is expected


def test_nat64_and_ipv4_mapped_validate_embedded_ipv4():
    """On DNS64/NAT64 networks getaddrinfo returns 64:ff9b:: addresses embedding the
    real IPv4 — validate that, not the (reserved-flagged) v6 wrapper."""
    assert _ip_is_safe("64:ff9b::b32b:a7a4") is True    # embeds 179.43.167.164 (public)
    assert _ip_is_safe("64:ff9b::7f00:1") is False      # embeds 127.0.0.1 (loopback)
    assert _ip_is_safe("64:ff9b::a00:5") is False       # embeds 10.0.0.5 (private)
    assert _ip_is_safe("::ffff:8.8.8.8") is True        # IPv4-mapped public
    assert _ip_is_safe("::ffff:127.0.0.1") is False     # IPv4-mapped loopback


def _fake_getaddrinfo(mapping):
    def _resolver(host, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror("name resolution failed")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], 0))]

    return _resolver


def test_hostname_resolving_to_internal_is_blocked(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo(
            {
                "metadata.internal": "169.254.169.254",
                "sneaky.example.com": "127.0.0.1",
            }
        ),
    )
    assert _is_safe_url("http://metadata.internal/latest/meta-data") is False
    assert _is_safe_url("http://sneaky.example.com/x") is False


def test_public_hostname_is_allowed(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo({"annas-archive.gl": "104.26.0.1"})
    )
    assert _is_safe_url("https://annas-archive.gl/search?q=x") is True


def test_unresolvable_hostname_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({}))
    assert _is_safe_url("http://does-not-resolve.invalid/x") is False


def test_multi_homed_host_blocked_if_any_address_internal(monkeypatch):
    # A host that returns both a public and a private address must be rejected.
    def resolver(host, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    assert _is_safe_url("http://dual.example.com/x") is False


def test_ip_is_safe_helper():
    assert _ip_is_safe("93.184.216.34") is True
    assert _ip_is_safe("127.0.0.1") is False
    assert _ip_is_safe("::1") is False
    assert _ip_is_safe("garbage") is False
