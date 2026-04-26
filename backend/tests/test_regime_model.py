"""Tests for the GMM regime model.

Pure-stats tests on synthetic data — no network, no DB. Covers:
- fit on three-Gaussian synthetic data recovers the right component count
- label ordering (risk_off = lowest mean return)
- warm-start label stability across refits
- predict_regime returns a probability distribution that sums to 1
- min-candle guard
- fingerprint determinism
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.regime_model import (
    DEFAULT_VOL_WINDOW,
    MIN_FIT_CANDLES,
    REGIME_LABELS,
    fit_gmm,
    predict_regime,
    _build_features,
    _fingerprint,
    _label_components,
)


def _three_regime_closes(seed: int = 0, n_per_regime: int = 1500) -> pd.Series:
    """Synthesise a price series stitched from three regimes with clearly
    different drift + vol so a GMM should identify all three.

    Regime A (risk_off): negative drift, high vol
    Regime B (range):    zero drift, low vol
    Regime C (risk_on):  positive drift, medium vol
    """
    rng = np.random.default_rng(seed)
    log_ret_a = rng.normal(loc=-0.005, scale=0.030, size=n_per_regime)
    log_ret_b = rng.normal(loc=0.0,    scale=0.005, size=n_per_regime)
    log_ret_c = rng.normal(loc=0.004,  scale=0.015, size=n_per_regime)
    log_returns = np.concatenate([log_ret_a, log_ret_b, log_ret_c])
    # Convert log-returns → close-price series starting at 100
    closes = 100.0 * np.exp(np.cumsum(log_returns))
    return pd.Series(closes, dtype=float)


class TestBuildFeatures:
    def test_drops_nan_rows_from_rolling(self):
        closes = pd.Series([100.0 + i for i in range(50)], dtype=float)
        feats = _build_features(closes, vol_window=10)
        # First 10 rows go to compute the rolling vol; row 0 also drops for the diff
        assert len(feats) == 50 - 10
        assert not feats.isna().any().any()
        assert list(feats.columns) == ['log_return', 'vol']

    def test_rejects_too_few_candles(self):
        closes = pd.Series([100.0, 101.0, 102.0], dtype=float)
        with pytest.raises(ValueError, match="at least"):
            _build_features(closes, vol_window=10)


class TestFitGmm:
    def test_fits_three_components_on_synthetic_data(self):
        closes = _three_regime_closes(seed=1)
        model = fit_gmm(closes, n_components=3, seed=42)
        assert model.gmm.n_components == 3
        # All three labels must be assigned
        assert set(model.component_to_label.values()) == set(REGIME_LABELS)
        # Each label has a centroid
        assert set(model.label_centroids.keys()) == set(REGIME_LABELS)

    def test_rejects_too_few_candles(self):
        closes = pd.Series(np.random.default_rng(0).normal(100, 1, MIN_FIT_CANDLES - 50))
        with pytest.raises(ValueError, match="at least"):
            fit_gmm(closes)

    def test_label_ordering_risk_off_lowest_mean(self):
        closes = _three_regime_closes(seed=2)
        model = fit_gmm(closes, seed=42)
        # risk_off must be the component with the lowest mean log-return
        mean_log_ret_by_label = {
            lbl: centroid[0] for lbl, centroid in model.label_centroids.items()
        }
        sorted_labels = sorted(mean_log_ret_by_label.items(), key=lambda kv: kv[1])
        # Lowest = risk_off, highest = risk_on
        assert sorted_labels[0][0] == 'risk_off'
        assert sorted_labels[-1][0] == 'risk_on'

    def test_warm_start_preserves_label_mapping(self):
        # Fit once cold-start, then refit with the prior centroids and verify
        # labels stay attached to the same component centroids (within ε).
        closes = _three_regime_closes(seed=3)
        model_a = fit_gmm(closes, seed=42)
        # Now perturb the data slightly (different seed) and refit warm
        closes_perturbed = _three_regime_closes(seed=4)
        model_b = fit_gmm(
            closes_perturbed,
            seed=42,
            prior_centroids=model_a.label_centroids,
        )
        # Centroids close to the prior should map to the same label
        for label, prior_centroid in model_a.label_centroids.items():
            new_centroid = model_b.label_centroids[label]
            # Both centroids should be in roughly the same region of feature space
            dist = np.linalg.norm(np.array(prior_centroid) - np.array(new_centroid))
            # Sloppy threshold since the data is different but same regime distribution
            assert dist < 0.05, f"Label {label} drifted by {dist:.4f}"


class TestPredictRegime:
    def test_returns_distribution_summing_to_one(self):
        closes = _three_regime_closes(seed=5)
        model = fit_gmm(closes, seed=42)
        prediction = predict_regime(model, closes)
        # Posteriors must sum to 1 (within float tolerance)
        total = sum(prediction.posteriors.values())
        assert total == pytest.approx(1.0, abs=1e-6)
        # Confidence is the max posterior
        assert prediction.confidence == max(prediction.posteriors.values())
        # Argmax label must match
        argmax_label = max(prediction.posteriors.items(), key=lambda kv: kv[1])[0]
        assert prediction.label == argmax_label

    def test_label_is_one_of_known_regimes(self):
        closes = _three_regime_closes(seed=6)
        model = fit_gmm(closes, seed=42)
        prediction = predict_regime(model, closes)
        assert prediction.label in REGIME_LABELS

    def test_recent_uptrend_classified_as_risk_on(self):
        # Fit on three-regime data, then predict on a synthetic recent slice
        # that's nothing but positive returns — should classify as risk_on.
        closes = _three_regime_closes(seed=7)
        model = fit_gmm(closes, seed=42)
        # Append 50 strong positive bars
        rng = np.random.default_rng(99)
        last = closes.iloc[-1]
        uptrend = []
        for r in rng.normal(loc=0.005, scale=0.008, size=50):
            last = last * np.exp(r)
            uptrend.append(last)
        recent = pd.concat([closes.iloc[-100:], pd.Series(uptrend, dtype=float)], ignore_index=True)
        prediction = predict_regime(model, recent)
        # Posterior on risk_on should dominate
        assert prediction.posteriors['risk_on'] > prediction.posteriors['risk_off']

    def test_predict_requires_sufficient_history(self):
        closes = _three_regime_closes(seed=8)
        model = fit_gmm(closes, seed=42)
        # Only 5 bars — can't compute the rolling vol window
        with pytest.raises(ValueError):
            predict_regime(model, closes.iloc[-5:])


class TestFingerprint:
    def test_deterministic_for_identical_centroids(self):
        c = {'risk_off': [-0.001, 0.02], 'range': [0.0, 0.005], 'risk_on': [0.003, 0.015]}
        assert _fingerprint(c) == _fingerprint(c)

    def test_changes_when_centroids_change(self):
        c1 = {'risk_off': [-0.001, 0.02], 'range': [0.0, 0.005], 'risk_on': [0.003, 0.015]}
        c2 = {'risk_off': [-0.002, 0.02], 'range': [0.0, 0.005], 'risk_on': [0.003, 0.015]}
        assert _fingerprint(c1) != _fingerprint(c2)

    def test_independent_of_dict_insertion_order(self):
        c1 = {'risk_off': [-0.001, 0.02], 'range': [0.0, 0.005], 'risk_on': [0.003, 0.015]}
        c2 = {'risk_on': [0.003, 0.015], 'risk_off': [-0.001, 0.02], 'range': [0.0, 0.005]}
        assert _fingerprint(c1) == _fingerprint(c2)


class TestLabelComponents:
    def test_cold_start_assigns_by_mean_return(self):
        # Build a fake fitted GMM with hand-picked component means
        from sklearn.mixture import GaussianMixture
        gmm = GaussianMixture(n_components=3, covariance_type='diag', random_state=0)
        # Hand-set the means (would normally be computed by .fit()).
        gmm.means_ = np.array([
            [0.005, 0.01],   # comp 0: high return → should be risk_on
            [-0.003, 0.03],  # comp 1: low return  → should be risk_off
            [0.0, 0.005],    # comp 2: mid return  → should be range
        ])
        comp_to_label, _ = _label_components(gmm, prior_centroids=None)
        assert comp_to_label[1] == 'risk_off'
        assert comp_to_label[2] == 'range'
        assert comp_to_label[0] == 'risk_on'

    def test_warm_start_matches_to_nearest_prior(self):
        from sklearn.mixture import GaussianMixture
        gmm = GaussianMixture(n_components=3, covariance_type='diag', random_state=0)
        gmm.means_ = np.array([
            [-0.002, 0.025],  # comp 0: was risk_off last time
            [0.001, 0.006],   # comp 1: was range last time
            [0.004, 0.014],   # comp 2: was risk_on last time
        ])
        prior = {
            'risk_off': [-0.003, 0.030],  # close to comp 0
            'range':    [0.000, 0.005],   # close to comp 1
            'risk_on':  [0.005, 0.015],   # close to comp 2
        }
        comp_to_label, _ = _label_components(gmm, prior_centroids=prior)
        assert comp_to_label[0] == 'risk_off'
        assert comp_to_label[1] == 'range'
        assert comp_to_label[2] == 'risk_on'
