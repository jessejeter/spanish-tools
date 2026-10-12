"""How many averaged training runs make the nightly model stable?"""
import json, math, time
from datetime import datetime, timedelta
import numpy as np
from joblib import Parallel, delayed
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
import forecast as F
from retrain import APPS_SCRIPT_URL, fetch_srs, build_dataset

M = 20                     # members trained per snapshot; K-member ensembles use the first K
KS = [1, 2, 3, 5, 10, 20]

srs = fetch_srs(APPS_SCRIPT_URL)
vocab = {k: v for k, v in srs.items() if not k.startswith("frame:") and not k.startswith("__")}
now = datetime.now(F.TZ).replace(tzinfo=None)
mid = datetime(now.year, now.month, now.day)

def cut(data, cutoff):
    out = {}
    for w, d in data.items():
        revs = [r for r in d.get("reviews", []) if "date" in r and F.parse_local(r["date"]) < cutoff]
        out[w] = {**d, "reviews": revs}
    return out

def fit(X, y, seed):
    sc = StandardScaler().fit(X)
    clf = MLPClassifier(hidden_layer_sizes=(16, 8), activation="relu", solver="adam", alpha=0.01,
                        max_iter=1000, random_state=seed).fit(sc.transform(X), y)
    return {"scaler_mean": sc.mean_.tolist(), "scaler_std": sc.scale_.tolist(),
            "weights": [w.tolist() for w in clf.coefs_], "biases": [b.tolist() for b in clf.intercepts_]}

def train_members(data):
    rows = build_dataset(data)
    X = np.array([r[0] for r in rows]); y = np.array([r[1] for r in rows])
    t = time.time()
    members = Parallel(n_jobs=-1)(delayed(fit)(X, y, s) for s in range(M))
    print(f"  {len(rows)} rows, {M} fits in {time.time() - t:.0f}s")
    return members

# Current card states, evaluated at tonight's typical session time
rbc = {}
for w, d in vocab.items():
    if d.get("retired"): continue
    revs = []
    for r in d.get("reviews", []):
        try: revs.append((F.parse_local(r["date"]), bool(r.get("passed"))))
        except Exception: pass
    if revs: rbc[w] = sorted(revs)
cards = list(rbc)
st = F.card_state(rbc, cards, mid, mid)
t = 20.7 / 24
n = st["nc"] + st["nw"]
Xc = np.column_stack([np.log1p(np.maximum(t - st["last"], 0.01)), st["nc"], st["nw"], st["nc"] / n,
                      np.log1p(n), np.log1p(np.maximum(t - st["first"], 0.01))])

# 1) Night-to-night stability: snapshots as of the last 4 midnights + now
snaps = [mid - timedelta(days=k) for k in (3, 2, 1, 0)]
P = []  # per snapshot: (M, cards) member predictions
for c in snaps:
    print("snapshot", c.date())
    mem = train_members(cut(vocab, c))
    P.append(np.array([F.nn_forward(Xc, m) for m in mem]))

print("\nNight-to-night change in tonight's predictions (avg over 3 night pairs):")
print(f"{'K':>3} {'cards flipping 90% line':>24} {'mean |dP|':>10} {'max |dP|':>9} {'below-90 counts by night':>28}")
for K in KS:
    ens = [p[:K].mean(0) for p in P]
    flips = [int(((a < 0.9) != (b < 0.9)).sum()) for a, b in zip(ens, ens[1:])]
    dp = [np.abs(a - b) for a, b in zip(ens, ens[1:])]
    print(f"{K:>3} {np.mean(flips):>24.1f} {np.mean([d.mean() for d in dp]):>10.4f} {np.max([d.max() for d in dp]):>9.3f} "
          f"{str([int((e < 0.9).sum()) for e in ens]):>28}")

# Pure seed noise on the same data: ensemble of members [0..K) vs [K..2K)
print("\nSame data, different seeds (pure training noise):")
for K in [1, 2, 3, 5, 10]:
    a, b = P[-1][:K].mean(0), P[-1][K:2 * K].mean(0)
    print(f"  K={K:>2}: flips {int(((a < 0.9) != (b < 0.9)).sum())}, mean |dP| {np.abs(a - b).mean():.4f}")

# 2) Accuracy: train on data before 14 days ago, score the last 14 days of reviews
hold = mid - timedelta(days=14)
print("\nHoldout (last 14 days):")
mem = train_members(cut(vocab, hold))
test_rows = [r for r in build_dataset(vocab)]
# keep only rows whose review happened in the holdout window: rebuild with dates
test = []
for w, d in vocab.items():
    if d.get("retired"): continue
    revs = d.get("reviews", [])
    for i in range(1, len(revs)):
        try: dt = F.parse_local(revs[i]["date"])
        except Exception: continue
        if hold <= dt < mid:
            sub = build_dataset({w: {**d, "reviews": revs[:i + 1]}})
            if sub: test.append(sub[-1])
Xt = np.array([r[0] for r in test]); yt = np.array([r[1] for r in test])
Pt = np.array([F.nn_forward(Xt, m) for m in mem])
print(f"  {len(test)} holdout reviews, pass rate {yt.mean():.3f}")
for K in KS:
    p = np.clip(Pt[:K].mean(0), 1e-6, 1 - 1e-6)
    ll = -np.mean(yt * np.log(p) + (1 - yt) * np.log(1 - p))
    brier = np.mean((p - yt) ** 2)
    print(f"  K={K:>2}: log loss {ll:.4f}, Brier {brier:.4f}")
