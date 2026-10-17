"""Out-of-time calibration: for each recent day, train as the nightly job would have
(data before that day), then score that day's reviews. Ensemble (10) vs single net."""
import time
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np
from joblib import Parallel, delayed
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
import forecast as F
from retrain import APPS_SCRIPT_URL, fetch_srs, build_dataset

DAYS = 21
srs = fetch_srs(APPS_SCRIPT_URL)
vocab = {k: v for k, v in srs.items() if not k.startswith("__") and not k.startswith("frame:")}
now = datetime.now(F.TZ).replace(tzinfo=None)
mid = datetime(now.year, now.month, now.day)

def cut(data, cutoff):
    return {w: {**d, "reviews": [r for r in d.get("reviews", []) if "date" in r and F.parse_local(r["date"]) < cutoff]}
            for w, d in data.items()}

def fit(X, y, seed):
    sc = StandardScaler().fit(X)
    c = MLPClassifier(hidden_layer_sizes=(16, 8), activation="relu", solver="adam", alpha=0.01,
                      max_iter=1000, random_state=seed).fit(sc.transform(X), y)
    return sc, c

def predict(models, X):
    return np.mean([c.predict_proba(sc.transform(X))[:, 1] for sc, c in models], axis=0)

# Every review with its features (history before it), its time and outcome
events = []  # (dt, features, passed, first_of_day_for_card)
for w, d in vocab.items():
    revs = []
    for r in d.get("reviews", []):
        try: revs.append((F.parse_local(r["date"]), bool(r.get("passed"))))
        except Exception: pass
    revs.sort()
    seen_days = set()
    for i in range(1, len(revs)):
        dt, passed = revs[i]
        prev = revs[:i]
        n = len(prev); nc = sum(p for _, p in prev)
        dsl = max((dt - prev[-1][0]).total_seconds() / 86400, 0.01)
        dsf = max((dt - prev[0][0]).total_seconds() / 86400, 0.01)
        feats = [np.log1p(dsl), nc, n - nc, nc / n, np.log1p(n), np.log1p(dsf)]
        first = dt.date() not in seen_days
        seen_days.add(dt.date())
        events.append((dt, feats, passed, first))

bins = [0, 0.7, 0.8, 0.85, 0.9, 0.95, 1.01]
agg = {k: defaultdict(lambda: [0, 0.0, 0]) for k in ("ensemble", "single")}  # bin -> [n, sumP, passes]
daily = []
for k in range(DAYS, 0, -1):
    day = mid - timedelta(days=k); end = day + timedelta(days=1)
    todays = [e for e in events if day <= e[0] < end and e[3]]  # first review of the day per card
    if not todays:
        daily.append((day.date(), 0, None, None)); continue
    rows = build_dataset(cut(vocab, day))
    X = np.array([r[0] for r in rows]); y = np.array([r[1] for r in rows])
    ens = Parallel(n_jobs=-1)(delayed(fit)(X, y, s) for s in range(10))
    single = [fit(X, y, 42)]
    Xt = np.array([e[1] for e in todays]); yt = np.array([e[2] for e in todays], float)
    for name, models in (("ensemble", ens), ("single", single)):
        p = predict(models, Xt)
        for pi, yi in zip(p, yt):
            b = np.digitize(pi, bins) - 1
            a = agg[name][b]; a[0] += 1; a[1] += pi; a[2] += yi
    # cards that model would call due (<90%) at that evening's typical time
    cards = [w for w, d in vocab.items() if not d.get("retired")]
    st_rbc = {}
    for w in cards:
        rv = []
        for r in vocab[w].get("reviews", []):
            try: rv.append((F.parse_local(r["date"]), bool(r.get("passed"))))
            except Exception: pass
        st_rbc[w] = sorted(rv)
    st = F.card_state(st_rbc, cards, day, day)
    n = st["nc"] + st["nw"]; t = 20.7 / 24
    m = n > 0
    Xc = np.column_stack([np.log1p(np.maximum(t - st["last"], 0.01)), st["nc"], st["nw"], st["nc"] / np.maximum(n, 1),
                          np.log1p(n), np.log1p(np.maximum(t - st["first"], 0.01))])[m]
    due_e = int((predict(ens, Xc) < 0.9).sum()); due_s = int((predict(single, Xc) < 0.9).sum())
    all_reviews_today = sum(1 for e in events if day <= e[0] < end)
    daily.append((day.date(), all_reviews_today, due_e, due_s))
    print(f"{day.date()}: reviews {all_reviews_today:>3} | due by ensemble {due_e:>3} | due by single {due_s:>3}", flush=True)

print("\nCalibration on first review of the day per card (out-of-time):")
print(f"{'pred P bin':>12} | {'ensemble: n  mean P  actual':>30} | {'single: n  mean P  actual':>28}")
for b in range(len(bins) - 1):
    lab = f"{bins[b]:.2f}-{min(bins[b+1],1):.2f}"
    cells = []
    for name in ("ensemble", "single"):
        nn, sp, ps = agg[name][b]
        cells.append(f"{nn:>5} {sp/nn:>7.3f} {ps/nn:>7.3f}" if nn else f"{0:>5} {'-':>7} {'-':>7}")
    print(f"{lab:>12} | {cells[0]:>30} | {cells[1]:>28}")
for name in ("ensemble", "single"):
    tot = [sum(a[i] for a in agg[name].values()) for i in range(3)]
    print(f"{name}: overall mean predicted {tot[1]/tot[0]:.3f} vs actual pass rate {tot[2]/tot[0]:.3f} (n={tot[0]})")

before = [r for d, r, *_ in daily if d < datetime(2026, 10, 12).date()]
after = [r for d, r, *_ in daily if d >= datetime(2026, 10, 12).date()]
print(f"\nAvg reviews/day: before Oct 12 {np.mean(before):.1f} ({len(before)} days), from Oct 12 {np.mean(after):.1f} ({len(after)} days)")
