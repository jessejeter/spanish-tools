#!/usr/bin/env python3
"""
SpanishDict Vocabulary Scraper

Scrapes vocabulary words from SpanishDict lists and exports to CSV.
"""

import csv
import json
import os
import re
import time
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

    words = []

    # Data is at top level (not nested under props.pageProps)
    word_list = data.get("words", [])
    senses = data.get("senses", [])
    translations = data.get("translations", [])

    vocab_translations = data.get("vocabTranslations", [])

    # Build lookup: senseId -> translation (text and id)
    sense_to_translation = {}
    for t in translations:
        sense_to_translation[t.get("senseId")] = {
            "text": t.get("translation"),
            "id": t.get("id"),
        }

    # Build lookup: translationId -> createdAt date
    translation_to_date = {}
    for vt in vocab_translations:
        created = vt.get("createdAt", "")
        if created:
            translation_to_date[vt.get("translationId")] = created[:10]  # "YYYY-MM-DD"

    # Build lookup: wordId -> sense data
    word_to_sense = {}
    for s in senses:
        word_to_sense[s.get("wordId")] = {
            "id": s.get("id"),
            "contextEn": s.get("contextEn", ""),
            "contextEs": s.get("contextEs", ""),
            "partOfSpeechId": s.get("partOfSpeechId", ""),
            "gender": s.get("gender", ""),
        }

    # Extract each word with its translation, metadata, and date added
    for word_entry in word_list:
        word_id = word_entry.get("id")
        spanish = word_entry.get("source")

        if not spanish:
            continue

        sense = word_to_sense.get(word_id, {})
        sense_id = sense.get("id")
        trans = sense_to_translation.get(sense_id, {}) if sense_id else {}
        english = trans.get("text", "")
        date_added = translation_to_date.get(trans.get("id"), "")

        pos = PART_OF_SPEECH.get(sense.get("partOfSpeechId"), str(sense.get("partOfSpeechId", "")))
        gender = sense.get("gender", "")
        if gender:
            pos = f"{pos} ({gender})"

        words.append({
            "spanish": spanish,
            "english": english,
            "context_en": sense.get("contextEn", ""),
            "part_of_speech": pos,
            "popularity": word_entry.get("popularity", ""),
            "date_added": date_added,
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
        all_words.extend(words)
        print(f"  Found {len(words)} words")

    return all_words


def load_existing_analyses(output_path: Path = OUTPUT_FILE) -> dict[str, str]:
    """Load previously generated AI analyses from the existing CSV, keyed by Spanish word."""
    analyses = {}
    if not output_path.exists():
        return analyses
    with open(output_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            analysis = row.get("AI Analysis", "").strip()
            if analysis:
                analyses[row["Spanish"]] = analysis
    return analyses


def generate_ai_analysis(word: dict) -> str:
    """Call Gemini API to generate analysis for a word."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return ""

    prompt = (
        f"For these cells, {word['spanish']}, {word['english']}, "
        f"{word['part_of_speech']}, {word['popularity']}, put them together "
        f"in this format: Cell1: Cell2 -- Cell 3 (Cell 4). Then, for this word, "
        f"{word['spanish']}, note the main definition(s) and usages (ignore less "
        f"common defs/usages), any important grammatical context (if anything "
        f"particularly unusual or interesting, but say none if nothing worth "
        f"noting) and a sample sentence, note the root origin, note the closest "
        f"etymologically related English words (maybe one to three). If said "
        f"English word is extremely rare define it in parentheses. If said English "
        f"word has serious meaning drift, or it is not obvious how the words are "
        f"etymologically related, detail the meaning and/or phonetic drift in "
        f"parentheses. If no related English words, note that. Also note any very "
        f"common etymologically related words in Spanish that are absolutely worth "
        f"knowing. You do not have to include Spanish words that are not commonly "
        f"used. If there are Spanish cognates worth including, then after listing "
        f"them, define them in parentheses. Also, formatting-wise, can you "
        f"separate each section with a vertical space?"
    )

    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"  Warning: Gemini API error for {word['spanish']}: {e}")
        return ""


def export_to_csv(words: list[dict], output_path: Path = OUTPUT_FILE):
    """Export vocabulary to CSV file with UTF-8 BOM for Excel compatibility."""
    words.sort(key=lambda w: (w.get("date_added", ""), w["spanish"].lower()))

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Date Added", "Spanish", "English", "Part of Speech", "Popularity", "AI Analysis"])
        for w in words:
            writer.writerow([w["date_added"], w["spanish"], w["english"], w["part_of_speech"], w["popularity"], w.get("ai_analysis", "")])

    print(f"\nExported {len(words)} words to {output_path}")


def main():
    """Main entry point."""
    print("=" * 50)
    print("SpanishDict Vocabulary Scraper")
    print(f"Running at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50 + "\n")

    all_words = get_all_vocabulary()

    # Load existing AI analyses so we only generate for new words
    existing = load_existing_analyses()
    new_count = 0
    for w in all_words:
        if w["spanish"] in existing:
            w["ai_analysis"] = existing[w["spanish"]]
        else:
            print(f"  Generating AI analysis for: {w['spanish']}")
            w["ai_analysis"] = generate_ai_analysis(w)
            new_count += 1
            if new_count % 15 == 0:
                time.sleep(60)  # Rate limit: 15 requests per minute
    print(f"\nGenerated {new_count} new AI analyses")

    export_to_csv(all_words)

    print("\nDone!")


if __name__ == "__main__":
    main()
