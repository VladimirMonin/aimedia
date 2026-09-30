"""Composition root: CLI calls these services, not HTTP or ORM directly."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager

import httpx

from aimedia.application.execution import ImageServices
from aimedia.application.single_image import ImageExecutionSetup
from aimedia.artifacts.inputs import LocalManagedInputStorage
from aimedia.artifacts.output import _validate_filename
from aimedia.artifacts.storage import PillowArtifactStorage
from aimedia.config import Settings
from aimedia.domain.errors import (
    InvalidParameterValueError,
    JobError,
    ProviderError,
    UnknownModelError,
    UnknownProviderError,
)
from aimedia.domain.job import Job
from aimedia.domain.refs import ProviderModelBinding
from aimedia.domain.requests import ImageGenerationRequest
from aimedia.domain.state import JobStatus
from aimedia.providers.polza.download import PolzaArtifactDownloader
from aimedia.providers.polza.gateway import PolzaProviderGateway
from aimedia.registry.builtin import load_builtin_registry
from aimedia.registry.errors import RegistryError
from aimedia.registry.models import EffectiveModelDefinition, Family, ModelStatus
from aimedia.registry.resolver import ModelResolver
from aimedia.registry.validator import validate_model_request
from aimedia.storage.costs import PeeweeCostReportRepository
from aimedia.storage.database import DatabaseManager
from aimedia.storage.errors import StorageError
from aimedia.storage.ownership import claim_job
from aimedia.storage.repository import PeeweeJobRepository
from aimedia.storage.search import HistorySearch

# Explicit local allocation safeguards, NOT provider/model limits.
MAX_BODY_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


class LocalApplication:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.records = load_builtin_registry()
        self.resolver = ModelResolver(self.records)
        self._manager: DatabaseManager | None = None
        self.inputs = LocalManagedInputStorage(data_root=settings.data_dir)
        self.artifacts = PillowArtifactStorage(data_root=settings.data_dir)

    def history(self) -> PeeweeJobRepository:
        if self._manager is None:
            root = self.settings.data_dir.absolute()
            for component in (root, *root.parents):
                if component.is_symlink() or component.is_junction():
                    raise InvalidParameterValueError("Перенаправленный data-root запрещён")
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise StorageError("Не удалось открыть каталог истории") from exc
            manager = DatabaseManager(root / "database.sqlite3")
            manager.open()
            self._manager = manager
        return PeeweeJobRepository(self._manager)

    def search(
        self, query: str, *, limit: int = 20, status: JobStatus | None = None
    ) -> Sequence[Job]:
        repository = self.history()
        assert self._manager is not None
        return HistorySearch(self._manager, repository).search(query, limit=limit, status=status)

    def costs(self, **period: object) -> dict[str, object]:
        self.history()
        assert self._manager is not None
        # Public boundaries are parsed by the CLI; typed datetime parameters below.
        from datetime import datetime

        start = period.get("start")
        end = period.get("end")
        report = PeeweeCostReportRepository(self._manager).aggregate(
            start=start if isinstance(start, datetime) else None,
            end=end if isinstance(end, datetime) else None,
        )
        return report.model_dump(mode="json")

    def show(self, job_id: int) -> dict[str, object]:
        job = self.history().get(job_id)
        if job is None:
            raise InvalidParameterValueError("Job не найден")
        data = job.model_dump(mode="json")
        # User-facing absolute locators allow opening copies after source deletion.
        data["managed_inputs"] = [
            {"position": ref.position, "path": str(self.inputs.resolve_path(ref))}
            for ref in job.inputs
            if ref.managed_path is not None
        ]
        data["artifact_files"] = [
            {"path": str(self.artifacts.resolve_path(a)), "exists": self.artifacts.exists(a)}
            for a in (*(job.result.artifacts if job.result else ()), *job.artifacts)
        ]
        return data

    def close(self) -> None:
        if self._manager is not None:
            self._manager.close()
            self._manager = None

    def prepare_provider(
        self,
        request: ImageGenerationRequest,
        client: httpx.AsyncClient,
        *,
        allow_experimental: bool = False,
        base_name: str | None = None,
    ) -> ImageExecutionSetup:
        if request.provider.id != "polza":
            raise UnknownProviderError("Поддерживается только Polza")
        try:
            effective = self.resolver.resolve(request.model.id, request.provider.id)
        except RegistryError:
            raise UnknownModelError("Модель/provider не найдены в каталоге") from None
        validate_model_request(effective, request)
        if effective.status is ModelStatus.EXPERIMENTAL and not allow_experimental:
            raise InvalidParameterValueError(
                "Модель не проверена live; требуется --allow-experimental"
            )
        if base_name is not None:
            try:
                _validate_filename(base_name, "png")
            except ValueError:
                raise InvalidParameterValueError("Небезопасное имя результата") from None
        key = self.settings.resolve_api_key()
        if not key:
            raise ProviderError(
                JobError(
                    code="PROVIDER_AUTHENTICATION",
                    message="Задайте API key через настроенную переменную окружения",
                )
            )
        gateway = PolzaProviderGateway(
            client=client,
            api_key=key,
            effective=effective,
            max_body_bytes=MAX_BODY_BYTES,
            max_response_bytes=MAX_RESPONSE_BYTES,
        )
        return ImageExecutionSetup(
            ProviderModelBinding(
                provider_id=effective.provider_id, remote_model_id=effective.remote_model_id
            ),
            gateway,
        )

    def sync_gateway(self, job: Job, client: httpx.AsyncClient) -> PolzaProviderGateway:
        # GET recovery uses recorded identifiers, not a current catalog binding or
        # current model validation. This snapshot is not a new production model.
        if job.provider.id != "polza" or job.remote_ref is None or not job.remote_model_id:
            raise InvalidParameterValueError("Нет известного Polza remote execution")
        key = self.settings.resolve_api_key()
        if not key:
            raise ProviderError(
                JobError(
                    code="PROVIDER_AUTHENTICATION",
                    message="Задайте API key через настроенную переменную окружения",
                )
            )
        snapshot = EffectiveModelDefinition(
            requested_model=job.model.id,
            model_id=job.model.id,
            name=job.model.id,
            provider_id=job.provider.id,
            remote_model_id=job.remote_model_id,
            family=Family.IMAGE,
            status=ModelStatus.EXPERIMENTAL,
        )
        return PolzaProviderGateway(
            client=client,
            api_key=key,
            effective=snapshot,
            max_body_bytes=MAX_BODY_BYTES,
            max_response_bytes=MAX_RESPONSE_BYTES,
        )

    @asynccontextmanager
    async def network(
        self, *, allow_experimental: bool = False, base_name: str | None = None
    ) -> AsyncIterator[ImageServices]:
        downloader = PolzaArtifactDownloader(max_artifact_bytes=MAX_ARTIFACT_BYTES)
        try:
            async with httpx.AsyncClient(
                timeout=30.0, trust_env=False, follow_redirects=False
            ) as client:
                with self.services(
                    client, downloader, allow_experimental=allow_experimental, base_name=base_name
                ) as services:
                    yield services
        finally:
            await downloader.aclose()

    @contextmanager
    def services(
        self,
        client: httpx.AsyncClient,
        downloader: PolzaArtifactDownloader,
        *,
        allow_experimental: bool = False,
        base_name: str | None = None,
    ) -> Iterator[ImageServices]:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        yield ImageServices(
            repository=self.history(),
            inputs=self.inputs,
            artifacts=self.artifacts,
            prepare=lambda request: self.prepare_provider(
                request, client, allow_experimental=allow_experimental, base_name=base_name
            ),
            sync_gateway=lambda job: self.sync_gateway(job, client),
            download=downloader.fetch,
            claim=lambda job_id: claim_job(self.settings.data_dir, job_id),
        )
