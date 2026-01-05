import os
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pytz

SHEET_ID = "1t3eqKccUSKawZHfYz9nrbGdADK1pntbSsDEf0erxPZ0"  # change to your exact Google Sheet name
TAB_NAME = "DailyLog"

def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    return gspread.authorize(creds)

def main():
    gc = get_client()
    sh = gc.open(SHEET_ID)
    ws = sh.worksheet(TAB_NAME)

    utc_now = datetime.utcnow().replace(tzinfo=pytz.utc)
    barbados = pytz.timezone("America/Barbados")
    date_local = utc_now.astimezone(barbados).date().isoformat()

    # Append a single test row
    ws.append_row([
        utc_now.isoformat(),
        date_local,
        "TEST USER 1",
        "Pressing",
        1,
        "TEST CLIENT 1",
        "Camelot",
        "TEST-SUBMISSION-ID"
    ], value_input_option="USER_ENTERED")

    print("✅ Wrote test row successfully.")

if __name__ == "__main__":
    main()
