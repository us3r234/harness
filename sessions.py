"""Index Claude Code's own session logs (~/.claude/projects/*/*.jsonl) by project. Read-only.

For each session: its title, and every turn (your prompt -> what Claude did -> Claude's reply)
that touched a configured project. "Touched" = a tool call edited a file there, or a
command/working folder pointed there. Tokens are read from each API response (deduped by
message id, like ccusage does) and split across the projects a turn touched.

The index lives in data/sessions.json; a log is re-read only when its size or mtime changes.
If you don't use Claude Code, there are no logs and the Sessions tab just says so.
"""
import glob
import json
import os
import re
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")
INDEX = os.path.join(HERE, "data", "sessions.json")
VERSION = 6
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
LOCK = threading.Lock()
_state = {"index": None, "building": False, "checked": 0}


def _variants(path):
    """The ways a tool may spell one folder: C:\\x\\y, C:/x/y, and Git Bash's /c/x/y (all lowercased)."""
    p = os.path.abspath(path).rstrip("\\/").lower()
    fwd = p.replace("\\", "/")
    out = {p, fwd}
    if len(fwd) > 2 and fwd[1] == ":":
        out.add("/" + fwd[0] + fwd[2:])
    return sorted(out, key=len, reverse=True)


def _known(projects):
    return [(name, _variants(path)) for name, path in projects]


def _apps_in(text, known):
    t, found = (text or "").lower(), set()
    if not t:
        return found
    for name, variants in known:
        for v in variants:
            i = t.find(v)
            while i >= 0:
                j = i + len(v)
                if j == len(t) or not (t[j].isalnum() or t[j] in "_-."):  # whole folder name, not a prefix of another
                    found.add(name)
                    break
                i = t.find(v, i + 1)
            if name in found:
                break
    return found


def _rel(path, app, known):
    p = path.replace("/", "\\")
    for name, variants in known:
        if name == app:
            for v in variants:
                v = v.replace("/", "\\")
                if p.lower().startswith(v + "\\"):
                    return p[len(v) + 1:]
    return os.path.basename(p)


def _prompt_text(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return None
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return None


def parse(path, known):
    """One session log -> {id, title, start, end, turns:[...]} keeping only turns that touched apps."""
    title, start, end, cwd, first_cwd = "", None, None, "", ""
    turns, cur, seen_ids = [], None, set()

    def close():
        if cur and cur["apps"]:
            cur["apps"] = sorted(cur["apps"])
            cur["files"] = {a: sorted(f)[:15] for a, f in cur["files"].items()}
            turns.append(cur)

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"type":"user"' not in line and '"type":"assistant"' not in line and '"ai-title"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("type")
            if t == "ai-title":
                title = d.get("aiTitle") or title
                continue
            if t not in ("user", "assistant"):
                continue
            ts = d.get("timestamp")
            if ts:
                start = start or ts
                end = ts
            cwd = d.get("cwd") or cwd
            first_cwd = first_cwd or cwd
            msg = d.get("message") or {}
            side = d.get("isSidechain")
            if t == "user" and not side and not d.get("isMeta"):
                text = _prompt_text(msg)
                if text is not None and text.strip() and not text.lstrip().startswith(("<", "[Request interrupted")):
                    close()
                    cur = {"at": ts, "prompt": text.strip()[:700], "apps": set(), "files": {}, "edits": {}, "cmds": {},
                           "tools": {}, "reply": "", "out": 0, "inp": 0, "cache": 0}
                    cur["apps"] |= _apps_in(text, known)
                continue
            if cur is None:
                continue
            cur["apps"] |= _apps_in(cwd, known) if not side else set()
            if t == "assistant" and isinstance(msg.get("content"), list):
                mid = msg.get("id")
                u = msg.get("usage")
                if u and mid not in seen_ids:
                    seen_ids.add(mid)
                    cur["out"] += u.get("output_tokens", 0)
                    cur["inp"] += u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                    cur["cache"] += u.get("cache_read_input_tokens", 0)
                for b in msg["content"]:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and not side and b.get("text", "").strip():
                        cur["reply"] = b["text"].strip()[:1500]
                    elif b.get("type") == "tool_use":
                        name, inp = b.get("name", ""), b.get("input") or {}
                        cur["tools"][name] = cur["tools"].get(name, 0) + 1
                        fp = inp.get("file_path") or inp.get("notebook_path") or ""
                        hits = _apps_in(fp, known) | _apps_in(inp.get("command", ""), known) \
                            | _apps_in(inp.get("path", ""), known)
                        cur["apps"] |= hits
                        if name in ("Bash", "PowerShell"):
                            for a in hits | _apps_in(cwd, known):
                                cur["cmds"][a] = cur["cmds"].get(a, 0) + 1
                        if name in EDIT_TOOLS and fp:
                            for a in _apps_in(fp, known):
                                cur["edits"][a] = cur["edits"].get(a, 0) + 1
                                cur["files"].setdefault(a, set()).add(_rel(fp, a, known))
    close()
    sid = os.path.splitext(os.path.basename(path))[0]
    return {"id": sid, "title": title, "start": start, "end": end, "cwd": first_cwd, "turns": turns}


def _load():
    try:
        with open(INDEX, encoding="utf-8") as f:
            idx = json.load(f)
        if idx.get("version") == VERSION:
            return idx
    except (OSError, ValueError):
        pass
    return {"version": VERSION, "files": {}}


def _save(idx):
    os.makedirs(os.path.dirname(INDEX), exist_ok=True)
    tmp = INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    os.replace(tmp, INDEX)


def refresh(projects):
    """Re-read logs that changed since the last index. Safe to call often (throttled to 60 s)."""
    with LOCK:
        if _state["building"] or (time.time() - _state["checked"] < 60 and _state["index"]):
            return
        _state["building"] = True
    try:
        known = _known(projects)
        idx = _state["index"] or _load()
        sig_projects = sorted(f"{n}={p}" for n, p in projects)
        if idx.get("projects") != sig_projects:  # project list changed: every log must be re-matched
            idx = {"version": VERSION, "files": {}, "projects": sig_projects}
        files = {p: os.stat(p) for p in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))}
        changed = False
        for p, st in files.items():
            sig = [st.st_size, int(st.st_mtime)]
            old = idx["files"].get(p)
            if old and old["sig"] == sig:
                continue
            try:
                idx["files"][p] = {"sig": sig, "session": parse(p, known)}
                changed = True
            except OSError:
                pass
        for p in [p for p in idx["files"] if p not in files]:
            del idx["files"][p]
            changed = True
        if changed:
            _save(idx)
        _state["index"] = idx
        _state["checked"] = time.time()
    finally:
        _state["building"] = False


def status():
    return {"ready": _state["index"] is not None, "building": _state["building"]}


def for_app(app):
    """Sessions that touched one app, newest first, each with only that app's turns."""
    idx = _state["index"]
    if not idx:
        return None
    out = []
    for p, entry in idx["files"].items():
        s = entry["session"]
        turns = []
        for t in s["turns"]:
            if app in t["apps"]:
                share = 1 / len(t["apps"])
                turns.append({"at": t["at"], "prompt": t["prompt"], "reply": t["reply"], "edits": t["edits"].get(app, 0), "cmds": t["cmds"].get(app, 0),
                              "files": t["files"].get(app, []), "tools": t["tools"], "others": [a for a in t["apps"] if a != app],
                              "out": round(t["out"] * share), "inp": round(t["inp"] * share), "cache": round(t["cache"] * share)})
        if turns:
            out.append({"id": s["id"], "title": s["title"] or turns[0]["prompt"][:60], "project": os.path.basename(os.path.dirname(p)), "cwd": s.get("cwd", ""),
                        "start": turns[0]["at"], "end": turns[-1]["at"], "turns": turns,
                        "out": sum(t["out"] for t in turns), "inp": sum(t["inp"] for t in turns),
                        "files": sorted({f for t in turns for f in t["files"]})})
    out.sort(key=lambda s: s["start"] or "", reverse=True)
    return out


def totals():
    """{app: {"sessions": n, "out": output tokens}} across every indexed session."""
    idx = _state["index"]
    if not idx:
        return {}
    tot = {}
    for entry in idx["files"].values():
        apps_here = set()
        for t in entry["session"]["turns"]:
            share = 1 / len(t["apps"])
            for a in t["apps"]:
                tot.setdefault(a, {"sessions": 0, "out": 0})["out"] += round(t["out"] * share)
                apps_here.add(a)
        for a in apps_here:
            tot[a]["sessions"] += 1
    return tot


if __name__ == "__main__":
    import sys
    sys.path.insert(0, HERE)
    import harness
    t = time.time()
    refresh(harness.apps())
    print(f"indexed in {time.time() - t:.1f}s")
    for a, v in sorted(totals().items(), key=lambda kv: -kv[1]["out"]):
        print(f"  {a:22s} {v['sessions']:3d} sessions  {v['out']:>10,} output tokens")
