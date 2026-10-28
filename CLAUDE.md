# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

KTRU classifier (B2B-RTS hackathon, task 5). A user uploads a procurement spec (xlsx/csv/docx/pdf/txt); the system extracts attributes per row, picks a KTRU catalog code with a confidence %, a human reviews doubtful rows, and exports xlsx/csv. `README.md` (Russian) describes every module and the data flow — read the relevant section before touching an area; the old measurements and decision log are in `docs/notes-and-measurements.md`. `OWNERSHIP.md` defines per-person file zones and cross-zone contracts; if a change crosses a zone, say so to the user instead of silently editing.

## Commands

Backend (Python 3.12; a venv is at the repo root `.venv`, README suggests `backend/.venv`). Run from `backend/`:

```bash
python -m pytest -q tests                      # all tests, no network (SQLite, small fake catalogs)
python -m pytest -q tests -k test_parse_json_answer   # single test
ruff check --line-length 100 . && ruff format --check --line-length 100 .
uvicorn api:app --reload --port 8000           # needs env vars below
```

Local backend env (without Docker): `DATABASE_URL=sqlite:///./dev.db`, `CATALOG_PATH=../db/ktru_catalog.jsonl.gz`, `CACHE_DIR=./.cache`, `UPLOAD_DIR=./.uploads`, `CATALOG_VECTORS=../db/ktru_vectors_yandex.npz`, `YANDEX_EMBED_API_KEY` and `YANDEX_EMBED_FOLDER_ID` (without the key, search falls back to word matching).

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
file→rows    LLM/rules attrs   Yandex vectors  in-memory exact cosine   confidence %, zone, status
```

- **Queue is the `jobs` table**, no Redis/Celery. `WORKERS` (default 2) threads inside the backend process poll for `queued` jobs with `SELECT … FOR UPDATE SKIP LOCKED` plus a conditional `UPDATE` (`claim_job` / `worker_loop` / `run_job` / `process_items` in `api.py`). Items are written in batches of 20 (`BATCH`) and `jobs.processed` drives the UI progress. Jobs left `processing` at startup are re-queued. Schema is created by `create_all`; there are no migrations.
- **Two different neural nets.** (1) external LLMs from `providers.py` (five YandexGPT keys of Yandex AI Studio first, `Api-Key` auth plus the folder in `OpenAI-Project` and the `gpt://<folder>/<model>` URI; GigaChat as the last resort; `chooser.py` gives a second opinion on `needs_review` rows) extract `{name, brand, model, characteristics, search_text, measure}`; (2) Yandex AI Studio embeddings (`embedder.py`, 256 numbers, `text-search-query` for rows, one text per request, `YANDEX_EMBED_RPS`, key `YANDEX_EMBED_API_KEY` + `YANDEX_EMBED_FOLDER_ID`) vectorise text. There are no local models and no Ollama. The catalog vectors of the same model come from `CATALOG_VECTORS` (`db/ktru_vectors_yandex.npz`, not in git, built once by `scripts/embed_catalog.py` with `text-search-doc`, matched by code); without the file the catalog is `error` with the reason in `/api/health` `catalog_error`, and `api.load_catalog` refuses vectors of another size. The old bge-m3 vectors (`embedding_b64`) are still in the dump but are not read. Only YandexGPT and GigaChat are LLM providers; other names in `LLM_PROVIDERS` are skipped. The code itself is chosen by exact cosine over the whole 81 509×256 matrix (no HNSW — it lost up to 14 % of correct codes) plus hand-written rerank rules in `Catalog._rank` (`search.py`). `Catalog._anchored` keeps the product the row names: when the best candidate lacks the words of a catalog name written at the start of the row (`anchors` = row texts, not the LLM search hint), the codes of that name are ranked instead, unless the best is the same product (`same_product`: the same words in any order, or a longer name starting with the row's). A decimal number is one word (`2,5` is `2.5`, not `2` and `5`); changing `words()` needs a `CACHE_VERSION` bump. `with_synonyms` maps customer words to catalog characteristic names (`жирность` → `массовая доля жира`, `вес` → `масса`) for the ranking only. `UNIT_BONUS` 0.01: twins often differ only by the unit; a unit the customer wrote that `unit_key` does not know blocks the LLM guess (`api.unit_hints`).
- **LLM output is never trusted:** `extractor.accept` keeps a value only if it literally occurs in the source row; the model receives rows as JSON data, not instructions. When the key is missing, the gateway is down, or the 240 s per-job budget (`LLM_BUDGET`) runs out, `extractor.rules` builds attributes and the reason is stored in `attributes.fallback` (`no_key|unavailable|budget|error|missing`) and shown in the UI. A failing provider is paused (15–30 s for network errors and 429/5xx, 120 s for 401–403) and batches go to the others.
- `GIGACHAT_PARALLEL=1` is deliberate (Freemium answers 429 to a second concurrent request).
- Catalog loading takes ~20 s on first start and its metadata is cached in `CACHE_DIR`; `/api/health` reports `catalog: loading|ready|error` (+ `catalog_error`), `llm: on|off|down` and `embeddings: on|off|down|unknown`. When the embeddings are down the search silently degrades to words: such rows get `attributes.words_only`, low confidence, and the UI shows a banner (home) and a notice (job page).
- Confidence is a logistic of the `top1` score (capped at 1.0) and the margin to the second candidate taken from the uncapped `raw_score` (`W_SCORE`, `W_MARGIN`, `BIAS`, `MAX_MARGIN` in `confidence.py`; one fit for the Yandex embeddings of 28.10.2026, see the comment there); ≥85 % `high`/`auto`, 60–85 % `medium`, <60 % `low`; cancelled codes and non-products are never `auto`. When the second opinion picks another candidate, the row has no confidence (null, «—»): the search's percent was about another code. `confidence.review_reason` says in words why a row needs review (twins differing by unit or characteristic, weak match); it is kept in `attributes.review`.
- Item statuses: `auto / needs_review / corrected / accepted / failed` (`ITEM_STATUSES` in `api.py`). Manual edits: `PATCH /items/{id}` (code), `PUT /items/{id}/attributes` (re-runs search, status `corrected`), `POST /items/{id}/accept`, `POST /items/{id}/undo-code`, `POST /items/{id}/revert`, bulk `POST /jobs/{id}/accept` and `POST /jobs/{id}/revert`, `POST /items/{id}/apply-same` (the code goes to waiting rows of the job with the same text). A manual code or accepting a doubtful row is remembered in the `decisions` table by the row text and applied to the same row of later files (`REMEMBER_DECISIONS`, `attributes.remembered`); revert, undo and deleting the job take it back. `JOBS_KEEP_DAYS` (default 0) deletes old finished jobs. Numbers in ranking only meet numbers of the same quantity (`unit_of`/`same_quantity` in `search.py`).
- Frontend: React 19 + antd 6 + Vite, no router library (History API in `router.ts`: `/`, `/catalog`, `/jobs`, `/jobs/<id>`, `/jobs/<id>/items/<id>`; `/` and `/catalog` are two tabs of one `UploadScreen`), polling every second in `JobScreen`. `api.ts` holds all request functions and the TS types mirroring backend contracts (`Item.attributes`, `Candidate`). The tour (`Tutorial.tsx`) is an antd `Modal` with pictures from `public/tour/*.webp`; regenerate them with `node scripts/tour-shots.mjs` (needs `npm run dev` and the stack) after visible UI changes. `public/primer.xlsx` is the example file and the downloadable template.

## Conventions that are easy to break

- Code and comments in English; user-facing text in Russian. In the frontend **all** UI strings live in `src/texts.ts` and **all** colors/layout in `src/theme.css` (CSS variables, one light theme; no colors in components). One UI library only (antd), icons from lucide-react, everything keyboard-accessible.
- Python: pinned `==` versions in `requirements.txt`, ruff line length 100, short functions, files ≲ 300 lines (`api.py`, `search.py` already exceed it — don't grow them gratuitously).
- Accuracy changes (prompt, ranking weights, thresholds) must be justified with before/after measurements on every labelled set: the real file, 292 and 1000 rows with their modified copies, the EIS form and its copy, `dataset/human_2026_10_28` (770 graded rows); improving one while hurting another is not accepted. Ranking and confidence variants can be replayed offline on saved LLM attributes: `scripts/bench.py` (live run of all sets, rows saved), `scripts/replay.py` (inside the backend container), `scripts/compare_runs.py` (row by row, exit 1 on a regression); the LLM answers vary between runs, so one live run per variant is not proof for a single row.
- Do not weaken security limits: upload size/signature checks, zip-bomb limits, value-vs-source check, export formula escaping (`cell()` prefixes `'` on `= + - @`).
- Never hand-edit `db/ktru_catalog.jsonl.gz` or `frontend/package-lock.json`. Secrets only in `.env` (git-ignored).
- Commit messages in English.
