import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HybridRetrieverConfigCompatibilityTests(unittest.TestCase):
    def test_import_uses_defaults_when_cached_config_predates_fusion_settings(self):
        legacy_config = types.ModuleType("app_config")
        legacy_config.EMBED_BATCH_SIZE = 64
        legacy_config.EMBED_MODEL = "bge-m3"
        legacy_config.OLLAMA_EMBED_TIMEOUT_SECONDS = 120
        legacy_config.OLLAMA_EMBED_URL = "http://127.0.0.1:11434/api/embed"
        legacy_config.RAG_DOCS_FILE = ROOT / "data" / "rag_documents.json"

        module_name = "_hybrid_retriever_legacy_config_test"
        previous_config = sys.modules.get("app_config")
        try:
            sys.modules["app_config"] = legacy_config
            spec = importlib.util.spec_from_file_location(module_name, ROOT / "hybrid_retriever.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(module_name, None)
            if previous_config is None:
                sys.modules.pop("app_config", None)
            else:
                sys.modules["app_config"] = previous_config

        self.assertEqual(module.RETRIEVAL_FUSION_MODE, "rrf")
        self.assertEqual(module.RETRIEVAL_RRF_K, 60)


if __name__ == "__main__":
    unittest.main()
