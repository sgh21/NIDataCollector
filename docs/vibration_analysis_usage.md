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
