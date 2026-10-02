#!/usr/bin/env python3
"""Validate explicit runtime distribution or the complete development library."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import jsonschema
import yaml

BASE_REQUIRED = {
    "SKILL.md", "manifest.yaml", "requirements.txt", "routes.yaml", "workflow.yaml",
    "rules/registry.yaml", "references/qa/gates.yaml",
    "references/production/renderer-router.yaml",
    "tests/routing-cases.yaml", "tests/regression-cases.yaml",
    "scripts/validate_package.py", "scripts/build_runtime.py",
}
CATEGORIES = ("skeletons", "style_profiles", "contracts", "playbooks", "curated_prompts", "public_routes", "renderer_routes")
PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of accepting an overwritten declaration."""


def unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML mapping key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def read(path: Path):
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else yaml.load(text, Loader=UniqueKeyLoader)


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError(f"invalid relative package path: {value!r}")
    parts = PurePosixPath(value)
    if parts.is_absolute() or any(part in {"..", "."} for part in value.split("/")):
        raise ValueError(f"invalid relative package path: {value!r}")
    if "*" in value or "?" in value or value.endswith("/"):
        raise ValueError(f"allowlist requires a literal file path: {value!r}")
    return value


def load_manifest(root: Path) -> dict:
    manifest = read(root / "manifest.yaml")
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a mapping")
    for key in ("schema_version", "version", "skill", "distribution", "distributions", "examples", "tests", "source_map"):
        if key not in manifest:
            raise ValueError(f"manifest missing required key: {key}")
    if manifest["distribution"] not in {"development", "runtime"}:
        raise ValueError("manifest distribution must be development or runtime")
    for key in CATEGORIES:
        values = manifest.get(key)
        if not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values):
            raise ValueError(f"manifest {key} must be a non-empty list of strings")
        if len(values) != len(set(values)):
            raise ValueError(f"manifest {key} contains duplicate IDs")
    if not isinstance(manifest["examples"], dict) or not isinstance(manifest["tests"], dict):
        raise ValueError("manifest examples/tests must be mappings")
    distribution = manifest["distributions"]
    if not isinstance(distribution, dict) or set(distribution) != {"development", "runtime"}:
        raise ValueError("manifest must declare development and runtime profiles")
    for name in distribution:
        if not isinstance(distribution[name], dict):
            raise ValueError(f"manifest {name} profile must be a mapping")
    files = distribution["runtime"].get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("runtime files must be a non-empty explicit allowlist")
    files = [safe_relative(value) for value in files]
    if len(set(files)) != len(files) or len(set(v.casefold() for v in files)) != len(files):
        raise ValueError("runtime allowlist contains duplicate or case-colliding paths")
    for value in files:
        if value.endswith(".generated.md") or value.startswith(("source-map/", "evals/", "inbox-provenance-")) or value == "scripts/extract_sections.py" or "__pycache__" in PurePosixPath(value).parts:
            raise ValueError(f"non-runtime file in runtime allowlist: {value}")
    receipt = safe_relative(distribution["runtime"].get("receipt"))
    if receipt in files:
        raise ValueError("runtime receipt must not include its own hash")
    dev = distribution["development"]
    if not isinstance(dev.get("required_files"), list) or not dev["required_files"]:
        raise ValueError("development required_files must be a non-empty list")
    for value in dev["required_files"]:
        safe_relative(value)
    safe_relative(dev.get("extraction_scope"))
    safe_relative(dev.get("provenance_root"))
    return manifest


def promised_files(manifest: dict) -> set[str]:
    required = set(BASE_REQUIRED)
    for key, prefix, suffix in (
        ("skeletons", "references/grammar/skeletons/", ".yaml"),
        ("style_profiles", "references/style/profiles/", ".yaml"),
        ("contracts", "contracts/", ".schema.json"),
        ("playbooks", "playbooks/", ".md"),
        ("curated_prompts", "prompts/", ".md"),
    ):
        required.update(safe_relative(prefix + name + suffix) for name in manifest[key])
    required.update(f"templates/{name}.yaml" for name in manifest["contracts"])
    example_files = {
        "create": ["production-contract.yaml", "content-packet.yaml", "semantic-spec.yaml", "visual-spec.yaml", "wireframe.yaml", "qa-report.yaml"],
        "audit": ["semantic-spec.yaml", "wireframe.yaml", "qa-report.yaml"],
        "repair": ["semantic-spec.yaml", "wireframe-before.yaml", "wireframe-after.yaml", "qa-report.yaml", "edit-contract.yaml", "preserve-before.json", "preserve-after.json"],
    }
    for route, names in manifest["examples"].items():
        if route not in example_files or not isinstance(names, list) or not names:
            raise ValueError(f"invalid examples declaration: {route}")
        for name in names:
            required.update(safe_relative(f"examples/{route}/{name}/{filename}") for filename in example_files[route])
    return required


def validate_hashes(root: Path, manifest: dict, files: set[str], errors: list[str]) -> None:
    receipt_path = root / manifest["distributions"]["runtime"]["receipt"]
    if not receipt_path.is_file():
        errors.append(f"missing required hash receipt: {receipt_path.name}")
        return
    try:
        receipt = read(receipt_path)
        if not isinstance(receipt, dict) or receipt.get("algorithm") != "sha256" or receipt.get("skill") != manifest["skill"] or receipt.get("version") != manifest["version"] or receipt.get("distribution") != "runtime":
            raise ValueError("invalid runtime hash receipt metadata")
        hashes = receipt.get("files")
        if not isinstance(hashes, dict) or set(hashes) != files:
            raise ValueError("hash receipt paths do not match runtime allowlist")
        for relative, digest in hashes.items():
            safe_relative(relative)
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                errors.append(f"invalid SHA256 digest: {relative}")
            elif (root / relative).is_file() and hashlib.sha256((root / relative).read_bytes()).hexdigest() != digest:
                errors.append(f"hash mismatch: {relative}")
    except (ValueError, OSError, TypeError) as exc:
        errors.append(f"invalid hash receipt: {exc}")


def validate_runtime_content(root: Path, manifest: dict, files: set[str], errors: list[str]) -> None:
    parsed = {}
    for relative in sorted(files):
        path = root / relative
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(root.resolve()):
            errors.append(f"package path resolves outside root: {relative}")
            continue
        try:
            if path.suffix in {".yaml", ".yml", ".json"}:
                parsed[relative] = read(path)
                if relative.endswith(".schema.json"):
                    jsonschema.Draft202012Validator.check_schema(parsed[relative])
            if path.suffix in {".yaml", ".yml", ".json", ".md", ".py"}:
                content = path.read_text(encoding="utf-8")
                if PATH_PATTERN.search(content):
                    errors.append(f"absolute path leak: {relative}")
                if path.suffix == ".md":
                    if relative == "SKILL.md" and len(content.splitlines()) > 300:
                        errors.append("SKILL.md exceeds the 300-line entrypoint limit")
                    for target in re.findall(r"\]\(([^)]+)\)", content):
                        target = target.strip().strip("<>")
                        if "://" in target or target.startswith(("#", "mailto:")):
                            continue
                        clean = unquote(target.split("#", 1)[0])
                        linked = (path.parent / clean).resolve()
                        if clean and (not linked.is_relative_to(root.resolve()) or not linked.exists()):
                            errors.append(f"broken or external local Markdown link in {relative}: {target}")
                        elif clean and linked.is_file() and linked.relative_to(root.resolve()).as_posix() not in files:
                            errors.append(f"Markdown link outside runtime allowlist in {relative}: {target}")
        except (ValueError, TypeError, AttributeError, OSError, jsonschema.SchemaError, yaml.YAMLError) as exc:
            errors.append(f"parse error {relative}: {exc}")

    def data(relative: str) -> dict:
        value = parsed.get(relative)
        if not isinstance(value, dict):
            raise ValueError(f"required structured document is not a mapping: {relative}")
        return value

    try:
        skill = (root / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(skill.split("---", 2)[1]) if skill.startswith("---") else {}
        if not isinstance(frontmatter, dict) or frontmatter.get("version") != manifest["version"] or frontmatter.get("name") != manifest["skill"]:
            errors.append("SKILL frontmatter name/version does not match manifest")
        if set(data("routes.yaml").get("public_routes", {})) != set(manifest["public_routes"]):
            errors.append("public route IDs do not match manifest")
        if set(data("references/production/renderer-router.yaml").get("routes", {})) != set(manifest["renderer_routes"]):
            errors.append("renderer route IDs do not match manifest")
        routing = data("tests/routing-cases.yaml").get("cases", [])
        if len(routing) < manifest["tests"]["minimum_routing_cases"]:
            errors.append(f"routing cases below minimum: {len(routing)}")
        regressions = data("tests/regression-cases.yaml").get("cases", [])
        registry = data("rules/registry.yaml").get("rules", {})
        blockers = {key for key, rule in registry.items() if rule.get("severity") == "blocker"}
        covered = {key for case in regressions for key in case.get("covers", [])}
        if blockers - covered:
            errors.append(f"blockers without regression fixtures: {sorted(blockers - covered)}")
        for case in regressions:
            if len(case.get("assertions", [])) < manifest["tests"]["minimum_assertions_per_regression"]:
                errors.append(f"regression has too few assertions: {case.get('id')}")
            if set(case.get("covers", [])) - set(registry):
                errors.append(f"regression references unknown rule: {case.get('id')}")
        gates = data("references/qa/gates.yaml").get("gates", {})
        for gate_id, gate in gates.items():
            expected = {key for key, rule in registry.items() if rule.get("severity") == "blocker" and gate_id in rule.get("gates", [])}
            if set(gate.get("blockers", [])) != expected:
                errors.append(f"gate blocker parity mismatch {gate_id}")
        for key, rule in registry.items():
            if set(rule.get("gates", [])) - set(gates):
                errors.append(f"rule references unknown gate: {key}")
        assigned = []
        for relative in sorted(files):
            if relative.startswith("rules/") and relative != "rules/registry.yaml" and relative.endswith(".yaml"):
                group = data(relative)
                if group.get("owner") != "rules/registry.yaml" or set(group.get("rule_ids", [])) - set(registry):
                    errors.append(f"rule group owner or ID mismatch: {relative}")
                if len(group.get("rule_ids", [])) != len(set(group.get("rule_ids", []))):
                    errors.append(f"duplicate IDs within rule group: {relative}")
                assigned.extend(group.get("rule_ids", []))
        if set(assigned) != set(registry):
            errors.append("rule group references must cover the registry")
        for name in manifest["contracts"]:
            validator = jsonschema.Draft202012Validator(data(f"contracts/{name}.schema.json"))
            template_errors = list(validator.iter_errors(data(f"templates/{name}.yaml")))
            if template_errors:
                errors.append(f"invalid template {name}: {template_errors[0].message}")
        qa_schema = data("contracts/qa-report.schema.json")
        invalid_qa = {"schema_version": "1.0", "gate_id": "G1_SEMANTIC", "verdict": "pass", "confidence": 1.0, "hard_fails": [{"rule_id": "SEM-001", "title": "unsupported"}], "findings": [], "unknowns": [], "scores": {"total": 100}, "release_recommendation": "release"}
        if not list(jsonschema.Draft202012Validator(qa_schema).iter_errors(invalid_qa)):
            errors.append("qa-report schema accepts pass with blocker and score")
        invalid_content = dict(data("templates/content-packet.yaml"))
        invalid_content["data_values"] = [{"id": "bad", "value": {"arbitrary": True}, "unit": "x", "source_refs": [{"source_id": "s", "locator": "l"}]}]
        if not list(jsonschema.Draft202012Validator(data("contracts/content-packet.schema.json")).iter_errors(invalid_content)):
            errors.append("content-packet schema accepts object-valued data")
        invalid_visual = dict(data("templates/visual-spec.yaml"))
        invalid_visual["entity_encoding"] = {"node": {"arbitrary": True}}
        if not list(jsonschema.Draft202012Validator(data("contracts/visual-spec.schema.json")).iter_errors(invalid_visual)):
            errors.append("visual-spec schema accepts untyped nested encoding")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, OSError, jsonschema.SchemaError, yaml.YAMLError) as exc:
        errors.append(f"runtime consistency check could not complete: {exc}")


def validate_development(root: Path, manifest: dict, errors: list[str]) -> None:
    profile = manifest["distributions"]["development"]
    for relative in profile["required_files"]:
        if not (root / relative).is_file():
            errors.append(f"missing development required file: {relative}")
    if any(not (root / value).is_file() for value in profile["required_files"]):
        return
    try:
        extraction = read(root / "source-map/extract-map.yaml")
        ownership = read(root / "source-map/section-ownership.yaml")
        scope = read(root / profile["extraction_scope"])
        snapshot = read(root / "source-map/atlas-snapshot.json")
        sections = extraction.get("sections", [])
        if len(sections) != manifest["source_map"]["expected_sections"]:
            errors.append(f"source-map section count mismatch: {len(sections)}")
        if len({section["id"] for section in sections}) != len(sections):
            errors.append("duplicate source-map section IDs")
        canonical = extraction.get("canonical_owners", {})
        assigned = defaultdict(set)
        for section in sections:
            owner, target = section["transfer"]["owner"], safe_relative(section["transfer"]["target"].rstrip("/"))
            assigned[target].add(owner)
            if owner not in canonical or canonical[owner].rstrip("/") != target:
                errors.append(f"source-map owner path mismatch: {section['id']}")
        for target, owners in assigned.items():
            if len(owners) > 1:
                errors.append(f"multiple extraction owners for target: {target}")
        for target in ownership.get("owners", {}).values():
            if not (root / safe_relative(target.rstrip("/"))).exists():
                errors.append(f"missing curated ownership target: {target}")
        for target in scope.get("required_curated_targets", []):
            if not (root / safe_relative(target)).is_file():
                errors.append(f"missing extraction baseline curated target: {target}")
        sources = snapshot.get("sources", {})
        if snapshot.get("algorithm") != "sha256" or len(sources) != manifest["source_map"]["expected_sources"]:
            errors.append("Atlas snapshot algorithm/source count mismatch")
        if set(sources) != {section["source"] for section in sections}:
            errors.append("Atlas snapshot sources do not match extraction map")
        # Historical extraction baseline is checked against immutable local provenance,
        # never against curated 0.2.0 prose and never by writing the external Atlas.
        spec = importlib.util.spec_from_file_location("infographic_extraction", root / "scripts/extract_sections.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        atlas_root = root / profile["provenance_root"]
        source_text = {}
        for source, expected_hash in sources.items():
            path = atlas_root / Path(source).name
            if not path.is_file():
                errors.append(f"missing provenance source: {source}")
                continue
            content = path.read_text(encoding="utf-8")
            source_text[source] = content
            if module.sha256_text(content) != expected_hash:
                errors.append(f"provenance source hash mismatch: {source}")
        grouped = defaultdict(list)
        for section in sections:
            content = source_text.get(section["source"])
            if content is None:
                continue
            found = module.extract_by_marker(content, section["id"])
            if found is None:
                selector = section.get("selector", {})
                found = module.extract_by_heading(content, selector["fallback_heading"], selector.get("occurrence"))
            if module.selected(section, scope):
                body, line = found
                target = section["transfer"]["target"]
                if not target.endswith(".generated.md"):
                    errors.append(f"copy/compile baseline target is not generated Markdown: {target}")
                grouped[target].append((int(section["transfer"].get("order", 0)), module.render_generated(section, body, line)))
        for target, chunks in grouped.items():
            expected = "\n\n".join(chunk.rstrip() for _, chunk in sorted(chunks, key=lambda item: item[0])) + "\n"
            path = root / safe_relative(target)
            if not path.is_file():
                errors.append(f"missing selected generated target: {target}")
            elif path.read_text(encoding="utf-8") != expected:
                errors.append(f"stale generated extraction target: {target}")
    except (ValueError, OSError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        errors.append(f"development provenance check could not complete: {exc}")


def validate_package(root: Path, distribution: str | None = None, verify_hashes: bool = False) -> dict:
    root = root.resolve()
    errors = []
    try:
        manifest = load_manifest(root)
        selected = distribution or manifest["distribution"]
        if selected != manifest["distribution"]:
            raise ValueError(f"requested profile {selected} does not match manifest distribution {manifest['distribution']}")
        files = set(manifest["distributions"]["runtime"]["files"])
        promised = promised_files(manifest)
        if promised - files:
            errors.append(f"promised files absent from runtime allowlist: {sorted(promised - files)}")
        for relative in sorted(files):
            if not (root / relative).is_file():
                errors.append(f"missing required file: {relative}")
        validate_runtime_content(root, manifest, files, errors)
        if selected == "runtime":
            receipt = manifest["distributions"]["runtime"]["receipt"]
            actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.relative_to(root).parts}
            if actual - files - {receipt}:
                errors.append(f"unexpected files in runtime distribution: {sorted(actual - files - {receipt})}")
            if verify_hashes:
                validate_hashes(root, manifest, files, errors)
        else:
            validate_development(root, manifest, errors)
        return {"valid": not errors, "distribution": selected, "version": manifest["version"], "required_runtime_files": len(files), "errors": errors}
    except (ValueError, OSError, TypeError, KeyError, yaml.YAMLError) as exc:
        return {"valid": False, "distribution": distribution, "errors": [f"invalid package manifest: {exc}"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("skill_root", type=Path)
    parser.add_argument("--distribution", choices=["development", "runtime"])
    parser.add_argument("--verify-hashes", action="store_true", help="Verify optional export receipt")
    args = parser.parse_args()
    result = validate_package(args.skill_root, args.distribution, verify_hashes=args.verify_hashes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
