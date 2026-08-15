# Repository Guidelines

## Project Structure & Module Organization

`novel/` contains the Python runtime, CLI, FastAPI service, orchestration, and `stage0`–`stage7`. Shared Pydantic schemas, judges, rules, and invariants belong in `contracts/`; do not duplicate them in consumers. Tests and documentation guards live in `tests/`. The React/Vite/TypeScript console is under `web/src/`, organized into `api/`, `screens/`, and reusable `components/`. Contract docs live in `docs/schema/`; product constraints are tracked in `docs/REQUIREMENTS.md` and `docs/BACKEND_CONTRACT.md`. Keep `outputs/`, `Test_data/`, `.llm_cache/`, and root `.env` local.

## Build, Test, and Development Commands

Run backend commands from the repository root using the `novel_refactor` Conda environment:

```powershell
conda run -n novel_refactor python -m pytest -q
conda run -n novel_refactor python -m novel.api --base <output-dir>
```

The first runs pytest; the second starts the API on `127.0.0.1:8765`. From `web/`, run:

```powershell
npm ci
npm run typecheck
npm run seams
npm run build
npm run dev
```

`seams` enforces frontend data and design boundaries; `dev` serves Vite on `127.0.0.1:5173`.

## Coding Style & Naming Conventions

Python uses four spaces, `snake_case` modules/functions, and `PascalCase` classes. Preserve type annotations and existing store/orchestration disk boundaries. TypeScript uses two spaces, single quotes, no semicolons, `PascalCase` components, and `useXxx` hooks. Screens and components consume hooks/API adapters; they must not call `fetch` or import mocks directly. No repository-wide formatter is configured, so match adjacent code and rely on typecheck and seam guards.

## Testing Guidelines

Pytest 8 is the backend framework. Name files `test_<area>.py` and tests `test_<behavior>`. During iteration, run a focused file such as `conda run -n novel_refactor python -m pytest -q tests/test_stage6.py`, then the full suite. Contract, schema, or layering changes need positive and counterexample coverage. No numeric coverage threshold is configured.

## Commit & Pull Request Guidelines

History uses Conventional Commit-style subjects such as `feat(stage6): ...`, `fix(cli): ...`, `docs: ...`, and `refactor: ...`. Keep commits focused and scope them by stage or subsystem. Pull requests should explain behavior and contract impact, list validation commands, link issues or requirement IDs, and include UI screenshots. Call out real-LLM or paid-path testing explicitly.

## Security & Configuration

Copy `.env.example` to `.env` for local secrets and never commit API keys, copyrighted corpus text, caches, or generated outputs. Keep the API loopback-only. Use `web/.env` to select mock versus real API behavior, and verify real mode against the local service before claiming integration completion.
