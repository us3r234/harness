# harness

A local desktop viewer that explains a codebase and how it's been built. Point it at one repo
or a folder of repos. For each project it shows a **map of the code** (every function, what
triggers it, what it connects to, what it needs before it can run, and the code itself),
plus the project's change history, your notes, and optionally the Claude Code sessions that
worked on it.

Everything runs on your machine. Nothing is uploaded. The only part that uses an AI model is
the **Explain this** button, and only when you click it.

## Quick start

Needs Python 3.10+ and git.

```
git clone https://github.com/us3r234/harness.git
cd harness
python harness.py add C:\path\to\your-project        # one repo
python harness.py add-root C:\path\to\folder-of-repos   # or: every subfolder is a project
python app.py
```

`app.py` opens a desktop window if [pywebview](https://pywebview.flowrl.com/) is installed
(`pip install pywebview`). If it isn't, it opens the same page in your browser at
http://127.0.0.1:47640/. `python app.py --serve` always uses the browser.

For the **Map** tab, install graphify (code-only mode, no AI, no API key):

```
python -m pip install --user graphifyy
```

Note the double y. Packages named plain "graphify" are different projects.
On Windows, `run.bat` starts the window without a console.

## What each tab shows

| Tab | What it's for |
|---|---|
| **Map** | Core pieces (the most-connected functions), areas of code that work together, and one function in depth (see below). |
| **Changes** | Uncommitted files and the commit history (git). |
| **Timeline** / **Decisions** / **Briefs** / **Proof** | Filled in when a project uses the docs convention (below). |
| **Sessions** | Claude Code conversations that touched the project, read from `~/.claude/projects` (read-only). Empty if you don't use Claude Code. |
| **Notes** | Your notes and questions per project or per round. Stored in `data/notes.json` on this machine. |
| **About** | The project's AGENTS.md or README. |

Tap any dotted word in the window for a plain explanation of the term.

### The Map, one function in depth

Pick a function to see:

- **How it gets triggered**: the web route that runs it (Flask/FastAPI decorators like
  `@app.route("/tickets")`, or `if path == "/x"` blocks in `http.server` handlers), and the
  call chain from an entry point down to it.
- **How it connects**: a diagram of what calls it and what it calls, with each one's docstring.
  Front-end code that requests `/api/x` is linked to the Python function that answers `/api/x`.
- **Before it can run**: the modules it imports, programs it launches (git, ffmpeg...), files it
  opens, network calls, environment variables, settings/constants it reads, and for front-end
  code the backend addresses, page elements and browser storage it uses.
- **The code**, with line numbers.
- **Explain this** (optional): a plain-English write-up with five parts: in plain words,
  step by step, what it needs first, how it connects, and what to watch out for. It runs
  `claude -p` (Claude Code CLI, must be installed and signed in) with no tools enabled. Each
  answer is saved in `data/explain/` and flagged when the code changes.

Each card also shows **what goes in and comes out** (parameters, the path/query/JSON/form fields a
route reads, return values, HTTP status codes), **data it touches** (database tables read or changed,
from the SQL strings), **how hard to follow** (complexity, length, nesting), **when things go wrong**
(what it raises and catches, bare `except:` warnings), **tested by**, TODO/FIXME comments, its **git
history** and who wrote the current lines (`git log -L` + `git blame`), a path finder, and notes pinned
to that function.

Static analysis is best for Python. JavaScript gets a lighter pass.

### The Map, whole project

- **Routes**: every address the app answers, with its handler, the inputs it expects, the tables it
  reads/writes (following the call chain), and the page code that calls it.
- **Tours**: step-by-step walkthroughs generated from the call graph ("Start here", plus "Follow a
  request" for each route), with Back/Next and the full card at every step. The idea is from CodeTour.
- **Hotspots**: complexity × how often the file changes, the CodeScene idea that code which is both
  complex and busy is where problems collect.
- **Possibly unused**: nothing calls it, it isn't a route or an entry point, and its name appears
  nowhere else.
- **Search** (`/`) and **path finder**: how does A reach B, including page → server web requests.

## The docs convention (optional)

A project can keep three small files so its history is readable by anyone:

| File | Answers |
|---|---|
| `AGENTS.md` | How to run it, how it's laid out, and the rules nobody may break |
| `DECISIONS.md` | Why it's built this way: one dated line per decision |
| `LOG.md` | Each round of work: in plain words, what was asked, who built it, what changed, how it was verified |

`python harness.py adopt <project>` adds blank templates and never overwrites existing files.
`python harness.py round <project> "title"` starts a new round. `python harness.py` prints a
status table for every project.

## Privacy and safety

- The window only **reads** your projects. The only things it writes are its own files in
  `data/` (maps, notes, the session index, saved explanations) and `config.json`.
  `adopt` and `round` write to a project, and only when you run them.
- `config.json` and `data/` are gitignored, so paths, notes and maps of private code never get
  committed to this repo.
- graphify always runs with `GRAPHIFY_BACKEND=none`, so it can't call an AI service. Never run
  `graphify install`: it registers itself with your AI assistant and adds hooks. This tool doesn't need that.
- **Explain this** sends that one function's code to Claude through your own Claude Code
  sign-in. Don't use it on code you aren't allowed to share with an AI service.

## Files

`app.py` window and local server · `harness.py` projects, config, CLI · `maps.py` graphify
runner and graph reader · `explain.py` function detail and explanations · `sessions.py` Claude
Code log index · `notes.py` notes CLI · `ui/` the page · `template/` docs templates.
