<div align="center">

<img src="docs/assets/logo.png" alt="Holons" width="120" align="right" />

# Holons

**Your AI company, on your own machine.**
Hire a team of AI agents, give each one a name and a role, assign work
from a chat window, and watch the output land.
A one-person shop today, a whole department tomorrow — same app,
swappable backing DB.

> *"A Holon is both a whole and a part. Each agent in Holons is autonomous
> enough to handle its own work, yet composes cleanly into a larger team."*
> — after Arthur Koestler, *The Ghost in the Machine* (1967)

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/jhk482001/Holons/actions/workflows/ci.yml/badge.svg)](https://github.com/jhk482001/Holons/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/jhk482001/Holons?include_prereleases)](https://github.com/jhk482001/Holons/releases)
[![Stars](https://img.shields.io/github/stars/jhk482001/Holons?style=social)](https://github.com/jhk482001/Holons/stargazers)

</div>

![Lead proposes a full workflow from a one-line request](docs/assets/screenshots/02b-dialog-lead-response.png)

> Tell Lead what you want; it decomposes the task, drafts a workflow, and
> hands you Edit / Save / Run Now. No YAML, no code.
>
> 🎬 **[Watch the full walkthrough](docs/assets/demo-walkthrough.webm)** — login → dialog → groups → group chat → workflow editor → dashboard → library.

---

## ⚡ Quick start

Pick one of three modes. Personal is by far the easiest.

### Personal desktop (recommended)

Download the latest `.dmg` / `.msi` / `.AppImage` from
[Releases](https://github.com/jhk482001/Holons/releases), install,
launch, and log in with **`admin` / `admin`**. The app bundles a Python
sidecar with SQLite — no Docker, no API keys to set up.

> **macOS**: builds are unsigned. First launch: right-click → Open → Open
> Anyway. Standard for open-source apps without a paid signing cert.
>
> To uninstall or fully reset to the first-run state, see
> [docs/BUILD.md#uninstall--full-reset-macos](docs/BUILD.md#uninstall--full-reset-macos).
> Dragging the `.app` to Trash alone leaves your login and local DB behind
> in `~/Library/Application Support/com.holons.desktop/` and `~/.agent_company/`.

### Self-host (local dev or server)

Requires Python 3.9+, Node 18+, Rust (for the desktop build), Docker for
the Postgres option.

```bash
git clone https://github.com/jhk482001/Holons.git
cd Holons

cp .env.example .env
pip install -r requirements.txt
cd frontend && npm install && cd ..

# --- Backend ---
# Easiest: SQLite, single binary, auto-provisions admin user.
python -m backend.standalone --port 8087
# Or: Postgres (docker compose up -d postgres first), multi-user ready.
# python -m backend.app

# --- Frontend (second shell) ---
cd frontend && npm run dev        # http://localhost:5173

# Optional: seed demo user + two showcase teams
python -m demo.seed_demo
```

Login: `admin` / `admin`, or `jay` / `demo` after running the seed.

### Managed / production deploy

See **[docs/BUILD.md](docs/BUILD.md)** for Docker, TLS, reverse proxy, and
the GitHub Actions release pipeline.

---

## 🎯 Why this, instead of another framework

Most multi-agent projects are **SDKs** (CrewAI, AutoGen, LangGraph) or
**no-code canvases** (Dify, Langflow). Agent Company is neither — it's a
**personal-scale management UI** that treats agents the way an operator
treats a small team:

- **Hire** — each agent has a name, a face, a role title, a system prompt.
- **Assign** — chat with Lead; it decomposes the task and drafts a workflow.
- **Coordinate** — groups fan tasks out in parallel, or round-robin them.
- **Observe** — dashboard shows who's idle, who's busy, what today cost.
- **Review** — open a group chat and sit in the room while they deliberate.

The same app runs as a single binary + SQLite on your laptop, or as a
Postgres-backed multi-user deploy on a server. Works on macOS, Windows,
Linux.

---

## 🧭 A quick tour

### 1. Lead designs the workflow for you

The hero shot above shows Lead turning "spin up a pitch for a B2B AI
accountant" into a three-stage flow, with cost estimate and a Run Now
button. Direct answers, workflow proposals, and resource-conflict hints
all live in the same chat.

🎥 [`videos/01-dialog-workflow-proposal.webm`](docs/assets/videos/01-dialog-workflow-proposal.webm)

### 2. Sit in a room and talk to a team

A "group chat" is exactly what it sounds like: you, and every agent in a
group, in one thread. Replies come in parallel (everyone answers at once)
or sequential (round-robin, each one reads the last). Hit **"Let them
continue"** and the agents keep riffing without you for 1–10 rounds.

![Group chat with Writers Room](docs/assets/screenshots/04b-group-chat-active.png)

🎥 [`videos/02-group-chat.webm`](docs/assets/videos/02-group-chat.webm)

### 3. Workflows are visual and editable

Drag nodes, swap agents, change parallel vs. sequential, override a prompt
for one step — no YAML, no code.

![Workflow editor](docs/assets/screenshots/06-workflow-editor.png)

🎥 [`videos/03-workflow-editor.webm`](docs/assets/videos/03-workflow-editor.webm)

### 4. See who's working and what it cost

Real-time Gantt chart of agent activity, today's cost, queue depth per
agent, and who's idle.

![Dashboard](docs/assets/screenshots/07-dashboard.png)

🎥 [`videos/04-dashboard.webm`](docs/assets/videos/04-dashboard.webm)

### 5. Plug in the outside world

Share skills, tools, and MCP servers across all your agents from one
place. Built-in tools ship out of the box; add your own via the UI.

![Library](docs/assets/screenshots/08-library.png)

🎥 [`videos/05-library.webm`](docs/assets/videos/05-library.webm)

<details>
<summary>More screenshots</summary>

| | |
|---|---|
| ![Login](docs/assets/screenshots/01-login.png) | ![Dialog — empty state](docs/assets/screenshots/02-dialog.png) |
| ![Groups](docs/assets/screenshots/03-groups.png) | ![Group chat — room](docs/assets/screenshots/04-group-chat.png) |
| ![Workflows](docs/assets/screenshots/05-workflows.png) | |

</details>

---

## 📦 What's inside

| | |
|---|---|
| **Two surfaces, one backend** | Rich web console (React + Flask) plus a transparent desktop overlay (Tauri + Rust + React). |
| **Lead agent** | Your personal secretary — takes natural-language requests, decomposes them, proposes a runnable workflow. |
| **Teams** | Parallel or sequential groups; fan out, debate, or hand off with full context. |
| **Group chat rooms** | Observable deliberation; *"let them continue"* for 1–10 rounds. |
| **Visual workflow editor** | Drag nodes, swap agents, override a prompt per step. |
| **Library** | Share skills, tools, and MCP servers across agents from one place. |
| **Pluggable LLMs** | AWS Bedrock, Anthropic, OpenAI, Gemini, MiniMax. Pick per agent. |
| **Two backends** | SQLite (personal, single file) or Postgres + pgvector (team / prod). |

---

## 🎬 Demo teams

Two showcase setups ship in [`demo/seed_demo.py`](demo/seed_demo.py):

- **Screenwriting Room** — Jade (showrunner), Eli (writer), Mia (script
  doctor), Leo (structure consultant). Writers-room flow.
- **Startup Pitch Council** — three founder archetypes draft a pitch, three
  VC archetypes critique, a final polish pass outputs a markdown pitch deck.

Log in as **`jay`** / **`demo`** and you'll see both teams pre-loaded.

---

## 🗂 Repository layout

```
holons/
├── backend/              Flask app + services (LLM clients, engine, queue, Lead)
├── frontend/             React + Vite web console
├── desktop/              Tauri desktop overlay; embeds the web build
├── demo/                 Showcase seed data + Playwright walkthrough
├── docs/                 Architecture, build, development guides + assets
├── build/                Build scripts (sidecar, dmg)
├── docker-compose.yml    Postgres + pgAdmin for dev
└── .github/workflows/    CI + multi-platform release pipelines
```

---

## 📚 Docs

| | |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the pieces fit together |
| [docs/BUILD.md](docs/BUILD.md) | Build the desktop binary / Docker image |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local setup, tests, conventions |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |

---

## 💬 Community

- **Bugs / feature requests** → [GitHub Issues](https://github.com/jhk482001/Holons/issues)
- **Questions / show & tell** → [GitHub Discussions](https://github.com/jhk482001/Holons/discussions)
- **Security** — please email the maintainer rather than filing a public issue.

## 📝 License

[MIT](LICENSE). Use it, fork it, ship it.
