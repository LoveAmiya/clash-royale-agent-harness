import unittest
from types import SimpleNamespace

import app_config  # noqa: F401 - initializes the src package path for root runs.

from clashroyale_agent.api import health, routes, startup
from clashroyale_agent.api.feedback_routes import register_feedback_routes
from clashroyale_agent.api.lifecycle import initialize_runtime_data_state, initialize_runtime_services
from clashroyale_agent.api.lifespan import build_runtime_lifespan_dependencies
from clashroyale_agent.api.preheat import resolve_rag_preheat_target
from clashroyale_agent.api.rag_preheat import (
    RAGPreheatDependencies,
    build_rag_preheat_dependencies,
    preheat_rag_retriever,
)
from clashroyale_agent.api.dataset_runtime import (
    DatasetRuntimeDependencies,
    get_dataset_catalog,
)
from clashroyale_agent.api.status_runtime import (
    StatusRouteDependencies,
    build_live_snapshot_status_from_runtime,
    build_readiness_status_from_runtime,
    build_status_route_dependencies,
    register_runtime_status_routes,
)
from clashroyale_agent.api.runtime import (
    RuntimeAppDependencies,
    build_runtime_app_dependencies,
    create_registered_runtime_app,
)
from clashroyale_agent.api.runtime_facade import RuntimeFacade
from clashroyale_agent.api.snapshot_lifecycle import (
    SnapshotLifecycleDependencies,
    ensure_live_snapshot,
)
from clashroyale_agent.api.process_routes import (
    ProcessRuntimeDependencies,
    build_process_runtime_dependencies,
    handle_process_request,
    register_process_routes,
)
from clashroyale_agent.api.status import build_health_payload
from clashroyale_agent.api.status_routes import register_status_routes
from clashroyale_agent.qa.runtime_dependencies import (
    build_dataset_runtime_dependencies,
    build_snapshot_lifecycle_dependencies,
)
from clashroyale_agent.qa.runtime_parsing import AnswerParseDependencies, parse_answer_request
from clashroyale_agent.qa.runtime_pipeline import run_runtime_answer_pipeline
from clashroyale_agent.qa.synthesis_dependencies import EvidenceSynthesisDependencies
from clashroyale_agent.qa.runtime_answering import (
    AnswerPipelineDependencies,
    execute_grounded_answer,
    run_answer_pipeline,
)


class ApiPackageBoundaryTests(unittest.TestCase):
    def test_runtime_answering_owns_grounded_execution(self):
        self.assertTrue(callable(execute_grounded_answer))

    def test_runtime_answering_owns_answer_pipeline_boundary(self):
        self.assertTrue(AnswerPipelineDependencies)
        self.assertTrue(callable(run_answer_pipeline))
    def test_routes_facade_exposes_registered_route_groups(self):
        self.assertIs(routes.register_feedback_routes, register_feedback_routes)
        self.assertIs(routes.register_process_routes, register_process_routes)
        self.assertIs(routes.register_status_routes, register_status_routes)

    def test_health_facade_exposes_status_payload_builders(self):
        self.assertIs(health.build_health_payload, build_health_payload)

    def test_startup_facade_exposes_lifecycle_and_preheat_helpers(self):
        self.assertIs(startup.initialize_runtime_data_state, initialize_runtime_data_state)
        self.assertIs(startup.initialize_runtime_services, initialize_runtime_services)
        self.assertIs(startup.resolve_rag_preheat_target, resolve_rag_preheat_target)
        self.assertIs(startup.RAGPreheatDependencies, RAGPreheatDependencies)
        self.assertTrue(callable(build_rag_preheat_dependencies))
        self.assertIs(startup.preheat_rag_retriever, preheat_rag_retriever)
        self.assertTrue(callable(build_runtime_lifespan_dependencies))

    def test_snapshot_lifecycle_exposes_explicit_refresh_boundary(self):
        self.assertTrue(SnapshotLifecycleDependencies)
        self.assertTrue(callable(ensure_live_snapshot))

    def test_dataset_runtime_exposes_explicit_dependency_boundary(self):
        self.assertTrue(DatasetRuntimeDependencies)
        self.assertTrue(callable(get_dataset_catalog))

    def test_status_runtime_exposes_route_dependency_boundary(self):
        self.assertTrue(StatusRouteDependencies)
        self.assertTrue(callable(build_live_snapshot_status_from_runtime))
        self.assertTrue(callable(build_readiness_status_from_runtime))
        self.assertTrue(callable(build_status_route_dependencies))
        self.assertTrue(callable(register_runtime_status_routes))

    def test_process_routes_exposes_explicit_handler_boundary(self):
        self.assertTrue(ProcessRuntimeDependencies)
        self.assertTrue(callable(build_process_runtime_dependencies))
        self.assertTrue(callable(handle_process_request))

    def test_runtime_exposes_registered_app_assembly_boundary(self):
        self.assertTrue(RuntimeAppDependencies)
        self.assertTrue(callable(build_runtime_app_dependencies))
        self.assertTrue(callable(create_registered_runtime_app))

    def test_runtime_facade_owns_compatibility_delegates(self):
        self.assertTrue(RuntimeFacade)

    def test_runtime_facade_keeps_live_sample_settings_runtime_bound(self):
        runtime = {
            "DAILY_TARGET_BATTLES": 100,
            "LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED": True,
            "build_fixed_live_sample_settings": lambda app, **kwargs: {"app": app, **kwargs},
        }

        payload = RuntimeFacade(runtime).live_sample_settings(SimpleNamespace(), "refreshing")

        self.assertEqual(payload["fixed_target_battles"], 100)
        self.assertTrue(payload["can_update_target"])
        self.assertEqual(payload["refresh_status"], "refreshing")

    def test_qa_runtime_dependencies_exposes_lifecycle_builder(self):
        self.assertTrue(callable(build_snapshot_lifecycle_dependencies))
        self.assertTrue(callable(build_dataset_runtime_dependencies))

    def test_qa_runtime_parsing_exposes_answer_parse_boundary(self):
        self.assertTrue(AnswerParseDependencies)
        self.assertTrue(callable(parse_answer_request))

    def test_qa_runtime_pipeline_exposes_build_answer_owner(self):
        self.assertTrue(callable(run_runtime_answer_pipeline))

    def test_synthesis_dependencies_have_an_owned_contract_module(self):
        self.assertTrue(EvidenceSynthesisDependencies)


if __name__ == "__main__":
    unittest.main()
