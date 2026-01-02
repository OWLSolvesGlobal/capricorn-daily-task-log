import os
import uuid
from datetime import datetime
import json
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

# Quantity guardrails (keeps data sane on mobile)
QTY_MIN = 1
QTY_MAX = 200  # adjust if needed


# =========================
# HELPERS
# =========================
def _get_setting(key: str, default=None):
    """
    Reads from Streamlit Secrets first, then environment variables.
    This lets the same code run locally and on Streamlit Cloud.
    """
    if hasattr(st, "secrets") and key in st.secrets:
        return st.secrets[key]
    return os.getenv(key, default)

@st.cache_resource
def get_gspread_client():
    """
    Auth priority:
    1) Streamlit secrets: gcp_service_account_json (recommended, most robust)
    2) Streamlit secrets: gcp_service_account (dict form - optional fallback)
    3) Local dev: GOOGLE_APPLICATION_CREDENTIALS file path
    """

    # --- Option 1: Streamlit Secrets - full JSON blob (recommended) ---
    if hasattr(st, "secrets") and "gcp_service_account_json" in st.secrets:
        raw = str(st.secrets["gcp_service_account_json"]).strip()
        try:
            info = json.loads(raw)
        except Exception as e:
            st.error(
                "Couldnt parse `gcp_service_account_json` as JSON.\n"
                "Re-paste the FULL service account JSON file into Streamlit Secrets."
            )
            st.exception(e)
            st.stop()

        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return gspread.authorize(creds)

    # --- Option 2: Streamlit Secrets - dict form (fallback) ---
    if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
        creds_info = st.secrets["gcp_service_account"]
        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        return gspread.authorize(creds)

    # --- Option 3: Local JSON file via env var ---
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        st.error(
            "Credentials not configured.\n\n"
            "For Streamlit Cloud: add `gcp_service_account_json` in Secrets.\n"
            "For local dev: set GOOGLE_APPLICATION_CREDENTIALS to the path of your JSON.\n\n"
            'PowerShell example:\n$env:GOOGLE_APPLICATION_CREDENTIALS="G:\\My Drive\\CapricornDrapery\\DailyTaskLog\\YOUR_KEY.json"'
        )
        st.stop()

    if not os.path.exists(creds_path):
        st.error(f"Credentials file not found at: {creds_path}")
        st.stop()

    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)


def open_sheet(gc):
    sheet_id = _get_setting("sheet_id")
    if not sheet_id:
        # Fallback to hard-coded SHEET_ID for local testing if you want.
        # Recommended: set sheet_id in secrets.
        sheet_id = "1t3eqKccUSKawZHfYz9nrbGdADK1pntbSsDEf0erxPZ0"

    # Guard against pasting full URL
    if "http" in sheet_id or "/d/" in sheet_id:
        st.error("sheet_id looks like a URL. Paste ONLY the ID between /d/ and /edit.")
        st.stop()

    try:
        return gc.open_by_key(sheet_id)
    except Exception as e:
        st.error("Could not open the Google Sheet. Check sheet_id and sharing permissions.")
        st.write(f"sheet_id: `{sheet_id}`")
        st.exception(e)
        st.stop()


def load_task_options(sh, tab_config: str):
    try:
        ws = sh.worksheet(tab_config)
    except Exception as e:
        st.error(f"Could not find worksheet/tab named '{tab_config}'.")
        st.write("Fix the tab name or rename your sheet tab to match.")
        st.exception(e)
        st.stop()

    col = ws.col_values(1)
    tasks = [x.strip() for x in col if x.strip()]
    if not tasks:
        st.error(f"No tasks found in {tab_config}! Add task names in column A.")
        st.stop()

    if "Other" not in tasks:
        tasks.append("Other")

    return tasks


def append_rows_batch(sh, tab_log: str, rows):
    """
    Efficient append using Google Sheets API via gspread's values_append.
    This is more reliable under concurrent submissions than looping append_row.
    """
    try:
        ws = sh.worksheet(tab_log)
    except Exception as e:
        st.error(f"Could not find worksheet/tab named '{tab_log}'.")
        st.exception(e)
        st.stop()

    # Append to the sheet
    # Using A1 range starting at A (Google appends after last row when insertDataOption is set)
    body = {"values": rows}
    ws.spreadsheet.values_append(
        ws.title,
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
        body=body,
    )


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


# =========================
# APP
# =========================
st.set_page_config(page_title="Daily Task Log", layout="centered")

# --- Simple access gate ---
# Set in Streamlit Secrets as: app_passcode = "..."
# Or locally as env var: APP_PASSCODE
app_passcode = _get_setting("app_passcode") or _get_setting("APP_PASSCODE")
if not app_passcode:
    st.warning(
        "Security note: No app passcode configured.\n\n"
        "Set `app_passcode` in Streamlit Secrets (recommended), or `APP_PASSCODE` as an environment variable."
    )

with st.container():
    st.title("Daily Task Log")
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    now_local = now_utc.astimezone(BARBADOS_TZ)
    st.caption(f"Date: {now_local.strftime('%Y-%m-%d')}   Time: {now_local.strftime('%I:%M %p')}")

    if app_passcode:
        code = st.text_input("Access code", type="password", placeholder="Enter access code")
        if code != app_passcode:
            st.stop()

# --- Sheet + tasks ---
tab_config = _get_setting("tab_config", DEFAULT_TAB_CONFIG)
tab_log = _get_setting("tab_log", DEFAULT_TAB_LOG)

gc = get_gspread_client()
sh = open_sheet(gc)
task_options = load_task_options(sh, tab_config)

# --- Initialize session state for tasks ---
if "tasks" not in st.session_state:
    st.session_state.tasks = [
        {"task_category": task_options[0], "quantity": 1, "client_notes": "", "task_other_text": ""}
    ]

employee_name = st.text_input("Your name", placeholder="Type your full name").strip()

st.subheader("Tasks completed today")


def add_task_row():
    st.session_state.tasks.append(
        {"task_category": task_options[0], "quantity": 1, "client_notes": "", "task_other_text": ""}
    )


# Optional: allow removing a task row to reduce entry mistakes
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


# --- Submit ---
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
        rows.append(
            [
                timestamp_utc,                 # timestamp_utc
                date_local,                    # date_local
                employee_name.strip(),         # employee_name
                t["task_category"],            # task_category
                int(t["quantity"]),            # quantity
                t["client_notes"].strip(),     # client_notes
                t["task_other_text"].strip(),  # task_other_text
                submission_id,                 # submission_id
            ]
        )

    try:
        append_rows_batch(sh, tab_log, rows)
        st.success("Submitted ✅ Thank you.")

        # Reset form after success
        st.session_state.tasks = [
            {"task_category": task_options[0], "quantity": 1, "client_notes": "", "task_other_text": ""}
        ]
        st.rerun()

    except Exception as ex:
        st.error("Submission failed. Please try again.")
        st.exception(ex)
