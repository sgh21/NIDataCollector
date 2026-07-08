from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import SensorMetadata
from .standard_workflow import StandardWorkflowConfig


@dataclass(frozen=True)
class OutputStartupConfig:
    run_dir: str = "data/runs"
    override_network_reservation: bool = False


@dataclass(frozen=True)
class VibrationStartupConfig:
    sample_rate_hz: float = 5120.0
    segment_samples: int = 51200
    min_g: float = -50.0
    max_g: float = 50.0
    sensitivity_mv_per_g: float = 100.0
    excitation_current_a: float = 0.004
    settle_seconds: float = 5.0
    coupling: str = "AC"
    plot_window_s: float = 10.0
    plot_min_g: float = -0.1
    plot_max_g: float = 0.1

    @property
    def segment_seconds(self) -> float:
        return self.segment_samples / self.sample_rate_hz


@dataclass(frozen=True)
class RtdStartupConfig:
    excitation_current_a: float = 0.001
    r0_ohms: float = 100.0
    type: str = "PT_3851"
    wiring: str = "FOUR_WIRE"


@dataclass(frozen=True)
class NtcStartupConfig:
    config_path: str = "config/temperature_card.json"


@dataclass(frozen=True)
class TemperatureStartupConfig:
    sample_rate_hz: float = 10.0
    segment_samples: int = 100
    min_deg_c: float = -40.0
    max_deg_c: float = 150.0
    plot_window_s: float = 120.0
    plot_min_deg_c: float = 10.0
    plot_max_deg_c: float = 50.0
    alert_deg_c: float = 80.0
    rtd: RtdStartupConfig = field(default_factory=RtdStartupConfig)
    ntc: NtcStartupConfig = field(default_factory=NtcStartupConfig)

    @property
    def segment_seconds(self) -> float:
        return self.segment_samples / self.sample_rate_hz


@dataclass(frozen=True)
class SpindleStartupConfig:
    config_path: str = "config/spindle_control.json"
    default_target_rpm: int = 500
    plot_window_s: float = 10.0


@dataclass(frozen=True)
class RecordStartupConfig:
    spindle_id_placeholder: str = "SP01"
    sample_label_placeholder: str = "normal_baseline"


@dataclass(frozen=True)
class ChannelSelectionStartupConfig:
    plot: bool = False
    save: bool = False


@dataclass(frozen=True)
class StartupConfig:
    output: OutputStartupConfig = field(default_factory=OutputStartupConfig)
    vibration: VibrationStartupConfig = field(default_factory=VibrationStartupConfig)
    temperature: TemperatureStartupConfig = field(default_factory=TemperatureStartupConfig)
    spindle: SpindleStartupConfig = field(default_factory=SpindleStartupConfig)
    standard_flow: StandardWorkflowConfig = field(default_factory=StandardWorkflowConfig)
    record: RecordStartupConfig = field(default_factory=RecordStartupConfig)
    channel_metadata: dict[str, SensorMetadata] = field(default_factory=dict)
    channel_selection: dict[str, ChannelSelectionStartupConfig] = field(default_factory=dict)


def default_startup_config() -> StartupConfig:
    return StartupConfig()


def load_startup_config(path: Path) -> StartupConfig:
    if not path.exists():
        return default_startup_config()

    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object.")

    output = _mapping(raw, "output")
    vibration = _mapping(raw, "vibration")
    temperature = _mapping(raw, "temperature")
    rtd = _mapping(temperature, "rtd")
    ntc = _mapping(temperature, "ntc")
    spindle = _mapping(raw, "spindle")
    standard_flow = _mapping(raw, "standard_flow")
    record = _mapping(raw, "record")
    channel_metadata = _channel_metadata(raw.get("channel_metadata", {}))
    channel_selection = _channel_selection(raw.get("channel_selection", {}))

    default = default_startup_config()
    config = StartupConfig(
        output=OutputStartupConfig(
            run_dir=_string(output, "run_dir", default.output.run_dir),
            override_network_reservation=_boolean(
                output,
                "override_network_reservation",
                default.output.override_network_reservation,
            ),
        ),
        vibration=VibrationStartupConfig(
            sample_rate_hz=_number(vibration, "sample_rate_hz", default.vibration.sample_rate_hz),
            segment_samples=_integer(vibration, "segment_samples", default.vibration.segment_samples),
            min_g=_number(vibration, "min_g", default.vibration.min_g),
            max_g=_number(vibration, "max_g", default.vibration.max_g),
            sensitivity_mv_per_g=_number(
                vibration,
                "sensitivity_mv_per_g",
                default.vibration.sensitivity_mv_per_g,
            ),
            excitation_current_a=_number(
                vibration,
                "excitation_current_a",
                default.vibration.excitation_current_a,
            ),
            settle_seconds=_number(vibration, "settle_seconds", default.vibration.settle_seconds),
            coupling=_string(vibration, "coupling", default.vibration.coupling),
            plot_window_s=_number(vibration, "plot_window_s", default.vibration.plot_window_s),
            plot_min_g=_number(vibration, "plot_min_g", default.vibration.plot_min_g),
            plot_max_g=_number(vibration, "plot_max_g", default.vibration.plot_max_g),
        ),
        temperature=TemperatureStartupConfig(
            sample_rate_hz=_number(temperature, "sample_rate_hz", default.temperature.sample_rate_hz),
            segment_samples=_integer(temperature, "segment_samples", default.temperature.segment_samples),
            min_deg_c=_number(temperature, "min_deg_c", default.temperature.min_deg_c),
            max_deg_c=_number(temperature, "max_deg_c", default.temperature.max_deg_c),
            plot_window_s=_number(temperature, "plot_window_s", default.temperature.plot_window_s),
            plot_min_deg_c=_number(temperature, "plot_min_deg_c", default.temperature.plot_min_deg_c),
            plot_max_deg_c=_number(temperature, "plot_max_deg_c", default.temperature.plot_max_deg_c),
            alert_deg_c=_number(temperature, "alert_deg_c", default.temperature.alert_deg_c),
            rtd=RtdStartupConfig(
                excitation_current_a=_number(
                    rtd,
                    "excitation_current_a",
                    default.temperature.rtd.excitation_current_a,
                ),
                r0_ohms=_number(rtd, "r0_ohms", default.temperature.rtd.r0_ohms),
                type=_string(rtd, "type", default.temperature.rtd.type),
                wiring=_string(rtd, "wiring", default.temperature.rtd.wiring),
            ),
            ntc=NtcStartupConfig(
                config_path=_string(ntc, "config_path", default.temperature.ntc.config_path),
            ),
        ),
        spindle=SpindleStartupConfig(
            config_path=_string(spindle, "config_path", default.spindle.config_path),
            default_target_rpm=_integer(
                spindle,
                "default_target_rpm",
                default.spindle.default_target_rpm,
            ),
            plot_window_s=_number(spindle, "plot_window_s", default.spindle.plot_window_s),
        ),
        standard_flow=StandardWorkflowConfig(
            start_rpm=_integer(standard_flow, "start_rpm", default.standard_flow.start_rpm),
            step_rpm=_integer(standard_flow, "step_rpm", default.standard_flow.step_rpm),
            max_rpm=_integer(standard_flow, "max_rpm", default.standard_flow.max_rpm),
            transition_hold_s=_number(
                standard_flow,
                "transition_hold_s",
                default.standard_flow.transition_hold_s,
            ),
            max_hold_s=_number(standard_flow, "max_hold_s", default.standard_flow.max_hold_s),
        ),
        record=RecordStartupConfig(
            spindle_id_placeholder=_string(
                record,
                "spindle_id_placeholder",
                default.record.spindle_id_placeholder,
            ),
            sample_label_placeholder=_string(
                record,
                "sample_label_placeholder",
                default.record.sample_label_placeholder,
            ),
        ),
        channel_metadata=channel_metadata,
        channel_selection=channel_selection,
    )
    validate_startup_config(config)
    return config


def save_channel_defaults(
    path: Path,
    metadata_by_channel: dict[str, SensorMetadata],
    selection_by_channel: dict[str, ChannelSelectionStartupConfig],
) -> None:
    raw: dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            raw = loaded

    raw["channel_metadata"] = {
        channel: _metadata_to_dict(metadata)
        for channel, metadata in sorted(metadata_by_channel.items())
        if channel.strip() and _has_metadata(metadata)
    }
    raw["channel_selection"] = {
        channel: {
            "plot": bool(selection.plot),
            "save": bool(selection.save),
        }
        for channel, selection in sorted(selection_by_channel.items())
        if channel.strip()
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(raw, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    tmp_path.replace(path)


def save_channel_metadata_defaults(path: Path, metadata_by_channel: dict[str, SensorMetadata]) -> None:
    save_channel_defaults(path, metadata_by_channel, {})


def resolve_startup_path(root: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return root / path


def validate_startup_config(config: StartupConfig) -> None:
    if not config.output.run_dir.strip():
        raise ValueError("output.run_dir must not be empty.")
    if config.vibration.sample_rate_hz <= 0:
        raise ValueError("vibration.sample_rate_hz must be positive.")
    if config.vibration.segment_samples <= 0:
        raise ValueError("vibration.segment_samples must be positive.")
    if config.vibration.min_g >= config.vibration.max_g:
        raise ValueError("vibration.min_g must be smaller than vibration.max_g.")
    if config.vibration.sensitivity_mv_per_g <= 0:
        raise ValueError("vibration.sensitivity_mv_per_g must be positive.")
    if config.vibration.excitation_current_a < 0:
        raise ValueError("vibration.excitation_current_a must be non-negative.")
    if config.vibration.settle_seconds < 0:
        raise ValueError("vibration.settle_seconds must be non-negative.")
    if config.vibration.coupling.upper() not in ("AC", "DC", "GND", "NONE"):
        raise ValueError("vibration.coupling must be AC, DC, GND, or NONE.")
    if config.vibration.plot_window_s <= 0:
        raise ValueError("vibration.plot_window_s must be positive.")
    if config.vibration.plot_min_g >= config.vibration.plot_max_g:
        raise ValueError("vibration.plot_min_g must be smaller than vibration.plot_max_g.")

    if config.temperature.sample_rate_hz <= 0:
        raise ValueError("temperature.sample_rate_hz must be positive.")
    if config.temperature.segment_samples <= 0:
        raise ValueError("temperature.segment_samples must be positive.")
    if config.temperature.min_deg_c >= config.temperature.max_deg_c:
        raise ValueError("temperature.min_deg_c must be smaller than temperature.max_deg_c.")
    if config.temperature.plot_window_s <= 0:
        raise ValueError("temperature.plot_window_s must be positive.")
    if config.temperature.plot_min_deg_c >= config.temperature.plot_max_deg_c:
        raise ValueError("temperature.plot_min_deg_c must be smaller than temperature.plot_max_deg_c.")
    if config.temperature.rtd.excitation_current_a < 0:
        raise ValueError("temperature.rtd.excitation_current_a must be non-negative.")
    if config.temperature.rtd.r0_ohms <= 0:
        raise ValueError("temperature.rtd.r0_ohms must be positive.")
    if not config.temperature.rtd.type.strip():
        raise ValueError("temperature.rtd.type must not be empty.")
    if not config.temperature.rtd.wiring.strip():
        raise ValueError("temperature.rtd.wiring must not be empty.")
    if not config.temperature.ntc.config_path.strip():
        raise ValueError("temperature.ntc.config_path must not be empty.")

    if not config.spindle.config_path.strip():
        raise ValueError("spindle.config_path must not be empty.")
    if config.spindle.default_target_rpm < 0:
        raise ValueError("spindle.default_target_rpm must be non-negative.")
    if config.spindle.plot_window_s <= 0:
        raise ValueError("spindle.plot_window_s must be positive.")

    if config.standard_flow.start_rpm <= 0:
        raise ValueError("standard_flow.start_rpm must be positive.")
    if config.standard_flow.step_rpm <= 0:
        raise ValueError("standard_flow.step_rpm must be positive.")
    if config.standard_flow.max_rpm < config.standard_flow.start_rpm:
        raise ValueError("standard_flow.max_rpm must be greater than or equal to standard_flow.start_rpm.")
    if config.standard_flow.transition_hold_s <= 0:
        raise ValueError("standard_flow.transition_hold_s must be positive.")
    if config.standard_flow.max_hold_s <= 0:
        raise ValueError("standard_flow.max_hold_s must be positive.")


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key, {})
    if isinstance(value, dict):
        return value
    return {}


def _channel_metadata(raw: Any) -> dict[str, SensorMetadata]:
    if not isinstance(raw, dict):
        return {}
    metadata_by_channel: dict[str, SensorMetadata] = {}
    for channel, value in raw.items():
        if not isinstance(value, dict):
            continue
        channel_name = str(channel).strip()
        if not channel_name:
            continue
        metadata = SensorMetadata(
            sensor_id=_string(value, "sensor_id", "").strip(),
            measurement_position=_string(value, "measurement_position", "").strip(),
            direction=_string(value, "direction", "").strip(),
            mounting_method=_string(value, "mounting_method", "").strip(),
        )
        metadata_by_channel[channel_name] = metadata
    return metadata_by_channel


def _channel_selection(raw: Any) -> dict[str, ChannelSelectionStartupConfig]:
    if not isinstance(raw, dict):
        return {}
    selection_by_channel: dict[str, ChannelSelectionStartupConfig] = {}
    for channel, value in raw.items():
        if not isinstance(value, dict):
            continue
        channel_name = str(channel).strip()
        if not channel_name:
            continue
        selection_by_channel[channel_name] = ChannelSelectionStartupConfig(
            plot=_boolean(value, "plot", False),
            save=_boolean(value, "save", False),
        )
    return selection_by_channel


def _metadata_to_dict(metadata: SensorMetadata) -> dict[str, str]:
    return {
        "sensor_id": metadata.sensor_id,
        "measurement_position": metadata.measurement_position,
        "direction": metadata.direction,
        "mounting_method": metadata.mounting_method,
    }


def _has_metadata(metadata: SensorMetadata) -> bool:
    return any(
        (
            metadata.sensor_id,
            metadata.measurement_position,
            metadata.direction,
            metadata.mounting_method,
        )
    )


def _string(raw: dict[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    return str(value)


def _integer(raw: dict[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer.")
    return int(value)


def _number(raw: dict[str, Any], key: str, default: float) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number.")
    return float(value)


def _boolean(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    raise ValueError(f"{key} must be a boolean.")
