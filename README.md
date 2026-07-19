# Debugger

An AI-powered, closed-loop automated debugging agent for Python projects. Given a runtime traceback, it uses AST-based workspace analysis, ChromaDB-backed RAG retrieval, and a multi-stage LLM pipeline (via Groq) to locate the buggy method, generate a patch, apply it, and dynamically verify the fix — rolling back and retrying on failure.

## How It Works

1. **Workspace Discovery** — `ast_utils.py` walks the project directory and parses every `.py` file with the `ast` module to catalog all classes, their methods, and source code.
2. **Vector Indexing (RAG)** — `rag_engine.py` indexes class docstrings/metadata into a local ChromaDB collection.
3. **Retrieval** — the traceback text is used as a query against ChromaDB to retrieve the most likely relevant classes.
4. **Codebase Navigation (LLM Stage 1)** — an LLM ("Software Architect" persona) is given the traceback and retrieved classes and picks the exact `Class.method` responsible for the crash.
5. **Dependency-Aware Slicing** — `ast_utils.py` traces both same-class (`self.method()`) and cross-class (imported class) dependencies of the target method via AST, so the LLM sees only the relevant code, not the whole file.
6. **Doc Enhancement (LLM Stage 1.5)** — a "Source Code Reviewer" persona generates an enhanced technical docstring describing data types and mismatch risks for the isolated code.
7. **Patch Generation (LLM Stage 2)** — a "Test Engineer" persona reviews the traceback, code, and enhanced docs, and returns a corrected method wrapped in `[PATCH]...[/PATCH]` tags.
8. **Auto-Repair & Verification (Stage 3)** — `repair_engine.py`:
   - Applies the patch to the correct file/line range (auto-resolving the right class even if the LLM patched a cross-class dependency instead of the original target).
   - Verifies syntax via `py_compile`.
   - Runs the project's `pytest` suite, if one exists (`tests/`, `test_*.py`, `*_test.py`, or `conftest.py` anywhere in the workspace). If no suite is found, this step is skipped and logged rather than failing the run.
   - Dynamically runs the patched method in a subprocess to verify it no longer crashes.
   - Rolls back to a backup and asks the LLM to retry (up to `max_retries`) if syntax, the test suite, or runtime verification fails.
9. Prints a final report of the modified file, class, method, and applied patch on success.

## Project Structure

- `debugger.py` — Main orchestration script that runs the full multi-stage debugging workflow.
- `ast_utils.py` — AST-based workspace scanning, internal/cross-class dependency tracing, and code extraction utilities.
- `rag_engine.py` — ChromaDB-based indexing and retrieval engine for code context.
- `repair_engine.py` — Patch application, syntax verification (`py_compile`), dynamic runtime verification, and patch parsing helpers.
- `payment_service.py`, `user_service.py`, `notification_service.py`, `target_code.py` — Example application modules used as the sandbox target for the debugger (contain an intentional cross-class type-mismatch bug for demo purposes).

## Setup

1. Create and activate a Python virtual environment:

```bash
python -m venv venv
source venv/bin/activate
```

2. Install required dependencies:

```bash
pip install groq python-dotenv chromadb
```

3. Create a `.env` file with your Groq API key (see `.env.example`):

```text
GROQ_API_KEY=your_groq_api_key_here
```

## Usage

Run the debugger from the repository root:

```bash
python debugger.py
```

By default it debugs a hardcoded traceback (a `TypeError` in `payment_service.py`'s `apply_processing_fee`, caused by `user_service.py`'s `get_user_balance` cross-class dependency). Edit the `runtime_stack_trace` variable in `debugger.py`'s `main()` to point at a different crash.

The script will:

1. Scan the workspace for Python classes and methods.
2. Index class metadata in a local, in-memory ChromaDB collection.
3. Query the vector store using the traceback text.
4. Identify the buggy target class/method with an LLM.
5. Slice out the target method plus its internal and cross-class dependencies.
6. Generate an enhanced docstring, then generate and apply a patch.
7. Verify syntax and dynamically execute the patched method, rolling back and retrying on failure (up to 3 attempts).
8. Print a final report of what was changed.

## Notes

This project is a prototype for automated debugging and repair workflows. LLM output and patch success vary depending on the model and runtime environment. Patches are applied directly to source files on disk (with an in-memory backup used for rollback within a single run) — review changes before committing.
