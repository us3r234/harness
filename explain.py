"""Deep detail for one function on the Map: everything worked out from the code itself (free),
plus an optional plain-English explanation from Claude (on click, cached, costs tokens).

Static part (always accurate, no AI):
  - signature, full docstring/comment, the source with line numbers
  - prerequisites: modules, outside programs (ffmpeg, git...), files, network, env vars,
    settings/constants it reads; for JS: backend endpoints, pywebview calls, page elements, storage
  - triggers: the web route(s) that run it (Flask/FastAPI decorators, http.server path checks)
  - reach: the call chain from an entry point (nothing calls it) down to this function
  - JS -> Python: a fetch("/api/x") in JS is linked to the Python function that answers /api/x
"""
import ast
import hashlib
import json
import os
import re
import subprocess
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "explain")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MODEL = "sonnet"
_file_cache = {}


def _read(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    hit = _file_cache.get(path)
    if hit and hit[0] == st.st_mtime:
        return hit[1]
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    _file_cache[path] = (st.st_mtime, text)
    return text


def _line(loc):
    m = re.match(r"L(\d+)", loc or "")
    return int(m.group(1)) if m else 0


def _bare(label):
    return label.lstrip(".").split("(")[0]


# ---------------------------------------------------------------- python
SUBPROC = {"run", "Popen", "call", "check_call", "check_output"}
NET = {"urlopen": "HTTP request (urllib)", "get": None, "post": None, "request": None,
       "create_connection": "network socket", "gethostbyaddr": "DNS lookup", "getaddrinfo": "DNS lookup"}


def _py_imports(tree):
    names = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                names[(a.asname or a.name).split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                names[a.asname or a.name] = f"{node.module}.{a.name}"
    return names


def _py_constants(tree):
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    out[t.id] = ast.get_source_segment(_py_constants.src, node.value) or ""
                elif isinstance(t, ast.Tuple) and isinstance(node.value, ast.Tuple) and len(t.elts) == len(node.value.elts):
                    for tn, tv in zip(t.elts, node.value.elts):  # A, B = f("a"), f("b")
                        if isinstance(tn, ast.Name) and tn.id.isupper():
                            out[tn.id] = ast.get_source_segment(_py_constants.src, tv) or ""
    return out


def _py_find(tree, name, line):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
            if best is None or abs(first - line) < abs(best[1] - line):
                best = (node, first)
    return best[0] if best else None


def _const_str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _py_detail(src, name, line):
    tree = ast.parse(src)
    _py_constants.src = src
    imports, consts = _py_imports(tree), _py_constants(tree)
    node = _py_find(tree, name, line)
    if node is None:
        return None
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    lines = src.splitlines()
    sig = lines[node.lineno - 1].strip().rstrip(":")
    pre = {"modules": set(), "programs": set(), "files": set(), "network": set(), "env": set(), "settings": set()}
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in imports:
            pre["modules"].add(imports[n.id])
        if isinstance(n, ast.Name) and n.id in consts and n.id != name:
            v = consts[n.id].replace("\n", " ")
            pre["settings"].add(f"{n.id} = {v[:60]}" if v else n.id)
        if isinstance(n, ast.Call):
            f = n.func
            fname = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
            if owner == "subprocess" and fname in SUBPROC and n.args:
                a0 = n.args[0]
                if isinstance(a0, (ast.List, ast.Tuple)) and a0.elts:
                    prog = _const_str(a0.elts[0]) or (a0.elts[0].id if isinstance(a0.elts[0], ast.Name) else "")
                    if prog:
                        pre["programs"].add(prog)
            if fname == "open" and n.args:
                arg = n.args[0]
                pre["files"].add(_const_str(arg) or (arg.id if isinstance(arg, ast.Name) else ast.unparse(arg)[:50]))
            if fname in ("getenv",) or (fname == "get" and isinstance(f, ast.Attribute) and ast.unparse(f.value).endswith("environ")):
                if n.args and _const_str(n.args[0]):
                    pre["env"].add(_const_str(n.args[0]))
            if fname in NET and (NET[fname] or owner in ("requests", "httpx", "session")):
                pre["network"].add(NET[fname] or f"{owner}.{fname}")
        if isinstance(n, ast.Subscript) and ast.unparse(n.value).endswith("environ") and _const_str(n.slice):
            pre["env"].add(_const_str(n.slice))
    # programs hidden behind a setting: FFMPEG = shutil.which("ffmpeg") -> ffmpeg
    for s in list(pre["settings"]):
        for prog in re.findall(r"""(?:which|find_\w*|where)\(\s*["']([\w.\-]+)["']""", s):
            pre["programs"].add(prog)
    pre["programs"] = {p for p in pre["programs"] if not p.isupper()}  # drop bare constant names once resolved
    doc = ast.get_docstring(node) or ""
    routes = _decorator_routes(node)
    out = {"start": start, "end": node.end_lineno, "sig": sig, "doc": doc, "routes": routes,
           "pre": {k: sorted(v) for k, v in pre.items() if v}, "lang": "python"}
    out.update(_py_extras(node, lines, routes))
    return out


# ---------------------------------------------------------------- python: the extra detail
SQL_RE = re.compile(r"\b(select|insert\s+into|update|delete\s+from|replace\s+into|create\s+table)\b", re.I)


def _sql_tables(text):
    """(reads, writes) table names from one SQL string."""
    reads, writes = set(), set()
    t = re.sub(r"\s+", " ", text)
    for m in re.finditer(r"\b(?:from|join)\s+([\w.\[\]\"`]+)", t, re.I):
        reads.add(m.group(1).strip('[]"`'))
    for m in re.finditer(r"\b(?:insert\s+into|replace\s+into|update|delete\s+from|create\s+table(?:\s+if\s+not\s+exists)?)\s+([\w.\[\]\"`]+)", t, re.I):
        writes.add(m.group(1).strip('[]"`'))
    reads -= writes if re.match(r"\s*delete", t, re.I) else set()
    return {r for r in reads if r.lower() not in ("select", "(")}, writes


def _complexity(node):
    """Cyclomatic-style count (1 + decision points) and deepest nesting of blocks."""
    score = 1
    for n in ast.walk(node):
        if isinstance(n, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler, ast.With, ast.AsyncWith)):
            score += 0 if isinstance(n, (ast.With, ast.AsyncWith)) else 1
        elif isinstance(n, ast.BoolOp):
            score += len(n.values) - 1
        elif isinstance(n, ast.comprehension):
            score += 1 + len(n.ifs)
        elif hasattr(ast, "match_case") and isinstance(n, ast.match_case):
            score += 1

    def depth(n, d=0):
        blocks = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)
        kids = [depth(c, d + 1 if isinstance(c, blocks) else d) for c in ast.iter_child_nodes(n)
                if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))]
        return max([d] + kids)
    return score, depth(node)


def _rate(score, lines, nest):
    if score <= 5 and lines <= 40 and nest <= 2:
        return "simple", "Short, few branches. Easy to read top to bottom."
    if score <= 10 and lines <= 80 and nest <= 3:
        return "moderate", "Some branches to keep in mind, but one read-through should do it."
    if score <= 20:
        return "complex", "Many paths through it. Read it with the diagram open, and test carefully when changing it."
    return "very complex", "So many paths that it's hard to hold in your head. A good candidate to split into smaller functions."


def _py_extras(node, lines, routes):
    params = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        pos = a.posonlyargs + a.args
        defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
        for arg, dflt in zip(pos, defaults):
            if arg.arg in ("self", "cls"):
                continue
            params.append({"name": arg.arg, "type": ast.unparse(arg.annotation) if arg.annotation else "",
                           "default": ast.unparse(dflt) if dflt is not None else ""})
        if a.vararg:
            params.append({"name": "*" + a.vararg.arg, "type": "", "default": ""})
        for arg, dflt in zip(a.kwonlyargs, a.kw_defaults):
            params.append({"name": arg.arg, "type": ast.unparse(arg.annotation) if arg.annotation else "",
                           "default": ast.unparse(dflt) if dflt is not None else ""})
        if a.kwarg:
            params.append({"name": "**" + a.kwarg.arg, "type": "", "default": ""})

    # what a web request must carry: path params, query args, JSON body keys, form fields
    req = {"path": [], "query": set(), "json": set(), "form": set(), "files": set(), "headers": set()}
    for r in routes:  # /api/users/<int:user_id> (Flask) or /items/{item_id} (FastAPI)
        for pair in re.findall(r"<(?:\w+:)?(\w+)>|\{(\w+)\}", r.split(" ", 1)[1]):
            req["path"] += [x for x in pair if x and x not in req["path"]]
    json_vars = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = ast.unparse(n.value)
            if re.search(r"request\.(get_json\(.*\)|json)(?!\w)", v):
                json_vars.add(n.targets[0].id)
    src_map = {"args": "query", "values": "query", "form": "form", "files": "files", "headers": "headers", "json": "json", "cookies": "headers"}
    for n in ast.walk(node):
        key, base = None, None
        if isinstance(n, ast.Subscript) and _const_str(n.slice):
            key, base = _const_str(n.slice), ast.unparse(n.value)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and n.args and _const_str(n.args[0]):
            key, base = _const_str(n.args[0]), ast.unparse(n.func.value)
        if not key:
            continue
        m = re.fullmatch(r"request\.(\w+)", base)
        if m and m.group(1) in src_map:
            req[src_map[m.group(1)]].add(key)
        elif base in json_vars or re.fullmatch(r"request\.get_json\(.*\)", base):
            req["json"].add(key)
    req = {k: sorted(v) for k, v in req.items() if v}

    # what it hands back
    rets, codes = [], set()
    for n in ast.walk(node):
        if isinstance(n, ast.Return) and n.value is not None:
            v = n.value
            if isinstance(v, ast.Tuple) and len(v.elts) >= 2 and isinstance(v.elts[1], ast.Constant) and isinstance(v.elts[1].value, int):
                codes.add(v.elts[1].value)
            s = ast.unparse(v)
            if s not in rets:
                rets.append(s)
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("abort") and n.args and isinstance(n.args[0], ast.Constant):
            codes.add(n.args[0].value)
    yields = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(node))

    # data it touches: SQL in any string (plain or f-string)
    reads, writes = set(), set()
    for n in ast.walk(node):
        text = n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else \
            "".join(p.value for p in n.values if isinstance(p, ast.Constant)) if isinstance(n, ast.JoinedStr) else ""
        if text and SQL_RE.search(text):
            r, w = _sql_tables(text)
            reads |= r
            writes |= w

    # when things go wrong
    raises, catches, bare = set(), set(), False
    for n in ast.walk(node):
        if isinstance(n, ast.Raise) and n.exc is not None:
            e = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
            raises.add(ast.unparse(e))
        if isinstance(n, ast.ExceptHandler):
            if n.type is None:
                bare = True
            else:
                for t in (n.type.elts if isinstance(n.type, ast.Tuple) else [n.type]):
                    catches.add(ast.unparse(t))

    score, nest = _complexity(node)
    length = node.end_lineno - node.lineno + 1
    label, why = _rate(score, length, nest)
    body = lines[node.lineno - 1:node.end_lineno]
    todos = [{"line": node.lineno + i, "text": m.group(0).strip()} for i, l in enumerate(body)
             for m in [re.search(r"#\s*(TODO|FIXME|HACK|XXX|BUG)\b.*", l)] if m]
    return {"params": params, "request": req, "returns": rets[:6], "codes": sorted(codes), "yields": yields,
            "data": {"reads": sorted(reads), "writes": sorted(writes)} if reads or writes else {},
            "errors": {"raises": sorted(raises), "catches": sorted(catches), "bare": bare},
            "complexity": {"score": score, "lines": length, "nesting": nest, "params": len(params), "label": label, "why": why},
            "todos": todos}


HTTP_VERBS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _decorator_routes(node):
    """Flask/FastAPI style: @app.route("/x", methods=[...]), @app.get("/x"), @router.post("/x")."""
    routes = []
    for d in getattr(node, "decorator_list", []):
        if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.args and _const_str(d.args[0])):
            continue
        verb, path = d.func.attr.upper(), _const_str(d.args[0])
        if verb == "ROUTE" or verb == "API_ROUTE":
            methods = next((kw.value for kw in d.keywords if kw.arg == "methods"), None)
            verbs = [_const_str(e) for e in methods.elts] if isinstance(methods, (ast.List, ast.Tuple)) else ["GET"]
            routes += [f"{v.upper()} {path}" for v in verbs if v]
        elif verb in HTTP_VERBS:
            routes.append(f"{verb} {path}")
    return routes


def _py_all_routes(src):
    """{route path: {function names}} for every decorated route in one file."""
    out = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for r in _decorator_routes(n):
                out.setdefault(r.split(" ", 1)[1], set()).add(n.name)
    return out


def _path_matches(route, ep):
    """Does a page-side path like /api/users/ (from `/api/users/${id}`) hit a route like /api/users/<int:user_id>?"""
    rx = re.sub(r"<[^>]+>|\{[^}]+\}", "[^/]+", re.escape(route).replace(r"\<", "<").replace(r"\>", ">").replace(r"\{", "{").replace(r"\}", "}"))
    probe = ep + "X" if ep.endswith("/") else ep
    return re.fullmatch(rx, probe) is not None


IGNORE_CALLS = {"send", "send_response", "send_header", "end_headers", "send_error", "dumps", "loads", "get", "write",
                "len", "int", "str", "float", "bool", "dict", "list", "set", "tuple", "print", "isinstance", "open",
                "parse_qs", "urlparse", "unquote", "quote", "min", "max", "sorted", "range", "enumerate", "zip", "any", "all"}


def _py_routes(src):
    """http.server style: inside do_GET/do_POST, `if path == "/x":` blocks -> names called in them."""
    out = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for fn in ast.walk(tree):
        if not (isinstance(fn, ast.FunctionDef) and fn.name in ("do_GET", "do_POST", "do_PUT", "do_DELETE")):
            continue
        verb = fn.name[3:]
        for n in ast.walk(fn):
            if not isinstance(n, ast.If):
                continue
            paths = [c.value for c in ast.walk(n.test) if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value.startswith("/")]
            if not paths:
                continue
            # only this branch's own body (not nested elif branches), and only calls to project
            # functions: plain names, self.method(), or a function passed in (cache.get("x", 10, fn))
            branch = [x for b in n.body for x in ast.walk(b)]
            for c in branch:
                if not isinstance(c, ast.Call):
                    continue
                f, names = c.func, []
                if isinstance(f, ast.Name):
                    names.append(f.id)
                elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self":
                    names.append(f.attr)
                names += [a.id for a in c.args if isinstance(a, ast.Name) and a.id[:1].islower()]
                for nm in names:
                    if nm in IGNORE_CALLS or nm.startswith("_send"):
                        continue
                    for p in paths:
                        out.setdefault(nm, set()).add(f"{verb} {p}")
    # a helper used by most routes (self._json(), self.reply()) is plumbing, not the handler
    total = len({r for rs in out.values() for r in rs})
    if total >= 4:
        out = {nm: rs for nm, rs in out.items() if len(rs) <= max(2, total * 0.4)}
    return out


# ---------------------------------------------------------------- javascript
def _js_body(lines, start):
    """Lines of a JS function from its first line to the matching closing brace (string-aware enough)."""
    depth, began, out = 0, False, []
    for i in range(start - 1, min(len(lines), start + 400)):
        s = re.sub(r"(['\"`])(?:\\.|(?!\1).)*\1", "''", lines[i])
        s = re.sub(r"//.*", "", s)
        out.append(lines[i])
        for ch in s:
            if ch == "{":
                depth += 1
                began = True
            elif ch == "}":
                depth -= 1
        if began and depth <= 0:
            return out, i + 1
        if not began and s.rstrip().endswith(";"):  # one-line arrow fn: const f = (x) => g(x);
            return out, i + 1
    return out, start + len(out) - 1


def _js_detail(src, name, line):
    lines = src.splitlines()
    if not (0 < line <= len(lines)):
        return None
    body, end = _js_body(lines, line)
    text = "\n".join(body)
    doc, j = [], line - 2
    while j >= 0 and re.match(r"\s*(//|\*|/\*)", lines[j]):
        doc.insert(0, re.sub(r"^\s*(//+|/\*+|\*/?)\s?", "", lines[j]).rstrip("*/ "))
        j -= 1
    m = re.search(r"//\s*(.+)$", lines[line - 1])
    if m:
        doc.append(m.group(1))
    pre = {
        # any quoted path the page asks the backend for: fetch("/api/x"), video.src = `/stream?...`, <img src="/thumb">
        "endpoints": sorted(set(re.findall(r"""[`'"](/[a-z][\w\-]*(?:/[\w\-.]+)*/?)(?=[?`'"]|\$\{)""", text))),
        "desktop": sorted(set(re.findall(r"pywebview\??\.api\??\.(\w+)", text))),
        "elements": sorted(set(re.findall(r"""\$\(\s*['"`](#[\w-]+)""", text)))[:12],
        "storage": sorted(set(re.findall(r"""(?:localStorage\.\w+|store)\(\s*['"`]([\w-]+)""", text))),
        "timers": ["runs on a timer"] if re.search(r"setInterval|setTimeout", text) else [],
    }
    sig = lines[line - 1].strip()[:140]
    pm = re.search(r"\(([^)]*)\)", sig)
    params = [{"name": p.split("=")[0].strip(), "type": "", "default": p.split("=", 1)[1].strip() if "=" in p else ""}
              for p in (pm.group(1).split(",") if pm else []) if p.strip()]
    code_only = re.sub(r"(['\"`])(?:\\.|(?!\1).)*\1", "''", re.sub(r"//.*", "", text))
    score = 1 + len(re.findall(r"\bif\s*\(|\bfor\s*\(|\bwhile\s*\(|\bcatch\s*\(|\bcase\b|&&|\|\||\?\?|\?(?!\.)", code_only))
    nest = max((len(l) - len(l.lstrip())) // 2 for l in body) if body else 0
    label, why = _rate(score, len(body), max(0, nest - 1))
    todos = [{"line": line + i, "text": m.group(0).strip()} for i, l in enumerate(body)
             for m in [re.search(r"//\s*(TODO|FIXME|HACK|XXX|BUG)\b.*", l)] if m]
    rets = []
    for r in re.findall(r"\breturn\s+([^;\n]+)", text):
        if r.strip() not in rets:
            rets.append(r.strip()[:90])
    return {"start": line, "end": end, "sig": sig, "doc": " ".join(d for d in doc if d).strip(),
            "routes": [], "pre": {k: v for k, v in pre.items() if v}, "lang": "javascript",
            "params": params, "request": {}, "returns": rets[:6], "codes": [], "yields": False, "data": {},
            "errors": {"raises": sorted(set(re.findall(r"throw new (\w+)", text))), "catches": ["error"] if "catch" in code_only else [], "bare": False},
            "complexity": {"score": score, "lines": len(body), "nesting": max(0, nest - 1), "params": len(params), "label": label, "why": why},
            "todos": todos}


# ---------------------------------------------------------------- whole picture
def detail(app, app_dir, m, fn_id):
    """m = maps.load(app). Returns the static detail for one function."""
    f = m["fns"].get(fn_id)
    if not f:
        return None
    path = os.path.join(app_dir, f["file"])
    src = _read(path)
    if src is None:
        return {"error": f"can't read {f['file']}"}
    name, line = _bare(f["label"]), _line(f["line"])
    try:
        d = _py_detail(src, name, line) if path.endswith(".py") or path.endswith(".pyw") else _js_detail(src, name, line)
    except SyntaxError:
        d = None
    d = d or {"start": line, "end": line, "sig": f["label"], "doc": f.get("doc", ""), "routes": [], "pre": {}, "lang": "?"}
    lines = src.splitlines()
    end = min(d["end"], d["start"] + 160)
    d["code"] = [[i + 1, lines[i]] for i in range(d["start"] - 1, min(end, len(lines)))]
    d["truncated"] = d["end"] > end

    # routes from http.server handlers anywhere in the app (cached per file mtime)
    routes = set(d["routes"])
    for fid, g in (m["fns"].items() if d["lang"] == "python" else ()):  # only Python answers routes
        if g["file"].endswith((".py", ".pyw")) and g["label"].lstrip(".").startswith(("do_GET", "do_POST")):
            rsrc = _read(os.path.join(app_dir, g["file"]))
            if rsrc:
                routes |= _py_routes(rsrc).get(name, set())
    d["routes"] = sorted(routes)

    # JS fetch("/api/x") -> the Python function(s) that answer /api/x
    d["backend"] = []
    if d["lang"] == "javascript" and d["pre"].get("endpoints"):
        handlers = {}  # route path -> python function names that answer it
        for pyfile in {g["file"] for g in m["fns"].values() if g["file"].endswith((".py", ".pyw"))}:
            src_ = _read(os.path.join(app_dir, pyfile)) or ""
            for nm, rs in _py_routes(src_).items():           # http.server: if path == "/x"
                for r in rs:
                    handlers.setdefault(r.split(" ", 1)[1], set()).add(nm)
            for route, nms in _py_all_routes(src_).items():   # Flask / FastAPI decorators
                handlers.setdefault(route, set()).update(nms)
        by_name = {}  # handler name -> Python function (a JS function can share the name)
        for fid, g in m["fns"].items():
            if g["file"].endswith((".py", ".pyw")):
                by_name.setdefault(_bare(g["label"]), fid)
        for ep in d["pre"]["endpoints"]:
            hits = handlers.get(ep) or {nm for route, nms in handlers.items() if _path_matches(route, ep) for nm in nms}
            for nm in sorted(hits):
                if nm in by_name:
                    d["backend"].append({"endpoint": ep, "fn": by_name[nm]})

    # reach: shortest chain from an entry point (nothing calls it) down to here
    d["reach"] = _reach(m, fn_id)
    d["explained"] = _cached(app, fn_id, d)
    return d


def _reach(m, fn_id, limit=2):
    fns, paths, seen = m["fns"], [], {fn_id}
    q = deque([[fn_id]])
    while q and len(paths) < limit:
        chain = q.popleft()
        callers = [c for c in fns[chain[0]]["callers"] if c in fns]
        if not callers:
            if len(chain) > 1:
                paths.append(chain)
            continue
        if len(chain) >= 7:
            paths.append(chain)
            continue
        for c in callers:
            if c not in seen:
                seen.add(c)
                q.append([c] + chain)
    return paths


# ---------------------------------------------------------------- plain-English explanation (Claude, on demand)
def _hash(d):
    return hashlib.sha1("\n".join(l for _, l in d["code"]).encode("utf-8")).hexdigest()[:12]


def _key(app, fn_id):
    return os.path.join(CACHE, app, re.sub(r"[^\w.-]", "_", fn_id) + ".json")


def _cached(app, fn_id, d):
    try:
        with open(_key(app, fn_id), encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError):
        return None
    c["stale"] = c.get("code_hash") != _hash(d)
    return c


PROMPT = """Explain one function from the "{app}" project to someone new to this codebase.
Lead with plain words, then the real mechanism, laid out as a sequence.

Write Markdown with exactly these sections and nothing else:
**In plain words**: 2-3 sentences, no jargon: what it does and why the app needs it.
**Step by step**: a numbered list of what happens when it runs, in order (4-8 steps). Name real things (variables, files, endpoints).
**What it needs first**: bullets of what must already exist or be true before it can work (from the prerequisites below, explained).
**How it connects**: bullets: what triggers it, what it hands its result to, and what it calls. Use the names given.
**Watch out**: 1-2 bullets: an edge case, a failure mode, or a thing that's easy to break when changing it.

Keep it under 300 words. Don't repeat the code back.

Function: {label}  ({file} lines {start}-{end})
Signature: {sig}
Docstring/comment: {doc}
Triggered by routes: {routes}
Reached from: {reach}
Called by: {callers}
Calls: {calls}
Prerequisites found in the code: {pre}

Source:
```
{code}
```"""


def explain(app, m, fn_id, d):
    f = m["fns"][fn_id]
    label = lambda i: m["fns"][i]["label"] + (f" ({m['fns'][i]['doc'][:70]})" if m["fns"][i]["doc"] else "") if i in m["fns"] else i
    prompt = PROMPT.format(
        app=app, label=f["label"], file=f["file"], start=d["start"], end=d["end"], sig=d["sig"],
        doc=d["doc"] or "(none)", routes=", ".join(d["routes"]) or "(none found)",
        reach=" | ".join(" -> ".join(m["fns"][i]["label"] for i in ch) for ch in d["reach"]) or "(entry point)",
        callers=", ".join(label(i) for i in f["callers"][:10]) or "(none)",
        calls=", ".join(label(i) for i in f["calls"][:12]) or "(none)",
        pre=json.dumps(d["pre"]) if d["pre"] else "(none)",
        code="\n".join(l for _, l in d["code"])[:12000])
    args = ["claude", "-p", "--model", MODEL, "--output-format", "json", "--no-session-persistence",
            "--strict-mcp-config", "--tools", "", "--system-prompt",
            "You explain source code clearly and accurately. Only describe what the code shows; say so when unsure."]
    try:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=180, creationflags=NO_WINDOW, cwd=os.path.join(HERE, "data"))
        out = json.loads(r.stdout)
    except FileNotFoundError:
        return {"error": "The `claude` command isn't installed here, so plain-English explanations aren't available."}
    except (subprocess.TimeoutExpired, ValueError) as e:
        return {"error": f"Claude didn't answer ({type(e).__name__})."}
    if out.get("is_error"):
        return {"error": str(out.get("result"))[:300]}
    u = out.get("usage", {})
    res = {"text": out.get("result", ""), "model": MODEL, "out": u.get("output_tokens", 0),
           "inp": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0),
           "usd": out.get("total_cost_usd"), "code_hash": _hash(d)}
    os.makedirs(os.path.dirname(_key(app, fn_id)), exist_ok=True)
    with open(_key(app, fn_id), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False)
    res["stale"] = False
    return res
