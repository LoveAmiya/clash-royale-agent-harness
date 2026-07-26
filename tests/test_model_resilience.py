import unittest

from model_resilience import ModelCircuitOpenError, ModelProviderGuard


class _Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class ModelResilienceTests(unittest.TestCase):
    def test_circuit_opens_then_allows_one_half_open_probe(self):
        clock = _Clock()
        guard = ModelProviderGuard(
            provider_id="provider-test",
            failure_threshold=2,
            recovery_seconds=30,
            clock=clock,
        )

        guard.before_call("generate")
        guard.record_failure("generate", RuntimeError("one"))
        guard.before_call("generate")
        guard.record_failure("generate", RuntimeError("two"))
        self.assertEqual(guard.snapshot()["circuit_state"], "open")
        with self.assertRaises(ModelCircuitOpenError):
            guard.before_call("generate")

        clock.value = 31
        guard.before_call("probe")
        self.assertEqual(guard.snapshot()["circuit_state"], "half_open")
        with self.assertRaises(ModelCircuitOpenError):
            guard.before_call("generate")
        guard.record_success("probe")
        self.assertEqual(guard.snapshot()["circuit_state"], "closed")

    def test_capability_and_quality_metrics_are_low_cardinality(self):
        guard = ModelProviderGuard(provider_id="provider-test", failure_threshold=3, recovery_seconds=60)
        guard.record_stream_capability(supported=False, reason="no_public_delta")
        guard.record_stream_mode("fallback_chunked")
        guard.record_success("generate")

        snapshot = guard.snapshot()
        self.assertEqual(snapshot["capabilities"]["streaming"], "unsupported")
        self.assertEqual(snapshot["stream_modes"]["fallback_chunked"], 1)
        rendered = guard.render_prometheus()
        self.assertIn('provider="provider-test"', rendered)
        self.assertNotIn("no_public_delta", rendered)


if __name__ == "__main__":
    unittest.main()
