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
