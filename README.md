# Debugger

This repository contains an AI-powered automated debugging system for Python projects. It uses AST analysis, RAG-based code retrieval, and LLM-driven patch generation to identify and repair runtime bugs.

## Key Features

- Workspace discovery via AST parsing
- Semantic search using ChromaDB and traceback guidance
- LLM-based target identification and patch generation
- Automatic patch application, syntax verification, and runtime validation
- Logging of all debugging stages, LLM prompts/responses, patch attempts, and performance metrics

## Project Structure

- `debugger.py` - Main orchestration script for the debugging workflow
- `ast_utils.py` - AST-based workspace scanning and code extraction utilities
- `rag_engine.py` - ChromaDB-based retrieval engine for code context
- `repair_engine.py` - Patch application, syntax checking, and runtime validation
- `payment_service.py`, `user_service.py`, `notification_service.py`, `target_code.py` - Example application modules

## Setup

1. Create and activate a Python virtual environment:

```bash
python -m venv venv
source venv/bin/activate
```

2. Install required dependencies (example):

```bash
pip install groq python-dotenv chromadb
```

3. Create a `.env` file with your Groq API key:

```text
GROQ_API_KEY=your_api_key_here
```

## Usage

Run the debugger from the repository root:

```bash
python debugger.py
```

The script will:

1. Scan the workspace for Python classes and methods
2. Index class metadata in a local ChromaDB collection
3. Query the RAG store using a traceback example
4. Identify the buggy target method with an LLM
5. Generate and apply a patch
6. Verify syntax and run the modified method dynamically

## Notes

This project is designed as a prototype for automated debugging and repair workflows. The exact LLM behavior and patch success may vary depending on model output and runtime environment.
