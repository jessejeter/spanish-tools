#!/usr/bin/env python3
"""
SpanishDict Vocabulary Scraper

Scrapes vocabulary words from SpanishDict lists and exports to CSV.
"""

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo('America/New_York')

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

PART_OF_SPEECH = {
    1: "adjective",
    2: "transitive verb",
    3: "reflexive verb",
    4: "noun",
    5: "phrase",
    9: "adverb",
    10: "preposition",
    11: "conjunction",
    12: "interjection",
    13: "intransitive verb",
    14: "reciprocal verb",
    20: "proper noun",
    22: "pronominal verb",
    25: "transitive verb phrase",
    26: "intransitive verb phrase",
    27: "plural noun",
    32: "pronominal verb phrase",
}


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

    # Data is at top level (not nested under props.pageProps)
    word_list = data.get("words", [])
    senses = data.get("senses", [])
    translations = data.get("translations", [])
    vocab_translations = data.get("vocabTranslations", [])

    words_by_id = {w.get("id"): w for w in word_list}
    senses_by_id = {s.get("id"): s for s in senses}
    translations_by_id = {t.get("id"): t for t in translations}

    def make_row(word_entry, sense, trans, created):
        date_added = ""
        if created:
            dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
            date_added = dt.astimezone(EASTERN).strftime('%Y-%m-%d')
        pos = PART_OF_SPEECH.get(sense.get("partOfSpeechId"), str(sense.get("partOfSpeechId", "")))
        gender = sense.get("gender", "")
        if gender:
            pos = f"{pos} ({gender})"
        return {
            "spanish": word_entry.get("source"),
            "english": trans.get("translation", ""),
            "context_en": sense.get("contextEn", ""),
            "part_of_speech": pos,
            "popularity": word_entry.get("popularity", ""),
            "date_added": date_added,
        }

    # One row per saved list entry. A word saved with two meanings has two
    # entries pointing at different translations of the same word; walking
    # words instead (one sense per word) kept only one of them.
    words = []
    seen = set()
    for vt in vocab_translations:
        trans = translations_by_id.get(vt.get("translationId"))
        sense = senses_by_id.get(trans.get("senseId")) if trans else None
        word_entry = words_by_id.get(sense.get("wordId")) if sense else None
        if not word_entry or not word_entry.get("source") or trans.get("id") in seen:
            continue
        seen.add(trans.get("id"))
        words.append(make_row(word_entry, sense, trans, vt.get("createdAt", "")))

    # Fallback for any word the entries above didn't reach (or a page without them)
    covered = {sense.get("wordId") for sense in senses if any(
        t.get("senseId") == sense.get("id") and t.get("id") in seen for t in translations)}
    for word_entry in word_list:
        if word_entry.get("id") in covered or not word_entry.get("source"):
            continue
        sense = next((sn for sn in senses if sn.get("wordId") == word_entry.get("id")), {})
        trans = next((t for t in translations if t.get("senseId") == sense.get("id")), {}) if sense else {}
        words.append(make_row(word_entry, sense, trans, ""))

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
        all_words.extend(words)
        print(f"  Found {len(words)} words")

    return all_words


def export_to_csv(words: list[dict], output_path: Path = OUTPUT_FILE):
    """Export vocabulary to CSV file with UTF-8 BOM for Excel compatibility."""
    words.sort(key=lambda w: (w.get("date_added", ""), w["spanish"].lower()))

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Date Added", "Spanish", "English", "Part of Speech", "Popularity"])
        for w in words:
            writer.writerow([w["date_added"], w["spanish"], w["english"], w["part_of_speech"], w["popularity"]])

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
