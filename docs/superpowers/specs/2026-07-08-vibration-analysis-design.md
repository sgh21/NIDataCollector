# Vibration Segment Analysis Design

Date: 2026-07-08

## Goal

Build the first reusable vibration-analysis core for the `DataAnalysis` branch.

The feature must load one standard vibration `.npz.xz` segment, extract more information from the time-domain vibration signal, and produce both machine-readable and human-readable analysis outputs. The first version should be useful without any project acquisition code and should operate directly on the data format documented in `docs/data_migration_analysis.md`.

## Non-Goals

- Do not implement hard fault diagnosis labels such as imbalance, looseness, bearing fault, or misalignment.
- Do not require run-level metadata, spindle telemetry, bearing geometry, or historical baselines.
- Do not generate HTML reports.
- Do not depend on hardware acquisition modules from the original application branch.
- Do not make a notebook the primary implementation surface.

## Chosen Approach

Use a layered Python analysis pipeline:

```text
.npz.xz
-> read and validate payload
-> select all channels or one requested channel
-> preprocess each channel
-> extract time-domain, frequency-domain, envelope, band-energy, and optional order features
-> write JSON, Markdown, and PNG figures
```

This keeps the core algorithms reusable while still providing a simple command-line entry point for ad hoc analysis.

## Proposed File Layout

```text
src/data_analysis/
  __init__.py
  vibration_io.py
  vibration_features.py
  vibration_report.py
  cli.py
scripts/
  analyze_vibration_npz_xz.py
```

Responsibilities:

- `vibration_io.py`: read `.npz.xz`, disable pickle, validate required arrays, normalize scalar metadata, and select channels.
- `vibration_features.py`: compute preprocessing outputs and all numeric features.
- `vibration_report.py`: write JSON, Markdown, and figures.
- `cli.py`: parse CLI arguments and orchestrate the pipeline.
- `scripts/analyze_vibration_npz_xz.py`: thin script entry that calls the package CLI.

The algorithm modules must not depend on the CLI. This keeps them usable from notebooks, batch scripts, future run-level analyzers, or a GUI.

## Input Contract

The required input is one `.npz.xz` file whose internal NumPy payload contains:

- `time_s`
- `data`
- `channels`
- `sample_start_index`
- `sample_rate_hz`
- `signal_type`
- `unit`

Validation rules:

- `signal_type` must be `acceleration`.
- `data` must be two-dimensional with shape `(channel_count, sample_count)`.
- `len(time_s) == data.shape[1]`.
- `len(channels) == data.shape[0]`.
- `sample_rate_hz > 0`.
- `time_s` must be finite and generally increasing.
- Channel names are matched exactly for `--channel`.

## CLI Design

Example commands:

```powershell
python scripts\analyze_vibration_npz_xz.py path\to\segment.npz.xz --output-dir analysis_out
python scripts\analyze_vibration_npz_xz.py path\to\segment.npz.xz --channel Dev1/ai0 --rpm 6000 --output-dir analysis_out
```

Arguments:

- positional input path: required `.npz.xz` segment path.
- `--output-dir`: output directory, default can be derived from the input stem.
- `--channel`: optional exact channel name. If omitted, analyze all channels.
- `--rpm`: optional spindle speed. If present, add order information.
- `--top-peaks`: optional number of spectral peaks, default 10.

## Output Contract

Output directory:

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

`vibration_analysis.json` contains:

- input path and payload metadata
- selected channels
- per-channel basic information
- per-channel time-domain features
- per-channel frequency-domain features
- per-channel envelope features
- optional rpm and order information
- warnings
- neutral analysis notes

`vibration_analysis.md` contains:

- file and sampling summary
- per-channel feature summary
- spectral peak tables
- envelope peak tables
- neutral abnormality clues
- links to generated figures

Figures:

- waveform
- single-sided amplitude spectrum
- Welch PSD
- envelope spectrum

## Feature Set

### Basic Information

For each channel:

- `channel`
- `sample_rate_hz`
- `sample_count`
- `duration_s`
- `time_start_s`
- `time_end_s`
- `unit`
- finite-value ratio
- near-constant signal flag

### Time-Domain Features

- mean
- standard deviation
- RMS
- min
- max
- peak absolute value
- peak-to-peak
- absolute mean
- energy
- crest factor
- impulse factor
- shape factor
- clearance factor
- skewness
- kurtosis
- zero-crossing rate

### Frequency-Domain Features

- single-sided FFT amplitude spectrum
- Welch PSD
- dominant frequency
- top N spectral peaks
- spectral centroid
- spectral bandwidth
- spectral rolloff
- band energy and band RMS

Default bands:

```text
0-10 Hz
10-100 Hz
100-1000 Hz
1000-5000 Hz
5000 Hz-Nyquist
```

Bands above Nyquist are clipped or omitted.

### Envelope Features

Use demeaned signal and Hilbert envelope. If a later version adds configurable bandpass filtering, it should remain optional.

Envelope outputs:

- envelope RMS
- envelope kurtosis
- envelope spectrum top peaks

Envelope analysis provides impact and modulation clues but does not assert a bearing fault diagnosis.

### Optional RPM Features

If `--rpm` is provided:

- `rotating_frequency_hz = rpm / 60`
- every major spectral peak can include `order = frequency_hz / rotating_frequency_hz`
- notes may mention peaks near 1x, 2x, or 3x order

Order information is explanatory only and must not produce a hard fault label in version 1.

## Neutral Analysis Notes

The analyzer may emit notes such as:

- High crest factor suggests impulsive content.
- High kurtosis suggests a sharp distribution or outlier impacts.
- Energy is concentrated in low-frequency bands.
- The spectrum contains a dominant peak and possible harmonic structure.
- The signal is near constant or has too many non-finite values.

These notes must remain evidence-based and neutral. They should not name a specific mechanical fault.

## Error Handling

Hard errors:

- input file does not exist
- file cannot be decompressed or loaded
- required payload fields are missing
- `signal_type` is not `acceleration`
- invalid `data`, `time_s`, or `channels` shape
- non-positive `sample_rate_hz`
- requested channel does not exist

Warnings and partial results:

- signal is too short for reliable FFT, PSD, or envelope spectrum
- non-finite values are present but below the skip threshold
- `time_s` has small irregularities
- frequency bands are clipped because Nyquist is lower than a default band edge

If finite-value quality is too poor for a channel, skip that channel and record the reason in JSON and Markdown.

## Dependencies

Use:

- Python standard library
- `numpy`
- `scipy`
- `matplotlib`
- `pandas`

Rationale:

- `numpy` handles arrays and FFT basics.
- `scipy` provides Welch PSD, Hilbert envelope, peak detection, and statistics.
- `matplotlib` writes portable PNG figures.
- `pandas` simplifies tabular report assembly and future CSV integration.

## Testing Strategy

Tests should not depend on real hardware data.

Create synthetic `.npz.xz` fixtures with known properties:

- single-channel sine wave
- multi-channel sine wave with different frequencies
- signal with impulse spikes
- malformed payloads with missing fields or wrong shapes
- too-short signal

Checks:

- loader rejects malformed payloads with clear errors
- channel selection works
- dominant frequency for a synthetic sine wave is close to the true frequency
- RMS for a sine wave is close to theoretical RMS
- CLI writes JSON, Markdown, and expected figure files
- temporary test outputs and `__pycache__` are cleaned after validation

## Acceptance Criteria

The first implementation is complete when:

- a standard vibration `.npz.xz` can be analyzed with one CLI command
- all channels are analyzed by default
- `--channel` limits analysis to one channel
- `--rpm` adds order information without requiring it
- `vibration_analysis.json` is valid JSON and contains structured features
- `vibration_analysis.md` is readable without inspecting code
- PNG figures are generated for each analyzed channel
- malformed inputs fail with actionable messages
- tests cover loader validation, core numerical features, and CLI output generation
