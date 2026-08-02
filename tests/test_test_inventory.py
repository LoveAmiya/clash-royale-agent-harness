import unittest

from evaluation.test_inventory import build_inventory, classify_test_class


class TestInventoryTests(unittest.TestCase):
    def test_inventory_sums_layers_to_discovered_total(self) -> None:
        inventory = build_inventory()
        layer_total = sum(layer["test_count"] for layer in inventory["layers"].values())

        self.assertEqual(inventory["total_tests"], layer_total)
        self.assertGreater(inventory["layers"]["L2_ai_rag_regression"]["test_count"], 0)
        self.assertGreater(inventory["layers"]["L3_resilience_security_ops"]["test_count"], 0)

    def test_known_quality_classes_are_not_left_in_default_layer(self) -> None:
        self.assertEqual(classify_test_class("EvaluationCorpusContractTests"), "L2_ai_rag_regression")
        self.assertEqual(classify_test_class("SupercellLiveDataTests"), "L3_resilience_security_ops")
        self.assertEqual(classify_test_class("StructuredAPIContractTests"), "L1_api_ui_integration")
