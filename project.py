"""Whole-project views for the Map, all static (no AI): routes table, hotspots, possibly-unused code,
tests, guided tours, and per-function git history.

Ideas borrowed from other tools:
  - hotspots = change frequency x complexity (CodeScene): code that changes often AND is hard to
    follow is where bugs and effort pile up
  - tours = step-by-step walkthroughs (CodeTour), generated here from the real call graph
  - history / who knows it = git log -L + git blame on just this function's lines
"""
import os
import re
import subprocess
from collections import Counter

import explain

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_cache = {}
ENTRY_NAMES = {"main", "__init__", "__main__", "do_GET", "do_POST", "do_PUT", "do_DELETE", "run", "setup", "create_app"}


def _git(path, *args, timeout=30):
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _details(app, app_dir, m):
    """Static detail for every function (cached per graph build)."""
    out = {}
    for fid in m["fns"]:
        try:
            d = explain.detail(app, app_dir, m, fid)
        except Exception:
            d = None
        if d and "error" not in d:
            out[fid] = d
    return out


def overview(app, app_dir, m):
    head = (_git(app_dir, "rev-parse", "HEAD") or "").strip()
    key = (app, m["builtAt"], head)
    if _cache.get(app, {}).get("key") == key:
        return _cache[app]["data"]
    det = _details(app, app_dir, m)
    fns = m["fns"]

    # ---- routes: every address the app answers, who calls it from the page, what data it touches
    routes = []
    for fid, d in det.items():
        for r in d.get("routes") or []:
            verb, path = r.split(" ", 1)
            seen, stack, reads, writes = set(), [fid], set(), set()
            while stack:  # the handler plus everything it calls (3 levels) -> tables touched
                cur = stack.pop()
                if cur in seen or len(seen) > 40:
                    continue
                seen.add(cur)
                dd = det.get(cur, {})
                reads |= set(dd.get("data", {}).get("reads", []))
                writes |= set(dd.get("data", {}).get("writes", []))
                stack += [c for c in fns[cur]["calls"] if c in fns][:12]
            pagers = [g for g, dd in det.items() if dd.get("lang") == "javascript"
                      and any(b["fn"] == fid for b in dd.get("backend", []))]
            routes.append({"verb": verb, "path": path, "fn": fid, "reads": sorted(reads), "writes": sorted(writes),
                           "inputs": det[fid].get("request", {}), "page": pagers})
    uniq = {}
    for r in routes:  # one row per (verb, path, handler); merge page callers
        k = (r["verb"], r["path"], r["fn"])
        if k in uniq:
            uniq[k]["page"] = sorted(set(uniq[k]["page"]) | set(r["page"]))
        else:
            uniq[k] = r
    routes = sorted(uniq.values(), key=lambda r: (r["path"], r["verb"]))

    # ---- hotspots: commits touching each file (last 500) x the function's complexity
    churn = Counter()
    log = _git(app_dir, "log", "-n", "500", "--format=", "--name-only")
    for line in (log or "").splitlines():
        if line.strip():
            churn[line.strip().replace("\\", "/")] += 1
    hot = []
    for fid, d in det.items():
        if fns[fid]["kind"] in ("class", "file"):  # a class's score is the sum of its methods; rank the methods
            continue
        c = d.get("complexity") or {}
        ch = churn.get(fns[fid]["file"].replace("\\", "/"), 0)
        if c.get("score"):
            hot.append({"fn": fid, "score": c["score"] * max(ch, 1), "changes": ch, "complexity": c["score"],
                        "lines": c.get("lines", 0), "label": c.get("label", "")})
    hot.sort(key=lambda h: -h["score"])
    hot = hot[:15]
    top = hot[0]["score"] if hot else 1
    for h in hot:  # simple code is never a hotspot, however often it changes
        h["level"] = "low" if h["complexity"] < 6 else "high" if h["score"] >= top * 0.5 else "medium" if h["score"] >= top * 0.2 else "low"
    history_note = "" if max(churn.values(), default=0) >= 5 else \
        "Little git history yet, so this ranks mostly by complexity. It gets sharper as commits pile up."

    # ---- possibly unused: nothing calls it, it isn't a route or an entry point, and its name
    #      appears nowhere else in the project (so no event handler / template refers to it either)
    corpus = []
    for root, dirs, files in os.walk(app_dir):
        dirs[:] = [x for x in dirs if x not in (".git", "node_modules", "__pycache__", "dist", "build", "data", ".venv", "venv")]
        for f in files:
            if f.endswith((".py", ".pyw", ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".jinja", ".j2")):
                try:
                    with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                        corpus.append(fh.read())
                except OSError:
                    pass
    text = "\n".join(corpus)
    unused = []
    for fid, f in fns.items():
        name = explain._bare(f["label"])
        if f["callers"] or f["kind"] == "file" or name in ENTRY_NAMES or name.startswith("__") or not name.isidentifier():
            continue
        if det.get(fid, {}).get("routes"):
            continue
        if len(re.findall(r"\b" + re.escape(name) + r"\b", text)) <= 1:
            unused.append(fid)

    # ---- tests that mention each function
    tests = {}
    for root, dirs, files in os.walk(app_dir):
        dirs[:] = [x for x in dirs if x not in (".git", "node_modules", "__pycache__")]
        for f in files:
            if re.match(r"(test_.*\.py|.*_test\.py|.*\.(test|spec)\.[jt]sx?)$", f) or os.path.basename(root) in ("tests", "test"):
                p = os.path.join(root, f)
                try:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        body = fh.read()
                except OSError:
                    continue
                rel = os.path.relpath(p, app_dir).replace("\\", "/")
                for fid, fn in fns.items():
                    if re.search(r"\b" + re.escape(explain._bare(fn["label"])) + r"\b", body):
                        tests.setdefault(fid, []).append(rel)

    # ---- tours (CodeTour-style), built from the real call graph
    def walk(start, limit=9):
        order, seen = [], set()

        def dfs(fid, depth):
            if fid in seen or len(order) >= limit or depth > 3 or fid not in fns:
                return
            seen.add(fid)
            order.append(fid)
            for c in fns[fid]["calls"][:4]:
                dfs(c, depth + 1)
        dfs(start, 0)
        return order
    tours = []
    for r in routes[:25]:
        steps = (r["page"][:1] if r["page"] else []) + walk(r["fn"])
        tours.append({"title": f"Follow a request: {r['verb']} {r['path']}",
                      "why": "What happens, in order, when this address is asked for.", "steps": list(dict.fromkeys(steps))})
    entries = [fid for fid, f in fns.items() if not f["callers"] and f["calls"] and f["kind"] != "file"]
    entries.sort(key=lambda fid: -len(fns[fid]["calls"]))
    if entries:
        start_steps = list(dict.fromkeys(entries[:2] + m["hubs"][:6]))
        tours.insert(0, {"title": "Start here", "why": "Where things begin, then the pieces everything else leans on.", "steps": start_steps})

    data = {"routes": routes, "hotspots": hot, "historyNote": history_note, "unused": unused[:40], "tests": tests, "tours": tours,
            "fnStats": {fid: {"label": (d.get("complexity") or {}).get("label", ""), "score": (d.get("complexity") or {}).get("score", 0),
                              "todos": len(d.get("todos") or []), "tables": sorted(set(d.get("data", {}).get("reads", [])) | set(d.get("data", {}).get("writes", [])))}
                        for fid, d in det.items()}}
    _cache[app] = {"key": key, "data": data}
    return data


def history(app_dir, rel_file, start, end):
    """Commits that changed just these lines (git log -L), plus who wrote the lines as they are now (git blame)."""
    rel = rel_file.replace("\\", "/")
    log = _git(app_dir, "log", f"-L{start},{end}:{rel}", "--no-patch", "--format=%h%x1f%cs%x1f%an%x1f%s", timeout=60)
    commits = [dict(zip(("hash", "date", "author", "subject"), l.split("\x1f")))
               for l in (log or "").splitlines() if l.count("\x1f") == 3]
    blame = _git(app_dir, "blame", f"-L{start},{end}", "--line-porcelain", "--", rel)
    authors, newest = Counter(), ""
    for l in (blame or "").splitlines():
        if l.startswith("author "):
            authors[l[7:]] += 1
        elif l.startswith("author-time "):
            pass
    if commits:
        newest = commits[0]["date"]
    total = sum(authors.values()) or 1
    return {"commits": commits[:30], "count": len(commits), "first": commits[-1]["date"] if commits else "",
            "last": newest, "owners": [{"name": a, "share": round(100 * n / total)} for a, n in authors.most_common(5)],
            "tracked": log is not None}
