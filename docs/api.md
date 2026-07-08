# NIDataCollector API Quickstart Guide

This document is written in a quickstart style: start with the shortest useful
examples, then use the function reference when you need exact inputs, outputs,
and side effects.

NIDataCollector has three independent hardware boundaries:

- NI cDAQ: NI 9234 vibration and NI 9216 RTD temperature acquisition.
- DAMX-8013: two NTC temperature channels over Modbus RTU.
- Spindle controller: serial speed control plus speed/current feedback.

The public API is split into these layers:

```text
nidata_collector.config          Shared data models
nidata_collector.ui.startup_config  Qt startup defaults
nidata_collector.hardware.ni     NI discovery and reservation
nidata_collector.hardware.damx8013  DAMX-8013 protocol and serial client
nidata_collector.hardware.spindle   Spindle protocol, control, telemetry
nidata_collector.core.engine     Monitoring, live events, recording trigger
nidata_collector.core.storage    Run folders, compressed segments, summaries, trends
```

## 1. Install and Run

Install dependencies in the NI Conda environment:

```powershell
conda activate NI
python -m pip install -r requirements.txt
```

Run the desktop monitor:

```powershell
E:\software\conda\envs\NI\python.exe scripts\run_monitor.py
```

Probe NI hardware without opening the Qt UI:

```powershell
E:\software\conda\envs\NI\python.exe scripts\ni_probe.py
```

Run the built-in NI self-test entry point:

```powershell
E:\software\conda\envs\NI\python.exe scripts\run_monitor.py --self-test
```

## 2. Minimal Non-GUI Example

This example starts live monitoring, triggers recording, waits, then stops.
Replace the channel names with the actual physical channels shown by the UI or
`scripts\ni_probe.py`.

```python
import queue
import time
from pathlib import Path

from nidata_collector.config import (
    AccelerationSettings,
    AcquisitionGroup,
    ChannelSelection,
    RunConfiguration,
    SignalType,
)
from nidata_collector.core.engine import AcquisitionController

channels = [
    ChannelSelection(
        physical_name="cDAQ9185-254D6AAMod1/ai0",
        device_name="cDAQ9185-254D6AA",
        product_type="NI 9234",
        signal_type=SignalType.ACCELERATION,
        visualize=True,
        save=True,
    )
]

settings = AccelerationSettings(
    sample_rate_hz=25600.0,
    segment_samples=256000,
    segment_seconds=10.0,
    min_value=-50.0,
    max_value=50.0,
    sensitivity_mv_per_g=100.0,
    excitation_current_a=0.004,
    coupling="AC",
    settle_seconds=5.0,
)

config = RunConfiguration(
    output_dir=Path("data/runs"),
    groups=[
        AcquisitionGroup(
            signal_type=SignalType.ACCELERATION,
            channels=channels,
            settings=settings,
        )
    ],
)

controller = AcquisitionController()
controller.start(config)
run_dir = controller.trigger_recording()
print(f"recording to {run_dir}")

deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    try:
        event = controller.events.get(timeout=0.1)
    except queue.Empty:
        continue
    print(event.kind, event.group, event.message)

controller.stop()
while not controller.poll_finished():
    time.sleep(0.1)
controller.shutdown()
```

## 3. Configuration Files

### Startup UI defaults

File: `config/app_startup.json`

This file controls values shown when the Qt UI starts. Runtime edits in the UI
still override these values for the current session.

Top-level sections:

| Key | Purpose |
| --- | --- |
| `output` | Default run folder and NI reservation override checkbox. |
| `vibration` | NI 9234 acquisition defaults and vibration plot defaults. |
| `temperature` | Common RTD/NTC sample rate, segment size, temperature limits, plot window, plot Y range, and NTC alert threshold. |
| `temperature.rtd` | RTD-only excitation, R0, RTD type, and wiring. |
| `temperature.ntc.config_path` | Path to the DAMX-8013 device config. |
| `spindle` | Path to the spindle device config, default target rpm, and spindle plot window. |
| `record` | Metadata placeholders shown on the Record tab. |
| `channel_metadata` | Per-physical-channel default values for the channel Meta dialog. |

Load it from Python:

```python
from pathlib import Path
from nidata_collector.ui.startup_config import load_startup_config, resolve_startup_path

root = Path.cwd()
startup = load_startup_config(root / "config" / "app_startup.json")
temperature_card_path = resolve_startup_path(root, startup.temperature.ntc.config_path)
spindle_config_path = resolve_startup_path(root, startup.spindle.config_path)
```

`channel_metadata` is keyed by the exact physical channel name shown in the
channel list, for example `COM9/ntc1` or `cDAQ9185-254D6AAMod1/ai0`.
Each entry can contain:

- `sensor_id`
- `measurement_position`
- `direction`
- `mounting_method`

The Qt UI loads these values when channels are refreshed. When the UI exits,
the current channel Meta values are saved back to `channel_metadata` without
rewriting the hardware protocol config files.

### DAMX-8013 device config

File: `config/temperature_card.json`

This file controls DAMX-8013 serial communication and NTC hardware parameters.

Important fields:

| Key | Meaning |
| --- | --- |
| `model` | Must be `DAMX-8013`. |
| `port` | Serial port, for example `COM9`. |
| `slave_id` | Modbus RTU slave id, 1..247. |
| `baudrate`, `data_bits`, `parity`, `stop_bits`, `timeout_s` | Serial settings. |
| `channel_count` | Must be 2. |
| `r_kohms` | NTC R value in kohms. Default is 10.0. |
| `b_value` | NTC B value. Default is 3950. |
| `sync_parameters_on_start` | If true, write R/B registers before acquisition. |

Temperature sample rate and segment size are initialized from
`app_startup.json` in the Qt UI. When NTC acquisition starts, the UI writes the
current common temperature acquisition values back into `temperature_card.json`
before creating `TemperatureNtcSettings`.

### Spindle device config

File: `config/spindle_control.json`

This file controls spindle serial communication, protocol addresses, scaling,
and safety limits.

Important sections:

| Section | Purpose |
| --- | --- |
| `serial` | COM port, baudrate, device id, read/write timeouts. |
| `control` | Protocol addresses/opcodes for speed setpoint, run enable, and run mode. |
| `signals.speed` | Speed feedback register, response id, scale, fallback, deadband. |
| `signals.current` | Current feedback register, response id, scale, fallback, deadband. |
| `safety` | Allowed rpm range. |
| `ui` | Poll and keepalive intervals used by the UI. |

## 4. Core Data Models

Import path:

```python
from nidata_collector.config import (
    SignalType,
    ChannelSelection,
    AcquisitionGroup,
    RunConfiguration,
    AccelerationSettings,
    TemperatureRtdSettings,
    TemperatureNtcSettings,
)
```

### SignalType

| Value | Label | Unit | Device |
| --- | --- | --- | --- |
| `SignalType.ACCELERATION` | `Vibration` | `g` | NI 9234 |
| `SignalType.TEMPERATURE_RTD` | `Temperature` | `degC` | NI 9216 |
| `SignalType.TEMPERATURE_NTC` | `Temperature` | `degC` | DAMX-8013 |

### ChannelSelection

```python
ChannelSelection(
    physical_name: str,
    device_name: str,
    product_type: str,
    signal_type: SignalType,
    visualize: bool = False,
    save: bool = False,
    sensor: SensorMetadata = SensorMetadata(),
)
```

Fields:

- `physical_name`: DAQmx physical channel or serial logical channel, for
  example `cDAQ.../ai0` or `COM9/ntc1`.
- `device_name`: owning device or COM port.
- `product_type`: hardware model string.
- `signal_type`: one of the supported signal types.
- `visualize`: include this channel in live plot events.
- `save`: write this channel after `trigger_recording()`.
- `sensor`: optional sensor metadata written to `sensor_info.csv`.

### AcquisitionGroup

```python
AcquisitionGroup(
    signal_type: SignalType,
    channels: list[ChannelSelection],
    settings: SignalAcquisitionSettings,
)
```

Groups are the unit of worker creation. One group maps to one acquisition
worker and one segment writer.

Convenience properties:

- `read_channels`: all physical channels in the group.
- `save_channels`: channels with `save=True`.
- `visualize_channels`: channels with `visualize=True`.

### Settings classes

Common fields:

```python
sample_rate_hz: float
segment_samples: int
segment_seconds: float
min_value: float
max_value: float
```

Additional acceleration fields:

```python
sensitivity_mv_per_g: float = 100.0
excitation_current_a: float = 0.004
coupling: str = "AC"
settle_seconds: float = 5.0
```

Additional RTD fields:

```python
rtd_type: str = "PT_3851"
resistance_config: str = "FOUR_WIRE"
excitation_current_a: float = 0.001
r0_ohms: float = 100.0
```

Additional NTC fields:

```python
model: str = "DAMX-8013"
port: str = "COM9"
slave_id: int = 1
baudrate: int = 9600
data_bits: int = 8
parity: str = "N"
stop_bits: float = 1.0
timeout_s: float = 1.0
channel_count: int = 2
r_kohms: float = 10.0
b_value: int = 3950
sync_parameters_on_start: bool = True
```

### RunConfiguration

```python
RunConfiguration(
    output_dir: Path,
    groups: list[AcquisitionGroup] = [],
    operator_note: str = "",
    experiment_record: ExperimentRecord = ExperimentRecord(),
    override_network_reservation: bool = False,
)
```

`RunConfiguration.has_saves` is true if at least one selected group has a save
channel. `trigger_recording()` requires this to be true.

## 5. NI Discovery and Reservation API

Import path:

```python
from nidata_collector.hardware.ni import (
    get_system_snapshot,
    reserve_network_devices,
    unreserve_network_devices,
    find_ai_channels,
)
```

### get_system_snapshot

```python
snapshot = get_system_snapshot()
```

Input arguments: none.

Return value: dictionary:

```python
{
    "driver_version": "24.0.0",
    "devices": [
        {
            "name": "cDAQ9185-254D6AA",
            "product_type": "cDAQ-9185",
            "product_num": ...,
            "serial_num": ...,
            "bus_type": ...,
            "tcpip_hostname": ...,
            "tcpip_ethernet_ip": ...,
            "chassis": ...,
            "slot": ...,
            "modules": [...],
            "ai_channels": ["cDAQ.../ai0", ...],
        }
    ],
}
```

Side effects: queries NI-DAQmx system state only.

### reserve_network_devices

```python
results = reserve_network_devices(override=False)
```

Input arguments:

- `override`: pass through to NI network reservation. Use true only when you
  intentionally want to override another reservation.

Return value:

```python
ReservationResult(device="cDAQ9185-254D6AA", ok=True, message="")
```

Side effects: reserves TCP/IP NI devices.

### unreserve_network_devices

```python
results = unreserve_network_devices(["cDAQ9185-254D6AA"])
results = unreserve_network_devices()  # release all TCP/IP devices seen by NI-DAQmx
```

Input arguments:

- `device_names`: optional iterable of device names. If omitted, every TCP/IP
  device visible to NI-DAQmx is considered.

Return value: list of `ReservationResult`.

Side effects: releases NI network reservations.

### find_ai_channels

```python
channels = find_ai_channels("9234")
```

Input arguments:

- `product_type_hint`: optional substring match against device product type.

Return value: list of AI physical channel names.

## 6. AcquisitionController API

Import path:

```python
from nidata_collector.core.engine import AcquisitionController, AcquisitionEvent
```

Create a controller:

```python
controller = AcquisitionController()
```

Public properties:

| Property | Meaning |
| --- | --- |
| `events` | Queue of `AcquisitionEvent`. UI and scripts consume this. |
| `running` | True while at least one worker thread is alive. |
| `recording` | True after `trigger_recording()` succeeds. |
| `has_saves` | True when the active config has any Save channels. |

### start

```python
controller.start(config)
```

Input arguments:

- `config`: `RunConfiguration`.

Return value: none.

Side effects:

- Reserves NI network devices when NI groups are selected.
- Starts one worker per active group.
- Emits a `started` event.
- If NI is unavailable but NTC groups are selected, drops NI groups and keeps
  serial NTC monitoring alive.

Errors:

- Raises if already running.
- Raises if no groups are selected.
- Raises if no selected acquisition device is available.

### trigger_recording

```python
run_dir = controller.trigger_recording()
```

Input arguments: none.

Return value: `Path` to the new run directory.

Side effects:

- Creates the run directory.
- Writes `manifest.json`, experiment metadata, spindle metadata, sensor
  metadata, and `segment_records.csv`.
- Enables segment writers for channels with `save=True`.
- Emits a `recording_started` event.

Errors:

- Raises if monitoring has not started.
- Raises if no Save channel is selected.
- Raises if recording has already been triggered.

### stop

```python
controller.stop()
```

Input arguments: none.

Return value: none.

Side effects: asks workers to stop. Worker shutdown is asynchronous; call
`poll_finished()` until it returns true.

### poll_finished

```python
if controller.poll_finished():
    print("all workers stopped")
```

Input arguments: none.

Return value: bool.

Side effects:

- Emits a `stopped` event once all worker threads have exited.
- Clears active worker/config references after shutdown.

### shutdown

```python
controller.shutdown(join_timeout_s=5.0)
```

Input arguments:

- `join_timeout_s`: join timeout for worker threads.

Return value: none.

Side effects:

- Stops workers.
- Joins worker threads.
- Releases NI network reservations if all workers stopped.

### AcquisitionEvent

```python
AcquisitionEvent(
    kind: str,
    group: str = "",
    message: str = "",
    payload: dict | None = None,
)
```

Common `kind` values:

| Kind | Payload |
| --- | --- |
| `started` | none |
| `status` | human-readable status in `message` |
| `data` | live plot data |
| `recording_started` | `{"run_dir": "..."}` |
| `saved` | `{"csv": "...", "json": "..."}` |
| `error` | optional traceback |
| `stopped` | none |

Live data payload:

```python
{
    "sample_start": 0,
    "sample_rate_hz": 25600.0,
    "unit": "g",
    "channels": ["cDAQ.../ai0"],
    "time_s": numpy.ndarray,
    "data": numpy.ndarray,  # shape = (channel_count, sample_count)
}
```

## 7. DAMX-8013 NTC API

Import path:

```python
from nidata_collector.hardware.damx8013 import (
    Damx8013Config,
    Damx8013Client,
    load_temperature_card_config,
    save_temperature_card_config,
    temperature_ntc_settings_from_config,
    build_temperature_channel_name,
    temperature_channel_index,
    encode_r_kohms,
    desired_ntc_parameter_registers,
    modbus_crc16,
    build_read_holding_registers_request,
    build_write_single_register_request,
    parse_read_holding_registers_response,
    parse_write_single_register_response,
    registers_to_temperatures,
)
```

### Load and save config

```python
from pathlib import Path

config = load_temperature_card_config(Path("config/temperature_card.json"))
save_temperature_card_config(Path("config/temperature_card.json"), config)
settings = temperature_ntc_settings_from_config(config)
```

Validation rules:

- `model` must be `DAMX-8013`.
- `channel_count` must be 2.
- `r_kohms` must be positive and encodable into one Modbus register as
  `round(r_kohms * 100)`.
- `b_value` must fit in 1..65535.

### Channel helpers

```python
name = build_temperature_channel_name("COM9", 1)  # "COM9/ntc1"
index = temperature_channel_index("COM9/ntc2")    # 1
```

Input arguments:

- `port`: COM port string.
- `channel_number`: 1 or 2.

Return values:

- `build_temperature_channel_name`: logical channel name.
- `temperature_channel_index`: zero-based channel index.

### R/B register helpers

```python
encoded_r = encode_r_kohms(10.0)  # 1000
r_register, b_register = desired_ntc_parameter_registers(settings)
```

Register mapping:

- R value register: `40926`, protocol address `0x039D`.
- B value register: `40927`, protocol address `0x039E`.
- R encoding: `r_kohms * 100`.
- B encoding: raw integer B value.

### Modbus helpers

```python
crc = modbus_crc16(bytes.fromhex("01 03 00 00 00 01"))  # 0x0A84
request = build_read_holding_registers_request(1, 0x0000, 2)
write = build_write_single_register_request(1, 0x039D, 1000)
```

Temperature registers:

- Holding register `40001`: channel 1, protocol address `0x0000`.
- Holding register `40002`: channel 2, protocol address `0x0001`.
- Raw values are signed 16-bit integers scaled by 0.1 degC.

Parse example:

```python
registers = parse_read_holding_registers_response(
    response_bytes,
    slave_id=1,
    register_count=2,
)
temperatures = registers_to_temperatures(registers)
```

### Damx8013Client

```python
with Damx8013Client(settings) as client:
    client.sync_ntc_parameters()
    temperatures = client.read_temperatures()
```

Methods:

| Method | Input | Return | Side effects |
| --- | --- | --- | --- |
| `sync_ntc_parameters()` | none | none | Reads R/B registers, writes configured R/B values if needed, verifies. |
| `read_temperatures()` | none | `list[float]` | Reads two temperature registers. |
| `read_holding_registers(start_address, register_count)` | Modbus address and count | `list[int]` | Sends function `0x03`. |
| `write_single_register(address, value)` | Modbus address and 16-bit value | none | Sends function `0x06`. |

Errors:

- Raises `RuntimeError` if `pyserial` is not installed.
- Raises `TimeoutError` when the expected response length is not received.
- Raises `ValueError` for CRC mismatch, Modbus exception, or malformed response.

Acquisition behavior:

- The NTC worker polls at `1 / sample_rate_hz`.
- Three consecutive read failures stop the NTC worker for that acquisition run.
- NTC failures do not stop spindle control and do not automatically stop NI
  groups unless the selected run has no remaining active groups.

## 8. Spindle API

Import path:

```python
from nidata_collector.hardware.spindle import (
    SpindleConfig,
    SpindleDevice,
    SpindleReading,
    SpindleTelemetryRecorder,
    default_spindle_config,
    load_spindle_config,
    save_spindle_config,
    checksum,
    build_read_frame,
    build_write_frame,
    parse_value_response,
)
```

### Load and save config

```python
from pathlib import Path

config = load_spindle_config(Path("config/spindle_control.json"))
save_spindle_config(Path("config/spindle_control.json"), config)
```

Validation rules:

- COM port must not be empty.
- Baudrate and timeouts must be positive.
- `max_rpm` must be greater than `min_run_rpm`.
- UI poll/keepalive/plot intervals must be positive.

### Connect, read, set speed, stop

```python
device = SpindleDevice(config)
try:
    device.connect()
    device.set_speed_rpm(500, prepare=True)
    reading = device.read()
    print(reading.speed_rpm, reading.current_a)
    device.keepalive()
    device.stop()
finally:
    device.close()
```

`SpindleDevice` methods:

| Method | Input | Return | Side effects |
| --- | --- | --- | --- |
| `connect()` | none | none | Opens the serial port. |
| `close()` | none | none | Closes the serial port. |
| `set_speed_rpm(rpm, prepare=True)` | rpm float/int | none | Optionally writes control mode prep, then run enable and speed setpoint. |
| `stop()` | none | none | Writes 0 rpm, run disable, and configured run mode. |
| `keepalive()` | none | bool | Rewrites run enable and current target rpm when target rpm is positive. |
| `read()` | none | `SpindleReading` | Reads speed/current feedback. Uses last value as fallback on read failure. |
| `target_rpm` | property | int | Last requested target rpm. |

`SpindleReading`:

```python
SpindleReading(
    speed_rpm: float,
    current_a: float,
    speed_ok: bool,
    current_ok: bool,
)
```

`speed_ok` and `current_ok` indicate whether that value came from the latest
serial response. If false, the value is a fallback from the last known reading
or the configured default.

### Low-level frame helpers

Use these only for protocol tests or debugging:

```python
read_frame = build_read_frame(address=0x3008, device_id=1)
write_frame = build_write_frame(address=0x0008, value=500, opcode=0x77, device_id=1)
```

`parse_value_response(frame)` returns:

```python
ValueResponse(response_id: int, value: int, raw: bytes)
```

### SpindleTelemetryRecorder

The Qt UI creates this automatically when recording is triggered while the
spindle is connected.

```python
recorder = SpindleTelemetryRecorder(run_dir, config)
try:
    recorder.write(time.monotonic(), reading, target_rpm=500, keepalive_enabled=True)
finally:
    recorder.close()
```

Output files:

- `spindle_telemetry.csv`
- `spindle_telemetry.json`

CSV columns:

```text
sample_index,time_s,target_rpm,actual_speed_rpm,current_a,speed_ok,current_ok,keepalive_enabled
```

## 9. Storage API and Output Format

Import path:

```python
from nidata_collector.core.storage import (
    RunStorage,
    SegmentWriter,
    read_segment_npz_xz,
    postprocess_run_outputs,
)
```

Most application code should use `AcquisitionController.trigger_recording()`.
Use storage classes directly only for tests or custom recording tools.

### Run folder layout

After `trigger_recording()`, files are written under:

```text
data/runs/run_YYYYMMDD_HHMMSS/
  manifest.json
  experiment_record.csv
  spindle_info.csv
  sensor_info.csv
  segment_records.csv
  segment_summary.csv
  acceleration_25600Hz_256000samples/
    000001_segment_acceleration_25600Hz_256000samples_start0.npz.xz
  temperature_ntc_10Hz_100samples/
    ...
  temperature_rtd_10Hz_100samples/
    ...
  trends/
    summary_overview.png
  spindle_telemetry.csv
  spindle_telemetry.json
```

Exact group folder names depend on `signal_type`, `sample_rate_hz`, and
`segment_samples`.

### manifest.json

Contains:

- `run_id`
- `created_at_local`
- `time_axis` description
- `storage_format`, currently `npz_xz_float64_with_time`
- compressed raw segment array schema
- `output_dir`
- serialized `RunConfiguration`
- NI device snapshot
- serial temperature card snapshot
- optional spindle telemetry annotation

### Segment `.npz.xz` files

Raw segments are structured NumPy archives compressed with `lzma/xz`.
Read them with:

```python
from pathlib import Path
from nidata_collector.core.storage import read_segment_npz_xz

payload = read_segment_npz_xz(Path("segment.npz.xz"))
time_s = payload["time_s"]
data = payload["data"]
channels = payload["channels"]
```

Payload arrays:

- `time_s`: float64, shape `(sample_count,)`.
- `data`: float64, shape `(channel_count, sample_count)`.
- `channels`: string array aligned with `data` axis 0.
- `sample_start_index`: int64 scalar array.
- `sample_rate_hz`: float64 scalar array.
- `signal_type`: string scalar array.
- `unit`: string scalar array.

`time_s[i]` corresponds to `data[:, i]`.

For one-off inspection without importing the application package:

```powershell
E:\software\conda\envs\NI\python.exe scripts\inspect_npz_xz.py data\runs\run_xxx\...\segment.npz.xz --head 8
E:\software\conda\envs\NI\python.exe scripts\inspect_npz_xz.py data\runs\run_xxx\...\segment.npz.xz --plot preview.png
```

### segment_records.csv

One row per saved raw segment, including:

- run metadata
- signal type and channels
- unit
- sample rate/count/start/end
- time window start/center/end
- partial flag
- raw data format and `.npz.xz` path

### segment_summary.csv

One wide-table row per fixed 1 second time window. Raw files can still be
saved as 10 second `.npz.xz` segments; postprocessing splits those raw
segments into 1Hz summary windows. The base columns are:

- `time_start_s`
- `time_center_s`, always `time_start_s + 0.5`
- `time_end_s`

Signal columns use these patterns:

- `acceleration__<sensor_or_channel>__mean_abs`
- `acceleration__<sensor_or_channel>__max`
- `acceleration__<sensor_or_channel>__min`
- `temperature_ntc__<sensor_or_channel>__mean`
- `temperature_ntc__<sensor_or_channel>__max`
- `temperature_ntc__<sensor_or_channel>__min`
- `temperature_rtd__<sensor_or_channel>__mean`
- `temperature_rtd__<sensor_or_channel>__max`
- `temperature_rtd__<sensor_or_channel>__min`
- `spindle_speed__mean`, `spindle_speed__max`, `spindle_speed__min`
- `spindle_current__mean`, `spindle_current__max`, `spindle_current__min`

### Trend plots

`postprocess_run_outputs(run_dir)` creates `trends/summary_overview.png` from
`segment_summary.csv`.

The overview image contains four stacked comparison plots:

- vibration channels: `mean_abs`
- temperature channels: `mean`, with NTC before RTD
- spindle speed mean
- spindle current mean

## 10. Qt UI Entry Points

Import path:

```python
from nidata_collector.ui.qt_app import DataCollectorQtApp, main
```

Create a window from Python:

```python
from PySide6 import QtWidgets
from nidata_collector.ui.qt_app import DataCollectorQtApp

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
window = DataCollectorQtApp(initial_refresh=True)
window.show()
app.exec()
```

Constructor:

```python
DataCollectorQtApp(initial_refresh: bool = True)
```

Arguments:

- `initial_refresh`: if true, immediately refresh NI and DAMX-8013 channel
  rows. Pass false for headless UI smoke tests.

UI behavior:

- `Refresh`: reloads hardware channel rows. DAMX-8013 rows come from the
  configured `temperature.ntc.config_path`.
- `Start`: starts live monitoring only.
- `Trigger`: creates a run folder and starts saving selected Save channels.
- `Stop`: stops workers and flushes partial segments.
- Temperature plot tab combines NTC and RTD. NTC curves are ordered first.
- The large NTC badge is safety-oriented and uses only NTC temperatures.

## 11. Common Recipes

### Read DAMX-8013 temperatures once

```python
from pathlib import Path
from nidata_collector.hardware.damx8013 import (
    Damx8013Client,
    load_temperature_card_config,
    temperature_ntc_settings_from_config,
)

config = load_temperature_card_config(Path("config/temperature_card.json"))
settings = temperature_ntc_settings_from_config(config)

with Damx8013Client(settings) as client:
    client.sync_ntc_parameters()
    print(client.read_temperatures())
```

### Build DAMX-8013 channel selections

```python
from nidata_collector.config import ChannelSelection, SignalType
from nidata_collector.hardware.damx8013 import build_temperature_channel_name

port = "COM9"
channels = [
    ChannelSelection(
        physical_name=build_temperature_channel_name(port, index),
        device_name=port,
        product_type="DAMX-8013",
        signal_type=SignalType.TEMPERATURE_NTC,
        visualize=True,
        save=True,
    )
    for index in (1, 2)
]
```

### Read spindle feedback once

```python
from pathlib import Path
from nidata_collector.hardware.spindle import SpindleDevice, load_spindle_config

config = load_spindle_config(Path("config/spindle_control.json"))
device = SpindleDevice(config)
try:
    device.connect()
    reading = device.read()
    print(reading.speed_rpm, reading.current_a, reading.speed_ok, reading.current_ok)
finally:
    device.close()
```

### Start spindle at a target speed

```python
device.connect()
device.set_speed_rpm(500, prepare=True)
device.keepalive()
device.stop()
device.close()
```

### Check startup defaults

```python
from pathlib import Path
from nidata_collector.ui.startup_config import load_startup_config

startup = load_startup_config(Path("config/app_startup.json"))
print(startup.vibration.sample_rate_hz)
print(startup.temperature.sample_rate_hz)
print(startup.spindle.default_target_rpm)
```

## 12. Validation Commands

Compile the public entry points and UI startup loader:

```powershell
E:\software\conda\envs\NI\python.exe -B -m py_compile scripts\run_monitor.py scripts\ni_probe.py src\nidata_collector\core\engine.py src\nidata_collector\core\storage.py src\nidata_collector\ui\startup_config.py src\nidata_collector\ui\qt_app.py
```

Run a Qt initialization smoke test without opening a visible window:

```powershell
$env:PYTHONPATH='src'
$env:QT_QPA_PLATFORM='offscreen'
@'
from PySide6 import QtWidgets
from nidata_collector.ui.qt_app import DataCollectorQtApp
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
window = DataCollectorQtApp(initial_refresh=False)
print(window.accel_rate.value(), window.temp_rate.value(), window.spindle_target_rpm.value())
window.close()
'@ | E:\software\conda\envs\NI\python.exe -B -
```

## 13. API Stability Notes

Stable for application use:

- `nidata_collector.config`
- `nidata_collector.hardware.ni`
- `nidata_collector.hardware.damx8013`
- `nidata_collector.hardware.spindle`
- `AcquisitionController`
- `RunStorage` and `SegmentWriter` for custom storage tools
- `read_segment_npz_xz` and `postprocess_run_outputs`
- `load_startup_config`

Internal implementation details:

- `AcquisitionWorker`
- `TemperatureNtcWorker`
- `AsyncSegmentWriter`
- private helper functions beginning with `_`

Prefer the stable APIs above for new scripts. Internal classes may change when
the UI or acquisition engine is reorganized.
