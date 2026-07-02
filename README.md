# NIDataCollector

Minimal NI-DAQmx vibration acquisition starter project for a cDAQ-9185 chassis
with an NI 9234 dynamic signal acquisition module.

## Environment

Use the existing Conda environment:

```powershell
conda activate NI
python -m pip install -r requirements.txt
```

If Conda activation is inconvenient, use the environment Python directly:

```powershell
D:\Softwares\miniconda3\envs\NI\python.exe -m pip install -r requirements.txt
```

## Probe NI Hardware

The network cDAQ chassis must be reserved before NI-DAQmx tasks can run.
The probe script reserves network devices by default without overriding another
host reservation.

```powershell
D:\Softwares\miniconda3\envs\NI\python.exe scripts\ni_probe.py
```

Expected hardware in this setup:

- Chassis: `cDAQ9185-254D6AA`
- Module: `cDAQ9185-254D6AAMod1`
- AI channels: `cDAQ9185-254D6AAMod1/ai0` through `ai3`

## Acquire Vibration Data

Acquire a short acceleration waveform from the first detected NI 9234 channel:

```powershell
D:\Softwares\miniconda3\envs\NI\python.exe scripts\acquire_vibration.py --duration 2 --sample-rate 5120
```

Acquire all four NI 9234 channels:

```powershell
D:\Softwares\miniconda3\envs\NI\python.exe scripts\acquire_vibration.py --all-channels --duration 2 --sample-rate 5120
```

Acquire a specific channel and set accelerometer sensitivity:

```powershell
D:\Softwares\miniconda3\envs\NI\python.exe scripts\acquire_vibration.py --channel cDAQ9185-254D6AAMod1/ai0 --sensor-sensitivity-mv-per-g 100
```

Outputs are written to `data/`:

- `*.csv`: time column plus acceleration values in g
- `*.png`: waveform plot
- `*.json`: acquisition metadata and basic channel statistics

Adjust `--sensor-sensitivity-mv-per-g`, `--min-g`, `--max-g`, and
`--excitation-current` to match the accelerometer datasheet. The acquisition
script uses `--coupling AC` and `--settle-seconds 0.5` by default so startup
transients from IEPE excitation are discarded before saving data.

## Simulated Pipeline Check

This verifies CSV and PNG generation without hardware access:

```powershell
D:\Softwares\miniconda3\envs\NI\python.exe scripts\acquire_vibration.py --simulate --duration 1
```

## Desktop Monitoring App

Run the full desktop acquisition software:

```powershell
D:\Softwares\miniconda3\envs\NI\python.exe scripts\run_monitor.py
```

Non-GUI hardware check:

```powershell
D:\Softwares\miniconda3\envs\NI\python.exe scripts\run_monitor.py --self-test
```

If the cDAQ chassis was previously reserved by another host and you are sure no
other computer is actively acquiring from it:

```powershell
D:\Softwares\miniconda3\envs\NI\python.exe scripts\run_monitor.py --self-test --override-reservation
```

Supported modules in the current implementation:

- `NI 9234`: IEPE acceleration / vibration, saved in `g`
- `NI 9216`: RTD temperature, saved in `degC`

The desktop app uses `PySide6` and `pyqtgraph` for live plotting. The plotting
widgets reuse curve objects and update them with `setData()`, which avoids the
Matplotlib redraw path that is too slow for high-rate vibration signals.
For real-time monitoring, each channel uses a fixed-length ring buffer and a
fixed X/Y view range. New samples enter at the right side of the trace and older
samples scroll left; the plot does not auto-rescale Y during acquisition.

The UI separates channel selection into `Plot` and `Save`. A channel can be
plotted without being saved, saved without being plotted, or both. Vibration and
RTD temperature settings are configured independently, including sample rate,
segment samples, segment duration, expected measurement range, and signal-type
specific settings.

`Start` only starts live monitoring and plotting. It does not create a run
folder or write data files. Press `Trigger` after the spindle reaches the
desired condition to start recording the selected `Save` channels. Recording
segments are cut by the configured segment sample count / segment duration, and
the saved sample index and `time_s` column start from the trigger point. `Stop`
ends monitoring and flushes the final partial recording segment, if any.

The status bar shows whether the app is monitoring or recording. During
recording it displays elapsed recording time and the number of segment files
that have been written.

The live plot area has separate tabs for `Vibration` and `RTD Temperature`.
Each signal type has its own display window length and manual Y-axis range, so
temperature can use a much longer trend window than high-rate vibration. Plot
refresh is intentionally faster than file segment rotation, and plotted data is
downsampled for display only. Saved CSV files still keep every acquired sample
in each segment.

The `Record` settings tab captures experiment metadata following the spindle
monitoring record template: spindle information, operating condition, speed,
manual temperature checkpoints, exceptions, and follow-up labels. Each channel
row also has a `Meta` button for sensor ID, measurement position, direction,
and mounting method.

For each signal type, `Sample rate Hz`, `Segment samples`, and `Segment seconds`
are linked:

- Editing sample rate or segment samples updates segment seconds.
- Editing segment seconds updates segment samples.
- During acquisition setup, sample rate and segment samples define the actual
  hardware-timed segment length.

Saved runs are written under `data/runs/run_YYYYMMDD_HHMMSS/` by default. Each
run contains a `manifest.json`, `experiment_record.csv`, `spindle_info.csv`,
`sensor_info.csv`, and `segment_records.csv`. Each signal type has its own
folder with CSV segment files and matching JSON metadata files. Segment
filenames include signal type, sample rate, sample count, and start sample
index. Segment JSON files also embed the experiment record and the per-segment
template row for direct traceability.

The `time_s` column is generated as `sample_index / configured_sample_rate_hz`
from NI-DAQmx hardware-timed sample reads. The computer clock is only used for
file and run naming.

Segment writing runs on a separate writer thread per signal type. The DAQmx
read loop enqueues complete segment arrays with their hardware sample index and
sample rate; CSV/JSON serialization happens outside the read loop.

The GUI keeps the cDAQ network reservation while the application is open so
starting and stopping acquisition remains continuous. Pressing Stop only stops
DAQmx tasks; the reservation is released when the GUI window is closed.
