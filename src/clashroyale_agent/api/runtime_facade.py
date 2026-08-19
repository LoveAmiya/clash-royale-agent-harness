from __future__ import annotations

from typing import Any, Mapping


class RuntimeFacade:
    """Keep root runtime compatibility names thin and patch-friendly."""

    def __init__(self, runtime: Mapping[str, Any]) -> None:
        self.runtime = runtime

    def active_snapshot_id(self, app: Any) -> str | None:
        return self.runtime["active_snapshot_id"](app)

    def rag_alignment_state(self, app: Any) -> dict:
        return self.runtime["build_rag_alignment_state"](app)

    def public_rag_validation(self, report: object) -> dict | None:
        return self.runtime["public_rag_validation"](report)

    def activate_snapshot_state(self, app: Any, snapshot: dict) -> None:
        self.runtime["activate_snapshot_state"](
            app,
            snapshot,
            now_monotonic=self.runtime["time"].monotonic(),
            target_battles=self.runtime["DAILY_TARGET_BATTLES"],
        )

    def preheat_retriever(
        self,
        app: Any,
        *,
        candidate_snapshot: dict | None = None,
        activate_snapshot: bool = False,
    ) -> Any:
        dependencies = self.runtime["build_rag_preheat_dependencies"](
            self.runtime["RAGPreheatDependencies"],
            self.runtime,
        )
        return self.runtime["preheat_rag_retriever"](
            app,
            dependencies=dependencies,
            candidate_snapshot=candidate_snapshot,
            activate_snapshot=activate_snapshot,
        )

    def ensure_retriever(self, app: Any) -> Any:
        return self.runtime["find_active_rag_retriever"](
            app,
            active_snapshot_id=self.runtime["_active_snapshot_id"](app),
        )

    async def preheat_retriever_in_background(
        self,
        app: Any,
        *,
        candidate_snapshot: dict | None = None,
        activate_snapshot: bool = False,
    ) -> None:
        await self.runtime["run_rag_preheat_in_thread"](
            self.runtime["preheat_retriever"],
            app,
            candidate_snapshot=candidate_snapshot,
            activate_snapshot=activate_snapshot,
        )

    def live_sample_target(self, app: Any) -> int:
        return self.runtime["fixed_live_sample_target"](self.runtime["DAILY_TARGET_BATTLES"])

    def live_sample_settings(self, app: Any, refresh_status: str = "ready") -> dict:
        return self.runtime["build_fixed_live_sample_settings"](
            app,
            fixed_target_battles=self.runtime["DAILY_TARGET_BATTLES"],
            can_update_target=self.runtime["LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED"],
            refresh_status=refresh_status,
        )

    def runtime_summary(self, app: Any) -> dict:
        return self.runtime["build_runtime_summary"](app)

    def refresh_cooldown_seconds(self, failures: int) -> int:
        return self.runtime["refresh_cooldown_seconds"](failures)

    def record_live_refresh_attempt(
        self,
        app: Any,
        *,
        status: str,
        snapshot: dict | None = None,
        error: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        self.runtime["record_live_refresh_attempt"](
            app,
            status=status,
            default_target_battles=self.runtime["DAILY_TARGET_BATTLES"],
            snapshot=snapshot,
            error=error,
            finished_at=finished_at,
        )

    def record_live_collection_progress(self, app: Any, progress: dict) -> None:
        public_progress = self.runtime["record_live_collection_progress"](app, progress)
        self.runtime["logger"].info(
            "snapshot_collection_progress usable=%s target=%s players=%s requests=%s rate_limited=%s",
            public_progress.get("usable_battles"),
            public_progress.get("target_battles"),
            public_progress.get("fetched_players"),
            public_progress.get("request_count"),
            public_progress.get("rate_limited"),
        )

    def snapshot_lifecycle_dependencies(self) -> Any:
        return self.runtime["build_snapshot_lifecycle_dependencies"](
            self.runtime["SnapshotLifecycleDependencies"],
            self.runtime,
        )

    def dataset_runtime_dependencies(self, data_dir: Any = None) -> Any:
        return self.runtime["build_dataset_runtime_dependencies"](
            self.runtime["DatasetRuntimeDependencies"],
            self.runtime,
            data_dir,
        )

    def status_route_dependencies(self) -> Any:
        return self.runtime["build_status_route_dependencies"](
            self.runtime["StatusRouteDependencies"],
            self.runtime,
        )

    def snapshot_artifact_status(self, data_dir: Any, snapshot_id: str | None) -> dict:
        return self.runtime["build_snapshot_artifact_status"](data_dir, snapshot_id)

    def live_snapshot_status(self, app: Any) -> dict:
        return self.runtime["build_live_snapshot_status_from_runtime"](app, self.runtime)

    def readiness_status(
        self,
        app: Any,
        *,
        external_api_required: bool | None = None,
        model_api_configured: bool | None = None,
    ) -> dict:
        return self.runtime["build_readiness_status_from_runtime"](
            app,
            self.runtime,
            external_api_required=external_api_required,
            model_api_configured=model_api_configured,
        )

    def configure_live_sample_target(self, app: Any, target_battles: int) -> dict:
        try:
            self.runtime["reject_live_sample_target_update"](
                target_battles,
                fixed_target_battles=self.runtime["DAILY_TARGET_BATTLES"],
            )
        except self.runtime["FixedLiveSampleTargetError"] as exc:
            raise self.runtime["HTTPException"](status_code=exc.status_code, detail=str(exc)) from exc
        return self.runtime["get_live_sample_settings"](app)

    def validate_dataset_scope(self, dataset_scope: str) -> str:
        return self.runtime["validate_dataset_scope_orchestrated"](
            dataset_scope,
            dependencies=self.runtime["_dataset_runtime_dependencies"](),
        )

    def active_snapshot_group_manifest(self, data_dir: Any = None) -> dict | None:
        if data_dir is None:
            data_dir = self.runtime["DATA_DIR"]
        return self.runtime["load_active_manifest_orchestrated"](
            dependencies=self.runtime["_dataset_runtime_dependencies"](data_dir),
        )

    def rag_scope_stats_for_manifest(
        self,
        app: Any,
        manifest: dict,
        data_dir: Any,
    ) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
        return self.runtime["rag_scope_stats_orchestrated"](
            app,
            manifest,
            dependencies=self.runtime["_dataset_runtime_dependencies"](data_dir),
        )

    def dataset_catalog(self, app: Any) -> dict:
        return self.runtime["get_dataset_catalog_orchestrated"](
            app,
            dependencies=self.runtime["_dataset_runtime_dependencies"](),
        )

    def structured_repository(self, app: Any, dataset_scope: str | None = None) -> Any:
        if dataset_scope is None:
            dataset_scope = self.runtime["DEFAULT_DATASET_SCOPE"]
        return self.runtime["get_structured_repository_orchestrated"](
            app,
            dataset_scope,
            dependencies=self.runtime["_dataset_runtime_dependencies"](),
        )

    def dataset_retriever(self, app: Any, dataset_scope: str) -> Any:
        return self.runtime["ensure_dataset_retriever_orchestrated"](
            app,
            dataset_scope,
            dependencies=self.runtime["_dataset_runtime_dependencies"](),
        )

    def restore_published_snapshot(self, app: Any) -> dict | None:
        return self.runtime["restore_published_snapshot_orchestrated"](
            app,
            dependencies=self.runtime["_snapshot_lifecycle_dependencies"](),
        )

    def ensure_live_snapshot(self, app: Any) -> dict | None:
        return self.runtime["ensure_live_snapshot_orchestrated"](
            app,
            dependencies=self.runtime["_snapshot_lifecycle_dependencies"](),
        )

    async def refresh_live_snapshot_loop(self, app: Any) -> None:
        await self.runtime["refresh_live_snapshot_loop_orchestrated"](
            app,
            dependencies=self.runtime["_snapshot_lifecycle_dependencies"](),
        )

    async def follow_published_snapshot_loop(self, app: Any) -> None:
        await self.runtime["follow_published_snapshot_loop_orchestrated"](
            app,
            dependencies=self.runtime["_snapshot_lifecycle_dependencies"](),
        )

    async def refresh_live_snapshot_once(self, app: Any) -> None:
        await self.runtime["refresh_live_snapshot_once_orchestrated"](
            app,
            dependencies=self.runtime["_snapshot_lifecycle_dependencies"](),
        )


__all__ = ["RuntimeFacade"]
