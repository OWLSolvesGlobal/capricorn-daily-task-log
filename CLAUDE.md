# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this app does

A Streamlit web app for Capricorn Drapery staff to log their daily tasks. Employees fill in their name, task categories, item types, quantities, and client/project notes. On submit, rows are appended to a Google Sheet (`DailyLog` tab). Task categories are loaded dynamically from the `Config` tab (column A) of the same sheet.

## Running the app

```bash
pip install -r requirements.txt
streamlit run app.py
```

Runs on port 8501 by default. The devcontainer starts the app automatically via `postAttachCommand`.

## Local dev environment

A test Google Sheet exists for local development (separate from production). Local secrets are stored in `.streamlit/secrets.toml` (gitignored — never committed).

To run locally with hot reload and the dev banner:

```bash
STREAMLIT_ENV=dev streamlit run app.py --server.runOnSave true
```

To change the test passcode, edit `app_passcode` in `.streamlit/secrets.toml`.

**Workflow:** edit `app.py` → save → browser reloads instantly → test against the test sheet → push to GitHub → Streamlit Cloud auto-deploys to staff.

## Credentials / secrets

The app requires Google Sheets access. Two methods:

1. **Streamlit Cloud**: Add `[gcp_service_account]` TOML block in Secrets, plus `sheet_id`, optionally `app_passcode`, `tab_config`, `tab_log`.
2. **Local dev**: Set `GOOGLE_APPLICATION_CREDENTIALS` env var to the path of a service account JSON file (e.g., `capricorndrapery-62db40aff639.json`). The JSON file is gitignored.

The `sheet_id` is the bare ID from the Google Sheets URL (between `/d/` and `/edit`), not the full URL.

## Google Sheet structure

| Tab | Purpose |
|-----|---------|
| `Config` | Column A lists available task categories (loaded at startup, cached 1 hour) |
| `DailyLog` | Append-only log; columns: `timestamp_utc`, `date_local`, `employee`, `task_category`, `item_type`, `item_other_text`, `quantity`, `client_notes`, `task_other_text`, `submission_id` |

## Key design decisions

- **`@st.cache_resource`** caches the gspread client and open spreadsheet object across reruns (avoids re-authenticating).
- **`@st.cache_data(ttl=3600)`** caches the Config tab task list for 1 hour.
- **Rate limit handling**: All Google API calls retry with exponential backoff on 429 / quota errors (backoff sequence: 0, 1, 2, 4, 8, 16 seconds).
- **Form reset**: Uses `st.session_state["reset_requested"]` flag pattern — reset is applied at the top of the next rerun, before widgets render, to avoid Streamlit key conflicts.
- **`ITEM_OPTIONS`**: Hardcoded list in `app.py` (unlike task categories which come from the sheet). Edit `ITEM_OPTIONS` directly to add/remove item types.
- **Passcode gate**: If `app_passcode` is set in secrets/env, the entire app is blocked until the correct code is entered.

## write_test.py

A standalone script to verify Google Sheets write access. Run with `GOOGLE_APPLICATION_CREDENTIALS` set. Uses `gc.open(SHEET_ID)` (by name, not key) — note this differs from `app.py` which uses `open_by_key`.
