# NIDataCollector

NIDataCollector is a desktop monitoring and recording tool for three independent devices:

- NI cDAQ: NI 9234 vibration and NI 9216 RTD temperature acquisition.
- DAMX-8013: two-channel NTC temperature acquisition over Modbus RTU.
- Spindle controller: serial speed control with speed/current feedback.

The implementation is intentionally split into hardware communication, acquisition core,
and UI code. See [docs/architecture.md](docs/architecture.md) and [docs/api.md](docs/api.md).

## Environment

```powershell
conda activate NI
python -m pip install -r requirements.txt
```

If Conda activation is inconvenient:

```powershell
E:\software\conda\envs\NI\python.exe -m pip install -r requirements.txt
```

## Run

Start the desktop app:

```powershell
E:\software\conda\envs\NI\python.exe scripts\run_monitor.py
```

Probe NI hardware without opening the UI:

```powershell
E:\software\conda\envs\NI\python.exe scripts\ni_probe.py
```

Run the app self-test:

```powershell
E:\software\conda\envs\NI\python.exe scripts\run_monitor.py --self-test
```

## Configuration

- `config/app_startup.json`: startup UI defaults for output folder, vibration settings, common temperature acquisition settings, plot windows/Y ranges, NTC alert threshold, spindle default target speed, device config paths, and per-channel metadata defaults.
- `config/temperature_card.json`: DAMX-8013 COM port, Modbus settings, NTC R/B values.
- `config/spindle_control.json`: spindle COM port, protocol addresses, polling and safety limits.
- Temperature sample rate, segment length, and temperature range initialize from `app_startup.json`, are set in the UI, and apply to both RTD and NTC channels.
- RTD-only settings in the UI are labeled with `RTD`: excitation current, R0, type, and wiring.

## Recording Model

`Start` begins live monitoring. `Trigger` creates a run directory and starts writing selected `Save` channels. `Stop` ends monitoring and flushes any partial segment.

Runs are written under `data/runs/run_YYYYMMDD_HHMMSS/` and include:

- `manifest.json`
- `experiment_record.csv`
- `spindle_info.csv`
- `sensor_info.csv`
- `segment_records.csv`
- compressed per-signal `.npz.xz` segment files with `time_s`, `data`, channel names, sample rate, signal type, and unit
- `segment_summary.csv` with fixed 1Hz trend features
- `trends/summary_overview.png` generated from the summary CSV
- optional `spindle_telemetry.csv/json` when the spindle is connected

## Hardware Independence

NI, DAMX-8013, and spindle control are isolated. If one device is offline, the other selected devices can still be used. Mixed NI + DAMX monitoring will continue with the available device group when one side fails.
