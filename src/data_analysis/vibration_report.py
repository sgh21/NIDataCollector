from __future__ import annotations

import json
import re
from pathlib import Path
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


def _safe_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")
    return safe or "channel"


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


def _write_channel_figures(channel: dict[str, Any], figures_dir: Path) -> None:
    safe = _safe_name(channel["channel"])
    spectrum = channel["frequency_domain"].get("_spectrum", {})
    frequency = spectrum.get("frequency_hz")
    amplitude = spectrum.get("amplitude")
    psd = channel["frequency_domain"].get("_psd", {})
    psd_frequency = psd.get("frequency_hz")
    psd_power = psd.get("power")
    envelope_spectrum = channel["envelope"].get("_spectrum", {})
    envelope_frequency = envelope_spectrum.get("frequency_hz")
    envelope_amplitude = envelope_spectrum.get("amplitude")

    sample_count = channel["basic"]["sample_count"]
    sample_rate_hz = channel["basic"]["sample_rate_hz"]
    time_axis = np.arange(sample_count) / sample_rate_hz
    waveform = channel.get("_waveform")
    if waveform is not None and np.asarray(waveform).size:
        _line_plot(
            time_axis,
            waveform,
            "Time (s)",
            f"Amplitude ({channel['basic']['unit']})",
            channel["channel"],
            figures_dir / f"{safe}_waveform.png",
        )

    if frequency is not None and amplitude is not None:
        _line_plot(
            frequency,
            amplitude,
            "Frequency (Hz)",
            "Amplitude",
            f"{channel['channel']} amplitude spectrum",
            figures_dir / f"{safe}_spectrum.png",
        )
    if psd_frequency is not None and psd_power is not None:
        _line_plot(
            psd_frequency,
            psd_power,
            "Frequency (Hz)",
            "PSD",
            f"{channel['channel']} Welch PSD",
            figures_dir / f"{safe}_psd.png",
            yscale="log",
        )
    if envelope_frequency is not None and envelope_amplitude is not None:
        _line_plot(
            envelope_frequency,
            envelope_amplitude,
            "Frequency (Hz)",
            "Amplitude",
            f"{channel['channel']} envelope spectrum",
            figures_dir / f"{safe}_envelope_spectrum.png",
        )


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
