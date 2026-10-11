"""One-off: how many new words per day keeps review time at ~5 min/day long-term?"""
import json
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np

import forecast as F
from retrain import APPS_SCRIPT_URL, fetch_srs

H = 730
BUDGET = 5.0

now = datetime.now(F.TZ).replace(tzinfo=None)
today = now.date()
today_mid = datetime(today.year, today.month, today.day)
srs = fetch_srs(APPS_SCRIPT_URL)
model = json.loads(F.MODEL_PATH.read_text())

timer = defaultdict(float)
for e in (srs.get(F.TIMER_SYNC_KEY) or {}).get("reviews", []):
    for d, s in (e.get("data") or {}).items():
        timer[d] += s
rbc = {}
for w, data in srs.items():
    if w.startswith("__") or w.startswith("frame:") or data.get("retired"):
        continue
    revs = []
    for r in data.get("reviews", []):
        try:
            revs.append((F.parse_local(r["date"]), bool(r.get("passed"))))
        except (KeyError, ValueError, TypeError):
            pass
    rbc[w] = sorted(revs)

deck = F.load_deck()
cards, avail = [], []
for c, dr in deck.items():
    if (srs.get(c) or {}).get("retired"):
        continue
    prior = any(r[0] < today_mid for r in rbc.get(c, []))
    cards.append(c); avail.append(1 if dr == today.isoformat() and not prior else 0)
avail = np.array(avail)

firsts = [r[0][1] for r in rbc.values() if r and today_mid - timedelta(days=90) <= r[0][0] < today_mid]
p_first = float(np.mean(firsts))
starts = {}
for revs in rbc.values():
    for dt, _ in revs:
        if today_mid - timedelta(days=30) <= dt < today_mid and (dt.date() not in starts or dt < starts[dt.date()]):
            starts[dt.date()] = dt
session_hour = float(np.median([(v - datetime(v.year, v.month, v.day)).total_seconds() / 3600 for v in starts.values()]))
fd, od = F.daily_counts({c: [r for r in v if r[0] < today_mid] for c, v in rbc.items()})
s1, s2, _ = F.fit_seconds(timer, fd, od, today)
rng = np.random.default_rng(7)
factor, _ = F.backtest(model, rbc, today_mid, session_hour, p_first, rng)
print(f"cards={len(cards)} p_first={p_first:.2f} hour={session_hour:.1f} s1={s1:.1f} s2={s2:.1f} factor={factor:.2f}")

mins = lambda fc, oc: (fc * s1 + oc * s2 * factor) / 60
state = F.card_state(rbc, cards, today_mid, today_mid)
fc, oc = F.simulate(model, state, avail, H, session_hour, p_first, 20, rng)
base = mins(fc, oc).mean(0)
empty = {k: np.zeros(1) for k in ("last", "first", "nc", "nw")}
nfc, noc = F.simulate(model, empty, np.array([1]), H, session_hour, p_first, 3000, rng)
e = mins(nfc, noc).mean(0)          # minutes on day d from one word marked on day 0
S = np.cumsum(e)                    # load on day H from one word marked every day

print("\nOne word's review time by age:")
for a, b in [(1, 2), (2, 8), (8, 31), (31, 91), (91, 181), (181, 366), (366, 730)]:
    print(f"  days {a:>3}-{b - 1:<3}: {e[a:b].sum() * 60:6.1f} s total, reviews {(nfc + noc * factor).mean(0)[a:b].sum():.2f}")
print(f"  lifetime-to-2y: {e.sum() * 60:.0f} s")

print("\nIf you add N words EVERY day (starting today):")
print(f"{'day':>5} {'existing deck':>14} {'per word/day':>13} {'N for 5 min':>12}")
for h in [7, 14, 30, 60, 90, 180, 365, 545, 715]:
    w = slice(max(1, h - 7), h + 7)
    b, s = base[w].mean(), S[w].mean()
    print(f"{h:>5} {b:>12.2f}m {s * 60:>11.1f}s {(BUDGET - b) / s:>12.1f}")

for N in [5, 8, 10, 12, 15, 20]:
    tot = base + N * np.concatenate([[0], S[:-1]])
    print(f"N={N:>2}: min/day at d30 {tot[23:37].mean():.1f}, d90 {tot[83:97].mean():.1f}, "
          f"d180 {tot[173:187].mean():.1f}, d365 {tot[358:372].mean():.1f}, d715 {tot[708:722].mean():.1f}")
