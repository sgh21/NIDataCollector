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


def test_channels_must_be_1d(tmp_path):
    path = tmp_path / "bad_channels.npz.xz"
    buffer = io.BytesIO()
    np.savez(
        buffer,
        time_s=np.array([0.0, 0.001]),
        data=np.array([[1.0, 2.0], [3.0, 4.0]]),
        channels=np.array([["Dev1/ai0"], ["Dev1/ai1"]]),
        sample_start_index=np.asarray(0),
        sample_rate_hz=np.asarray(1000.0),
        signal_type=np.asarray("acceleration"),
        unit=np.asarray("g"),
    )
    with lzma.open(path, "wb") as handle:
        handle.write(buffer.getvalue())

    with pytest.raises(VibrationPayloadError, match="channels must be 1D"):
        read_vibration_segment(path)


def test_channels_must_be_string_like(tmp_path):
    path = tmp_path / "bad_channels_numeric.npz.xz"
    buffer = io.BytesIO()
    np.savez(
        buffer,
        time_s=np.array([0.0, 0.001]),
        data=np.array([[1.0, 2.0]]),
        channels=np.array([1, 2]),
        sample_start_index=np.asarray(0),
        sample_rate_hz=np.asarray(1000.0),
        signal_type=np.asarray("acceleration"),
        unit=np.asarray("g"),
    )
    with lzma.open(path, "wb") as handle:
        handle.write(buffer.getvalue())

    with pytest.raises(VibrationPayloadError, match="channels.*string"):
        read_vibration_segment(path)


@pytest.mark.parametrize("sample_rate", [np.nan, np.inf, -np.inf])
def test_sample_rate_hz_must_be_finite(tmp_path, sample_rate):
    path = tmp_path / "bad_rate.npz.xz"
    buffer = io.BytesIO()
    np.savez(
        buffer,
        time_s=np.array([0.0, 0.001]),
        data=np.array([[1.0, 2.0]]),
        channels=np.array(["Dev1/ai0"]),
        sample_start_index=np.asarray(0),
        sample_rate_hz=np.asarray(sample_rate),
        signal_type=np.asarray("acceleration"),
        unit=np.asarray("g"),
    )
    with lzma.open(path, "wb") as handle:
        handle.write(buffer.getvalue())

    with pytest.raises(VibrationPayloadError, match="sample_rate_hz must be finite"):
        read_vibration_segment(path)
