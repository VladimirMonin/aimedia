#!/usr/bin/env python3
"""Compare topology. Containment runs container -> child and renders as parent.

Other relationships render as edges. Omitted wireframe directed flags inherit
the semantic value; explicit flags must agree with it.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import yaml

def load(path:Path):
    text=path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix.lower()==".json" else yaml.safe_load(text)

def unique_ids(items):
    ids=[item.get("id") for item in items]
    return ids, len(ids)==len(set(ids)) and None not in ids

def check_parents(parents, nodes, location):
    errors=[]
    for child,parent in parents.items():
        if parent is not None and parent not in nodes:
            errors.append(f"STR-002 dangling {location} parent for {child}: {parent}")
        seen={child}; current=parent
        while current is not None and current in parents:
            if current in seen:
                errors.append(f"STR-002 {location} containment cycle involving {child}"); break
            seen.add(current); current=parents[current]
    return errors

def validate_graph(semantic, wireframe):
    errors=[]
    entities=semantic.get("entities",[]); relationships=semantic.get("relationships",[])
    nodes=wireframe.get("nodes",[]); edges=wireframe.get("edges",[])
    for name,items in [("semantic entity",entities),("semantic relationship",relationships),("wireframe node",nodes),("wireframe edge",edges)]:
        ids,ok=unique_ids(items)
        if not ok or any(not isinstance(i,str) or not i.strip() for i in ids):
            errors.append(f"STR-006 duplicate or missing {name} IDs")
    semantic_nodes={item.get("id"):item for item in entities}
    wire_nodes={item.get("id"):item for item in nodes}
    required={item.get("id") for item in entities if item.get("required",True)}
    missing=sorted(required-wire_nodes.keys(),key=str); extra=sorted(wire_nodes.keys()-semantic_nodes.keys(),key=str)
    if missing: errors.append(f"STR-003 missing required nodes: {missing}")
    if extra: errors.append(f"SEM-003 invented nodes: {extra}")
    parents={node_id:None for node_id in semantic_nodes}; expected_edges={}
    for entity in entities:
        if "parent" in entity: errors.append(f"STR-002 semantic entity.parent unsupported: {entity.get('id')}; use containment relationship")
    for edge in relationships:
        edge_id,source,target=edge.get("id"),edge.get("from"),edge.get("to")
        if source not in semantic_nodes or target not in semantic_nodes: errors.append(f"STR-003 dangling semantic relationship: {edge_id}")
        if edge.get("type")=="containment":
            if edge.get("directed") is not True: errors.append(f"STR-002 containment must be directed container to child: {edge_id}")
            if target in parents and parents[target] is not None: errors.append(f"STR-002 multiple containment parents for {target}")
            parents[target]=source
        else: expected_edges[edge_id]=edge
    errors.extend(check_parents(parents,semantic_nodes,"semantic"))
    observed_parents={node_id:node.get("parent") for node_id,node in wire_nodes.items()}
    errors.extend(check_parents(observed_parents,wire_nodes,"wireframe"))
    for node_id,node in wire_nodes.items():
        if node_id in semantic_nodes and node.get("parent")!=parents[node_id]:
            errors.append(f"STR-002 false containment for {node_id}: expected {parents[node_id]}, observed {node.get('parent')}")
    observed_edges={item.get("id"):item for item in edges}
    for edge_id,expected in expected_edges.items():
        observed=observed_edges.get(edge_id)
        if observed is None: errors.append(f"STR-001 missing edge: {edge_id}"); continue
        directed=expected.get("directed",True)
        endpoints=(expected.get("from"),expected.get("to")); actual=(observed.get("from"),observed.get("to"))
        matches=actual==endpoints or (not directed and actual==endpoints[::-1])
        if not matches or observed.get("type")!=expected.get("type") or observed.get("directed",directed)!=directed:
            errors.append(f"STR-001 changed edge direction, endpoints or type: {edge_id}")
        if (directed or expected.get("critical") or observed.get("critical")) and not str(observed.get("label") or "").strip():
            errors.append(f"STR-004 unlabeled directed or critical edge: {edge_id}")
        if expected.get("label") is not None and observed.get("label")!=expected["label"]: errors.append(f"STR-004 changed semantic edge label: {edge_id}")
    for edge in edges:
        if edge.get("id") not in expected_edges: errors.append(f"STR-001 extra edge: {edge.get('id')}")
        if edge.get("from") not in wire_nodes or edge.get("to") not in wire_nodes: errors.append(f"STR-003 dangling edge: {edge.get('id')}")
    return errors

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("semantic_spec",type=Path); p.add_argument("wireframe",type=Path); args=p.parse_args()
    semantic=load(args.semantic_spec); wireframe=load(args.wireframe)
    errors=validate_graph(semantic,wireframe)
    if errors:
        print("\n".join(f"ERROR {e}" for e in errors)); return 1
    print(json.dumps({"valid":True,"nodes":len(wireframe.get("nodes",[])),"edges":len(wireframe.get("edges",[]))},ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
