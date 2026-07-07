# Functional and API Reference

## Entry Points

Run the desktop app:

```powershell
E:\software\conda\envs\NI\python.exe scripts\run_monitor.py
```

Probe NI hardware:

```powershell
E:\software\conda\envs\NI\python.exe scripts\ni_probe.py
```

Run a non-GUI app self-test:

```powershell
E:\software\conda\envs\NI\python.exe scripts\run_monitor.py --self-test
```

## Core API

```python
from nidata_collector.core.engine import AcquisitionController

controller = AcquisitionController()
controller.start(run_config)
run_dir = controller.trigger_recording()
controller.stop()
```

Important properties:

- `controller.events`: queue of `AcquisitionEvent`.
- `controller.running`: live monitoring state.
- `controller.recording`: recording state after trigger.
- `controller.has_saves`: true when at least one active group has Save enabled.

## Configuration Models

```python
from nidata_collector.config import (
    RunConfiguration,
    AcquisitionGroup,
    ChannelSelection,
    SignalType,
    AccelerationSettings,
    TemperatureRtdSettings,
    TemperatureNtcSettings,
)
```

Signal types:

- `SignalType.ACCELERATION`: NI 9234 vibration in `g`.
- `SignalType.TEMPERATURE_RTD`: NI 9216 RTD temperature in `degC`.
- `SignalType.TEMPERATURE_NTC`: DAMX-8013 NTC temperature in `degC`.

## NI Hardware API

```python
from nidata_collector.hardware.ni import (
    get_system_snapshot,
    reserve_network_devices,
    unreserve_network_devices,
)
```

Use this module only for NI-DAQmx device discovery and network reservation. It does not start the Qt app and does not write run data.

## DAMX-8013 API

```python
from pathlib import Path
from nidata_collector.hardware.damx8013 import (
    Damx8013Client,
    load_temperature_card_config,
    save_temperature_card_config,
    temperature_ntc_settings_from_config,
)

config = load_temperature_card_config(Path("config/temperature_card.json"))
settings = temperature_ntc_settings_from_config(config)

with Damx8013Client(settings) as client:
    client.sync_ntc_parameters()
    temperatures = client.read_temperatures()
```

Protocol helpers in the same module cover Modbus CRC, read-holding-register requests, write-single-register requests, and temperature response parsing.

## Spindle API

```python
from pathlib import Path
from nidata_collector.hardware.spindle import (
    SpindleDevice,
    load_spindle_config,
)

config = load_spindle_config(Path("config/spindle_control.json"))
device = SpindleDevice(config)
try:
    device.connect()
    reading = device.read()
    device.set_speed_rpm(500)
    device.stop()
finally:
    device.close()
```

`SpindleReading` contains:

- `speed_rpm`
- `current_a`
- `speed_ok`
- `current_ok`

## Storage API

```python
from nidata_collector.core.storage import RunStorage, SegmentWriter
```

`RunStorage` creates the run folder and metadata files from a `RunConfiguration`.

`SegmentWriter` writes one acquisition group segment at a time. The acquisition controller normally owns this; UI code should not call it directly.

## UI Behavior

- `Start`: starts live monitoring only.
- `Trigger`: creates a run folder and starts saving selected Save channels.
- `Stop`: stops workers and flushes partial segments.
- Temperature plots combine NTC and RTD curves in one tab. NTC curves are ordered before RTD curves.
- The large NTC badge is safety-oriented and uses only NTC temperatures.

## Validation Commands

```powershell
E:\software\conda\envs\NI\python.exe -B -m unittest discover -s tests
E:\software\conda\envs\NI\python.exe -B -m py_compile scripts\run_monitor.py scripts\ni_probe.py
```
