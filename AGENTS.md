# AGENTS.md

This file captures the durable project knowledge and working rules for the `DataAnalysis` branch of `NIDataCollector`.

## Working Rules

* At the start of a new session in this repository, read root `AGENTS.md` / `AGENT.md` before scanning files, reading code, editing files, or running commands.
* Put conclusions first after experiments, debugging, or validation.
* Do not generate final HTML reports with Python scripts. Use Markdown, JSON, PNG, or manual summaries instead.
* Reuse existing code where possible and prefer the smallest practical change.
* Clean temporary code, intermediate files, `.pytest_cache`, and `__pycache__` after validation.
* If test or validation output creates temporary folders such as `analysis_out/` or `tmp_vibration_smoke/`, ask whether to keep them when they are user-facing results; otherwise clean them.
* After experiments or validation, ask whether the conclusions should be added to `AGENTS.md`. If the user says yes, update this file.
* For temporary test code, ask whether to keep it after testing. If not kept, remove it.
* For image generation tasks, prefer ChatGPT image generation instead of local PIL drawing.
* Do not include unrelated user changes in commits. In this branch, user-local changes have appeared in `docs/data_migration_analysis.md`, `.gitignore`, `.vscode/`, and `.superpowers/`; handle them deliberately.

## Branch Purpose

* Current branch: `DataAnalysis`.
* This branch is an orphan-style analysis branch intended to document and implement data reading and vibration-analysis tooling independent of the original acquisition application code.
* The branch should help future users understand:
  * how the experiment data was collected,
  * how each run is organized,
  * how raw `.npz.xz` segments are encoded,
  * how to read and validate vibration segments,
  * how to extract richer information from time-domain vibration signals.
* Do not reintroduce unnecessary original application architecture discussion into the data-analysis docs unless it directly affects data interpretation.

## Key Documentation

* `docs/data_migration_analysis.md`: primary data-format and data-collection explanation for the `DataAnalysis` branch.
* `docs/vibration_analysis_usage.md`: user guide for the vibration segment analyzer.
* `docs/superpowers/specs/2026-07-08-vibration-analysis-design.md`: approved design spec for the analyzer.
* `docs/superpowers/plans/2026-07-08-vibration-analysis.md`: implementation plan used for the analyzer.

## Standard Data Format

The standard data unit is one run directory:

```text
data/runs/run_YYYYMMDD_HHMMSS/
  manifest.json
  experiment_record.csv
  spindle_info.csv
  sensor_info.csv
  segment_records.csv
  segment_summary.csv
  spindle_telemetry.csv
  spindle_telemetry.json
  acceleration_25600Hz_256000samples/
    *.npz.xz
  temperature_ntc_10Hz_100samples/
    *.npz.xz
  temperature_rtd_10Hz_100samples/
    *.npz.xz
  trends/
    summary_overview.png
```

Not every run must contain every signal type. Analysis code should detect available data from files and metadata instead of assuming all devices were online.

## Raw Segment `.npz.xz` Contract

Raw vibration and temperature segments are stored as `.npz.xz`:

* The inner payload is NumPy `npz`.
* The outer compression is `lzma/xz`.
* Read with `np.load(..., allow_pickle=False)`.
* Do not use pickle for experiment data storage.

Required arrays:

| Field | Expected shape | Meaning |
| --- | --- | --- |
| `time_s` | `(sample_count,)` | run-relative time in seconds |
| `data` | `(channel_count, sample_count)` | channel-major sample data |
| `channels` | `(channel_count,)` | channel names aligned to `data` axis 0 |
| `sample_start_index` | scalar | first sample index in this signal stream |
| `sample_rate_hz` | scalar | segment sample rate |
| `signal_type` | scalar string | e.g. `acceleration` |
| `unit` | scalar string | e.g. `g` or `degC` |

Alignment rules:

* `time_s[i]` corresponds to `data[:, i]`.
* `len(time_s) == data.shape[1]`.
* `len(channels) == data.shape[0]`.
* `time_s` is the source of truth for timing. Do not reconstruct timing only from filenames, sample index, or sample rate unless migrating old data with no better timing source.

## Signal Types and Defaults

| Signal | `signal_type` | Source | Default sample rate | Default segment length | Unit |
| --- | --- | --- | ---: | ---: | --- |
| Vibration | `acceleration` | NI 9234 | `25600 Hz` | `256000 samples` | `g` |
| NTC temperature | `temperature_ntc` | DAMX-8013 two-channel NTC card | `10 Hz` | `100 samples` | `degC` |
| RTD temperature | `temperature_rtd` | NI 9216 | `10 Hz` | `100 samples` | `degC` |
| Spindle speed/current | `spindle_speed` / `spindle_current` | spindle controller telemetry | config-dependent | CSV telemetry | `rpm` / `A` |

Default vibration and temperature raw segments are about 10 seconds long.

## Time Axis and Synchronization

* `time_s` is run-relative seconds.
* NI vibration and RTD data use the NI sampling clock.
* DAMX-8013 NTC uses serial polling count and configured sampling rate.
* Spindle telemetry uses monotonic elapsed time from the telemetry recorder.
* For trend analysis, 1 second windows are usually enough.
* For sub-second cross-device phase or latency analysis, inspect the specific run trigger order, device latency, and clock source.
* The current raw format does not store an absolute wall-clock timestamp per sample.

## Summary and Trends

`segment_summary.csv` is the preferred source for trends, dashboards, and long-duration thermal analysis.

* Summary windows are fixed at 1 second even when raw `.npz.xz` files are 10 second segments.
* Vibration summary features: `mean_abs`, `max`, `min`.
* Temperature summary features: `mean`, `max`, `min`.
* Spindle speed/current summary features: `mean`, `max`, `min`.
* Trend overview should use four stacked subplots:
  * vibration channels together,
  * temperature channels together,
  * spindle speed alone,
  * spindle current alone.
* Do not plot spindle speed and current on the same subplot or twin Y axis.

## 6000 rpm Experiment Context

Main known analyzed run:

```text
data/runs/run_20260707_185820
```

Known conclusions from the 2026-07-07 compressed-storage integration experiment:

* The run supports the standard `.npz.xz` data format and the 6000 rpm standard acquisition flow.
* It stepped through 500, 1000, 2000, 3000, 4000, 5000, 6000, 7000, and 8000 rpm, then returned to 6000 rpm.
* For formal 6000 rpm standard acquisition, do not overshoot to 8000 rpm. Ramp and stabilize directly at 6000 rpm.
* Final switch to 6000 rpm occurred at about `121.015 s`; actual speed reached 6000 rpm near `121.5 s`.
* 6000 rpm hold lasted about `582 s`, or `9.7 min`.
* NTC rose from about `23.98 degC` at 6000 rpm stabilization to about `26.55 degC` before deceleration.
* NTC is the primary temperature and safety/thermal-stability channel.
* 6000 rpm thermal stabilization estimate from NTC: at least 8 min after speed stabilizes; use `8.5-9 min` for a conservative wait.
* RTD should be recorded but not used as the primary thermal-stability gate until placement/sensitivity improves.
* Spindle current currently has negative values and spikes. Record it, but do not use it for load, thermal stability, or data-validity decisions until register meaning, scaling, and sign are verified.

## Vibration Analysis Tool

The reusable analyzer was added on the `DataAnalysis` branch.

Main files:

* `src/data_analysis/vibration_io.py`: `.npz.xz` loading, payload validation, exact channel selection.
* `src/data_analysis/vibration_features.py`: time-domain, frequency-domain, Welch PSD, envelope, band-energy, optional order features, neutral notes.
* `src/data_analysis/vibration_report.py`: JSON, Markdown, and PNG report generation.
* `src/data_analysis/cli.py`: command-line orchestration.
* `scripts/analyze_vibration_npz_xz.py`: thin script wrapper.
* `tests/`: synthetic `.npz.xz` fixtures and unit/CLI tests.

Example use:

```powershell
python scripts\analyze_vibration_npz_xz.py path\to\segment.npz.xz --output-dir analysis_out
python scripts\analyze_vibration_npz_xz.py path\to\segment.npz.xz --channel Dev1/ai0 --rpm 6000 --output-dir analysis_out
```

Outputs:

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

The analyzer reports features and neutral clues. It must not emit hard fault labels such as imbalance, looseness, bearing fault, or misalignment as generated diagnoses.

Implemented feature groups:

* Basic metadata: channel, sample rate, sample count, duration, unit, finite-value ratio, near-constant flag.
* Time-domain features: mean, standard deviation, RMS, min, max, peak absolute value, peak-to-peak, absolute mean, energy, crest factor, impulse factor, shape factor, clearance factor, skewness, kurtosis, zero-crossing rate.
* Frequency-domain features: FFT amplitude spectrum, Welch PSD, dominant frequency, spectral peaks, spectral centroid, spectral bandwidth, spectral rolloff, band energy and band RMS.
* Envelope features: Hilbert envelope RMS, finite guarded envelope kurtosis, envelope spectrum peaks.
* Optional RPM features: `rotating_frequency_hz = rpm / 60` and order values for major peaks.

Important implementation choices:

* Loader wraps malformed payloads in `VibrationPayloadError` so CLI errors are actionable.
* `sample_rate_hz`, `rpm`, and `top_peaks` are validated.
* `rpm` must be finite and positive when supplied.
* `top_peaks` must be non-negative.
* JSON report writing uses strict non-NaN behavior.
* Non-finite samples above the usable threshold are interpolated in index space to preserve sample count and uniform sampling for spectral analysis.
* If finite-value ratio is below `0.5`, feature groups are skipped for that channel and warnings are recorded.
* Low Nyquist clipping/omission of default frequency bands should emit warnings.
* Figure filenames include channel index to avoid collisions after sanitization.
* Markdown links only to figures that were actually generated.

## Dependencies

`pyproject.toml` and `requirements.txt` define the analysis dependencies.

Runtime dependencies:

* `numpy`
* `scipy`
* `matplotlib`
* `pandas`
* `tabulate`

Test dependency:

* `pytest`

Install command:

```powershell
python -m pip install -r requirements.txt
```

## NI Environment

The path from older notes, `E:\software\conda\envs\NI\python.exe`, does not exist on the current machine because there is no `E:` drive.

Current verified NI environment:

```text
D:\Softwares\miniconda3\envs\NI\python.exe
```

Verification on 2026-07-08:

```text
Python 3.11.4
numpy 2.4.6
scipy 1.17.1
matplotlib 3.11.0
pandas 3.0.3
tabulate 0.10.0
pytest 9.1.1
```

The NI environment originally had `numpy`, `scipy`, `matplotlib`, and `pandas`, but lacked `tabulate` and `pytest`. These were installed with:

```powershell
D:\Softwares\miniconda3\envs\NI\python.exe -m pip install -r requirements.txt
```

## Validation Commands

Use the verified NI Python when checking this branch:

```powershell
D:\Softwares\miniconda3\envs\NI\python.exe -m pytest tests -v
```

Expected current result:

```text
35 passed
```

Compile check:

```powershell
D:\Softwares\miniconda3\envs\NI\python.exe -m py_compile scripts\analyze_vibration_npz_xz.py src\data_analysis\__init__.py src\data_analysis\vibration_io.py src\data_analysis\vibration_features.py src\data_analysis\vibration_report.py src\data_analysis\cli.py
```

After running tests or smoke checks, remove:

```text
__pycache__/
.pytest_cache/
tmp_vibration_smoke/
```

Handle `analysis_out/` carefully. It may be a user-facing generated report. Ask before deleting if the user is inspecting it.

## Git and Branch Notes

* `DataAnalysis` was reset to the remote orphan history on 2026-07-08.
* Old local pre-orphan history was backed up as `backup/DataAnalysis-before-orphan-20260708-101550`.
* The vibration-analysis feature was implemented through commits after `origin/DataAnalysis`:
  * `926e698` design spec,
  * `fbbd3c0` implementation plan,
  * `e26d30f` fixture writer,
  * `cd888bc`, `cb80f95`, `7a1a605` loader and validation,
  * `f3d2cf1`, `c979102`, `7aaa13e`, `b6a96a9` feature extraction and hardening,
  * `ad70ba6` reports,
  * `d1f6c5b` CLI,
  * `3653724` usage docs,
  * `724f262` final edge-case hardening,
  * `d12b2d0` requirements.
* Final branch review after `724f262` found no Critical, Important, or Minor issues and said ready to merge.
* As of dependency verification, the branch was ahead of `origin/DataAnalysis` by 15 commits.

