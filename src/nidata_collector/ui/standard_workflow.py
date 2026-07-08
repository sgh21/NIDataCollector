from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StandardWorkflowConfig:
    start_rpm: int = 500
    step_rpm: int = 500
    max_rpm: int = 6000
    transition_hold_s: float = 10.0
    max_hold_s: float = 480.0

    def to_json(self) -> dict[str, int | float]:
        return {
            "start_rpm": self.start_rpm,
            "step_rpm": self.step_rpm,
            "max_rpm": self.max_rpm,
            "transition_hold_s": self.transition_hold_s,
            "max_hold_s": self.max_hold_s,
        }


@dataclass(frozen=True)
class StandardWorkflowStep:
    index: int
    target_rpm: int
    hold_s: float
    is_max_step: bool
    phase: str

    def to_json(self) -> dict[str, int | float | bool | str]:
        return {
            "index": self.index,
            "target_rpm": self.target_rpm,
            "hold_s": self.hold_s,
            "is_max_step": self.is_max_step,
            "phase": self.phase,
        }


@dataclass(frozen=True)
class StandardWorkflowSnapshot:
    status: str
    step_index: int
    step_count: int
    target_rpm: int
    elapsed_s: float
    remaining_s: float
    progress_fraction: float


@dataclass(frozen=True)
class StandardWorkflowEvent:
    kind: str
    step: StandardWorkflowStep | None = None


class StandardWorkflowRun:
    def __init__(
        self,
        config: StandardWorkflowConfig,
        *,
        min_allowed_rpm: int = 1,
        max_allowed_rpm: int,
        started_at_monotonic: float,
    ) -> None:
        validate_standard_workflow_config(
            config,
            min_allowed_rpm=min_allowed_rpm,
            max_allowed_rpm=max_allowed_rpm,
        )
        self.config = config
        self.steps = build_standard_workflow_steps(config)
        self.status = "running"
        self.started_at_monotonic = started_at_monotonic
        self.step_started_at_monotonic = started_at_monotonic
        self.step_index = 0

    @property
    def current_step(self) -> StandardWorkflowStep:
        return self.steps[self.step_index]

    def update(self, now_monotonic: float) -> StandardWorkflowEvent | None:
        if self.status != "running":
            return None
        if now_monotonic - self.step_started_at_monotonic < self.current_step.hold_s:
            return None
        if self.step_index + 1 >= len(self.steps):
            self.status = "completed"
            return StandardWorkflowEvent("complete")

        self.step_index += 1
        self.step_started_at_monotonic = now_monotonic
        return StandardWorkflowEvent("set_speed", self.current_step)

    def emergency_stop(self) -> None:
        self.status = "emergency_stopped"

    def fail(self) -> None:
        self.status = "failed"

    def snapshot(self, now_monotonic: float) -> StandardWorkflowSnapshot:
        step = self.current_step
        elapsed = max(0.0, now_monotonic - self.step_started_at_monotonic)
        remaining = max(0.0, step.hold_s - elapsed)
        completed_hold = sum(item.hold_s for item in self.steps[: self.step_index])
        total_hold = sum(item.hold_s for item in self.steps)
        progress = 0.0
        if total_hold > 0:
            progress = min(1.0, max(0.0, (completed_hold + min(elapsed, step.hold_s)) / total_hold))
        return StandardWorkflowSnapshot(
            status=self.status,
            step_index=self.step_index + 1,
            step_count=len(self.steps),
            target_rpm=step.target_rpm,
            elapsed_s=elapsed,
            remaining_s=remaining,
            progress_fraction=progress,
        )

    def steps_json(self) -> list[dict[str, int | float | bool]]:
        return [step.to_json() for step in self.steps]


def build_standard_workflow_steps(config: StandardWorkflowConfig) -> list[StandardWorkflowStep]:
    ramp_up_speeds = []
    speed = config.start_rpm
    while speed < config.max_rpm:
        ramp_up_speeds.append(speed)
        speed += config.step_rpm
    ramp_up_speeds.append(config.max_rpm)

    raw_steps: list[tuple[int, float, bool, str]] = []
    for target_rpm in ramp_up_speeds:
        if target_rpm == config.max_rpm:
            raw_steps.append((target_rpm, config.max_hold_s, True, "max_hold"))
        else:
            raw_steps.append((target_rpm, config.transition_hold_s, False, "ramp_up"))
    for target_rpm in reversed(ramp_up_speeds[:-1]):
        raw_steps.append((target_rpm, config.transition_hold_s, False, "ramp_down"))

    steps = []
    for index, (target_rpm, hold_s, is_max_step, phase) in enumerate(raw_steps):
        steps.append(
            StandardWorkflowStep(
                index=index,
                target_rpm=target_rpm,
                hold_s=hold_s,
                is_max_step=is_max_step,
                phase=phase,
            )
        )
    return steps


def validate_standard_workflow_config(
    config: StandardWorkflowConfig,
    *,
    min_allowed_rpm: int = 1,
    max_allowed_rpm: int,
) -> None:
    if config.start_rpm < min_allowed_rpm:
        raise ValueError(f"standard_flow.start_rpm must be at least {min_allowed_rpm} rpm.")
    if config.step_rpm <= 0:
        raise ValueError("standard_flow.step_rpm must be positive.")
    if config.max_rpm < config.start_rpm:
        raise ValueError("standard_flow.max_rpm must be greater than or equal to start_rpm.")
    if config.max_rpm < min_allowed_rpm:
        raise ValueError(f"standard_flow.max_rpm must be at least {min_allowed_rpm} rpm.")
    if config.max_rpm > max_allowed_rpm:
        raise ValueError(f"standard_flow.max_rpm must not exceed spindle safety max {max_allowed_rpm} rpm.")
    if config.transition_hold_s <= 0:
        raise ValueError("standard_flow.transition_hold_s must be positive.")
    if config.max_hold_s <= 0:
        raise ValueError("standard_flow.max_hold_s must be positive.")
