#!/usr/bin/env python3
"""Verify preserved paths and allow-listed changes between structured artifacts."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import yaml

def load(path:Path):
    text=path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix.lower()==".json" else yaml.safe_load(text)

def get_path(value,path):
    cur=value
    for part in path.split("."):
        cur=cur[int(part)] if isinstance(cur,list) else cur[part]
    return cur

def diff_paths(a,b,prefix=""):
    if type(a)!=type(b): return [prefix or "$" ]
    if isinstance(a,dict):
        out=[]
        for key in sorted(set(a)|set(b)):
            path=f"{prefix}.{key}" if prefix else key
            if key not in a or key not in b: out.append(path)
            else: out.extend(diff_paths(a[key],b[key],path))
        return out
    if isinstance(a,list):
        if len(a)!=len(b): return [prefix]
        out=[]
        for i,(x,y) in enumerate(zip(a,b)): out.extend(diff_paths(x,y,f"{prefix}.{i}"))
        return out
    return [] if a==b else [prefix]

def validate_preserve(c,before,after):
    errors=[]
    for path in c.get("preserve",[]):
        try:
            if get_path(before,path)!=get_path(after,path): errors.append(f"REG-001 preserved path changed: {path}")
        except (KeyError,IndexError,TypeError,ValueError): errors.append(f"REG-002 preserved path missing: {path}")
    allowed=[f"{m['target_id']}.{m['property']}" for m in c.get("modify",[])]
    changed=diff_paths(before,after)
    for path in changed:
        if not any(path==a or path.startswith(a+".") for a in allowed) and path not in c.get("preserve",[]):
            errors.append(f"REG-003 out-of-scope change: {path}")
    return errors,changed

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("contract",type=Path); p.add_argument("before",type=Path); p.add_argument("after",type=Path); args=p.parse_args()
    errors,changed=validate_preserve(load(args.contract),load(args.before),load(args.after))
    if errors: print("\n".join(f"ERROR {e}" for e in errors)); return 1
    print(json.dumps({"valid":True,"changed":changed},ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
