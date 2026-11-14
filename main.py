#!/usr/bin/env python3
"""Forecast Polymarket FAA outage-attribution prices, 4 hours ahead.

Model (v2): Laplace quantiles with an EWMA volatility estimate, a
sub-diffusive spread growth, and a mean-reversion of the median toward the
market's fair share of the family total.

- Volatility: exponentially weighted sigma of 1-minute price changes
  (half-life 60 min, last 120 changes), floored at 0.0005 and capped at 0.01
  per sqrt-minute.  A floor matters because many markets sit flat for hours
  (sigma 0) yet still jump.
- Spread: Laplace offsets times sigma/sqrt(2) times h**0.4 (NOT sqrt(h)).
  Prediction-market prices are mean-reverting, so the h-minute spread grows
  slower than a random walk (sub-diffusive).  On the 6 practice cases this
  beats the sqrt(h) spread by ~0.013pp of pinball loss.
- Median: the last price, pulled toward the market's fair share
  x / sum(all markets' last prices) with time constant 120 minutes:
  med(h) = x + (x/S - x) * (1 - exp(-h/120)).  The family is winner-take-all
  (the Yes prices sum to ~1.0-1.1, the excess is overround), so a market's
  price reverts toward its share of the total.  On the practice cases this
  adds ~0.010pp on top of the sub-diffusive spread.
- Zero drift otherwise: no momentum extrapolation (tested, it hurts) and no
  contrarian drift (tested, it hurts despite a negative correlation, because
  the signal is outlier-driven and horizon-specific).

Validated on the 6 practice cases (true 240-min futures fetched from the
public Polymarket CLOB API): pinball loss 0.2681pp vs 0.2926pp for the v1
model (Laplace + EWMA sigma + sqrt(h), no mean-reversion) and 0.3718pp for
the house baseline.  The improvement holds on every case in a
leave-one-case-out check (v2 beats v1 on all 6 held-out cases), so it is a
robust improvement, not a single-case spike.

Input:  one case file per market family, canonical JSON
  {"schema":"makefun/polymarket-case/v1","case","at","horizon_minutes",
   "quantiles","event","markets":[{"id","name","slug","yes_token","start",
   "prices"}]}  -- prices = one Yes price per minute from start to at.
Output: one file per case, {"forecasts":[{"id","q"}]} where q has exactly
  horizon_minutes rows of 5 non-decreasing numbers in [0,1].
Env vars (outside the grader container): MAKEFUN_INPUT_DIR and
MAKEFUN_OUTPUT_DIR stand in for /input and /output.
"""

import json
import math
import os
import sys

# Laplace quantile offsets for tau = 0.05, 0.25, 0.5, 0.75, 0.95.
# For a Laplace distribution with scale b, q(tau) = median + b * L(tau),
# where L(tau) = ln(2*tau) for tau < 0.5 and -ln(2*(1-tau)) for tau > 0.5.
LAPLACE_OFFSETS = [
    math.log(2 * 0.05),
    math.log(2 * 0.25),
    0.0,
    -math.log(2 * 0.25),
    -math.log(2 * 0.05),
]

# Model constants (validated on the 6 practice cases; see README).
HALFLIFE_MIN = 60.0      # EWMA half-life for the 1-minute changes
WINDOW = 120             # number of recent changes used for sigma
SIGMA_FLOOR = 0.0005     # per sqrt-minute; flat markets still jump
SIGMA_CAP = 0.01         # per sqrt-minute
SPREAD_ALPHA = 0.4       # spread grows as h**0.4 (sub-diffusive)
MR_TAU_MIN = 120.0       # mean-reversion time constant toward fair share


def clamp01(x):
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def price6(x):
    """Round to 6 decimals, clamped to [0, 1]."""
    return round(clamp01(x) * 1e6) / 1e6


def ewma_sigma(changes):
    """EWMA standard deviation of the given 1-minute changes."""
    alpha = 0.5 ** (1.0 / HALFLIFE_MIN)
    w = 1.0
    sw = 0.0
    sv = 0.0
    for d in reversed(changes):
        sv += w * d * d
        sw += w
        w *= alpha
        if w < 1e-6:
            break
    if sw <= 0.0:
        return SIGMA_FLOOR
    s = math.sqrt(sv / sw)
    if s < SIGMA_FLOOR:
        return SIGMA_FLOOR
    if s > SIGMA_CAP:
        return SIGMA_CAP
    return s


def forecast_case(case):
    """Return {market_id: [[q05,q25,q50,q75,q95] x horizon]} for one case."""
    horizon = int(case["horizon_minutes"])
    quantiles = case["quantiles"]
    markets = case["markets"]
    if not markets:
        return {}
    total = sum(m["prices"][-1] for m in markets if m["prices"])
    out = {}
    for m in markets:
        prices = m["prices"]
        if not prices:
            out[m["id"]] = [[0.5] * len(quantiles) for _ in range(horizon)]
            continue
        x = prices[-1]
        changes = [prices[j] - prices[j - 1] for j in range(1, len(prices))]
        sigma = ewma_sigma(changes[-WINDOW:])
        b = sigma / math.sqrt(2.0)
        fair = x / total if total > 0 else x
        rows = []
        for h in range(1, horizon + 1):
            med = x + (fair - x) * (1.0 - math.exp(-h / MR_TAU_MIN))
            spread = b * (h ** SPREAD_ALPHA)
            rows.append([price6(med + off * spread) for off in LAPLACE_OFFSETS])
        out[m["id"]] = rows
    return out


def main():
    in_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)
    names = sorted(n for n in os.listdir(in_dir) if n.endswith(".in"))
    if not names:
        print("no .in files in %s" % in_dir, file=sys.stderr)
        return 1
    for name in names:
        with open(os.path.join(in_dir, name)) as f:
            case = json.load(f)
        forecasts = forecast_case(case)
        out_case = {"forecasts": [{"id": mid, "q": rows}
                                  for mid, rows in forecasts.items()]}
        out_name = name[:-3] + ".out"
        with open(os.path.join(out_dir, out_name), "w") as f:
            json.dump(out_case, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
