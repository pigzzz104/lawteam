"""SSRF and external-fetch domain allowlist guards."""

from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from app.config import Settings, get_settings

_CLOUD_METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",
        "169.254.170.2",
        "100.100.100.200",
        "metadata.google.internal",
        "metadata.goog",
    }
)

TRUSTED_SMS_PORTAL_SUFFIXES: tuple[str, ...] = (
    "court.gov.cn",
    "12309.gov.cn",
    "gdems.com",
    "10102368.com",
    "hbfy.gov.cn",
    "sifayun.com",
    "cdfy12368.gov.cn",
    "cietac.org",
    "shiac.org",
    "scia.com.cn",
    "bjac.org.cn",
    "gzac.org",
    "chinatax.gov.cn",
    "jss.com.cn",
    "nuonuo.com",
    "baiwang.com",
    "bwfapiao.com",
    "gov.cn",
)


class UnsafeUrlError(ValueError):
    """Raised when an outbound URL violates SSRF or domain allowlist policy."""


_BENCHMARK_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")


def _is_private_or_reserved_ip(
    raw_ip: str, *, allow_benchmark_fake_ip: bool = False
) -> bool:
    try:
        ip_obj = ipaddress.ip_address(raw_ip.split("%", 1)[0])
    except ValueError:
        return True
    if (
        allow_benchmark_fake_ip
        and isinstance(ip_obj, ipaddress.IPv4Address)
        and ip_obj in _BENCHMARK_FAKE_IP_NET
    ):
        # Local proxy TUN modes (e.g. Clash/Surge fake-IP) resolve domains into 198.18.0.0/15.
        return False
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def _resolve_host_ips(host: str, port: int) -> tuple[list[str], bool]:
    clean = host.strip("[]")
    try:
        ipaddress.ip_address(clean)
        return [clean], True
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(clean, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeUrlError(f"无法解析目标域名: {clean}") from exc
    ips = [str(info[4][0]) for info in infos if info[4]]
    if not ips:
        raise UnsafeUrlError(f"域名无可用 IP 地址: {clean}")
    return ips, False


def host_matches_allowlist(host: str, suffixes: tuple[str, ...]) -> bool:
    clean = host.strip().lower().rstrip(".")
    return any(clean == s or clean.endswith("." + s) for s in suffixes)


def validate_external_fetch_url(
    url: str,
    *,
    require_portal_allowlist: bool = False,
) -> str:
    """Validate an SMS-extracted URL against scheme, private IP, and optional domain allowlist."""
    clean = (url or "").strip()
    parsed = urlparse(clean)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeUrlError("仅支持标准 http/https 外部链接")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("链接禁止包含内嵌认证信息")

    host = parsed.hostname.lower()
    if host in _CLOUD_METADATA_HOSTS or host in ("localhost", "localhost.localdomain"):
        raise UnsafeUrlError(f"禁止访问受限主机: {host}")

    if require_portal_allowlist and not host_matches_allowlist(host, TRUSTED_SMS_PORTAL_SUFFIXES):
        raise UnsafeUrlError(f"链接域名不在司法/发票官方白名单内: {host}")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ips, is_literal_ip = _resolve_host_ips(host, port)
    for ip_str in ips:
        if _is_private_or_reserved_ip(ip_str, allow_benchmark_fake_ip=not is_literal_ip):
            raise UnsafeUrlError(f"禁止访问内网或保留地址 ({ip_str})")

    return clean


def validate_webhook_url(url: str, *, settings: Optional[Settings] = None) -> str:
    """Validate user-configured notification webhook URL (blocks cloud metadata; LAN requires allowlist)."""
    cfg = settings or get_settings()
    clean = (url or "").strip()
    parsed = urlparse(clean)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeUrlError("Webhook 地址必须以 http:// 或 https:// 开头")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("Webhook 地址禁止包含内嵌认证信息")

    host = parsed.hostname.lower()
    if host in _CLOUD_METADATA_HOSTS:
        raise UnsafeUrlError("禁止将云元数据地址设为 Webhook")

    if host in cfg.internal_webhook_host_allowlist:
        return clean

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ips, is_literal_ip = _resolve_host_ips(host, port)
    for ip_str in ips:
        if ip_str in _CLOUD_METADATA_HOSTS or ip_str.startswith("169.254."):
            raise UnsafeUrlError("禁止访问云元数据或链路本地地址")
        if _is_private_or_reserved_ip(ip_str, allow_benchmark_fake_ip=not is_literal_ip):
            raise UnsafeUrlError(
                f"Webhook 目标地址 {host} 解析到内网地址 ({ip_str})，如需内网机器人请由管理员加入白名单"
            )
    return clean


async def safe_async_get(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 25.0,
    max_redirects: int = 5,
    require_portal_allowlist: bool = False,
) -> httpx.Response:
    """Perform an async HTTP GET validating every redirect hop against SSRF rules."""
    current_url = validate_external_fetch_url(
        url, require_portal_allowlist=require_portal_allowlist
    )
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers=headers,
    ) as client:
        for _ in range(max_redirects + 1):
            resp = await client.get(current_url)
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                next_url = urljoin(current_url, resp.headers["location"].strip())
                current_url = validate_external_fetch_url(
                    next_url, require_portal_allowlist=False
                )
                continue
            return resp
    raise UnsafeUrlError("外部链接重定向次数过多")
