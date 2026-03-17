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
# ITEM DROPDOWN OPTIONS
# =========================
TASK_PLACEHOLDER = "— Select task —"
ITEM_PLACEHOLDER = "— Select item —"

ITEM_OPTIONS = [
    ITEM_PLACEHOLDER,
    # Soft Furnishings
    "Cushion (Throw Pillow)",
    "Cushion (Seat)",
    "Cushion (Back)",
    "Bolster Cushion",
    "Outdoor Cushion",
    "Lounger Pad",
    "Bench Cushion",
    "Headrests",
    "Window Seat Cushion",
    "Bed Runner",
    "Bed Skirt / Valance",
    "Throw / Coverlet",

    # Drapery (styles)
    "Drapes - Triple Pleat",
    "Drapes - Pinch Pleat",
    "Drapes - Pencil Pleat",
    "Drapes - Eyelet / Grommet",
    "Drapes - Ripplefold / Wave",
    "Drapes - Goblet Pleat",
    "Drapes - Tab Top",
    "Drapes - Rod Pocket",
    "Sheer Panels",

    # Blinds & Shades
    "Roman Blind",
    "Roller Blind",
    "Solar / Screen Blind",
    "Venetian Blind",
    "Vertical Blind",

    # Decorative top treatments
    "Pelmet",
    "Valance",
    "Cornice",

    # Upholstery / Furniture items
    "Dining Chair",
    "Armchair",
    "Sofa",
    "Ottoman",
    "Headboard",
    "Banquette / Booth Seating",
    "Slipcovers",

    # Hotel / Specialty
    "Blackout Lining",
    "Interlining",
    "Tiebacks / Holdbacks",
    "Curtain Track / Rod Work",

    # Catch-all
    "Other",
]


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
    if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
        creds_info = dict(st.secrets["gcp_service_account"])

        # Normalize private_key line breaks if pasted with literal \n
        pk = creds_info.get("private_key", "")
        if "\\n" in pk and "\n" not in pk:
            creds_info["private_key"] = pk.replace("\\n", "\n")

        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        return gspread.authorize(creds)

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
            raise

    raise last_err


# =========================
# DATA LOAD (CACHED)
# =========================
@st.cache_data(ttl=3600)
def load_task_options_cached(sheet_id: str, tab_config: str):
    """
    Loads task options from Config tab column A and caches them.
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
        item_type = (t.get("item_type") or "").strip()
        item_other_text = (t.get("item_other_text") or "").strip()
        client_notes = (t.get("client_notes") or "").strip()
        other_task_text = (t.get("task_other_text") or "").strip()

        try:
            qty = int(t.get("quantity"))
        except Exception:
            qty = 0

        if not task_cat or task_cat == TASK_PLACEHOLDER:
            errors.append(f"Task {idx}: task category is required.")

        # Item validation
        if not item_type or item_type == ITEM_PLACEHOLDER:
            errors.append(f"Task {idx}: item worked on is required.")
        if item_type == "Other" and len(item_other_text) < 3:
            errors.append(f"Task {idx}: item name is required for 'Other' (min 3 characters).")

        # Quantity + client
        if qty < QTY_MIN:
            errors.append(f"Task {idx}: quantity must be at least {QTY_MIN}.")
        if qty > QTY_MAX:
            errors.append(f"Task {idx}: quantity must be {QTY_MAX} or less.")
        if len(client_notes) < 3:
            errors.append(f"Task {idx}: Client / Project is required (min 3 characters).")

        # Task "Other" validation (existing behaviour)
        if task_cat == "Other" and len(other_task_text) < 3:
            errors.append(f"Task {idx}: description is required for task 'Other' (min 3 characters).")

    return errors


def reset_form(task_options):
    """
    Clears all user inputs and resets the form to a single blank task row.
    Uses pop() so Streamlit widget keys don't throw exceptions.
    """
    st.session_state.pop("employee_name", None)

    prefixes = (
        "task_cat_", "qty_", "client_", "other_",
        "item_", "item_other_",
        "remove_"
    )
    for k in list(st.session_state.keys()):
        if isinstance(k, str) and k.startswith(prefixes):
            st.session_state.pop(k, None)

    st.session_state["tasks"] = [{
        "task_category": TASK_PLACEHOLDER,
        "item_type": ITEM_PLACEHOLDER,
        "item_other_text": "",
        "quantity": 1,
        "client_notes": "",
        "task_other_text": ""
    }]


# =========================
# APP UI
# =========================
st.set_page_config(page_title="Capricorn Drapery Daily Task Log", layout="centered")

st.markdown("""
<style>
    /* ── Base font size ── */
    html, body, [class*="css"] {
        font-size: 16px !important;
    }

    /* ── Page title ── */
    h1 { font-size: 1.6rem !important; font-weight: 700 !important; }

    /* ── Section headings ── */
    h2, h3 { font-size: 1.1rem !important; font-weight: 600 !important; }

    /* ── Form labels ── */
    label, .stSelectbox label, .stTextInput label,
    .stNumberInput label, .stTextArea label {
        font-size: 0.95rem !important;
        font-weight: 600 !important;
        color: #1A1A1A !important;
    }

    /* ── Font smoothing — eliminates pixelation ── */
    *, *::before, *::after {
        -webkit-font-smoothing: antialiased !important;
        -moz-osx-font-smoothing: grayscale !important;
        text-rendering: optimizeLegibility !important;
    }

    /* ── Dropdown text ── */
    .stSelectbox div[data-baseweb="select"] span,
    .stSelectbox div[data-baseweb="select"] div {
        font-size: 0.95rem !important;
        color: #1A1A1A !important;
        line-height: 1.5 !important;
        white-space: normal !important;
        overflow: visible !important;
    }

    /* ── Dropdown container — let it breathe, no clipping ── */
    .stSelectbox div[data-baseweb="select"] > div {
        padding-top: 0.35rem !important;
        padding-bottom: 0.35rem !important;
        height: auto !important;
        min-height: unset !important;
    }

    /* ── Text input fields ── */
    input[type="text"], input[type="number"] {
        font-size: 0.95rem !important;
        color: #1A1A1A !important;
        padding: 0.4rem 0.65rem !important;
    }

    /* ── Primary button (Save) ── */
    .stButton > button[kind="primary"],
    .stButton > button {
        font-size: 1rem !important;
        font-weight: 600 !important;
        padding: 0.5rem 1.25rem !important;
        border-radius: 6px !important;
    }

    /* ── Remove / confirm buttons — subtle red text style ── */
    [data-testid="stButton"] button[kind="secondary"]:has(~ *),
    div[data-testid="stButton"] > button {
        font-size: 0.8rem !important;
    }
    div[data-testid="stButton"]:has(button[title="Remove task 1"]) > button,
    div[data-testid="stButton"]:has(button[title="Remove task 2"]) > button,
    div[data-testid="stButton"]:has(button[title="Remove task 3"]) > button {
        background: transparent !important;
        border: none !important;
        color: #c0392b !important;
        font-size: 0.8rem !important;
        padding: 0 !important;
        text-decoration: underline !important;
        box-shadow: none !important;
    }

    /* ── Success / error / warning banners ── */
    .stAlert { font-size: 1rem !important; padding: 0.75rem !important; }

    /* ── Caption text (date/time) ── */
    .stCaption { font-size: 0.9rem !important; color: #444 !important; }

    /* ── Divider spacing ── */
    hr { margin: 0.75rem 0 !important; }

    /* ── Task block label ── */
    strong { font-size: 1rem !important; }
</style>
""", unsafe_allow_html=True)

if os.getenv("STREAMLIT_ENV") == "dev":
    st.warning("⚠️ DEV MODE — submissions go to the TEST sheet, not production.")

# Logo + title header
col_logo, col_title = st.columns([1, 2.5])
with col_logo:
    st.image("assets/logo.png", width=160)
with col_title:
    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    st.title("Daily Task Log")
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    now_local = now_utc.astimezone(BARBADOS_TZ)
    st.caption(f"Date: {now_local.strftime('%Y-%m-%d')}   \u2022   Time: {now_local.strftime('%I:%M %p')}")

sheet_id = _get_setting("sheet_id")
tab_config = _get_setting("tab_config", DEFAULT_TAB_CONFIG)
tab_log = _get_setting("tab_log", DEFAULT_TAB_LOG)

try:
    task_options = load_task_options_cached(sheet_id, tab_config)
except Exception as e:
    if _is_rate_limit_error(e):
        st.error("Google Sheets is temporarily rate-limiting reads. Please wait ~60 seconds and refresh.")
        st.stop()
    st.error("Could not load task list from the Config tab.")
    st.exception(e)
    st.stop()


# Show full-page success screen after a submission
if "submission_success" in st.session_state:
    info = st.session_state["submission_success"]
    st.success(f"✅ Thank you, {info['name']}. Your tasks have been saved for {info['date']}.")
    st.markdown("###")
    if st.button("📋 Log tasks for another day"):
        del st.session_state["submission_success"]
        st.rerun()
    st.stop()

# Initialize state
if "tasks" not in st.session_state:
    reset_form(task_options)

st.markdown("**Enter your name, then fill in each task you completed today. Press Save My Tasks for Today when done.**")

# Name field
st.text_input("Your full name (required)", key="employee_name", placeholder="Type your full name")
employee_name = (st.session_state.get("employee_name") or "").strip()

st.subheader("Tasks completed today")


def add_task_row():
    st.session_state.tasks.append({
        "task_category": TASK_PLACEHOLDER,
        "item_type": ITEM_PLACEHOLDER,
        "item_other_text": "",
        "quantity": 1,
        "client_notes": "",
        "task_other_text": ""
    })


def remove_task_row(index: int):
    if len(st.session_state.tasks) <= 1:
        return
    st.session_state.tasks.pop(index)


for i, t in enumerate(st.session_state.tasks):
    st.markdown(f"**Task {i+1}**")

    # Row 1: task category + item worked on + qty
    c1, c2, c3 = st.columns([2, 2, 1])

    with c1:
        _task_opts = [TASK_PLACEHOLDER] + task_options
        t["task_category"] = st.selectbox(
            "Task category",
            _task_opts,
            index=_task_opts.index(t["task_category"]) if t["task_category"] in _task_opts else 0,
            key=f"task_cat_{i}",
        )

    with c2:
        t["item_type"] = st.selectbox(
            "Item worked on",
            ITEM_OPTIONS,
            index=ITEM_OPTIONS.index(t["item_type"]) if t["item_type"] in ITEM_OPTIONS else 0,
            key=f"item_{i}",
        )

    with c3:
        t["quantity"] = st.number_input(
            "Qty (# items)",
            min_value=QTY_MIN,
            max_value=QTY_MAX,
            step=1,
            value=int(t["quantity"]) if str(t["quantity"]).isdigit() else 1,
            key=f"qty_{i}",
        )

    # If item is Other, force item name
    if t["item_type"] == "Other":
        t["item_other_text"] = st.text_input(
            "Other item name (required)",
            value=t.get("item_other_text", ""),
            placeholder="Type the item name (e.g. Banquette cushion set)",
            key=f"item_other_{i}",
        )
    else:
        t["item_other_text"] = ""

    # Client/project notes
    t["client_notes"] = st.text_input(
        "Client / Project (required)",
        value=t.get("client_notes", ""),
        placeholder="e.g. Sandy Lane Villa, Apt 3",
        key=f"client_{i}",
    )

    # If task category is Other, require task description
    if t["task_category"] == "Other":
        t["task_other_text"] = st.text_input(
            "Describe task (required for task 'Other')",
            value=t.get("task_other_text", ""),
            placeholder="Describe what you did",
            key=f"other_{i}",
        )
    else:
        t["task_other_text"] = ""

    # Two-tap remove — only shown when multiple tasks exist
    if len(st.session_state.tasks) > 1:
        _, col_remove = st.columns([4, 1])
        with col_remove:
            if st.session_state.get("confirm_remove") == i:
                st.markdown(
                    "<p style='color:#c0392b; font-size:0.8rem; margin-bottom:2px'>Are you sure?</p>",
                    unsafe_allow_html=True,
                )
                if st.button("Yes, remove", key=f"confirm_{i}"):
                    remove_task_row(i)
                    del st.session_state["confirm_remove"]
                    st.rerun()
            else:
                st.markdown(
                    f"<p style='color:#c0392b; font-size:0.8rem; cursor:pointer; margin-top:8px'>"
                    f"🗑 Remove</p>",
                    unsafe_allow_html=True,
                )
                if st.button("🗑 Remove", key=f"remove_{i}", help=f"Remove task {i+1}"):
                    st.session_state["confirm_remove"] = i
                    st.rerun()

    st.divider()

st.button("➕ Add another task", on_click=add_task_row)


# =========================
# SUBMIT
# =========================
if st.button("✅ Save My Tasks for Today"):
    errs = validate(employee_name, st.session_state.tasks)
    if errs:
        st.error("Please fix the following:")
        for e in errs:
            st.write(f"- {e}")
        st.stop()

    submission_id = str(uuid.uuid4())[:8]
    timestamp_utc = datetime.utcnow().replace(tzinfo=pytz.utc).isoformat()
    date_local = datetime.utcnow().replace(tzinfo=pytz.utc).astimezone(BARBADOS_TZ).date().isoformat()

    # NOTE: Adds two new columns to the sheet output:
    # item_type, item_other_text
    rows = []
    for t in st.session_state.tasks:
        rows.append([
            timestamp_utc,
            date_local,
            employee_name.strip(),
            t["task_category"],
            t["item_type"],
            t["item_other_text"].strip(),
            int(t["quantity"]),
            t["client_notes"].strip(),
            t["task_other_text"].strip(),
            submission_id
        ])

    try:
        append_rows_batch(sheet_id, tab_log, rows)

        st.session_state["submission_success"] = {
            "name": employee_name.strip(),
            "date": date_local,
        }
        reset_form(task_options)
        st.rerun()

    except Exception as e:
        if _is_rate_limit_error(e):
            st.error("Google Sheets is temporarily rate-limiting writes. Please wait ~60 seconds and submit again.")
            st.stop()
        st.error("Submission failed. Please try again.")
        st.exception(e)
