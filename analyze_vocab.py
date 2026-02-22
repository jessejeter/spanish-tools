#!/usr/bin/env python3
"""
Vocabulary AI Analysis

Reads vocabulary from Google Sheet (Sheet1), generates AI analysis
using Gemini API, and writes results to Sheet2.
"""

import json
import os
import sys
import time

import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SPREADSHEET_ID = "14oqOzF2MXMDvhp8XbZo1fa3yFHTW3anVgfFrHvK4tNc"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
RATE_LIMIT_DELAY = 5  # seconds between Gemini calls
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


def read_sheet(service, range_name):
    """Read values from a sheet range."""
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
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
        if response.status_code == 429:
            wait = RATE_LIMIT_DELAY * (2 ** attempt)  # 5, 10, 20, 40, 80s
            print(f"  Rate limited, waiting {wait}s (attempt {attempt + 1}/{MAX_RETRIES})")
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
        "can you separate each section with a vertical space?"
    )


def make_other_translations_prompt(english_word):
    """Build the Gemini prompt for Other Translations (Sheet2 col E)."""
    return (
        f"{english_word} What are the main spanish translations of this word? "
        "List all *very* common ones. Omit infrequent ones. Also give the sense "
        "of the translation in parentheses"
    )


def main():
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        print("Error: GEMINI_API_KEY environment variable not set")
        sys.exit(1)

    print("Authenticating with Google Sheets API...")
    service = get_sheets_service()

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

        # Write batch to sheet periodically so progress isn't lost
        if len(pending_updates) >= BATCH_SIZE:
            print(f"  Writing batch of {len(pending_updates)} updates...")
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"valueInputOption": "RAW", "data": pending_updates},
            ).execute()
            total_written += len(pending_updates)
            pending_updates = []

    # Write any remaining updates
    if pending_updates:
        print(f"Writing final batch of {len(pending_updates)} updates...")
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "RAW", "data": pending_updates},
        ).execute()
        total_written += len(pending_updates)

    print(f"Done! Wrote {total_written} updates total.")


if __name__ == "__main__":
    main()
