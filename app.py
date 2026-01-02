import os
import uuid
import time
from datetime import datetime

import gspread
import pytz
import streamlit as st
from google.oauth2.service_account import Credentials


# =========================
# CONFIG
# =========================
BARBADOS_TZ = pytz.timezone("America/Barbados")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

DEFAULT_TAB_CONFIG = "Config"
DEFAULT_TAB_LOG = "DailyLog"

QTY_MIN = 1
QTY_MAX = 200  # adjust if needed


# =========================
# SETTINGS + AUTH
# =========================
def _get_setting(key: str, default=None):
    """Reads from Streamlit Secrets first, then environment variables."""
    if hasattr(st, "secrets") and key in st.secrets:
        return st.secrets[key]
    return os.getenv(key, default)


@st.cache_resource
def get_gspread_client():
    """
    Auth priority:
    1) Streamlit secrets: gcp_service_account (TOML dict)
    2) Local dev: GOOGLE_APPLICATION_CREDENTIALS file path
    """
    # Streamlit Cloud secrets
    if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
        creds_info = dict(st.secrets["gcp_service_account"])

        # Normalize private_key line breaks if pasted with literal \n
        pk = creds_info.get("private_key", "")
        if "\\n" in pk and "\n" not in pk:
            creds_info["private_key"] = pk.replace("\\n", "\n")

        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        return gspread.authorize(creds)

    # Local fallback
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        st.error(
            "Credentials not configured.\n\n"
            "For Streamlit Cloud: add [gcp_service_account] in Secrets.\n"
            "For local dev: set GOOGLE_APPLICATION_CREDENTIALS to the path of your JSON."
        )
        st.stop()

    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)


def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e)
    return ("429" in msg) or ("Quota exceeded" in msg) or ("rate limit" in msg.lower())


@st.cache_resource
def open_sheet_cached(sheet_id: str):
    """
    Opens the Google Sheet once and caches the Spreadsheet object across reruns.
    Includes retry/backoff for 429s.
    """
    if not sheet_id:
        raise RuntimeError("Missing sheet_id. Set `sheet_id` in Streamlit Secrets.")

    # Guard against pasting a URL instead of an ID
    if "http" in sheet_id or "/d/" in sheet_id:
        raise RuntimeError("sheet_id looks like a URL. Paste ONLY the ID between /d/ and /edit.")

    gc = get_gspread_client()

    backoffs = [0, 1, 2, 4, 8, 16]
    last_err = None
    for wait in backoffs:
        if wait:
            time.sleep(wait)
        try:
            return gc.open_by_key(sheet_id)
        except Exception as e:
            last_err = e
            if _is_rate_limit_error(e):
                continue
            raise  # non-429: fail fast

    raise last_err


# =========================
# DATA LOAD (CACHED)
# =========================
@st.cache_data(ttl=3600)
def load_task_options_cached(sheet_id: str, tab_config: str):
    """
    Loads task options from Config tab column A and caches them.
    This removes repeated 'read' calls on every Streamlit rerun.
    """
    sh = open_sheet_cached(sheet_id)
    ws = sh.worksheet(tab_config)

    col = ws.col_values(1)
    tasks = [x.strip() for x in col if x.strip()]
    if not tasks:
        raise RuntimeError(f"No tasks found in '{tab_config}' column A.")

    if "Other" not in tasks:
        tasks.append("Other")

    return tasks


# =========================
# WRITE (ONLY ON SUBMIT)
# =========================
def append_rows_batch(sheet_id: str, tab_log: str, rows):
    """
    Appends rows to DailyLog using values_append.
    Retries on 429s.
    """
    sh = open_sheet_cached(sheet_id)

    backoffs = [0, 1, 2, 4, 8, 16]
    last_err = None
    for wait in backoffs:
        if wait:
            time.sleep(wait)
        try:
            ws = sh.worksheet(tab_log)

            body = {"values": rows}
            ws.spreadsheet.values_append(
                ws.title,
                params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                body=body,
            )
            return
        except Exception as e:
            last_err = e
            if _is_rate_limit_error(e):
                continue
            raise

    raise last_err


# =========================
# VALIDATION + RESET
# =========================
def validate(employee, tasks):
    errors = []
    employee = (employee or "").strip()

    if not employee:
        errors.append("Name is required.")

    if not tasks:
        errors.append("At least one task is required.")
        return errors

    for idx, t in enumerate(tasks, start=1):
        task_cat = (t.get("task_category") or "").strip()
        client_notes = (t.get("client_notes") or "").strip()
        other_text = (t.get("task_other_text") or "").strip()

        try:
            qty = int(t.get("quantity"))
        except Exception:
            qty = 0

        if not task_cat:
            errors.append(f"Task {idx}: task category is required.")
        if qty < QTY_MIN:
            errors.append(f"Task {idx}: quantity must be at least {QTY_MIN}.")
        if qty > QTY_MAX:
            errors.append(f"Task {idx}: quantity must be {QTY_MAX} or less.")
        if len(client_notes) < 3:
            errors.append(f"Task {idx}: Client / Project is required (min 3 characters).")
        if task_cat == "Other" and len(other_text) < 3:
            errors.append(f"Task {idx}: description is required for 'Other' (min 3 characters).")

    return errors


def reset_form(task_options):
    """
    IMPORTANT:
    Do NOT assign to st.session_state['employee_name'] after the widget exists.
    Instead, remove keys with pop() BEFORE widgets instantiate (handled via reset flag).
    """
    # Clear name widget state
    st.session_state.pop("employee_name", None)

    # Clear dynamic widget keys
    prefixes = ("task_cat_", "qty_", "client_", "other_", "remove_")
    for k in list(st.session_state.keys()):
        if isinstance(k, str) and k.startswith(prefixes):
            st.session_state.pop(k, None)

    # Reset tasks to a single row
    st.session_state["tasks"] = [{
        "task_category": task_options[0],
        "quantity": 1,
        "client_notes": "",
        "task_other_text": ""
    }]


# =========================
# APP UI
# =========================
st.set_page_config(page_title="Daily Task Log", layout="centered")

# Passcode gate
app_passcode = _get_setting("app_passcode") or _get_setting("APP_PASSCODE")
if not app_passcode:
    st.warning(
        "Security note: No app passcode configured.\n\n"
        "Set `app_passcode` in Streamlit Secrets (recommended)."
    )

st.title("Daily Task Log")
now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
now_local = now_utc.astimezone(BARBADOS_TZ)
st.caption(f"Date: {now_local.strftime('%Y-%m-%d')}   Time: {now_local.strftime('%I:%M %p')}")

if app_passcode:
    code = st.text_input("Access code", type="password", placeholder="Enter access code")
    if code != app_passcode:
        st.stop()

# Settings from secrets/env
sheet_id = _get_setting("sheet_id")
tab_config = _get_setting("tab_config", DEFAULT_TAB_CONFIG)
tab_log = _get_setting("tab_log", DEFAULT_TAB_LOG)

# Load task options (cached; minimal reads)
try:
    task_options = load_task_options_cached(sheet_id, tab_config)
except Exception as e:
    if _is_rate_limit_error(e):
        st.error("Google Sheets is temporarily rate-limiting reads. Please wait ~60 seconds and refresh.")
        st.stop()
    st.error("Could not load task list from the Config tab.")
    st.exception(e)
    st.stop()

# --- Apply post-submit reset BEFORE widgets are instantiated ---
if st.session_state.get("reset_requested", False):
    reset_form(task_options)
    st.session_state["reset_requested"] = False

toast_msg = st.session_state.pop("toast_msg", None)
if toast_msg:
    st.toast(toast_msg, icon="✅")

# Initialize session state
if "tasks" not in st.session_state:
    reset_form(task_options)

# Name field (keyed so we can clear it)
st.text_input("Your name", key="employee_name", placeholder="Type your full name")
employee_name = (st.session_state.get("employee_name") or "").strip()

st.subheader("Tasks completed today")


def add_task_row():
    st.session_state.tasks.append(
        {"task_category": task_options[0], "quantity": 1, "client_notes": "", "task_other_text": ""}
    )


def remove_task_row(index: int):
    if len(st.session_state.tasks) <= 1:
        return
    st.session_state.tasks.pop(index)


for i, t in enumerate(st.session_state.tasks):
    st.markdown(f"**Task {i+1}**")
    c1, c2, c3 = st.columns([2, 1, 1])

    with c1:
        t["task_category"] = st.selectbox(
            "Task category",
            task_options,
            index=task_options.index(t["task_category"]) if t["task_category"] in task_options else 0,
            key=f"task_cat_{i}",
        )

    with c2:
        t["quantity"] = st.number_input(
            "Quantity (# items)",
            min_value=QTY_MIN,
            max_value=QTY_MAX,
            step=1,
            value=int(t["quantity"]) if str(t["quantity"]).isdigit() else 1,
            key=f"qty_{i}",
        )

    with c3:
        if st.button("Remove", key=f"remove_{i}"):
            remove_task_row(i)
            st.rerun()

    t["client_notes"] = st.text_input(
        "Client / Project (required)",
        value=t.get("client_notes", ""),
        placeholder="e.g. Sandy Lane Villa, Apt 3",
        key=f"client_{i}",
    )

    if t["task_category"] == "Other":
        t["task_other_text"] = st.text_input(
            "Describe task (required for Other)",
            value=t.get("task_other_text", ""),
            placeholder="Describe what you did",
            key=f"other_{i}",
        )
    else:
        t["task_other_text"] = ""

    st.divider()

st.button("➕ Add another task", on_click=add_task_row)


# =========================
# SUBMIT
# =========================
if st.button("✅ Submit"):
    errs = validate(employee_name, st.session_state.tasks)
    if errs:
        st.error("Please fix the following:")
        for e in errs:
            st.write(f"- {e}")
        st.stop()

    submission_id = str(uuid.uuid4())[:8]
    timestamp_utc = datetime.utcnow().replace(tzinfo=pytz.utc).isoformat()
    date_local = datetime.utcnow().replace(tzinfo=pytz.utc).astimezone(BARBADOS_TZ).date().isoformat()

    rows = []
    for t in st.session_state.tasks:
        rows.append([
            timestamp_utc,
            date_local,
            employee_name.strip(),
            t["task_category"],
            int(t["quantity"]),
            t["client_notes"].strip(),
            t["task_other_text"].strip(),
            submission_id
        ])

    try:
        append_rows_batch(sheet_id, tab_log, rows)

        # Request reset on next run (before widgets instantiate)
        st.session_state["toast_msg"] = "Thank you — your submission has been saved ✅"
        st.session_state["reset_requested"] = True
        st.rerun()

    except Exception as e:
        if _is_rate_limit_error(e):
            st.error("Google Sheets is temporarily rate-limiting writes. Please wait ~60 seconds and submit again.")
            st.stop()
        st.error("Submission failed. Please try again.")
        st.exception(e)
