"""Public CLI through real Polza mapper/gateway/downloader, SQLite and files."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from download_backend import PUBLIC_IPV4, FakeResolver, RecordingBackend, raw_http_response
from offline_policy import child_process_env
from PIL import Image
from typer.testing import CliRunner

import aimedia.bootstrap as bootstrap
from aimedia.cli.app import app
from aimedia.storage.database import DatabaseManager
from aimedia.storage.repository import PeeweeJobRepository

MODEL = "qwen-image-2-1"
CANARY = "complete_cli_canary_" + "secret_not_a_real_key"


def png_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (3, 2), "red").save(out, format="PNG")
    return out.getvalue()


class OfflinePolza:
    def __init__(self):
        self.calls = []
        self.next_id = 0
        self.mode = "complete"
        self.peak = 0
        self.active = 0
        self.barrier = None
        self.gets = {}
        self.output_count = 1
        self.fail_second_download = False

    async def handle(self, request):
        self.calls.append((request.method, str(request.url)))
        assert str(request.url).startswith("https://polza.ai/api/v1/media")
        assert request.headers["authorization"] == f"Bearer {CANARY}"
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["model"] == "qwen/image-2.1"
            assert "max_images" not in payload["input"]
            self.next_id += 1
            remote = f"aig_{self.next_id}"
            if self.mode == "uncertain":
                raise httpx.ReadError(CANARY, request=request)
            if self.mode == "batch" and "reject" in payload["input"]["prompt"]:
                return httpx.Response(400, json={"error": CANARY})
            if self.mode in {"batch", "running", "retry-get", "interrupt"}:
                self.active += 1
                self.peak = max(self.active, self.peak)
                if self.barrier is None:
                    self.barrier = asyncio.Event()
                if self.active == 2:
                    self.barrier.set()
                return httpx.Response(
                    200, json={"id": remote, "object": "media.generation", "status": "pending"}
                )
        else:
            remote = request.url.path.rsplit("/", 1)[-1]
            if self.mode == "batch":
                await asyncio.wait_for(self.barrier.wait(), 2)
            if self.mode == "interrupt":
                raise asyncio.CancelledError()
            if self.mode == "unauthorized":
                return httpx.Response(401, json={"error": CANARY})
            if self.mode == "remote-failed":
                return httpx.Response(
                    200, json={"id": remote, "object": "media.generation", "status": "failed"}
                )
            if self.mode == "retry-get" and not self.gets.get(remote):
                self.gets[remote] = 1
                return httpx.Response(503, json={"error": CANARY})
            if self.mode == "running":
                return httpx.Response(
                    200, json={"id": remote, "object": "media.generation", "status": "processing"}
                )
            self.gets[remote] = self.gets.get(remote, 0) + 1
            if self.gets[remote] == 2 and self.active:
                self.active -= 1
        return httpx.Response(
            200,
            json={
                "id": remote,
                "object": "media.generation",
                "status": "completed",
                "data": [
                    {"url": f"https://s3.polza.ai/{remote}_{i}.png?sig={CANARY}"}
                    for i in range(self.output_count)
                ],
                "usage": {"cost": "3.000", "cost_rub": "3.000", "images": 1},
                "provider_text": CANARY,
            },
        )


@pytest.fixture
def offline_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("POLZA_API_KEY", CANARY)
    monkeypatch.setenv("AIMEDIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AIMEDIA_CONFIG", str(tmp_path / "missing-settings.toml"))
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    server = OfflinePolza()
    original_client = httpx.AsyncClient
    original_downloader = bootstrap.PolzaArtifactDownloader
    backends = []
    monkeypatch.setattr(
        bootstrap.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(server.handle), **kwargs),
    )

    def downloader(**kwargs):
        fail_second = server.fail_second_download
        server.fail_second_download = False

        class SequencedBackend(RecordingBackend):
            async def connect_tcp(self, *args, **options):
                if fail_second and len(self.connects) == 1:
                    failed = RecordingBackend(
                        [
                            raw_http_response(
                                status=503,
                                body=CANARY.encode(),
                                headers=[(b"connection", b"close")],
                            )
                        ]
                    )
                    stream = await failed.connect_tcp(*args, **options)
                    self.connects.extend(failed.connects)
                    self.streams.extend(failed.streams)
                    return stream
                return await super().connect_tcp(*args, **options)

        backend = SequencedBackend(
            [raw_http_response(body=png_bytes(), headers=[(b"connection", b"close")])]
        )
        backends.append(backend)
        return original_downloader(
            **kwargs, resolver=FakeResolver(default=[PUBLIC_IPV4]), direct_backend=backend
        )

    monkeypatch.setattr(bootstrap, "PolzaArtifactDownloader", downloader)
    return CliRunner(), server, backends


def invoke(runner, argv, code=0):
    response = runner.invoke(app, [*argv, "--json"])
    assert response.exit_code == code, (response.stdout, response.stderr, response.exception)
    payload = json.loads(response.stdout)
    assert "\x1b[" not in response.stdout
    assert CANARY not in response.stdout + response.stderr
    return payload


def subprocess_cli(tmp_path, args):
    env = child_process_env()
    env.update(
        AIMEDIA_DATA_DIR=str(tmp_path / "data"),
        PYTHONIOENCODING="utf-8",
        AIMEDIA_CONFIG=str(tmp_path / "missing-settings.toml"),
    )
    completed = subprocess.run(
        [sys.executable, "-m", "aimedia", *args, "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert CANARY not in completed.stdout + completed.stderr
    return json.loads(completed.stdout)["data"]


def test_generate_restart_show_search_costs_copies_retry_and_clean_billing(tmp_path, offline_cli):
    runner, server, backends = offline_cli
    a, c, ref = (tmp_path / name for name in ["a.md", "c.md", "ref.png"])
    a.write_text("laboratory", encoding="utf-8")
    c.write_text("robot", encoding="utf-8")
    ref.write_bytes(png_bytes())
    generated = invoke(
        runner,
        [
            "--json",
            "image",
            "generate",
            "--model",
            MODEL,
            "--allow-experimental",
            "--prompt-file",
            str(a),
            "--prompt",
            "blue",
            "--prompt-file",
            str(c),
            "--image",
            str(ref),
            "--format",
            "webp",
            "--keep-original",
            "--name",
            "character",
        ],
    )["data"]["job"]
    assert generated["compiled_prompt"]["text"] == "laboratory\n\nblue\n\nrobot"
    assert generated["status"] == "completed"
    assert generated["remote_ref"]["operation"] == "media"
    assert generated["cost"] == {"amount": "3.000", "currency": "RUB"}
    assert len(generated["result"]["artifacts"]) == 2
    for backend in backends:
        assert backend.connects and all(
            CANARY.encode() not in s.request_bytes.split(b"\r\n\r\n")[0].split(b"?sig=")[0]
            for s in backend.streams
        )
        assert all(b"authorization:" not in s.request_bytes.lower() for s in backend.streams)
    a.unlink()
    c.unlink()
    ref.unlink()
    shown = subprocess_cli(tmp_path, ["jobs", "show", str(generated["id"])])
    copied = Path(shown["managed_inputs"][0]["path"])
    assert copied.read_bytes() == png_bytes()
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == shown["inputs"][0]["sha256"]
    assert all(Path(file["path"]).exists() for file in shown["artifact_files"])
    assert (
        subprocess_cli(tmp_path, ["jobs", "search", "laboratory robot"])[0]["id"] == generated["id"]
    )
    costs = subprocess_cli(tmp_path, ["jobs", "costs", "--today"])
    assert costs["totals"][0]["amount"] == "3.000"
    retried = invoke(runner, ["jobs", "retry", str(generated["id"]), "--allow-experimental"])[
        "data"
    ]["job"]
    assert retried["id"] != generated["id"]
    assert retried["relation"] == {"parent_job_id": generated["id"], "type": "retry_of"}
    assert retried["inputs"][0]["managed_path"] != generated["inputs"][0]["managed_path"]
    assert (
        subprocess_cli(tmp_path, ["jobs", "show", str(generated["id"])])["cost"]
        == generated["cost"]
    )
    assert sum(method == "POST" for method, _ in server.calls) == 2
    before = list((tmp_path / "data" / "outputs").rglob("*"))
    invoke(runner, ["jobs", "sync", str(generated["id"])])
    invoke(runner, ["jobs", "sync", str(generated["id"])])
    assert before == list((tmp_path / "data" / "outputs").rglob("*"))
    assert sum(method == "POST" for method, _ in server.calls) == 2
    assert CANARY.encode() not in (tmp_path / "data" / "database.sqlite3").read_bytes()


@pytest.mark.parametrize(
    "option,value,code",
    [
        ("--resolution", "4K", 3),
        ("--seed", "4", 3),
        ("--max-images", "2", 3),
        ("--name", "../escape", 3),
        ("--out", "missing-output", 7),
    ],
)
def test_semantic_and_output_errors_are_failed_jobs_without_post(
    tmp_path, offline_cli, option, value, code
):
    runner, server, _ = offline_cli
    failed = invoke(
        runner,
        [
            "image",
            "generate",
            "--model",
            MODEL,
            "--allow-experimental",
            "--prompt",
            "robot",
            option,
            value,
        ],
        code,
    )
    assert not failed["ok"] and failed["data"]["job"]["status"] == "failed"
    assert not server.calls


def test_no_key_and_unknown_submit_are_not_automatically_retried(
    tmp_path, offline_cli, monkeypatch
):
    runner, server, _ = offline_cli
    monkeypatch.delenv("POLZA_API_KEY")
    invoke(
        runner,
        ["image", "generate", "--model", MODEL, "--allow-experimental", "--prompt", "robot"],
        4,
    )
    assert not server.calls
    monkeypatch.setenv("POLZA_API_KEY", CANARY)
    server.mode = "uncertain"
    failed = invoke(
        runner,
        ["image", "generate", "--model", MODEL, "--allow-experimental", "--prompt", "robot"],
        4,
    )["data"]["job"]
    assert failed["error"]["code"] == "SUBMIT_UNCERTAIN"
    assert failed["remote_ref"] is None
    invoke(runner, ["jobs", "sync", str(failed["id"])], 3)
    assert len(server.calls) == 1


def test_batch_five_jobs_overlap_bounded_whole_flows_and_expected_failure(
    tmp_path, offline_cli, monkeypatch
):
    runner, server, _ = offline_cli
    server.mode = "batch"
    owned = peak = 0
    original_claim = bootstrap.claim_job

    @contextmanager
    def measured_claim(root, job_id):
        nonlocal owned, peak
        with original_claim(root, job_id):
            owned += 1
            peak = max(owned, peak)
            try:
                yield
            finally:
                owned -= 1

    monkeypatch.setattr(bootstrap, "claim_job", measured_claim)
    paths = []
    for i in range(5):
        path = tmp_path / f"prompt_{i}.md"
        path.write_text("reject" if i == 3 else f"robot {i}", encoding="utf-8")
        paths.append(str(path))
    payload = invoke(
        runner,
        [
            "image",
            "batch",
            *paths,
            "--model",
            MODEL,
            "--allow-experimental",
            "--concurrency",
            "2",
            "--poll-interval",
            "0.01",
        ],
        9,
    )
    assert payload["data"]["completed"] == 4 and payload["data"]["failed"] == 1
    assert peak == server.peak == 2 and owned == 0
    assert sum(method == "POST" for method, _ in server.calls) == 5
    assert len(list((tmp_path / "data" / "outputs").rglob("*.png"))) == 4


@pytest.mark.parametrize(
    "args,code",
    [
        (["version"], 0),
        (["models", "list"], 0),
        (["providers", "list"], 0),
        (["help"], 0),
        (["help", "image.generate"], 0),
        (["config", "show"], 0),
        (["jobs", "recent"], 0),
        (["jobs", "costs", "--month"], 0),
        (["no-such-command"], 2),
        (["image", "generate", "--wat", "secret"], 2),
        (["jobs", "show", "not-an-int"], 2),
        (["jobs", "costs", "--today", "--month"], 2),
        (["--provider", "polza", "models", "list", "--provider", "wrong"], 2),
    ],
)
def test_whole_stdout_json_for_local_and_argv_errors(tmp_path, offline_cli, args, code):
    runner, server, _ = offline_cli
    payload = invoke(runner, args, code)
    assert payload["ok"] == (code == 0)
    assert not server.calls


def test_source_error_no_job_and_help_raw_json_equivalence(tmp_path, offline_cli):
    runner, server, _ = offline_cli
    invoke(
        runner,
        ["image", "generate", "--model", MODEL, "--prompt-file", str(tmp_path / "absent.md")],
        7,
    )
    assert not (tmp_path / "data" / "database.sqlite3").exists()
    for topic in invoke(runner, ["help"])["data"]:
        raw = runner.invoke(app, ["help", topic["topic"], "--raw"])
        assert raw.exit_code == 0
        resolved = invoke(runner, ["help", topic["topic"]])["data"]
        assert raw.stdout == resolved["markdown"]
        assert "{{" not in raw.stdout and not raw.stdout.startswith("---")
    assert not server.calls


def test_timeout_restart_sync_running_keeps_failed_then_completed_is_idempotent(
    tmp_path, offline_cli
):
    runner, server, _ = offline_cli
    server.mode = "running"
    job = invoke(
        runner,
        [
            "image",
            "generate",
            "--model",
            MODEL,
            "--allow-experimental",
            "--prompt",
            "robot",
            "--poll-interval",
            "0.01",
            "--wait-timeout",
            "0.025",
        ],
        6,
    )["data"]["job"]
    assert job["remote_ref"] and job["error"]["code"] == "JOB_TIMEOUT"
    observation = invoke(runner, ["jobs", "sync", str(job["id"])])
    assert observation["ok"] and "error" not in observation
    observed = observation["data"][0]
    assert observed["status"] == "failed" and observed["error"] == job["error"]
    server.mode = "complete"
    recovered = invoke(runner, ["jobs", "sync", str(job["id"])])["data"][0]
    assert recovered["status"] == "completed"
    assert recovered["recovery"]["previous_error"] == job["error"]
    before = list(server.calls)
    assert invoke(runner, ["jobs", "sync", str(job["id"])])["data"][0] == recovered
    assert server.calls == before
    assert sum(method == "POST" for method, _ in server.calls) == 1
    with DatabaseManager(tmp_path / "data" / "database.sqlite3") as manager:
        assert PeeweeJobRepository(manager).get(job["id"]).cost.amount == 3


@pytest.mark.parametrize("mode,exit_code", [("retry-get", 0), ("interrupt", 130)])
def test_safe_get_retry_and_local_interrupt_never_repeat_submit(
    tmp_path, offline_cli, mode, exit_code
):
    runner, server, _ = offline_cli
    server.mode = mode
    payload = invoke(
        runner,
        [
            "image",
            "generate",
            "--model",
            MODEL,
            "--allow-experimental",
            "--prompt",
            "robot",
            "--poll-interval",
            "0.01",
        ],
        exit_code,
    )
    assert sum(method == "POST" for method, _ in server.calls) == 1
    assert all(method in {"POST", "GET"} for method, _ in server.calls)
    if mode == "interrupt":
        shown = subprocess_cli(tmp_path, ["jobs", "recent"])[0]
        assert shown["remote_ref"]["remote_job_id"] == "aig_1"
        assert shown["error"]["code"] == "JOB_WAIT_INTERRUPTED"
        server.mode = "complete"
        recovered = invoke(runner, ["jobs", "sync", str(shown["id"])])["data"][0]
        assert recovered["status"] == "completed"
        assert sum(method == "POST" for method, _ in server.calls) == 1
    else:
        assert payload["data"]["job"]["status"] == "completed"
        assert sum(method == "GET" for method, _ in server.calls) == 3


def test_explicit_unverified_model_choice_and_config_no_clobber(tmp_path, offline_cli):
    runner, server, _ = offline_cli
    rejected = invoke(runner, ["image", "generate", "--model", MODEL, "--prompt", "robot"], 3)
    assert rejected["data"]["job"]["status"] == "failed" and not server.calls
    path = tmp_path / "settings.toml"
    invoke(runner, ["config", "init", "--file", str(path)])
    original = path.read_bytes()
    invoke(runner, ["config", "init", "--file", str(path)], 7)
    assert path.read_bytes() == original and CANARY.encode() not in original
    assert invoke(runner, ["--config", str(path), "config", "validate"])["ok"]


def test_sync_reuses_real_persisted_partial_files_without_duplicate_cost_or_output(
    tmp_path, offline_cli
):
    runner, server, _ = offline_cli
    server.output_count = 2
    server.fail_second_download = True
    failed = invoke(
        runner,
        ["image", "generate", "--model", MODEL, "--allow-experimental", "--prompt", "robot"],
        7,
    )["data"]["job"]
    assert len(failed["result"]["artifacts"]) == 1
    first = failed["result"]["artifacts"][0]
    assert len(list((tmp_path / "data" / "outputs").rglob("*.png"))) == 1
    completed = invoke(runner, ["jobs", "sync", str(failed["id"])])["data"][0]
    assert completed["status"] == "completed"
    assert completed["result"]["artifacts"][0] == first
    assert len(completed["result"]["artifacts"]) == 2
    assert len(list((tmp_path / "data" / "outputs").rglob("*.png"))) == 2
    before = list(server.calls)
    assert invoke(runner, ["jobs", "sync", str(failed["id"])])["data"][0] == completed
    assert before == server.calls
    assert completed["cost"] == failed["cost"]
    assert subprocess_cli(tmp_path, ["jobs", "costs"])["totals"][0]["amount"] == "3.000"
    assert sum(method == "POST" for method, _ in server.calls) == 1


@pytest.mark.parametrize("previous_status", ["submitted", "failed"])
@pytest.mark.parametrize(
    "mode,code,error_code",
    [
        ("unauthorized", 4, "PROVIDER_AUTHENTICATION"),
        ("remote-failed", 5, "REMOTE_GENERATION_FAILED"),
        ("local-failed", 7, "IMAGE_FINALIZATION_FAILED"),
    ],
)
def test_sync_reports_current_failure_not_previous_job_error(
    tmp_path, offline_cli, monkeypatch, previous_status, mode, code, error_code
):
    from aimedia.domain.job import Job

    runner, server, _ = offline_cli
    server.mode = "interrupt"
    invoke(
        runner,
        ["image", "generate", "--model", MODEL, "--allow-experimental", "--prompt", "robot"],
        130,
    )
    with DatabaseManager(tmp_path / "data" / "database.sqlite3") as manager:
        repository = PeeweeJobRepository(manager)
        original = repository.list_recent()[0]
        if previous_status == "submitted":
            original = repository.save(
                Job.model_validate(
                    {
                        **original.model_dump(),
                        "status": "submitted",
                        "error": None,
                        "completed_at": None,
                    }
                )
            )
    server.mode = mode if mode != "local-failed" else "complete"
    if mode == "local-failed":

        def fail_save(*args, **kwargs):
            raise OSError(CANARY)

        monkeypatch.setattr(bootstrap.PillowArtifactStorage, "save", fail_save)
    failed = invoke(runner, ["jobs", "sync", str(original.id)], code)
    assert not failed["ok"] and failed["error"]["code"] == error_code
    observed = failed["data"][0]
    assert observed["status"] == "failed"
    assert observed["remote_ref"] == original.remote_ref.model_dump(mode="json")
    if previous_status == "failed":
        assert observed["error"] == original.error.model_dump(mode="json")
        assert failed["error"]["code"] != observed["error"]["code"]
    else:
        assert observed["error"]["code"] == error_code
    assert sum(method == "POST" for method, _ in server.calls) == 1
    assert all(method in {"POST", "GET"} for method, _ in server.calls)
    assert (
        subprocess_cli(tmp_path, ["jobs", "show", str(original.id)])["error"] == observed["error"]
    )


@pytest.mark.parametrize("command", ["generate", "batch"])
def test_invalid_format_is_json_syntax_error_before_source_reading_or_job(
    tmp_path, offline_cli, command
):
    runner, server, _ = offline_cli
    args = ["image", command]
    if command == "batch":
        args.append(str(tmp_path / "not-read.md"))
    else:
        args += ["--prompt-file", str(tmp_path / "not-read.md")]
    payload = invoke(runner, [*args, "--model", MODEL, "--format", "invalid_canary"], 2)
    assert not payload["ok"] and payload["error"]["code"] == "INVALID_ARGUMENT"
    assert not (tmp_path / "data" / "database.sqlite3").exists()
    assert not server.calls


def test_valid_jpg_parser_alias_creates_real_jpeg_artifact(tmp_path, offline_cli):
    runner, server, _ = offline_cli
    job = invoke(
        runner,
        [
            "image",
            "generate",
            "--model",
            MODEL,
            "--allow-experimental",
            "--prompt",
            "robot",
            "--format",
            "jpg",
        ],
    )["data"]["job"]
    assert job["request"]["final_format"] == "jpeg"
    artifact = job["result"]["artifacts"][0]
    assert artifact["mime_type"] == "image/jpeg"
    with Image.open(tmp_path / "data" / artifact["local_path"]) as image:
        assert image.format == "JPEG"
    assert sum(method == "POST" for method, _ in server.calls) == 1
