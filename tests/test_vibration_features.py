import json

import numpy as np
import pytest

from data_analysis.vibration_features import analyze_segment
from data_analysis.vibration_io import read_vibration_segment
from tests.helpers import write_npz_xz_segment


def test_sine_wave_features_match_known_frequency_and_rms(tmp_path):
    sample_rate_hz = 4096.0
    duration_s = 2.0
    frequency_hz = 128.0
    time_s = np.arange(int(sample_rate_hz * duration_s)) / sample_rate_hz
    data = np.sin(2.0 * np.pi * frequency_hz * time_s)[None, :]
    path = write_npz_xz_segment(
        tmp_path / "sine.npz.xz",
        data=data,
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0"],
    )
    segment = read_vibration_segment(path)

    result = analyze_segment(segment, top_peaks=5)
    channel = result["channels"][0]

    assert channel["channel"] == "Dev1/ai0"
    assert channel["basic"]["sample_count"] == len(time_s)
    assert channel["frequency_domain"]["dominant_frequency_hz"] == pytest.approx(frequency_hz, abs=1.0)
    assert channel["time_domain"]["rms"] == pytest.approx(2 ** -0.5, rel=0.02)
    assert channel["time_domain"]["peak_abs"] == pytest.approx(1.0, rel=0.02)
    assert channel["frequency_domain"]["spectral_peaks"][0]["frequency_hz"] == pytest.approx(frequency_hz, abs=1.0)
    assert channel["frequency_domain"]["bands"]
    assert set(channel["envelope"]).issuperset({"rms", "kurtosis", "spectral_peaks"})
    json.dumps(result)


def test_top_peaks_zero_still_reports_dominant_frequency(tmp_path):
    sample_rate_hz = 4096.0
    time_s = np.arange(4096) / sample_rate_hz
    frequency_hz = 96.0
    data = np.sin(2.0 * np.pi * frequency_hz * time_s)[None, :]
    path = write_npz_xz_segment(
        tmp_path / "zero-peaks.npz.xz",
        data=data,
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0"],
    )
    segment = read_vibration_segment(path)

    result = analyze_segment(segment, top_peaks=0)
    channel = result["channels"][0]

    assert channel["frequency_domain"]["dominant_frequency_hz"] == pytest.approx(frequency_hz, abs=1.0)
    assert channel["frequency_domain"]["spectral_peaks"] == []


def test_rpm_adds_order_to_spectral_peaks(tmp_path):
    sample_rate_hz = 4096.0
    time_s = np.arange(4096) / sample_rate_hz
    data = np.sin(2.0 * np.pi * 100.0 * time_s)[None, :]
    path = write_npz_xz_segment(
        tmp_path / "order.npz.xz",
        data=data,
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0"],
    )
    segment = read_vibration_segment(path)

    result = analyze_segment(segment, rpm=6000.0, top_peaks=3)
    peak = result["channels"][0]["frequency_domain"]["spectral_peaks"][0]

    assert result["rpm"] == 6000.0
    assert result["rotating_frequency_hz"] == pytest.approx(100.0)
    assert peak["order"] == pytest.approx(1.0, abs=0.02)


def test_impulse_signal_emits_neutral_notes(tmp_path):
    data = np.zeros((1, 4096), dtype=float)
    data[0, 200] = 10.0
    data[0, 1200] = -8.0
    path = write_npz_xz_segment(
        tmp_path / "impulse.npz.xz",
        data=data,
        sample_rate_hz=4096.0,
        channels=["Dev1/ai0"],
    )
    segment = read_vibration_segment(path)

    result = analyze_segment(segment)
    notes = " ".join(result["channels"][0]["analysis_notes"])

    assert "crest factor" in notes or "kurtosis" in notes
    assert "bearing fault" not in notes.lower()
    assert "imbalance" not in notes.lower()


def test_non_finite_input_warns_and_analyzes_finite_values(tmp_path):
    sample_rate_hz = 2048.0
    time_s = np.arange(8) / sample_rate_hz
    values = np.array([1.0, np.nan, 2.0, np.inf, 3.0, -np.inf, 4.0, 5.0], dtype=float)
    path = write_npz_xz_segment(
        tmp_path / "nonfinite.npz.xz",
        data=values[None, :],
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0"],
    )
    segment = read_vibration_segment(path)

    result = analyze_segment(segment, top_peaks=3)
    channel = result["channels"][0]
    expected_values = np.interp(
        np.arange(values.size, dtype=float),
        np.flatnonzero(np.isfinite(values)).astype(float),
        values[np.isfinite(values)],
    )

    assert any("finite ratio=" in warning for warning in channel["warnings"])
    assert channel["basic"]["finite_ratio"] == pytest.approx(5 / 8)
    assert channel["time_domain"]["rms"] == pytest.approx(np.sqrt(np.mean(expected_values**2)))
    assert channel["frequency_domain"]["bands"]
    assert channel["envelope"]["rms"] > 0.0


def test_non_finite_samples_keep_alignment_for_frequency_analysis(tmp_path):
    sample_rate_hz = 4096.0
    duration_s = 2.0
    frequency_hz = 128.0
    time_s = np.arange(int(sample_rate_hz * duration_s)) / sample_rate_hz
    values = np.sin(2.0 * np.pi * frequency_hz * time_s)
    values[1000] = np.nan
    values[2500] = np.inf
    path = write_npz_xz_segment(
        tmp_path / "aligned-nonfinite.npz.xz",
        data=values[None, :],
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0"],
    )
    segment = read_vibration_segment(path)

    result = analyze_segment(segment, top_peaks=5)
    channel = result["channels"][0]

    assert channel["basic"]["sample_count"] == len(time_s)
    assert channel["basic"]["finite_ratio"] == pytest.approx((len(time_s) - 2) / len(time_s))
    assert channel["frequency_domain"]["dominant_frequency_hz"] == pytest.approx(frequency_hz, abs=1.0)
    assert channel["frequency_domain"]["spectral_peaks"][0]["frequency_hz"] == pytest.approx(frequency_hz, abs=1.0)
    assert any("finite ratio=" in warning for warning in channel["warnings"])


def test_constant_signal_has_no_fabricated_spectral_peak(tmp_path):
    sample_rate_hz = 2048.0
    values = np.full(2048, 3.5, dtype=float)
    path = write_npz_xz_segment(
        tmp_path / "constant.npz.xz",
        data=values[None, :],
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0"],
    )
    segment = read_vibration_segment(path)

    result = analyze_segment(segment, top_peaks=5)
    channel = result["channels"][0]

    assert channel["basic"]["near_constant"] is True
    assert channel["frequency_domain"]["spectral_peaks"] == []
    assert channel["frequency_domain"]["dominant_frequency_hz"] == 0.0
    assert np.isfinite(channel["envelope"]["kurtosis"])
    json.dumps(result)
    assert any("near constant" in note.lower() for note in channel["analysis_notes"])


def test_below_half_finite_ratio_skips_channel_feature_groups(tmp_path):
    sample_rate_hz = 1024.0
    values = np.array(
        [1.0, np.nan, np.inf, np.nan, 2.0, np.nan, np.nan, np.inf, np.nan, np.nan, np.inf, 3.0],
        dtype=float,
    )
    path = write_npz_xz_segment(
        tmp_path / "below-half-finite-ratio.npz.xz",
        data=values[None, :],
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0"],
    )
    segment = read_vibration_segment(path)

    result = analyze_segment(segment)
    channel = result["channels"][0]

    assert channel["basic"]["sample_count"] == len(values)
    assert channel["basic"]["finite_ratio"] == pytest.approx(3 / len(values))
    assert channel["time_domain"] == {}
    assert channel["frequency_domain"] == {}
    assert channel["envelope"] == {}
    assert any("finite ratio=" in warning for warning in channel["warnings"])
    assert any("finite-value ratio below 0.5" in warning for warning in channel["warnings"])
    assert any("too short for reliable analysis" in warning.lower() for warning in channel["warnings"])


def test_segment_analysis_uses_all_channels_by_default(tmp_path):
    sample_rate_hz = 1024.0
    time_s = np.arange(1024) / sample_rate_hz
    data = np.vstack(
        [
            np.sin(2.0 * np.pi * 32.0 * time_s),
            np.sin(2.0 * np.pi * 64.0 * time_s),
        ]
    )
    path = write_npz_xz_segment(
        tmp_path / "multi-channel.npz.xz",
        data=data,
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0", "Dev1/ai1"],
    )
    segment = read_vibration_segment(path)

    result = analyze_segment(segment, top_peaks=3)

    assert result["selected_channels"] == ["Dev1/ai0", "Dev1/ai1"]
    assert len(result["channels"]) == 2
    assert {channel["channel"] for channel in result["channels"]} == {"Dev1/ai0", "Dev1/ai1"}


def test_low_nyquist_warns_when_default_bands_are_clipped_or_omitted(tmp_path):
    sample_rate_hz = 100.0
    time_s = np.arange(1000) / sample_rate_hz
    data = np.sin(2.0 * np.pi * 8.0 * time_s)[None, :]
    path = write_npz_xz_segment(
        tmp_path / "low-nyquist.npz.xz",
        data=data,
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0"],
    )
    segment = read_vibration_segment(path)

    result = analyze_segment(segment, top_peaks=3)
    channel = result["channels"][0]

    assert any("10.0-100.0 Hz clipped" in warning for warning in channel["warnings"])
    assert any("100.0-1000.0 Hz omitted" in warning for warning in channel["warnings"])
    assert any(band["high_hz"] <= sample_rate_hz / 2.0 for band in channel["frequency_domain"]["bands"])


def test_analyze_segment_rejects_invalid_optional_numeric_inputs(tmp_path):
    path = write_npz_xz_segment(
        tmp_path / "valid.npz.xz",
        data=np.array([[0.0, 1.0, 0.0, -1.0]], dtype=float),
        sample_rate_hz=1000.0,
        channels=["Dev1/ai0"],
    )
    segment = read_vibration_segment(path)

    with pytest.raises(ValueError, match="rpm must be finite and positive"):
        analyze_segment(segment, rpm=float("nan"))

    with pytest.raises(ValueError, match="top_peaks must be greater than or equal to 0"):
        analyze_segment(segment, top_peaks=-1)
