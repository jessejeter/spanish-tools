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
from datetime import datetime
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


def parse_date(date_str):
    """Parse date string in M/D/YYYY or YYYY-MM-DD format for correct chronological sorting."""
    if not date_str or not date_str.strip():
        return datetime.min
    s = date_str.strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        pass
    try:
        parts = s.split("/")
        if len(parts) == 3:
            return datetime(int(parts[2]), int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        pass
    return datetime.min


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


def find_neodict(obj):
    """Recursively search a parsed JSON structure for the 'neodict' key."""
    if isinstance(obj, dict):
        if "neodict" in obj:
            return obj["neodict"]
        for v in obj.values():
            result = find_neodict(v)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = find_neodict(item)
            if result is not None:
                return result
    return None


def scrape_sense(spanish_word, english_translation):
    """Return the SpanishDict sense label (contextEn) for the given translation.

    Fetches the SpanishDict page, extracts SD_COMPONENT_DATA, and walks
    neodict → posGroups → senses to find the sense whose translations match
    the english_translation string.  Returns "" on any error or no match.
    """
    try:
        url = f"https://www.spanishdict.com/translate/{spanish_word}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        marker = "window.SD_COMPONENT_DATA = "
        idx = resp.text.find(marker)
        if idx == -1:
            return ""

        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(resp.text, idx + len(marker))

        neodict = find_neodict(data)
        if not neodict:
            return ""

        eng_lower = english_translation.lower().strip()
        for entry in neodict:
            for pos_group in entry.get("posGroups", []):
                for sense in pos_group.get("senses", []):
                    for trans in sense.get("translations", []):
                        t = trans.get("translation", "").lower().strip()
                        if not t:
                            continue
                        # 1. Exact match
                        if t == eng_lower:
                            return sense.get("contextEn", "")
                        # 2. Sheet1 english is a prefix of SD translation
                        if t.startswith(eng_lower):
                            return sense.get("contextEn", "")
                        # 3. SD translation is a prefix of Sheet1 english
                        if eng_lower.startswith(t):
                            return sense.get("contextEn", "")
        return ""
    except Exception:
        return ""


def backfill_senses(service):
    """Fill Sheet1 col F (Sense) for rows that are missing it."""
    # Ensure col F has a header
    execute_with_retry(
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!F1",
            valueInputOption="RAW",
            body={"values": [["Sense"]]},
        )
    )
    print("Wrote 'Sense' header to Sheet1!F1")

    print("Reading Sheet1 (cols A:F)...")
    sheet1_rows = read_sheet(service, "Sheet1!A:F")

    if len(sheet1_rows) < 2:
        print("No data rows found.")
        return

    data_rows = sheet1_rows[1:]  # skip header
    total_words = len(data_rows)
    pending = []
    filled = 0

    for i, row in enumerate(data_rows):
        while len(row) < 6:
            row.append("")

        spanish = row[1]
        english = row[2]
        sense = row[5]

        if not spanish or sense:
            continue  # skip empty or already-filled rows

        sheet_row = i + 2  # 1-indexed; header is row 1, data starts at row 2
        print(f"[{i + 1}/{total_words}] {spanish} ({english}): ", end="", flush=True)

        result = scrape_sense(spanish, english)
        print(result if result else "(no match)")

        if result:
            pending.append({
                "range": f"Sheet1!F{sheet_row}",
                "values": [[result]],
            })
            filled += 1

        time.sleep(0.5)

        if len(pending) >= 20:
            execute_with_retry(
                service.spreadsheets().values().batchUpdate(
                    spreadsheetId=SPREADSHEET_ID,
                    body={"valueInputOption": "RAW", "data": pending},
                )
            )
            pending = []

    if pending:
        execute_with_retry(
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"valueInputOption": "RAW", "data": pending},
            )
        )

    print(f"\nDone! Filled {filled} senses.")


def needs_generation(value):
    """Check if a cell value is empty or contains an error."""
    if not value or not value.strip():
        return True
    v = value.strip().upper()
    return v in ("#ERROR!", "#VALUE!", "#REF!", "#N/A", "#NAME?", "LOADING...")


def make_analysis_prompt(spanish_word, english=None, sense=None):
    """Build the Gemini prompt for AI Analysis (Sheet2 col B)."""
    if sense:
        intro = (
            f"For the Spanish word \"{spanish_word}\" in the \"{sense}\" sense "
            f"(English: \"{english}\"), write the following sections.\n"
            f"Important: the Definition and Sample sentence must cover only this "
            f"specific sense — do not cover unrelated senses.\n\n"
        )
    else:
        intro = f"For the Spanish word \"{spanish_word}\", write the following sections:\n\n"
    return (
        intro +
        "Definition: Main definition(s) and usages. Ignore uncommon ones.\n\n"
        "Grammatical context: Note only what actually applies — write "
        "'Grammatical context: none.' if nothing is worth flagging, which is expected "
        "most of the time. Cover the following if relevant: unexpected or "
        "meaning-changing gender (e.g. el problema, el/la capital); irregular plural; "
        "invariable adjective (doesn't inflect for gender/number); gustar-type "
        "structure (indirect object + verb); preposition(s) the word takes (verbs "
        "and adjectives); notable conjugation irregularities, excluding spelling "
        "changes that merely preserve regular pronunciation; defective usage "
        "(restricted to certain tenses or persons); whether it triggers the "
        "subjunctive; transitive vs. intransitive when both exist and the distinction "
        "matters; preterite vs. imperfect meaning shift if significant; reflexive vs. "
        "non-reflexive meaning split when -se creates a meaningfully different sense, "
        "but don't note reflexivity that's already obvious from the word's form; "
        "register if notably colloquial, formal, literary, or vulgar; regional usage "
        "if the word is primarily associated with Spain or Latin America.\n\n"
        "Sample sentence: One Spanish sentence only — no English translation.\n\n"
        "Root origin: Etymology.\n\n"
        "Related English words: Closest etymologically related English words (one to "
        "three). Define rare ones in parentheses. Explain non-obvious relationships "
        "or meaning/phonetic drift in parentheses. Write 'none' if none exist.\n\n"
        "Related Spanish words: Very common etymologically related Spanish words worth "
        "knowing. Define each in parentheses. Omit the section if none are worth noting.\n\n"
        "IMPORTANT formatting rules: Write in plain text only. No markdown — no bold, "
        "no headers, no bullet points, no numbered lists. Write in flowing prose. "
        "Be concise. Use section labels: \"Definition:\", \"Grammatical context:\", "
        "\"Sample sentence:\", \"Root origin:\", \"Related English words:\", "
        "\"Related Spanish words:\". Separate each "
        "section with a blank line. For the sample sentence, write only the Spanish "
        "sentence — no English translation."
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

    # Track where new rows will land (header = row 1, existing data fills rows 2..N)
    first_new_sheet_row = len(sheet1_rows) + 1  # 1-indexed

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

    # Scrape sense labels for newly added rows
    print("Scraping senses for new words...")
    sense_updates = []
    for idx, row in enumerate(new_rows):
        spanish = row[1] if len(row) > 1 else ""
        english = row[2] if len(row) > 2 else ""
        if not spanish or not english:
            continue
        sense = scrape_sense(spanish, english)
        print(f"  {spanish}: {sense if sense else '(no match)'}")
        if sense:
            sheet_row = first_new_sheet_row + idx
            sense_updates.append({"range": f"Sheet1!F{sheet_row}", "values": [[sense]]})
        time.sleep(0.5)

    if sense_updates:
        execute_with_retry(
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"valueInputOption": "RAW", "data": sense_updates},
            )
        )


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
    sheet1_rows = read_sheet(service, "Sheet1!A:F")

    print("Reading Sheet2...")
    sheet2_rows = read_sheet(service, "Sheet2!A:E")

    # Sheet1: [Date Added, Spanish, English, POS, Popularity]
    # Sheet2: [?, AI Analysis, Reviewed, ?, Other Translations]

    if len(sheet1_rows) < 2:
        print("No data in Sheet1")
        return

    # Find rows that need generation (skip header at index 0)
    # Sheet2 has no header row, so Sheet2[i-1] pairs with Sheet1[i].
    # Sheet2 spreadsheet row number = i (1-indexed, since no header offset).
    words_to_process = []
    for i in range(1, len(sheet1_rows)):
        s1_row = sheet1_rows[i]
        s2_row = sheet2_rows[i - 1] if i - 1 < len(sheet2_rows) else []

        # Pad rows to expected width
        while len(s1_row) < 6:
            s1_row.append("")
        while len(s2_row) < 5:
            s2_row.append("")

        spanish = s1_row[1]  # Sheet1 col B
        english = s1_row[2]  # Sheet1 col C
        sense   = s1_row[5]  # Sheet1 col F

        if not spanish:
            continue

        need_analysis = needs_generation(s2_row[1])  # Sheet2 col B
        need_other = needs_generation(s2_row[4])  # Sheet2 col E

        if need_analysis or need_other:
            words_to_process.append(
                {
                    "sheet1_row": i + 1,  # 1-indexed Sheet1 row (has header)
                    "sheet2_row": i,      # 1-indexed Sheet2 row (no header)
                    "spanish": spanish,
                    "english": english,
                    "sense": sense,
                    "need_analysis": need_analysis,
                    "need_other": need_other,
                }
            )

    print(f"Found {len(words_to_process)} words needing analysis")

    if not words_to_process:
        print("Nothing to do!")
        sort_sheets(service)
        return

    # Process each word, writing in batches to preserve progress
    BATCH_SIZE = 10
    pending_updates = []
    total_written = 0

    for idx, word in enumerate(words_to_process):
        s2_row = word["sheet2_row"]
        spanish = word["spanish"]
        english = word["english"]
        sense   = word["sense"]

        print(f"[{idx + 1}/{len(words_to_process)}] Processing: {spanish} ({english})")

        if word["need_analysis"]:
            try:
                result = call_gemini(make_analysis_prompt(spanish, english, sense), gemini_key)
                pending_updates.append({"range": f"Sheet2!B{s2_row}", "values": [[result]]})
                print(f"  Analysis generated ({len(result)} chars)")
            except Exception as e:
                print(f"  Analysis failed: {e}")
            time.sleep(RATE_LIMIT_DELAY)

        if word["need_other"]:
            try:
                result = call_gemini(
                    make_other_translations_prompt(english), gemini_key
                )
                pending_updates.append({"range": f"Sheet2!E{s2_row}", "values": [[result]]})
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

    # Get Sheet2's numeric sheetId for data validation requests
    spreadsheet = execute_with_retry(service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID))
    sheet2_id = next(
        s["properties"]["sheetId"]
        for s in spreadsheet["sheets"]
        if s["properties"]["title"] == "Sheet2"
    )

    sheet1_rows = read_sheet(service, "Sheet1!A:F")
    sheet2_rows = read_sheet(service, "Sheet2!A:E")

    if len(sheet1_rows) < 2:
        return

    header1 = sheet1_rows[0]
    header2 = sheet2_rows[0] if sheet2_rows else []

    # Pair data rows, skipping empty rows (no Spanish word in Sheet1 col B).
    # Sheet2 has no header row, so Sheet2[i-1] corresponds to Sheet1[i].
    pairs = []
    max_rows = max(len(sheet1_rows), len(sheet2_rows) + 1)
    for i in range(1, max_rows):
        s1 = sheet1_rows[i] if i < len(sheet1_rows) else []
        s2 = sheet2_rows[i - 1] if i - 1 < len(sheet2_rows) else []
        while len(s1) < 6:
            s1.append("")
        while len(s2) < 5:
            s2.append("")
        if not s1[1].strip():
            continue
        pairs.append((s1, s2))

    # Sort: unreviewed first (newest date first), then reviewed (newest date first).
    # Use parse_date() to handle both M/D/YYYY and YYYY-MM-DD formats correctly.
    unreviewed = [(s1, s2) for s1, s2 in pairs if s2[2].strip().upper() != "TRUE"]
    reviewed = [(s1, s2) for s1, s2 in pairs if s2[2].strip().upper() == "TRUE"]
    unreviewed.sort(key=lambda p: parse_date(p[0][0]), reverse=True)
    reviewed.sort(key=lambda p: parse_date(p[0][0]), reverse=True)
    pairs = unreviewed + reviewed

    if not pairs:
        print("No data rows to sort")
        return

    n_rows = len(pairs)
    last_row = n_rows + 1  # last data row number (1-indexed, since data starts at row 2)

    # Clear a large fixed range to catch any stray rows from previous runs.
    # Sheet2 has no header row, so clear/write from row 1.
    execute_with_retry(
        service.spreadsheets().values().clear(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A2:F10000",
        )
    )
    execute_with_retry(
        service.spreadsheets().values().clear(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet2!A1:E10000",
        )
    )

    all_s1 = [p[0] for p in pairs]

    execute_with_retry(
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A2",
            valueInputOption="RAW",
            body={"values": all_s1},
        )
    )

    # Write all Sheet2 columns in one atomic call to prevent partial-write misalignment.
    # Col A formula + col C "TRUE"/"FALSE" need USER_ENTERED so they evaluate correctly.
    # AI text in cols B/D/E is very unlikely to start with "=" so USER_ENTERED is safe.
    # Sheet2 starts at row 1 (no header). Sheet1 data starts at row 2 (has header).
    all_s2 = [
        [
            f'=Sheet1!B{i + 2}&": "&Sheet1!C{i + 2}',          # col A: formula referencing Sheet1 row i+2
            p[1][1],                                              # col B: AI analysis
            "TRUE" if p[1][2].strip().upper() == "TRUE" else "FALSE",  # col C: checkbox
            p[1][3],                                              # col D: review date
            p[1][4],                                              # col E: other translations
        ]
        for i, p in enumerate(pairs)
    ]

    execute_with_retry(
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet2!A1",
            valueInputOption="USER_ENTERED",
            body={"values": all_s2},
        )
    )

    # Re-apply checkbox data validation to Sheet2 col C for all data rows.
    # Values.clear() and values.update() only affect cell values, not formatting/validation.
    # After a sort, rows shift positions so validation from old positions no longer matches.
    execute_with_retry(
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet2_id,
                        "startRowIndex": 0,        # 0-based; Sheet2 has no header
                        "endRowIndex": n_rows,
                        "startColumnIndex": 2,     # col C
                        "endColumnIndex": 3,
                    },
                    "rule": {
                        "condition": {"type": "BOOLEAN"},
                        "showCustomUi": True,
                    },
                }
            }]},
        )
    )

    print(f"Sorted: {len(unreviewed)} unreviewed on top, {len(reviewed)} reviewed below")


def full_regenerate():
    """Clear all Sheet2 col B (analysis) and col E (other translations) and regenerate from scratch.

    Use this after fixing sort_sheets bugs to wipe stale/scrambled AI data and
    regenerate everything correctly aligned.
    """
    service = get_sheets_service()
    print("Clearing Sheet2 col B (AI analysis) and col E (other translations)...")
    execute_with_retry(
        service.spreadsheets().values().batchClear(
            spreadsheetId=SPREADSHEET_ID,
            body={"ranges": ["Sheet2!B1:B10000", "Sheet2!E1:E10000"]},
        )
    )
    print("Cleared. Running full regeneration...")
    main()


def repair_sheet2_offset():
    """Fix the 1-row data offset in Sheet2.

    Sheet2's content columns (B:E) are shifted 1 row up relative to Sheet1.
    This inserts a blank row at Sheet2 row 2 so every word's data lines up
    with the correct Sheet1 row again.  Run once, then re-run normally to
    regenerate the missing analysis for the first word.
    """
    service = get_sheets_service()

    # Get Sheet2's numeric sheetId (required for insertDimension)
    spreadsheet = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheet2_id = next(
        s['properties']['sheetId']
        for s in spreadsheet['sheets']
        if s['properties']['title'] == 'Sheet2'
    )

    print("Inserting blank row at Sheet2 row 2 to fix 1-row offset...")
    execute_with_retry(
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{
                "insertDimension": {
                    "range": {
                        "sheetId": sheet2_id,
                        "dimension": "ROWS",
                        "startIndex": 1,  # 0-based → inserts before row 2
                        "endIndex": 2,
                    },
                    "inheritFromBefore": False,
                }
            }]},
        )
    )
    print("Done. Sheet2 data is now aligned with Sheet1.")
    print("The first vocabulary word has blank Sheet2 data — run analyze_vocab.py normally to fill it in.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--repair-offset":
            repair_sheet2_offset()
        elif sys.argv[1] == "--full-regenerate":
            full_regenerate()
        elif sys.argv[1] == "--backfill-senses":
            backfill_senses(get_sheets_service())
        else:
            print(f"Unknown argument: {sys.argv[1]}")
            sys.exit(1)
    else:
        main()
