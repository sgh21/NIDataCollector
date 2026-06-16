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

Supported modules in the current implementation:

- `NI 9234`: IEPE acceleration / vibration, saved in `g`
- `NI 9216`: RTD temperature, saved in `degC`

The UI separates channel selection into `Plot` and `Save`. A channel can be
plotted without being saved, saved without being plotted, or both. Vibration and
RTD temperature settings are configured independently, including sample rate,
segment samples, segment duration, expected measurement range, and signal-type
specific settings.

The live plot area has separate tabs for `Vibration` and `RTD Temperature`.
Plot refresh is intentionally faster than file segment rotation, and plotted
data is downsampled for display only. Saved CSV files still keep every acquired
sample in each segment.

For each signal type, `Sample rate Hz`, `Segment samples`, and `Segment seconds`
are linked:

- Editing sample rate or segment samples updates segment seconds.
- Editing segment seconds updates segment samples.
- During acquisition setup, sample rate and segment samples define the actual
  hardware-timed segment length.

Saved runs are written under `data/runs/run_YYYYMMDD_HHMMSS/` by default. Each
run contains a `manifest.json`, and each signal type has its own folder with
CSV segment files and matching JSON metadata files. Segment filenames include
signal type, sample rate, sample count, and start sample index.

The `time_s` column is generated as `sample_index / configured_sample_rate_hz`
from NI-DAQmx hardware-timed sample reads. The computer clock is only used for
file and run naming.

Segment writing runs on a separate writer thread per signal type. The DAQmx
read loop enqueues complete segment arrays with their hardware sample index and
sample rate; CSV/JSON serialization happens outside the read loop.
