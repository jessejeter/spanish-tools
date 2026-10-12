"""Dry run: what the fixed sync would add to Sheet1. Writes nothing to Sheets."""
import csv, collections, subprocess
import analyze_vocab as av
from forecast import fetch_sheet

old = list(csv.reader(open("spanishdict_vocab.csv", encoding="utf-8-sig")))[1:]
subprocess.run(["python", "sync_vocab.py"], check=True, capture_output=True)
new = list(csv.reader(open("spanishdict_vocab.csv", encoding="utf-8-sig")))[1:]
print(f"CSV rows: old {len(old)} -> new {len(new)}")
key = lambda r: (r[1], r[2])
added = collections.Counter(map(key, new)) - collections.Counter(map(key, old))
lost = collections.Counter(map(key, old)) - collections.Counter(map(key, new))
print("rows only in new CSV:", len(added), sorted(added)[:40])
print("rows only in old CSV (should be none):", sorted(lost)[:20])

sheet1 = [["header"]] + fetch_sheet("Sheet1")
rows = av.find_new_sheet1_rows(new, sheet1)
print(f"\nWould append {len(rows)} rows to Sheet1:")
for r in rows: print("  ", r)

print("\nExisting Sheet1 rows for those words (date | spanish | english | sense | ctx):")
want = {r[1].strip().lower() for r in rows}
for r in sheet1[1:]:
    r = r + [""] * (7 - len(r))
    if r[1].strip().lower() in want:
        print("   ", r[0], "|", r[1], "|", r[2], "|", r[5], "|", r[6])
