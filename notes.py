"""Your notes and questions about each project. Stored in data/notes.json (gitignored, stays on this machine).

  python notes.py                         open questions (ask your AI assistant to run this and answer them)
  python notes.py all [app]               every note, newest first
  python notes.py reply <id> "text"       answer a question (marks it answered)
  python notes.py add <app> "text" [--round "Round 3: ..."] [--who assistant]
  python notes.py done <id>               close a note or question
"""
import datetime
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "data", "notes.json")
LOCK = threading.Lock()


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load():
    try:
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"version": 1, "next": 1, "notes": []}


def save(db):
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
    os.replace(tmp, FILE)  # atomic: a crash never leaves half a file


def add(app, text, kind="note", round_=None, tab=None, who="you", fn=None, fn_label=None):
    text = (text or "").strip()
    if not text:
        raise ValueError("empty note")
    with LOCK:
        db = load()
        note = {"id": db["next"], "app": app, "kind": kind if kind in ("note", "question") else "note",
                "round": round_ or None, "tab": tab or None, "fn": fn or None, "fnLabel": fn_label or None, "who": who, "text": text,
                "at": now(), "replies": [], "status": "open"}
        db["next"] += 1
        db["notes"].append(note)
        save(db)
        return note


def reply(note_id, text, who="assistant"):
    with LOCK:
        db = load()
        for n in db["notes"]:
            if n["id"] == note_id:
                n["replies"].append({"who": who, "text": text.strip(), "at": now()})
                if who == "assistant" and n["kind"] == "question":
                    n["status"] = "answered"
                elif who == "you" and n["kind"] == "question":
                    n["status"] = "open"  # a follow-up reopens it
                save(db)
                return n
    raise KeyError(note_id)


def set_status(note_id, status):
    with LOCK:
        db = load()
        for n in db["notes"]:
            if n["id"] == note_id:
                n["status"] = status
                save(db)
                return n
    raise KeyError(note_id)


def delete(note_id):
    with LOCK:
        db = load()
        db["notes"] = [n for n in db["notes"] if n["id"] != note_id]
        save(db)


def for_app(app=None):
    notes = load()["notes"]
    return [n for n in notes if app is None or n["app"] == app]


def _print(n):
    where = n["app"] + (f" · {n['round']}" if n["round"] else "") + (f" · {n['tab']} tab" if n["tab"] else "")
    print(f"#{n['id']} [{n['kind']}, {n['status']}] {where} ({n['at'][:16]}, {n['who']})")
    print("   " + n["text"].replace("\n", "\n   "))
    for r in n["replies"]:
        print(f"   ↳ {r['who']}: " + r["text"].replace("\n", "\n     "))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if not a:
        open_qs = [n for n in for_app() if n["kind"] == "question" and n["status"] == "open"]
        for n in open_qs:
            _print(n)
        print(f"{len(open_qs)} open question(s).")
    elif a[0] == "all":
        for n in reversed(for_app(a[1] if len(a) > 1 else None)):
            _print(n)
    elif a[0] == "reply" and len(a) == 3:
        _print(reply(int(a[1]), a[2]))
    elif a[0] == "done" and len(a) == 2:
        _print(set_status(int(a[1]), "done"))
    elif a[0] == "add" and len(a) >= 3:
        rnd = a[a.index("--round") + 1] if "--round" in a else None
        who = a[a.index("--who") + 1] if "--who" in a else "you"
        _print(add(a[1], a[2], round_=rnd, who=who))
    else:
        print(__doc__)
