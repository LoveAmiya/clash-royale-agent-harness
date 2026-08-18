import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.

from clashroyale_agent.api import health, routes, startup
from clashroyale_agent.api.feedback_routes import register_feedback_routes
from clashroyale_agent.api.lifecycle import initialize_runtime_data_state, initialize_runtime_services
from clashroyale_agent.api.preheat import resolve_rag_preheat_target
from clashroyale_agent.api.rag_preheat import RAGPreheatDependencies, preheat_rag_retriever
from clashroyale_agent.api.dataset_runtime import (
    DatasetRuntimeDependencies,
    get_dataset_catalog,
)
from clashroyale_agent.api.status_runtime import (
    StatusRouteDependencies,
    register_runtime_status_routes,
)
from clashroyale_agent.api.runtime import RuntimeAppDependencies, create_registered_runtime_app
from clashroyale_agent.api.snapshot_lifecycle import (
    SnapshotLifecycleDependencies,
    ensure_live_snapshot,
)
from clashroyale_agent.api.process_routes import (
    ProcessRuntimeDependencies,
    handle_process_request,
    register_process_routes,
)
from clashroyale_agent.api.status import build_health_payload
from clashroyale_agent.api.status_routes import register_status_routes


class ApiPackageBoundaryTests(unittest.TestCase):
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
        self.assertIs(startup.preheat_rag_retriever, preheat_rag_retriever)

    def test_snapshot_lifecycle_exposes_explicit_refresh_boundary(self):
        self.assertTrue(SnapshotLifecycleDependencies)
        self.assertTrue(callable(ensure_live_snapshot))

    def test_dataset_runtime_exposes_explicit_dependency_boundary(self):
        self.assertTrue(DatasetRuntimeDependencies)
        self.assertTrue(callable(get_dataset_catalog))

    def test_status_runtime_exposes_route_dependency_boundary(self):
        self.assertTrue(StatusRouteDependencies)
        self.assertTrue(callable(register_runtime_status_routes))

    def test_process_routes_exposes_explicit_handler_boundary(self):
        self.assertTrue(ProcessRuntimeDependencies)
        self.assertTrue(callable(handle_process_request))

    def test_runtime_exposes_registered_app_assembly_boundary(self):
        self.assertTrue(RuntimeAppDependencies)
        self.assertTrue(callable(create_registered_runtime_app))


if __name__ == "__main__":
    unittest.main()
