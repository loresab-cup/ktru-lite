# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

KTRU classifier (B2B-RTS hackathon, task 5). A user uploads a procurement spec (xlsx/csv/docx/pdf/txt); the system extracts attributes per row, picks a KTRU catalog code with a confidence %, a human reviews doubtful rows, and exports xlsx/csv. `README.md` (Russian) is detailed and accurate — read the relevant section before touching an area. `OWNERSHIP.md` defines per-person file zones and cross-zone contracts; if a change crosses a zone, say so to the user instead of silently editing.

## Commands

Backend (Python 3.12; a venv is at the repo root `.venv`, README suggests `backend/.venv`). Run from `backend/`:

```bash
python -m pytest -q tests                      # all tests, no network (SQLite, small fake catalogs)
python -m pytest -q tests -k test_parse_json_answer   # single test
ruff check --line-length 100 . && ruff format --check --line-length 100 .
uvicorn api:app --reload --port 8000           # needs env vars below
```

Local backend env (without Docker): `DATABASE_URL=sqlite:///./dev.db`, `CATALOG_PATH=../db/ktru_catalog.jsonl.gz`, `CACHE_DIR=./.cache`, `UPLOAD_DIR=./.uploads`, `OLLAMA_URL=http://localhost:11434` (without Ollama, search falls back to word matching).

Parser tests read fixtures from `../b2b/backend/tests/fixtures` (override with `B2B_FIXTURES`); they are skipped when absent.

Frontend (Node 24), from `frontend/`:

```bash
npm ci
npm run dev        # :5173, proxies /api to localhost:8000
npm run lint       # oxlint
npm run build      # tsc -b && vite build
```

Full stack: `cp .env.example .env`, then `docker compose up --build` → http://localhost:3000 (Swagger at :8000/api/docs). Needs `db/ktru_catalog.jsonl.gz` (~344 MB, not in git). After editing `.env` use `docker compose up -d backend`.

The pre-change gate from OWNERSHIP.md is: pytest + ruff check + ruff format --check (backend) and `npm run lint` + `npm run build` (frontend).

## Architecture

Flat backend (no package): `api.py` is the only entrypoint; the pipeline modules are imported by it.

```
parser.py → extractor.py → embedder.py → search.py → confidence.py   (orchestrated in api.py)
file→rows    LLM/rules attrs   Ollama bge-m3   in-memory exact cosine   confidence %, zone, status
```

- **Queue is the `jobs` table**, no Redis/Celery. `WORKERS` (default 2) threads inside the backend process poll for `queued` jobs with `SELECT … FOR UPDATE SKIP LOCKED` plus a conditional `UPDATE` (`claim_job` / `worker_loop` / `run_job` / `process_items` in `api.py`). Items are written in batches of 100 and `jobs.processed` drives the UI progress. Jobs left `processing` at startup are re-queued. Schema is created by `create_all`; there are no migrations.
- **Two different neural nets.** (1) External GigaChat (`extractor.py`) extracts `{name, brand, model, characteristics, search_text, measure}`; (2) local bge-m3 via Ollama (`embedder.py`) vectorises text. The catalog dump already contains bge-m3 vectors, so `EMBED_MODEL` must match it. The code itself is chosen by exact cosine over the whole 81 509×1024 matrix (no HNSW — it lost up to 14 % of correct codes) plus hand-written rerank rules in `Catalog._rank` (`search.py`).
- **LLM output is never trusted:** `extractor.accept` keeps a value only if it literally occurs in the source row; the model receives rows as JSON data, not instructions. When the key is missing, the gateway is down, or the 150 s per-job budget runs out, `extractor.rules` builds attributes and the reason is stored in `attributes.fallback` (`no_key|unavailable|budget|error|missing`) and shown in the UI. After an `unavailable` failure the job goes straight to rules for 120 s.
- `GIGACHAT_PARALLEL=1` is deliberate (Freemium answers 429 to a second concurrent request).
- Catalog loading takes ~20 s on first start and is cached as a matrix in `CACHE_DIR`; `/api/health` reports `catalog: loading|ready|error` and `llm: on|off|down`.
- Confidence is a logistic of `top1` score and `margin` to the second candidate (`W_SCORE`, `W_MARGIN`, `BIAS` in `confidence.py`); ≥85 % `high`/`auto`, 60–85 % `medium`, <60 % `low`; cancelled codes and non-products are never `auto`.
- Item statuses: `auto / needs_review / corrected / accepted / failed` (`ITEM_STATUSES` in `api.py`). Manual edits: `PATCH /items/{id}` (code), `PUT /items/{id}/attributes` (re-runs search), `POST /items/{id}/accept`.
- Frontend: React 19 + antd 6 + Vite, no router library (History API in `App.tsx`: `/` and `/jobs/<id>`), polling every second in `JobScreen`. `api.ts` holds all request functions and the TS types mirroring backend contracts (`Item.attributes`, `Candidate`).

## Conventions that are easy to break

- Code and comments in English; user-facing text in Russian. In the frontend **all** UI strings live in `src/texts.ts` and **all** colors/layout in `src/theme.css` (CSS variables for light/dark themes; no colors in components). One UI library only (antd), icons from lucide-react, everything keyboard-accessible.
- Python: pinned `==` versions in `requirements.txt`, ruff line length 100, short functions, files ≲ 300 lines (`api.py`, `search.py` already exceed it — don't grow them gratuitously).
- Accuracy changes (prompt, ranking weights, thresholds) must be justified with before/after measurements on both a real file and the draft reference set; improving one while hurting the other is not accepted.
- Do not weaken security limits: upload size/signature checks, zip-bomb limits, value-vs-source check, export formula escaping (`cell()` prefixes `'` on `= + - @`).
- Never hand-edit `db/ktru_catalog.jsonl.gz` or `frontend/package-lock.json`. Secrets only in `.env` (git-ignored).
- Commit messages in English.
