// Harness viewer. Reads /api/apps + /api/app; the only writes are your notes (/api/notes/*).
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const api = p => fetch(p).then(r => r.json());
const post = (p, body) => fetch(p, {method: "POST", headers: {"Content-Type": "application/json", "X-Harness": "1"},
  body: JSON.stringify(body)}).then(r => r.json());

let apps = [], allNotes = [], filter = "all", view = "overview", current = null, tab = "timeline", detail = null, sub = null;
let openRounds = new Set(), sess = null, openSess = new Set();
let mapData = null, mapSel = null, mapBusy = false, mapOpenAreas = new Set();
const quiz = {};  // "app|id" -> quiz state (answers live only in this page, never saved)
let proj = null, mapView = "explore", mapQuery = "", pathFrom = null, pathTo = null, tourIdx = null, tourStep = 0;
const fnHist = {};  // "app|id" -> git history of that function

// ---- words: tap a dotted word to see what it means and what's underneath ----
const TERMS = {
  git: ["A history of every saved version of the app's files.", "Each app folder has a hidden <code>.git</code> folder holding snapshots. Any snapshot can be brought back."],
  commit: ["One saved snapshot, with a message saying what changed.", "<code>git commit</code> records the staged files plus a message, the author and the time, under a short id like <code>495a95f</code>."],
  uncommitted: ["Files changed since the last snapshot. If they break, there's nothing to roll back to.", "<code>git status</code> lists them. <b>M</b> = modified, <b>new</b> = never saved before, <b>D</b> = deleted."],
  remote: ["A second copy of the history on another machine.", "<code>git remote -v</code> shows where it is (GitHub, a server, another PC). <code>git push</code> sends commits there; anyone else runs <code>git pull</code> to get them."],
  pushed: ["Every snapshot here also exists on the remote.", "<code>git rev-list @{u}..HEAD</code> counts commits that are local only. 0 means pushed."],
  "local only": ["The history exists only on this PC. If the drive dies, it's gone.", "There's no <code>git remote</code> set up yet."],
  round: ["One request from you, built and checked, start to finish.", "Ask → Brief → Build → Verify → Record. Each round gets a LOG entry, a brief, and a proof folder."],
  brief: ["The written spec handed to the builder before it starts.", "Lives in <code>briefs/roundN.md</code>: what to build, which files it may touch, how it'll be checked."],
  proof: ["Evidence that it works: screenshots, selftest output, logs.", "Lives in <code>proof/roundN/</code>. If there's no proof, the round's 'verified' claim has nothing behind it."],
  verified: ["Someone ran the real app and saw it work.", "Not 'the code looks right' or 'simulated checks pass'. The LOG says who ran it and how."],
  bak: ["Hand-made backup copies like <code>app.py.bak-r3</code>.", "The pre-git way of keeping old versions. git replaces these: <code>git log</code> + <code>git checkout</code> does the same with no clutter."],
  "AGENTS.md": ["The app's instruction sheet: how to run it, how it's laid out, and the rules nobody may break.", "People and AI assistants (Claude Code, Codex, Copilot) read it before changing anything."],
  "DECISIONS.md": ["Why the app is built the way it is. One dated line per choice.", "When something seems odd later, the reason is here, not in someone's memory."],
  "LOG.md": ["The story of the app, round by round.", "Each entry: plain words, what you asked, who built it, what changed, how it was verified."],
  harness: ["A shared way to record how each project gets built, so you can read any of them the same way.", "Conventions in this tool's README.md. The window reads each project's files and git history and never changes the code."],
  session: ["One Claude Code conversation, from opening the terminal to closing it.", "Claude Code saves every session as a <code>.jsonl</code> file in <code>~/.claude/projects/</code>. The harness reads those (read-only) and keeps the turns that touched this app."],
  tokens: ["How much text Claude wrote. Roughly 4 characters per token. It's the main thing that uses up your plan.", "Read from each response's <code>usage.output_tokens</code>, counted once per message (like ccusage). When one turn touched several apps, it's split evenly between them."],
  turn: ["One message from you plus everything Claude did before answering.", "Your prompt → the tool calls (edits, commands) → Claude's reply."],
  map: ["A picture of the app's code: which pieces exist and which ones use each other.", "Built by graphify in code-only mode: tree-sitter reads the source files on this PC (no AI, no tokens) and records every function and every call between them. Areas are groups of pieces that call each other a lot."],
  hub: ["A piece lots of other code depends on. Change it and many things feel it.", "Ranked by connections: how many distinct pieces it calls plus how many call it (graphify calls these god nodes)."],
  impact: ["What could change behaviour if you edit this function.", "Everything that calls it, the callers of those, and so on, plus the page code that reaches it through a web request, the addresses and buttons at the top of those chains, and the tests that touch any of them."],
  clickpath: ["What really happens when someone uses the page: the button, the page code, the web request, the server code, the database.", "Found by reading on*= attributes in HTML and addEventListener / .onclick in JS, then following calls and web requests through the Map."],
  security: ["Common ways code gets attacked or leaks data, found by reading it.", "SQL built by gluing in values (injection), routes with no login check, shell=True commands, eval, secrets in code, unescaped HTML (XSS), certificate checks off, debug mode, swallowed errors. Leads to check, not proof."],
  explain: ["A plain-English walkthrough of this one function: what it does, step by step, what it needs, how it connects, and what to watch out for.", "Written by Claude from the function's code plus the facts on this card (routes, callers, prerequisites). Only runs when you click, and the answer is saved in <code>data/explain/</code>."],
  trigger: ["What makes this code run.", "Either a web address the app answers (a <b>route</b> like <code>GET /api/probe</code>), or a chain of calls that starts at an entry point."],
  entry: ["Code nothing else in the project calls. Something outside starts it.", "For example <code>main()</code> when the app launches, a click handler, a timer, or the browser requesting a page."],
  prereq: ["What has to exist before this code can work.", "Found by reading the code: imports, programs it launches (<code>subprocess</code>), files it opens, network calls, environment variables, settings, and for the page side the backend addresses it calls."],
  route: ["A web address the app answers, like GET /api/tickets.", "The verb (GET reads, POST creates, PUT/PATCH change, DELETE removes) plus the path. Found from Flask/FastAPI decorators or http.server path checks."],
  hotspot: ["Code that's both hard to follow and changed often.", "Score = complexity × number of commits that touched the file (last 500). The idea is from CodeScene: research finds change frequency is the strongest single predictor of quality problems."],
  complexity: ["How many different paths the code can take. More paths means more to keep in your head and more to test.", "Cyclomatic complexity: 1 + each if, loop, except, and/or, and comprehension. Rated with length and nesting depth: simple, moderate, complex, very complex."],
  tables: ["Database tables this code reads or changes.", "Found by reading the SQL strings in the function (SELECT ... FROM, JOIN, INSERT INTO, UPDATE, DELETE FROM). ORM calls aren't detected."],
  "loose ends": ["Things a round said were still open.", "The <b>Open</b> line of each app's newest LOG entry."],
};
const fmtTok = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n || 0);
const localDay = iso => { const d = new Date(iso); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const term = (key, label) => `<span class="term" data-term="${esc(key)}">${esc(label || key)}</span>`;

// ---- status rules (one place) ----
function state(a) {
  if (!a.git) return {cls: "bad", text: "not in git"};
  if (a.dirty) return {cls: "warn", text: `${a.dirty} uncommitted`};
  if (!a.adopted) return {cls: "warn", text: "missing docs"};
  return {cls: "ok", text: "clean"};
}
function verified(text) {
  if (!text || /not recorded|^\(who/i.test(text)) return {cls: "warn", text: "unverified"};
  return {cls: "ok", text: "verified"};
}
function builders(text) {
  const out = [];
  for (const [re, name] of [[/claude/i, "Claude"], [/codex/i, "Codex"], [/copilot/i, "Copilot"], [/cursor/i, "Cursor"]])
    if (re.test(text || "")) out.push(`<span class="chip ${name.toLowerCase()}"><i></i>${name}</span>`);
  return out.join("");
}
const roundNum = title => (title.match(/round\s*(\d+)/i) || [])[1];
const when = iso => { const d = new Date(iso); return d.toLocaleDateString(undefined, {month: "short", day: "numeric"}) + " " + d.toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"}); };

// ---- tiny markdown ----
function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1");
}
function md(src) {
  const lines = (src || "").replace(/\r/g, "").split("\n"), out = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^```/.test(l)) {
      const buf = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`); i++; continue;
    }
    const h = l.match(/^(#{1,4})\s+(.*)/);
    if (h) { const n = Math.min(h[1].length, 3); out.push(`<h${n}>${inline(h[2])}</h${n}>`); i++; continue; }
    if (/^\|/.test(l)) {
      const rows = [];
      while (i < lines.length && /^\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = r => r.replace(/^\||\|$/g, "").split("|").map(c => inline(c.trim()));
      const body = rows.filter((r, k) => k !== 1 || !/^[\s|:-]+$/.test(r));
      out.push("<table>" + body.map((r, k) => "<tr>" + cells(r).map(c => k ? `<td>${c}</td>` : `<th>${c}</th>`).join("") + "</tr>").join("") + "</table>");
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s/.test(l)) {
      const ordered = /^\s*\d+\./.test(l), items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s/.test(lines[i])) {
        let item = lines[i++].replace(/^\s*([-*]|\d+\.)\s/, "");
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+\.)\s/.test(lines[i])) item += " " + lines[i++].trim();
        items.push(`<li>${inline(item)}</li>`);
      }
      out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`); continue;
    }
    if (!l.trim()) { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#|```|\||\s*([-*]|\d+\.)\s)/.test(lines[i])) para.push(lines[i++]);
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }
  return `<div class="md">${out.join("")}</div>`;
}

// ---- sidebar ----
function renderSide() {
  const list = apps.filter(a => filter === "all" || (filter === "on" ? a.adopted : state(a).cls !== "ok"));
  $("#applist").innerHTML = list.map(a => {
    const answered = allNotes.filter(n => n.app === a.name && n.kind === "question" && n.status === "answered").length;
    return `<button class="app ${a.name === current && view === "app" ? "on" : ""}" data-app="${esc(a.name)}" title="${esc(state(a).text)}">
      <span class="dot ${state(a).cls}"></span><span class="nm">${esc(a.name)}</span>
      ${answered ? `<span class="ans" title="${answered} answered question(s)">${answered}</span>` : ""}
      <span class="n">${a.rounds || ""}</span>
    </button>`;
  }).join("") || `<div class="faint" style="padding:8px">none</div>`;
  $("#nav-overview").classList.toggle("on", view === "overview");
  $("#nav-guide").classList.toggle("on", view === "guide");
  document.querySelectorAll("#filters button").forEach(b => b.classList.toggle("on", b.dataset.f === filter));
}

// ---- overview ----
function renderOverview() {
  if (!apps.length) {
    $("#main").innerHTML = `<div class="wrap"><h1>No projects yet</h1>
      <div class="sub" style="margin-top:8px">Point the harness at a repo (one project) or at a folder full of repos:</div>
      <pre class="file">python harness.py add C:\\path\\to\\your-project
python harness.py add-root C:\\path\\to\\folder-of-repos</pre>
      <div class="sub">Then press <b>r</b> to refresh. Paths are saved in <code>config.json</code> on this machine only.</div></div>`;
    return;
  }
  const on = apps.filter(a => a.adopted).length, noGit = apps.filter(a => !a.git).length;
  const dirty = apps.reduce((n, a) => n + a.dirty, 0), bak = apps.reduce((n, a) => n + a.bak, 0);
  const maxDirty = Math.max(1, ...apps.map(a => a.dirty));
  const openQ = allNotes.filter(n => n.kind === "question" && n.status === "open").length;
  const recent = allNotes.filter(n => n.status !== "done").slice().reverse().slice(0, 6);
  const loose = apps.filter(a => a.open);
  $("#main").innerHTML = `<div class="wrap">
    <h1>All projects</h1>
    <div class="sub">Every project in <code>config.json</code>. Tap any dotted word for what it means.</div>
    <div class="kpis">
      <div class="kpi"><div class="v">${apps.length}</div><div class="l">projects</div></div>
      <div class="kpi"><div class="v"><span class="dot ok"></span>${on}</div><div class="l">use the ${term("harness", "docs convention")}</div></div>
      <div class="kpi"><div class="v"><span class="dot bad"></span>${noGit}</div><div class="l">not in ${term("git")}</div></div>
      <div class="kpi"><div class="v"><span class="dot ${dirty ? "warn" : "ok"}"></span>${dirty}</div><div class="l">${term("uncommitted")} files</div></div>
      <div class="kpi"><div class="v">${bak}</div><div class="l">${term("bak", ".bak")} copies</div></div>
      <div class="kpi"><div class="v">${openQ ? `<span class="dot warn"></span>` : ""}${openQ}</div><div class="l">open questions</div></div>
    </div>
    ${recent.length ? `<h2>Your notes</h2>${recent.map(n => noteCard(n, true)).join("")}` : ""}
    ${loose.length ? `<h2>${term("loose ends", "Loose ends")}</h2><div class="loose">${loose.map(a => `
      <div class="lrow" data-app="${esc(a.name)}"><b>${esc(a.name)}</b><span>${inline(a.open)}</span><em>${esc(a.openRound)}</em></div>`).join("")}</div>` : ""}
    <h2>Apps</h2>
    <table><thead><tr><th>App</th><th>State</th><th style="text-align:right">Uncommitted</th><th class="hide-n">Remote</th><th>Docs</th><th style="text-align:right">${term("session", "Sessions")}</th><th>Last activity</th><th>Last round</th></tr></thead>
    <tbody>${apps.map(a => {
      const s = state(a);
      const docs = ["AGENTS.md", "DECISIONS.md", "LOG.md"].map(d => `<span class="${a.docs[d] ? "y" : "n"}" title="${d}">${a.docs[d] ? d[0] : "-"}</span>`).join("");
      return `<tr data-app="${esc(a.name)}">
        <td><strong>${esc(a.name)}</strong></td>
        <td><span class="st"><span class="dot ${s.cls}"></span>${esc(s.text)}</span></td>
        <td class="num barcell">${a.dirty ? `<div class="bar" style="width:${Math.round(a.dirty / maxDirty * 100)}%"></div><span>${a.dirty}</span>` : `<span class="faint">${a.git ? 0 : ""}</span>`}</td>
        <td class="muted hide-n">${esc(a.remote)}</td>
        <td class="docs">${docs}</td>
        <td class="num" title="${fmtTok(a.tokens)} tokens written by Claude Code">${a.sessions || `<span class="faint">0</span>`}</td>
        <td class="mono muted">${esc(a.last || a.touched || "")}</td>
        <td class="muted lr" title="${esc(a.lastRound)}">${esc(a.lastRound)}</td></tr>`;
    }).join("")}</tbody></table>
  </div>`;
}

// ---- guide: the whole idea as one picture ----
const STEPS = [
  ["Ask", "You say what you want, in your own words.", "Saved as the round's <b>Asked</b> line, word for word."],
  ["Brief", "The spec gets written before anything gets built.", "<code>briefs/roundN.md</code>: what to build, files allowed, and how it'll be checked."],
  ["Build", "A person or an AI assistant writes the code.", "<b>Built by</b> says who. An assistant's raw output can be saved in proof."],
  ["Verify", "Someone runs the real app and watches it work.", "Green <b>verified</b> badge = a real run. Amber = nobody recorded a check."],
  ["Record", "The LOG entry, the decisions, then a commit.", "One git snapshot per round, so any round can be rolled back."],
];
function stepStrip(lit) {
  return `<div class="steps">${STEPS.map(([s], i) => `<span class="step ${lit[i] ? "lit" : ""}" title="${s}${lit[i] ? "" : ": nothing recorded"}">${s}</span>`).join("")}</div>`;
}
function renderGuide() {
  $("#main").innerHTML = `<div class="wrap">
    <h1>How the harness works</h1>
    <div class="sub">Every app gets built in ${term("round", "rounds")}. Each round goes through the same five steps.</div>
    <div class="loop">${STEPS.map(([s, plain, under], i) => `
      <div class="lstep"><div class="ln">${i + 1}</div><div class="lt">${s}</div><div class="lp">${plain}</div><div class="lu">${under}</div></div>
      ${i < STEPS.length - 1 ? `<div class="arrow"><svg viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></svg></div>` : ""}`).join("")}
    </div>
    <h2>Where each step lives</h2>
    <div class="where">
      <div><b>Timeline tab</b><span>One card per round. The five-step strip lights up for each step that left a record.</span></div>
      <div><b>Sessions tab</b><span>The real Claude Code conversations behind the app: what you asked, what Claude did, what it said, and the tokens it used.</span></div>
      <div><b>Map tab</b><span>A picture of the code: core pieces, areas, and for any piece what it uses and what uses it. Built by graphify on this PC, no AI.</span></div>
      <div><b>Decisions tab</b><span>Why it's built that way. Opening a round also shows the decisions made that day.</span></div>
      <div><b>Changes tab</b><span>The ${term("git")} side: ${term("uncommitted")} files and every ${term("commit")}.</span></div>
      <div><b>Briefs tab</b><span>The exact ${term("brief", "specs")} the builder got.</span></div>
      <div><b>Proof tab</b><span>Screenshots and logs behind each ${term("verified")} badge.</span></div>
      <div><b>Notes tab</b><span>Your notes and questions. Ask your AI assistant to run <code>python notes.py</code> and answer the open questions; answers show up here.</span></div>
    </div>
    <h2>Colors</h2>
    <div class="where">
      <div><b><span class="dot ok"></span> green</b><span>On the harness, everything committed. Or: a round someone actually ran.</span></div>
      <div><b><span class="dot warn"></span> amber</b><span>Something's waiting: uncommitted work, missing docs, an unverified round, or an open question.</span></div>
      <div><b><span class="dot bad"></span> red</b><span>Not in git, so there's no history and nothing to roll back to.</span></div>
    </div>
  </div>`;
}

// ---- notes ----
function noteCard(n, showApp) {
  const isQ = n.kind === "question";
  const badge = isQ ? (n.status === "answered" ? `<span class="ver ok">answered</span>` : n.status === "done" ? `<span class="ver mute">closed</span>` : `<span class="ver warn">waiting for an answer</span>`)
    : n.status === "done" ? `<span class="ver mute">closed</span>` : "";
  const where = [showApp && `<button class="link" data-app="${esc(n.app)}">${esc(n.app)}</button>`, n.round && esc(n.round), n.fnLabel && `on <code>${esc(n.fnLabel)}</code>`, !n.fnLabel && n.tab && esc(n.tab) + " tab"].filter(Boolean).join(" · ");
  return `<div class="note ${n.who} ${n.status}" data-note="${n.id}">
    <div class="nh"><span class="kind">${isQ ? "Question" : n.who === "assistant" ? "Note from the assistant" : "Note"}</span>${badge}<span class="nw">${where}</span><span class="nt">${when(n.at)}</span></div>
    <div class="nb">${esc(n.text).replace(/\n/g, "<br>")}</div>
    ${n.replies.map(r => `<div class="reply ${r.who}"><span class="rw">${r.who === "assistant" ? "Assistant" : "You"}</span>${inline(r.text).replace(/\n/g, "<br>")}</div>`).join("")}
    <div class="nact">
      ${n.status !== "done" ? `<button data-follow="${n.id}">${isQ ? "Follow-up" : "Add"}</button><button data-close="${n.id}">Close</button>` : `<button data-reopen="${n.id}">Reopen</button>`}
      <button data-del="${n.id}" class="del">Delete</button>
    </div>
  </div>`;
}
function composer(ctx) {
  return `<div class="composer" data-ctx="${esc(JSON.stringify(ctx))}">
    <div class="ctop"><div class="seg"><button data-kind="note" class="on">Note</button><button data-kind="question">Question</button></div>
      <span class="cctx">${esc([ctx.round, ctx.tab && ctx.tab + " tab"].filter(Boolean).join(" · ") || "about " + ctx.app)}</span></div>
    <textarea placeholder="Write it however it comes out. Ctrl+Enter saves." rows="3"></textarea>
    <div class="cbot"><span class="faint">Stored on this machine only. An AI assistant can answer questions with <code>python notes.py</code>.</span><button class="btn primary" data-save>Save</button></div>
  </div>`;
}
async function saveComposer(c) {
  const ta = $("textarea", c), text = ta.value.trim();
  if (!text) return ta.focus();
  const ctx = JSON.parse(c.dataset.ctx), kind = $(".seg .on", c).dataset.kind;
  await post("/api/notes/add", {...ctx, kind, text});
  await loadNotes();
  rerender();
}

// ---- one app ----
const TABS = [["timeline", "Timeline"], ["sessions", "Sessions"], ["map", "Map"], ["decisions", "Decisions"], ["changes", "Changes"], ["briefs", "Briefs"], ["proof", "Proof"], ["notes", "Notes"], ["about", "About"]];
const appNotes = () => allNotes.filter(n => n.app === current);

function counts(d) {
  return {timeline: d.log.length, sessions: sess && sess.sessions ? sess.sessions.length : "", decisions: d.decisions.length, changes: d.changes.length + d.commits.length,
          briefs: d.briefs.length, proof: d.proof.length, notes: appNotes().filter(n => n.status !== "done").length, about: ""};
}

function renderApp() {
  const d = detail, s = state(d), c = counts(d);
  const missing = [!d.git && "git", ...["AGENTS.md", "DECISIONS.md", "LOG.md"].filter(f => !d.docs[f])].filter(Boolean);
  const verifiedRounds = d.log.filter(r => verified(r.fields.Verified).cls === "ok").length;
  $("#main").innerHTML = `<div class="wrap">
    <div class="head"><div class="grow"><h1>${esc(d.name)}</h1><div class="sub mono">${esc(d.path)}</div></div>
      <button class="btn" data-newnote>+ Note <span class="faint">N</span></button><button class="btn" id="open">Open folder</button></div>
    <div class="kpis">
      <div class="kpi"><div class="v"><span class="dot ${s.cls}"></span>${d.git ? (d.dirty ? d.dirty : "clean") : "none"}</div><div class="l">${d.git ? (d.dirty ? term("uncommitted") + " files" : "working tree") : term("git")}</div></div>
      <div class="kpi"><div class="v">${d.log.length}</div><div class="l">${term("round", "rounds")} logged</div></div>
      <div class="kpi"><div class="v"><span class="dot ${verifiedRounds === d.log.length && d.log.length ? "ok" : d.log.length ? "warn" : ""}"></span>${verifiedRounds}/${d.log.length}</div><div class="l">rounds ${term("verified")}</div></div>
      <div class="kpi"><div class="v">${d.decisions.length}</div><div class="l">decisions</div></div>
      <div class="kpi"><div class="v" style="font-size:16px;padding:4px 0">${d.remote ? term(d.remote.includes("unpushed") ? "remote" : d.remote, d.remote) : "—"}</div><div class="l">${term("remote")}</div></div>
      <div class="kpi"><div class="v">${d.bak}</div><div class="l">${term("bak", ".bak")} copies</div></div>
      <div class="kpi"><div class="v">${sess && sess.sessions ? sess.sessions.length : "…"}</div><div class="l">${term("session", "sessions")} · ${sess && sess.sessions ? fmtTok(sess.sessions.reduce((n, s) => n + s.out, 0)) : "…"} ${term("tokens")}</div></div>
    </div>
    ${missing.length ? `<div class="notice"><b>Optional:</b> this project doesn't use the ${term("harness", "docs convention")} (missing ${missing.join(", ")}). Timeline and Decisions fill in once it does: <code>python harness.py adopt ${esc(d.name)}</code></div>` : ""}
    <div class="tabs">${TABS.map(([k, label], i) => `<button class="tab ${k === tab ? "on" : ""}" data-tab="${k}" title="${i + 1}">${label}<span class="c">${c[k] || ""}</span></button>`).join("")}</div>
    <div class="pane" id="pane"></div>
  </div>`;
  $("#open").onclick = () => openThing(d.name, "folder");
  renderPane();
}

function roundCard(r, i) {
  const d = detail, f = r.fields, v = verified(f.Verified), n = roundNum(r.title);
  const open = openRounds.has(r.title);
  const proofs = n ? d.proof.filter(p => p.path.startsWith(`proof/round${n}/`)) : [];
  const lit = [!!f.Asked, n && d.briefs.includes(`round${n}.md`), !!f["Built by"], v.cls === "ok", !!f.Changed && d.git];
  const decs = d.decisions.filter(x => x.date === r.date);
  const rnotes = appNotes().filter(x => x.round === r.title);
  const rsess = (sess && sess.sessions || []).filter(s => s.turns.some(t => localDay(t.at) === r.date));
  const rtok = rsess.reduce((n, s) => n + s.turns.filter(t => localDay(t.at) === r.date).reduce((m, t) => m + t.out, 0), 0);
  const rows = ["Asked", "Built by", "Changed", "Verified", "Open"].filter(k => f[k]).map(k =>
    `<dt>${k === "Verified" ? term("verified", "Verified") : k}</dt><dd>${k === "Built by" ? `<div class="who" style="margin-bottom:3px">${builders(f[k])}</div>` : ""}${k === "Built by" && !/[a-z]{3,}/i.test(f[k].replace(/claude|codex|copilot|cursor|and|\W/gi, "")) ? "" : inline(f[k])}</dd>`).join("");
  return `<div class="round ${open ? "open" : ""}"><div class="date">${esc(r.date)}</div><div class="rbody">
    <button class="rhead" data-round="${esc(r.title)}">
      <span class="t">${esc(r.title)} <span class="ver ${v.cls}">${v.text}</span>${rnotes.length ? `<span class="nc">${rnotes.length} note${rnotes.length > 1 ? "s" : ""}</span>` : ""}</span>
      ${f["In plain words"] ? `<span class="plain">${inline(f["In plain words"])}</span>` : ""}
      ${stepStrip(lit)}
      <span class="chev">${open ? "Hide details" : "Details"}</span>
    </button>
    ${open ? `<dl>${rows}</dl>
      ${rsess.length ? `<div class="rsec"><div class="rsl">Built in</div><button class="link" data-gosess>${rsess.length} ${term("session", rsess.length > 1 ? "sessions" : "session")} that day · ${fmtTok(rtok)} ${term("tokens")}</button> <span class="faint">(${rsess.map(s => esc(s.title)).join(" · ")})</span></div>` : ""}
      ${decs.length ? `<div class="rsec"><div class="rsl">Decided that day</div>${decs.map(x => `<div class="rdec"><strong>${inline(x.decision)}</strong> <span class="muted">${inline(x.why)}</span></div>`).join("")}</div>` : ""}
      ${proofs.length ? `<div class="rsec"><div class="rsl">${term("proof", "Proof")}</div>${proofs.map(p => `<button class="link" data-goproof>${esc(p.path)}</button>`).join(" ")}</div>` : ""}
      <div class="rsec"><div class="rsl">Your notes on this round</div>${rnotes.map(x => noteCard(x)).join("")}
        ${composer({app: d.name, round: r.title, tab: null})}</div>` : ""}
  </div></div>`;
}

function turnView(t) {
  const tools = Object.entries(t.tools).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ×${v}`).join(" · ");
  return `<div class="turn">
    <div class="tt mono faint">${new Date(t.at).toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"})} · ${fmtTok(t.out)} tokens${t.others.length ? ` · also touched ${t.others.map(esc).join(", ")}` : ""}</div>
    <div class="you"><span class="rw">You</span><div class="clamp">${esc(t.prompt).replace(/\n/g, "<br>")}</div></div>
    <div class="did"><span class="rw">Claude did</span>${tools || "nothing"}${t.files.length ? `<div>${t.files.map(f => `<code>${esc(f)}</code>`).join(" ")}</div>` : ""}</div>
    <div class="said"><span class="rw">Claude said</span>${t.reply ? `<div class="clamp">${md(t.reply)}</div>` : `<span class="faint">No reply text in this turn. It ended in a question to you or got interrupted.</span>`}</div>
  </div>`;
}

function openThing(app, what) {
  if (window.pywebview) return what === "map" ? window.pywebview.api.open_map(app) : window.pywebview.api.open_folder(app);
  post("/api/open", {app, what});
}

// ---- map: areas + a "who uses what" picture for the selected piece ----
function egoSvg(m, f) {
  const left = f.callers.slice(0, 7), right = f.calls.slice(0, 7);
  const W = 760, rowH = 30, H = Math.max(3, left.length, right.length) * rowH + 20;
  const cy = H / 2, cx = W / 2, lx = 150, rx = W - 150;
  const y = (i, n) => cy + (i - (n - 1) / 2) * rowH;
  const box = (x, yy, id, cls) => {
    const lab = m.fns[id].label, w = Math.min(230, lab.length * 7.4 + 22);
    return `<g class="enode ${cls}" data-fn="${esc(id)}"><rect x="${x - w / 2}" y="${yy - 12}" width="${w}" height="24" rx="4"/><text x="${x}" y="${yy + 4}">${esc(lab)}</text></g>`;
  };
  let s = `<svg viewBox="0 0 ${W} ${H}" class="ego" role="img" aria-label="what ${esc(f.label)} uses and what uses it">`;
  left.forEach((id, i) => { s += `<path d="M${lx + 60} ${y(i, left.length)} C ${cx - 120} ${y(i, left.length)}, ${cx - 140} ${cy}, ${cx - 100} ${cy}" class="eline in"/>`; });
  right.forEach((id, i) => { s += `<path d="M${cx + 100} ${cy} C ${cx + 140} ${cy}, ${cx + 120} ${y(i, right.length)}, ${rx - 60} ${y(i, right.length)}" class="eline out"/>`; });
  left.forEach((id, i) => { s += box(lx, y(i, left.length), id, "side"); });
  right.forEach((id, i) => { s += box(rx, y(i, right.length), id, "side"); });
  s += box(cx, cy, f.id, "center");
  if (!left.length) s += `<text x="${lx}" y="${cy + 4}" class="enone">nothing calls it</text>`;
  if (!right.length) s += `<text x="${rx}" y="${cy + 4}" class="enone">calls nothing here</text>`;
  s += `<text x="${lx}" y="12" class="ehead">used by</text><text x="${rx}" y="12" class="ehead">uses</text></svg>`;
  return s;
}

// ---- one function, in depth: explained, triggered by, connects to, needs, code ----
const fnCache = {};            // "app|id" -> static detail (from /api/fn)
const explaining = new Set();  // "app|id" while Claude is writing an explanation
const PRE = [  // [key, plain heading, what it means]
  ["programs", "Programs that must be installed", "outside tools it runs"],
  ["modules", "Code libraries it imports", "Python modules it uses"],
  ["files", "Files it reads or writes", ""],
  ["network", "Network access", ""],
  ["env", "Environment variables", ""],
  ["settings", "Settings it reads", "constants defined at the top of the file"],
  ["endpoints", "Backend addresses it calls", "the page asks the Python side for these"],
  ["desktop", "Desktop-window calls", "pywebview functions only the app window has"],
  ["elements", "Page elements it touches", ""],
  ["storage", "Saved in the browser", "localStorage keys"],
  ["timers", "Timing", ""],
];

// what depends on this function: callers of callers, the web addresses and page code above them, buttons, tests
function impactOf(m, fid) {
  const P = proj && !proj.loading ? proj : null;
  const pageOf = {};  // handler -> page functions that request it
  for (const r of (P && P.routes) || []) (pageOf[r.fn] = pageOf[r.fn] || []).push(...r.page);
  const depth = {[fid]: 0}, q = [fid];
  while (q.length) {
    const cur = q.shift();
    for (const c of [...(m.fns[cur] ? m.fns[cur].callers : []), ...(pageOf[cur] || [])])
      if (m.fns[c] && !(c in depth)) { depth[c] = depth[cur] + 1; q.push(c); }
  }
  delete depth[fid];
  const ids = Object.keys(depth);
  const routes = P ? P.routes.filter(r => r.fn === fid || r.fn in depth) : [];
  const clicks = P ? P.triggers.filter(t => t.fn === fid || t.fn in depth) : [];
  const tests = P ? [...new Set([fid, ...ids].flatMap(i => P.tests[i] || []))] : [];
  return {direct: m.fns[fid].callers.filter(c => m.fns[c]), all: ids.sort((a, b) => depth[a] - depth[b]), routes, clicks, tests};
}

function quizBox(f, key) {
  const st = quiz[key];
  if (!st) return `<button class="btn" data-quiz="${esc(f.id)}">Quiz me on it</button>`;
  if (st.error) return `<div class="notice" style="margin:0">${esc(st.error)}</div> <button class="link" data-quiz="${esc(f.id)}">try again</button>`;
  if (st.busy) return `<div class="muted">${esc(st.busy)}</div>`;
  const set = st.stage === "retry" ? st.retry : st.qs;
  if (st.stage === "ask" || st.stage === "retry") {
    const i = st.idx, q = set[i];
    return `<div class="quiz"><div class="qhead">${st.stage === "retry" ? "Second try" : "Quiz"} · question ${i + 1} of ${set.length}</div>
      <div class="qq">${esc(q.q)}</div>
      <textarea class="qa" rows="3" placeholder="Answer in your own words. Messy is fine. Ctrl+Enter = next">${esc(st.answers[i] || "")}</textarea>
      <div class="qbar">${q.hint ? `<button class="link" data-qhint>${st.showHint === i ? esc(q.hint) : "hint"}</button>` : ""}<span class="grow"></span>
        ${i ? `<button class="btn" data-qprev>Back</button>` : ""}
        <button class="btn primary" data-qnext>${i < set.length - 1 ? "Next" : "Check my answers"}</button></div></div>`;
  }
  const res = st.results || [];
  const right = res.filter(r => r.verdict === "right").length;
  return `<div class="quiz"><div class="qhead">${st.stage === "retryresults" ? "Second try" : "Results"}: ${right} of ${res.length} right</div>
    ${res.map(r => `<div class="qres ${r.verdict}"><div class="qv">${r.verdict === "right" ? "Right" : r.verdict === "partly" ? "Partly" : "Not yet"}</div>
      <div><div class="qq">${esc(r.q)}</div><div class="qt">${esc(r.teach)}</div>${r.verdict !== "right" ? `<div class="qk"><span>Key idea</span>${esc(r.answer)}</div>` : ""}</div></div>`).join("")}
    <div class="qbar"><span class="grow"></span>${st.retry && st.retry.length ? `<button class="btn primary" data-qretry>Try the ${st.retry.length} I missed again</button>` : ""}
      <button class="btn" data-quiz="${esc(f.id)}" data-qfresh>New quiz</button></div></div>`;
}

// the extra sections: in/out, data, difficulty, errors, tests, todos, history, notes
function detailSections(m, f, d, key) {
  const sec = (title, body) => `<div class="fsec"><div class="fsl">${title}</div>${body}</div>`;
  let h = "";
  // impact
  const imp = impactOf(m, f.id);
  h += sec(term("impact", "If you change this"), !imp.all.length && !imp.routes.length && !imp.clicks.length
    ? `<div class="muted">Nothing else in the project depends on it. A change here stays here (but check it still does its own job).</div>`
    : `<div class="impact">
        <div><b>${imp.direct.length}</b><span>use it directly</span></div><div><b>${imp.all.length}</b><span>depend on it in total</span></div>
        <div><b>${imp.routes.length}</b><span>web address${imp.routes.length === 1 ? "" : "es"}</span></div><div><b>${imp.clicks.length}</b><span>button${imp.clicks.length === 1 ? "" : "s"} / event${imp.clicks.length === 1 ? "" : "s"}</span></div>
        <div><b>${imp.tests.length}</b><span>test file${imp.tests.length === 1 ? "" : "s"}</span></div></div>
      ${imp.routes.length ? `<div class="io"><div class="ioh">Addresses that could behave differently</div><div>${imp.routes.map(r => `<span class="route"><b>${esc(r.verb)}</b> ${esc(r.path)}</span>`).join(" ")}</div></div>` : ""}
      ${imp.clicks.length ? `<div class="io"><div class="ioh">What people click that runs it</div><div>${imp.clicks.map(c => `<code>${esc(c.event)}: ${esc(c.element)}</code>`).join(" ")}</div></div>` : ""}
      ${imp.all.length ? `<div class="io"><div class="ioh">Functions to re-check</div><div>${imp.all.slice(0, 14).map(id => fnLink(m, id)).join(", ")}${imp.all.length > 14 ? ` <span class="faint">+${imp.all.length - 14} more</span>` : ""}</div></div>` : ""}
      <div class="faint" style="font-size:12px;margin-top:4px">${imp.tests.length ? "Run those tests after changing it." : "No test covers this chain, so check these by hand after a change."}</div>`);
  // started by (click paths through this function)
  const P = proj && !proj.loading ? proj : null;
  if (P) {
    const via = P.clickpaths.filter(c => c.fn === f.id || c.chains.some(ch => ch.handler === f.id || ch.js.includes(f.id)));
    if (via.length) h += sec(term("clickpath", "Started by"), via.map(c => `<div class="crow"><span class="ev">${esc(c.event)}</span><span>${esc(c.element)}</span>${c.chains[0] ? `<span class="muted">→ ${esc(c.chains[0].verb)} ${esc(c.chains[0].path)}</span>` : ""}</div>`).join(""));
    const mine = P.security.filter(s => s.fn === f.id);
    if (mine.length) h += sec(term("security", "Security"), mine.map(s => `<div class="finding ${s.sev}"><div class="fth"><b>${esc(s.title)}</b><span class="faint mono">line ${s.line} · ${s.sev}</span></div><div class="muted">${esc(s.why)}</div><div class="ffix"><span>Fix</span>${esc(s.fix)}</div></div>`).join(""));
  }
  // what goes in and out
  const params = d.params || [], req = d.request || {}, rets = d.returns || [];
  const reqNames = {path: "In the address", query: "In the query string (?x=)", json: "In the JSON body", form: "In a form", files: "Uploaded files", headers: "Headers / cookies"};
  if (params.length || Object.keys(req).length || rets.length) {
    h += sec("What goes in and comes out",
      (params.length ? `<div class="io"><div class="ioh">It takes</div><div>${params.map(p => `<code>${esc(p.name)}${p.type ? `: ${esc(p.type)}` : ""}${p.default ? ` = ${esc(p.default)}` : ""}</code>`).join(" ")}</div></div>` : `<div class="io"><div class="ioh">It takes</div><div class="faint">nothing</div></div>`) +
      Object.entries(req).map(([k, v]) => `<div class="io"><div class="ioh">${reqNames[k] || k}</div><div>${v.map(x => `<code>${esc(x)}</code>`).join(" ")}</div></div>`).join("") +
      (rets.length ? `<div class="io"><div class="ioh">It hands back</div><div>${rets.map(r => `<code class="block">${esc(r)}</code>`).join("")}</div></div>` : `<div class="io"><div class="ioh">It hands back</div><div class="faint">nothing (it does its work by side effect)</div></div>`) +
      (d.codes && d.codes.length ? `<div class="io"><div class="ioh">HTTP status codes</div><div>${d.codes.map(c => `<code>${c}</code>`).join(" ")}</div></div>` : "") +
      (d.yields ? `<div class="io"><div class="ioh">Note</div><div class="muted">It's a generator: it hands back values one at a time.</div></div>` : ""));
  }
  // data
  const data = d.data || {};
  if ((data.reads || []).length || (data.writes || []).length) {
    h += sec(term("tables", "Data it touches"),
      ((data.reads || []).length ? `<div class="io"><div class="ioh">Reads tables</div><div>${data.reads.map(t => `<code>${esc(t)}</code>`).join(" ")}</div></div>` : "") +
      ((data.writes || []).length ? `<div class="io"><div class="ioh">Changes tables</div><div>${data.writes.map(t => `<code class="w">${esc(t)}</code>`).join(" ")}</div></div>` : ""));
  }
  // difficulty
  const c = d.complexity;
  if (c) {
    const pct = Math.min(100, Math.round(c.score / 25 * 100));
    h += sec(term("complexity", "How hard to follow"),
      `<div class="cx"><span class="cxl ${c.label.replace(" ", "-")}">${esc(c.label)}</span><span class="hbar"><i style="width:${pct}%"></i></span><span class="muted">${esc(c.why)}</span></div>
       <div class="cxs muted">${c.lines} lines · ${c.score - 1} decision point${c.score === 2 ? "" : "s"} (if / loop / and / or / except) · nested ${c.nesting} deep · ${c.params} input${c.params === 1 ? "" : "s"}</div>`);
  }
  // errors
  const e = d.errors || {};
  if ((e.raises || []).length || (e.catches || []).length || e.bare) {
    h += sec("When things go wrong",
      ((e.raises || []).length ? `<div class="io"><div class="ioh">It raises (stops with an error)</div><div>${e.raises.map(x => `<code>${esc(x)}</code>`).join(" ")}</div></div>` : "") +
      ((e.catches || []).length ? `<div class="io"><div class="ioh">It catches and handles</div><div>${e.catches.map(x => `<code>${esc(x)}</code>`).join(" ")}</div></div>` : "") +
      (e.bare ? `<div class="io"><div class="ioh">Careful</div><div class="muted">It has a bare <code>except:</code>, which silently swallows every error, including ones you'd want to see.</div></div>` : ""));
  }
  // tests + todos
  const tests = proj && proj.tests ? proj.tests[f.id] || [] : null;
  if (tests !== null) h += sec("Tested by", tests.length ? tests.map(t => `<code>${esc(t)}</code>`).join(" ") : `<span class="muted">No test file mentions it. Changes here are only checked by hand.</span>`);
  if ((d.todos || []).length) h += sec("Left to do (comments in the code)", d.todos.map(t => `<div class="crow"><span class="mono faint">line ${t.line}</span><span>${esc(t.text)}</span></div>`).join(""));
  // history
  const hist = fnHist[key];
  if (hist === undefined) {
    fnHist[key] = null;
    api(`/api/fn/history?app=${encodeURIComponent(current)}&id=${encodeURIComponent(f.id)}`).then(r => { fnHist[key] = r; if (mapSel === f.id && tab === "map") renderPane(); });
  }
  h += sec("History", !hist ? `<span class="faint">Reading git…</span>`
    : !hist.tracked || !hist.count ? `<span class="muted">No git history for these lines yet.</span>`
    : `<div class="muted" style="margin-bottom:6px">Changed in ${hist.count} commit${hist.count === 1 ? "" : "s"}${hist.first ? `, first written ${esc(hist.first)}` : ""}. ${hist.owners.length ? `Who wrote the lines as they are now: ${hist.owners.map(o => `<b>${esc(o.name)}</b> ${o.share}%`).join(", ")}.` : ""}</div>
       ${hist.commits.slice(0, 8).map(cm => `<div class="row"><span class="h">${esc(cm.hash)}</span><span class="d">${esc(cm.date)}</span><span class="s">${esc(cm.subject)}</span></div>`).join("")}`);
  // path finder buttons
  h += sec("Find a path", `<button class="btn" data-pathfrom="${esc(f.id)}">Start here</button> <button class="btn" data-pathto="${esc(f.id)}">End here</button>
    <span class="faint" style="font-size:12px;margin-left:6px">Pick a start and an end to see how one reaches the other.</span>`);
  // notes pinned to this function
  const fnotes = appNotes().filter(n => n.fn === f.id);
  h += sec("Your notes on this function", fnotes.map(n => noteCard(n)).join("") + composer({app: current, round: null, tab: "map", fn: f.id, fnLabel: f.label}));
  return h;
}

function focusCard(m, f) {
  const key = `${current}|${f.id}`, d = fnCache[key];
  if (!d) {
    api(`/api/fn?app=${encodeURIComponent(current)}&id=${encodeURIComponent(f.id)}`).then(r => { fnCache[key] = r; if (mapSel === f.id && tab === "map") renderPane(); });
  }
  const fl = id => m.fns[id] ? `<button class="link" data-fn="${esc(id)}">${esc(m.fns[id].label)}</button>` : "";
  const oneLine = id => m.fns[id] ? `<div class="crow">${fl(id)}<span class="muted">${esc(m.fns[id].doc || "")}</span></div>` : "";
  const sec = (title, body) => `<div class="fsec"><div class="fsl">${title}</div>${body}</div>`;
  let html = `<div class="focus">
    <div class="fhead"><span class="ftitle">${esc(f.label)}</span><span class="mono faint">${esc(f.file)} ${d && d.start ? `lines ${d.start}–${d.end}` : esc(f.line)}</span>${d && d.lang ? `<span class="lang">${esc(d.lang)}</span>` : ""}</div>
    ${d && d.sig ? `<div class="fsig mono">${esc(d.sig)}</div>` : ""}
    ${(d && d.doc) || f.doc ? `<div class="fdoc"><span class="rw">In its own words</span>${esc((d && d.doc) || f.doc)}</div>` : `<div class="fdoc faint">No docstring or comment on this one, so the explanation below matters more.</div>`}`;

  // plain-English explanation (Claude, on click)
  const ex = d && d.explained, busy = explaining.has(key);
  html += sec(`${term("explain", "Explained")}`, ex && ex.text
    ? `${ex.stale ? `<div class="notice" style="margin:0 0 8px">The code changed since this was written. <button class="link" data-explain="${esc(f.id)}">Explain again</button></div>` : ""}
       <div class="expl">${md(ex.text)}</div><div class="faint" style="font-size:11.5px;margin-top:6px">Written by Claude ${esc(ex.model || "")} · ${fmtTok(ex.out)} tokens · saved, so reopening it is free</div>`
    : ex && ex.error ? `<div class="notice" style="margin:0">${esc(ex.error)}</div>`
    : busy ? `<div class="muted">Claude is reading the code… (usually 15–40 seconds)</div>`
    : `<button class="btn primary" data-explain="${esc(f.id)}" ${d ? "" : "disabled"}>Explain this in plain English</button>
       <span class="faint" style="font-size:12px;margin-left:8px">Uses Claude Sonnet, a few cents' worth of tokens. Saved after, so it's paid once.</span>`);

  if (d) html += `<div class="qwrap">${quizBox(f, key)}</div>`;

  // how it gets triggered
  if (d) {
    const routes = d.routes || [], reach = d.reach || [];
    html += sec(term("trigger", "How it gets triggered"),
      (routes.length ? `<div class="routes">${routes.map(r => `<span class="route"><b>${esc(r.split(" ")[0])}</b> ${esc(r.split(" ").slice(1).join(" "))}</span>`).join("")}</div>
        <div class="faint" style="font-size:12px;margin:4px 0 8px">The app runs this when something asks for ${routes.length > 1 ? "these addresses" : "this address"}.</div>` : "") +
      (reach.length ? reach.map(ch => `<div class="chain">${ch.map((id, i) => (i ? `<span class="arrowc">→</span>` : "") + (id === f.id ? `<b>${esc(m.fns[id].label)}</b>` : fl(id))).join("")}</div>`).join("")
        + `<div class="faint" style="font-size:12px;margin-top:4px">Read left to right: the first one starts things, each calls the next.</div>`
        : !routes.length ? `<div class="muted">Nothing in the code calls it, so it's an ${term("entry", "entry point")}: it gets started from outside (the app launching, a button, a timer, or the browser).</div>` : ""));
  }

  // how it connects
  html += sec("How it connects", egoSvg(m, f) +
    (f.callers.length > 7 || f.calls.length > 7 ? `<div class="faint" style="font-size:12px">Diagram shows 7 per side.</div>` : "") +
    `<div class="conn2"><div><div class="csub">Uses (${f.calls.length})</div>${f.calls.map(oneLine).join("") || `<div class="faint">nothing in this project</div>`}</div>
      <div><div class="csub">Used by (${f.callers.length})</div>${f.callers.map(oneLine).join("") || `<div class="faint">nothing (entry point)</div>`}</div></div>` +
    (d && d.backend && d.backend.length ? `<div class="csub" style="margin-top:10px">Talks to the backend</div>${d.backend.map(b => `<div class="crow"><code>${esc(b.endpoint)}</code><span class="arrowc">→</span>${fl(b.fn)}<span class="muted">${esc(m.fns[b.fn] ? m.fns[b.fn].doc : "")}</span></div>`).join("")}` : ""));

  // prerequisites
  if (d) {
    const groups = PRE.filter(([k]) => d.pre && d.pre[k] && d.pre[k].length);
    html += sec(term("prereq", "Before it can run"), groups.length
      ? `<div class="pre">${groups.map(([k, h, why]) => `<div class="pg"><div class="pgh">${h}${why ? ` <span class="faint">· ${why}</span>` : ""}</div><div class="pgv">${d.pre[k].map(v => `<code>${esc(v)}</code>`).join(" ")}</div></div>`).join("")}</div>`
      : `<div class="muted">Nothing outside this file: no programs, files, network or settings.</div>`);
    html += detailSections(m, f, d, key);
    // the code
    if (d.code && d.code.length) {
      html += `<details class="fsec codebox"><summary><span class="fsl" style="display:inline">The code</span> <span class="faint">${d.code.length} lines${d.truncated ? " (first 160)" : ""} · tap to open</span></summary>
        <pre class="src">${d.code.map(([n, l]) => `<span class="ln">${n}</span>${esc(l)}`).join("\n")}</pre></details>`;
    }
  } else {
    html += `<div class="faint" style="margin-top:10px">Reading the code…</div>`;
  }
  return html + `</div>`;
}

// ---- whole-project views: routes, tours, hotspots, possibly unused ----
function fnLink(m, id) { return m.fns[id] ? `<button class="link" data-fn="${esc(id)}" data-go="explore">${esc(m.fns[id].label)}</button>` : ""; }

function mapSubview(m, P) {
  if (mapView === "routes") {
    if (!P.routes.length) return `<div class="empty">No web ${term("route", "routes")} found. This project doesn't look like a web app (no Flask/FastAPI decorators or http.server path checks).</div>`;
    const inp = r => Object.entries(r.inputs || {}).map(([k, v]) => `<span class="faint">${{path: "in the address", query: "query", json: "JSON body", form: "form", files: "upload", headers: "header"}[k] || k}:</span> ${v.map(x => `<code>${esc(x)}</code>`).join(" ")}`).join("<br>");
    return `<div class="sub" style="margin:10px 0">Every address this app answers: who handles it, what it expects, what data it touches, and which page code calls it. Tap a handler to open it.</div>
      <table class="rtable"><thead><tr><th>Address</th><th>Handled by</th><th>Expects</th><th>Data</th><th>Called from the page</th></tr></thead><tbody>
      ${P.routes.map(r => `<tr><td class="mono"><span class="verb ${esc(r.verb)}">${esc(r.verb)}</span> ${esc(r.path)}</td>
        <td>${fnLink(m, r.fn)}<div class="faint" style="font-size:12px">${esc((m.fns[r.fn] || {}).doc || "")}</div></td>
        <td>${inp(r) || `<span class="faint">nothing</span>`}</td>
        <td>${r.reads.length ? `<span class="faint">reads</span> ${r.reads.map(t => `<code>${esc(t)}</code>`).join(" ")}<br>` : ""}${r.writes.length ? `<span class="faint">writes</span> ${r.writes.map(t => `<code class="w">${esc(t)}</code>`).join(" ")}` : ""}${!r.reads.length && !r.writes.length ? `<span class="faint">no SQL tables</span>` : ""}</td>
        <td>${r.page.map(id => fnLink(m, id)).join(", ") || `<span class="faint">not from this code</span>`}</td></tr>`).join("")}</tbody></table>`;
  }
  if (mapView === "clicks") {
    if (!P.clickpaths.length) return `<div class="empty">No buttons or page events wired to this project's functions were found (looked for onclick= in HTML and addEventListener / .onclick in JS).</div>`;
    return `<div class="sub" style="margin:10px 0">${term("clickpath", "Click paths")}: what really happens when someone uses the page, from the button all the way to the database. Tap any step to open it.</div>
      ${P.clickpaths.map(c => `<div class="cpath">
        <div class="cpel"><span class="ev">${esc(c.event)}</span> ${esc(c.element)} <span class="faint mono">${esc(c.file)}:${c.line}</span></div>
        ${c.chains.length ? c.chains.map(ch => `<div class="cpchain">
            ${ch.js.map(id => `<span class="cpstep js">${fnLink(m, id)}</span>`).join(`<span class="arrowc">→</span>`)}
            <span class="arrowc">→</span><span class="cpstep web"><span class="verb ${esc(ch.verb)}">${esc(ch.verb)}</span> <code>${esc(ch.path)}</code></span>
            <span class="arrowc">→</span><span class="cpstep py">${fnLink(m, ch.handler)}</span>
            ${ch.reads.length || ch.writes.length ? `<span class="arrowc">→</span><span class="cpstep db">${ch.reads.map(t => `<code>${esc(t)}</code>`).join(" ")}${ch.writes.map(t => `<code class="w">${esc(t)}</code>`).join(" ")}</span>` : ""}
          </div>`).join("")
          : `<div class="cpchain"><span class="cpstep js">${fnLink(m, c.fn)}</span><span class="faint" style="margin-left:8px">stays in the page (no server request)</span></div>`}
      </div>`).join("")}
      <div class="faint" style="font-size:12px;margin-top:8px">Legend: <span class="cpstep js">page code</span> <span class="cpstep web">web request</span> <span class="cpstep py">server code</span> <span class="cpstep db">tables (amber = changed)</span></div>`;
  }
  if (mapView === "data") {
    if (!P.tables.length) return `<div class="empty">No database tables found. (Looked for SQL in the code and .sql files, and SQLAlchemy models.)</div>`;
    return `<div class="sub" style="margin:10px 0">The Map turned around: start from the data. Each table, its columns, and everything that reads or changes it.</div>
      <div class="tables">${P.tables.map(t => `<div class="tbl">
        <div class="tname"><b>${esc(t.name)}</b><span class="faint">${t.columns.length} column${t.columns.length === 1 ? "" : "s"}</span></div>
        ${t.columns.length ? `<div class="tcols">${t.columns.map(c => `<code>${esc(c)}</code>`).join(" ")}</div>` : ""}
        <div class="io"><div class="ioh">Changed by</div><div>${t.writers.map(id => fnLink(m, id)).join(", ") || `<span class="faint">nothing in this code</span>`}${t.routesWrite.length ? `<div class="faint" style="font-size:12px">via ${t.routesWrite.map(esc).join(", ")}</div>` : ""}</div></div>
        <div class="io"><div class="ioh">Read by</div><div>${t.readers.map(id => fnLink(m, id)).join(", ") || `<span class="faint">nothing in this code</span>`}${t.routesRead.length ? `<div class="faint" style="font-size:12px">via ${t.routesRead.map(esc).join(", ")}</div>` : ""}</div></div>
      </div>`).join("")}</div>`;
  }
  if (mapView === "security") {
    const sevName = {high: "High", medium: "Medium", low: "Low"};
    return `<div class="sub" style="margin:10px 0">${term("security", "Security lens")}: risky patterns found by reading the code, each with why it matters and the usual fix. ${P.globalAuth ? "A login check runs before every request (found a before_request hook or login manager), so per-route login warnings are off." : ""} These are leads to check, not proof of a hole.</div>
      ${P.security.length ? ["high", "medium", "low"].map(sv => {
        const rows = P.security.filter(s => s.sev === sv);
        return rows.length ? `<h2 class="sevh2 ${sv}">${sevName[sv]} (${rows.length})</h2>` + rows.map(s => `<div class="finding ${sv}">
          <div class="fth"><b>${esc(s.title)}</b><span class="faint mono">${fnLink(m, s.fn)}${m.fns[s.fn] && m.fns[s.fn].kind !== "file" ? " · " : ""}${esc((m.fns[s.fn] || {}).file || s.file || "")}:${s.line}</span></div>
          <div class="muted">${esc(s.why)}</div><div class="ffix"><span>Fix</span>${esc(s.fix)}</div></div>`).join("") : "";
      }).join("") : `<div class="empty">Nothing risky found by these checks.</div>`}`;
  }
  if (mapView === "tours") {
    if (tourIdx === null) {
      return `<div class="sub" style="margin:10px 0">Guided walkthroughs built from the real call graph. Each one goes step by step, with the full detail card at every stop.</div>
        <div class="tours">${P.tours.map((t, i) => `<button class="tourcard" data-tour="${i}"><b>${esc(t.title)}</b><span class="muted">${esc(t.why)}</span>
          <span class="faint mono">${t.steps.map(s => esc((m.fns[s] || {}).label || "")).join(" → ")}</span></button>`).join("")}</div>`;
    }
    const t = P.tours[tourIdx], id = t.steps[tourStep];
    mapSel = id;
    return `<div class="tourbar"><button class="btn" data-tourexit>All tours</button>
        <b>${esc(t.title)}</b><span class="grow"></span>
        <button class="btn" data-tourstep="-1" ${tourStep ? "" : "disabled"}>← Back</button>
        <span class="mono">Step ${tourStep + 1} of ${t.steps.length}</span>
        <button class="btn primary" data-tourstep="1" ${tourStep < t.steps.length - 1 ? "" : "disabled"}>Next →</button></div>
      <div class="tsteps">${t.steps.map((s, i) => `<button class="tstep ${i === tourStep ? "on" : i < tourStep ? "done" : ""}" data-tourgo="${i}"><span>${i + 1}</span>${esc((m.fns[s] || {}).label || "")}</button>`).join("")}</div>
      ${m.fns[id] ? focusCard(m, m.fns[id]) : ""}`;
  }
  if (mapView === "hot") {
    const max = Math.max(1, ...P.hotspots.map(h => h.score));
    return `<div class="sub" style="margin:10px 0">${term("hotspot", "Hotspots")}: code that is hard to follow <b>and</b> changes often. That's where bugs and effort tend to pile up (the idea comes from CodeScene). ${esc(P.historyNote || "")}</div>
      ${P.hotspots.map(h => `<div class="hrow"><span class="dot ${h.level === "high" ? "bad" : h.level === "medium" ? "warn" : ""}"></span>
        ${fnLink(m, h.fn)}<span class="hbar"><i style="width:${Math.round(100 * h.score / max)}%"></i></span>
        <span class="muted">${esc(h.label)} (${h.complexity}) · changed in ${h.changes} commit${h.changes === 1 ? "" : "s"} · ${h.lines} lines</span></div>`).join("")}`;
  }
  return `<div class="sub" style="margin:10px 0">Nothing in the project calls these, they aren't routes or entry points, and their name appears nowhere else in the code. They're <b>possibly</b> leftovers. Check before deleting: something outside the code (an HTML onclick, another app, a scheduled task) may still use them.</div>
    ${P.unused.map(id => `<div class="crow">${fnLink(m, id)}<span class="muted">${esc(m.fns[id].file)} ${esc(m.fns[id].line)}</span><span class="muted">${esc(m.fns[id].doc || "")}</span></div>`).join("") || `<div class="empty">Nothing looks unused.</div>`}`;
}

// ---- path finder: how does A reach B? (Sourcetrail / graphify path) ----
function findPath(m, a, b) {
  // page code reaches the server through web requests, not calls: add those as one-way links
  const web = {};
  for (const r of (proj && proj.routes) || []) for (const pg of r.page) (web[pg] = web[pg] || []).push(r.fn);
  const bfs = (useCallers) => {
    const prev = {[a]: null}, q = [a];
    while (q.length) {
      const cur = q.shift();
      if (cur === b) break;
      const fwd = [...m.fns[cur].calls, ...(web[cur] || [])];
      const next = useCallers ? [...fwd, ...m.fns[cur].callers] : fwd;
      for (const n of next) if (m.fns[n] && !(n in prev)) { prev[n] = cur; q.push(n); }
    }
    if (!(b in prev)) return null;
    const path = [];
    for (let x = b; x !== null; x = prev[x]) path.unshift(x);
    return path;
  };
  const direct = bfs(false);
  return direct ? {path: direct, direct: true} : (bfs(true) ? {path: bfs(true), direct: false} : null);
}
function pathPanel(m) {
  const lab = id => id && m.fns[id] ? esc(m.fns[id].label) : "…";
  let body = `<span class="faint">Pick the other end with the buttons on any function card.</span>`;
  if (pathFrom && pathTo) {
    const r = findPath(m, pathFrom, pathTo);
    body = !r ? `<span class="muted">No connection between them in this project's code.</span>`
      : `<div class="chain">${r.path.map((id, i) => (i ? `<span class="arrowc">${r.direct ? "→" : "–"}</span>` : "") + fnLink(m, id)).join("")}</div>
         <div class="faint" style="font-size:12px">${r.direct ? `${r.path.length - 1} step${r.path.length > 2 ? "s" : ""}: each one calls the next, directly or by a web request from the page to the server.` : "No one-way call chain, but they're linked through shared code (some steps go the other way)."}</div>`;
  }
  return `<div class="pathbox"><div><b>How does</b> ${lab(pathFrom)} <b>reach</b> ${lab(pathTo)}<b>?</b> <button class="link" data-pathclear>clear</button></div>${body}</div>`;
}

function renderMap(p) {
  const d = detail;
  if (mapData === null) {
    p.innerHTML = `<div class="empty">Loading the map…</div>`;
    api(`/api/map?app=${encodeURIComponent(d.name)}`).then(m => { if (d === detail) { mapData = m; renderPane(); } });
    return;
  }
  if (mapData.built === false) {
    if (mapData.graphify === false) {
      p.innerHTML = `<div class="empty">The ${term("map")} needs graphify, which isn't installed.<br><br>
        <code>python -m pip install --user graphifyy</code> <span class="faint">(double y)</span><br><br>
        <span class="faint">It runs in code-only mode: no AI, no tokens, nothing leaves this machine. Then come back and press r.</span></div>`;
      return;
    }
    p.innerHTML = `<div class="empty">No ${term("map")} yet for ${esc(d.name)}.<br><br>
      <button class="btn primary" data-mapbuild ${mapBusy ? "disabled" : ""}>${mapBusy ? "Building… (a few seconds)" : "Build the map"}</button>
      <div class="faint" style="margin-top:10px">Runs graphify code-only on this PC. No AI, no tokens, and nothing in the app folder changes.</div></div>`;
    return;
  }
  const m = mapData;
  if (!mapSel || !m.fns[mapSel]) mapSel = m.hubs[0];
  const f = m.fns[mapSel];
  const chip = id => m.fns[id] ? `<button class="fchip ${id === mapSel ? "on" : ""}" data-fn="${esc(id)}">${esc(m.fns[id].label)}</button>` : "";
  const built = new Date(m.builtAt * 1000).toLocaleString(undefined, {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"});
  if (proj === null || proj.app !== d.name) {
    proj = {app: d.name, loading: true};
    api(`/api/project?app=${encodeURIComponent(d.name)}`).then(r => { if (proj && proj.app === d.name) { proj = {...r, app: d.name}; if (tab === "map") renderPane(); } });
  }
  const P = proj && !proj.loading ? proj : null;
  const hi = P ? P.security.filter(s => s.sev === "high").length : 0;
  const VIEWS = [["explore", "Explore"], ["routes", `Routes${P ? ` <span class="c">${P.routes.length}</span>` : ""}`],
    ["clicks", `Click paths${P ? ` <span class="c">${P.clickpaths.length}</span>` : ""}`],
    ["data", `Data${P ? ` <span class="c">${P.tables.length}</span>` : ""}`],
    ["security", `Security${P ? (hi ? ` <span class="c sevh">${hi} high</span>` : ` <span class="c">${P.security.length}</span>`) : ""}`],
    ["tours", `Tours${P ? ` <span class="c">${P.tours.length}</span>` : ""}`], ["hot", "Hotspots"],
    ["unused", `Possibly unused${P ? ` <span class="c">${P.unused.length}</span>` : ""}`]];
  const head = `
    <div class="mapbar"><span class="muted">${m.stats.files} files · ${Object.keys(m.fns).length} pieces · ${m.stats.edges} connections · built ${esc(built)}</span>
      <span class="grow"></span>
      <button class="btn" data-mapbuild ${mapBusy ? "disabled" : ""}>${mapBusy ? "Rebuilding…" : "Rebuild"}</button>
      <button class="btn" data-mapfull>Full graph</button></div>
    <div class="mapnav">${VIEWS.map(([k, l]) => `<button class="${mapView === k ? "on" : ""}" data-mapview="${k}">${l}</button>`).join("")}</div>`;
  if (mapView !== "explore") {
    p.innerHTML = head + (P ? mapSubview(m, P) : `<div class="empty">Reading the whole project…</div>`);
    return;
  }
  const q = mapQuery.trim().toLowerCase();
  const hits = q ? Object.values(m.fns).filter(x => x.kind !== "file" && (x.label.toLowerCase().includes(q) || x.file.toLowerCase().includes(q) || (x.doc || "").toLowerCase().includes(q)))
    .sort((a, b) => (a.label.toLowerCase().startsWith(q) ? 0 : 1) - (b.label.toLowerCase().startsWith(q) ? 0 : 1) || b.deg - a.deg).slice(0, 18) : [];
  p.innerHTML = head + `
    <div class="mapsearch"><input id="mapq" placeholder="Search functions, files or words in their docs ( / )" value="${esc(mapQuery)}" autocomplete="off">
      ${q ? `<div class="fchips" style="margin-top:8px">${hits.map(x => chip(x.id)).join("") || `<span class="faint">no matches</span>`}</div>` : ""}</div>
    ${pathFrom || pathTo ? pathPanel(m) : ""}
    <h2 style="margin-top:14px">${term("hub", "Core pieces")}</h2>
    <div class="fchips">${m.hubs.map(chip).join("")}</div>
    ${focusCard(m, f)}
    <h2>${term("map", "Areas")}</h2>
    <div class="areas">${m.areas.map(a => {
      const open = mapOpenAreas.has(a.id), shown = open ? a.members : a.members.slice(0, 8);
      return `<div class="area ${a.members.includes(mapSel) ? "has" : ""}">
        <div class="aname">${esc(a.name)}</div>
        <div class="afiles mono faint">${a.files.map(esc).join(" · ")}</div>
        ${a.doc ? `<div class="adoc">${esc(a.doc)}</div>` : ""}
        <div class="fchips">${shown.map(chip).join("")}${a.members.length > 8 ? `<button class="fchip more" data-area="${a.id}">${open ? "less" : "+" + (a.members.length - 8)}</button>` : ""}</div>
      </div>`;
    }).join("")}</div>`;
}

function renderPane() {
  const d = detail, p = $("#pane");
  if (tab === "timeline") {
    if (d.log.length && !openRounds.size) openRounds.add(d.log[0].title);
    p.innerHTML = d.log.length ? d.log.map(roundCard).join("")
      : `<div class="empty">No ${term("LOG.md")} yet. Rounds show up here once the app is on the harness.</div>`;
  } else if (tab === "sessions") {
    if (!sess || !sess.sessions) { p.innerHTML = `<div class="empty">Reading Claude Code's session logs…</div>`; return; }
    if (!sess.sessions.length) { p.innerHTML = `<div class="empty">No Claude Code ${term("session", "sessions")} on this PC touched ${esc(d.name)}. (Only this machine's Claude Code logs are read. If you don't use Claude Code, this tab stays empty.)</div>`; return; }
    p.innerHTML = `<div class="sub" style="margin-bottom:10px">The actual conversations behind this app, newest first. Only the ${term("turn", "turns")} that touched ${esc(d.name)} are shown.</div>` +
      sess.sessions.map(s => {
        const open = openSess.has(s.id);
        const edits = s.turns.reduce((n, t) => n + t.edits, 0), cmds = s.turns.reduce((n, t) => n + t.cmds, 0);
        const span = new Date(s.start).toLocaleDateString(undefined, {month: "short", day: "numeric"}) + " " + new Date(s.start).toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"});
        return `<div class="sess ${open ? "open" : ""}">
          <button class="shead" data-sess="${esc(s.id)}">
            <span class="sdate mono">${esc(span)}</span>
            <span class="stitle">${esc(s.title)}</span>
            <span class="sstat">${s.turns.length} ${s.turns.length > 1 ? "turns" : "turn"} · ${edits} edits · ${cmds} commands · ${fmtTok(s.out)} tokens</span>
            ${s.files.length ? `<span class="sfiles">${s.files.slice(0, 8).map(f => `<code>${esc(f)}</code>`).join(" ")}${s.files.length > 8 ? ` <span class="faint">+${s.files.length - 8}</span>` : ""}</span>` : ""}
          </button>
          ${open ? `<div class="turns">${s.turns.map(t => turnView(t)).join("")}
            <div class="resume faint">Reopen this conversation: <code>${s.cwd ? `cd "${esc(s.cwd)}"; ` : ""}claude --resume ${esc(s.id)}</code></div></div>` : ""}
        </div>`;
      }).join("");
  } else if (tab === "map") {
    renderMap(p);
  } else if (tab === "decisions") {
    p.innerHTML = d.decisions.length ? `<div class="sub" style="margin-bottom:6px">Newest first. Each one: what was decided, then why.</div>` + d.decisions.slice().reverse().map(x =>
      `<div class="dec"><div class="date">${esc(x.date)}</div><div><strong>${inline(x.decision)}</strong><div class="why">${inline(x.why)}</div></div></div>`).join("")
      : `<div class="empty">No ${term("DECISIONS.md")} yet.</div>`;
  } else if (tab === "changes") {
    if (!d.git) { p.innerHTML = `<div class="empty">Not in ${term("git")}, so there's no history. Every change so far is untracked.</div>`; return; }
    const code = c => c === "??" ? "Q" : c[0];
    p.innerHTML = `<h2 style="margin-top:0">${term("uncommitted", "Uncommitted")} (${d.changes.length})</h2>
      ${d.changes.length ? d.changes.map(x => `<div class="row"><span class="code ${code(x.code)}">${esc(x.code === "??" ? "new" : x.code)}</span><span class="s mono">${esc(x.file)}</span></div>`).join("") : `<div class="muted" style="padding:6px 10px">Nothing. Everything is committed.</div>`}
      <h2>${term("commit", "Commits")}</h2>
      ${d.commits.map(x => `<div class="row"><span class="h">${esc(x.hash)}</span><span class="d">${esc(x.date)}</span><span class="s">${esc(x.subject)}</span></div>`).join("") || `<div class="muted" style="padding:6px 10px">No commits yet.</div>`}`;
  } else if (tab === "briefs") {
    if (!d.briefs.length) { p.innerHTML = `<div class="empty">No ${term("brief", "briefs")}. These are the specs written before each round.</div>`; return; }
    if (!d.briefs.includes(sub)) sub = d.briefs[d.briefs.length - 1];
    p.innerHTML = `<div class="split"><div class="list">${d.briefs.map(b => `<button data-brief="${esc(b)}" class="${b === sub ? "on" : ""}">${esc(b.replace(/\.md$/, ""))}</button>`).join("")}</div><div id="doc" class="muted">Loading…</div></div>`;
    fetch(`/file?app=${encodeURIComponent(d.name)}&p=${encodeURIComponent("briefs/" + sub)}`).then(r => r.text()).then(t => { const el = $("#doc"); if (el) el.outerHTML = md(t); });
  } else if (tab === "proof") {
    if (!d.proof.length) { p.innerHTML = `<div class="empty">No ${term("proof")} saved. Screenshots and selftest output go in <code>proof/roundN/</code>.</div>`; return; }
    const url = x => `/file?app=${encodeURIComponent(d.name)}&p=${encodeURIComponent(x.path)}`;
    p.innerHTML = `<div class="grid">${d.proof.map(x => x.kind === "image"
      ? `<button class="shot" data-img="${esc(url(x))}"><img loading="lazy" src="${esc(url(x))}"><div>${esc(x.path)}</div></button>`
      : `<button class="shot txt" data-txt="${esc(url(x))}"><div>${esc(x.path)}</div></button>`).join("")}</div><div id="txt"></div>`;
  } else if (tab === "notes") {
    const ns = appNotes().slice().reverse();
    const live = ns.filter(n => n.status !== "done"), closed = ns.filter(n => n.status === "done");
    p.innerHTML = composer({app: d.name, round: null, tab: null}) +
      (live.length ? live.map(n => noteCard(n)).join("") : `<div class="empty">No notes yet. Write anything: what confused you, what you'd change, a question.</div>`) +
      (closed.length ? `<details class="closed"><summary>${closed.length} closed</summary>${closed.map(n => noteCard(n)).join("")}</details>` : "");
  } else {
    p.innerHTML = d.agents ? `<div class="faint mono" style="margin-bottom:10px">${esc(d.agentsFile)}</div>` + md(d.agents)
      : `<div class="empty">No ${term("AGENTS.md")} or README. Nothing says how to run this app.</div>`;
  }
}

// ---- navigation ----
function rerender() {
  renderSide();
  const s = $("#main").scrollTop;
  if (view === "overview") renderOverview(); else if (view === "guide") renderGuide(); else if (detail) renderApp();
  $("#main").scrollTop = s;
}
async function loadNotes() { allNotes = await api("/api/notes"); }
async function loadApps() {
  [apps] = await Promise.all([api("/api/apps"), loadNotes()]);
  renderSide();
  if (view === "overview") renderOverview();
}
async function openApp(name) {
  if (name !== current) { openRounds = new Set(); openSess = new Set(); sub = null; sess = null; mapData = null; mapSel = null; mapOpenAreas = new Set(); proj = null; mapView = "explore"; mapQuery = ""; pathFrom = pathTo = null; tourIdx = null; tourStep = 0; }
  current = name; view = "app";
  renderSide();
  const sessP = loadSessions(name);
  detail = await api(`/api/app?name=${encodeURIComponent(name)}`);
  if (detail.error) { $("#main").innerHTML = `<div class="wrap"><div class="notice">${esc(detail.error)}</div></div>`; return; }
  if (tab === "timeline" && !detail.log.length) tab = "map";
  renderApp();
  await sessP;
}
async function loadSessions(name, tries = 0) {
  const r = await api(`/api/sessions?app=${encodeURIComponent(name)}`);
  if (name !== current) return;
  if (!r.sessions) { if (tries < 30) setTimeout(() => loadSessions(name, tries + 1), 1000); return; }  // first index still building
  sess = r;
  if (view === "app" && detail && detail.name === name) {
    const s = $("#main").scrollTop; renderApp(); $("#main").scrollTop = s;
  }
}
function go(v) { view = v; renderSide(); if (v === "overview") renderOverview(); else renderGuide(); $("#main").scrollTop = 0; }
async function refresh() {
  if (document.activeElement?.matches("textarea")) return;  // never wipe a half-written note
  await loadApps();
  if (view === "app" && current) { const s = $("#main").scrollTop; await openApp(current); $("#main").scrollTop = s; }
}
function newNote() {
  if (view !== "app") return;
  const c = tab === "timeline" ? $(".round.open .composer") : null;
  if (c) { $("textarea", c).focus(); c.scrollIntoView({block: "center", behavior: "smooth"}); return; }
  tab = "notes"; renderApp(); $("#pane textarea")?.focus();
}

// term popover
function showTerm(el) {
  hideTerm();
  const [plain, under] = TERMS[el.dataset.term] || ["", ""];
  const pop = document.createElement("div");
  pop.className = "pop";
  pop.innerHTML = `<div class="pt">${esc(el.textContent)}</div><div>${plain}</div><div class="pu"><span>Under the hood</span>${under}</div>`;
  document.body.append(pop);
  const r = el.getBoundingClientRect();
  pop.style.left = Math.min(r.left, innerWidth - pop.offsetWidth - 12) + "px";
  pop.style.top = (r.bottom + pop.offsetHeight + 8 > innerHeight ? r.top - pop.offsetHeight - 6 : r.bottom + 6) + "px";
}
const hideTerm = () => document.querySelectorAll(".pop").forEach(p => p.remove());

document.addEventListener("click", async e => {
  const tEl = e.target.closest(".term");
  if (tEl) { e.stopPropagation(); e.preventDefault(); return showTerm(tEl); }
  if (!e.target.closest(".pop")) hideTerm();
  const t = e.target.closest("[data-app],[data-tab],[data-brief],[data-img],[data-txt],[data-round],[data-kind],[data-save],[data-follow],[data-close],[data-reopen],[data-del],[data-newnote],[data-goproof],[data-gosess],[data-sess],[data-fn],[data-area],[data-explain],[data-mapview],[data-tour],[data-tourstep],[data-tourgo],[data-tourexit],[data-pathfrom],[data-pathto],[data-pathclear],[data-quiz],[data-qnext],[data-qprev],[data-qhint],[data-qretry],[data-mapfull],[data-mapbuild],#nav-overview,#nav-guide,#filters button,.lightbox");
  if (!t) return;
  if (t.classList.contains("lightbox")) return t.remove();
  if (t.id === "nav-overview") return go("overview");
  if (t.id === "nav-guide") return go("guide");
  if (t.dataset.f) { filter = t.dataset.f; return renderSide(); }
  if (t.dataset.app) return openApp(t.dataset.app);
  if (t.dataset.tab) { tab = t.dataset.tab; return renderApp(); }
  if (t.dataset.round) { const k = t.dataset.round; openRounds.has(k) ? openRounds.delete(k) : openRounds.add(k); return renderPane(); }
  if (t.dataset.brief) { sub = t.dataset.brief; return renderPane(); }
  if (t.hasAttribute("data-goproof")) { tab = "proof"; return renderApp(); }
  if (t.hasAttribute("data-gosess")) { tab = "sessions"; return renderApp(); }
  if (t.dataset.fn) { if (t.dataset.go || mapView === "tours") { mapView = "explore"; tourIdx = null; } mapSel = t.dataset.fn; renderPane(); $(".focus")?.scrollIntoView({block: "nearest", behavior: "smooth"}); return; }
  if (t.dataset.explain) {
    const id = t.dataset.explain, app = current, key = `${app}|${id}`;
    if (explaining.has(key)) return;
    explaining.add(key); renderPane();
    const r = await post("/api/fn/explain", {app, id});
    explaining.delete(key);
    if (fnCache[key]) fnCache[key].explained = r;
    if (current === app && tab === "map") renderPane();
    return;
  }
  if (t.dataset.mapview) { mapView = t.dataset.mapview; if (mapView !== "tours") tourIdx = null; return renderPane(); }
  if (t.dataset.tour) { tourIdx = +t.dataset.tour; tourStep = 0; renderPane(); $("#main").scrollTop = 0; return; }
  if (t.dataset.tourstep) { tourStep += +t.dataset.tourstep; renderPane(); $(".tourbar")?.scrollIntoView({block: "nearest"}); return; }
  if (t.dataset.tourgo) { tourStep = +t.dataset.tourgo; return renderPane(); }
  if (t.hasAttribute("data-tourexit")) { tourIdx = null; return renderPane(); }
  if (t.dataset.quiz) {
    const id = t.dataset.quiz, app = current, k = `${app}|${id}`;
    if (t.hasAttribute("data-qfresh")) delete quiz[k];
    quiz[k] = {busy: "Claude is writing 3 questions about this function… (about 15–30 seconds)"}; renderPane();
    const r = await post("/api/fn/quiz", {app, id});
    quiz[k] = r.error ? {error: r.error} : {stage: "ask", qs: r.questions, idx: 0, answers: []};
    if (current === app) renderPane();
    return;
  }
  if (t.hasAttribute("data-qnext") || t.hasAttribute("data-qprev") || t.hasAttribute("data-qhint") || t.hasAttribute("data-qretry")) {
    const k = `${current}|${mapSel}`, st = quiz[k];
    if (!st) return;
    const ta = $(".quiz .qa");
    if (ta) st.answers[st.idx] = ta.value;
    if (t.hasAttribute("data-qhint")) { st.showHint = st.idx; return renderPane(); }
    if (t.hasAttribute("data-qprev")) { st.idx--; return renderPane(); }
    if (t.hasAttribute("data-qretry")) { Object.assign(st, {stage: "retry", idx: 0, answers: [], showHint: null}); renderPane(); $(".quiz .qa")?.focus(); return; }
    const set = st.stage === "retry" ? st.retry : st.qs;
    if (st.idx < set.length - 1) { st.idx++; st.showHint = null; renderPane(); $(".quiz .qa")?.focus(); return; }
    const retry = st.stage === "retry", app = current, id = mapSel;
    st.busy = "Checking your answers…"; renderPane();
    const r = await post("/api/fn/quiz/grade", {app, id, answers: set.map((_, i) => st.answers[i] || ""), retry});
    st.busy = null;
    if (r.error) st.error = r.error;
    else Object.assign(st, {stage: retry ? "retryresults" : "results", results: r.results, retry: r.retry, answers: []});  // answers dropped once graded
    if (current === app) renderPane();
    return;
  }
  if (t.dataset.pathfrom) { pathFrom = t.dataset.pathfrom; renderPane(); $(".pathbox")?.scrollIntoView({block: "nearest", behavior: "smooth"}); return; }
  if (t.dataset.pathto) { pathTo = t.dataset.pathto; renderPane(); $(".pathbox")?.scrollIntoView({block: "nearest", behavior: "smooth"}); return; }
  if (t.hasAttribute("data-pathclear")) { pathFrom = pathTo = null; return renderPane(); }
  if (t.dataset.area) { const k = +t.dataset.area; mapOpenAreas.has(k) ? mapOpenAreas.delete(k) : mapOpenAreas.add(k); return renderPane(); }
  if (t.hasAttribute("data-mapfull")) { openThing(current, "map"); return; }
  if (t.hasAttribute("data-mapbuild")) {
    if (mapBusy) return;
    mapBusy = true; renderPane();
    const app = current, r = await post("/api/map/build", {app});
    mapBusy = false;
    if (app !== current) return;
    mapData = r.ok ? await api(`/api/map?app=${encodeURIComponent(app)}`) : {built: false};
    renderPane();
    if (!r.ok) $("#pane").insertAdjacentHTML("afterbegin", `<div class="notice"><b>Map build failed.</b><pre class="file">${esc(r.log || r.error)}</pre></div>`);
    return;
  }
  if (t.dataset.sess) { const k = t.dataset.sess; openSess.has(k) ? openSess.delete(k) : openSess.add(k); return renderPane(); }
  if (t.hasAttribute("data-newnote")) return newNote();
  if (t.dataset.kind) { t.parentNode.querySelectorAll("button").forEach(b => b.classList.toggle("on", b === t)); return; }
  if (t.hasAttribute("data-save")) return saveComposer(t.closest(".composer"));
  if (t.dataset.follow) {
    const card = t.closest(".note");
    if ($(".fbox", card)) return $(".fbox textarea", card).focus();
    t.closest(".nact").insertAdjacentHTML("beforebegin", `<div class="fbox"><textarea rows="2" placeholder="Ctrl+Enter saves"></textarea><button class="btn primary" data-fsave="${t.dataset.follow}">Save</button></div>`);
    $(".fbox textarea", card).focus(); return;
  }
  if (t.dataset.close || t.dataset.reopen) {
    await post("/api/notes/status", {id: +(t.dataset.close || t.dataset.reopen), status: t.dataset.close ? "done" : "open"});
    await loadNotes(); return rerender();
  }
  if (t.dataset.del) {
    if (t.dataset.sure !== "1") { t.dataset.sure = "1"; t.textContent = "Really delete?"; setTimeout(() => { t.dataset.sure = ""; t.textContent = "Delete"; }, 3000); return; }
    await post("/api/notes/delete", {id: +t.dataset.del}); await loadNotes(); return rerender();
  }
  if (t.dataset.img) { const lb = document.createElement("div"); lb.className = "lightbox"; lb.innerHTML = `<img src="${t.dataset.img}">`; return document.body.append(lb); }
  if (t.dataset.txt) fetch(t.dataset.txt).then(r => r.text()).then(x => { $("#txt").innerHTML = `<pre class="file">${esc(x)}</pre>`; });
});
document.addEventListener("click", e => {
  const c = e.target.closest(".clamp");
  if (c && !e.target.closest("a,.term")) c.classList.toggle("full");
});
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-fsave]");
  if (!b) return;
  const text = $("textarea", b.parentNode).value.trim();
  if (!text) return;
  await post("/api/notes/reply", {id: +b.dataset.fsave, text});
  await loadNotes(); rerender();
});

document.addEventListener("keydown", e => {
  if (e.target.matches("textarea,input")) {
    if (e.key === "Enter" && e.ctrlKey) {
      e.preventDefault();
      const c = e.target.closest(".composer"), f = e.target.closest(".fbox");
      if (c) saveComposer(c); else if (f) $("[data-fsave]", f).click();
      else if (e.target.matches(".qa")) $(".quiz [data-qnext]")?.click();
    }
    if (e.key === "Escape") e.target.blur();
    return;
  }
  if (e.ctrlKey || e.altKey) return;
  const lb = $(".lightbox");
  if (e.key === "Escape") { if ($(".pop")) return hideTerm(); if (lb) return lb.remove(); return go("overview"); }
  if (e.key === "r") return refresh();
  if (e.key === "n") { e.preventDefault(); return newNote(); }
  if (e.key === "/" && view === "app" && tab === "map") { e.preventDefault(); mapView = "explore"; renderPane(); const i = $("#mapq"); if (i) { i.focus(); i.select(); } return; }
  if (e.key === "g") return go("guide");
  if (["j", "k", "ArrowDown", "ArrowUp"].includes(e.key) && apps.length) {
    const names = [...document.querySelectorAll("#applist .app")].map(b => b.dataset.app);
    const i = view === "app" ? names.indexOf(current) : -1, down = e.key === "j" || e.key === "ArrowDown";
    const next = names[i < 0 ? (down ? 0 : names.length - 1) : Math.max(0, Math.min(names.length - 1, i + (down ? 1 : -1)))];
    if (next) { e.preventDefault(); openApp(next); }
    return;
  }
  const n = parseInt(e.key, 10);
  if (view === "app" && n >= 1 && n <= TABS.length) { tab = TABS[n - 1][0]; renderApp(); }
});

document.addEventListener("input", e => {
  if (e.target.id !== "mapq") return;
  mapQuery = e.target.value;
  const pos = e.target.selectionStart;
  renderPane();
  const i = $("#mapq"); if (i) { i.focus(); i.setSelectionRange(pos, pos); }
});

window.addEventListener("focus", () => refresh());

// deep links: ?view=guide · ?app=my-project&tab=notes · &pop=<term> opens that word's explainer
(async () => {
  const q = new URLSearchParams(location.search);
  await loadApps();
  if (q.get("view") === "guide") go("guide");
  if (q.get("app")) { if (q.get("tab")) tab = q.get("tab"); await openApp(q.get("app")); }
  const pop = q.get("pop") && document.querySelector(`.term[data-term="${CSS.escape(q.get("pop"))}"]`);
  if (pop) showTerm(pop);
})();
