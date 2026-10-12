"""
One-time migration: record which meaning owns each word's existing review history.

Review history used to be keyed by the Spanish word alone, so every meaning of
a word shared one record. Cards are now keyed by "spanish: english", except
that the meaning which already owns a legacy (Spanish-only) record keeps using
it, so no history moves. This file pins that owner down before additional
meanings are synced into Sheet1, where row order alone would be ambiguous.

Writes srs_keys.json: {spanish: card_id of the meaning that owns srs[spanish]}.

Usage:
    python build_srs_keys.py
"""

import json
from pathlib import Path

from forecast import fetch_sheet
from retrain import APPS_SCRIPT_URL, fetch_srs

OUTPUT_PATH = Path(__file__).parent / "srs_keys.json"


def card_id(spanish: str, english: str) -> str:
    """Same as cardId() in index.html / stats.html / search.html."""
    return f"{spanish.strip()}: {english.strip()}".lower()


def main():
    srs = fetch_srs(APPS_SCRIPT_URL)
    sheet1 = fetch_sheet("Sheet1")

    owners = {}
    for row in sheet1:
        row = row + [""] * (3 - len(row))
        spanish, english = row[1].strip(), row[2].strip()
        if spanish and spanish in srs and spanish not in owners:
            owners[spanish] = card_id(spanish, english)

    legacy = [w for w in srs if not w.startswith("__") and not w.startswith("frame:")]
    orphans = [w for w in legacy if w not in owners]
    OUTPUT_PATH.write_text(json.dumps(owners, ensure_ascii=False, indent=0, sort_keys=True))
    print(f"{len(owners)} legacy records mapped to a meaning; {len(orphans)} with no Sheet1 row "
          f"(e.g. {orphans[:5]})")


if __name__ == "__main__":
    main()
