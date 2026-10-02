#!/usr/bin/env python3
"""Run artifact/validator checks, keyword controls and scenario inventory checks.

Inventory descriptions do not execute model behavior or visual QA.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import jsonschema
import yaml
from validate_contract import validate_artifact
from test_contract_regressions import run_regressions


def load(path: Path):
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


def classify(prompt: str) -> str:
    text = prompt.casefold()
    audit = ["проверь", "аудит", "найди ошибки", "правильно ли", "по картинке ответить", "сверь схему", "искажения данных", "сделай qa", "оцени читаемость", "audit"]
    repair = ["исправь", "перекрась", "почини", "не меняя", "не трогай", "замени только", "восстанови направление", "удали лишний", "поправь контраст", "repair"]
    create = ["инфограф", "объясни визуально", "создай техническую схему", "визуальную модель", "диаграмму архитектуры", "карта процесса", "визуализируй", "таймлайн", "visual explanation"]
    if any(token in text for token in repair):
        return "repair"
    if any(token in text for token in audit):
        return "audit"
    if any(token in text for token in create):
        return "create"
    return "none"


def run(command: list[str]) -> tuple[int, str]:
    completed = subprocess.run(command, text=True, capture_output=True, encoding="utf-8")
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("skill_root", type=Path)
    args = parser.parse_args()
    root = args.skill_root.resolve()
    failures: list[str] = []
    checks = 0

    routing = load(root / "tests/routing-cases.yaml")
    for case in routing["cases"]:
        observed = classify(case["prompt"])
        if observed != case["expected"]:
            failures.append(f"routing: {case['prompt']!r}: expected {case['expected']}, observed {observed}")

    regressions = load(root / "tests/regression-cases.yaml")
    for case in regressions["cases"]:
        if len(case.get("assertions", [])) < 3:
            failures.append(f"regression {case['id']} has fewer than 3 assertions")
        if not case.get("covers"):
            failures.append(f"regression {case['id']} has no rule coverage IDs")

    registry = load(root / "rules/registry.yaml").get("rules", {})
    blocker_ids = {rule_id for rule_id, rule in registry.items() if rule.get("severity") == "blocker"}
    covered_ids = {rule_id for case in regressions["cases"] for rule_id in case.get("covers", [])}
    missing_blockers = sorted(blocker_ids - covered_ids)
    if missing_blockers:
        failures.append(f"blockers without planned behavioral scenario: {missing_blockers}")

    executable = run_regressions(root)
    failures.extend(executable["failures"])

    contract_pairs = [
        ("production-contract.schema.json", "production-contract.yaml"),
        ("content-packet.schema.json", "content-packet.yaml"),
        ("semantic-spec.schema.json", "semantic-spec.yaml"),
        ("visual-spec.schema.json", "visual-spec.yaml"),
        ("qa-report.schema.json", "qa-report.yaml"),
    ]
    for directory in sorted((root / "examples/create").iterdir()):
        if not directory.is_dir():
            continue
        for schema_name, artifact_name in contract_pairs:
            checks += 1
            schema = load(root / "contracts" / schema_name)
            artifact = load(directory / artifact_name)
            errors = validate_artifact(schema, artifact)
            if errors:
                failures.append(f"contract {directory.name}/{artifact_name}: {errors[0]}")
        checks += 1
        code, output = run([
            sys.executable,
            str(root / "scripts/validate_graph.py"),
            str(directory / "semantic-spec.yaml"),
            str(directory / "wireframe.yaml"),
        ])
        if code != 0:
            failures.append(f"graph {directory.name}: {output}")

    audit_case = root / "examples/audit/reversed-edge-invented-node"
    checks += 1
    code, output = run([
        sys.executable,
        str(root / "scripts/validate_graph.py"),
        str(audit_case / "semantic-spec.yaml"),
        str(audit_case / "wireframe.yaml"),
    ])
    if code == 0 or "STR-001" not in output or "SEM-003" not in output:
        failures.append(f"audit example did not catch STR-001 and SEM-003: {output}")
    checks += 1
    audit_schema = load(root / "contracts/qa-report.schema.json")
    audit_report = load(audit_case / "qa-report.yaml")
    audit_errors = validate_artifact(audit_schema, audit_report)
    if audit_errors or audit_report.get("verdict") == "pass":
        failures.append("audit example report is invalid or incorrectly passes")

    repair_case = root / "examples/repair/rag-minimal-patch"
    checks += 1
    code, output = run([
        sys.executable,
        str(root / "scripts/validate_graph.py"),
        str(repair_case / "semantic-spec.yaml"),
        str(repair_case / "wireframe-after.yaml"),
    ])
    if code != 0:
        failures.append(f"repair example graph did not pass: {output}")
    checks += 1
    code, output = run([
        sys.executable,
        str(root / "scripts/validate_preserve_contract.py"),
        str(repair_case / "edit-contract.yaml"),
        str(repair_case / "preserve-before.json"),
        str(repair_case / "preserve-after.json"),
    ])
    if code != 0:
        failures.append(f"repair example preserve contract did not pass: {output}")
    for schema_name, artifact_name in [
        ("edit-contract.schema.json", "edit-contract.yaml"),
        ("qa-report.schema.json", "qa-report.yaml"),
    ]:
        checks += 1
        schema = load(root / "contracts" / schema_name)
        artifact = load(repair_case / artifact_name)
        errors = validate_artifact(schema, artifact)
        if errors:
            failures.append(f"repair example {artifact_name}: {errors[0]}")

    with tempfile.TemporaryDirectory(prefix="infographic-designer-") as temp:
        temp_root = Path(temp)
        semantic = load(root / "examples/create/rag-architecture/semantic-spec.yaml")
        wireframe = load(root / "examples/create/rag-architecture/wireframe.yaml")
        wireframe["edges"][0]["from"], wireframe["edges"][0]["to"] = wireframe["edges"][0]["to"], wireframe["edges"][0]["from"]
        semantic_path = temp_root / "semantic.yaml"
        wireframe_path = temp_root / "wireframe.yaml"
        semantic_path.write_text(yaml.safe_dump(semantic, allow_unicode=True, sort_keys=False), encoding="utf-8")
        wireframe_path.write_text(yaml.safe_dump(wireframe, allow_unicode=True, sort_keys=False), encoding="utf-8")
        checks += 1
        code, output = run([sys.executable, str(root / "scripts/validate_graph.py"), str(semantic_path), str(wireframe_path)])
        if code == 0 or "STR-001" not in output:
            failures.append("negative control: reversed edge was not rejected as STR-001")

        invalid = load(root / "examples/create/gpu-callouts/semantic-spec.yaml")
        invalid.pop("primary_question")
        checks += 1
        errors = list(jsonschema.Draft202012Validator(load(root / "contracts/semantic-spec.schema.json")).iter_errors(invalid))
        if not errors:
            failures.append("negative control: missing primary_question passed semantic schema")

    checks += 1
    code, output = run([sys.executable, str(root / "scripts/validate_package.py"), str(root)])
    if code != 0:
        failures.append(f"package validation: {output}")

    result = {
        "passed": not failures,
        "artifact_checks": checks,
        "executable_regression_checks": executable["executable_regression_checks"],
        "routing_keyword_controls": len(routing["cases"]),
        "scenario_inventory_checks": len(regressions["cases"]),
        "behavioral_scenarios_executed": 0,
        "evidence_scope": "Structured artifact validation and local keyword controls; no model activation, render or vision proof.",
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
