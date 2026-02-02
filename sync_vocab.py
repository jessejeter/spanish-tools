#!/usr/bin/env python3
"""
SpanishDict Vocabulary Scraper

Scrapes vocabulary words from SpanishDict lists and exports to CSV.
"""

import csv
import json
import re
from datetime import datetime
from pathlib import Path

import requests

# Configuration - your SpanishDict list URLs
SPANISHDICT_URLS = {
    "misc": "https://www.spanishdict.com/lists/8562777/jesse35630s-misc",
    "nouns": "https://www.spanishdict.com/lists/8543367/jesse35630s-nouns",
    "verbs": "https://www.spanishdict.com/lists/8524520/jesse35630s-verbs",
    "adjectives": "https://www.spanishdict.com/lists/8545180/jesse35630s-adjectives",
    "phrases": "https://www.spanishdict.com/lists/8545326/jesse35630s-phrases",
}

OUTPUT_FILE = Path(__file__).parent / "spanishdict_vocab.csv"


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


def export_to_csv(words: list[dict], output_path: Path = OUTPUT_FILE):
    """Export vocabulary to CSV file with UTF-8 BOM for Excel compatibility."""
    today = datetime.now().strftime("%Y-%m-%d")

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Spanish", "English", "Type", "Date Added"])
        for w in words:
            writer.writerow([w["spanish"], w["english"], w["type"], today])

    print(f"\nExported {len(words)} words to {output_path}")


def main():
    """Main entry point."""
    print("=" * 50)
    print("SpanishDict Vocabulary Scraper")
    print(f"Running at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50 + "\n")

    all_words = get_all_vocabulary()
    export_to_csv(all_words)

    print("\nDone!")


if __name__ == "__main__":
    main()
