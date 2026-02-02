#!/usr/bin/env python3
"""
SpanishDict to Google Sheets Vocabulary Sync

This script scrapes vocabulary words from SpanishDict lists and syncs them
to a Google Spreadsheet, tracking when words were first added.
"""

import json
import re
import os
from datetime import datetime
from pathlib import Path

import requests
import gspread
from google.oauth2.service_account import Credentials

# Configuration
SPANISHDICT_URLS = {
    "misc": "https://www.spanishdict.com/lists/8562777/jesse35630s-misc",
    "nouns": "https://www.spanishdict.com/lists/8543367/jesse35630s-nouns",
    "verbs": "https://www.spanishdict.com/lists/8524520/jesse35630s-verbs",
    "adjectives": "https://www.spanishdict.com/lists/8545180/jesse35630s-adjectives",
    "phrases": "https://www.spanishdict.com/lists/8545326/jesse35630s-phrases",
}

SPREADSHEET_ID = "1os_1x085Kr4eDVdDrylwGdZlR2HW8a2iocRN5Dm0dI0"

# Path to your Google service account credentials JSON file
# Update this path to where you save your credentials
CREDENTIALS_PATH = Path(__file__).parent / "credentials.json"


def scrape_spanishdict_list(url: str) -> list[dict]:
    """
    Scrape vocabulary words from a SpanishDict list page.

    Returns a list of dicts with 'spanish' and 'english' keys.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    # Find the embedded JSON data in window.SD_COMPONENT_DATA
    match = re.search(
        r'window\.SD_COMPONENT_DATA\s*=\s*(\{.*?\});?\s*</script>',
        response.text,
        re.DOTALL
    )

    if not match:
        print(f"Warning: Could not find vocabulary data in {url}")
        return []

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        print(f"Warning: Failed to parse JSON from {url}: {e}")
        return []

    words = []

    # Data is at top level (not nested under props.pageProps)
    word_list = data.get("words", [])
    senses = data.get("senses", [])
    translations = data.get("translations", [])

    # Build lookup: senseId -> translation text
    sense_to_translation = {t.get("senseId"): t.get("translation") for t in translations}

    # Build lookup: wordId -> senseId
    word_to_sense = {s.get("wordId"): s.get("id") for s in senses}

    # Extract each word with its translation
    for word_entry in word_list:
        word_id = word_entry.get("id")
        spanish = word_entry.get("source")

        if not spanish:
            continue

        # word.id -> sense.wordId -> sense.id -> translation.senseId
        sense_id = word_to_sense.get(word_id)
        english = sense_to_translation.get(sense_id, "") if sense_id else ""

        words.append({
            "spanish": spanish,
            "english": english,
        })

    return words


def get_all_vocabulary() -> list[dict]:
    """
    Scrape all vocabulary from all SpanishDict lists.

    Returns a list of dicts with 'spanish', 'english', and 'type' keys.
    """
    all_words = []

    for word_type, url in SPANISHDICT_URLS.items():
        print(f"Fetching {word_type} from {url}...")
        words = scrape_spanishdict_list(url)

        for word in words:
            word["type"] = word_type

        all_words.extend(words)
        print(f"  Found {len(words)} words")

    return all_words


def get_google_sheets_client():
    """
    Authenticate and return a gspread client.
    """
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"Credentials file not found at {CREDENTIALS_PATH}\n"
            "Please download your service account JSON key from Google Cloud Console "
            "and save it as 'credentials.json' in the same folder as this script."
        )

    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
    return gspread.authorize(creds)


def sync_to_google_sheets(words: list[dict]):
    """
    Sync vocabulary words to Google Sheets.

    - Reads existing words from the sheet
    - Adds only new words (not already in the sheet)
    - Records the date each word was added
    """
    print("\nConnecting to Google Sheets...")
    client = get_google_sheets_client()

    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    # Use the first sheet
    sheet = spreadsheet.sheet1

    # Get all existing data
    existing_data = sheet.get_all_values()

    # Check if sheet has headers, if not add them
    headers = ["Spanish", "English", "Type", "Date Added"]
    if not existing_data or existing_data[0] != headers:
        if not existing_data:
            sheet.append_row(headers)
            existing_spanish = set()
        else:
            # Sheet has data but different headers - assume first row is headers
            existing_spanish = {row[0] for row in existing_data[1:] if row}
    else:
        existing_spanish = {row[0] for row in existing_data[1:] if row}

    # Find new words
    today = datetime.now().strftime("%Y-%m-%d")
    new_words = []

    for word in words:
        if word["spanish"] not in existing_spanish:
            new_words.append([
                word["spanish"],
                word["english"],
                word["type"],
                today
            ])
            existing_spanish.add(word["spanish"])

    if new_words:
        print(f"Adding {len(new_words)} new words...")
        # Batch append for efficiency
        sheet.append_rows(new_words)
        print("Done!")
    else:
        print("No new words to add.")

    return len(new_words)


def main():
    """Main entry point."""
    print("=" * 50)
    print("SpanishDict to Google Sheets Sync")
    print(f"Running at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # Scrape all vocabulary
    all_words = get_all_vocabulary()
    print(f"\nTotal words scraped: {len(all_words)}")

    # Sync to Google Sheets
    new_count = sync_to_google_sheets(all_words)

    print("\n" + "=" * 50)
    print(f"Sync complete. Added {new_count} new words.")
    print("=" * 50)


if __name__ == "__main__":
    main()
