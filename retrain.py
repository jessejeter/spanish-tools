"""
Nightly SRS model retraining.

Fetches review history from Google Sheets via Apps Script, trains a small
neural net on the vocab review history, and writes JSON weight files that the
browser loads on startup.

Dependencies:
    pip install requests numpy scikit-learn
"""

import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import numpy as np
    from joblib import Parallel, delayed
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
except ImportError:
    print("ERROR: Missing dependencies. Run: pip install numpy scikit-learn requests")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERROR: Missing requests. Run: pip install requests")
    sys.exit(1)


APPS_SCRIPT_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbwbrp0yQwWy4TXLeG0h2NAhW3l0gS4ECGzHhkHJ2XxO2joA7-ShSkTR0Ax2tXbclNW7/exec"
)

OUTPUT_DIR = Path(__file__).parent

MIN_SAMPLES = 30  # skip training if fewer rows than this

# Averaging several nets trained from different random starts keeps nightly
# retrains from shifting predictions much: a single net lands in a slightly
# different place each night, and with hundreds of cards sitting near 90%,
# small shifts flip many cards between due and not due.
ENSEMBLE_SIZE = 10


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_srs(url: str, retries: int = 3, timeout: int = 60) -> dict:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError):
            if attempt == retries:
                raise
            wait = 5 * attempt
            print(f"  fetch attempt {attempt} failed, retrying in {wait}s...")
            time.sleep(wait)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------
# For each review event (index i >= 1), we use the history *before* that
# review as features and the review outcome as the label.
#
# Features:
#   0: log1p(days_since_last_review)   — recency of last review
#   1: n_correct_so_far                — raw correct count
#   2: n_wrong_so_far                  — raw wrong count
#   3: accuracy_so_far                 — n_correct / n_total
#   4: log1p(n_reviews_so_far)         — total exposure (log-scaled)
#   5: log1p(days_since_first_review)  — how long the word has been studied
# ---------------------------------------------------------------------------

N_FEATURES = 6


def build_dataset(srs: dict) -> list[tuple[list[float], int]]:
    rows = []
    for word, data in srs.items():
        if data.get("retired"):
            continue
        reviews = data.get("reviews", [])
        if len(reviews) < 2:
            continue

        for i in range(1, len(reviews)):
            prev = reviews[:i]
            curr = reviews[i]

            try:
                curr_date  = datetime.fromisoformat(curr["date"])
                prev_date  = datetime.fromisoformat(prev[-1]["date"])
                first_date = datetime.fromisoformat(prev[0]["date"])
            except (KeyError, ValueError):
                continue

            dt_days          = max((curr_date - prev_date).total_seconds()  / 86400, 0.01)
            days_since_first = max((curr_date - first_date).total_seconds() / 86400, 0.01)
            n_correct = sum(1 for r in prev if r.get("passed"))
            n_wrong   = sum(1 for r in prev if not r.get("passed"))
            n_total   = len(prev)
            accuracy  = n_correct / n_total

            features = [
                math.log1p(dt_days),
                n_correct,
                n_wrong,
                accuracy,
                math.log1p(n_total),
                math.log1p(days_since_first),
            ]
            label = 1 if curr.get("passed") else 0
            rows.append((features, label))

    return rows


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def fit_member(X_scaled: np.ndarray, y: np.ndarray, seed: int) -> MLPClassifier:
    clf = MLPClassifier(
        hidden_layer_sizes=(16, 8),
        activation="relu",
        solver="adam",
        alpha=0.01,        # L2 regularization
        max_iter=1000,
        random_state=seed,
    )
    return clf.fit(X_scaled, y)


def train(rows: list, name: str) -> dict:
    X = np.array([r[0] for r in rows], dtype=float)
    y = np.array([r[1] for r in rows], dtype=float)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    clfs = Parallel(n_jobs=-1)(delayed(fit_member)(X_scaled, y, seed) for seed in range(ENSEMBLE_SIZE))

    p = np.mean([c.predict_proba(X_scaled)[:, 1] for c in clfs], axis=0)
    train_acc = float(((p >= 0.5) == y).mean())
    class_balance = y.mean()
    print(f"  {name}: {len(rows)} samples, {ENSEMBLE_SIZE} members, "
          f"train acc={train_acc:.3f}, "
          f"positive rate={class_balance:.2f}")

    # sklearn stores weights as [in x out], which matches our JS forward pass
    members = [{
        "scaler_mean":  scaler.mean_.tolist(),
        "scaler_std":   scaler.scale_.tolist(),
        "weights":      [w.tolist() for w in c.coefs_],
        "biases":       [b.tolist() for b in c.intercepts_],
    } for c in clfs]

    return {
        "trained_at":   datetime.now().isoformat(),
        "n_samples":    len(rows),
        "n_features":   N_FEATURES,
        "feature_names": [
            "log1p_days_since_last",
            "n_correct",
            "n_wrong",
            "accuracy",
            "log1p_n_reviews",
            "log1p_days_since_first",
        ],
        # Top level holds the first member so older cached pages still work;
        # current pages average everything in "ensemble".
        **members[0],
        "ensemble":     members,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 50)
    print("SRS Model Retraining")
    print(f"Running at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    print("\nFetching SRS data...")
    try:
        all_srs = fetch_srs(APPS_SCRIPT_URL)
        print(f"  fetched {len(all_srs)} entries total")
    except Exception as e:
        print(f"  ERROR fetching: {e}")
        sys.exit(1)

    vocab_srs = {k: v for k, v in all_srs.items() if not k.startswith("frame:") and not k.startswith("__")}
    print(f"  vocab: {len(vocab_srs)}")

    jobs = [
        ("vocab", vocab_srs, OUTPUT_DIR / "vocab_model.json"),
    ]

    failed = False

    for name, srs, output_path in jobs:
        print(f"\n--- {name} ---")

        rows = build_dataset(srs)
        print(f"  built {len(rows)} training rows")

        if len(rows) < MIN_SAMPLES:
            print(f"  too few samples (need {MIN_SAMPLES}), skipping")
            continue

        try:
            model = train(rows, name)
        except Exception as e:
            print(f"  ERROR training: {e}")
            failed = True
            continue

        output_path.write_text(json.dumps(model))  # compact: 10 members
        print(f"  wrote {output_path.name}")

    print("\nDone!")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
