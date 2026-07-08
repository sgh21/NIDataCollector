# Vibration Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable Python vibration-analysis core that reads one standard vibration `.npz.xz` segment and writes JSON, Markdown, and PNG analysis outputs.

**Architecture:** Use a small layered package under `src/data_analysis`: IO validation, feature extraction, reporting, and CLI orchestration are separate modules. The command-line script is only a thin wrapper around reusable library functions.

**Tech Stack:** Python 3.10+, standard library, `numpy`, `scipy`, `matplotlib`, `pandas`, `pytest`.

## Global Constraints

- Operate directly on the `.npz.xz` format documented in `docs/data_migration_analysis.md`.
- Do not depend on hardware acquisition modules from the original application branch.
- Do not implement hard fault diagnosis labels such as imbalance, looseness, bearing fault, or misalignment.
- Do not require run-level metadata, spindle telemetry, bearing geometry, or historical baselines.
- Do not generate HTML reports.
- Do not use pickle when reading NumPy payloads.
- Analyze all channels by default; `--channel` selects exactly one channel.
- `--rpm` adds order information but is not required.
- Keep JSON machine-readable and Markdown human-readable.
- Clean temporary test outputs and `__pycache__` after validation.
- Do not include unrelated existing worktree changes in commits.

---

## Scope Check

This plan implements one subsystem: single-segment vibration analysis. It does not include batch run analysis, baseline management, fault classification, or GUI integration.

## File Structure

- Create `pyproject.toml`: package metadata, dependencies, pytest configuration.
- Create `src/data_analysis/__init__.py`: package exports and version.
- Create `src/data_analysis/vibration_io.py`: `.npz.xz` loading, validation, channel selection.
- Create `src/data_analysis/vibration_features.py`: time-domain, frequency-domain, envelope, band-energy, order, and neutral note logic.
- Create `src/data_analysis/vibration_report.py`: JSON, Markdown, and PNG output.
- Create `src/data_analysis/cli.py`: CLI argument parsing and pipeline orchestration.
- Create `scripts/analyze_vibration_npz_xz.py`: thin executable wrapper.
- Create `tests/helpers.py`: synthetic `.npz.xz` fixture writer.
- Create `tests/test_vibration_io.py`: loader and validation tests.
- Create `tests/test_vibration_features.py`: numerical feature tests.
- Create `tests/test_vibration_report.py`: report writer tests.
- Create `tests/test_cli.py`: end-to-end CLI tests.
- Create `docs/vibration_analysis_usage.md`: usage guide for the new analyzer.

---

### Task 1: Project Scaffold and Synthetic Test Fixture

**Files:**
- Create: `pyproject.toml`
- Create: `src/data_analysis/__init__.py`
- Create: `tests/helpers.py`
- Test: `tests/test_fixture_writer.py`

**Interfaces:**
- Produces: `tests.helpers.write_npz_xz_segment(path: Path, data: np.ndarray, sample_rate_hz: float, channels: list[str], signal_type: str = "acceleration", unit: str = "g") -> Path`
- Later tasks use the fixture writer to create valid and malformed `.npz.xz` test files.

- [ ] **Step 1: Write the fixture writer test**

Create `tests/test_fixture_writer.py`:

```python
import lzma

import numpy as np

from tests.helpers import write_npz_xz_segment


def test_write_npz_xz_segment_creates_standard_payload(tmp_path):
    path = tmp_path / "segment.npz.xz"
    sample_rate_hz = 1000.0
    data = np.array([[0.0, 1.0, 0.0, -1.0]], dtype=float)

    written = write_npz_xz_segment(
        path,
        data=data,
        sample_rate_hz=sample_rate_hz,
        channels=["Dev1/ai0"],
    )

    assert written == path
    assert path.exists()

    with lzma.open(path, "rb") as handle:
        payload = np.load(handle, allow_pickle=False)
        assert set(payload.files) == {
            "time_s",
            "data",
            "channels",
            "sample_start_index",
            "sample_rate_hz",
            "signal_type",
            "unit",
        }
        np.testing.assert_allclose(payload["time_s"], [0.0, 0.001, 0.002, 0.003])
        np.testing.assert_allclose(payload["data"], data)
        assert payload["channels"].astype(str).tolist() == ["Dev1/ai0"]
        assert int(payload["sample_start_index"].item()) == 0
        assert float(payload["sample_rate_hz"].item()) == sample_rate_hz
        assert str(payload["signal_type"].item()) == "acceleration"
        assert str(payload["unit"].item()) == "g"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
python -m pytest tests\test_fixture_writer.py -v
```

Expected: FAIL because `tests.helpers` does not exist.

- [ ] **Step 3: Add package configuration and fixture helper**

Create `pyproject.toml`:

```toml
[project]
name = "nidatacollector-data-analysis"
version = "0.1.0"
description = "DataAnalysis branch tools for NI vibration segment analysis"
requires-python = ">=3.10"
dependencies = [
  "numpy",
  "scipy",
  "matplotlib",
  "pandas",
]

[project.optional-dependencies]
test = [
  "pytest",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src", "."]
```

Create `src/data_analysis/__init__.py`:

```python
"""Reusable data-analysis tools for standard NIDataCollector segments."""

__version__ = "0.1.0"
```

Create `tests/helpers.py`:

```python
from __future__ import annotations

import io
import lzma
from pathlib import Path

import numpy as np


def write_npz_xz_segment(
    path: Path,
    *,
    data: np.ndarray,
    sample_rate_hz: float,
    channels: list[str],
    signal_type: str = "acceleration",
    unit: str = "g",
    sample_start_index: int = 0,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(data, dtype=float)
    if data.ndim != 2:
        raise ValueError("data must have shape (channel_count, sample_count)")
    if data.shape[0] != len(channels):
        raise ValueError("channels length must match data.shape[0]")

    sample_count = data.shape[1]
    time_s = (sample_start_index + np.arange(sample_count, dtype=float)) / float(sample_rate_hz)

    buffer = io.BytesIO()
    np.savez(
        buffer,
        time_s=time_s.astype(np.float64),
        data=data.astype(np.float64),
        channels=np.asarray(channels, dtype=str),
        sample_start_index=np.asarray(sample_start_index, dtype=np.int64),
        sample_rate_hz=np.asarray(float(sample_rate_hz), dtype=np.float64),
        signal_type=np.asarray(signal_type, dtype=str),
        unit=np.asarray(unit, dtype=str),
    )

    with lzma.open(path, "wb") as handle:
        handle.write(buffer.getvalue())

    return path
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```powershell
python -m pytest tests\test_fixture_writer.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml src\data_analysis\__init__.py tests\helpers.py tests\test_fixture_writer.py
git commit -m "test: add vibration segment fixture writer"
```

---

### Task 2: `.npz.xz` Reader, Validation, and Channel Selection

**Files:**
- Create: `src/data_analysis/vibration_io.py`
- Test: `tests/test_vibration_io.py`

**Interfaces:**
- Consumes: `tests.helpers.write_npz_xz_segment(...)`
- Produces: `class VibrationPayloadError(ValueError)`
- Produces: `@dataclass(frozen=True) class VibrationSegment`
- Produces: `read_vibration_segment(path: Path | str) -> VibrationSegment`
- Produces: `select_channels(segment: VibrationSegment, channel: str | None = None) -> VibrationSegment`

- [ ] **Step 1: Write loader and channel-selection tests**

Create `tests/test_vibration_io.py`:

```python
import io
import lzma

import numpy as np
import pytest

from data_analysis.vibration_io import (
    VibrationPayloadError,
    read_vibration_segment,
    select_channels,
)
from tests.helpers import write_npz_xz_segment


def test_read_vibration_segment_loads_valid_payload(tmp_path):
    path = write_npz_xz_segment(
        tmp_path / "valid.npz.xz",
        data=np.array([[0.0, 1.0], [2.0, 3.0]], dtype=float),
        sample_rate_hz=1000.0,
        channels=["Dev1/ai0", "Dev1/ai1"],
    )

    segment = read_vibration_segment(path)

    assert segment.path == path
    assert segment.signal_type == "acceleration"
    assert segment.unit == "g"
    assert segment.sample_rate_hz == 1000.0
    assert segment.sample_start_index == 0
    assert segment.channels == ("Dev1/ai0", "Dev1/ai1")
    assert segment.data.shape == (2, 2)
    assert segment.time_s.shape == (2,)
    assert segment.warnings == ()


def test_select_channels_returns_exact_channel(tmp_path):
    path = write_npz_xz_segment(
        tmp_path / "valid.npz.xz",
        data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=float),
        sample_rate_hz=1000.0,
        channels=["Dev1/ai0", "Dev1/ai1"],
    )
    segment = read_vibration_segment(path)

    selected = select_channels(segment, "Dev1/ai1")

    assert selected.channels == ("Dev1/ai1",)
    np.testing.assert_allclose(selected.data, [[3.0, 4.0]])


def test_select_channels_lists_available_channels_for_unknown_name(tmp_path):
    path = write_npz_xz_segment(
        tmp_path / "valid.npz.xz",
        data=np.array([[1.0, 2.0]], dtype=float),
        sample_rate_hz=1000.0,
        channels=["Dev1/ai0"],
    )
    segment = read_vibration_segment(path)

    with pytest.raises(VibrationPayloadError, match="Dev1/ai9.*Dev1/ai0"):
        select_channels(segment, "Dev1/ai9")


def test_missing_required_field_is_reported(tmp_path):
    path = tmp_path / "missing.npz.xz"
    buffer = io.BytesIO()
    np.savez(buffer, data=np.array([[1.0, 2.0]]))
    with lzma.open(path, "wb") as handle:
        handle.write(buffer.getvalue())

    with pytest.raises(VibrationPayloadError, match="missing required fields.*channels.*sample_rate_hz"):
        read_vibration_segment(path)


def test_non_acceleration_signal_is_rejected(tmp_path):
    path = write_npz_xz_segment(
        tmp_path / "temperature.npz.xz",
        data=np.array([[23.0, 24.0]], dtype=float),
        sample_rate_hz=10.0,
        channels=["COM4/ch0"],
        signal_type="temperature_ntc",
        unit="degC",
    )

    with pytest.raises(VibrationPayloadError, match="signal_type must be acceleration"):
        read_vibration_segment(path)


def test_shape_mismatch_is_rejected(tmp_path):
    path = tmp_path / "bad_shape.npz.xz"
    buffer = io.BytesIO()
    np.savez(
        buffer,
        time_s=np.array([0.0, 0.001, 0.002]),
        data=np.array([[1.0, 2.0]]),
        channels=np.array(["Dev1/ai0"]),
        sample_start_index=np.asarray(0),
        sample_rate_hz=np.asarray(1000.0),
        signal_type=np.asarray("acceleration"),
        unit=np.asarray("g"),
    )
    with lzma.open(path, "wb") as handle:
        handle.write(buffer.getvalue())

    with pytest.raises(VibrationPayloadError, match="len\\(time_s\\).*data.shape\\[1\\]"):
        read_vibration_segment(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests\test_vibration_io.py -v
```

Expected: FAIL because `data_analysis.vibration_io` does not exist.

- [ ] **Step 3: Implement reader and validation**

Create `src/data_analysis/vibration_io.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, replace
import lzma
from pathlib import Path
from typing import Any

import numpy as np


REQUIRED_FIELDS = (
    "time_s",
    "data",
    "channels",
    "sample_start_index",
    "sample_rate_hz",
    "signal_type",
    "unit",
)


class VibrationPayloadError(ValueError):
    """Raised when a vibration segment cannot be read or validated."""


@dataclass(frozen=True)
class VibrationSegment:
    path: Path
    time_s: np.ndarray
    data: np.ndarray
    channels: tuple[str, ...]
    sample_start_index: int
    sample_rate_hz: float
    signal_type: str
    unit: str
    warnings: tuple[str, ...] = ()


def read_vibration_segment(path: Path | str) -> VibrationSegment:
    segment_path = Path(path)
    if not segment_path.exists():
        raise VibrationPayloadError(f"input file does not exist: {segment_path}")
    if not segment_path.name.endswith(".npz.xz"):
        raise VibrationPayloadError(f"expected a .npz.xz file: {segment_path}")

    try:
        with lzma.open(segment_path, "rb") as handle:
            loaded = np.load(handle, allow_pickle=False)
            payload: dict[str, Any] = {name: loaded[name] for name in loaded.files}
    except Exception as exc:
        raise VibrationPayloadError(f"failed to read .npz.xz payload: {segment_path}: {exc}") from exc

    missing = [name for name in REQUIRED_FIELDS if name not in payload]
    if missing:
        raise VibrationPayloadError(f"missing required fields: {', '.join(missing)}")

    time_s = np.asarray(payload["time_s"], dtype=np.float64)
    data = np.asarray(payload["data"], dtype=np.float64)
    channels = tuple(str(value) for value in np.asarray(payload["channels"]).astype(str).tolist())
    sample_start_index = int(np.asarray(payload["sample_start_index"]).item())
    sample_rate_hz = float(np.asarray(payload["sample_rate_hz"]).item())
    signal_type = str(np.asarray(payload["signal_type"]).item())
    unit = str(np.asarray(payload["unit"]).item())

    warnings: list[str] = []

    if signal_type != "acceleration":
        raise VibrationPayloadError(f"signal_type must be acceleration, got {signal_type!r}")
    if data.ndim != 2:
        raise VibrationPayloadError(f"data must be 2D with shape (channel_count, sample_count), got {data.shape}")
    if time_s.ndim != 1:
        raise VibrationPayloadError(f"time_s must be 1D, got shape {time_s.shape}")
    if len(time_s) != data.shape[1]:
        raise VibrationPayloadError(f"len(time_s) must equal data.shape[1], got {len(time_s)} and {data.shape[1]}")
    if len(channels) != data.shape[0]:
        raise VibrationPayloadError(f"len(channels) must equal data.shape[0], got {len(channels)} and {data.shape[0]}")
    if sample_rate_hz <= 0:
        raise VibrationPayloadError(f"sample_rate_hz must be positive, got {sample_rate_hz}")
    if not np.all(np.isfinite(time_s)):
        raise VibrationPayloadError("time_s contains non-finite values")
    if len(time_s) > 1:
        diffs = np.diff(time_s)
        if np.any(diffs < -1e-12):
            raise VibrationPayloadError("time_s must be monotonically increasing")
        expected_dt = 1.0 / sample_rate_hz
        if np.any(np.abs(diffs - expected_dt) > max(1e-9, expected_dt * 0.01)):
            warnings.append("time_s spacing differs from 1/sample_rate_hz by more than 1%")

    return VibrationSegment(
        path=segment_path,
        time_s=time_s,
        data=data,
        channels=channels,
        sample_start_index=sample_start_index,
        sample_rate_hz=sample_rate_hz,
        signal_type=signal_type,
        unit=unit,
        warnings=tuple(warnings),
    )


def select_channels(segment: VibrationSegment, channel: str | None = None) -> VibrationSegment:
    if channel is None:
        return segment
    if channel not in segment.channels:
        available = ", ".join(segment.channels)
        raise VibrationPayloadError(f"unknown channel {channel!r}; available channels: {available}")

    index = segment.channels.index(channel)
    return replace(
        segment,
        data=segment.data[index : index + 1, :],
        channels=(channel,),
    )
```

- [ ] **Step 4: Run loader tests and fixture tests**

Run:

```powershell
python -m pytest tests\test_fixture_writer.py tests\test_vibration_io.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src\data_analysis\vibration_io.py tests\test_vibration_io.py
git commit -m "feat: read vibration npz xz segments"
```

---

### Task 3: Core Feature Extraction

**Files:**
- Create: `src/data_analysis/vibration_features.py`
- Test: `tests/test_vibration_features.py`

**Interfaces:**
- Consumes: `VibrationSegment`
- Produces: `analyze_segment(segment: VibrationSegment, rpm: float | None = None, top_peaks: int = 10) -> dict[str, Any]`
- Produces: `analyze_channel(channel: str, time_s: np.ndarray, values: np.ndarray, sample_rate_hz: float, unit: str, rpm: float | None, top_peaks: int) -> dict[str, Any]`
- Later reporting uses the returned dictionary directly.

- [ ] **Step 1: Write numerical feature tests**

Create `tests/test_vibration_features.py`:

```python
import numpy as np

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
```

Add the missing import at the top of the same file:

```python
import pytest
```

- [ ] **Step 2: Run feature tests to verify they fail**

Run:

```powershell
python -m pytest tests\test_vibration_features.py -v
```

Expected: FAIL because `data_analysis.vibration_features` does not exist.

- [ ] **Step 3: Implement feature extraction**

Create `src/data_analysis/vibration_features.py` with these public functions and keys:

```python
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import signal, stats

from data_analysis.vibration_io import VibrationSegment


DEFAULT_BANDS_HZ = (
    (0.0, 10.0),
    (10.0, 100.0),
    (100.0, 1000.0),
    (1000.0, 5000.0),
)


def analyze_segment(
    segment: VibrationSegment,
    *,
    rpm: float | None = None,
    top_peaks: int = 10,
) -> dict[str, Any]:
    rotating_frequency_hz = None if rpm is None else float(rpm) / 60.0
    channels = [
        analyze_channel(
            channel=channel,
            time_s=segment.time_s,
            values=segment.data[index],
            sample_rate_hz=segment.sample_rate_hz,
            unit=segment.unit,
            rpm=rpm,
            top_peaks=top_peaks,
        )
        for index, channel in enumerate(segment.channels)
    ]
    return {
        "input": {
            "path": str(segment.path),
            "signal_type": segment.signal_type,
            "unit": segment.unit,
            "sample_rate_hz": segment.sample_rate_hz,
            "sample_start_index": segment.sample_start_index,
        },
        "selected_channels": list(segment.channels),
        "rpm": None if rpm is None else float(rpm),
        "rotating_frequency_hz": rotating_frequency_hz,
        "warnings": list(segment.warnings),
        "channels": channels,
    }


def analyze_channel(
    *,
    channel: str,
    time_s: np.ndarray,
    values: np.ndarray,
    sample_rate_hz: float,
    unit: str,
    rpm: float | None,
    top_peaks: int,
) -> dict[str, Any]:
    clean_values, warnings = _finite_values(np.asarray(values, dtype=float))
    if clean_values.size < 4:
        return {
            "channel": channel,
            "basic": _basic_info(channel, time_s, values, sample_rate_hz, unit),
            "time_domain": {},
            "frequency_domain": {},
            "envelope": {},
            "warnings": warnings + ["signal is too short for reliable analysis"],
            "analysis_notes": ["Signal is too short for reliable FFT, PSD, or envelope analysis."],
        }

    demeaned = clean_values - float(np.mean(clean_values))
    time_features = _time_domain_features(clean_values, sample_rate_hz)
    frequency_features = _frequency_features(demeaned, sample_rate_hz, rpm, top_peaks)
    envelope_features = _envelope_features(demeaned, sample_rate_hz, rpm, top_peaks)
    notes = _analysis_notes(time_features, frequency_features, clean_values)

    return {
        "channel": channel,
        "basic": _basic_info(channel, time_s, values, sample_rate_hz, unit),
        "time_domain": time_features,
        "frequency_domain": frequency_features,
        "envelope": envelope_features,
        "warnings": warnings,
        "analysis_notes": notes,
    }
```

In the same file, implement helper functions with these exact names:

```python
def _finite_values(values: np.ndarray) -> tuple[np.ndarray, list[str]]:
    mask = np.isfinite(values)
    finite_ratio = float(np.count_nonzero(mask) / values.size) if values.size else 0.0
    warnings: list[str] = []
    if finite_ratio < 1.0:
        warnings.append(f"non-finite values removed; finite ratio={finite_ratio:.6f}")
    if finite_ratio < 0.5:
        return np.asarray([], dtype=float), warnings + ["finite-value ratio below 0.5"]
    return values[mask].astype(float), warnings
```

```python
def _basic_info(channel: str, time_s: np.ndarray, values: np.ndarray, sample_rate_hz: float, unit: str) -> dict[str, Any]:
    finite_ratio = float(np.count_nonzero(np.isfinite(values)) / values.size) if values.size else 0.0
    finite_values = values[np.isfinite(values)]
    near_constant = bool(finite_values.size > 0 and np.nanstd(finite_values) <= max(1e-12, abs(float(np.nanmean(finite_values))) * 1e-9))
    return {
        "channel": channel,
        "sample_rate_hz": float(sample_rate_hz),
        "sample_count": int(values.size),
        "duration_s": float(time_s[-1] - time_s[0]) if len(time_s) > 1 else 0.0,
        "time_start_s": float(time_s[0]) if len(time_s) else None,
        "time_end_s": float(time_s[-1]) if len(time_s) else None,
        "unit": unit,
        "finite_ratio": finite_ratio,
        "near_constant": near_constant,
    }
```

```python
def _time_domain_features(values: np.ndarray, sample_rate_hz: float) -> dict[str, float]:
    abs_values = np.abs(values)
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=0))
    rms = float(np.sqrt(np.mean(values**2)))
    peak_abs = float(np.max(abs_values))
    absolute_mean = float(np.mean(abs_values))
    sqrt_abs_mean = float(np.mean(np.sqrt(abs_values)))
    eps = 1e-12
    signs = np.signbit(values)
    zero_crossings = int(np.count_nonzero(signs[1:] != signs[:-1])) if values.size > 1 else 0
    return {
        "mean": mean,
        "std": std,
        "rms": rms,
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "peak_abs": peak_abs,
        "peak_to_peak": float(np.ptp(values)),
        "absolute_mean": absolute_mean,
        "energy": float(np.sum(values**2)),
        "crest_factor": float(peak_abs / max(rms, eps)),
        "impulse_factor": float(peak_abs / max(absolute_mean, eps)),
        "shape_factor": float(rms / max(absolute_mean, eps)),
        "clearance_factor": float(peak_abs / max(sqrt_abs_mean**2, eps)),
        "skewness": float(stats.skew(values, bias=False)) if values.size > 2 else 0.0,
        "kurtosis": float(stats.kurtosis(values, fisher=False, bias=False)) if values.size > 3 else 0.0,
        "zero_crossing_rate_hz": float(zero_crossings * sample_rate_hz / max(values.size - 1, 1)),
    }
```

Implement `_frequency_features`, `_envelope_features`, `_top_peaks`, `_band_features`, `_with_order`, and `_analysis_notes` in the same file:

```python
def _frequency_features(values: np.ndarray, sample_rate_hz: float, rpm: float | None, top_peaks: int) -> dict[str, Any]:
    n = values.size
    windowed = values * np.hanning(n)
    frequencies = np.fft.rfftfreq(n, d=1.0 / sample_rate_hz)
    amplitudes = (2.0 / max(np.sum(np.hanning(n)), 1e-12)) * np.abs(np.fft.rfft(windowed))
    if amplitudes.size:
        amplitudes[0] = amplitudes[0] / 2.0
    peak_rows = _top_peaks(frequencies, amplitudes, top_peaks, rpm)
    psd_freq, psd_values = signal.welch(values, fs=sample_rate_hz, nperseg=min(4096, n))
    power_sum = float(np.sum(amplitudes))
    centroid = float(np.sum(frequencies * amplitudes) / power_sum) if power_sum > 0 else 0.0
    bandwidth = float(np.sqrt(np.sum(((frequencies - centroid) ** 2) * amplitudes) / power_sum)) if power_sum > 0 else 0.0
    cumulative = np.cumsum(amplitudes)
    rolloff_index = int(np.searchsorted(cumulative, 0.85 * cumulative[-1])) if cumulative.size and cumulative[-1] > 0 else 0
    return {
        "dominant_frequency_hz": float(peak_rows[0]["frequency_hz"]) if peak_rows else 0.0,
        "spectral_peaks": peak_rows,
        "spectral_centroid_hz": centroid,
        "spectral_bandwidth_hz": bandwidth,
        "spectral_rolloff_hz": float(frequencies[min(rolloff_index, len(frequencies) - 1)]) if frequencies.size else 0.0,
        "bands": _band_features(frequencies, amplitudes, sample_rate_hz),
        "_spectrum": {"frequency_hz": frequencies, "amplitude": amplitudes},
        "_psd": {"frequency_hz": psd_freq, "power": psd_values},
    }
```

```python
def _envelope_features(values: np.ndarray, sample_rate_hz: float, rpm: float | None, top_peaks: int) -> dict[str, Any]:
    analytic = signal.hilbert(values)
    envelope = np.abs(analytic)
    envelope_demeaned = envelope - float(np.mean(envelope))
    n = envelope_demeaned.size
    frequencies = np.fft.rfftfreq(n, d=1.0 / sample_rate_hz)
    amplitudes = (2.0 / max(n, 1)) * np.abs(np.fft.rfft(envelope_demeaned))
    peaks = _top_peaks(frequencies, amplitudes, top_peaks, rpm)
    return {
        "rms": float(np.sqrt(np.mean(envelope**2))),
        "kurtosis": float(stats.kurtosis(envelope, fisher=False, bias=False)) if envelope.size > 3 else 0.0,
        "spectral_peaks": peaks,
        "_spectrum": {"frequency_hz": frequencies, "amplitude": amplitudes},
    }
```

```python
def _top_peaks(frequencies: np.ndarray, amplitudes: np.ndarray, top_peaks: int, rpm: float | None) -> list[dict[str, float]]:
    if frequencies.size < 2 or amplitudes.size < 2:
        return []
    peak_indices, _ = signal.find_peaks(amplitudes)
    if peak_indices.size == 0:
        peak_indices = np.array([int(np.argmax(amplitudes[1:]) + 1)])
    ordered = peak_indices[np.argsort(amplitudes[peak_indices])[::-1]][:top_peaks]
    return [_with_order(float(frequencies[index]), float(amplitudes[index]), rpm) for index in ordered]


def _with_order(frequency_hz: float, amplitude: float, rpm: float | None) -> dict[str, float]:
    row = {"frequency_hz": frequency_hz, "amplitude": amplitude}
    if rpm is not None and rpm > 0:
        row["order"] = frequency_hz / (rpm / 60.0)
    return row
```

```python
def _band_features(frequencies: np.ndarray, amplitudes: np.ndarray, sample_rate_hz: float) -> list[dict[str, float]]:
    nyquist = sample_rate_hz / 2.0
    edges = list(DEFAULT_BANDS_HZ) + [(5000.0, nyquist)]
    rows: list[dict[str, float]] = []
    total_energy = float(np.sum(amplitudes**2))
    for low, high in edges:
        clipped_low = max(0.0, low)
        clipped_high = min(high, nyquist)
        if clipped_high <= clipped_low:
            continue
        mask = (frequencies >= clipped_low) & (frequencies < clipped_high)
        energy = float(np.sum(amplitudes[mask] ** 2))
        rows.append(
            {
                "low_hz": clipped_low,
                "high_hz": clipped_high,
                "energy": energy,
                "rms": float(np.sqrt(np.mean(amplitudes[mask] ** 2))) if np.any(mask) else 0.0,
                "energy_ratio": float(energy / total_energy) if total_energy > 0 else 0.0,
            }
        )
    return rows
```

```python
def _analysis_notes(time_features: dict[str, float], frequency_features: dict[str, Any], values: np.ndarray) -> list[str]:
    notes: list[str] = []
    if time_features.get("crest_factor", 0.0) >= 5.0:
        notes.append("High crest factor suggests impulsive content.")
    if time_features.get("kurtosis", 0.0) >= 5.0:
        notes.append("High kurtosis suggests a sharp distribution or outlier impacts.")
    bands = frequency_features.get("bands", [])
    if bands:
        strongest = max(bands, key=lambda row: row["energy_ratio"])
        if strongest["energy_ratio"] >= 0.6:
            notes.append(
                f"Energy is concentrated in {strongest['low_hz']:.1f}-{strongest['high_hz']:.1f} Hz "
                f"with ratio {strongest['energy_ratio']:.3f}."
            )
    peaks = frequency_features.get("spectral_peaks", [])
    if len(peaks) >= 2 and peaks[0]["amplitude"] > 3.0 * max(peaks[1]["amplitude"], 1e-12):
        notes.append("The spectrum contains a dominant peak.")
    if float(np.std(values)) <= 1e-12:
        notes.append("The signal is near constant.")
    return notes
```

- [ ] **Step 4: Run feature tests**

Run:

```powershell
python -m pytest tests\test_vibration_features.py -v
```

Expected: PASS.

- [ ] **Step 5: Run all current tests**

Run:

```powershell
python -m pytest tests -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src\data_analysis\vibration_features.py tests\test_vibration_features.py
git commit -m "feat: extract vibration segment features"
```

---

### Task 4: JSON, Markdown, and PNG Report Writers

**Files:**
- Create: `src/data_analysis/vibration_report.py`
- Test: `tests/test_vibration_report.py`

**Interfaces:**
- Consumes: analysis dictionary from `analyze_segment(...)`
- Produces: `write_analysis_outputs(analysis: dict[str, Any], output_dir: Path | str) -> dict[str, Path]`
- Produces output paths with keys `json`, `markdown`, and `figures_dir`.

- [ ] **Step 1: Write report tests**

Create `tests/test_vibration_report.py`:

```python
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
    assert (outputs["figures_dir"] / "Dev1_ai0_waveform.png").exists()
    assert (outputs["figures_dir"] / "Dev1_ai0_spectrum.png").exists()
    assert (outputs["figures_dir"] / "Dev1_ai0_psd.png").exists()
    assert (outputs["figures_dir"] / "Dev1_ai0_envelope_spectrum.png").exists()

    loaded = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert "_spectrum" not in json.dumps(loaded)
    assert loaded["channels"][0]["frequency_domain"]["dominant_frequency_hz"] > 0.0

    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "# Vibration Analysis Report" in markdown
    assert "Dev1/ai0" in markdown
    assert "bearing fault" not in markdown.lower()
```

- [ ] **Step 2: Run report tests to verify they fail**

Run:

```powershell
python -m pytest tests\test_vibration_report.py -v
```

Expected: FAIL because `data_analysis.vibration_report` does not exist.

- [ ] **Step 3: Implement report writer**

Create `src/data_analysis/vibration_report.py` with these public functions:

```python
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def write_analysis_outputs(analysis: dict[str, Any], output_dir: Path | str) -> dict[str, Path]:
    root = Path(output_dir)
    figures_dir = root / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    for channel in analysis["channels"]:
        _write_channel_figures(channel, figures_dir)

    json_path = root / "vibration_analysis.json"
    markdown_path = root / "vibration_analysis.md"
    json_path.write_text(json.dumps(_json_safe(analysis), indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(_markdown_report(analysis), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path, "figures_dir": figures_dir}
```

Implement helper functions in the same file:

```python
def _safe_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")
    return safe or "channel"
```

```python
def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(inner) for key, inner in value.items() if not str(key).startswith("_")}
    if isinstance(value, list):
        return [_json_safe(inner) for inner in value]
    if isinstance(value, tuple):
        return [_json_safe(inner) for inner in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value
```

```python
def _write_channel_figures(channel: dict[str, Any], figures_dir: Path) -> None:
    safe = _safe_name(channel["channel"])
    frequency = channel["frequency_domain"].get("_spectrum", {}).get("frequency_hz")
    amplitude = channel["frequency_domain"].get("_spectrum", {}).get("amplitude")
    psd_frequency = channel["frequency_domain"].get("_psd", {}).get("frequency_hz")
    psd_power = channel["frequency_domain"].get("_psd", {}).get("power")
    envelope_frequency = channel["envelope"].get("_spectrum", {}).get("frequency_hz")
    envelope_amplitude = channel["envelope"].get("_spectrum", {}).get("amplitude")

    sample_count = channel["basic"]["sample_count"]
    sample_rate_hz = channel["basic"]["sample_rate_hz"]
    time_axis = np.arange(sample_count) / sample_rate_hz
    waveform = channel.get("_waveform")
    if waveform is not None:
        _line_plot(time_axis, waveform, "Time (s)", f"Amplitude ({channel['basic']['unit']})", channel["channel"], figures_dir / f"{safe}_waveform.png")

    if frequency is not None and amplitude is not None:
        _line_plot(frequency, amplitude, "Frequency (Hz)", "Amplitude", f"{channel['channel']} amplitude spectrum", figures_dir / f"{safe}_spectrum.png")
    if psd_frequency is not None and psd_power is not None:
        _line_plot(psd_frequency, psd_power, "Frequency (Hz)", "PSD", f"{channel['channel']} Welch PSD", figures_dir / f"{safe}_psd.png", yscale="log")
    if envelope_frequency is not None and envelope_amplitude is not None:
        _line_plot(envelope_frequency, envelope_amplitude, "Frequency (Hz)", "Amplitude", f"{channel['channel']} envelope spectrum", figures_dir / f"{safe}_envelope_spectrum.png")
```

Add `_line_plot` and `_markdown_report`:

```python
def _line_plot(x: Any, y: Any, xlabel: str, ylabel: str, title: str, path: Path, yscale: str = "linear") -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(np.asarray(x), np.asarray(y), linewidth=0.8)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_yscale(yscale)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
```

```python
def _markdown_report(analysis: dict[str, Any]) -> str:
    lines = [
        "# Vibration Analysis Report",
        "",
        "## Input",
        "",
        f"- Path: `{analysis['input']['path']}`",
        f"- Sample rate: `{analysis['input']['sample_rate_hz']}` Hz",
        f"- Unit: `{analysis['input']['unit']}`",
        f"- Channels: `{', '.join(analysis['selected_channels'])}`",
        "",
    ]
    if analysis.get("rpm") is not None:
        lines.extend(
            [
                f"- RPM: `{analysis['rpm']}`",
                f"- Rotating frequency: `{analysis['rotating_frequency_hz']}` Hz",
                "",
            ]
        )

    for channel in analysis["channels"]:
        safe = _safe_name(channel["channel"])
        lines.extend([f"## Channel `{channel['channel']}`", ""])
        feature_rows = [
            {"feature": key, "value": value}
            for key, value in channel["time_domain"].items()
            if isinstance(value, (int, float))
        ]
        if feature_rows:
            lines.append(pd.DataFrame(feature_rows).to_markdown(index=False))
            lines.append("")
        peaks = channel["frequency_domain"].get("spectral_peaks", [])
        if peaks:
            lines.extend(["### Spectral Peaks", "", pd.DataFrame(peaks).to_markdown(index=False), ""])
        envelope_peaks = channel["envelope"].get("spectral_peaks", [])
        if envelope_peaks:
            lines.extend(["### Envelope Spectral Peaks", "", pd.DataFrame(envelope_peaks).to_markdown(index=False), ""])
        if channel["analysis_notes"]:
            lines.extend(["### Analysis Notes", ""])
            lines.extend([f"- {note}" for note in channel["analysis_notes"]])
            lines.append("")
        if channel["warnings"]:
            lines.extend(["### Warnings", ""])
            lines.extend([f"- {warning}" for warning in channel["warnings"]])
            lines.append("")
        lines.extend(
            [
                "### Figures",
                "",
                f"- [Waveform](figures/{safe}_waveform.png)",
                f"- [Amplitude spectrum](figures/{safe}_spectrum.png)",
                f"- [Welch PSD](figures/{safe}_psd.png)",
                f"- [Envelope spectrum](figures/{safe}_envelope_spectrum.png)",
                "",
            ]
        )
    return "\n".join(lines)
```

Update `analyze_channel` in `src/data_analysis/vibration_features.py` so each channel includes waveform for plotting:

```python
    return {
        "channel": channel,
        "basic": _basic_info(channel, time_s, values, sample_rate_hz, unit),
        "time_domain": time_features,
        "frequency_domain": frequency_features,
        "envelope": envelope_features,
        "warnings": warnings,
        "analysis_notes": notes,
        "_waveform": clean_values,
    }
```

Also add `tabulate` to `pyproject.toml` dependencies because `pandas.DataFrame.to_markdown()` requires it:

```toml
dependencies = [
  "numpy",
  "scipy",
  "matplotlib",
  "pandas",
  "tabulate",
]
```

- [ ] **Step 4: Run report tests**

Run:

```powershell
python -m pytest tests\test_vibration_report.py -v
```

Expected: PASS.

- [ ] **Step 5: Run feature and report tests together**

Run:

```powershell
python -m pytest tests\test_vibration_features.py tests\test_vibration_report.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml src\data_analysis\vibration_features.py src\data_analysis\vibration_report.py tests\test_vibration_report.py
git commit -m "feat: write vibration analysis reports"
```

---

### Task 5: CLI Entry Point and End-to-End Behavior

**Files:**
- Create: `src/data_analysis/cli.py`
- Create: `scripts/analyze_vibration_npz_xz.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `read_vibration_segment`, `select_channels`, `analyze_segment`, `write_analysis_outputs`
- Produces: `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write CLI tests**

Create `tests/test_cli.py`:

```python
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
```

- [ ] **Step 2: Run CLI tests to verify they fail**

Run:

```powershell
python -m pytest tests\test_cli.py -v
```

Expected: FAIL because CLI files do not exist.

- [ ] **Step 3: Implement CLI module**

Create `src/data_analysis/cli.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from data_analysis.vibration_features import analyze_segment
from data_analysis.vibration_io import VibrationPayloadError, read_vibration_segment, select_channels
from data_analysis.vibration_report import write_analysis_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze a standard vibration .npz.xz segment.")
    parser.add_argument("input", type=Path, help="Path to a vibration .npz.xz segment")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for JSON, Markdown, and figures")
    parser.add_argument("--channel", default=None, help="Exact channel name to analyze")
    parser.add_argument("--rpm", type=float, default=None, help="Optional spindle speed in RPM")
    parser.add_argument("--top-peaks", type=int, default=10, help="Number of spectral peaks to report")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    output_dir = args.output_dir or Path(f"{args.input.stem}_analysis")

    try:
        segment = read_vibration_segment(args.input)
        selected = select_channels(segment, args.channel)
        analysis = analyze_segment(selected, rpm=args.rpm, top_peaks=args.top_peaks)
        outputs = write_analysis_outputs(analysis, output_dir)
    except VibrationPayloadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"JSON: {outputs['json']}")
    print(f"Markdown: {outputs['markdown']}")
    print(f"Figures: {outputs['figures_dir']}")
    return 0
```

Create `scripts/analyze_vibration_npz_xz.py`:

```python
from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from data_analysis.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI tests**

Run:

```powershell
python -m pytest tests\test_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run:

```powershell
python -m pytest tests -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src\data_analysis\cli.py scripts\analyze_vibration_npz_xz.py tests\test_cli.py
git commit -m "feat: add vibration analysis cli"
```

---

### Task 6: Usage Documentation and Final Validation

**Files:**
- Create: `docs/vibration_analysis_usage.md`

**Interfaces:**
- Consumes: CLI and output contract from prior tasks.
- Produces: user-facing usage documentation.

- [ ] **Step 1: Write usage documentation**

Create `docs/vibration_analysis_usage.md`:

```markdown
# Vibration Analysis Usage

This document describes how to analyze one standard vibration `.npz.xz` segment in the `DataAnalysis` branch.

## Analyze All Channels

```powershell
python scripts\analyze_vibration_npz_xz.py path\to\segment.npz.xz --output-dir analysis_out
```

## Analyze One Channel

```powershell
python scripts\analyze_vibration_npz_xz.py path\to\segment.npz.xz --channel Dev1/ai0 --output-dir analysis_out
```

## Add RPM for Order Information

```powershell
python scripts\analyze_vibration_npz_xz.py path\to\segment.npz.xz --rpm 6000 --output-dir analysis_out
```

`--rpm` adds rotating-frequency and order values to spectral peaks. It does not produce hard fault labels.

## Outputs

```text
analysis_out/
  vibration_analysis.json
  vibration_analysis.md
  figures/
    <channel_safe_name>_waveform.png
    <channel_safe_name>_spectrum.png
    <channel_safe_name>_psd.png
    <channel_safe_name>_envelope_spectrum.png
```

Use `vibration_analysis.json` for programmatic feature ingestion.
Use `vibration_analysis.md` and the PNG files for manual review.

## Interpretation Boundary

The analyzer reports features, spectral peaks, envelope peaks, band energy, order values, warnings, and neutral notes. It does not diagnose imbalance, looseness, bearing faults, misalignment, or other named mechanical faults.
```

- [ ] **Step 2: Run targeted validation**

Run:

```powershell
python -m pytest tests -v
```

Expected: PASS.

- [ ] **Step 3: Run compile check**

Run:

```powershell
python -m py_compile scripts\analyze_vibration_npz_xz.py src\data_analysis\__init__.py src\data_analysis\vibration_io.py src\data_analysis\vibration_features.py src\data_analysis\vibration_report.py src\data_analysis\cli.py
```

Expected: command exits with code 0.

- [ ] **Step 4: Run a synthetic end-to-end manual smoke test**

Run this PowerShell script:

```powershell
@'
from pathlib import Path
import numpy as np

from tests.helpers import write_npz_xz_segment

sample_rate_hz = 4096.0
time_s = np.arange(4096) / sample_rate_hz
data = np.vstack([
    np.sin(2.0 * np.pi * 128.0 * time_s),
    0.5 * np.sin(2.0 * np.pi * 256.0 * time_s),
])
write_npz_xz_segment(
    Path("tmp_vibration_smoke/segment.npz.xz"),
    data=data,
    sample_rate_hz=sample_rate_hz,
    channels=["Dev1/ai0", "Dev1/ai1"],
)
'@ | python -

python scripts\analyze_vibration_npz_xz.py tmp_vibration_smoke\segment.npz.xz --rpm 6000 --output-dir tmp_vibration_smoke\analysis
```

Expected output includes:

```text
JSON: tmp_vibration_smoke\analysis\vibration_analysis.json
Markdown: tmp_vibration_smoke\analysis\vibration_analysis.md
Figures: tmp_vibration_smoke\analysis\figures
```

- [ ] **Step 5: Remove temporary smoke-test output**

Run:

```powershell
Remove-Item -LiteralPath tmp_vibration_smoke -Recurse -Force
Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
```

Expected: `tmp_vibration_smoke` and generated `__pycache__` directories are removed.

- [ ] **Step 6: Commit**

```powershell
git add docs\vibration_analysis_usage.md
git commit -m "docs: add vibration analysis usage"
```

---

## Final Acceptance Check

- [ ] `python -m pytest tests -v` passes.
- [ ] `python -m py_compile ...` passes for every new source file.
- [ ] CLI can analyze a synthetic `.npz.xz` and produce JSON, Markdown, and PNG outputs.
- [ ] JSON does not include NumPy arrays under internal keys such as `_spectrum`.
- [ ] Markdown contains neutral notes and no hard fault diagnosis labels.
- [ ] Temporary smoke-test output and `__pycache__` directories are removed.
- [ ] Commits do not include unrelated existing worktree changes.

## Self-Review Notes

- Spec coverage: The plan covers `.npz.xz` loading, validation, all-channel default behavior, `--channel`, `--rpm`, time-domain features, frequency-domain features, envelope features, JSON output, Markdown output, PNG figures, CLI behavior, malformed-input errors, and tests without real hardware data.
- Marker scan: The plan contains no unresolved marker text and no unspecified edge-case instruction.
- Type consistency: The core flow uses `VibrationSegment -> analyze_segment(...) -> write_analysis_outputs(...)`, and all tasks use those same interfaces.
