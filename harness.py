"""harness: status of every project in config.json, adopt a project, start a round.

  python harness.py                         status table
  python harness.py add <path>              add a project (one repo) to config.json
  python harness.py add-root <path>         add a folder whose subfolders are each a project
  python harness.py adopt <project>         add AGENTS/DECISIONS/LOG templates (never overwrites)
  python harness.py round <project> "title" new briefs/roundN.md + LOG stub
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")   # per machine, gitignored (see config.example.json)
TEMPLATE = os.path.join(HERE, "template")
DOCS = ("AGENTS.md", "DECISIONS.md", "LOG.md")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("projects", [])
    cfg.setdefault("roots", [])
    return cfg


def save_config(cfg):
    with open(CONFIG, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, indent=2)


def git(app_dir, *args):
    r = subprocess.run(["git", "-C", app_dir, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    return r.stdout.strip() if r.returncode == 0 else None


def apps():
    """[(name, path)] for every configured project: explicit ones plus each subfolder of each root."""
    cfg, found = load_config(), []
    for p in cfg["projects"]:
        if os.path.isdir(p):
            found.append(os.path.abspath(p))
    for root in cfg["roots"]:
        if os.path.isdir(root):
            for name in sorted(os.listdir(root), key=str.lower):
                p = os.path.join(root, name)
                if os.path.isdir(p) and not name.startswith((".", "_")):
                    found.append(os.path.abspath(p))
    out, seen = [], set()
    for p in dict.fromkeys(found):
        name = os.path.basename(p.rstrip("\\/"))
        if name in seen:  # same folder name in two places: add the parent to tell them apart
            name = f"{os.path.basename(os.path.dirname(p))}-{name}"
        seen.add(name)
        out.append((name, p))
    return sorted(out, key=lambda x: x[0].lower())


def path_of(name):
    return dict(apps()).get(name)


def last_round(path):
    log = os.path.join(path, "LOG.md")
    if not os.path.exists(log):
        return ""
    with open(log, encoding="utf-8") as f:
        for line in f:
            if line.startswith("## "):
                return line[3:].strip()
    return ""


def status():
    rows = []
    for name, path in apps():
        is_git = os.path.isdir(os.path.join(path, ".git"))
        dirty = last = remote = ""
        if is_git:
            porcelain = git(path, "status", "--porcelain") or ""
            n = len([l for l in porcelain.splitlines() if l.strip()])
            dirty = f"{n} files" if n else "clean"
            last = git(path, "log", "-1", "--format=%cs") or "no commits"
            if git(path, "remote"):
                ahead = git(path, "rev-list", "--count", "@{u}..HEAD")
                remote = "pushed" if ahead == "0" else (f"{ahead} unpushed" if ahead else "no upstream")
            else:
                remote = "local only"
        docs = "".join(d[0] if os.path.exists(os.path.join(path, d)) else "-" for d in DOCS)
        bak = sum(1 for root, _, files in os.walk(path) if ".git" not in root
                  for f in files if ".bak" in f)
        rows.append((name, "yes" if is_git else "NO", dirty, last, remote, docs,
                     str(bak) if bak else "", last_round(path)[:46]))

    if not rows:
        print("No projects yet. Add one:  python harness.py add C:\\path\\to\\your\\repo")
        return
    head = ("project", "git", "uncommitted", "last commit", "remote", "A/D/L", ".bak", "last round")
    widths = [max(len(r[i]) for r in rows + [head]) for i in range(len(head))]
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*head))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*r))
    adopted = sum(1 for r in rows if r[5] == "ADL")
    print(f"\n{adopted}/{len(rows)} projects use the docs convention.  A/D/L = AGENTS / DECISIONS / LOG present.")


def add(path, key="projects"):
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        sys.exit(f"not a folder: {path}")
    cfg = load_config()
    if path not in cfg[key]:
        cfg[key].append(path)
        save_config(cfg)
    print(f"added to {key}: {path}")


def adopt(name):
    path = path_of(name)
    if not path:
        sys.exit(f"unknown project: {name} (see: python harness.py)")
    made = []
    for rel in ("AGENTS.md", "DECISIONS.md", "LOG.md", os.path.join("briefs", "TEMPLATE.md")):
        dst = os.path.join(path, rel)
        if not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(os.path.join(TEMPLATE, rel), encoding="utf-8") as f:
                text = f.read().replace("{app}", name)
            with open(dst, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            made.append(rel)
    os.makedirs(os.path.join(path, "proof"), exist_ok=True)
    gi = os.path.join(path, ".gitignore")
    if not os.path.exists(gi):
        shutil.copy(os.path.join(TEMPLATE, "gitignore"), gi)
        made.append(".gitignore")
    if not os.path.isdir(os.path.join(path, ".git")):
        git(path, "init", "-q", "-b", "master")
        made.append("git init")
    print(f"{name}: " + (", ".join(made) if made else "already has the docs, nothing changed"))
    print("Fill in AGENTS.md, then commit.")


def new_round(name, title):
    path = path_of(name)
    if not path:
        sys.exit(f"unknown project: {name}")
    briefs = os.path.join(path, "briefs")
    if not os.path.exists(os.path.join(path, "LOG.md")):
        sys.exit(f"{name} isn't on the harness yet; run: python harness.py adopt {name}")
    os.makedirs(briefs, exist_ok=True)
    nums = [int(m.group(1)) for f in os.listdir(briefs) if (m := re.match(r"round(\d+)\.md$", f))]
    n = max(nums, default=0) + 1
    with open(os.path.join(TEMPLATE, "briefs", "TEMPLATE.md"), encoding="utf-8") as f:
        brief = f.read().replace("{app}", name).replace("{n}", str(n)).replace("{title}", title)
    brief_path = os.path.join(briefs, f"round{n}.md")
    with open(brief_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(brief)
    os.makedirs(os.path.join(path, "proof", f"round{n}"), exist_ok=True)

    log = os.path.join(path, "LOG.md")
    with open(log, encoding="utf-8") as f:
        text = f.read()
    entry = (f"## Round {n}: {title} ({datetime.date.today()})\n"
             f"- **In plain words:** \n- **Asked:** \n- **Built by:** \n- **Changed:** \n"
             f"- **Verified:** (who, how; proof in `proof/round{n}/`)\n- **Open:** \n\n")
    i = text.find("\n## ")
    text = text + entry if i < 0 else text[:i + 1] + entry + text[i + 1:]
    with open(log, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"round {n}: {brief_path}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        status()
    elif a[0] == "add" and len(a) == 2:
        add(a[1])
    elif a[0] == "add-root" and len(a) == 2:
        add(a[1], "roots")
    elif a[0] == "adopt" and len(a) == 2:
        adopt(a[1])
    elif a[0] == "round" and len(a) == 3:
        new_round(a[1], a[2])
    else:
        print(__doc__)
