
from __future__ import annotations
import json, io, zipfile
from typing import Any, Dict, List, Tuple

def _to_pascal(name: str) -> str:
    parts = [p for p in str(name).replace('-', '_').split('_') if p]
    return "_".join(p[:1].upper() + p[1:] for p in parts)

def _normalize_child_basename(resource: str, child_key: str) -> str:
    prefix = (resource + "_").lower()
    n = str(child_key)
    if n.lower().startswith(prefix):
        n = n[len(prefix):]
    return f"{resource}_{_to_pascal(n)}"

def _normalize_child_filename(resource: str, child_key: str) -> str:
    return f"Output/{resource}/{_normalize_child_basename(resource, child_key)}.json"

def split_parent_children(obj: Dict[str, Any]) -> Dict[str, Any]:
    res_type = obj.get("resourceType", "Unknown")
    id_val = obj.get("id"); mv = None
    meta = obj.get("meta")
    if isinstance(meta, dict):
        mv = meta.get("versionid") or meta.get("versionId")
    parent_row: Dict[str, Any] = {"resourceType": res_type}
    if id_val is not None: parent_row["id"] = id_val
    if mv is not None: parent_row["meta_versionid"] = mv
    parent_row["__snapshot"] = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    children: Dict[str, List[Dict[str, Any]]] = {}
    def ensure(name: str) -> List[Dict[str, Any]]:
        if name not in children: children[name] = []
        return children[name]
    def flatten(node: Any, prefix: tuple, out: Dict[str, Any]):
        if isinstance(node, dict):
            for k, v in node.items():
                keyp = prefix + (k,)
                if isinstance(v, list):
                    out["_".join(keyp)] = json.dumps(v, ensure_ascii=False)
                elif isinstance(v, dict):
                    flatten(v, keyp, out)
                else:
                    out["_".join(keyp)] = v
        return out
    def emit(items, path: tuple):
        table = "_".join(path); rows = ensure(table)
        for it in items:
            row: Dict[str, Any] = {}
            if id_val is not None: row["id"] = id_val
            if mv is not None: row["meta_versionid"] = mv
            if isinstance(it, dict):
                for k, v in it.items():
                    keyp = path + (k,)
                    if isinstance(v, list):
                        row["_".join(keyp)] = json.dumps(v, ensure_ascii=False)
                    elif isinstance(v, dict):
                        row.update(flatten(v, keyp, {}))
                    else:
                        row["_".join(keyp)] = v
            else:
                row["_".join(path)] = it
            rows.append(row)
    def walk(node, path: tuple = ()):
        if isinstance(node, dict):
            for k, v in node.items():
                keyp = path + (k,)
                if isinstance(v, list): emit(v, keyp)
                elif isinstance(v, dict): parent_row.update(flatten(v, keyp, {}))
                else: parent_row["_".join(keyp)] = v
        elif isinstance(node, list): emit(node, path)
    payload = {k: v for k, v in obj.items() if k not in ("id","versionid","resourceType","meta")}
    walk(payload)
    return {"parent": [parent_row], "children": children}

def collect_resources_except_bundle(obj: Any, out: List[Dict[str, Any]] | None = None) -> List[Dict[str, Any]]:
    if out is None: out = []
    if isinstance(obj, dict):
        if obj.get("resourceType") == "Bundle":
            for e in obj.get("entry", []):
                if isinstance(e, dict) and "resource" in e:
                    collect_resources_except_bundle(e["resource"], out)
        elif "resourceType" in obj and obj.get("resourceType") != "Bundle":
            out.append(obj)
        else:
            for v in obj.values():
                collect_resources_except_bundle(v, out)
    elif isinstance(obj, list):
        for item in obj: collect_resources_except_bundle(item, out)
    return out

def group_by_resource_type(objs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for r in objs:
        rt = r.get("resourceType", "Unknown")
        res = split_parent_children(r)
        g = grouped.setdefault(rt, {"parent": [], "children": {}})
        g["parent"].extend(res.get("parent") or [])
        for k, rows in (res.get("children") or {}).items():
            g["children"].setdefault(k, []).extend(rows)
    return grouped

def ui_mapping(by_type: Dict[str, Dict[str, Any]]):
    out = {}
    for rt, data in by_type.items():
        child_keys = list((data.get("children") or {}).keys())
        child_files = [_normalize_child_filename(rt, k) for k in child_keys]
        child_tables = [_normalize_child_basename(rt, k) for k in child_keys]
        out[rt] = {
            "parent_label": f"Output/{rt}/parent.json",
            "children_files": child_files,
            "child_tables": child_tables,
        }
    return out

def build_zip_bytes(by_type: Dict[str, Dict[str, Any]]):
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        for rt, data in by_type.items():
            zf.writestr(f"Output/{rt}/parent.json", json.dumps(data.get("parent") or [], ensure_ascii=False, indent=2))
            for child_key, rows in (data.get("children") or {}).items():
                fname = f"Output/{rt}/{_normalize_child_basename(rt, child_key)}.json"
                zf.writestr(fname, json.dumps(rows or [], ensure_ascii=False, indent=2))
    mem.seek(0)
    return mem.getvalue()
