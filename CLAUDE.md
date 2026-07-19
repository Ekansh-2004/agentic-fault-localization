# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A prototype AI debugging agent. It takes a Python runtime traceback, uses AST parsing + ChromaDB RAG retrieval to find the buggy class/method across the workspace, calls Groq LLMs through a 3-stage pipeline (Architect → Doc Reviewer → Test Engineer) to generate a patch, then applies/verifies/rolls back that patch against real files on disk. See `README.md` for the full pipeline walkthrough.

## Architecture map

- `debugger.py` — orchestrator; `main()` runs the entire pipeline linearly. No classes, no CLI args — the target traceback is a hardcoded string in `main()`.
- `ast_utils.py` — pure AST utilities (workspace scanning, dependency tracing, line-range lookup, function-body extraction). No I/O side effects besides reading source files.
- `rag_engine.py` — thin wrapper around an in-memory `chromadb.Client()` (recreated every run; nothing persists between runs).
- `repair_engine.py` — the only module that mutates files on disk (`apply_patch_to_file`) and spawns subprocesses (`verify_syntax` via `py_compile`, `run_test_suite` via `pytest` if a suite is detected, `verify_runtime_execution` via a generated `temp_test_runner.py`).
- `payment_service.py`, `user_service.py`, `notification_service.py`, `target_code.py` — sandbox fixtures the debugger targets. `payment_service.py`/`user_service.py` contain the intentional cross-class bug (`get_user_balance` returns `str` type mismatch causing `TypeError`).

## Working in this repo

- **File mutation is real, not simulated.** Running `debugger.py` will rewrite `payment_service.py`/`user_service.py` in place if a patch is applied. `repair_engine.py` keeps only an in-memory backup string for rollback within a single run — there is no on-disk backup. Check `git status`/`git diff` after any test run of the debugger, and be ready to `git checkout` the sandbox files back to their buggy baseline if you need to re-demo the fix.
- **`ast_utils.discover_classes_in_workspace` hardcodes an exclusion list** of the debugger's own modules (`debugger.py`, `ast_utils.py`, `rag_engine.py`, `repair_engine.py`, `temp_test_runner.py`) so it doesn't try to "debug itself." If you add a new core module, add it to that exclusion list too.
- **`temp_test_runner.py` is generated and deleted at runtime** by `verify_runtime_execution` — it's expected to not exist in the repo; don't check it in if you see it appear mid-run (it should self-delete, but confirm on crash).
- **Model/config is hardcoded** in `debugger.py` (`CLOUD_MODEL = "llama-3.1-8b-instant"`, `max_retries = 3`) — there's no config file or CLI flags.
- **Requires `GROQ_API_KEY`** in `.env` (see `.env.example`). The script exits immediately if it's missing.
- No test suite, linter config, or CI currently exists in this repo — there is nothing to run beyond `python debugger.py` itself to validate changes.

## Conventions observed

- Modules use plain function-based APIs (no classes in `ast_utils.py`/`rag_engine.py`/`repair_engine.py`), each with a one-line docstring on the module and each public function.
- Console output uses emoji-prefixed status lines (`✅`, `❌`, `⚠️`, `🔍`, etc.) and `=====`-banner section headers — match this style if extending `debugger.py`'s console output.
- `venv/` and `__pycache__/` are excluded from AST workspace scanning and from git (`.gitignore`); `.env` is also gitignored.
