#!/usr/bin/env python3
"""
Vocabulary AI Analysis

Reads vocabulary from Google Sheet (Sheet1), generates AI analysis
using Gemini API, and writes results to Sheet2.
"""

import csv
import json
import os
import ssl
import sys
import time
from pathlib import Path

import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

CSV_FILE = Path(__file__).parent / "spanishdict_vocab.csv"

SPREADSHEET_ID = "14oqOzF2MXMDvhp8XbZo1fa3yFHTW3anVgfFrHvK4tNc"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
RATE_LIMIT_DELAY = 0.5  # seconds between Gemini calls (paid tier has ~1000 RPM)
MAX_RETRIES = 5  # retry on rate limit errors


def get_sheets_service():
    """Authenticate and return Google Sheets API service."""
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if not creds_json:
        print("Error: GOOGLE_SHEETS_CREDENTIALS environment variable not set")
        sys.exit(1)

    creds_data = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_data, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def execute_with_retry(request, max_retries=5):
    """Execute a Google API request with retry on transient network/SSL errors."""
    for attempt in range(max_retries):
        try:
            return request.execute()
        except (ssl.SSLEOFError, ssl.SSLError, OSError, ConnectionError) as e:
            if attempt < max_retries - 1:
                wait = 15 * (2 ** attempt)  # 15, 30, 60, 120, 240s
                print(f"  Network/SSL error (attempt {attempt + 1}/{max_retries}), retrying in {wait}s: {e}")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            if attempt < max_retries - 1 and any(
                code in str(e) for code in ["500", "502", "503", "504"]
            ):
                wait = 15 * (2 ** attempt)
                print(f"  API server error, retrying in {wait}s: {e}")
                time.sleep(wait)
            else:
                raise


def ensure_sheet_capacity(service, sheet_names, min_rows=2000):
    """Expand any sheets that don't have enough rows."""
    spreadsheet = execute_with_retry(
        service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID)
    )
    requests = []
    for sheet in spreadsheet["sheets"]:
        props = sheet["properties"]
        if props.get("title") in sheet_names:
            current = props["gridProperties"]["rowCount"]
            if current < min_rows:
                requests.append({
                    "appendDimension": {
                        "sheetId": props["sheetId"],
                        "dimension": "ROWS",
                        "length": min_rows - current,
                    }
                })
    if requests:
        execute_with_retry(
            service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"requests": requests},
            )
        )
        print(f"Expanded sheets to {min_rows} rows.")


def read_sheet(service, range_name):
    """Read values from a sheet range."""
    result = execute_with_retry(
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueRenderOption="FORMATTED_VALUE",
        )
    )
    return result.get("values", [])


def call_gemini(prompt, api_key):
    """Call Gemini API with retry and exponential backoff on rate limits."""
    for attempt in range(MAX_RETRIES):
        response = requests.post(
            GEMINI_URL,
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        if response.status_code in (429, 503):
            wait = RATE_LIMIT_DELAY * (2 ** attempt)  # 5, 10, 20, 40, 80s
            label = "Rate limited" if response.status_code == 429 else "Service unavailable"
            print(f"  {label}, waiting {wait}s (attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
            continue
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    raise Exception("Rate limited after all retries")


def needs_generation(value):
    """Check if a cell value is empty or contains an error."""
    if not value or not value.strip():
        return True
    v = value.strip().upper()
    return v in ("#ERROR!", "#VALUE!", "#REF!", "#N/A", "#NAME?", "LOADING...")


def make_analysis_prompt(spanish_word):
    """Build the Gemini prompt for AI Analysis (Sheet2 col B)."""
    return (
        f"For this word, {spanish_word}, note the main definition(s) and usages "
        "(ignore less common defs/usages), any important grammatical context "
        "(if anything particularly unusual or interesting, but say none if nothing "
        "worth noting. Also, do not note irregular spelling changes of conjugations "
        "if they're only there to preserve regular pronunciation) and a sample "
        "sentence, note the root origin, note the closest etymologically related "
        "English words (maybe one to three). If said English word is extremely rare "
        "define it in parentheses. If said English word has serious meaning drift, "
        "or it is not obvious how the words are etymologically related, detail the "
        "meaning and/or phonetic drift in parentheses. If no related English words, "
        "note that. Also note any very common etymologically related words in Spanish "
        "that are absolutely worth knowing. You do not have to include Spanish words "
        "that are not commonly used. If there are Spanish cognates worth including, "
        "then after listing them, define them in parentheses. Also, formatting-wise, "
        "can you separate each section with a vertical space?\n\n"
        "IMPORTANT formatting rules: Write in plain text only. Do NOT use markdown "
        "formatting — no bold (**), no headers, no bullet points, no numbered lists. "
        "Write in flowing paragraphs. Be concise. Use short section labels followed "
        "by a colon, like: \"Grammatical context: none.\" or \"Root origin: ...\" or "
        "\"Related English words: ...\" or \"Related Spanish words: ...\". "
        "If grammatical context is none, just write \"Grammatical context: none.\" "
        "For the sample sentence, write only the Spanish sentence — do NOT include "
        "an English translation of it."
    )


def make_other_translations_prompt(english_word):
    """Build the Gemini prompt for Other Translations (Sheet2 col E)."""
    return (
        f"{english_word} What are the main spanish translations of this word? "
        "List all *very* common ones. Omit infrequent ones. Also give the sense "
        "of the translation in parentheses.\n\n"
        "IMPORTANT formatting rules: Write in plain text only. Do NOT use markdown "
        "formatting — no bold, no headers, no numbered lists. Use a simple list with "
        "dashes. Keep each entry to one line: the Spanish word followed by the sense "
        "in parentheses. Do NOT include example sentences. Be concise."
    )


def sync_csv_to_sheet1(service):
    """Sync new words from the CSV to Sheet1, appending any missing entries."""
    if not CSV_FILE.exists():
        print(f"CSV file not found: {CSV_FILE}")
        return

    # Read CSV
    with open(CSV_FILE, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        csv_header = next(reader)
        csv_rows = list(reader)

    # Read current Sheet1
    sheet1_rows = read_sheet(service, "Sheet1!A:E")
    existing_words = set()
    for row in sheet1_rows[1:]:  # skip header
        if len(row) >= 2 and row[1]:
            existing_words.add(row[1].strip())

    # Find new words in CSV not in Sheet1
    # CSV columns: [Date Added, Spanish, English, Part of Speech, Popularity]
    new_rows = []
    for row in csv_rows:
        if len(row) >= 2 and row[1].strip() not in existing_words:
            new_rows.append(row)

    if not new_rows:
        print("Sheet1 is up to date with CSV")
        return

    print(f"Adding {len(new_rows)} new words to Sheet1...")
    execute_with_retry(
        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": new_rows},
        )
    )
    print(f"Synced {len(new_rows)} new words to Sheet1")


def main():
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        print("Error: GEMINI_API_KEY environment variable not set")
        sys.exit(1)

    print("Authenticating with Google Sheets API...")
    service = get_sheets_service()

    ensure_sheet_capacity(service, ["Sheet1", "Sheet2"])

    print("Syncing CSV to Sheet1...")
    sync_csv_to_sheet1(service)

    print("Reading Sheet1...")
    sheet1_rows = read_sheet(service, "Sheet1!A:E")

    print("Reading Sheet2...")
    sheet2_rows = read_sheet(service, "Sheet2!A:E")

    # Sheet1: [Date Added, Spanish, English, POS, Popularity]
    # Sheet2: [?, AI Analysis, Reviewed, ?, Other Translations]

    if len(sheet1_rows) < 2:
        print("No data in Sheet1")
        return

    # Find rows that need generation (skip header at index 0)
    words_to_process = []
    for i in range(1, len(sheet1_rows)):
        s1_row = sheet1_rows[i]
        s2_row = sheet2_rows[i] if i < len(sheet2_rows) else []

        # Pad rows to expected width
        while len(s1_row) < 5:
            s1_row.append("")
        while len(s2_row) < 5:
            s2_row.append("")

        spanish = s1_row[1]  # Sheet1 col B
        english = s1_row[2]  # Sheet1 col C

        if not spanish:
            continue

        need_analysis = needs_generation(s2_row[1])  # Sheet2 col B
        need_other = needs_generation(s2_row[4])  # Sheet2 col E

        if need_analysis or need_other:
            words_to_process.append(
                {
                    "row": i + 1,  # 1-indexed for Sheets API
                    "spanish": spanish,
                    "english": english,
                    "need_analysis": need_analysis,
                    "need_other": need_other,
                }
            )

    print(f"Found {len(words_to_process)} words needing analysis")

    if not words_to_process:
        print("Nothing to do!")
        return

    # Process each word, writing in batches to preserve progress
    BATCH_SIZE = 10
    pending_updates = []
    total_written = 0

    for idx, word in enumerate(words_to_process):
        row = word["row"]
        spanish = word["spanish"]
        english = word["english"]

        print(f"[{idx + 1}/{len(words_to_process)}] Processing: {spanish} ({english})")

        if word["need_analysis"]:
            try:
                result = call_gemini(make_analysis_prompt(spanish), gemini_key)
                pending_updates.append({"range": f"Sheet2!B{row}", "values": [[result]]})
                print(f"  Analysis generated ({len(result)} chars)")
            except Exception as e:
                print(f"  Analysis failed: {e}")
            time.sleep(RATE_LIMIT_DELAY)

        if word["need_other"]:
            try:
                result = call_gemini(
                    make_other_translations_prompt(english), gemini_key
                )
                pending_updates.append({"range": f"Sheet2!E{row}", "values": [[result]]})
                print(f"  Other translations generated ({len(result)} chars)")
            except Exception as e:
                print(f"  Other translations failed: {e}")
            time.sleep(RATE_LIMIT_DELAY)

        # Write first word immediately, then batch the rest
        if idx == 0 or len(pending_updates) >= BATCH_SIZE:
            print(f"  Writing batch of {len(pending_updates)} updates...")
            execute_with_retry(
                service.spreadsheets().values().batchUpdate(
                    spreadsheetId=SPREADSHEET_ID,
                    body={"valueInputOption": "RAW", "data": pending_updates},
                )
            )
            total_written += len(pending_updates)
            pending_updates = []

    # Write any remaining updates
    if pending_updates:
        print(f"Writing final batch of {len(pending_updates)} updates...")
        execute_with_retry(
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"valueInputOption": "RAW", "data": pending_updates},
            )
        )
        total_written += len(pending_updates)

    print(f"Done! Wrote {total_written} updates total.")

    # Sort both sheets: unreviewed first (newest date first), then reviewed
    sort_sheets(service)


def sort_sheets(service):
    """Sort Sheet1 and Sheet2 together: unreviewed on top (newest first), reviewed below."""
    print("\nSorting sheets...")
    sheet1_rows = read_sheet(service, "Sheet1!A:E")
    sheet2_rows = read_sheet(service, "Sheet2!A:E")

    if len(sheet1_rows) < 2:
        return

    header1 = sheet1_rows[0]
    header2 = sheet2_rows[0] if sheet2_rows else []

    # Pair data rows, skipping empty rows (no Spanish word in Sheet1 col B)
    pairs = []
    max_rows = max(len(sheet1_rows), len(sheet2_rows))
    for i in range(1, max_rows):
        s1 = sheet1_rows[i] if i < len(sheet1_rows) else []
        s2 = sheet2_rows[i] if i < len(sheet2_rows) else []
        while len(s1) < 5:
            s1.append("")
        while len(s2) < 5:
            s2.append("")
        if not s1[1].strip():
            continue
        pairs.append((s1, s2))

    # Sort: unreviewed first (newest date first), then reviewed (newest date first)
    unreviewed = [(s1, s2) for s1, s2 in pairs if s2[2].strip().upper() != "TRUE"]
    reviewed = [(s1, s2) for s1, s2 in pairs if s2[2].strip().upper() == "TRUE"]
    unreviewed.sort(key=lambda p: p[0][0] or "", reverse=True)
    reviewed.sort(key=lambda p: p[0][0] or "", reverse=True)
    pairs = unreviewed + reviewed

    # Clear a large fixed range to catch any stray rows from previous runs
    execute_with_retry(
        service.spreadsheets().values().clear(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A2:E10000",
        )
    )
    execute_with_retry(
        service.spreadsheets().values().clear(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet2!A2:E10000",
        )
    )

    all_s1 = [p[0] for p in pairs]

    # Convert reviewed column (col C, index 2) back to boolean so checkboxes aren't
    # replaced with literal 'TRUE'/'FALSE' strings when writing with RAW mode.
    all_s2 = []
    for s2 in [p[1] for p in pairs]:
        row = list(s2)
        if len(row) > 2:
            row[2] = row[2].strip().upper() == "TRUE"
        all_s2.append(row)

    execute_with_retry(
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A2",
            valueInputOption="RAW",
            body={"values": all_s1},
        )
    )

    execute_with_retry(
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet2!A2",
            valueInputOption="RAW",
            body={"values": all_s2},
        )
    )

    print(f"Sorted: {len(unreviewed)} unreviewed on top, {len(reviewed)} reviewed below")


if __name__ == "__main__":
    main()
