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
