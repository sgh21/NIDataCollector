from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class SignalType(str, Enum):
    ACCELERATION = "acceleration"
    TEMPERATURE_RTD = "temperature_rtd"

    @property
    def label(self) -> str:
        if self == SignalType.ACCELERATION:
            return "Vibration"
        if self == SignalType.TEMPERATURE_RTD:
            return "RTD Temperature"
        return self.value

    @property
    def unit(self) -> str:
        if self == SignalType.ACCELERATION:
            return "g"
        if self == SignalType.TEMPERATURE_RTD:
            return "degC"
        return ""


@dataclass(frozen=True)
class SensorMetadata:
    sensor_id: str = ""
    measurement_position: str = ""
    direction: str = ""
    mounting_method: str = ""


@dataclass(frozen=True)
class ChannelSelection:
    physical_name: str
    device_name: str
    product_type: str
    signal_type: SignalType
    visualize: bool = False
    save: bool = False
    sensor: SensorMetadata = field(default_factory=SensorMetadata)


@dataclass(frozen=True)
class SignalAcquisitionSettings:
    sample_rate_hz: float
    segment_samples: int
    segment_seconds: float
    min_value: float
    max_value: float


@dataclass(frozen=True)
class AccelerationSettings(SignalAcquisitionSettings):
    sensitivity_mv_per_g: float = 100.0
    excitation_current_a: float = 0.004
    coupling: str = "AC"
    settle_seconds: float = 0.5


@dataclass(frozen=True)
class TemperatureRtdSettings(SignalAcquisitionSettings):
    rtd_type: str = "PT_3851"
    resistance_config: str = "FOUR_WIRE"
    excitation_current_a: float = 0.001
    r0_ohms: float = 100.0


@dataclass(frozen=True)
class AcquisitionGroup:
    signal_type: SignalType
    channels: list[ChannelSelection]
    settings: SignalAcquisitionSettings

    @property
    def read_channels(self) -> list[str]:
        return [channel.physical_name for channel in self.channels]

    @property
    def save_channels(self) -> list[str]:
        return [channel.physical_name for channel in self.channels if channel.save]

    @property
    def visualize_channels(self) -> list[str]:
        return [channel.physical_name for channel in self.channels if channel.visualize]


@dataclass(frozen=True)
class SpindleInfo:
    spindle_id: str = ""
    model: str = ""
    rated_speed_rpm: float | None = None
    max_speed_rpm: float | None = None
    test_date: str = ""
    accumulated_runtime_hours: float | None = None


@dataclass(frozen=True)
class OperatingCondition:
    target_speed_rpm: float | None = None
    actual_speed_rpm: float | None = None
    ramp_method: str = ""
    run_duration_s: float | None = None
    preheated: bool = False
    thermal_state: str = ""


@dataclass(frozen=True)
class TemperatureRecord:
    front_bearing_deg_c: float | None = None
    rear_bearing_deg_c: float | None = None
    motor_housing_deg_c: float | None = None
    ambient_deg_c: float | None = None


@dataclass(frozen=True)
class SpeedRecord:
    set_speed_rpm: float | None = None
    actual_speed_rpm: float | None = None
    fluctuation_rpm: str = ""
    has_phase_signal: bool = False


@dataclass(frozen=True)
class ExceptionRecord:
    abnormal_noise: bool = False
    over_temperature: bool = False
    alarm: bool = False
    cable_loose: bool = False
    acquisition_interrupted: bool = False
    misoperation: bool = False
    note: str = ""


@dataclass(frozen=True)
class FollowupLabel:
    rotation_accuracy_measured: bool = False
    rotation_accuracy_value: str = ""
    measurement_position: str = ""
    measurement_condition: str = ""
    label: str = ""


@dataclass(frozen=True)
class ExperimentRecord:
    spindle: SpindleInfo = field(default_factory=SpindleInfo)
    condition: OperatingCondition = field(default_factory=OperatingCondition)
    temperature: TemperatureRecord = field(default_factory=TemperatureRecord)
    speed: SpeedRecord = field(default_factory=SpeedRecord)
    exception: ExceptionRecord = field(default_factory=ExceptionRecord)
    followup: FollowupLabel = field(default_factory=FollowupLabel)


@dataclass(frozen=True)
class RunConfiguration:
    output_dir: Path
    groups: list[AcquisitionGroup] = field(default_factory=list)
    operator_note: str = ""
    experiment_record: ExperimentRecord = field(default_factory=ExperimentRecord)
    override_network_reservation: bool = False

    @property
    def has_saves(self) -> bool:
        return any(group.save_channels for group in self.groups)
