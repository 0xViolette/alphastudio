"""Statistical helpers for tearsheets."""

from math import exp, sqrt
from statistics import NormalDist

NORMAL = NormalDist()


def moments(xs):
    """(mean, sample stdev, skewness, non-excess kurtosis)."""
    n = len(xs)
    if n < 2:
        return (sum(xs) / n if n else 0.0), 0.0, 0.0, 3.0
    mean = sum(xs) / n
    dev = [x - mean for x in xs]
    m2 = sum(d * d for d in dev) / n
    m3 = sum(d**3 for d in dev) / n
    m4 = sum(d**4 for d in dev) / n
    sd = sqrt(sum(d * d for d in dev) / (n - 1))
    skew = m3 / m2**1.5 if m2 else 0.0
    kurt = m4 / m2**2 if m2 else 3.0
    return mean, sd, skew, kurt


def quantile(xs, q):
    """Linear-interpolation quantile of a sorted-able sample."""
    ys = sorted(xs)
    if not ys:
        return float("nan")
    pos = q * (len(ys) - 1)
    lo, hi = int(pos), min(int(pos) + 1, len(ys) - 1)
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def downside_deviation(rs, target=0.0):
    """Target downside deviation (Sortino): sqrt(mean of min(r - T, 0)^2)."""
    return sqrt(sum(min(r - target, 0.0) ** 2 for r in rs) / len(rs))


def cvar(rs, q):
    """Historical expected shortfall: mean of returns at or below quantile q."""
    cut = quantile(rs, q)
    tail = [r for r in rs if r <= cut]
    return sum(tail) / len(tail) if tail else cut


def sharpe_var(n, sr, g3, g4):
    """Variance of the estimated Sharpe ratio, Merton's result as used by
    Bailey & Lopez de Prado (2012): (1 - g3*SR + (g4-1)/4*SR^2) / (T-1)."""
    return (1 - g3 * sr + (g4 - 1) / 4 * sr**2) / (n - 1)


def psr(n, sr, g3, g4, sr_star=0.0):
    """Probabilistic Sharpe ratio: P(true SR > SR*), non-normality corrected."""
    var = 1 - g3 * sr + (g4 - 1) / 4 * sr**2
    return NORMAL.cdf((sr - sr_star) * sqrt(n - 1) / sqrt(var))


def min_track_record(sr, sr_star, g3, g4, alpha=0.05):
    """MinTRL: periods needed for SR to beat SR* with 1 - alpha confidence."""
    if sr <= sr_star:
        return float("inf")
    var = 1 - g3 * sr + (g4 - 1) / 4 * sr**2
    return 1 + var * (NORMAL.inv_cdf(1 - alpha) / (sr - sr_star)) ** 2


def expected_max_sharpe(trials, var_sharpe):
    """E[max SR] over `trials` independent trials with the same variance."""
    if trials < 2:
        return 0.0
    euler = 0.5772156649
    g = (1 - euler) * NORMAL.inv_cdf(1 - 1 / trials)
    g += euler * NORMAL.inv_cdf(1 - 1 / (trials * exp(1)))
    return sqrt(var_sharpe) * g


def ulcer_index(dd_pct):
    """Martin (1987): RMS of the percentage drawdown series."""
    return sqrt(sum(d * d for d in dd_pct) / len(dd_pct))


def drawdowns(equity, initial=None):
    """Return (dd_abs, peak_abs, episodes) for a list of equity values."""
    run_peak = float("-inf") if initial is None else initial
    dd, peaks = [], []
    for v in equity:
        run_peak = max(run_peak, v)
        peaks.append(run_peak)
        dd.append(v - run_peak)

    episodes, cur = [], None
    for i, (v, p) in enumerate(zip(equity, peaks)):
        at_peak = abs(v - p) < 1e-9
        if at_peak:
            if cur is not None:
                cur["recover"] = i
                episodes.append(cur)
                cur = None
        else:
            if cur is None:
                cur = {"peak_i": max(i - 1, 0), "trough_i": i, "recover": None}
            if dd[i] < dd[cur["trough_i"]]:
                cur["trough_i"] = i
    if cur is not None:
        episodes.append(cur)
    for ep in episodes:
        ep["depth"] = dd[ep["trough_i"]]
    return dd, peaks, episodes


def math_prod(xs):
    p = 1.0
    for x in xs:
        p *= x
    return p
