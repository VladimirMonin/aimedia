"""Проверки шаблона отчёта этапа.

Шаблон нужен, чтобы отчёт этапа начинался с честного `NOT_RUN`, а не с
скопированного «зелёного» результата. Тест фиксирует два инварианта: шаблон
остаётся валидным JSON, и в нём нет ни одного выдуманного `PASSED` или
непустого evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "docs" / "plans" / "progress" / "stage-report.template.json"

# Значения, которые запрещено оставлять в пустом шаблоне.
FABRICATED_STATUSES = {"PASSED", "FAILED", "SKIPPED", "XFAILED", "NOT_COLLECTED"}


def _template() -> dict[str, object]:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def test_template_exists_and_is_valid_json() -> None:
    assert TEMPLATE_PATH.is_file()
    assert isinstance(_template(), dict)


def test_template_starts_not_started_and_not_run() -> None:
    template = _template()
    assert template["work_status"] == "NOT_STARTED"
    assert template["source_commit"] is None
    checks = template["checks"]
    assert isinstance(checks, list) and checks
    for check in checks:
        assert check["status"] == "NOT_RUN"
        assert check["exit_code"] is None
        assert check["test_node_id"] is None
        assert check["evidence_path"] is None
        assert check["status"] not in FABRICATED_STATUSES


def test_template_has_no_fabricated_evidence() -> None:
    template = _template()
    assert template["environment"] is None
    assert template["coverage"] is None
    assert template["live_checks"] == []
    assert template["known_gaps"] == []
    assert template["reviewer"] is None
