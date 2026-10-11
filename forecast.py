"""
Daily review-time forecast.

Runs after retrain.py. Simulates the next HORIZON_DAYS of reviews under the
clear-everything-below-90% routine, using the freshly trained recall model,
and writes forecast.json for stats.html to plot.

Two simulations, combined linearly on the stats page:
  * base     — the current deck with no new cards added
  * new card — a single card first reviewed tomorrow (i.e. marked reviewed in
               Sheets today). Cards are scheduled independently, so N new
               cards add N times this card's expected load.

Minutes come from your own history: seconds per first review and per regular
review are fit against the daily in-app timer, and a one-step backtest
calibrates how many reviews you actually do versus what the model predicts
(covers the every-5th random card, fail re-reviews, etc.).

Usage:
    python forecast.py                 # fetch live data
    python forecast.py --srs srs.json  # use a saved SRS export (sheets fetched live)
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

OUTPUT_DIR = Path(__file__).parent
MODEL_PATH = OUTPUT_DIR / "vocab_model.json"
OUTPUT_PATH = OUTPUT_DIR / "forecast.json"

TZ = ZoneInfo("America/New_York")
SPREADSHEET_ID = "14oqOzF2MXMDvhp8XbZo1fa3yFHTW3anVgfFrHvK4tNc"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:json&sheet={{}}"
TIMER_SYNC_KEY = "__timer_history__"

THRESHOLD = 0.9            # you review until every card is at or above this
HORIZON_DAYS = 31          # today + 30
BASE_RUNS = 200
NEW_CARD_RUNS = 4000
MAX_ROUNDS = 8             # re-reviews of one card in one session (fail → retry)
RETRY_GAP_DAYS = 3 / 1440  # a failed card returns after the 2-min holdout
FIT_WINDOW_DAYS = 60       # history used for timing fit
BACKTEST_DAYS = 28
DEFAULT_BUDGET_MIN = 5.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def parse_local(s: str) -> datetime:
    """Review timestamps are local (ET) without offset; tolerate ISO with offset."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(TZ).replace(tzinfo=None)
    return dt


def parse_mdy(s: str) -> str:
    m = re.match(r"^Date\((\d+),(\d+),(\d+)", s or "")
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo) + 1:02d}-{int(d):02d}"
    parts = (s or "").split("/")
    if len(parts) != 3:
        return ""
    return f"{parts[2]}-{int(parts[0]):02d}-{int(parts[1]):02d}"


def fetch_sheet(name: str) -> list[list[str]]:
    import requests
    resp = requests.get(SHEET_URL.format(name) + ("&headers=1" if name == "Sheet1" else ""), timeout=60)
    resp.raise_for_status()
    m = re.search(r"google\.visualization\.Query\.setResponse\(({.*})\)", resp.text, re.S)
    if not m:
        raise ValueError("unexpected gviz response")
    table = json.loads(m.group(1))["table"]
    return [[("" if c is None or c.get("v") is None else str(c["v"])) for c in row["c"]]
            for row in table["rows"]]


def load_deck() -> dict[str, str]:
    """Cards in the app: spanish -> date marked reviewed in Sheets (mirrors index.html)."""
    s1, s2 = fetch_sheet("Sheet1"), fetch_sheet("Sheet2")
    s2map = {}
    for row in s2:
        key = (row[0] if row else "").split("\n")[0].strip().lower()
        if key:
            s2map[key] = row
    deck = {}
    for row in s1:
        row = row + [""] * (7 - len(row))
        spanish, english = row[1].strip(), row[2].strip()
        sense, trans_ctx = row[5].strip(), row[6].strip()
        if not spanish:
            continue
        key = f"{spanish}: {english} ({sense})" if sense else f"{spanish}: {english}"
        if trans_ctx:
            key += f" [{trans_ctx}]"
        s2row = s2map.get(key.lower(), [])
        s2row = s2row + [""] * (4 - len(s2row))
        if s2row[2].upper() != "TRUE":
            continue
        date_rev = parse_mdy(s2row[3])
        if spanish not in deck or date_rev < deck[spanish]:
            deck[spanish] = date_rev
    return deck


# ---------------------------------------------------------------------------
# Recall model (same forward pass as index.html)
# ---------------------------------------------------------------------------

def nn_forward(X: np.ndarray, model: dict) -> np.ndarray:
    a = (X - np.array(model["scaler_mean"])) / np.array(model["scaler_std"])
    n_layers = len(model["weights"])
    for i, (W, b) in enumerate(zip(model["weights"], model["biases"])):
        z = a @ np.array(W) + np.array(b)
        a = np.maximum(z, 0) if i < n_layers - 1 else 1 / (1 + np.exp(-np.clip(z, -20, 20)))
    return a[:, 0]


def p_recall(model, t, last, first, nc, nw) -> np.ndarray:
    """P(recall) at time t (days) for flat arrays of card state; 0 for never-reviewed."""
    n = nc + nw
    out = np.zeros(n.shape)
    seen = n > 0
    if seen.any():
        tt = t[seen] if np.ndim(t) else t
        ns = n[seen]
        X = np.column_stack([
            np.log1p(np.maximum(tt - last[seen], 0.01)),
            nc[seen],
            nw[seen],
            nc[seen] / ns,
            np.log1p(ns),
            np.log1p(np.maximum(tt - first[seen], 0.01)),
        ])
        out[seen] = nn_forward(X, model)
    return out


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def simulate(model, state, avail_day, horizon, session_hour, p_first, runs, rng):
    """
    Monte Carlo of the daily clear-below-threshold routine.

    state: dict of per-card arrays (last, first, nc, nw), times in days from
           the reference midnight. avail_day: first day each card can be shown.
    Returns (first_counts, other_counts), each shape (runs, horizon).
    """
    C = len(avail_day)
    last = np.tile(state["last"], (runs, 1)).ravel()
    first = np.tile(state["first"], (runs, 1)).ravel()
    nc = np.tile(state["nc"], (runs, 1)).ravel().astype(float)
    nw = np.tile(state["nw"], (runs, 1)).ravel().astype(float)
    avail = np.tile(avail_day, runs)
    run_of = np.repeat(np.arange(runs), C)

    first_counts = np.zeros((runs, horizon))
    other_counts = np.zeros((runs, horizon))

    for d in range(horizon):
        t0 = d + session_hour / 24
        idx = np.nonzero(avail <= d)[0]
        p = p_recall(model, t0, last[idx], first[idx], nc[idx], nw[idx])
        n = nc[idx] + nw[idx]
        keep = (n == 0) | (p < THRESHOLD)
        idx, p = idx[keep], p[keep]
        t = t0
        for _ in range(MAX_ROUNDS):
            if not len(idx):
                break
            is_new = (nc[idx] + nw[idx]) == 0
            pp = np.where(is_new, p_first, p)
            passed = rng.random(len(idx)) < pp
            np.add.at(first_counts[:, d], run_of[idx[is_new]], 1)
            np.add.at(other_counts[:, d], run_of[idx[~is_new]], 1)
            nc[idx] += passed
            nw[idx] += ~passed
            first[idx] = np.where(is_new, t, first[idx])
            last[idx] = t
            t += RETRY_GAP_DAYS
            p = p_recall(model, t, last[idx], first[idx], nc[idx], nw[idx])
            still = p < THRESHOLD
            idx, p = idx[still], p[still]

    return first_counts, other_counts


def card_state(reviews_by_card, cards, cutoff: datetime, ref: datetime):
    """History strictly before cutoff → state arrays (times relative to ref)."""
    last, first, nc, nw = [], [], [], []
    for c in cards:
        prev = [r for r in reviews_by_card.get(c, []) if r[0] < cutoff]
        if prev:
            first.append((prev[0][0] - ref).total_seconds() / 86400)
            last.append((prev[-1][0] - ref).total_seconds() / 86400)
            nc.append(sum(1 for r in prev if r[1]))
            nw.append(sum(1 for r in prev if not r[1]))
        else:
            first.append(0.0); last.append(0.0); nc.append(0); nw.append(0)
    return {"last": np.array(last), "first": np.array(first),
            "nc": np.array(nc), "nw": np.array(nw)}


# ---------------------------------------------------------------------------
# Fitting from history
# ---------------------------------------------------------------------------

def daily_counts(reviews_by_card):
    first, other = defaultdict(int), defaultdict(int)
    for revs in reviews_by_card.values():
        for i, (dt, _) in enumerate(revs):
            (first if i == 0 else other)[dt.date().isoformat()] += 1
    return first, other


def fit_seconds(timer, first, other, today):
    """Least squares secs/day = a*first + b*other over recent days; falls back to one rate."""
    start = (today - timedelta(days=FIT_WINDOW_DAYS)).isoformat()
    days = [d for d, s in timer.items()
            if start <= d < today.isoformat() and s > 0 and (first[d] + other[d]) > 0]
    if not days:
        raise RuntimeError("no overlapping timer/review history to fit timing")
    X = np.array([[first[d], other[d]] for d in days], dtype=float)
    y = np.array([timer[d] for d in days], dtype=float)
    single = y.sum() / X.sum()
    coef = None
    if (X[:, 0] > 0).sum() >= 5:
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    if coef is None or coef.min() <= 0:
        coef = np.array([single, single])
    resid = y - X @ coef
    return float(coef[0]), float(coef[1]), {
        "days_used": len(days),
        "single_rate_sec": round(float(single), 2),
        "rmse_min": round(float(np.sqrt(np.mean(resid ** 2)) / 60), 2),
    }


def backtest(model, reviews_by_card, today_mid, session_hour, p_first, rng):
    """
    Replay the last BACKTEST_DAYS: start from the deck as it was, add cards on
    the days they were actually first reviewed, simulate daily clearing, and
    compare repeat-review totals per week with what you actually did. Totals
    are robust to skipped or partial days, which a day-by-day check is not.
    """
    start = today_mid - timedelta(days=BACKTEST_DAYS)
    cards, avail = [], []
    for c, revs in reviews_by_card.items():
        if not revs or revs[0][0] >= today_mid:
            continue
        cards.append(c)
        avail.append(0 if revs[0][0] < start else (revs[0][0] - start).days)
    if not cards:
        return 1.0, {"days": 0}
    state = card_state(reviews_by_card, cards, start, start)
    _, oc = simulate(model, state, np.array(avail), BACKTEST_DAYS, session_hour, p_first, 40, rng)
    sim = oc.mean(0)

    actual = np.zeros(BACKTEST_DAYS)
    studied = set()
    for c in cards:
        for i, (dt, _) in enumerate(reviews_by_card[c]):
            k = (dt - start).days
            if 0 <= k < BACKTEST_DAYS:
                studied.add(k)
                if i > 0:
                    actual[k] += 1
    factor = float(actual.sum() / sim.sum()) if sim.sum() > 0 else 1.0
    weeks = [{"week_start": (start + timedelta(days=w)).date().isoformat(),
              "actual": int(actual[w:w + 7].sum()),
              "simulated": round(float(factor * sim[w:w + 7].sum()), 1)}
             for w in range(0, BACKTEST_DAYS, 7)]
    return factor, {
        "days": BACKTEST_DAYS,
        "days_studied": len(studied),
        "actual_repeat_reviews": int(actual.sum()),
        "simulated_repeat_reviews_raw": round(float(sim.sum()), 1),
        "by_week": weeks,
    }


def decay_check(model, reviews_by_card, today_mid):
    """Does predicted recall fall with time for current cards? Flags extrapolation trouble."""
    cards = [c for c, r in reviews_by_card.items() if r]
    st = card_state(reviews_by_card, cards, today_mid, today_mid)
    grid = [0, 1, 3, 7, 14, 30, 60]
    P = np.array([p_recall(model, float(g), st["last"], st["first"], st["nc"], st["nw"]) for g in grid])
    rising = float(np.mean(np.any(np.diff(P, axis=0) > 0.02, axis=0)))
    never_due = float(np.mean(P[-1] >= THRESHOLD))
    return {
        "frac_cards_recall_rises_with_time": round(rising, 3),
        "frac_cards_above_threshold_after_60d": round(never_due, 3),
        "median_p_by_days_ahead": {str(g): round(float(np.median(P[i])), 3) for i, g in enumerate(grid)},
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--srs", help="path to a saved SRS JSON export")
    ap.add_argument("--now", help="override current local time (ISO), for testing")
    ap.add_argument("--no-sheets", action="store_true", help="skip Sheets; deck = cards with reviews")
    args = ap.parse_args()

    print("=" * 50)
    print("Review-time forecast")
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(TZ).replace(tzinfo=None)
    today = now.date()
    today_mid = datetime(today.year, today.month, today.day)
    print(f"Local time: {now:%Y-%m-%d %H:%M}")
    print("=" * 50)

    if args.srs:
        srs = json.loads(Path(args.srs).read_text())
    else:
        from retrain import APPS_SCRIPT_URL, fetch_srs
        srs = fetch_srs(APPS_SCRIPT_URL)
    model = json.loads(MODEL_PATH.read_text())

    # Timer history: sum all devices
    timer = defaultdict(float)
    for entry in (srs.get(TIMER_SYNC_KEY) or {}).get("reviews", []):
        for d, secs in (entry.get("data") or {}).items():
            timer[d] += secs

    reviews_by_card = {}
    for word, data in srs.items():
        if word.startswith("__") or word.startswith("frame:") or data.get("retired"):
            continue
        revs = []
        for r in data.get("reviews", []):
            try:
                revs.append((parse_local(r["date"]), bool(r.get("passed"))))
            except (KeyError, ValueError, TypeError):
                continue
        reviews_by_card[word] = sorted(revs)

    deck = None
    if not args.no_sheets:
        try:
            deck = load_deck()
            print(f"Deck from Sheets: {len(deck)} cards")
        except Exception as e:
            print(f"WARNING: could not load Sheets ({e}); using cards with review history only")

    # Cards in the simulation and the first day each can appear.
    # Marked reviewed in Sheets today → shows up in the app tomorrow (day 1).
    today_s = today.isoformat()
    cards, avail = [], []
    sheets_today = 0
    if deck is not None:
        for c, date_rev in deck.items():
            srs_entry = srs.get(c) or {}
            if srs_entry.get("retired"):
                continue
            has_prior = any(r[0] < today_mid for r in reviews_by_card.get(c, []))
            if date_rev == today_s and not has_prior:
                sheets_today += 1
                cards.append(c); avail.append(1)
            else:
                cards.append(c); avail.append(0)
    else:
        cards = [c for c, r in reviews_by_card.items() if r and r[0][0] < today_mid]
        avail = [0] * len(cards)
    avail = np.array(avail, int)
    print(f"Active cards: {len(cards)}  (marked in Sheets today: {sheets_today})")

    # Empirical first-review pass rate (recent)
    recent = today_mid - timedelta(days=90)
    firsts = [r[0][1] for r in reviews_by_card.values() if r and recent <= r[0][0] < today_mid]
    p_first = float(np.mean(firsts)) if len(firsts) >= 10 else 0.6
    print(f"First-review pass rate: {p_first:.2f} (n={len(firsts)})")

    # Typical session start hour (median of each day's first review, last 30 days)
    starts = defaultdict(lambda: None)
    for revs in reviews_by_card.values():
        for dt, _ in revs:
            if today_mid - timedelta(days=30) <= dt < today_mid:
                k = dt.date()
                if starts[k] is None or dt < starts[k]:
                    starts[k] = dt
    hours = [(v - datetime(v.year, v.month, v.day)).total_seconds() / 3600 for v in starts.values() if v]
    session_hour = float(np.median(hours)) if hours else 12.0
    print(f"Typical session start: {session_hour:.1f}h")

    first_daily, other_daily = daily_counts(
        {c: [r for r in revs if r[0] < today_mid] for c, revs in reviews_by_card.items()})
    sec_first, sec_review, fit_info = fit_seconds(timer, first_daily, other_daily, today)
    print(f"Seconds per first review: {sec_first:.1f}, per review: {sec_review:.1f}  {fit_info}")

    rng = np.random.default_rng(42)
    factor, bt = backtest(model, reviews_by_card, today_mid, session_hour, p_first, rng)
    print(f"Backtest: studied {bt.get('days_studied')}/{bt.get('days')} days, review factor {factor:.2f}, "
          f"by week (actual vs calibrated sim): {bt.get('by_week')}")

    decay = decay_check(model, reviews_by_card, today_mid)
    print(f"Decay check: {decay}")

    def minutes(fc, oc):
        return (fc * sec_first + oc * sec_review * factor) / 60

    # Base: current deck, no new cards
    state = card_state(reviews_by_card, cards, today_mid, today_mid)
    fc, oc = simulate(model, state, avail, HORIZON_DAYS, session_hour, p_first, BASE_RUNS, rng)
    base_min = minutes(fc, oc)

    # What makes up today's load: first reviews of words already in the app,
    # and repeat reviews (which pile up when days are skipped)
    last_study = max((r[0] for revs in reviews_by_card.values() for r in revs if r[0] < today_mid), default=None)
    today_breakdown = {
        "first_reviews": round(float(fc[:, 0].mean()), 1),
        "repeat_reviews": round(float(oc[:, 0].mean() * factor), 1),
        "last_study_date": last_study.date().isoformat() if last_study else None,
    }

    # One new card, first shown tomorrow
    empty = {"last": np.zeros(1), "first": np.zeros(1), "nc": np.zeros(1), "nw": np.zeros(1)}
    nfc, noc = simulate(model, empty, np.array([1]), HORIZON_DAYS, session_hour, p_first, NEW_CARD_RUNS, rng)
    new_min = minutes(nfc, noc)

    days = [(today + timedelta(days=i)).isoformat() for i in range(HORIZON_DAYS)]
    r = lambda a: [round(float(x), 4) for x in a]
    out = {
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "today": today_s,
        "days": days,
        "default_budget_min": DEFAULT_BUDGET_MIN,
        "threshold": THRESHOLD,
        "sheets_today_at_run": sheets_today,
        "today_breakdown": today_breakdown,
        "base": {
            "mean": r(base_min.mean(0)),
            "var": r(base_min.var(0)),
            "p10": r(np.percentile(base_min, 10, axis=0)),
            "p90": r(np.percentile(base_min, 90, axis=0)),
            "reviews_mean": r((fc + oc * factor).mean(0)),
        },
        "per_new_card": {
            "mean": r(new_min.mean(0)),
            "var": r(new_min.var(0)),
            "reviews_mean": r((nfc + noc * factor).mean(0)),
        },
        "params": {
            "sec_first_review": round(sec_first, 2),
            "sec_review": round(sec_review, 2),
            "review_factor": round(factor, 3),
            "p_first_pass": round(p_first, 3),
            "session_hour": round(session_hour, 2),
            "n_cards": len(cards),
            "base_runs": BASE_RUNS,
            "new_card_runs": NEW_CARD_RUNS,
            "timing_fit": fit_info,
        },
        "backtest": bt,
        "decay_check": decay,
    }
    OUTPUT_PATH.write_text(json.dumps(out, indent=1))

    week_base = sum(out["base"]["mean"][1:8])
    week_new = sum(out["per_new_card"]["mean"][1:8])
    n_max = max(0, int((7 * DEFAULT_BUDGET_MIN - week_base) // week_new)) if week_new > 0 else 0
    print(f"\nToday: {out['base']['mean'][0]:.1f} min expected  {today_breakdown}")
    print(f"Next 7 days avg, no new cards: {week_base / 7:.2f} min")
    print(f"Each new card adds {week_new / 7 * 60:.1f} s/day over the week")
    print(f"→ can add {n_max} cards today at {DEFAULT_BUDGET_MIN:g} min/day")
    print(f"wrote {OUTPUT_PATH.name}")


if __name__ == "__main__":
    main()
