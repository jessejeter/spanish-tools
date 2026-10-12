"""Why is today's expected load high? Yesterday's reviews and old vs new model."""
import json
from datetime import datetime, timedelta
import numpy as np
import forecast as F
from retrain import APPS_SCRIPT_URL, fetch_srs

srs = fetch_srs(APPS_SCRIPT_URL)
now = datetime.now(F.TZ).replace(tzinfo=None)
mid = datetime(now.year, now.month, now.day)
rbc = {}
for w, d in srs.items():
    if w.startswith("__") or w.startswith("frame:") or d.get("retired"):
        continue
    revs = []
    for r in d.get("reviews", []):
        try: revs.append((F.parse_local(r["date"]), bool(r.get("passed"))))
        except Exception: pass
    if revs: rbc[w] = sorted(revs)
for k in range(4, -1, -1):
    day = mid - timedelta(days=k)
    revs = [(dt, p) for v in rbc.values() for dt, p in v if day <= dt < day + timedelta(days=1)]
    print(day.date(), "reviews:", len(revs), "first:", min(revs)[0].time() if revs else "-", "last:", max(revs)[0].time() if revs else "-")

cards = list(rbc)
st = F.card_state(rbc, cards, mid, mid)
t = 19.7 / 24
for name, path in [("new model (today)", "vocab_model.json"), ("yesterday's model", "model_prev.json")]:
    m = json.loads(open(path).read())
    p = F.p_recall(m, t, st["last"], st["first"], st["nc"], st["nw"])
    print(f"{name}: below 90% tonight = {(p < 0.9).sum()}, 0.88-0.92 band = {((p > 0.88) & (p < 0.92)).sum()}, median p = {np.median(p):.3f}")
# Below 90% at the start of yesterday's session with each model (what yesterday's run saw)
y = mid - timedelta(days=1)
st_y = F.card_state(rbc, cards, y, y)
for name, path in [("new model", "vocab_model.json"), ("yesterday's model", "model_prev.json")]:
    m = json.loads(open(path).read())
    p = F.p_recall(m, t, st_y["last"], st_y["first"], st_y["nc"], st_y["nw"])
    print(f"{name}: below 90% at yesterday evening (before yesterday's reviews) = {(p < 0.9).sum()}")
