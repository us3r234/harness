"""harness viewer: a window onto every project in config.json (read-only except your notes and maps).

  pythonw app.py              open the window (pywebview), or your browser if pywebview isn't installed
  python app.py --serve       backend only, open http://127.0.0.1:47640/ in a browser
"""
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import harness
import notes
import sessions
import maps
import explain
import project

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")
PORT = 47640
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
TEXT_EXT = {".md", ".txt", ".log", ".json"}


def git(path, *args):
    r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    return r.stdout.rstrip() if r.returncode == 0 else None


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def bak_files(path):
    out = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__")]
        out += [os.path.relpath(os.path.join(root, f), path) for f in files if ".bak" in f]
    for d in os.listdir(path):
        if d.startswith("backup") and os.path.isdir(os.path.join(path, d)):
            out.append(d + "/")
    return sorted(out)


def parse_log(text):
    """LOG.md → [{title, date, fields:{Asked, Built by, ...}}], newest first."""
    rounds = []
    for block in re.split(r"^## ", text or "", flags=re.M)[1:]:
        lines = block.splitlines()
        title = lines[0].strip()
        m = re.search(r"\((\d{4}-\d{2}-\d{2})\)\s*$", title)
        fields = {}
        for line in lines[1:]:
            fm = re.match(r"-\s*\*\*(.+?):\*\*\s*(.*)", line)
            if fm:
                fields[fm.group(1)] = fm.group(2).strip()
        rounds.append({"title": re.sub(r"\s*\(\d{4}-\d{2}-\d{2}\)\s*$", "", title),
                       "date": m.group(1) if m else "", "fields": fields})
    return rounds


def parse_decisions(text):
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"-\s*\*\*(\d{4}-\d{2}-\d{2}):\s*(.+?)\*\*\s*(.*)", line)
        if m:
            out.append({"date": m.group(1), "decision": m.group(2), "why": m.group(3)})
    return out


def summary(name, path):
    is_git = os.path.isdir(os.path.join(path, ".git"))
    s = {"name": name, "git": is_git, "dirty": 0, "last": "", "remote": "",
         "docs": {d: os.path.exists(os.path.join(path, d)) for d in harness.DOCS},
         "bak": len(bak_files(path)), "rounds": 0, "lastRound": ""}
    if is_git:
        s["dirty"] = len([l for l in (git(path, "status", "--porcelain") or "").splitlines() if l.strip()])
        s["last"] = git(path, "log", "-1", "--format=%cs") or ""
        if git(path, "remote"):
            ahead = git(path, "rev-list", "--count", "@{u}..HEAD")
            s["remote"] = "pushed" if ahead == "0" else (f"{ahead} unpushed" if ahead else "no upstream")
        else:
            s["remote"] = "local only"
    rounds = parse_log(read(os.path.join(path, "LOG.md")))
    s["rounds"] = len(rounds)
    s["lastRound"] = rounds[0]["title"] if rounds else ""
    # loose ends: the newest round's Open line, unless it says there's nothing open
    op = rounds[0]["fields"].get("Open", "") if rounds else ""
    s["open"] = "" if re.fullmatch(r"\s*(none\.?|none known\.?|-|n/a)?\s*", op, re.I) else op
    s["openRound"] = rounds[0]["title"] if s["open"] else ""
    s["adopted"] = all(s["docs"].values()) and is_git
    if not s["last"]:  # fall back to newest file time for non-git apps
        newest = 0
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", "dist", "build")]
            for f in files:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(root, f)))
                except OSError:
                    pass
        if newest:
            import datetime
            s["touched"] = datetime.date.fromtimestamp(newest).isoformat()
    return s


def detail(name):
    path = harness.path_of(name)
    d = summary(name, path)
    d["agents"] = read(os.path.join(path, "AGENTS.md")) or read(os.path.join(path, "README.md")) \
        or read(os.path.join(path, "README.txt"))
    d["agentsFile"] = next((f for f in ("AGENTS.md", "README.md", "README.txt")
                            if os.path.exists(os.path.join(path, f))), "")
    d["log"] = parse_log(read(os.path.join(path, "LOG.md")))
    d["decisions"] = parse_decisions(read(os.path.join(path, "DECISIONS.md")))
    d["commits"], d["changes"] = [], []
    if d["git"]:
        raw = git(path, "log", "-40", "--format=%h%x1f%cs%x1f%an%x1f%s") or ""
        d["commits"] = [dict(zip(("hash", "date", "author", "subject"), l.split("\x1f")))
                        for l in raw.splitlines() if l]
        for l in (git(path, "status", "--porcelain") or "").splitlines():
            if l.strip():
                d["changes"].append({"code": l[:2].strip(), "file": l[3:]})
    briefs = os.path.join(path, "briefs")
    d["briefs"] = sorted((f for f in os.listdir(briefs) if f.endswith(".md") and f != "TEMPLATE.md"),
                         key=lambda f: int(re.sub(r"\D", "", f) or 0)) if os.path.isdir(briefs) else []
    d["proof"] = []
    proof = os.path.join(path, "proof")
    if os.path.isdir(proof):
        for root, _, files in os.walk(proof):
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(root, f), path).replace("\\", "/")
                ext = os.path.splitext(f)[1].lower()
                d["proof"].append({"path": rel, "kind": "image" if ext in IMAGE_EXT else "text"})
    # projects that keep proof files at their top level (verified-*.png / .json)
    for f in sorted(os.listdir(path)):
        if f.startswith("verified") and os.path.splitext(f)[1].lower() in IMAGE_EXT | TEXT_EXT:
            d["proof"].append({"path": f, "kind": "image" if os.path.splitext(f)[1].lower() in IMAGE_EXT else "text"})
    d["bakFiles"] = bak_files(path)
    d["path"] = path
    return d


def safe(name, rel):
    root = harness.path_of(name)
    if not root:
        return None
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, rel))
    return full if full.startswith(base + os.sep) and os.path.isfile(full) else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/apps":
                kick_index()
                tot = sessions.totals()
                out = []
                for n, p in harness.apps():
                    s = summary(n, p)
                    s["sessions"] = tot.get(n, {}).get("sessions", 0)
                    s["tokens"] = tot.get(n, {}).get("out", 0)
                    out.append(s)
                return self.send(200, json.dumps(out))
            if u.path == "/api/map":
                if q.get("app") not in dict(harness.apps()):
                    return self.send(404, "{}")
                return self.send(200, json.dumps(maps.load(q["app"]) or {"built": False, "graphify": bool(maps.graphify_exe())}))
            if u.path == "/api/fn":
                apps_ = dict(harness.apps())
                m = maps.load(q.get("app", "")) if q.get("app") in apps_ else None
                d = explain.detail(q["app"], apps_[q["app"]], m, q.get("id", "")) if m else None
                return self.send(200 if d else 404, json.dumps(d or {"error": "not found"}))
            if u.path == "/api/project":  # routes, hotspots, unused, tests, tours (static, cached per build)
                apps_ = dict(harness.apps())
                m = maps.load(q.get("app", "")) if q.get("app") in apps_ else None
                return self.send(200, json.dumps(project.overview(q["app"], apps_[q["app"]], m) if m else {}))
            if u.path == "/api/fn/history":  # git log -L + blame on just this function's lines
                apps_ = dict(harness.apps())
                m = maps.load(q.get("app", "")) if q.get("app") in apps_ else None
                d = explain.detail(q["app"], apps_[q["app"]], m, q.get("id", "")) if m else None
                if not d or "error" in d:
                    return self.send(404, json.dumps({"error": "not found"}))
                return self.send(200, json.dumps(project.history(apps_[q["app"]], m["fns"][q["id"]]["file"], d["start"], d["end"])))
            if u.path == "/map-html":
                full = os.path.join(maps.out_dir(q.get("app", "")), "graph.html")
                if q.get("app") in dict(harness.apps()) and os.path.isfile(full):
                    with open(full, "rb") as f:
                        return self.send(200, f.read(), "text/html; charset=utf-8")
                return self.send(404, "no map", "text/plain")
            if u.path == "/api/sessions":
                kick_index()
                return self.send(200, json.dumps({**sessions.status(), "sessions": sessions.for_app(q.get("app", ""))}))
            if u.path == "/api/app":
                if q.get("name") not in dict(harness.apps()):
                    return self.send(404, "{}")
                return self.send(200, json.dumps(detail(q["name"])))
            if u.path == "/api/notes":
                return self.send(200, json.dumps(notes.for_app(q.get("app") or None)))
            if u.path == "/file":
                full = safe(q.get("app", ""), q.get("p", ""))
                if not full:
                    return self.send(404, "not found", "text/plain")
                ctype = mimetypes.guess_type(full)[0] or "text/plain"
                if ctype.startswith("text") or full.endswith((".md", ".log", ".json")):
                    ctype = "text/plain; charset=utf-8"
                with open(full, "rb") as f:
                    return self.send(200, f.read(), ctype)
            rel = "index.html" if u.path == "/" else u.path.lstrip("/")
            full = os.path.realpath(os.path.join(UI, rel))
            if full.startswith(os.path.realpath(UI)) and os.path.isfile(full):
                with open(full, "rb") as f:
                    return self.send(200, f.read(), mimetypes.guess_type(full)[0] or "text/plain")
            self.send(404, "not found", "text/plain")
        except Exception as e:  # keep the window alive; show the error in the UI
            self.send(500, json.dumps({"error": str(e)}))


    def do_POST(self):
        # Only our own page may write: same-origin Host + a custom header a cross-site form can't set.
        if self.headers.get("X-Harness") != "1" or not self.headers.get("Host", "").startswith("127.0.0.1:"):
            return self.send(403, json.dumps({"error": "forbidden"}))
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            path = urlparse(self.path).path
            if path == "/api/notes/add":
                if body.get("app") not in dict(harness.apps()):
                    return self.send(400, json.dumps({"error": "unknown app"}))
                n = notes.add(body["app"], body.get("text"), body.get("kind", "note"),
                              body.get("round"), body.get("tab"), fn=body.get("fn"), fn_label=body.get("fnLabel"))
                return self.send(200, json.dumps(n))
            if path == "/api/notes/reply":
                return self.send(200, json.dumps(notes.reply(int(body["id"]), body.get("text", ""), "you")))
            if path == "/api/notes/status":
                if body.get("status") not in ("open", "done"):
                    return self.send(400, json.dumps({"error": "bad status"}))
                return self.send(200, json.dumps(notes.set_status(int(body["id"]), body["status"])))
            if path in ("/api/fn/quiz", "/api/fn/quiz/grade"):  # costs tokens: only on an explicit click
                apps_ = dict(harness.apps())
                m = maps.load(body.get("app", "")) if body.get("app") in apps_ else None
                d = explain.detail(body["app"], apps_[body["app"]], m, body.get("id", "")) if m else None
                if not d or "error" in d:
                    return self.send(404, json.dumps({"error": "not found"}))
                if path == "/api/fn/quiz":
                    return self.send(200, json.dumps(explain.quiz_make(body["app"], m, body["id"], d)))
                return self.send(200, json.dumps(explain.quiz_grade(body["app"], m, body["id"], d, body.get("answers") or [],
                                                                     "retry" if body.get("retry") else "questions")))
            if path == "/api/fn/explain":  # costs tokens: only ever runs on an explicit click
                apps_ = dict(harness.apps())
                m = maps.load(body.get("app", "")) if body.get("app") in apps_ else None
                d = explain.detail(body["app"], apps_[body["app"]], m, body.get("id", "")) if m else None
                if not d:
                    return self.send(404, json.dumps({"error": "not found"}))
                return self.send(200, json.dumps(explain.explain(body["app"], m, body["id"], d)))
            if path == "/api/open":  # browser mode: same as the window's Open folder / Full graph buttons
                api_ = Api()
                (api_.open_map if body.get("what") == "map" else api_.open_folder)(body.get("app", ""))
                return self.send(200, "{}")
            if path == "/api/map/build":
                apps_ = dict(harness.apps())
                if body.get("app") not in apps_:
                    return self.send(400, json.dumps({"error": "unknown app"}))
                return self.send(200, json.dumps(maps.build(body["app"], apps_[body["app"]])))
            if path == "/api/notes/delete":
                notes.delete(int(body["id"]))
                return self.send(200, "{}")
            self.send(404, "{}")
        except (KeyError, ValueError) as e:
            self.send(400, json.dumps({"error": str(e)}))


WINDOW = []  # kept off Api: pywebview recursively introspects js_api attributes


def _open(path):
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


class Api:
    def open_map(self, name):
        full = os.path.join(maps.out_dir(name), "graph.html")
        if harness.path_of(name) and os.path.isfile(full):
            _open(full)  # full force-directed graph in the default browser

    def open_folder(self, name):
        path = harness.path_of(name)
        if path:
            _open(path)


def kick_index():
    """Refresh the Claude Code session index in the background (it throttles itself to once a minute)."""
    threading.Thread(target=sessions.refresh, args=(harness.apps(),), daemon=True).start()


def main():
    kick_index()
    try:
        import webview
    except ImportError:
        webview = None
    serve = "--serve" in sys.argv or webview is None
    server = ThreadingHTTPServer(("127.0.0.1", PORT if serve else 0), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if serve:
        print(f"harness running at {url}  (Ctrl+C to stop)")
        if webview is None and "--serve" not in sys.argv:
            import webbrowser
            webbrowser.open(url)  # no pywebview: use the normal browser
        return server.serve_forever()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("harness.viewer")
    WINDOW.append(webview.create_window("Harness", url, js_api=Api(), width=1240, height=800,
                                        min_size=(860, 540), background_color="#0e0f11"))
    icon = os.path.join(HERE, "harness.ico")
    if os.path.isfile(icon):
        def set_icon():
            try:
                from System import Action
                from System.Drawing import Icon
                form = WINDOW[0].native
                form.BeginInvoke(Action(lambda: setattr(form, "Icon", Icon(icon))))
            except Exception:
                pass
        WINDOW[0].events.shown += set_icon
    profile = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "harness-viewer")
    webview.start(private_mode=False, storage_path=profile)


if __name__ == "__main__":
    main()
