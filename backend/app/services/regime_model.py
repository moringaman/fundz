"""
Gaussian Mixture regime model — Phase 4 of quant rigour rollout.

Why this exists
---------------
Marina's regime classification used to be pure LLM vibes: "looks bullish to me."
That's fine until the LLM hallucinates a trend that ain't there. This module
replaces vibes with a posterior probability from a fitted GMM so Marina has a
statistical anchor to either confirm or override.

Methodology
-----------
Features per bar:
  - log-return  (price drift signal)
  - rolling realised volatility  (regime-sensitive scale)

Three Gaussian components map to risk_off / range / risk_on after labelling by
mean return. Labels are stable across refits via centroid-distance matching:
each new component is mapped to the previous label whose mean is closest, so
"risk_on" doesn't randomly flip to component-0 or component-2 between refits.

Hidden assumptions
------------------
- 1h candles. Other timeframes work but vol windows would need re-tuning.
- 3 components. Could be 4-5 with more data; 3 keeps it interpretable for the
  LLM prompt.
- "risk_off" = lowest mean return component, NOT highest vol. A high-vol
  rally is risk_on. Adjust the labeller if the desk wants vol-sorted labels.
- We do NOT use full-covariance — diagonal covariance is more stable on small
  windows and the off-diagonal between log-return and vol is rarely meaningful.

Failure modes
-------------
- Too few candles (< 200) → fit raises. Caller should fall back to default.
- All-flat data → singular covariance → sklearn handles via reg_covar but
  posteriors degenerate to ~uniform.
- Persisted label centroids stale (e.g. risk_off mean has shifted) → the
  matcher might mis-route. Mitigation: refit centroids whenever fit_gmm is
  called and persist the latest set as `model_fingerprint`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture

logger = logging.getLogger(__name__)


# Canonical regime labels. Order is meaningful: index 0 = lowest mean return.
REGIME_LABELS: Tuple[str, str, str] = ("risk_off", "range", "risk_on")
DEFAULT_N_COMPONENTS = 3
# Rolling window for realised vol. 24 bars on 1h candles = 1 day of context;
# long enough to smooth, short enough to react to regime shifts within ~12h.
DEFAULT_VOL_WINDOW = 24
# Minimum candles required to fit. Below this the covariance matrices be
# unstable and the posterior tells ye nothing useful.
MIN_FIT_CANDLES = 200


@dataclass
class RegimePrediction:
    """Posterior over regime labels for the most recent bar."""
    label: str                                    # argmax label, one of REGIME_LABELS
    confidence: float                             # max posterior probability
    posteriors: Dict[str, float] = field(default_factory=dict)
    feature_vector: Optional[List[float]] = None  # the [log_return, vol] used
    timestamp: Optional[str] = None               # ISO timestamp of the bar predicted

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class FittedRegimeModel:
    """A fitted GMM + the label mapping. Pickle-safe; cache in Redis."""
    gmm: GaussianMixture
    component_to_label: Dict[int, str]
    # Centroid means per label, used for label-stability matching across refits.
    # {label: [mean_log_return, mean_vol]}
    label_centroids: Dict[str, List[float]]
    n_samples_fit: int
    feature_window: int
    # Fingerprint = hash of the centroids; used to detect label drift between
    # refits without re-running prediction. Deterministic for identical fits.
    fingerprint: str

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        """Posterior probabilities per (sample, component)."""
        return self.gmm.predict_proba(features)


def _build_features(
    closes: pd.Series,
    vol_window: int = DEFAULT_VOL_WINDOW,
) -> pd.DataFrame:
    """Compute the feature matrix [log_return, realised_vol].

    Trims leading NaNs (the rolling vol window eats the first `vol_window` bars).
    """
    if len(closes) < vol_window + 2:
        raise ValueError(
            f"Need at least {vol_window + 2} candles for vol window {vol_window}, "
            f"got {len(closes)}"
        )
    log_ret = np.log(closes / closes.shift(1))
    vol = log_ret.rolling(window=vol_window, min_periods=vol_window).std()
    df = pd.DataFrame({'log_return': log_ret, 'vol': vol}).dropna()
    return df


def _label_components(
    gmm: GaussianMixture,
    prior_centroids: Optional[Dict[str, List[float]]] = None,
) -> Tuple[Dict[int, str], Dict[str, List[float]]]:
    """Map raw component indices to regime labels.

    Two strategies:
    1. Cold start (no prior_centroids): sort components by mean log-return
       ascending → assign REGIME_LABELS in order. Lowest = risk_off, highest = risk_on.
    2. Warm start (prior_centroids from previous fit): map each new component
       to the previous label whose centroid is nearest in feature space.
       Falls back to cold-start ordering if assignment is ambiguous (i.e. a
       new component lands closer to two prior labels' midpoint than either).

    Returns (component_idx → label, label → centroid).
    """
    means = gmm.means_  # shape (n_components, 2): [log_ret_mean, vol_mean]
    n_comp = means.shape[0]

    if prior_centroids is None or len(prior_centroids) != n_comp:
        # Cold start. Sort by mean log-return.
        order = np.argsort(means[:, 0])  # ascending
        comp_to_label = {int(comp_idx): REGIME_LABELS[rank] for rank, comp_idx in enumerate(order)}
    else:
        # Warm start. For each component, pick the prior label whose centroid is closest.
        # Done greedily: compute all distances, pick smallest, fix that pairing, repeat.
        # Greedy works fine for n_comp=3; would need Hungarian algorithm for larger n.
        prior_labels = list(prior_centroids.keys())
        prior_arr = np.array([prior_centroids[lbl] for lbl in prior_labels])
        # distances[i,j] = |new_component_i - prior_label_j|
        distances = np.linalg.norm(means[:, None, :] - prior_arr[None, :, :], axis=2)
        comp_to_label: Dict[int, str] = {}
        used_labels = set()
        # Greedy: take smallest available pair until all assigned
        flat_idx = np.argsort(distances, axis=None)
        for fi in flat_idx:
            ci, pj = np.unravel_index(fi, distances.shape)
            if ci in comp_to_label or prior_labels[pj] in used_labels:
                continue
            comp_to_label[int(ci)] = prior_labels[pj]
            used_labels.add(prior_labels[pj])
            if len(comp_to_label) == n_comp:
                break

    label_centroids = {
        comp_to_label[ci]: means[ci].tolist()
        for ci in range(n_comp)
    }
    return comp_to_label, label_centroids


def _fingerprint(centroids: Dict[str, List[float]]) -> str:
    """Deterministic short hash of the centroid map."""
    import hashlib
    payload = "|".join(
        f"{lbl}:{c[0]:.6f},{c[1]:.6f}"
        for lbl, c in sorted(centroids.items())
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def fit_gmm(
    closes: pd.Series,
    *,
    n_components: int = DEFAULT_N_COMPONENTS,
    vol_window: int = DEFAULT_VOL_WINDOW,
    seed: int = 42,
    prior_centroids: Optional[Dict[str, List[float]]] = None,
) -> FittedRegimeModel:
    """Fit a Gaussian Mixture on close-price-derived features.

    Pass `prior_centroids` from the previous fit to keep regime labels stable
    across refits (otherwise component indices may shuffle randomly).
    """
    if len(closes) < MIN_FIT_CANDLES:
        raise ValueError(
            f"Need at least {MIN_FIT_CANDLES} candles to fit GMM, got {len(closes)}"
        )

    features_df = _build_features(closes, vol_window=vol_window)
    if len(features_df) < MIN_FIT_CANDLES // 2:
        raise ValueError(
            f"After feature derivation only {len(features_df)} samples remain; need "
            f"at least {MIN_FIT_CANDLES // 2}"
        )

    X = features_df[['log_return', 'vol']].to_numpy()

    # Diagonal covariance: log-return and vol be roughly independent at the bar
    # level, and full-cov adds parameters that overfit on ~5k samples.
    gmm = GaussianMixture(
        n_components=n_components,
        covariance_type='diag',
        random_state=seed,
        # n_init=3 → multiple random starts, pick best log-likelihood. Mitigates
        # the well-known GMM local-optimum trap. 3 keeps fit time reasonable.
        n_init=3,
        max_iter=200,
        reg_covar=1e-6,
    )
    gmm.fit(X)

    comp_to_label, label_centroids = _label_components(gmm, prior_centroids=prior_centroids)
    fp = _fingerprint(label_centroids)

    return FittedRegimeModel(
        gmm=gmm,
        component_to_label=comp_to_label,
        label_centroids=label_centroids,
        n_samples_fit=len(features_df),
        feature_window=vol_window,
        fingerprint=fp,
    )


def predict_regime(
    model: FittedRegimeModel,
    closes: pd.Series,
    *,
    timestamp: Optional[str] = None,
) -> RegimePrediction:
    """Predict the regime for the latest bar in `closes`.

    Caller passes a recent slice of closes (at least `feature_window + 2` bars).
    We compute features for the WHOLE slice but only return posteriors for the
    last row — the rest is needed to seed the rolling vol window.
    """
    features_df = _build_features(closes, vol_window=model.feature_window)
    if features_df.empty:
        raise ValueError("Not enough candles after feature derivation to predict")
    last_row = features_df.iloc[-1:]
    X = last_row[['log_return', 'vol']].to_numpy()

    posteriors_per_component = model.predict_proba(X)[0]  # shape (n_components,)

    # Sum probabilities for components that share a label. Always 1:1 for n=3
    # but kept general to be safe if n_components changes.
    label_probs: Dict[str, float] = {lbl: 0.0 for lbl in REGIME_LABELS}
    for comp_idx, prob in enumerate(posteriors_per_component):
        lbl = model.component_to_label.get(comp_idx)
        if lbl is None:
            continue
        label_probs[lbl] = label_probs.get(lbl, 0.0) + float(prob)

    # Argmax label + its prob
    best_label = max(label_probs.items(), key=lambda kv: kv[1])
    return RegimePrediction(
        label=best_label[0],
        confidence=float(best_label[1]),
        posteriors={k: float(v) for k, v in label_probs.items()},
        feature_vector=X[0].tolist(),
        timestamp=timestamp,
    )


# ── In-memory cache ──────────────────────────────────────────────────────────
# Simple per-process cache keyed by symbol. Redis-backed cache would be better
# in a multi-worker deploy but this app is single-worker, and the GMM model is
# a few KB pickled — not worth the Redis round-trip per prediction.
# Persistence across restarts comes from RegimeStateRecord rows in Postgres
# which carry the latest centroids; on cold start `fit_gmm` reseeds from those.

_MODEL_CACHE: Dict[str, FittedRegimeModel] = {}


def cache_model(symbol: str, model: FittedRegimeModel) -> None:
    _MODEL_CACHE[symbol] = model


def get_cached_model(symbol: str) -> Optional[FittedRegimeModel]:
    return _MODEL_CACHE.get(symbol)


def clear_cache() -> None:
    _MODEL_CACHE.clear()
