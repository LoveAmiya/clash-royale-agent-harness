"""Preflight checks for long-running Supercell snapshot collection.

The collector is expensive in wall-clock time and should not start when the
current outbound IP is not accepted by the Supercell API token. This module is
intentionally independent from the runtime so it can run before the backend
starts and without initializing parser, RAG, embedding, or model code.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from ipaddress import ip_address, ip_network
from typing import Any


DEFAULT_PUBLIC_IP_URL = "https://api.ipify.org?format=json"
PUBLIC_IP_FALLBACK_URLS = (
    "https://api64.ipify.org?format=json",
    "https://ifconfig.me/ip",
    "https://ipinfo.io/ip",
    "https://icanhazip.com",
)
DEFAULT_SUPERCELL_PROBE_URL = "https://api.clashroyale.com/v1/locations/global/rankings/players?limit=1"


def _decode_base64url_json(segment: str) -> dict[str, Any]:
    padded = segment + ("=" * (-len(segment) % 4))
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def decode_token_client_cidrs(token: str) -> list[str]:
    """Return client CIDRs from a Supercell JWT without exposing token text."""
    parts = token.split(".")
    if len(parts) < 2:
        return []
    try:
        payload = _decode_base64url_json(parts[1])
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return []

    cidrs: list[str] = []
    for limit in payload.get("limits", []):
        if not isinstance(limit, dict) or limit.get("type") != "client":
            continue
        for cidr in limit.get("cidrs", []):
            if isinstance(cidr, str) and cidr.strip():
                cidrs.append(cidr.strip())
    return cidrs


def is_ip_allowed_by_cidrs(current_ip: str, allowed_cidrs: list[str]) -> bool:
    """Support exact IP allowlist entries and CIDR ranges."""
    try:
        candidate = ip_address(current_ip)
    except ValueError:
        return False
    for cidr in allowed_cidrs:
        try:
            if candidate in ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def fetch_public_ip(ip_url: str = DEFAULT_PUBLIC_IP_URL, timeout_seconds: float = 20.0) -> str:
    with urllib.request.urlopen(ip_url, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body.strip()
    ip = parsed.get("ip")
    if not isinstance(ip, str) or not ip.strip():
        raise ValueError("public IP probe did not return an ip field")
    return ip.strip()


def fetch_public_ip_with_fallbacks(ip_url: str = DEFAULT_PUBLIC_IP_URL, timeout_seconds: float = 20.0) -> str:
    urls = [ip_url]
    if ip_url == DEFAULT_PUBLIC_IP_URL:
        urls.extend(url for url in PUBLIC_IP_FALLBACK_URLS if url not in urls)
    last_error: Exception | None = None
    for url in urls:
        try:
            return fetch_public_ip(ip_url=url, timeout_seconds=timeout_seconds)
        except (ValueError, urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError("no public IP probe URLs configured")


def probe_supercell_api(
    token: str,
    probe_url: str = DEFAULT_SUPERCELL_PROBE_URL,
    timeout_seconds: float = 20.0,
) -> tuple[bool, int | None, str | None]:
    request = urllib.request.Request(
        probe_url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return 200 <= int(response.status) < 300, int(response.status), None
    except urllib.error.HTTPError as exc:
        return False, int(exc.code), type(exc).__name__
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, None, type(exc).__name__


def build_preflight_report(
    *,
    token: str,
    current_ip: str | None,
    allowed_cidrs: list[str],
    official_probe_status: int | None,
    official_probe_ok: bool,
    official_probe_error: str | None,
) -> dict[str, Any]:
    token_configured = bool(token.strip())
    current_ip_allowed = bool(current_ip and is_ip_allowed_by_cidrs(current_ip, allowed_cidrs))

    if not token_configured:
        status = "token_missing"
    elif not allowed_cidrs:
        status = "token_allowlist_missing"
    elif not current_ip_allowed:
        status = "ip_mismatch"
    elif not official_probe_ok:
        status = "official_probe_failed"
    else:
        status = "ready"

    return {
        "ready": status == "ready",
        "status": status,
        "current_ip": current_ip,
        "allowed_cidrs": list(allowed_cidrs),
        "current_ip_allowed": current_ip_allowed,
        "official_probe_status": official_probe_status,
        "official_probe_error": official_probe_error,
        "token_configured": token_configured,
    }


def run_preflight(
    *,
    token: str,
    ip_url: str = DEFAULT_PUBLIC_IP_URL,
    probe_url: str = DEFAULT_SUPERCELL_PROBE_URL,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    allowed_cidrs = decode_token_client_cidrs(token) if token else []
    current_ip: str | None = None
    public_ip_error: str | None = None
    try:
        current_ip = fetch_public_ip_with_fallbacks(ip_url=ip_url, timeout_seconds=timeout_seconds)
    except (ValueError, urllib.error.URLError, TimeoutError, OSError) as exc:
        public_ip_error = type(exc).__name__

    official_probe_ok = False
    official_probe_status: int | None = None
    official_probe_error: str | None = None
    if token and current_ip and is_ip_allowed_by_cidrs(current_ip, allowed_cidrs):
        official_probe_ok, official_probe_status, official_probe_error = probe_supercell_api(
            token,
            probe_url=probe_url,
            timeout_seconds=timeout_seconds,
        )

    report = build_preflight_report(
        token=token,
        current_ip=current_ip,
        allowed_cidrs=allowed_cidrs,
        official_probe_status=official_probe_status,
        official_probe_ok=official_probe_ok,
        official_probe_error=official_probe_error,
    )
    if public_ip_error and report["status"] != "ready":
        report["public_ip_error"] = public_ip_error
        if report["status"] == "ip_mismatch":
            report["status"] = "public_ip_probe_failed"
    return report


def exit_code_for_status(status: str) -> int:
    return {
        "ready": 0,
        "token_missing": 2,
        "token_allowlist_missing": 2,
        "public_ip_probe_failed": 3,
        "ip_mismatch": 3,
        "official_probe_failed": 4,
    }.get(status, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight Supercell collector IP and token access.")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--ip-url", default=DEFAULT_PUBLIC_IP_URL)
    parser.add_argument("--probe-url", default=DEFAULT_SUPERCELL_PROBE_URL)
    args = parser.parse_args(argv)

    token = os.getenv("SUPERCELL_API_TOKEN", "")
    report = run_preflight(
        token=token,
        ip_url=args.ip_url,
        probe_url=args.probe_url,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return exit_code_for_status(str(report.get("status", "")))


if __name__ == "__main__":
    sys.exit(main())
