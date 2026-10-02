#!/usr/bin/env python3
"""Export a reviewed explicit allowlist into a new staging directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import yaml

from validate_package import load_manifest, validate_package


def build_runtime(source: Path, destination: Path, verify_hashes: bool = False) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("staging destination must be separate from the development root")
    if destination.exists():
        raise ValueError("staging destination already exists; choose a new versioned sibling")
    source_result = validate_package(source, "development")
    if not source_result["valid"]:
        raise ValueError("development validation failed: " + json.dumps(source_result["errors"], ensure_ascii=False))
    manifest = load_manifest(source)
    files = manifest["distributions"]["runtime"]["files"]
    destination.mkdir(parents=True, exist_ok=False)
    for relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    # The only export transform is the declared distribution marker.
    # Source manifest remains the development source of truth.
    manifest["distribution"] = "runtime"
    (destination / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n"
    )
    if verify_hashes:
        hashes = {relative: hashlib.sha256((destination / relative).read_bytes()).hexdigest() for relative in sorted(files)}
        receipt = {
            "schema_version": "1.0", "skill": manifest["skill"], "version": manifest["version"],
            "distribution": "runtime", "algorithm": "sha256", "files": hashes,
        }
        (destination / manifest["distributions"]["runtime"]["receipt"]).write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    runtime_result = validate_package(destination, "runtime", verify_hashes=verify_hashes)
    if not runtime_result["valid"]:
        raise ValueError("staged runtime validation failed; candidate retained for inspection: " + json.dumps(runtime_result["errors"], ensure_ascii=False))
    return {"built": True, "version": manifest["version"], "distribution": "runtime", "files": len(files), "hash_receipt": verify_hashes, "runtime_valid": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("development_root", type=Path)
    parser.add_argument("staging_destination", type=Path)
    parser.add_argument("--verify-hashes", action="store_true", help="Write and verify an optional SHA256 receipt")
    args = parser.parse_args()
    try:
        result = build_runtime(args.development_root, args.staging_destination, verify_hashes=args.verify_hashes)
    except (ValueError, OSError) as exc:
        print(json.dumps({"built": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
