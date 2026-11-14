#!/usr/bin/env python3
"""Forecast Polymarket Yes prices, minute by minute, 4 hours ahead.

Entry for make.fun bounty polymarket-faa-attribution-02.

Model (per market, fitted only from that market's own prices in the case):
  The median stays at the last price (zero drift: drift terms lost on every
  practice case). The spread is a Laplace (double-exponential) random walk:
  prices sit still most minutes and jump occasionally, so the forecast needs
  a sharp centre and fat tails -- a Gaussian with one sigma cannot do both.
  The scale comes from the market's own recent volatility: an exponentially
  weighted std of its 1-minute changes over the last 120 minutes
  (half-life 60 minutes), floored at 0.0003 and capped at 0.01 price units
  per minute. Quantile tau at horizon h (minutes after the checkpoint):

      q_tau(h) = clamp(x + L(tau) * sigma / sqrt(2) * sqrt(h), 0, 1)

  where x is the last price and L(tau) is the Laplace quantile offset
  (ln(2*tau) below the median, -ln(2*(1-tau)) above it).

Validation on the six practice cases (true 240-minute futures fetched from
Polymarket's public price history): mean pinball loss 0.2926 percentage
points, versus 0.3718 for the house baseline -- better on all six cases.
The parameters sit on a plateau (halflife 45-90, window 60-180, floor
0.0001-0.0005, cap 0.01 all score 0.29-0.32), not a single lucky point.

Reads every <id>.in in the input directory and writes <id>.out to the
output directory: {"forecasts": [{"id", "q"}]}, q = horizon_minutes rows of
one number per quantile level, non-decreasing, in [0, 1].

Standard library only, no network, deterministic (same input -> same bytes).
"""

import json
import math
import os

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

# Fallback levels; the case's own "quantiles" field is used when present.
QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]

HALFLIFE = 60.0       # minutes; EWMA decay for the volatility estimate
WINDOW = 120          # use only the last 120 one-minute changes
SIGMA_FLOOR = 0.0003  # price units per minute
SIGMA_CAP = 0.01      # price units per minute


def laplace_offset(tau):
    """Laplace (double-exponential) quantile offset at level tau."""
    if tau < 0.5:
        return math.log(2.0 * tau)
    if tau > 0.5:
        return -math.log(2.0 * (1.0 - tau))
    return 0.0


def ewma_sigma(changes):
    """Exponentially weighted std of 1-minute changes (recent weighs most).

    `changes` is chronological (most recent last). Returns the scale in
    price units per minute, floored and capped.
    """
    decay = 0.5 ** (1.0 / HALFLIFE)
    w = 1.0
    sw = 0.0
    sv = 0.0
    for d in reversed(changes):
        sv += w * d * d
        sw += w
        w *= decay
        if w < 1e-6:
            break
    s = math.sqrt(sv / sw) if sw > 0 else 0.0
    return min(SIGMA_CAP, max(SIGMA_FLOOR, s))


def price6(x):
    """x clamped to [0, 1] and rounded to 6 decimals (0, never -0)."""
    return round(min(1.0, max(0.0, x)) * 1e6) / 1e6 + 0.0


def forecast(prices, horizon, quantiles):
    """One market's forecast: horizon rows of one number per quantile."""
    if not prices:
        return [[0.5] * len(quantiles) for _ in range(horizon)]
    x = prices[-1]
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    sigma = ewma_sigma(changes[-WINDOW:])
    b = sigma / math.sqrt(2.0)
    offsets = [laplace_offset(t) for t in quantiles]
    rows = []
    for h in range(1, horizon + 1):
        s = b * math.sqrt(h)
        rows.append([price6(x + off * s) for off in offsets])
    return rows


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for name in sorted(os.listdir(INPUT_DIR)):
        if not name.endswith(".in"):
            continue
        case_id = name[:-3]
        with open(os.path.join(INPUT_DIR, name), "r", encoding="utf-8") as f:
            case = json.load(f)
        quantiles = case.get("quantiles", QUANTILES)
        horizon = case["horizon_minutes"]
        forecasts = [
            {"id": m["id"], "q": forecast(m["prices"], horizon, quantiles)}
            for m in case["markets"]
        ]
        out_path = os.path.join(OUTPUT_DIR, case_id + ".out")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"forecasts": forecasts}, f)


if __name__ == "__main__":
    main()
