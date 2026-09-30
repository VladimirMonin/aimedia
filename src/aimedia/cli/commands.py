"""Public image/history/catalog commands; infrastructure is composed by bootstrap."""

from __future__ import annotations

import asyncio
import glob
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, cast

import typer

from aimedia.application.execution import (
    PreparedImage,
    SyncResult,
    can_sync,
    execute_batch,
    execute_image,
    prepare_retry,
    sync_image,
)
from aimedia.application.inputs.prepare import snapshot_reference_images
from aimedia.application.prompts.compile import PromptCompiler, PromptSourceRequest
from aimedia.bootstrap import LocalApplication
from aimedia.cli.runtime import emit, job_exit
from aimedia.domain.errors import InvalidParameterValueError
from aimedia.domain.inputs import PromptSourceKind
from aimedia.domain.job import Job, JobRelation
from aimedia.domain.refs import ModelRef, ProviderRef
from aimedia.domain.requests import FinalFormat, ImageGenerationRequest
from aimedia.domain.state import JobStatus
from aimedia.registry.views import build_model_list, build_model_view, render_model_help

image_app = typer.Typer(help="Генерация изображений", no_args_is_help=True)
jobs_app = typer.Typer(help="Локальная история", no_args_is_help=True)
models_app = typer.Typer(
    help="Документированный каталог, не live verification", no_args_is_help=True
)
providers_app = typer.Typer(help="Адаптеры", no_args_is_help=True)
config_app = typer.Typer(help="Локальные настройки без секретов", no_args_is_help=True)


def local(ctx: typer.Context) -> LocalApplication:
    return cast(LocalApplication, ctx.obj["application"])


def result(ctx: typer.Context, data: object) -> None:
    emit(data, as_json=bool(ctx.obj["as_json"]))


def job_result(ctx: typer.Context, job: Job) -> None:
    ctx.obj["emitted"] = True
    error = job.error.model_dump(mode="json") if job.error else None
    emit(
        {
            "job": job.model_dump(mode="json"),
            "retry_of": job.relation.parent_job_id if job.relation else None,
        },
        as_json=ctx.obj["as_json"],
        ok=error is None,
        error=error,
    )
    if job.error:
        raise typer.Exit(job_exit(job.error.code))


def prepare(
    ctx: typer.Context,
    model: str,
    images: list[Path],
    sources: list[PromptSourceRequest],
    *,
    resolution: str | None,
    aspect_ratio: str | None,
    quality: str | None,
    final_format: str | None,
    max_images: int,
    seed: int | None,
) -> PreparedImage:
    compiled = PromptCompiler().compile(sources)
    snapshots = snapshot_reference_images(images)
    canonical = model
    try:
        canonical = local(ctx).resolver.canonical_id(model)
    except Exception:
        pass
    if final_format == "jpg":
        final_format = "jpeg"
    return PreparedImage(
        ImageGenerationRequest(
            provider=ProviderRef(id=ctx.obj["provider"]),
            model=ModelRef(id=canonical),
            prompt=compiled.compiled,
            images=[s.ref for s in snapshots],
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            quality=quality,
            final_format=FinalFormat(final_format) if final_format else None,
            max_images=max_images,
            seed=seed,
        ),
        tuple(snapshots),
        compiled.sources,
    )


@image_app.command("generate")
def image_generate(
    ctx: typer.Context,
    model: Annotated[str, typer.Option("--model")],
    prompt: Annotated[list[str] | None, typer.Option("--prompt")] = None,
    prompt_file: Annotated[list[Path] | None, typer.Option("--prompt-file")] = None,
    image: Annotated[list[Path] | None, typer.Option("--image")] = None,
    resolution: Annotated[str | None, typer.Option("--resolution")] = None,
    aspect_ratio: Annotated[str | None, typer.Option("--aspect-ratio")] = None,
    quality: Annotated[str | None, typer.Option("--quality")] = None,
    final_format: Annotated[
        Literal["png", "jpeg", "webp", "jpg"] | None, typer.Option("--format")
    ] = None,
    max_images: Annotated[int, typer.Option("--max-images", min=1)] = 1,
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    out: Annotated[Path | None, typer.Option("--out")] = None,
    name: Annotated[str | None, typer.Option("--name")] = None,
    keep_original: Annotated[bool, typer.Option("--keep-original")] = False,
    allow_experimental: Annotated[bool, typer.Option("--allow-experimental")] = False,
    poll_interval: Annotated[float, typer.Option("--poll-interval", min=0.01)] = 1.0,
    wait_timeout: Annotated[float, typer.Option("--wait-timeout", min=0.01)] = 300.0,
) -> None:
    """Один prompt → один Job/POST. Подробнее: aimedia help image.generate."""
    sources = [
        PromptSourceRequest(kind=PromptSourceKind.INLINE, text=value)
        if key == "--prompt"
        else PromptSourceRequest(kind=PromptSourceKind.FILE, path=Path(value))
        for key, value in ctx.obj["sources"]
    ]
    prepared = prepare(
        ctx,
        model,
        image or [],
        sources,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        quality=quality,
        final_format=final_format,
        max_images=max_images,
        seed=seed,
    )

    async def run() -> Job:
        async with local(ctx).network(
            allow_experimental=allow_experimental, base_name=name
        ) as services:
            return await execute_image(
                prepared,
                services,
                output_dir=out.absolute() if out else None,
                base_name=name,
                keep_original=keep_original,
                poll_interval=poll_interval,
                wait_timeout=wait_timeout,
            )

    job_result(ctx, asyncio.run(run()))


@image_app.command("batch")
def image_batch(
    ctx: typer.Context,
    files: Annotated[list[str], typer.Argument()],
    model: Annotated[str, typer.Option("--model")],
    image: Annotated[list[Path] | None, typer.Option("--image")] = None,
    resolution: Annotated[str | None, typer.Option("--resolution")] = None,
    aspect_ratio: Annotated[str | None, typer.Option("--aspect-ratio")] = None,
    quality: Annotated[str | None, typer.Option("--quality")] = None,
    final_format: Annotated[
        Literal["png", "jpeg", "webp", "jpg"] | None, typer.Option("--format")
    ] = None,
    max_images: Annotated[int, typer.Option("--max-images", min=1)] = 1,
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    concurrency: Annotated[int, typer.Option("--concurrency", min=1, max=32)] = 3,
    out: Annotated[Path | None, typer.Option("--out")] = None,
    name: Annotated[str | None, typer.Option("--name")] = None,
    keep_original: Annotated[bool, typer.Option("--keep-original")] = False,
    allow_experimental: Annotated[bool, typer.Option("--allow-experimental")] = False,
    poll_interval: Annotated[float, typer.Option("--poll-interval", min=0.01)] = 1.0,
    wait_timeout: Annotated[float, typer.Option("--wait-timeout", min=0.01)] = 300.0,
) -> None:
    """Каждый positional prompt-файл — отдельный Job. Glob раскрывается и на Windows."""
    expanded: list[Path] = []
    for pattern in files:
        matches = sorted(glob.glob(pattern)) if glob.has_magic(pattern) else [pattern]
        if not matches:
            raise InvalidParameterValueError("Batch pattern не нашёл prompt-файлов")
        expanded.extend(Path(path) for path in matches)
    prepared = [
        prepare(
            ctx,
            model,
            image or [],
            [PromptSourceRequest(kind=PromptSourceKind.FILE, path=path)],
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            quality=quality,
            final_format=final_format,
            max_images=max_images,
            seed=seed,
        )
        for path in expanded
    ]

    async def run() -> tuple[Job, ...]:
        async with local(ctx).network(
            allow_experimental=allow_experimental, base_name=name
        ) as services:
            return await execute_batch(
                prepared,
                services,
                concurrency=concurrency,
                output_dir=out.absolute() if out else None,
                base_name=name,
                keep_original=keep_original,
                poll_interval=poll_interval,
                wait_timeout=wait_timeout,
            )

    jobs = asyncio.run(run())
    failed = sum(job.status is not JobStatus.COMPLETED for job in jobs)
    emit(
        {
            "total": len(jobs),
            "completed": len(jobs) - failed,
            "failed": failed,
            "jobs": [job.model_dump(mode="json") for job in jobs],
        },
        as_json=ctx.obj["as_json"],
        ok=not failed,
    )
    if failed:
        raise typer.Exit(9)


@jobs_app.command("recent")
@jobs_app.command("list", hidden=True)
def jobs_recent(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 20,
    status: Annotated[JobStatus | None, typer.Option()] = None,
) -> None:
    result(
        ctx,
        [
            job.model_dump(mode="json")
            for job in local(ctx)
            .history()
            .list_recent(limit=limit, statuses=[status] if status else None)
        ],
    )


@jobs_app.command("show")
def jobs_show(ctx: typer.Context, job_id: Annotated[int, typer.Argument(min=1)]) -> None:
    result(ctx, local(ctx).show(job_id))


@jobs_app.command("search")
def jobs_search(
    ctx: typer.Context,
    query: str,
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 20,
    status: Annotated[JobStatus | None, typer.Option()] = None,
) -> None:
    result(
        ctx,
        [
            job.model_dump(mode="json")
            for job in local(ctx).search(query, limit=limit, status=status)
        ],
    )


@jobs_app.command("costs")
def jobs_costs(
    ctx: typer.Context,
    today: Annotated[bool, typer.Option()] = False,
    month: Annotated[bool, typer.Option()] = False,
) -> None:
    if today and month:
        raise typer.BadParameter("Выберите today или month")
    now = datetime.now(UTC)
    start = (
        now.replace(hour=0, minute=0, second=0, microsecond=0)
        if today
        else (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) if month else None)
    )
    end = start + timedelta(days=1) if today and start else None
    if month and start:
        end = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
    result(ctx, local(ctx).costs(start=start, end=end))


@jobs_app.command("retry")
def jobs_retry(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(min=1)],
    allow_experimental: Annotated[bool, typer.Option("--allow-experimental")] = False,
) -> None:
    original = local(ctx).history().get(job_id)
    if original is None:
        raise InvalidParameterValueError("Job не найден")

    async def run() -> Job:
        async with local(ctx).network(allow_experimental=allow_experimental) as services:
            return await execute_image(
                prepare_retry(original, services),
                services,
                relation=JobRelation(parent_job_id=job_id),
            )

    job_result(ctx, asyncio.run(run()))


@jobs_app.command("sync")
def jobs_sync(
    ctx: typer.Context,
    job_id: Annotated[int | None, typer.Argument()] = None,
    all_jobs: Annotated[bool, typer.Option("--all")] = False,
    concurrency: Annotated[int, typer.Option(min=1, max=32)] = 3,
) -> None:
    app = local(ctx)
    if job_id is not None and all_jobs:
        raise typer.BadParameter("Выберите ID или --all")
    jobs = (
        [app.history().get(job_id)]
        if job_id is not None
        else list(
            app.history().list_recent(
                limit=2**31 - 1, statuses=[JobStatus.SUBMITTED, JobStatus.RUNNING, JobStatus.FAILED]
            )
        )
    )
    if job_id is not None and jobs[0] is None:
        raise InvalidParameterValueError("Job не найден")
    selected = [job for job in jobs if job is not None and (job_id is not None or can_sync(job))]

    async def run() -> list[SyncResult]:
        if all(job.status is JobStatus.COMPLETED for job in selected):
            return [SyncResult(job) for job in selected]
        async with app.network(allow_experimental=True) as services:
            semaphore = asyncio.Semaphore(concurrency)

            async def sync(job: Job) -> SyncResult:
                assert job.id is not None
                if job.status is JobStatus.COMPLETED:
                    return SyncResult(job)
                if job.remote_ref is None:
                    raise InvalidParameterValueError("Нет известного remote execution для sync")
                gateway = services.sync_gateway(job)
                async with semaphore:
                    return await sync_image(job.id, services, gateway)

            return list(await asyncio.gather(*(sync(job) for job in selected)))

    synced = asyncio.run(run())
    failure = next((attempt.error for attempt in synced if attempt.error is not None), None)
    ctx.obj["emitted"] = True
    emit(
        [attempt.job.model_dump(mode="json") for attempt in synced],
        as_json=ctx.obj["as_json"],
        ok=failure is None,
        error=failure.model_dump(mode="json") if failure else None,
    )
    if failure:
        raise typer.Exit(job_exit(failure.code))


@models_app.command("list")
def models_list(ctx: typer.Context) -> None:
    records = [record for record in local(ctx).records if ctx.obj["provider"] in record.providers]
    result(ctx, [view.model_dump(mode="json") for view in build_model_list(records)])


@models_app.command("show")
def models_show(ctx: typer.Context, model: str) -> None:
    view = build_model_view(local(ctx).resolver.resolve(model, ctx.obj["provider"]))
    result(ctx, view.model_dump(mode="json") if ctx.obj["as_json"] else render_model_help(view))


@providers_app.command("list")
def providers_list(ctx: typer.Context) -> None:
    result(
        ctx,
        [
            {
                "id": "polza",
                "adapter": True,
                "configured": bool(local(ctx).settings.resolve_api_key()),
                "authorization_verified": False,
            }
        ],
    )


@config_app.command("show")
@config_app.command("validate")
def config_show(ctx: typer.Context) -> None:
    result(ctx, local(ctx).settings.model_dump(mode="json"))


@config_app.command("init")
def config_init(
    ctx: typer.Context,
    file: Annotated[Path | None, typer.Option("--file")] = None,
) -> None:
    from aimedia.paths import user_settings_file

    path = file or user_settings_file()
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise InvalidParameterValueError("Перенаправленный config path запрещён")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write('polza_api_key_env = "POLZA_API_KEY"\nlog_level = "INFO"\n')
    result(ctx, {"path": str(path.absolute()), "api_key_env": "POLZA_API_KEY"})


def help_command(
    ctx: typer.Context,
    topic: Annotated[str | None, typer.Argument()] = None,
    raw: Annotated[bool, typer.Option("--raw")] = False,
) -> None:
    """Атомарная автономная справка: тема, --raw или --json."""
    from aimedia.help.registry import HelpRegistry

    registry = HelpRegistry(provider=ctx.obj["provider"])
    if topic is None:
        result(
            ctx,
            [
                {
                    "topic": t.topic,
                    "title": t.title,
                    "summary": t.summary,
                    "related": list(t.related),
                }
                for t in registry.list()
            ],
        )
        return
    resolved = registry.get(topic)
    if ctx.obj["as_json"]:
        result(ctx, resolved.model_dump(mode="json"))
    elif raw:
        typer.echo(resolved.markdown, nl=False)
    else:
        from rich.console import Console
        from rich.markdown import Markdown

        Console(no_color=ctx.obj["no_color"]).print(Markdown(resolved.markdown))


def register(app: typer.Typer) -> None:
    app.command("help")(help_command)
    for name, group in [
        ("image", image_app),
        ("jobs", jobs_app),
        ("models", models_app),
        ("providers", providers_app),
        ("config", config_app),
    ]:
        app.add_typer(group, name=name)
