"""Code maps via graphify (code-only: local tree-sitter parsing, no LLM, no tokens).

graphify writes to data/maps/<app>/graphify-out/ so the app folders are never touched.
GRAPHIFY_BACKEND=none is forced on every run, so graphify can't reach for an AI backend
(it can otherwise auto-detect API keys or the `claude` CLI to name clusters).
We read graph.json ourselves and turn it into areas + functions you can click through.
"""
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.join(HERE, "data", "maps")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CALLS = {"calls", "indirect_call", "method"}


def out_dir(app):
    return os.path.join(MAPS, app, "graphify-out")


def graphify_exe():
    """Path to the graphify CLI, or None. pip --user puts it in a Scripts folder that often isn't on PATH."""
    found = shutil.which("graphify")
    if found:
        return found
    ver = f"Python{sys.version_info[0]}{sys.version_info[1]}"
    for d in (os.path.join(os.path.dirname(sys.executable), "Scripts"),
              os.path.join(os.environ.get("APPDATA", ""), "Python", ver, "Scripts"),
              os.path.join(os.path.expanduser("~"), ".local", "bin")):
        for exe in ("graphify.exe", "graphify"):
            if os.path.isfile(os.path.join(d, exe)):
                return os.path.join(d, exe)
    return None


def build(app, app_dir):
    exe = graphify_exe()
    if not exe:
        return {"ok": False, "log": "graphify isn't installed. Run:  python -m pip install --user graphifyy   (double y)"}
    env = {**os.environ, "GRAPHIFY_BACKEND": "none", "PYTHONIOENCODING": "utf-8"}
    dest = os.path.join(MAPS, app)
    os.makedirs(dest, exist_ok=True)
    log = []
    for args in (["extract", app_dir, "--code-only", "--out", dest],
                 ["cluster-only", dest, "--no-label"]):
        r = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, creationflags=NO_WINDOW, timeout=600)
        log.append((r.stdout + r.stderr)[-1500:])
        if r.returncode != 0:
            return {"ok": False, "log": "\n".join(log)}
    return {"ok": True, "log": "\n".join(log)}


def load(app):
    path = os.path.join(out_dir(app), "graph.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        g = json.load(f)
    nodes = {n["id"]: n for n in g["nodes"]}
    doc, out, inc, parent = {}, defaultdict(list), defaultdict(list), {}
    for l in g["links"]:
        s, t, rel = l["source"], l["target"], l.get("relation")
        if rel == "rationale_for":
            doc[t] = nodes.get(s, {}).get("label", "")
        elif rel in CALLS:
            out[s].append(t)
            inc[t].append(s)
        elif rel == "contains":
            parent[t] = s
    code = {i: n for i, n in nodes.items() if n.get("file_type") == "code" and n.get("source_file")}
    deg = {i: len(set(out[i])) + len(set(inc[i])) for i in code}

    fns = {}
    for i, n in code.items():
        if i in doc or deg[i] or n.get("_callable"):
            fns[i] = {"id": i, "label": n["label"], "file": n["source_file"], "line": n.get("source_location", ""),
                      "area": n.get("community"), "doc": doc.get(i, ""), "deg": deg[i],
                      "calls": sorted({t for t in out[i] if t in code} - {i}, key=lambda x: -deg.get(x, 0)),
                      "callers": sorted({s for s in inc[i] if s in code} - {i}, key=lambda x: -deg.get(x, 0)),
                      "kind": "class" if n.get("_callable_class") else "file" if "." in n["label"] and not n["label"].endswith(")") else "fn"}

    areas = defaultdict(list)
    for f in fns.values():
        if f["kind"] != "file":
            areas[f["area"]].append(f)
    area_list = []
    for cid, members in areas.items():
        members.sort(key=lambda f: -f["deg"])
        files = Counter(f["file"] for f in members)
        top = members[0]
        area_list.append({"id": cid, "name": f"{files.most_common(1)[0][0]} · {top['label']}",
                          "files": [f for f, _ in files.most_common()], "size": len(members),
                          "members": [f["id"] for f in members],
                          "doc": next((f["doc"] for f in members if f["doc"]), "")})
    area_list.sort(key=lambda a: -a["size"])
    files_doc = {n["source_file"]: doc[i] for i, n in code.items() if i in doc and n["label"] == os.path.basename(n["source_file"])}
    hubs = sorted((f for f in fns.values() if f["kind"] != "file"), key=lambda f: -f["deg"])[:8]
    return {"areas": area_list, "fns": fns, "hubs": [h["id"] for h in hubs], "filesDoc": files_doc,
            "stats": {"nodes": len(g["nodes"]), "edges": len(g["links"]), "files": len({n["source_file"] for n in code.values()})},
            "builtAt": os.path.getmtime(path), "commit": g.get("built_at_commit", "")}
