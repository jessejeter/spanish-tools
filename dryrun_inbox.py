"""Read-only: where is sandía?"""
import json
import analyze_vocab as av
from retrain import APPS_SCRIPT_URL, fetch_srs
svc = av.get_sheets_service()
s1 = av.read_sheet(svc, "Sheet1!A:G"); s2 = av.read_sheet(svc, "Sheet2!A:E")
for i, r in enumerate(s1):
    if r[1:2] and "sand" in r[1].lower():
        r2 = s2[i] if i < len(s2) else []
        print("Sheet1 row", i + 1, r)
        print("Sheet2 row", i + 1, [((c[:70] + '…') if len(c) > 70 else c) for c in r2])
srs = fetch_srs(APPS_SCRIPT_URL)
for k, v in srs.items():
    if "sand" in k.lower():
        print("SRS", k, json.dumps({x: v.get(x) for x in ("retired", "lastReview")}), "reviews:", len(v.get("reviews", [])))
keys = json.load(open("srs_keys.json")) if __import__("os").path.exists("srs_keys.json") else {}
print("srs_keys owner:", {k: v for k, v in keys.items() if "sand" in k.lower()})
