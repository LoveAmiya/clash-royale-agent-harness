"""Classify the unittest suite into reviewable quality layers.

The script is intentionally read-only: it discovers tests, counts them by
class, assigns each class to a quality layer, and optionally writes a JSON
report. It does not execute the tests.
"""

from __future__ import annotations

import argparse
import json
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


LAYERS: dict[str, dict[str, str]] = {
    "L0_unit_contract": {
        "name": "Unit and deterministic domain contracts",
        "purpose": "Pure logic, parser/skill contracts, deterministic answer builders, and small utility behavior.",
    },
    "L1_api_ui_integration": {
        "name": "API, UI, and orchestration integration",
        "purpose": "FastAPI boundaries, SSE events, browser-facing structured flows, and multi-intent orchestration.",
    },
    "L2_ai_rag_regression": {
        "name": "AI/RAG offline regression",
        "purpose": "Golden-set parser/routing cases, retrieval ranking, citation grounding, and RAG synthesis quality gates.",
    },
    "L3_resilience_security_ops": {
        "name": "Resilience, security, and operations",
        "purpose": "Fault injection, model/provider failures, rate limits, deployment contracts, data publication, and privacy boundaries.",
    },
    "L4_live_external_smoke": {
        "name": "Live model/external-system smoke",
        "purpose": "Credentialed checks that intentionally touch configured external systems and are excluded from public CI.",
    },
}


CLASS_LAYER_OVERRIDES = {
    "EvaluationCorpusContractTests": "L2_ai_rag_regression",
    "CitationBenchmarkTests": "L2_ai_rag_regression",
    "EvidenceSynthesisRetrievalTests": "L2_ai_rag_regression",
    "EvidenceSynthesisSkillTests": "L2_ai_rag_regression",
    "RAGAvailabilityMessageTests": "L2_ai_rag_regression",
    "RAGEvidenceSkillTests": "L2_ai_rag_regression",
    "RAGPreheatTests": "L2_ai_rag_regression",
    "RAGQualityTests": "L2_ai_rag_regression",
    "RetrievalBenchmarkTests": "L2_ai_rag_regression",
    "RetrievalEvidencePreservationTests": "L2_ai_rag_regression",
    "RetrievalFallbackTests": "L2_ai_rag_regression",
    "AnswerQueryRAGRoutingTests": "L2_ai_rag_regression",
    "MetaEvidenceTests": "L2_ai_rag_regression",
    "OpenAnalysisRoutingTests": "L2_ai_rag_regression",
    "StructuredAPIContractTests": "L1_api_ui_integration",
    "StructuredFrontendContractTests": "L1_api_ui_integration",
    "ProcessSSETests": "L1_api_ui_integration",
    "RuntimeLifecycleTests": "L1_api_ui_integration",
    "RuntimeEventEmitterTests": "L1_api_ui_integration",
    "MultiIntentOrchestrationTests": "L1_api_ui_integration",
    "WebVisualizationDashboardTests": "L1_api_ui_integration",
    "AnalysisBoundaryTests": "L1_api_ui_integration",
    "AlertReceiverTests": "L3_resilience_security_ops",
    "AlertingContractTests": "L3_resilience_security_ops",
    "CollectionSchedulePolicyTests": "L3_resilience_security_ops",
    "DailySnapshotStoreTests": "L3_resilience_security_ops",
    "DeploymentRoleTests": "L3_resilience_security_ops",
    "FaultInjectionEvaluationTests": "L3_resilience_security_ops",
    "LoadTestContractTests": "L3_resilience_security_ops",
    "ModelGatewayStreamTests": "L3_resilience_security_ops",
    "ModelResilienceTests": "L3_resilience_security_ops",
    "ProductionHardeningHTTPContractTests": "L3_resilience_security_ops",
    "ProductionHardeningUnitTests": "L3_resilience_security_ops",
    "QualityOperationsContractTests": "L3_resilience_security_ops",
    "RedisQuotaIntegrationTests": "L3_resilience_security_ops",
    "RollingCorpusStoreTests": "L3_resilience_security_ops",
    "RollingMaterializerTests": "L3_resilience_security_ops",
    "SnapshotAuditExportTests": "L3_resilience_security_ops",
    "SupercellLiveDataTests": "L3_resilience_security_ops",
    "SupercellPreflightTests": "L3_resilience_security_ops",
}


def _iter_tests(suite: unittest.TestSuite) -> Iterable[unittest.case.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


def _class_name(test_id: str) -> str:
    parts = test_id.split(".")
    if len(parts) < 2:
        return test_id
    return parts[-2]


def classify_test_class(class_name: str) -> str:
    return CLASS_LAYER_OVERRIDES.get(class_name, "L0_unit_contract")


def build_inventory(start_dir: str = "tests") -> dict[str, Any]:
    suite = unittest.defaultTestLoader.discover(start_dir)
    class_counts = Counter(_class_name(test.id()) for test in _iter_tests(suite))
    layers: dict[str, dict[str, Any]] = {
        layer_id: {**definition, "test_count": 0, "class_count": 0, "classes": []}
        for layer_id, definition in LAYERS.items()
    }
    by_layer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for class_name, test_count in sorted(class_counts.items()):
        layer_id = classify_test_class(class_name)
        by_layer[layer_id].append({"class": class_name, "test_count": test_count})
    for layer_id, rows in by_layer.items():
        layers[layer_id]["classes"] = rows
        layers[layer_id]["class_count"] = len(rows)
        layers[layer_id]["test_count"] = sum(row["test_count"] for row in rows)
    return {
        "project": "clash-royale-agent-harness",
        "total_tests": sum(class_counts.values()),
        "total_classes": len(class_counts),
        "layers": layers,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    inventory = build_inventory()
    print(f"total_tests: {inventory['total_tests']}")
    for layer_id, layer in inventory["layers"].items():
        print(f"{layer_id}: {layer['test_count']} tests / {layer['class_count']} classes")
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
