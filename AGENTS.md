# Notes for coding agents

Read by Claude Code (through `CLAUDE.md`, a link to this file), Hermes, Codex, Cursor and other tools that load `AGENTS.md`.

- **What this is:** example code for a cheap-LLM review and fix pipeline (see `README.md`). Keep it small and readable: people copy and adapt it.
- **Python 3.10+, standard library only.** No third-party packages, no network in tests.
- **Tests:** `python3 -m unittest` must pass before every commit; add a test with every change (canned answers via `--answer-file`, never a live model call).
- **Style:** type hints on functions, short docstrings that say why, no em dashes anywhere (code, comments, docs).
- **Prompts:** a model's role and standing rules go in the system prompt (`ask_model.py --system-file`); the task and code go in the message.
- **Commits:** small and logical; no AI attribution trailers.
