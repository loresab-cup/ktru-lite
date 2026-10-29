# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

KTRU classifier (B2B-RTS hackathon, task 5). A user uploads a procurement spec (xlsx/csv/docx/pdf/txt); the system extracts attributes per row, picks a KTRU catalog code with a confidence %, a human reviews doubtful rows, and exports xlsx/csv. `README.md` (Russian) covers startup; `docs/backend-structure.md` describes modules and `docs/case5-architecture.md` the data flow — read the relevant section before touching an area; the old measurements and decision log are in `docs/notes-and-measurements.md`. `OWNERSHIP.md` defines per-person file zones and cross-zone contracts; if a change crosses a zone, say so to the user instead of silently editing.

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

Full stack: `cp .env.example .env`, then `docker compose up --build` → http://localhost:3000 (Swagger at :8000/api/docs). Both `db/` catalog files (~344 MB and ~90 MB) are not in git: the one-shot `catalog` service (`backend/fetch_catalog.py`) downloads missing ones from the release `catalog_yandex` and checks their SHA-256; a failure is only logged (a private repository answers 404). After editing `.env` use `docker compose up -d backend worker scheduler`. `python scripts/acceptance.py --live` is the end-to-end check (paid).

The pre-change gate from OWNERSHIP.md is: pytest + ruff check + ruff format --check (backend) and `npm run lint` + `npm run build` (frontend).

## Architecture

`api.py` assembles FastAPI. HTTP endpoints live in `routes/`; `tasks.py` runs Celery tasks, `job_queue.py` publishes jobs to Redis, `workers.py` prepares the catalog and cleans old jobs, `job_store.py` manages job state, and `pipeline.py` processes specifications. See `docs/backend-structure.md` and `docs/database-migrations.md`.

```
parser.py → extractor.py → embedder.py → search.py → confidence.py   (orchestrated in pipeline.py)
file→rows    LLM/rules attrs   Yandex vectors  pgvector exact inner product   confidence %, zone, status
```

- **Queue: Celery + Redis.** API commits a queued row then publishes its UUID. A separate threads worker (`concurrency=2`) processes it; Beat republishes pending jobs every 15 s and runs cleanup. PostgreSQL session advisory locks prevent concurrent duplicate processing; late acknowledgements allow redelivery after a crash. Reprocessing preserves manually accepted/corrected rows. Cancellation is polled from PostgreSQL. API has no processing thread. Startup applies Alembic migrations (head `0002_vectors`) before catalog loading; legacy schemas require the explicit checked adoption procedure. Redis and worker health are separate from API health.
- EIS form (`parse_eis_rows`, a header with «Наименование характеристики»): one position spans several rows; a range in the `>, >=` / `Min` / `<, <=` / `Max` columns becomes «≥ 92 и < 95», and a row with a value but no characteristic name adds one more value to the characteristic above. Test set: `dataset/eis/ЕИС_37_*` (37 positions of 7 real EIS purchases).
- PDF: tables are read with pdfplumber (`parser.read_pdf`): one position per table row, a row whose rest opens the next page without its number is joined back (`continues`), repeated headers dropped; a PDF without tables is read line by line with pypdf. `extractor.product_name` trims a confirmed brand/model, an accessory's device («для планшета Apple iPad Air») and a confirmed number tail from the displayed name only (search does not use it).
- **Two different neural nets.** (1) external LLMs from `providers.py` (five YandexGPT keys of Yandex AI Studio; `X_PARALLEL=auto`, the default, shares the 5 generations a cloud runs at once between the keys of one folder — `providers.share_cloud_slots`; `Api-Key` auth plus the folder in `OpenAI-Project` and the `gpt://<folder>/<model>` URI; `chooser.py` gives a second opinion on `needs_review` rows) extract `{name, brand, model, characteristics, search_text, measure}`; (2) Yandex AI Studio embeddings (`embedder.py`, 256 numbers, `text-search-query` for rows, one text per request, `YANDEX_EMBED_RPS`, key `YANDEX_EMBED_API_KEY` + `YANDEX_EMBED_FOLDER_ID`) vectorise text. There are no local models and no Ollama. The catalog vectors of the same model come from `CATALOG_VECTORS` (`db/ktru_vectors_yandex.npz`, not in git, built once by `scripts/embed_catalog.py` with `text-search-doc`, matched by code); without the file the catalog is `error` with the reason in `/api/health` `catalog_error`, and `workers.load_catalog` refuses vectors of another size. The old bge-m3 vectors (`embedding_b64`) are still in the dump but are not read. Only YandexGPT is an LLM provider; other names in `LLM_PROVIDERS` are skipped. The code itself is chosen by exact pgvector inner product over the whole 81 509×256 normalized catalog (no HNSW — it lost up to 14 % of correct codes) in PostgreSQL (`vector_store.py`, migration `0002_vectors`) plus hand-written rerank rules in `Catalog._rank` (`search.py`). `Catalog._anchored` keeps the product the row names: when the best candidate lacks the words of a catalog name written at the start of the row (`anchors` = row texts, not the LLM search hint), the codes of that name are ranked instead, unless the best is the same product (`same_product`: the same words in any order, or a longer name starting with the row's). A decimal number is one word (`2,5` is `2.5`, not `2` and `5`); changing `words()` needs a `CACHE_VERSION` bump. `with_synonyms` maps customer words to catalog characteristic names (`жирность` → `массовая доля жира`, `вес` → `масса`) for the ranking only. `UNIT_BONUS` 0.01: twins often differ only by the unit; a unit the customer wrote that `unit_key` does not know blocks the LLM guess (`pipeline.unit_hints`).
- **LLM output is never trusted:** `extractor.accept` keeps a value only if it literally occurs in the source row; the model receives rows as JSON data, not instructions. When the key is missing, the gateway is down, or the 240 s per-job budget (`LLM_BUDGET`) runs out, `extractor.rules` builds attributes and the reason is stored in `attributes.fallback` (`no_key|unavailable|budget|error|missing`) and shown in the UI. A failing provider is paused (15–30 s for network errors and 429/5xx, 120 s for 401–403) and batches go to the others.
- Catalog loading takes ~20 s on first start and its metadata is cached in `CACHE_DIR`; `/api/health` reports `catalog: loading|ready|error` (+ `catalog_error`), `llm: on|off|down` and `embeddings: on|off|down|unknown`. When the embeddings are down the search silently degrades to words: such rows get `attributes.words_only`, low confidence, and the UI shows a banner (home) and a notice (job page).
- Confidence is a logistic of the `top1` score (capped at 1.0) and the margin to the second candidate taken from the uncapped `raw_score` (`W_SCORE`, `W_MARGIN`, `BIAS`, `MAX_MARGIN` in `confidence.py`; one fit for the Yandex embeddings of 28.10.2026, see the comment there); ≥85 % `high`/`auto`, 60–85 % `medium`, <60 % `low`; cancelled codes and non-products are never `auto`. When the second opinion picks another candidate, its confidence uses that selected candidate's raw score and signed margin against the strongest rival, exactly as in candidate cards; it stays needs_review. Manual and remembered human decisions have no invented model percentage. See docs/confidence-completion-2026-10-28.md. `confidence.review_reason` says in words why a row needs review (twins differing by unit or characteristic, weak match); it is kept in `attributes.review`.
- Item statuses: `auto / needs_review / corrected / accepted / failed` (`ITEM_STATUSES` in `runtime.py`). Manual edits: `PATCH /items/{id}` (code), `PUT /items/{id}/attributes` (re-runs search, status `corrected`), `POST /items/{id}/accept`, `POST /items/{id}/undo-code`, `POST /items/{id}/revert`, bulk `POST /jobs/{id}/accept` and `POST /jobs/{id}/revert`, `POST /items/{id}/apply-same` (the code goes to waiting rows of the job with the same text). A manual code or accepting a doubtful row is remembered in the `decisions` table by the row text and applied to the same row of later files (`REMEMBER_DECISIONS`, `attributes.remembered`); revert, undo and deleting the job take it back. `JOBS_KEEP_DAYS` (default 0) deletes old finished jobs. Numbers in ranking only meet numbers of the same quantity (`unit_of`/`same_quantity` in `search.py`).
- **Accounts** (`auth.py`, middleware `require_login` in `api.py`): anyone registers by login and password (`POST /api/auth/register`, logged in at once, five accounts per address then a minute's pause); every `/api/` address except `PUBLIC` (health, login, register, logout, docs) needs the `ktru_session` HttpOnly cookie; the `sessions` table keeps only SHA-256 of the token, passwords are scrypt. An optional first admin comes from `ADMIN_LOGIN`/`ADMIN_PASSWORD` when there are no users; `python users.py` manages accounts. `AUTH_REQUIRED=0` opens the API (development only); `tests/conftest.py` sets it for the pipeline tests, `tests/test_auth.py` turns it on. The frontend shows `LoginScreen` on a 401 (`LOGGED_OUT` event in `api.ts`). `scripts/tour-shots.mjs` needs `TOUR_LOGIN`/`TOUR_PASSWORD`.
- Frontend: React 19 + antd 6 + Vite, no router library (History API in `router.ts`: `/`, `/catalog`, `/jobs`, `/jobs/<id>`, `/jobs/<id>/items/<id>`; `/` and `/catalog` are two tabs of one `UploadScreen`), polling every second in `JobScreen`. `api.ts` holds all request functions and the TS types mirroring backend contracts (`Item.attributes`, `Candidate`). The tour (`Tutorial.tsx`) is an antd `Modal` with pictures from `public/tour/*.webp`; regenerate them with `node scripts/tour-shots.mjs` (needs `npm run dev` and the stack) after visible UI changes. `public/primer.xlsx` is the example file and the downloadable template.

## Conventions that are easy to break

- Code and comments in English; user-facing text in Russian. In the frontend **all** UI strings live in `src/texts.ts` and **all** colors/layout in `src/styles/*.css`, imported in order by `src/theme.css` (CSS variables, one light theme; no colors in components). One UI library only (antd), icons from lucide-react, everything keyboard-accessible.
- Python: pinned `==` versions in `requirements.txt`, ruff line length 100, short functions, files ≲ 300 lines (`search.py` already exceeds it — don't grow them gratuitously).
- Accuracy changes (prompt, ranking weights, thresholds) must be justified with before/after measurements on every labelled set: the real file, 292 and 1000 rows with their modified copies, the EIS form and its copy, `dataset/human_2026_10_28` (770 graded rows); improving one while hurting another is not accepted. Ranking and confidence variants can be replayed offline on saved LLM attributes: `scripts/bench.py` (live run of all sets, rows saved), `scripts/replay.py` (inside the backend container), `scripts/compare_runs.py` (row by row, exit 1 on a regression); the LLM answers vary between runs, so one live run per variant is not proof for a single row.
- Do not weaken security limits: upload size/signature checks, zip-bomb limits, value-vs-source check, export formula escaping (`cell()` prefixes `'` on `= + - @`).
- Never hand-edit `db/ktru_catalog.jsonl.gz` or `frontend/package-lock.json`. Secrets only in `.env` (git-ignored).
- Commit messages in English.

Frontend: `ItemScreen.tsx` assembles `item/` components; `Results.tsx` uses `results/useResultsPage.ts` and result cards. `theme.css` imports ordered styles from `styles/`; all colors remain CSS variables. Search data loading is in `catalog_data.py`, quantity/characteristic comparison in `characteristics.py`; `search.py` keeps ranking. Preserve ranking constants and cache formats.

Case 5 acceptance: `python scripts/acceptance.py --live` (paid external calls), JSON in `outputs/acceptance/report.json`. Read its limitations; 12 handcrafted attribute examples do not prove accuracy on arbitrary files. API health exposes queue and vector-search implementations. Migration/rollback: `docs/case5-migration.md`.
