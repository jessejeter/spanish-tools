"""Trace the forecast's day-0 simulation round by round."""
import json
from datetime import datetime
import numpy as np
import forecast as F
from retrain import APPS_SCRIPT_URL, fetch_srs

srs = fetch_srs(APPS_SCRIPT_URL)
model = json.loads(open("vocab_model.json").read())
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
    rbc[w] = sorted(revs)
deck = F.load_deck()
cards = [c for c in deck if not (srs.get(c) or {}).get("retired")]
st = F.card_state(rbc, cards, mid, mid)
n = st["nc"] + st["nw"]
print("deck cards", len(cards), "with history", int((n > 0).sum()), "no history", int((n == 0).sum()),
      "| srs cards with history", sum(1 for v in rbc.values() if v), "| in srs not deck", sum(1 for c in rbc if rbc[c] and c not in deck))
nohist = [c for c in cards if not any(r[0] < mid for r in rbc.get(c, []))]
print("no-history deck cards (sample):", [(c, deck[c]) for c in nohist[:15]])
t = 20.68 / 24
p = F.p_recall(model, t, st["last"], st["first"], st["nc"], st["nw"])
print("seen & below 90%:", int(((n > 0) & (p < 0.9)).sum()))

due = (n > 0) & (p < 0.9)
t2 = t + 3 / 1440
for label, dc, dw in [("after a PASS", 1, 0), ("after 2 passes", 2, 0), ("after a FAIL", 0, 1)]:
    q = F.p_recall(model, t2, np.full(len(p), t), st["first"], st["nc"] + dc, st["nw"] + dw)
    print(f"due cards still <90% {label}: {int((due & (q < 0.9)).sum())} of {int(due.sum())}")
acc = st["nc"] / np.maximum(n, 1)
print("due cards accuracy quartiles:", np.percentile(acc[due], [25, 50, 75]).round(2), "| all cards median acc:", round(float(np.median(acc[n > 0])), 2))
print("due cards n reviews quartiles:", np.percentile(n[due], [25, 50, 75]))
_, oc = F.simulate(model, st, np.zeros(len(cards), int), 1, 20.68, 0.84, 50, np.random.default_rng(1))
print("simulated day-0 repeat reviews (no factor):", oc.mean())
