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
class ChannelSelection:
    physical_name: str
    device_name: str
    product_type: str
    signal_type: SignalType
    visualize: bool = False
    save: bool = False


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
class RunConfiguration:
    output_dir: Path
    groups: list[AcquisitionGroup] = field(default_factory=list)
    operator_note: str = ""

    @property
    def has_saves(self) -> bool:
        return any(group.save_channels for group in self.groups)
