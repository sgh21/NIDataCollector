# Architecture

## Purpose

The application controls three independent devices:

- NI cDAQ for vibration and RTD temperature acquisition.
- DAMX-8013 for NTC temperature acquisition.
- Spindle controller for speed commands and speed/current feedback.

The code is organized around one rule: hardware communication, acquisition orchestration, and UI rendering must not be mixed.

## Package Layout

```text
src/nidata_collector/
  config.py
  core/
    engine.py
    storage.py
  hardware/
    ni.py
    damx8013.py
    spindle.py
  ui/
    qt_app.py
scripts/
  run_monitor.py
  ni_probe.py
config/
  temperature_card.json
  spindle_control.json
```

## Responsibilities

`config.py`

Shared data models used by the UI, core, storage, and hardware adapters. This includes signal types, channel selections, acquisition settings, run configuration, and experiment metadata.

`hardware/ni.py`

NI-DAQmx boundary. It lists devices, reserves/releases network cDAQ chassis, returns system snapshots, and exposes helper models for reservation results. It does not know about the Qt UI or run folders.

`hardware/damx8013.py`

DAMX-8013 boundary. It owns the Modbus RTU CRC, request/response parsing, JSON config load/save, serial client, NTC R/B register sync, and temperature register reads.

`hardware/spindle.py`

Spindle boundary. It owns serial protocol frames, checksum handling, config load/save, spindle connect/read/set/stop operations, and spindle telemetry file writing.

`core/engine.py`

Acquisition lifecycle. It starts workers, isolates device failures, routes live data events, triggers recording, and stops monitoring. It calls hardware modules but does not import Qt.

`core/storage.py`

Run folder and segment persistence. It writes manifests, experiment metadata, sensor metadata, segment CSV files, and JSON sidecars.

`ui/qt_app.py`

Qt desktop app. It builds the interface, reads user settings, calls the acquisition controller, displays live plots, and handles spindle controls. It does not implement low-level protocol parsing.

## Device Isolation

The three device families are isolated at the core boundary:

- NI acquisition can fail without blocking DAMX-8013 NTC acquisition.
- DAMX-8013 serial failures stop only the NTC worker.
- Spindle connection/control is independent from acquisition start/stop.

The UI may show all devices together, but each device has its own communication path.

## Data Flow

```text
Qt UI settings
  -> RunConfiguration
  -> core.engine.AcquisitionController
  -> device workers
  -> live AcquisitionEvent data
  -> UI plots

Trigger
  -> core.storage.RunStorage
  -> per-signal SegmentWriter
  -> CSV/JSON files
```

## Configuration Flow

- `config/temperature_card.json` stores DAMX-8013 serial parameters and NTC R/B values.
- The UI `Temperature` tab controls common temperature acquisition parameters: sample rate, segment length, min/max degC.
- RTD-only parameters remain labeled as RTD-only.
- `config/spindle_control.json` stores spindle serial/protocol/polling defaults.

## Deleted Legacy Artifacts

The architecture cleanup removed historical diagnostic HTML reports, archived test data, the imported spindle reference GUI, and the one-off vibration acquisition script. The Git tag `baseline-before-architecture-refactor-20260707` preserves the full pre-cleanup state.
