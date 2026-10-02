#!/usr/bin/env python3
"""Executable validator regressions; no model, rendering or vision evaluation."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from validate_contract import load, validate_artifact
from validate_graph import validate_graph
from validate_preserve_contract import validate_preserve


def run_regressions(root: Path):
    failures = []
    checks = 0
    cases = []

    def expect(name, errors, valid=False, rule=None):
        nonlocal checks
        checks += 1
        cases.append(name)
        if bool(errors) == valid or (rule and not any(rule in e for e in errors)):
            failures.append(f"{name}: expected {'pass' if valid else 'reject'} {rule or ''}; observed {errors}")

    semantic = load(root / "examples/create/rag-architecture/semantic-spec.yaml")
    wire = load(root / "examples/create/rag-architecture/wireframe.yaml")
    semantic_schema = load(root / "contracts/semantic-spec.schema.json")
    qa_schema = load(root / "contracts/qa-report.schema.json")
    production_schema = load(root / "contracts/production-contract.schema.json")

    expect("graph-baseline", validate_graph(semantic, wire), valid=True)
    nested = deepcopy(semantic)
    nested["relationships"] = [{"id": "nest", "from": "documents", "to": "chunker", "type": "containment", "directed": True}]
    nested_wire = deepcopy(wire)
    nested_wire["edges"] = []
    nested_wire["nodes"][1]["parent"] = "documents"
    expect("containment-schema-valid", validate_artifact(semantic_schema, nested), valid=True)
    expect("containment-visible-parent-valid", validate_graph(nested, nested_wire), valid=True)
    bad = deepcopy(nested_wire); bad["nodes"][1].pop("parent")
    expect("containment-missing-parent", validate_graph(nested, bad), rule="STR-002")
    bad = deepcopy(nested_wire); bad["nodes"][1]["parent"] = "llm"
    expect("containment-wrong-parent", validate_graph(nested, bad), rule="STR-002")
    bad = deepcopy(wire); bad["nodes"][1]["parent"] = "documents"
    expect("new-unapproved-nesting", validate_graph(semantic, bad), rule="STR-002")
    bad = deepcopy(nested_wire); bad["nodes"][1]["parent"] = "absent"
    expect("containment-dangling-parent", validate_graph(nested, bad), rule="STR-002")
    bad = deepcopy(nested_wire); bad["nodes"][0]["parent"] = "chunker"
    expect("wireframe-containment-cycle", validate_graph(nested, bad), rule="STR-002")
    bad_sem = deepcopy(nested); bad_sem["relationships"].append({"id": "cycle", "from": "chunker", "to": "documents", "type": "containment", "directed": True})
    expect("semantic-containment-cycle", validate_graph(bad_sem, nested_wire), rule="STR-002")
    bad_sem = deepcopy(nested); bad_sem["relationships"].append({"id": "second-parent", "from": "llm", "to": "chunker", "type": "containment", "directed": True})
    expect("containment-multiple-parents", validate_graph(bad_sem, nested_wire), rule="STR-002")
    bad_sem = deepcopy(nested); bad_sem["relationships"][0]["directed"] = False
    expect("undirected-containment-schema", validate_artifact(semantic_schema, bad_sem))
    bad_sem = deepcopy(semantic); bad_sem["entities"][1]["parent"] = "documents"
    expect("legacy-semantic-parent-rejected", validate_artifact(semantic_schema, bad_sem))
    bad_sem = deepcopy(nested); bad_sem["relationships"][0]["from"] = "absent"
    expect("semantic-containment-dangling-parent", validate_graph(bad_sem, nested_wire), rule="STR-002")
    bad_sem = deepcopy(semantic); bad_sem["entities"][0].pop("id")
    expect("missing-semantic-id-clean-rejection", validate_graph(bad_sem, wire), rule="STR-006")
    bad_sem = deepcopy(semantic); bad_sem["relationships"][0].pop("label")
    expect("directed-semantic-label-required", validate_artifact(semantic_schema, bad_sem))
    for field, source, rule in [("entities", semantic, "STR-006"), ("relationships", semantic, "STR-006")]:
        bad_sem = deepcopy(source); bad_sem[field].append(deepcopy(bad_sem[field][0]))
        expect(f"duplicate-semantic-{field}", validate_graph(bad_sem, wire), rule=rule)
    for field in ["nodes", "edges"]:
        bad = deepcopy(wire); bad[field].append(deepcopy(bad[field][0]))
        expect(f"duplicate-wireframe-{field}", validate_graph(semantic, bad), rule="STR-006")
    bad = deepcopy(wire); bad["edges"][0]["from"], bad["edges"][0]["to"] = bad["edges"][0]["to"], bad["edges"][0]["from"]
    expect("reversed-directed-edge", validate_graph(semantic, bad), rule="STR-001")
    bad = deepcopy(wire); bad["edges"][0]["directed"] = False
    expect("explicit-direction-drift", validate_graph(semantic, bad), rule="STR-001")
    bad = deepcopy(wire); bad["edges"][0]["label"] = " "
    expect("critical-label-missing", validate_graph(semantic, bad), rule="STR-004")
    bad = deepcopy(wire); bad["edges"][0]["critical"] = False; bad["edges"][0].pop("label")
    expect("directed-label-cannot-bypass-critical", validate_graph(semantic, bad), rule="STR-004")
    bad = deepcopy(wire); bad["edges"][0]["label"] = "invented verb"
    expect("approved-label-drift", validate_graph(semantic, bad), rule="STR-004")
    undirected = deepcopy(semantic); undirected["relationships"][0]["directed"] = False
    bad = deepcopy(wire); bad["edges"][0]["from"], bad["edges"][0]["to"] = bad["edges"][0]["to"], bad["edges"][0]["from"]
    expect("undirected-endpoint-order-valid", validate_graph(undirected, bad), valid=True)
    bad = deepcopy(wire); bad["edges"][0]["to"] = "absent"
    expect("dangling-wireframe-edge", validate_graph(semantic, bad), rule="STR-003")
    bad_sem = deepcopy(semantic); bad_sem["relationships"][0]["to"] = "absent"
    expect("dangling-semantic-edge", validate_graph(bad_sem, wire), rule="STR-003")

    qa = {"schema_version": "1.0", "gate_id": "G2_STRUCTURAL", "verdict": "pass", "confidence": 1,
          "hard_fails": [], "findings": [], "unknowns": [], "scores": {}, "release_recommendation": "advance_to_next_stage"}
    for gate in ["G1_SEMANTIC", "G2_STRUCTURAL"]:
        candidate = deepcopy(qa); candidate["gate_id"] = gate
        expect(f"{gate}-intermediate-pass", validate_artifact(qa_schema, candidate), valid=True)
    bad = deepcopy(qa); bad["release_recommendation"] = "release"
    expect("intermediate-cannot-release", validate_artifact(qa_schema, bad))
    for key in ["g1_prerequisite", "g2_structural"]:
        for status in ["fail", "not_run"]:
            bad = deepcopy(qa); bad["scores"][key] = status
            expect(f"pass-rejects-{key}-{status}", validate_artifact(qa_schema, bad))
    for status in ["fail", "not_run"]:
        bad = deepcopy(qa); bad["scores"]["structural_checks"] = {"containment": status}
        expect(f"pass-rejects-structural-check-{status}", validate_artifact(qa_schema, bad))
    bad = deepcopy(qa); bad["scores"]["graph_validator"] = {"valid": False, "nodes": 5, "edges": 4}
    expect("pass-rejects-failed-graph", validate_artifact(qa_schema, bad))
    final = deepcopy(qa); final.update(gate_id="G4_RELEASE", release_recommendation="release", scores={"g1_prerequisite": "pass", "g2_structural": "pass", "total": 92, "threshold": 80})
    expect("final-release-valid", validate_artifact(qa_schema, final), valid=True)
    bad = deepcopy(final); bad["scores"] = {}
    expect("final-release-missing-prerequisites", validate_artifact(qa_schema, bad))
    bad = deepcopy(final); bad["scores"]["total"] = 79
    expect("score-below-threshold", validate_artifact(qa_schema, bad))
    bad = deepcopy(final); bad["scores"].pop("threshold")
    expect("score-missing-threshold", validate_artifact(qa_schema, bad))
    blocked = deepcopy(qa); blocked.update(verdict="repair", release_recommendation="rebuild_wireframe")
    blocked["hard_fails"] = [{"rule_id": "STR-002", "title": "False containment"}]
    blocked["findings"] = [{"id": "f1", "rule_id": "STR-002", "severity": "blocker", "expected": "approved parent", "observed": "invented parent", "evidence": "graph validator", "minimal_fix": "restore parent"}]
    expect("repair-blocker-valid", validate_artifact(qa_schema, blocked), valid=True)
    bad = deepcopy(blocked); bad["scores"] = {"total": 99, "threshold": 80}
    expect("blocker-cannot-have-ranking-score", validate_artifact(qa_schema, bad))
    bad = deepcopy(blocked); bad["release_recommendation"] = "release"
    expect("repair-blocker-score-release", validate_artifact(qa_schema, bad))
    bad = deepcopy(blocked); bad["scores"] = {"total": 99, "threshold": 80}; bad["release_recommendation"] = "release"
    expect("original-blocker-plus-score-plus-release-bug", validate_artifact(qa_schema, bad))
    bad = deepcopy(qa); bad.update(verdict="repair", release_recommendation="repair_render"); bad["scores"] = {"total": 99, "threshold": 80}
    expect("nonpassing-gate-cannot-rank", validate_artifact(qa_schema, bad))
    bad = deepcopy(blocked); bad["hard_fails"] = []
    expect("blocker-missing-hard-fail", validate_artifact(qa_schema, bad))
    bad = deepcopy(blocked); bad["hard_fails"][0]["rule_id"] = "SEM-003"
    expect("blocker-hard-fail-id-mismatch", validate_artifact(qa_schema, bad))
    bad = deepcopy(blocked); bad["findings"] = []
    expect("hard-fail-missing-finding", validate_artifact(qa_schema, bad))
    bad = deepcopy(blocked); bad["verdict"] = "pass"; bad["release_recommendation"] = "advance_to_next_stage"
    expect("pass-with-blocker", validate_artifact(qa_schema, bad))

    production = load(root / "examples/create/rag-architecture/production-contract.yaml")
    for rigor in [None, "standard", "strict"]:
        candidate = deepcopy(production)
        if rigor is None: candidate.pop("rigor", None)
        else: candidate["rigor"] = rigor
        expect(f"production-rigor-{rigor or 'default'}", validate_artifact(production_schema, candidate), valid=True)
    bad = deepcopy(production); bad["rigor"] = "enterprise"
    expect("unknown-rigor-rejected", validate_artifact(production_schema, bad))

    edit = {"preserve": ["nodes.a.label"], "modify": [{"target_id": "nodes.b", "property": "color"}]}
    before = {"nodes": {"a": {"label": "Keep"}, "b": {"color": "red"}}}
    after = deepcopy(before); after["nodes"]["b"]["color"] = "blue"
    expect("preserve-approved-change", validate_preserve(edit, before, after)[0], valid=True)
    bad = deepcopy(after); bad["nodes"]["a"]["label"] = "Drift"
    expect("preserved-text-drift", validate_preserve(edit, before, bad)[0], rule="REG-001")
    bad = deepcopy(after); bad["nodes"]["b"]["label"] = "Extra"
    expect("preserve-out-of-scope", validate_preserve(edit, before, bad)[0], rule="REG-003")
    malformed = {"preserve": ["nodes.badindex"], "modify": []}
    expect("missing-preserve-path-clean-rejection", validate_preserve(malformed, {"nodes": []}, {"nodes": []})[0], rule="REG-002")
    return {"passed": not failures, "executable_regression_checks": checks, "cases": cases, "failures": failures}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("skill_root", type=Path)
    result = run_regressions(parser.parse_args().skill_root.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1

if __name__ == "__main__": raise SystemExit(main())
