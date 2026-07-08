import json
import subprocess
import sys

import numpy as np

from tests.helpers import write_npz_xz_segment


def test_cli_generates_outputs_for_all_channels(tmp_path):
    sample_rate_hz = 2048.0
    time_s = np.arange(2048) / sample_rate_hz
    data = np.vstack(
        [
            np.sin(2.0 * np.pi * 64.0 * time_s),
            np.sin(2.0 * np.pi * 128.0 * time_s),
        ]
    )
    input_path = write_npz_xz_segment(
        tmp_path / "segment.npz.xz",
        data=data,
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0", "Dev1/ai1"],
    )
    output_dir = tmp_path / "analysis"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/analyze_vibration_npz_xz.py",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--rpm",
            "6000",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert (output_dir / "vibration_analysis.json").exists()
    assert (output_dir / "vibration_analysis.md").exists()
    loaded = json.loads((output_dir / "vibration_analysis.json").read_text(encoding="utf-8"))
    assert loaded["selected_channels"] == ["Dev1/ai0", "Dev1/ai1"]
    assert loaded["rotating_frequency_hz"] == 100.0


def test_cli_channel_filter_limits_output(tmp_path):
    sample_rate_hz = 1024.0
    time_s = np.arange(1024) / sample_rate_hz
    data = np.vstack([np.sin(2.0 * np.pi * 32.0 * time_s), np.sin(2.0 * np.pi * 96.0 * time_s)])
    input_path = write_npz_xz_segment(
        tmp_path / "segment.npz.xz",
        data=data,
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0", "Dev1/ai1"],
    )
    output_dir = tmp_path / "single"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/analyze_vibration_npz_xz.py",
            str(input_path),
            "--channel",
            "Dev1/ai1",
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    loaded = json.loads((output_dir / "vibration_analysis.json").read_text(encoding="utf-8"))
    assert loaded["selected_channels"] == ["Dev1/ai1"]
    assert len(loaded["channels"]) == 1


def test_cli_unknown_channel_returns_actionable_error(tmp_path):
    input_path = write_npz_xz_segment(
        tmp_path / "segment.npz.xz",
        data=np.array([[0.0, 1.0, 0.0, -1.0]]),
        sample_rate_hz=1000.0,
        channels=["Dev1/ai0"],
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/analyze_vibration_npz_xz.py",
            str(input_path),
            "--channel",
            "Dev1/ai9",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 2
    assert "available channels: Dev1/ai0" in completed.stderr
