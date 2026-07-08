import json

import numpy as np

from data_analysis.vibration_features import analyze_segment
from data_analysis.vibration_io import read_vibration_segment
from data_analysis.vibration_report import write_analysis_outputs
from tests.helpers import write_npz_xz_segment


def test_write_analysis_outputs_creates_json_markdown_and_figures(tmp_path):
    sample_rate_hz = 2048.0
    time_s = np.arange(2048) / sample_rate_hz
    data = np.sin(2.0 * np.pi * 64.0 * time_s)[None, :]
    path = write_npz_xz_segment(
        tmp_path / "segment.npz.xz",
        data=data,
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0"],
    )
    analysis = analyze_segment(read_vibration_segment(path), rpm=6000.0, top_peaks=5)

    outputs = write_analysis_outputs(analysis, tmp_path / "out")

    assert outputs["json"].exists()
    assert outputs["markdown"].exists()
    assert outputs["figures_dir"].exists()
    assert (outputs["figures_dir"] / "0_Dev1_ai0_waveform.png").exists()
    assert (outputs["figures_dir"] / "0_Dev1_ai0_spectrum.png").exists()
    assert (outputs["figures_dir"] / "0_Dev1_ai0_psd.png").exists()
    assert (outputs["figures_dir"] / "0_Dev1_ai0_envelope_spectrum.png").exists()

    loaded = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert "_spectrum" not in json.dumps(loaded)
    assert loaded["channels"][0]["frequency_domain"]["dominant_frequency_hz"] > 0.0

    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "# Vibration Analysis Report" in markdown
    assert "Dev1/ai0" in markdown
    assert "bearing fault" not in markdown.lower()


def test_markdown_links_only_generated_figures_for_short_signal(tmp_path):
    path = write_npz_xz_segment(
        tmp_path / "short.npz.xz",
        data=np.array([[0.0, 1.0, 0.0]], dtype=float),
        sample_rate_hz=1000.0,
        channels=["Dev1/ai0"],
    )
    analysis = analyze_segment(read_vibration_segment(path), top_peaks=3)

    outputs = write_analysis_outputs(analysis, tmp_path / "short-out")
    markdown = outputs["markdown"].read_text(encoding="utf-8")

    assert "figures/0_Dev1_ai0_waveform.png" in markdown
    assert "figures/0_Dev1_ai0_spectrum.png" not in markdown
    assert "figures/0_Dev1_ai0_psd.png" not in markdown
    assert "figures/0_Dev1_ai0_envelope_spectrum.png" not in markdown


def test_figure_base_names_are_unique_when_sanitized_names_collide(tmp_path):
    sample_rate_hz = 1024.0
    time_s = np.arange(1024) / sample_rate_hz
    data = np.vstack(
        [
            np.sin(2.0 * np.pi * 32.0 * time_s),
            np.sin(2.0 * np.pi * 64.0 * time_s),
        ]
    )
    path = write_npz_xz_segment(
        tmp_path / "collision.npz.xz",
        data=data,
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0", "Dev1_ai0"],
    )
    analysis = analyze_segment(read_vibration_segment(path), top_peaks=3)

    outputs = write_analysis_outputs(analysis, tmp_path / "collision-out")
    markdown = outputs["markdown"].read_text(encoding="utf-8")

    assert (outputs["figures_dir"] / "0_Dev1_ai0_waveform.png").exists()
    assert (outputs["figures_dir"] / "1_Dev1_ai0_waveform.png").exists()
    assert "figures/0_Dev1_ai0_waveform.png" in markdown
    assert "figures/1_Dev1_ai0_waveform.png" in markdown
