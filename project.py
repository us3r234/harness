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

    # ---- what the page does: which buttons/forms/keys start which JS function
    by_bare = {}
    for fid, f in fns.items():
        if f["file"].endswith((".js", ".ts", ".jsx", ".tsx", ".html", ".htm")):
            by_bare.setdefault(explain._bare(f["label"]), fid)
    triggers = _scan_triggers(app_dir, by_bare)

    # ---- click paths: element -> JS -> (web request) -> handler -> ... -> tables
    handler_of_page = {}
    for r in routes:
        for pg in r["page"]:
            handler_of_page.setdefault(pg, []).append(r)
    clickpaths = []
    for t in triggers:
        seen, q, hit = {t["fn"]}, [(t["fn"], [t["fn"]])], []
        while q and len(hit) < 3:
            cur, path = q.pop(0)
            for r in handler_of_page.get(cur, []):
                hit.append({"via": path, "route": r})
            if len(path) < 5:
                for c in fns.get(cur, {}).get("calls", []):
                    if c not in seen and c in fns:
                        seen.add(c)
                        q.append((c, path + [c]))
        clickpaths.append({**t, "chains": [{"js": h["via"], "verb": h["route"]["verb"], "path": h["route"]["path"], "handler": h["route"]["fn"],
                                            "reads": h["route"]["reads"], "writes": h["route"]["writes"]} for h in hit]})

    # ---- data view: every table, its columns, and who reads / changes it
    tables = {}
    for fid, d in det.items():
        for t in d.get("data", {}).get("reads", []):
            tables.setdefault(t, {"readers": set(), "writers": set()})["readers"].add(fid)
        for t in d.get("data", {}).get("writes", []):
            tables.setdefault(t, {"readers": set(), "writers": set()})["writers"].add(fid)
    columns = _table_columns(corpus_files(app_dir))
    for t in columns:
        tables.setdefault(t, {"readers": set(), "writers": set()})
    table_list = []
    for t, v in sorted(tables.items()):
        rr = [f"{r['verb']} {r['path']}" for r in routes if t in r["reads"]]
        rw = [f"{r['verb']} {r['path']}" for r in routes if t in r["writes"]]
        table_list.append({"name": t, "columns": columns.get(t, []), "readers": sorted(v["readers"]), "writers": sorted(v["writers"]),
                           "routesRead": rr, "routesWrite": rw})

    # ---- security lens: per-function findings + routes with no login check + secrets at file level
    global_auth = bool(re.search(r"@\w+\.before_request[\s\S]{0,400}?(login|session|auth|token|current_user)", text)) or \
        bool(re.search(r"LoginManager|login_manager\.|flask_login|HTTPBasicAuth|OAuth2PasswordBearer|Depends\(\s*get_current_user", text))
    findings = []
    for fid, d in det.items():
        for s in d.get("security") or []:
            findings.append({**s, "fn": fid})
        decos = " ".join(d.get("decorators") or [])
        if d.get("routes") and d.get("lang") == "python" and any(" " in r for r in d["routes"]) and \
                any(re.search(r"\.(route|get|post|put|patch|delete)$", x) for x in d.get("decorators") or []) and \
                not re.search(r"login|auth|require|permission|jwt|role|admin|token", decos, re.I) and not d.get("authHint") and not global_auth:
            writes_data = any(r.split(" ")[0] in ("POST", "PUT", "PATCH", "DELETE") for r in d["routes"])
            findings.append({"fn": fid, "sev": "high" if writes_data else "medium", "kind": "no-auth", "line": d.get("start", 0),
                             "title": f"No login check in front of {', '.join(d['routes'][:2])}",
                             "why": "Nothing in the code checks who's asking before this runs, so anyone who can reach the server can call it"
                                    + (" and change data." if writes_data else " and read what it returns."),
                             "fix": "Put a login check in front: @login_required (Flask-Login), a before_request hook, or your SSO middleware. If it's meant to be public, note that in DECISIONS.md."})
    # file-level code (outside any function): app.config["SECRET_KEY"] = "...", app.run(debug=True) under __main__
    import ast as _ast
    for rel, src in corpus_files(app_dir, (".py", ".pyw")):
        try:
            tree = _ast.parse(src)
        except SyntaxError:
            continue
        top = _ast.Module(body=[n for n in tree.body if not isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef))], type_ignores=[])
        file_fid = next((fid for fid, f in fns.items() if f["file"].replace("\\", "/") == rel and f["kind"] == "file"), None)
        for s in explain._py_security(top, [], {}):
            findings.append({**s, "fn": file_fid or rel, "file": rel})
    rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda x: (rank.get(x["sev"], 3), fns.get(x["fn"], {}).get("file", ""), x.get("line", 0)))

    data = {"routes": routes, "hotspots": hot, "historyNote": history_note,
            "triggers": triggers, "clickpaths": clickpaths, "tables": table_list, "security": findings, "globalAuth": global_auth, "unused": unused[:40], "tests": tests, "tours": tours,
            "fnStats": {fid: {"label": (d.get("complexity") or {}).get("label", ""), "score": (d.get("complexity") or {}).get("score", 0),
                              "todos": len(d.get("todos") or []), "tables": sorted(set(d.get("data", {}).get("reads", [])) | set(d.get("data", {}).get("writes", [])))}
                        for fid, d in det.items()}}
    _cache[app] = {"key": key, "data": data}
    return data


SKIP_DIRS = (".git", "node_modules", "__pycache__", "dist", "build", "data", ".venv", "venv", "env")


def corpus_files(app_dir, exts=(".py", ".pyw", ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".jinja", ".j2", ".sql")):
    out = []
    for root, dirs, files in os.walk(app_dir):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            if f.endswith(exts):
                p = os.path.join(root, f)
                try:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        out.append((os.path.relpath(p, app_dir).replace("\\", "/"), fh.read()))
                except OSError:
                    pass
    return out


EVENTS = "click|submit|change|input|keydown|keyup|dblclick|load|DOMContentLoaded|focus|blur"


def _element_label(html_texts, sel):
    """Human label for #id: the element's text, aria-label, value or placeholder, from the HTML."""
    if not sel.startswith("#"):
        return ""
    ident = re.escape(sel[1:])
    for _, html in html_texts:
        m = re.search(r"<(\w+)[^>]*\bid=[\"']" + ident + r"[\"'][^>]*>(.*?)</\1>", html, re.S | re.I)
        if m:
            inner = re.sub(r"<[^>]+>", " ", m.group(2))
            inner = re.sub(r"\s+", " ", inner).strip()
            attrs = re.search(r"(?:aria-label|title|value|placeholder)=[\"']([^\"']+)", m.group(0))
            return f"{m.group(1).lower()} \"{(inner or (attrs.group(1) if attrs else ''))[:40]}\""
    return ""


def _scan_triggers(app_dir, by_bare):
    """UI element -> JS function, from HTML on*="fn()" attributes and JS addEventListener / .onclick = fn."""
    files = corpus_files(app_dir, (".html", ".htm", ".jinja", ".j2", ".js", ".ts", ".jsx", ".tsx"))
    html_texts = [(p, t) for p, t in files if p.endswith((".html", ".htm", ".jinja", ".j2"))]
    out, seen = [], set()

    def add(fn_name, event, element, path, line):
        fid = by_bare.get(fn_name)
        if fid and (fid, event, element) not in seen:
            seen.add((fid, event, element))
            out.append({"fn": fid, "event": event, "element": element, "file": path, "line": line})

    for path, text in html_texts:
        for m in re.finditer(r"<(\w+)([^>]*?)\bon(" + EVENTS.lower() + r")=[\"']\s*(?:return\s+)?(\w+)\s*\(", text, re.I):
            tag, attrs, ev, fn = m.group(1).lower(), m.group(2), m.group(3).lower(), m.group(4)
            idm = re.search(r"\bid=[\"']([\w-]+)", attrs)
            close = re.search(r">(.*?)</" + tag, text[m.end():m.end() + 300], re.S)
            label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", close.group(1))).strip()[:40] if close else ""
            add(fn, ev, f"{tag}{'#' + idm.group(1) if idm else ''} \"{label}\"".replace(' ""', ""), path, text[:m.start()].count("\n") + 1)
    for path, text in files:
        if path.endswith((".html", ".htm", ".jinja", ".j2")):
            continue
        # the handler body ends where the next top-level statement starts (a newline followed by a non-space)
        for m in re.finditer(r"""(?:querySelector|getElementById|\$)\(\s*["'`]([^"'`]+)["'`]\s*\)\s*(?:\?)?\.\s*(?:addEventListener\(\s*["'](\w+)["']\s*,\s*([\s\S]{0,300}?)(?=\n\S|\Z)|on(\w+)\s*=\s*([\s\S]{0,300}?)(?=\n\S|\Z))""", text):
            sel = m.group(1)
            sel = sel if sel.startswith(("#", ".", "[")) or not re.match(r"^[\w-]+$", sel) else "#" + sel  # getElementById("x")
            ev = m.group(2) or m.group(4)
            body = m.group(3) or m.group(5) or ""
            names = re.findall(r"^\s*(\w+)\s*[,)]|=>\s*\{?\s*(?:await\s+)?(\w+)\s*\(|function\s*\([^)]*\)\s*\{\s*(?:await\s+)?(\w+)\s*\(|^\s*(\w+)\s*;", body)
            for tup in names[:2]:
                fn = next((x for x in tup if x), "")
                if fn and fn not in ("function", "async", "e", "ev", "event"):
                    label = _element_label(html_texts, sel) or sel
                    add(fn, ev, label, path, text[:m.start()].count("\n") + 1)
        for m in re.finditer(r"""(document|window)\.addEventListener\(\s*["'](\w+)["']\s*,\s*(?:async\s*)?(?:\(\w*\)\s*=>\s*\{?\s*)?(\w+)""", text):
            if m.group(3) not in ("e", "ev", "event", "function", "async"):
                add(m.group(3), m.group(2), f"the {m.group(1)}", path, text[:m.start()].count("\n") + 1)
    return out


def _table_columns(files):
    """{table: [columns]} from CREATE TABLE, INSERT INTO t (cols), UPDATE t SET col =, and SQLAlchemy models."""
    cols = {}

    def add(t, cs):
        t = t.strip('[]"`').split(".")[-1]
        lst = cols.setdefault(t, [])
        for c in cs:
            c = c.strip().strip('[]"`')
            if c and re.match(r"^\w+$", c) and c.upper() not in ("PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "CHECK", "KEY") and c not in lst:
                lst.append(c)

    for _, text in files:
        for m in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?([\w.\[\]\"`]+)\s*\(([\s\S]*?)\)\s*(?:;|[\"'`]|\n)", text, re.I):
            parts = re.split(r",\s*(?![^()]*\))", m.group(2))
            add(m.group(1), [p.strip().split()[0] for p in parts if p.strip()])
        for m in re.finditer(r"insert\s+(?:or\s+\w+\s+)?into\s+([\w.\[\]\"`]+)\s*\(([^)]+)\)", text, re.I):
            add(m.group(1), m.group(2).split(","))
        for m in re.finditer(r"update\s+([\w.\[\]\"`]+)\s+set\s+([^\"'`]+?)\s+where", text, re.I):
            add(m.group(1), [a.split("=")[0] for a in m.group(2).split(",")])
        for m in re.finditer(r"__tablename__\s*=\s*[\"'](\w+)[\"']([\s\S]{0,2000}?)(?=\nclass\s|\Z)", text):
            add(m.group(1), re.findall(r"^\s+(\w+)\s*(?::\s*Mapped\[[^\]]*\]\s*)?=\s*(?:db\.)?(?:Column|mapped_column|relationship)\(", m.group(2), re.M))
    return {t: c for t, c in cols.items() if t and t.lower() not in ("select", "set", "values")}


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
