#!/usr/bin/env python3
"""Validate a YAML or JSON artifact against a skill contract."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import yaml
try:
    import jsonschema
except ImportError as exc:
    raise SystemExit("Install jsonschema to validate contracts") from exc

def load(path: Path):
    text=path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix.lower()==".json" else yaml.safe_load(text)

def validate_artifact(schema, artifact):
    """Schema plus cross-value checks that JSON Schema cannot express."""
    errors=[f"{'.'.join(str(x) for x in e.absolute_path) or '$'}: {e.message}" for e in jsonschema.Draft202012Validator(schema).iter_errors(artifact)]
    if errors or schema.get("$id")!="infographic-designer/qa-report.schema.json": return errors
    blockers={f["rule_id"] for f in artifact["findings"] if f["severity"]=="blocker"}
    hard={f["rule_id"] for f in artifact["hard_fails"]}
    if blockers!=hard: errors.append("hard_fails: rule IDs must match blocker findings")
    if len(hard)!=len(artifact["hard_fails"]): errors.append("hard_fails: duplicate rule IDs")
    ids=[f["id"] for f in artifact["findings"]]
    if len(set(ids))!=len(ids): errors.append("findings: duplicate IDs")
    scores=artifact.get("scores",{})
    if ("total" in scores)!=("threshold" in scores): errors.append("scores: total and threshold must be supplied together")
    if artifact["verdict"]=="pass" and scores.get("total",100)<scores.get("threshold",0): errors.append("scores: passing verdict cannot be below threshold")
    return errors

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("contract", type=Path)
    p.add_argument("artifact", type=Path)
    args=p.parse_args()
    schema=load(args.contract); artifact=load(args.artifact)
    errors=validate_artifact(schema,artifact)
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 1
    print(json.dumps({"valid":True,"contract":args.contract.name,"artifact":str(args.artifact)},ensure_ascii=False))
    return 0
if __name__=="__main__": raise SystemExit(main())
