"""Actual public argv in a fresh process, with transport-only offline injections."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from offline_policy import child_process_env

CANARY = "subprocess_canary_" + "synthetic_secret"

LAUNCHER = """
import os, sys, json
from pathlib import Path
import httpx
import aimedia.bootstrap as bootstrap
from download_backend import FakeResolver, RecordingBackend, raw_http_response, PUBLIC_IPV4
from image_fixtures import png_bytes
os.environ['POLZA_API_KEY'] = 'subprocess_canary_' + 'synthetic_secret'
client_class = httpx.AsyncClient
downloader_class = bootstrap.PolzaArtifactDownloader
async def handler(request):
    assert request.method in {'POST', 'GET'}
    assert str(request.url).startswith('https://polza.ai/api/v1/media')
    root = Path(os.environ['AIMEDIA_DATA_DIR']).parent
    with (root / 'methods.txt').open('a', encoding='utf-8') as receipt:
        receipt.write(request.method + '\\n')
    if request.method == 'GET' and os.environ.get('AIMEDIA_OFFLINE_TEST_MODE') == 'hold':
        import asyncio
        (root / 'ready').write_text('ready')
        for _ in range(1000):
            if (root / 'release').exists():
                break
            await asyncio.sleep(0.01)
        else:
            raise TimeoutError('controlled GET barrier timed out')
    if request.method == 'POST':
        receipt = Path(os.environ['AIMEDIA_DATA_DIR']).parent / 'receipt.json'
        receipt.write_text(json.dumps({
            'posts': 1, 'prompt': json.loads(request.content)['input']['prompt']}))
        if os.environ.get('AIMEDIA_OFFLINE_TEST_MODE') == 'interrupt':
            return httpx.Response(200, json={
                'id':'aig_subprocess', 'object':'media.generation', 'status':'pending'})
    elif os.environ.get('AIMEDIA_OFFLINE_TEST_MODE') == 'interrupt':
        import signal
        signal.raise_signal(signal.SIGINT)
        await __import__('asyncio').sleep(0)
    return httpx.Response(200, json={'id':'aig_subprocess', 'object':'media.generation',
        'status':'completed','data':[{'url':'https://s3.polza.ai/result.png'}],
        'usage':{'cost_rub':'3.000'}})
bootstrap.httpx.AsyncClient = lambda **kwargs: client_class(
    transport=httpx.MockTransport(handler), **kwargs)
def downloader(**kwargs):
    backend = RecordingBackend([raw_http_response(
        body=png_bytes(), headers=[(b'connection',b'close')])])
    return downloader_class(**kwargs, resolver=FakeResolver(default=[PUBLIC_IPV4]),
                            direct_backend=backend)
bootstrap.PolzaArtifactDownloader = downloader
from aimedia.cli.app import main
main()
"""


def test_subprocess_generate_then_restart_reads_managed_copy_and_search(tmp_path: Path):
    launcher = tmp_path / "offline_launch.py"
    launcher.write_text(LAUNCHER, encoding="utf-8")
    env = child_process_env()
    env.update(
        AIMEDIA_DATA_DIR=str(tmp_path / "data"),
        PYTHONIOENCODING="utf-8",
        AIMEDIA_CONFIG=str(tmp_path / "missing-settings.toml"),
    )
    support = Path(__file__).resolve().parents[1] / "support"
    import os

    env["PYTHONPATH"] += os.pathsep + str(support)
    from image_fixtures import png_bytes

    reference = tmp_path / "reference.png"
    reference.write_bytes(png_bytes())
    a, c = tmp_path / "a.md", tmp_path / "c.md"
    a.write_text("first laboratory", encoding="utf-8")
    c.write_text("last robot", encoding="utf-8")
    args = [
        sys.executable,
        str(launcher),
        "--json",
        "image",
        "generate",
        "--model",
        "qwen-image-2-1",
        "--allow-experimental",
        "--prompt-file",
        str(a),
        "--prompt",
        "middle",
        "--prompt-file",
        str(c),
        "--image",
        str(reference),
    ]
    response = subprocess.run(
        args, cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert response.returncode == 0, (response.stdout, response.stderr)
    job = json.loads(response.stdout)["data"]["job"]
    assert job["status"] == "completed"
    assert job["compiled_prompt"]["text"] == "first laboratory\n\nmiddle\n\nlast robot"
    assert json.loads((tmp_path / "receipt.json").read_text()) == {
        "posts": 1,
        "prompt": job["compiled_prompt"]["text"],
    }
    a.unlink()
    c.unlink()
    reference.unlink()
    for command in [
        ["jobs", "show", str(job["id"])],
        ["jobs", "search", "laboratory robot"],
        ["jobs", "costs", "--today"],
    ]:
        read = subprocess.run(
            [sys.executable, "-m", "aimedia", *command, "--json"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert read.returncode == 0, read.stderr
        payload = json.loads(read.stdout)
        assert payload["ok"]
        if command[1] == "show":
            copy = Path(payload["data"]["managed_inputs"][0]["path"])
            assert copy.read_bytes() == png_bytes()
        assert CANARY not in read.stdout + read.stderr
    assert CANARY.encode() not in (tmp_path / "data" / "database.sqlite3").read_bytes()


def test_subprocess_sigint_retains_ref_then_explicit_sync_has_no_post(tmp_path):
    launcher = tmp_path / "offline_launch.py"
    launcher.write_text(LAUNCHER, encoding="utf-8")
    env = child_process_env()
    import os

    env["PYTHONPATH"] += os.pathsep + str(Path(__file__).resolve().parents[1] / "support")
    env.update(
        AIMEDIA_CONFIG=str(tmp_path / "missing-settings.toml"),
        AIMEDIA_DATA_DIR=str(tmp_path / "data"),
        PYTHONIOENCODING="utf-8",
        AIMEDIA_OFFLINE_TEST_MODE="interrupt",
    )
    process = subprocess.run(
        [
            sys.executable,
            str(launcher),
            "image",
            "generate",
            "--model",
            "qwen-image-2-1",
            "--allow-experimental",
            "--prompt",
            "robot",
            "--json",
            "--poll-interval",
            "0.01",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert process.returncode == 130, (process.stdout, process.stderr)
    assert json.loads(process.stdout)["error"]["code"] == "JOB_WAIT_INTERRUPTED"
    read = subprocess.run(
        [sys.executable, "-m", "aimedia", "jobs", "recent", "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert read.returncode == 0, read.stderr
    job = json.loads(read.stdout)["data"][0]
    assert job["remote_ref"]["remote_job_id"] == "aig_subprocess"
    assert job["error"]["code"] == "JOB_WAIT_INTERRUPTED"
    receipt = (tmp_path / "receipt.json").read_bytes()
    env["AIMEDIA_OFFLINE_TEST_MODE"] = "hold"
    sync_args = [sys.executable, str(launcher), "jobs", "sync", str(job["id"]), "--json"]
    owner = subprocess.Popen(
        sync_args,
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        import time

        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            assert owner.poll() is None
            time.sleep(0.01)
        assert (tmp_path / "ready").exists()
        observed_before = (tmp_path / "methods.txt").read_bytes()
        contender = subprocess.run(
            sync_args,
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert contender.returncode == 8, (contender.stdout, contender.stderr)
        assert json.loads(contender.stdout)["error"]["code"] == "DATABASE_ERROR"
        assert (tmp_path / "methods.txt").read_bytes() == observed_before
    finally:
        (tmp_path / "release").write_text("release")
        stdout, stderr = owner.communicate(timeout=30)
    assert owner.returncode == 0, (stdout, stderr)
    assert json.loads(stdout)["data"][0]["status"] == "completed"
    # Interrupted status GET + owning sync status/result GETs; contender had none.
    assert (tmp_path / "methods.txt").read_text().splitlines() == ["POST", "GET", "GET", "GET"]
    env["AIMEDIA_OFFLINE_TEST_MODE"] = "complete"
    synced = subprocess.run(
        [sys.executable, str(launcher), "jobs", "sync", str(job["id"]), "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert synced.returncode == 0, (synced.stdout, synced.stderr)
    assert json.loads(synced.stdout)["data"][0]["status"] == "completed"
    assert (tmp_path / "receipt.json").read_bytes() == receipt
    assert CANARY not in process.stdout + process.stderr + synced.stdout + synced.stderr


def test_subprocess_search_reference_and_result_snapshots_after_source_deleted(tmp_path):
    import os

    from image_fixtures import png_bytes

    launcher = tmp_path / "offline_launch.py"
    launcher.write_text(LAUNCHER, encoding="utf-8")
    env = child_process_env()
    env["PYTHONPATH"] += os.pathsep + str(Path(__file__).resolve().parents[1] / "support")
    config, root = tmp_path / "absent.toml", tmp_path / "data"
    env.update(PYTHONIOENCODING="utf-8", AIMEDIA_DATA_DIR=str(root), AIMEDIA_CONFIG=str(config))
    globals_ = ["--config", str(config), "--data-dir", str(root), "--json"]
    reference = tmp_path / "cobalt_reference_unique.png"
    reference.write_bytes(png_bytes(width=3, height=2))
    generated = subprocess.run(
        [
            sys.executable,
            str(launcher),
            *globals_,
            "image",
            "generate",
            "--model",
            "qwen-image-2-1",
            "--allow-experimental",
            "--prompt",
            "plain robot",
            "--image",
            str(reference),
            "--name",
            "tangerine_result_unique",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert generated.returncode == 0, (generated.stdout, generated.stderr)
    job = json.loads(generated.stdout)["data"]["job"]
    ref, artifact = job["inputs"][0], job["result"]["artifacts"][0]
    assert ref["sha256"] != artifact["sha256"]
    reference.unlink()
    queries = [
        reference.name,
        ref["managed_path"],
        ref["sha256"],
        Path(artifact["local_path"]).name,
        artifact["local_path"],
        artifact["sha256"],
    ]
    for query in queries:
        assert query not in job["compiled_prompt"]["text"] + job["model"]["id"]
        read = subprocess.run(
            [
                sys.executable,
                "-m",
                "aimedia",
                *globals_,
                "jobs",
                "search",
                query,
                "--status",
                "completed",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert read.returncode == 0, (read.stdout, read.stderr)
        payload = json.loads(read.stdout)
        assert payload["ok"] and [item["id"] for item in payload["data"]] == [job["id"]]
        assert CANARY not in read.stdout + read.stderr
    negative = subprocess.run(
        [sys.executable, "-m", "aimedia", *globals_, "jobs", "search", "nonexistent_unique_word"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert negative.returncode == 0
    assert json.loads(negative.stdout)["data"] == []


BATCH_INTERRUPT_LAUNCHER = r"""
import asyncio, json, os, signal
from contextlib import contextmanager
from pathlib import Path
import httpx
import aimedia.bootstrap as bootstrap
from download_backend import FakeResolver, RecordingBackend, raw_http_response, PUBLIC_IPV4
from image_fixtures import png_bytes
from aimedia.storage.database import DatabaseManager
from aimedia.storage.repository import PeeweeJobRepository
from aimedia.storage.ownership import JobOwnedError
os.environ['POLZA_API_KEY'] = 'subprocess_canary_' + 'synthetic_secret'
root = Path(os.environ['AIMEDIA_DATA_DIR']).parent
interrupt = os.environ.get('AIMEDIA_OFFLINE_TEST_MODE') == 'batch-interrupt'
client_class, downloader_class = httpx.AsyncClient, bootstrap.PolzaArtifactDownloader
claim = bootstrap.claim_job
owned, peak, downloads, posts = set(), 0, 0, 0
@contextmanager
def measured_claim(data_root, job_id):
    global peak
    with claim(data_root, job_id):
        assert job_id not in owned
        owned.add(job_id)
        peak = max(peak, len(owned))
        try:
            # A second real kernel claim cannot enter the same active Job.
            with claim(data_root, job_id):
                raise AssertionError('second owner entered')
        except JobOwnedError:
            pass
        try:
            yield
        finally:
            owned.remove(job_id)
            (root / 'ownership.json').write_text(json.dumps({'peak':peak,'active':len(owned)}))
bootstrap.claim_job = measured_claim
async def handler(request):
    global posts
    assert request.method in {'POST', 'GET'}  # Local cancellation never sends cancel.
    assert str(request.url).startswith('https://polza.ai/api/v1/media')
    with (root / 'methods.txt').open('a') as receipt:
        receipt.write(request.method + '\n')
    if request.method == 'POST':
        posts += 1
        return httpx.Response(200, json={'id':f'aig_batch_{posts}',
            'object':'media.generation','status':'pending'})
    remote = request.url.path.rsplit('/',1)[-1]
    return httpx.Response(200, json={'id':remote,'object':'media.generation',
        'status':'completed','data':[{'url':f'https://s3.polza.ai/{remote}.png'}],
        'usage':{'cost_rub':'3.000'}})
bootstrap.httpx.AsyncClient = lambda **kwargs: client_class(
    transport=httpx.MockTransport(handler), **kwargs)
class InterruptingDownloader(downloader_class):
    async def fetch(self, artifact):
        global downloads
        content = await super().fetch(artifact)
        if interrupt:
            downloads += 1
            if downloads == 2:
                database_path = Path(os.environ['AIMEDIA_DATA_DIR']) / 'database.sqlite3'
                with DatabaseManager(database_path) as db:
                    jobs = PeeweeJobRepository(db).list_recent()
                    (root / 'checkpoint.json').write_text(json.dumps({
                        'jobs':[j.model_dump(mode='json') for j in jobs],
                        'owned': sorted(owned), 'peak':peak}))
                signal.raise_signal(signal.SIGINT)
            await asyncio.Event().wait()
        return content

def downloader(**kwargs):
    backend = RecordingBackend([raw_http_response(body=png_bytes(),
        headers=[(b'connection',b'close')])])
    return InterruptingDownloader(**kwargs, resolver=FakeResolver(default=[PUBLIC_IPV4]),
                                  direct_backend=backend)
bootstrap.PolzaArtifactDownloader = downloader
from aimedia.cli.app import main
main()
"""


def test_subprocess_batch_sigint_cancels_queued_preserves_active_cost_refs_and_releases_owners(
    tmp_path,
):
    import os

    from image_fixtures import png_bytes

    launcher = tmp_path / "batch_launch.py"
    launcher.write_text(BATCH_INTERRUPT_LAUNCHER, encoding="utf-8")
    config, root = tmp_path / "absent.toml", tmp_path / "data"
    env = child_process_env()
    env["PYTHONPATH"] += os.pathsep + str(Path(__file__).resolve().parents[1] / "support")
    env.update(
        AIMEDIA_DATA_DIR=str(root),
        AIMEDIA_CONFIG=str(config),
        PYTHONIOENCODING="utf-8",
        AIMEDIA_OFFLINE_TEST_MODE="batch-interrupt",
    )
    globals_ = ["--config", str(config), "--data-dir", str(root), "--json"]
    files = []
    for index in range(5):
        path = tmp_path / f"batch_{index}.md"
        path.write_text(f"robot {index}", encoding="utf-8")
        files.append(str(path))
    reference = tmp_path / "batch_reference.png"
    reference.write_bytes(png_bytes(width=3, height=2))
    response = subprocess.run(
        [
            sys.executable,
            str(launcher),
            *globals_,
            "image",
            "batch",
            *files,
            "--model",
            "qwen-image-2-1",
            "--allow-experimental",
            "--image",
            str(reference),
            "--concurrency",
            "2",
            "--poll-interval",
            "0.01",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert response.returncode == 130, (response.stdout, response.stderr)
    assert json.loads(response.stdout)["error"]["code"] == "JOB_WAIT_INTERRUPTED"
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    before = sorted(checkpoint["jobs"], key=lambda job: job["id"])
    assert checkpoint["owned"] == [before[0]["id"], before[1]["id"]]
    assert checkpoint["peak"] == 2
    assert [job["status"] for job in before] == ["submitted"] * 2 + ["created"] * 3
    assert all(job["cost"] == {"amount": "3.000", "currency": "RUB"} for job in before[:2])
    assert all(job["remote_ref"] and job["inputs"][0]["managed_path"] for job in before[:2])
    assert all(job["cost"] is None and job["remote_ref"] is None for job in before[2:])
    assert json.loads((tmp_path / "ownership.json").read_text()) == {"peak": 2, "active": 0}
    methods = (tmp_path / "methods.txt").read_text().splitlines()
    assert methods.count("POST") == 2 and set(methods) == {"POST", "GET"}
    reference.unlink()
    for path in files:
        Path(path).unlink()
    read = subprocess.run(
        [sys.executable, "-m", "aimedia", *globals_, "jobs", "recent"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert read.returncode == 0, read.stderr
    jobs = sorted(json.loads(read.stdout)["data"], key=lambda job: job["id"])
    assert [job["status"] for job in jobs] == ["failed"] * 2 + ["cancelled"] * 3
    for job, prior in zip(jobs[:2], before[:2], strict=True):
        assert job["remote_ref"] == prior["remote_ref"] and job["cost"] == prior["cost"]
        assert job["error"]["code"] == "JOB_WAIT_INTERRUPTED"
        assert (root / job["inputs"][0]["managed_path"]).read_bytes() == png_bytes(
            width=3, height=2
        )
    assert all(
        job["completed_at"] and not job["remote_ref"] and job["cost"] is None for job in jobs[2:]
    )
    env["AIMEDIA_OFFLINE_TEST_MODE"] = "complete"
    recovered = subprocess.run(
        [sys.executable, str(launcher), *globals_, "jobs", "sync", "--all", "--concurrency", "2"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    synced = json.loads(recovered.stdout)["data"]
    assert len(synced) == 2 and all(job["status"] == "completed" for job in synced)
    assert all(
        job["recovery"]["previous_error"]["code"] == "JOB_WAIT_INTERRUPTED" for job in synced
    )
    assert (tmp_path / "methods.txt").read_text().splitlines().count("POST") == 2
    assert CANARY not in response.stdout + response.stderr + read.stdout + recovered.stdout
    costs = subprocess.run(
        [sys.executable, "-m", "aimedia", *globals_, "jobs", "costs"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert costs.returncode == 0
    report = json.loads(costs.stdout)["data"]
    assert report["totals"][0]["amount"] == "6.000"
    assert report["unknown_cost_jobs"] == 3
