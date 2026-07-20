from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit


Resolver = Callable[[str, int], Awaitable[list[str]]]
BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata.azure.internal",
    "instance-data.ec2.internal",
}
BLOCKED_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


class TargetSecurityError(ValueError):
    pass


@dataclass(frozen=True)
class PinnedTarget:
    original_url: str
    request_url: str
    origin: str
    host_header: str
    sni_hostname: str
    resolved_ips: tuple[str, ...]


class TargetGuard:
    def __init__(self, resolver: Resolver | None = None) -> None:
        self._resolver = resolver or resolve_host

    async def validate(
        self,
        url: str,
        *,
        security_mode: str,
        approved_origin: str | None,
    ) -> PinnedTarget:
        parsed = _parse_target(url)
        origin = canonical_origin(url)
        if security_mode == "local_private" and approved_origin != origin:
            raise TargetSecurityError("This exact local Origin has not been approved")
        if security_mode not in {"public_only", "local_private"}:
            raise TargetSecurityError("Unknown network security mode")
        addresses = await self._resolve_addresses(parsed)
        for address in addresses:
            _validate_address(address, allow_private=security_mode == "local_private")
        return _pin_target(parsed, origin, addresses)

    async def validate_for_approval(self, url: str) -> tuple[str, tuple[str, ...]]:
        parsed = _parse_target(url)
        addresses = await self._resolve_addresses(parsed)
        for address in addresses:
            _validate_address(address, allow_private=True)
        return canonical_origin(url), tuple(str(address) for address in addresses)

    async def _resolve_addresses(self, parsed: SplitResult) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        assert parsed.hostname is not None
        try:
            literal = ipaddress.ip_address(parsed.hostname)
            return [literal]
        except ValueError:
            pass
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        raw_addresses = await self._resolver(parsed.hostname, port)
        if not raw_addresses:
            raise TargetSecurityError("Target hostname did not resolve")
        addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for value in raw_addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise TargetSecurityError("Resolver returned an invalid IP address") from exc
            if address not in addresses:
                addresses.append(address)
        return addresses


async def resolve_host(hostname: str, port: int) -> list[str]:
    def lookup() -> list[str]:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        return [str(record[4][0]) for record in records]

    try:
        return await asyncio.to_thread(lookup)
    except socket.gaierror as exc:
        raise TargetSecurityError("Target hostname could not be resolved") from exc


def canonical_origin(url: str) -> str:
    parsed = _parse_target(url)
    assert parsed.hostname is not None
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default_port
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname.lower()
    suffix = "" if port == default_port else f":{port}"
    return f"{parsed.scheme}://{host}{suffix}"


def _parse_target(url: str) -> SplitResult:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise TargetSecurityError("Target URL is invalid") from exc
    if parsed.scheme not in {"http", "https"}:
        raise TargetSecurityError("Only http and https targets are allowed")
    if not parsed.hostname:
        raise TargetSecurityError("Target URL must include a hostname")
    hostname = parsed.hostname.lower().rstrip(".")
    if parsed.username is not None or parsed.password is not None:
        raise TargetSecurityError("Credentials in target URLs are forbidden")
    if parsed.fragment:
        raise TargetSecurityError("Target URL fragments are not supported")
    if hostname in BLOCKED_HOSTS:
        raise TargetSecurityError("Cloud metadata hostnames are always blocked")
    if port is not None and not 1 <= port <= 65535:
        raise TargetSecurityError("Target port is invalid")
    return parsed


def _validate_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_private: bool,
) -> None:
    if address in BLOCKED_METADATA_IPS:
        raise TargetSecurityError("Cloud metadata addresses are always blocked")
    if address.is_multicast or address.is_unspecified or address.is_reserved:
        raise TargetSecurityError("Reserved target addresses are blocked")
    if address.is_link_local:
        raise TargetSecurityError("Link-local target addresses are blocked")
    if not allow_private and not address.is_global:
        raise TargetSecurityError("Private and local target addresses require local_private approval")


def _pin_target(
    parsed: SplitResult,
    origin: str,
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> PinnedTarget:
    assert parsed.hostname is not None
    address = addresses[0]
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default_port
    address_host = f"[{address}]" if address.version == 6 else str(address)
    request_netloc = address_host if port == default_port else f"{address_host}:{port}"
    original_host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    host_header = original_host if port == default_port else f"{original_host}:{port}"
    request_url = urlunsplit(
        (parsed.scheme, request_netloc, parsed.path or "/", parsed.query, "")
    )
    return PinnedTarget(
        original_url=urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, "")),
        request_url=request_url,
        origin=origin,
        host_header=host_header,
        sni_hostname=parsed.hostname,
        resolved_ips=tuple(str(item) for item in addresses),
    )
