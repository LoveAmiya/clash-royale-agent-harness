import base64
import json
import unittest

from supercell_preflight import (
    build_preflight_report,
    decode_token_client_cidrs,
    fetch_public_ip_with_fallbacks,
    is_ip_allowed_by_cidrs,
)


def _jwt_with_payload(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    return f"header.{encoded}.signature"


class SupercellPreflightTests(unittest.TestCase):
    def test_decodes_client_ip_allowlist_from_supercell_jwt_without_secret_output(self):
        token = _jwt_with_payload(
            {
                "iss": "supercell",
                "limits": [
                    {"type": "throttling", "tier": "developer/silver"},
                    {"type": "client", "cidrs": ["198.51.100.42", "203.0.113.0/24"]},
                ],
            }
        )

        self.assertEqual(
            decode_token_client_cidrs(token),
            ["198.51.100.42", "203.0.113.0/24"],
        )

    def test_ip_allowlist_supports_single_ips_and_cidrs(self):
        self.assertTrue(is_ip_allowed_by_cidrs("198.51.100.42", ["198.51.100.42"]))
        self.assertTrue(is_ip_allowed_by_cidrs("203.0.113.119", ["203.0.113.0/24"]))
        self.assertFalse(is_ip_allowed_by_cidrs("192.0.2.39", ["198.51.100.42", "203.0.113.0/24"]))

    def test_report_marks_ip_mismatch_before_official_probe(self):
        token = "secret.token.value"

        report = build_preflight_report(
            token=token,
            current_ip="192.0.2.39",
            allowed_cidrs=["198.51.100.42"],
            official_probe_status=None,
            official_probe_ok=False,
            official_probe_error=None,
        )

        self.assertEqual(report["status"], "ip_mismatch")
        self.assertFalse(report["ready"])
        self.assertFalse(report["current_ip_allowed"])
        self.assertNotIn(token, json.dumps(report, sort_keys=True))

    def test_report_requires_successful_official_probe_even_when_ip_matches(self):
        report = build_preflight_report(
            token="secret.token.value",
            current_ip="198.51.100.42",
            allowed_cidrs=["198.51.100.42"],
            official_probe_status=403,
            official_probe_ok=False,
            official_probe_error="HTTPError",
        )

        self.assertEqual(report["status"], "official_probe_failed")
        self.assertFalse(report["ready"])
        self.assertEqual(report["official_probe_status"], 403)

    def test_report_is_ready_when_ip_and_official_probe_pass(self):
        report = build_preflight_report(
            token="secret.token.value",
            current_ip="198.51.100.42",
            allowed_cidrs=["198.51.100.42"],
            official_probe_status=200,
            official_probe_ok=True,
            official_probe_error=None,
        )

        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["ready"])

    def test_public_ip_probe_uses_fallback_when_default_endpoint_fails(self):
        calls = []

        def fake_fetch(*, ip_url, timeout_seconds):
            calls.append(ip_url)
            if len(calls) == 1:
                raise OSError("reset")
            return "198.51.100.42"

        import supercell_preflight

        original = supercell_preflight.fetch_public_ip
        try:
            supercell_preflight.fetch_public_ip = fake_fetch
            self.assertEqual(fetch_public_ip_with_fallbacks(timeout_seconds=1), "198.51.100.42")
        finally:
            supercell_preflight.fetch_public_ip = original

        self.assertGreaterEqual(len(calls), 2)
