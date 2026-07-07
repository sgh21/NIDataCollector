from __future__ import annotations

import argparse
import json
import queue
import threading
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from ..config import (
    AccelerationSettings,
    AcquisitionGroup,
    ChannelSelection,
    ExceptionRecord,
    ExperimentRecord,
    FollowupLabel,
    OperatingCondition,
    RunConfiguration,
    SensorMetadata,
    SignalType,
    SpeedRecord,
    SpindleInfo,
    TemperatureNtcSettings,
    TemperatureRecord,
    TemperatureRtdSettings,
)
from ..core.engine import AcquisitionController
from ..hardware.ni import get_system_snapshot, reserve_network_devices, unreserve_network_devices
from ..hardware.spindle import (
    SpindleConfig,
    SpindleDevice,
    SpindleReading,
    SpindleTelemetryRecorder,
    default_spindle_config,
    load_spindle_config,
    save_spindle_config,
)
from ..hardware.damx8013 import (
    DAMX8013_CHANNEL_COUNT,
    Damx8013Config,
    build_temperature_channel_name,
    load_temperature_card_config,
    save_temperature_card_config,
    temperature_ntc_settings_from_config,
)


ROOT = Path(__file__).resolve().parents[3]
TEMPERATURE_CARD_CONFIG_PATH = ROOT / "config" / "temperature_card.json"
SPINDLE_CONFIG_PATH = ROOT / "config" / "spindle_control.json"
EVENT_POLL_INTERVAL_MS = 10
PLOT_REDRAW_INTERVAL_MS = 16
DISPLAY_MAX_POINTS_PER_CURVE = 4096
DEFAULT_Y_RANGES = {
    SignalType.ACCELERATION: (-0.1, 0.1),
    SignalType.TEMPERATURE_NTC: (10.0, 50.0),
    SignalType.TEMPERATURE_RTD: (10.0, 50.0),
}
TEMPERATURE_SIGNAL_TYPES = (SignalType.TEMPERATURE_NTC, SignalType.TEMPERATURE_RTD)
PLOT_SIGNAL_TYPES = (SignalType.ACCELERATION, SignalType.TEMPERATURE_NTC)


class DataCollectorQtApp(QtWidgets.QMainWindow):
    def __init__(self, initial_refresh: bool = True) -> None:
        super().__init__()
        self.setWindowTitle("NI Data Collector")
        self.resize(1480, 880)
        self.setMinimumSize(1260, 740)

        pg.setConfigOptions(antialias=False)
        self.controller = AcquisitionController()
        self.channel_rows: list[ChannelRowWidget] = []
        self.plot_buffers: dict[SignalType, dict[str, PlotBuffer]] = {
            signal_type: {} for signal_type in PLOT_SIGNAL_TYPES
        }
        self.plot_panels: dict[SignalType, PlotPanel] = {}
        self.plot_dirty: set[SignalType] = set()
        self.plot_y_ranges: dict[SignalType, tuple[float, float]] = {
            signal_type: DEFAULT_Y_RANGES[signal_type] for signal_type in PLOT_SIGNAL_TYPES
        }
        self.reserved_device_names: set[str] = set()
        self._syncing_fields = False
        self.monitoring_started_at: float | None = None
        self.recording_started_at: float | None = None
        self.recorded_segment_count = 0
        self.last_recorded_segment_count = 0
        self.recording_run_dir: Path | None = None
        self.temperature_card_config, self.temperature_card_config_error = (
            self._load_initial_temperature_card_config()
        )
        self.latest_temperatures: dict[str, float] = {}
        self.latest_ntc_temperatures: dict[str, float] = {}
        self.spindle_config, self.spindle_config_error = self._load_initial_spindle_config()
        self.spindle_device: SpindleDevice | None = None
        self.spindle_thread: threading.Thread | None = None
        self.spindle_stop_event = threading.Event()
        self.spindle_events: queue.Queue[tuple] = queue.Queue()
        self.latest_spindle_reading: SpindleReading | None = None
        self.spindle_keepalive_enabled = False
        self.spindle_recorder: SpindleTelemetryRecorder | None = None

        self._build_ui()
        self._connect_setting_sync()
        if self.temperature_card_config_error:
            self._log(self.temperature_card_config_error)
        if self.spindle_config_error:
            self._log(self.spindle_config_error)

        self.event_timer = QtCore.QTimer(self)
        self.event_timer.timeout.connect(self._poll_events)
        self.event_timer.start(EVENT_POLL_INTERVAL_MS)

        self.plot_timer = QtCore.QTimer(self)
        self.plot_timer.timeout.connect(self._redraw_plots)
        self.plot_timer.start(PLOT_REDRAW_INTERVAL_MS)

        if initial_refresh:
            self.refresh_devices()

    def _load_initial_spindle_config(self) -> tuple[SpindleConfig, str]:
        try:
            return load_spindle_config(SPINDLE_CONFIG_PATH), ""
        except Exception as exc:
            return default_spindle_config(), f"Spindle config skipped: {type(exc).__name__}: {exc}"

    def _load_initial_temperature_card_config(self) -> tuple[Damx8013Config, str]:
        try:
            return load_temperature_card_config(TEMPERATURE_CARD_CONFIG_PATH), ""
        except Exception as exc:
            return Damx8013Config(), f"DAMX-8013 config skipped: {type(exc).__name__}: {exc}"

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f4f6f8; color: #172033; font-family: "Microsoft YaHei UI"; font-size: 9pt; }
            QFrame#card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 6px; }
            QLabel#title { color: #111827; font-size: 12pt; font-weight: 600; }
            QLabel#small { color: #667085; font-size: 8pt; }
            QLabel#metric { color: #111827; font-size: 14pt; font-weight: 700; }
            QPushButton { padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 5px; background: #ffffff; }
            QPushButton:hover { background: #eef2f7; }
            QPushButton#startButton { background: #0f766e; color: white; border-color: #0f766e; font-weight: 600; }
            QPushButton#triggerButton { background: #1d4ed8; color: white; border-color: #1d4ed8; font-weight: 600; }
            QPushButton#triggerButton:disabled { background: #dbe4f0; color: #667085; border-color: #cbd5e1; }
            QPushButton#stopButton { background: #b42318; color: white; border-color: #b42318; font-weight: 600; }
            QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox { padding: 4px; background: #ffffff; border: 1px solid #cbd5e1; border-radius: 4px; }
            QPlainTextEdit { background: #0f172a; color: #e5e7eb; border: 0; border-radius: 4px; font-family: Consolas; }
            QTabWidget::pane { border: 1px solid #e5e7eb; background: #ffffff; }
            QTabBar::tab { padding: 7px 12px; background: #edf2f7; border: 1px solid #e5e7eb; }
            QTabBar::tab:selected { background: #ffffff; font-weight: 600; }
            """
        )

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main = QtWidgets.QHBoxLayout(central)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(10)

        left = QtWidgets.QWidget()
        left.setFixedWidth(590)
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        main.addWidget(left)
        main.addWidget(right, stretch=1)

        left_layout.addWidget(self._build_channel_card(), stretch=0)
        left_layout.addWidget(self._build_settings_card(), stretch=1)
        right_layout.addWidget(self._build_plot_card(), stretch=1)
        right_layout.addWidget(self._build_log_card(), stretch=0)

    def _card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        return card

    def _build_channel_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Devices and channels")
        title.setObjectName("title")
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_devices)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        labels = QtWidgets.QHBoxLayout()
        for text, width in (
            ("Module", 118),
            ("Channel", 158),
            ("Signal", 110),
            ("Plot", 38),
            ("Save", 38),
            ("Meta", 46),
        ):
            label = QtWidgets.QLabel(text)
            label.setObjectName("small")
            label.setFixedWidth(width)
            labels.addWidget(label)
        layout.addLayout(labels)

        self.channel_list = QtWidgets.QWidget()
        self.channel_list_layout = QtWidgets.QVBoxLayout(self.channel_list)
        self.channel_list_layout.setContentsMargins(0, 0, 0, 0)
        self.channel_list_layout.setSpacing(2)
        self.channel_list_layout.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(250)
        scroll.setWidget(self.channel_list)
        layout.addWidget(scroll)
        return card

    def _build_settings_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        output_title = QtWidgets.QLabel("Output")
        output_title.setObjectName("title")
        layout.addWidget(output_title)

        output_grid = QtWidgets.QGridLayout()
        output_grid.setColumnStretch(1, 1)
        self.output_dir_edit = QtWidgets.QLineEdit(str(ROOT / "data" / "runs"))
        browse = QtWidgets.QPushButton("Browse")
        browse.clicked.connect(self._browse_output_dir)
        self.note_edit = QtWidgets.QLineEdit()
        self.override_checkbox = QtWidgets.QCheckBox("Override reservation")
        output_grid.addWidget(QtWidgets.QLabel("Folder"), 0, 0)
        output_grid.addWidget(self.output_dir_edit, 0, 1)
        output_grid.addWidget(browse, 0, 2)
        output_grid.addWidget(QtWidgets.QLabel("Note"), 1, 0)
        output_grid.addWidget(self.note_edit, 1, 1, 1, 2)
        output_grid.addWidget(self.override_checkbox, 2, 1, 1, 2)
        layout.addLayout(output_grid)

        settings_tabs = QtWidgets.QTabWidget()
        self._build_vibration_settings(settings_tabs)
        self._build_temperature_settings(settings_tabs)
        self._build_spindle_settings(settings_tabs)
        self._build_record_settings(settings_tabs)
        layout.addWidget(settings_tabs, stretch=1)

        controls = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton("Start")
        self.start_button.setObjectName("startButton")
        self.start_button.clicked.connect(self.start_acquisition)
        self.trigger_button = QtWidgets.QPushButton("Trigger")
        self.trigger_button.setObjectName("triggerButton")
        self.trigger_button.setEnabled(False)
        self.trigger_button.clicked.connect(self.trigger_recording)
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_acquisition)
        self.status_label = QtWidgets.QLabel("Ready")
        self.status_label.setMinimumWidth(210)
        controls.addWidget(self.start_button)
        controls.addWidget(self.trigger_button)
        controls.addWidget(self.stop_button)
        controls.addStretch(1)
        controls.addWidget(self.status_label)
        layout.addLayout(controls)
        self.statusBar().showMessage("Ready")
        return card

    def _build_vibration_settings(self, tabs: QtWidgets.QTabWidget) -> None:
        page = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(page)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self.accel_rate = self._double_spin(1, 200000, 5120, decimals=3)
        self.accel_samples = self._int_spin(1, 50_000_000, 5120)
        self.accel_seconds = self._double_spin(0.001, 86400, 1, decimals=6)
        self.accel_min = self._double_spin(-100000, 100000, -50, decimals=3)
        self.accel_max = self._double_spin(-100000, 100000, 50, decimals=3)
        self.accel_sensitivity = self._double_spin(0.001, 1_000_000, 100, decimals=6)
        self.accel_excitation = self._double_spin(0.0, 0.05, 0.004, decimals=6)
        self.accel_settle = self._double_spin(0.0, 120, 5.0, decimals=3)
        self.accel_coupling = QtWidgets.QComboBox()
        self.accel_coupling.addItems(["AC", "DC", "GND", "NONE"])

        fields = [
            ("Sample rate Hz", self.accel_rate),
            ("Segment samples", self.accel_samples),
            ("Segment seconds", self.accel_seconds),
            ("Min g", self.accel_min),
            ("Max g", self.accel_max),
            ("Sensitivity mV/g", self.accel_sensitivity),
            ("IEPE A", self.accel_excitation),
            ("Settle seconds", self.accel_settle),
            ("Coupling", self.accel_coupling),
        ]
        self._add_form_fields(grid, fields)
        tabs.addTab(page, "Vibration")

    def _build_temperature_settings(self, tabs: QtWidgets.QTabWidget) -> None:
        page = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(page)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        temp_config = self.temperature_card_config
        self.temp_rate = self._double_spin(0.001, 1000, temp_config.sample_rate_hz, decimals=6)
        self.temp_samples = self._int_spin(1, 50_000_000, temp_config.segment_samples)
        self.temp_seconds = self._double_spin(0.001, 86400, temp_config.segment_seconds, decimals=6)
        self.temp_min = self._double_spin(-273.15, 10000, temp_config.min_deg_c, decimals=3)
        self.temp_max = self._double_spin(-273.15, 10000, temp_config.max_deg_c, decimals=3)
        self.temp_excitation = self._double_spin(0.0, 0.05, 0.001, decimals=6)
        self.temp_r0 = self._double_spin(0.001, 1_000_000, 100, decimals=6)
        self.temp_rtd_type = QtWidgets.QComboBox()
        self.temp_rtd_type.addItems(["PT_3851", "PT_3750", "PT_3911", "PT_3916", "PT_3920", "PT_3928"])
        self.temp_wire = QtWidgets.QComboBox()
        self.temp_wire.addItems(["FOUR_WIRE", "THREE_WIRE", "TWO_WIRE"])

        fields = [
            ("Sample rate Hz", self.temp_rate),
            ("Segment samples", self.temp_samples),
            ("Segment seconds", self.temp_seconds),
            ("Min degC", self.temp_min),
            ("Max degC", self.temp_max),
            ("RTD excitation A", self.temp_excitation),
            ("RTD R0 ohms", self.temp_r0),
            ("RTD type", self.temp_rtd_type),
            ("RTD wiring", self.temp_wire),
        ]
        self._add_form_fields(grid, fields)
        tabs.addTab(page, "Temperature")

    def _build_spindle_settings(self, tabs: QtWidgets.QTabWidget) -> None:
        config = self.spindle_config
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        conn_grid = QtWidgets.QGridLayout()
        conn_grid.setColumnStretch(1, 1)
        self.spindle_port_combo = QtWidgets.QComboBox()
        self.spindle_port_combo.setEditable(True)
        ports = self._available_serial_ports()
        if config.serial.port not in ports:
            ports.append(config.serial.port)
        self.spindle_port_combo.addItems(ports)
        self.spindle_port_combo.setCurrentText(config.serial.port)
        self.spindle_baud = self._int_spin(1200, 2_000_000, config.serial.baudrate)
        self.spindle_connect_button = QtWidgets.QPushButton("Connect")
        self.spindle_connect_button.clicked.connect(self.toggle_spindle_connection)
        self.spindle_connection_label = QtWidgets.QLabel("Disconnected")
        self.spindle_connection_label.setObjectName("small")
        conn_grid.addWidget(QtWidgets.QLabel("COM"), 0, 0)
        conn_grid.addWidget(self.spindle_port_combo, 0, 1)
        conn_grid.addWidget(QtWidgets.QLabel("Baud"), 1, 0)
        conn_grid.addWidget(self.spindle_baud, 1, 1)
        conn_grid.addWidget(self.spindle_connect_button, 0, 2)
        conn_grid.addWidget(self.spindle_connection_label, 1, 2)
        layout.addLayout(conn_grid)

        control_grid = QtWidgets.QGridLayout()
        control_grid.setColumnStretch(1, 1)
        self.spindle_target_rpm = self._int_spin(0, config.safety.max_rpm, 500)
        self.spindle_plot_window = self._double_spin(1, 3600, config.ui.plot_window_seconds, decimals=1)
        self.spindle_set_button = QtWidgets.QPushButton("Set Speed")
        self.spindle_set_button.clicked.connect(self.set_spindle_speed)
        self.spindle_stop_button = QtWidgets.QPushButton("Stop Spindle")
        self.spindle_stop_button.clicked.connect(self.stop_spindle)
        self.spindle_set_button.setEnabled(False)
        self.spindle_stop_button.setEnabled(False)
        control_grid.addWidget(QtWidgets.QLabel("Target rpm"), 0, 0)
        control_grid.addWidget(self.spindle_target_rpm, 0, 1)
        control_grid.addWidget(self.spindle_set_button, 0, 2)
        control_grid.addWidget(self.spindle_stop_button, 0, 3)
        control_grid.addWidget(QtWidgets.QLabel("Plot win s"), 1, 0)
        control_grid.addWidget(self.spindle_plot_window, 1, 1)
        layout.addLayout(control_grid)

        metrics = QtWidgets.QGridLayout()
        metrics.setColumnStretch(1, 1)
        self.spindle_actual_label = QtWidgets.QLabel("-- rpm")
        self.spindle_current_label = QtWidgets.QLabel("-- A")
        self.spindle_target_label = QtWidgets.QLabel("Target 0 rpm")
        self.spindle_quality_label = QtWidgets.QLabel("No spindle data")
        self.spindle_actual_label.setObjectName("metric")
        self.spindle_current_label.setObjectName("metric")
        self.spindle_target_label.setObjectName("small")
        self.spindle_quality_label.setObjectName("small")
        metrics.addWidget(QtWidgets.QLabel("Actual"), 0, 0)
        metrics.addWidget(self.spindle_actual_label, 0, 1)
        metrics.addWidget(QtWidgets.QLabel("Current"), 1, 0)
        metrics.addWidget(self.spindle_current_label, 1, 1)
        metrics.addWidget(QtWidgets.QLabel("Target"), 2, 0)
        metrics.addWidget(self.spindle_target_label, 2, 1)
        metrics.addWidget(QtWidgets.QLabel("Status"), 3, 0)
        metrics.addWidget(self.spindle_quality_label, 3, 1, 1, 3)
        layout.addLayout(metrics)
        layout.addStretch(1)
        tabs.addTab(page, "Spindle")

    def _build_record_settings(self, tabs: QtWidgets.QTabWidget) -> None:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(10)

        self.spindle_id_edit = self._line_edit("SP01")
        self.spindle_model_edit = self._line_edit()
        self.spindle_rated_speed_edit = self._line_edit("rpm")
        self.spindle_max_speed_edit = self._line_edit("rpm")
        self.test_date_edit = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.test_date_edit.setCalendarPopup(True)
        self.test_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.accum_runtime_edit = self._line_edit("hours")
        content_layout.addWidget(
            self._metadata_section(
                "Spindle",
                [
                    ("Spindle ID", self.spindle_id_edit),
                    ("Model", self.spindle_model_edit),
                    ("Rated rpm", self.spindle_rated_speed_edit),
                    ("Max rpm", self.spindle_max_speed_edit),
                    ("Test date", self.test_date_edit),
                    ("Runtime h", self.accum_runtime_edit),
                ],
            )
        )

        self.target_speed_edit = self._line_edit("rpm")
        self.actual_speed_edit = self._line_edit("rpm")
        self.ramp_method_edit = self._line_edit("linear / step / manual")
        self.run_duration_edit = self._line_edit("seconds")
        self.preheated_checkbox = QtWidgets.QCheckBox()
        self.thermal_state_combo = QtWidgets.QComboBox()
        self.thermal_state_combo.addItems(["", "cold", "warming", "thermalstable", "cooldown"])
        self.speed_fluctuation_edit = self._line_edit("rpm range")
        self.phase_signal_checkbox = QtWidgets.QCheckBox()
        content_layout.addWidget(
            self._metadata_section(
                "Condition and speed",
                [
                    ("Target rpm", self.target_speed_edit),
                    ("Actual rpm", self.actual_speed_edit),
                    ("Ramp method", self.ramp_method_edit),
                    ("Run duration s", self.run_duration_edit),
                    ("Preheated", self.preheated_checkbox),
                    ("Thermal state", self.thermal_state_combo),
                    ("Speed fluct.", self.speed_fluctuation_edit),
                    ("Phase signal", self.phase_signal_checkbox),
                ],
            )
        )

        self.temp_front_edit = self._line_edit("degC")
        self.temp_rear_edit = self._line_edit("degC")
        self.temp_motor_edit = self._line_edit("degC")
        self.temp_ambient_edit = self._line_edit("degC")
        content_layout.addWidget(
            self._metadata_section(
                "Temperature record",
                [
                    ("Front bearing", self.temp_front_edit),
                    ("Rear bearing", self.temp_rear_edit),
                    ("Motor housing", self.temp_motor_edit),
                    ("Ambient", self.temp_ambient_edit),
                ],
            )
        )

        self.abnormal_noise_checkbox = QtWidgets.QCheckBox()
        self.over_temperature_checkbox = QtWidgets.QCheckBox()
        self.alarm_checkbox = QtWidgets.QCheckBox()
        self.cable_loose_checkbox = QtWidgets.QCheckBox()
        self.acquisition_interrupted_checkbox = QtWidgets.QCheckBox()
        self.misoperation_checkbox = QtWidgets.QCheckBox()
        self.exception_note_edit = self._line_edit()
        content_layout.addWidget(
            self._metadata_section(
                "Exceptions",
                [
                    ("Abnormal noise", self.abnormal_noise_checkbox),
                    ("Over temp", self.over_temperature_checkbox),
                    ("Alarm", self.alarm_checkbox),
                    ("Cable loose", self.cable_loose_checkbox),
                    ("Acq interrupted", self.acquisition_interrupted_checkbox),
                    ("Misoperation", self.misoperation_checkbox),
                    ("Note", self.exception_note_edit),
                ],
            )
        )

        self.rotation_accuracy_checkbox = QtWidgets.QCheckBox()
        self.rotation_accuracy_value_edit = self._line_edit()
        self.rotation_accuracy_position_edit = self._line_edit()
        self.rotation_accuracy_condition_edit = self._line_edit()
        self.sample_label_edit = self._line_edit("normal_baseline")
        content_layout.addWidget(
            self._metadata_section(
                "Follow-up label",
                [
                    ("Accuracy measured", self.rotation_accuracy_checkbox),
                    ("Accuracy value", self.rotation_accuracy_value_edit),
                    ("Measure position", self.rotation_accuracy_position_edit),
                    ("Measure condition", self.rotation_accuracy_condition_edit),
                    ("Label", self.sample_label_edit),
                ],
            )
        )

        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        tabs.addTab(page, "Record")

    def _build_plot_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        toolbar = QtWidgets.QGridLayout()
        title = QtWidgets.QLabel("Live plot")
        title.setObjectName("title")
        self.accel_plot_window = self._double_spin(0.25, 3600, 10, decimals=3)
        self.accel_plot_min = self._double_spin(-100000, 100000, -0.1, decimals=3)
        self.accel_plot_max = self._double_spin(-100000, 100000, 0.1, decimals=3)
        self.temp_plot_window = self._double_spin(1, 86400, 120, decimals=3)
        self.temp_plot_min = self._double_spin(-273.15, 10000, 10, decimals=3)
        self.temp_plot_max = self._double_spin(-273.15, 10000, 50, decimals=3)
        self.temp_alert_threshold = self._double_spin(-273.15, 10000, 80, decimals=1)
        self.current_temp_label = QtWidgets.QLabel("Temp -- degC")
        self.current_temp_label.setObjectName("tempBadge")
        self.current_temp_label.setAlignment(QtCore.Qt.AlignCenter)
        self.current_temp_label.setMinimumWidth(340)
        self.current_temp_label.setMinimumHeight(48)
        for spin in (
            self.accel_plot_window,
            self.accel_plot_min,
            self.accel_plot_max,
            self.temp_plot_window,
            self.temp_plot_min,
            self.temp_plot_max,
            self.temp_alert_threshold,
        ):
            spin.setFixedWidth(88)

        toolbar.addWidget(title, 0, 0, 2, 1)
        toolbar.addWidget(QtWidgets.QLabel("Vib win s"), 0, 1)
        toolbar.addWidget(self.accel_plot_window, 0, 2)
        toolbar.addWidget(QtWidgets.QLabel("Y min"), 0, 3)
        toolbar.addWidget(self.accel_plot_min, 0, 4)
        toolbar.addWidget(QtWidgets.QLabel("Y max"), 0, 5)
        toolbar.addWidget(self.accel_plot_max, 0, 6)
        toolbar.addWidget(QtWidgets.QLabel("Temp win s"), 1, 1)
        toolbar.addWidget(self.temp_plot_window, 1, 2)
        toolbar.addWidget(QtWidgets.QLabel("Y min"), 1, 3)
        toolbar.addWidget(self.temp_plot_min, 1, 4)
        toolbar.addWidget(QtWidgets.QLabel("Y max"), 1, 5)
        toolbar.addWidget(self.temp_plot_max, 1, 6)
        toolbar.addWidget(QtWidgets.QLabel("Alert degC"), 0, 8)
        toolbar.addWidget(self.temp_alert_threshold, 0, 9)
        toolbar.addWidget(self.current_temp_label, 1, 8, 1, 2)
        toolbar.setColumnStretch(7, 1)
        layout.addLayout(toolbar)
        self._update_temperature_badge()

        tabs = QtWidgets.QTabWidget()
        for signal_type in PLOT_SIGNAL_TYPES:
            panel = PlotPanel(signal_type)
            self.plot_panels[signal_type] = panel
            tabs.addTab(panel, signal_type.label)
        self.spindle_plot_panel = SpindlePlotPanel()
        tabs.addTab(self.spindle_plot_panel, "Spindle")
        layout.addWidget(tabs, stretch=1)
        return card

    def _build_log_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        title = QtWidgets.QLabel("Log")
        title.setObjectName("title")
        self.log_text = QtWidgets.QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(130)
        layout.addWidget(title)
        layout.addWidget(self.log_text)
        return card

    def _line_edit(self, placeholder: str = "") -> QtWidgets.QLineEdit:
        edit = QtWidgets.QLineEdit()
        if placeholder:
            edit.setPlaceholderText(placeholder)
        return edit

    def _double_spin(self, minimum: float, maximum: float, value: float, decimals: int = 3) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin

    def _int_spin(self, minimum: int, maximum: int, value: int) -> QtWidgets.QSpinBox:
        spin = QtWidgets.QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin

    def _add_form_fields(
        self,
        grid: QtWidgets.QGridLayout,
        fields: list[tuple[str, QtWidgets.QWidget]],
        columns: int = 2,
    ) -> None:
        for index, (label_text, widget) in enumerate(fields):
            row = index // columns
            column = (index % columns) * 2
            grid.addWidget(QtWidgets.QLabel(label_text), row, column)
            grid.addWidget(widget, row, column + 1)

    def _metadata_section(self, title_text: str, fields: list[tuple[str, QtWidgets.QWidget]]) -> QtWidgets.QWidget:
        section = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QtWidgets.QLabel(title_text)
        title.setObjectName("small")
        layout.addWidget(title)

        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)
        self._add_form_fields(grid, fields, columns=1)
        layout.addLayout(grid)
        return section

    def _available_serial_ports(self) -> list[str]:
        try:
            from serial.tools import list_ports

            return [port.device for port in list_ports.comports()]
        except Exception:
            return []

    def _connect_setting_sync(self) -> None:
        self.accel_rate.valueChanged.connect(lambda _value: self._sync_segment_fields("accel", "rate_or_samples"))
        self.accel_samples.valueChanged.connect(lambda _value: self._sync_segment_fields("accel", "rate_or_samples"))
        self.accel_seconds.valueChanged.connect(lambda _value: self._sync_segment_fields("accel", "seconds"))
        self.temp_rate.valueChanged.connect(lambda _value: self._sync_segment_fields("temp", "rate_or_samples"))
        self.temp_samples.valueChanged.connect(lambda _value: self._sync_segment_fields("temp", "rate_or_samples"))
        self.temp_seconds.valueChanged.connect(lambda _value: self._sync_segment_fields("temp", "seconds"))
        for widget in (
            self.accel_plot_window,
            self.accel_plot_min,
            self.accel_plot_max,
            self.temp_plot_window,
            self.temp_plot_min,
            self.temp_plot_max,
        ):
            widget.valueChanged.connect(lambda _value: self._refresh_plot_display_settings())
        self.temp_alert_threshold.valueChanged.connect(lambda _value: self._update_temperature_badge())
        self._refresh_plot_display_settings()

    def _sync_segment_fields(self, group: str, changed: str) -> None:
        if self._syncing_fields:
            return
        rate = self.accel_rate if group == "accel" else self.temp_rate
        samples = self.accel_samples if group == "accel" else self.temp_samples
        seconds = self.accel_seconds if group == "accel" else self.temp_seconds

        try:
            self._syncing_fields = True
            if changed == "seconds":
                samples.setValue(max(1, int(round(rate.value() * seconds.value()))))
            else:
                seconds.setValue(samples.value() / rate.value())
        finally:
            self._syncing_fields = False

    def _apply_temperature_common_settings(self, config: Damx8013Config) -> None:
        if not hasattr(self, "temp_rate"):
            return
        try:
            self._syncing_fields = True
            self.temp_rate.setValue(config.sample_rate_hz)
            self.temp_samples.setValue(config.segment_samples)
            self.temp_seconds.setValue(config.segment_seconds)
            self.temp_min.setValue(config.min_deg_c)
            self.temp_max.setValue(config.max_deg_c)
        finally:
            self._syncing_fields = False

    def refresh_devices(self) -> None:
        if self.controller.running:
            QtWidgets.QMessageBox.information(self, "DAQmx", "Stop acquisition before refreshing devices.")
            return

        self._clear_channel_rows()
        snapshot = {"devices": []}
        try:
            for result in reserve_network_devices(override=self.override_checkbox.isChecked()):
                self._log(f"{result.device}: {'reserved' if result.ok else 'reservation failed'} {result.message}")
                if result.ok:
                    self.reserved_device_names.add(result.device)
            snapshot = get_system_snapshot()
        except Exception as exc:
            self._log(f"NI device refresh failed: {type(exc).__name__}: {exc}")

        try:
            self.temperature_card_config = load_temperature_card_config(TEMPERATURE_CARD_CONFIG_PATH)
            self._apply_temperature_common_settings(self.temperature_card_config)
            self._add_temperature_card_rows(self.temperature_card_config)
        except Exception as exc:
            self._log(f"DAMX-8013 config skipped: {type(exc).__name__}: {exc}")

        for device in snapshot["devices"]:
            signal_type = infer_signal_type(str(device.get("product_type") or ""))
            if signal_type is None:
                continue
            for channel in device.get("ai_channels", []):
                row = ChannelRowWidget(
                    module=device["name"],
                    product_type=str(device.get("product_type") or ""),
                    channel=channel,
                    signal_type=signal_type,
                )
                self.channel_rows.append(row)
                self.channel_list_layout.insertWidget(self.channel_list_layout.count() - 1, row)

        if not self.channel_rows:
            empty = QtWidgets.QLabel("No supported NI 9234, NI 9216, or DAMX-8013 channels detected.")
            self.channel_list_layout.insertWidget(self.channel_list_layout.count() - 1, empty)
        self._log("Device refresh completed.")

    def _add_temperature_card_rows(self, config: Damx8013Config) -> None:
        for channel_number in range(1, DAMX8013_CHANNEL_COUNT + 1):
            row = ChannelRowWidget(
                module=config.port,
                product_type=config.model,
                channel=build_temperature_channel_name(config.port, channel_number),
                signal_type=SignalType.TEMPERATURE_NTC,
            )
            self.channel_rows.append(row)
            self.channel_list_layout.insertWidget(self.channel_list_layout.count() - 1, row)
        self._log(f"DAMX-8013 configured on {config.port}: 2 NTC channel(s).")

    def _clear_channel_rows(self) -> None:
        for row in self.channel_rows:
            row.setParent(None)
            row.deleteLater()
        self.channel_rows.clear()
        while self.channel_list_layout.count() > 1:
            item = self.channel_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def start_acquisition(self) -> None:
        try:
            self._validate_plot_display_settings()
            config = self._build_run_configuration()
            self._refresh_plot_display_settings()
            self.controller.start(config)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Start failed", str(exc))
            return

        self.plot_buffers = {signal_type: {} for signal_type in PLOT_SIGNAL_TYPES}
        for panel in self.plot_panels.values():
            panel.clear()
        self.plot_dirty = set(PLOT_SIGNAL_TYPES)
        self.latest_temperatures.clear()
        self.latest_ntc_temperatures.clear()
        self._update_temperature_badge()
        self.monitoring_started_at = time.monotonic()
        self.recording_started_at = None
        self.recorded_segment_count = 0
        self.last_recorded_segment_count = 0
        self.recording_run_dir = None
        self.start_button.setEnabled(False)
        active_has_saves = self.controller.has_saves
        self.trigger_button.setEnabled(active_has_saves)
        self.stop_button.setEnabled(True)
        self._update_status_display()
        if active_has_saves:
            self._log("Monitoring started. Press Trigger to begin recording.")
        else:
            self._log("Monitoring started with no Save channels selected.")

    def trigger_recording(self) -> None:
        try:
            run_dir = self.controller.trigger_recording()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Trigger failed", str(exc))
            return
        self.recording_started_at = time.monotonic()
        self.recorded_segment_count = 0
        self.recording_run_dir = run_dir
        self._start_spindle_recording(run_dir)
        self.trigger_button.setEnabled(False)
        self._update_status_display()
        self._log(f"Recording triggered: {run_dir}")

    def toggle_spindle_connection(self) -> None:
        if self.spindle_device is None:
            self.connect_spindle()
        else:
            self.disconnect_spindle()

    def connect_spindle(self) -> None:
        try:
            config = self._spindle_config_from_ui()
            save_spindle_config(SPINDLE_CONFIG_PATH, config)
            device = SpindleDevice(config)
            device.connect()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Spindle connect failed", str(exc))
            return

        self.spindle_config = config
        self.spindle_device = device
        self.spindle_stop_event.clear()
        self.spindle_thread = threading.Thread(
            target=self._spindle_poll_loop,
            name="spindle-poll",
            daemon=True,
        )
        self.spindle_thread.start()
        self.spindle_connect_button.setText("Disconnect")
        self.spindle_set_button.setEnabled(True)
        self.spindle_stop_button.setEnabled(True)
        self.spindle_connection_label.setText(f"Connected {config.serial.port}")
        self._log(f"Spindle connected on {config.serial.port}.")

    def disconnect_spindle(self) -> None:
        self._stop_spindle_recording()
        self.spindle_stop_event.set()
        if self.spindle_thread and self.spindle_thread.is_alive():
            self.spindle_thread.join(timeout=1.0)
        if self.spindle_device is not None:
            try:
                self.spindle_device.close()
            except Exception as exc:
                self._log(f"Spindle close failed: {type(exc).__name__}: {exc}")
        self.spindle_device = None
        self.spindle_thread = None
        self.spindle_keepalive_enabled = False
        self.spindle_connect_button.setText("Connect")
        self.spindle_set_button.setEnabled(False)
        self.spindle_stop_button.setEnabled(False)
        self.spindle_connection_label.setText("Disconnected")
        self.spindle_quality_label.setText("Disconnected")

    def set_spindle_speed(self) -> None:
        device = self.spindle_device
        if device is None:
            QtWidgets.QMessageBox.information(self, "Spindle", "Connect spindle before setting speed.")
            return
        rpm = self.spindle_target_rpm.value()
        self._run_spindle_command(
            lambda: device.set_speed_rpm(rpm, prepare=True),
            f"Spindle target set to {rpm} rpm; keepalive enabled.",
        )

    def stop_spindle(self) -> None:
        device = self.spindle_device
        if device is None:
            return
        self._run_spindle_command(device.stop, "Spindle stop command sent.")

    def _run_spindle_command(self, command, success_message: str) -> None:
        def worker() -> None:
            try:
                command()
                self.spindle_events.put(("status", success_message))
            except Exception as exc:
                self.spindle_events.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, name="spindle-command", daemon=True).start()

    def _spindle_config_from_ui(self) -> SpindleConfig:
        base = self.spindle_config
        return SpindleConfig(
            serial=base.serial.__class__(
                port=self.spindle_port_combo.currentText().strip(),
                baudrate=self.spindle_baud.value(),
                device_id=base.serial.device_id,
                timeout=base.serial.timeout,
                write_timeout=base.serial.write_timeout,
            ),
            control=base.control,
            speed=base.speed,
            current=base.current,
            safety=base.safety,
            ui=base.ui.__class__(
                poll_interval_ms=base.ui.poll_interval_ms,
                keepalive_interval_ms=base.ui.keepalive_interval_ms,
                plot_window_seconds=max(1, int(round(self.spindle_plot_window.value()))),
            ),
        )

    def _spindle_poll_loop(self) -> None:
        device = self.spindle_device
        if device is None:
            return
        interval = max(0.05, self.spindle_config.ui.poll_interval_ms / 1000.0)
        keepalive_interval = max(0.05, self.spindle_config.ui.keepalive_interval_ms / 1000.0)
        last_keepalive = 0.0
        while not self.spindle_stop_event.is_set():
            now = time.monotonic()
            keepalive_enabled = device.target_rpm > 0
            if now - last_keepalive >= keepalive_interval:
                try:
                    keepalive_enabled = device.keepalive()
                except Exception as exc:
                    self.spindle_events.put(("status", f"Spindle keepalive failed: {type(exc).__name__}: {exc}"))
                last_keepalive = now

            timestamp = time.monotonic()
            reading = device.read()
            self.spindle_events.put(
                ("reading", timestamp, reading, device.target_rpm, keepalive_enabled)
            )
            self.spindle_stop_event.wait(interval)

    def _poll_spindle_events(self) -> None:
        while True:
            try:
                event = self.spindle_events.get_nowait()
            except queue.Empty:
                return
            kind = event[0]
            if kind == "reading":
                _kind, timestamp, reading, target_rpm, keepalive_enabled = event
                self._handle_spindle_reading(timestamp, reading, target_rpm, keepalive_enabled)
            elif kind == "status":
                self.spindle_quality_label.setText(event[1])
                self._log(event[1])
            elif kind == "error":
                self.spindle_quality_label.setText(event[1])
                QtWidgets.QMessageBox.critical(self, "Spindle command failed", event[1])

    def _handle_spindle_reading(
        self,
        timestamp: float,
        reading: SpindleReading,
        target_rpm: int,
        keepalive_enabled: bool,
    ) -> None:
        self.latest_spindle_reading = reading
        self.spindle_keepalive_enabled = keepalive_enabled
        self.spindle_actual_label.setText(f"{reading.speed_rpm:.2f} rpm")
        self.spindle_current_label.setText(f"{reading.current_a:.2f} A")
        self.spindle_target_label.setText(f"Target {target_rpm} rpm")
        quality = [
            "speed OK" if reading.speed_ok else "speed fallback",
            "current OK" if reading.current_ok else "current fallback",
            "keepalive on" if keepalive_enabled else "keepalive off",
        ]
        self.spindle_quality_label.setText(" / ".join(quality))
        self.spindle_plot_panel.append(timestamp, reading, self.spindle_plot_window.value())
        if self.spindle_recorder is not None:
            self.spindle_recorder.write(timestamp, reading, target_rpm, keepalive_enabled)

    def _start_spindle_recording(self, run_dir: Path) -> None:
        self._stop_spindle_recording()
        if self.spindle_device is None:
            return
        self.spindle_recorder = SpindleTelemetryRecorder(run_dir, self.spindle_config)
        self._annotate_manifest_with_spindle(run_dir)
        self._log("Spindle telemetry recording enabled.")

    def _stop_spindle_recording(self) -> None:
        if self.spindle_recorder is None:
            return
        try:
            self.spindle_recorder.close()
            self._log("Spindle telemetry recording closed.")
        finally:
            self.spindle_recorder = None

    def _annotate_manifest_with_spindle(self, run_dir: Path) -> None:
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            return
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["spindle_control"] = {
                "configuration": self.spindle_config.to_json(),
                "telemetry_csv": "spindle_telemetry.csv",
                "telemetry_json": "spindle_telemetry.json",
            }
            tmp_path = manifest_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp_path.replace(manifest_path)
        except Exception as exc:
            self._log(f"Spindle manifest annotation failed: {type(exc).__name__}: {exc}")

    def _validate_plot_display_settings(self) -> None:
        for signal_type in PLOT_SIGNAL_TYPES:
            self._plot_y_range_for(signal_type)

    def _refresh_plot_display_settings(self) -> None:
        try:
            ranges = {signal_type: self._plot_y_range_for(signal_type) for signal_type in PLOT_SIGNAL_TYPES}
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        for signal_type, y_range in ranges.items():
            self.plot_y_ranges[signal_type] = y_range
            self.plot_panels[signal_type].set_y_range(y_range[0], y_range[1])
        self.plot_dirty.update(PLOT_SIGNAL_TYPES)

    def _plot_window_for(self, signal_type: SignalType) -> float:
        if signal_type == SignalType.ACCELERATION:
            return max(0.1, self.accel_plot_window.value())
        if signal_type in TEMPERATURE_SIGNAL_TYPES:
            return max(0.1, self.temp_plot_window.value())
        return 10.0

    def _plot_y_range_for(self, signal_type: SignalType) -> tuple[float, float]:
        if signal_type == SignalType.ACCELERATION:
            minimum = self.accel_plot_min.value()
            maximum = self.accel_plot_max.value()
            label = "Vibration"
        elif signal_type in TEMPERATURE_SIGNAL_TYPES:
            minimum = self.temp_plot_min.value()
            maximum = self.temp_plot_max.value()
            label = "Temperature"
        else:
            minimum, maximum = DEFAULT_Y_RANGES[signal_type]
            label = signal_type.label
        if minimum >= maximum:
            raise ValueError(f"{label} plot Y min must be smaller than Y max.")
        return (minimum, maximum)

    def stop_acquisition(self) -> None:
        self.controller.stop()
        self._set_status_text("Stopping")
        self.stop_button.setEnabled(False)
        self.trigger_button.setEnabled(False)
        self._log("Stop requested.")

    def _build_run_configuration(self) -> RunConfiguration:
        selections = [row.selection() for row in self.channel_rows if row.plot_checkbox.isChecked() or row.save_checkbox.isChecked()]
        if not selections:
            raise ValueError("Select at least one channel for Plot or Save.")

        groups = []
        for signal_type in SignalType:
            channels = [selection for selection in selections if selection.signal_type == signal_type]
            if not channels:
                continue
            settings = self._settings_for_signal_type(signal_type)
            groups.append(AcquisitionGroup(signal_type=signal_type, channels=channels, settings=settings))

        return RunConfiguration(
            output_dir=Path(self.output_dir_edit.text()).expanduser(),
            groups=groups,
            operator_note=self.note_edit.text().strip(),
            experiment_record=self._experiment_record(),
            override_network_reservation=self.override_checkbox.isChecked(),
        )

    def _settings_for_signal_type(
        self,
        signal_type: SignalType,
    ) -> AccelerationSettings | TemperatureRtdSettings | TemperatureNtcSettings:
        if signal_type == SignalType.ACCELERATION:
            return self._acceleration_settings()
        if signal_type == SignalType.TEMPERATURE_RTD:
            return self._temperature_settings()
        if signal_type == SignalType.TEMPERATURE_NTC:
            return self._temperature_ntc_settings()
        raise ValueError(f"Unsupported signal type: {signal_type}")

    def _acceleration_settings(self) -> AccelerationSettings:
        min_g = self.accel_min.value()
        max_g = self.accel_max.value()
        if min_g >= max_g:
            raise ValueError("Vibration Min g must be smaller than Max g.")
        return AccelerationSettings(
            sample_rate_hz=self.accel_rate.value(),
            segment_samples=self.accel_samples.value(),
            segment_seconds=self.accel_samples.value() / self.accel_rate.value(),
            min_value=min_g,
            max_value=max_g,
            sensitivity_mv_per_g=self.accel_sensitivity.value(),
            excitation_current_a=self.accel_excitation.value(),
            coupling=self.accel_coupling.currentText(),
            settle_seconds=self.accel_settle.value(),
        )

    def _temperature_settings(self) -> TemperatureRtdSettings:
        min_temp = self.temp_min.value()
        max_temp = self.temp_max.value()
        if min_temp >= max_temp:
            raise ValueError("Temperature Min degC must be smaller than Max degC.")
        return TemperatureRtdSettings(
            sample_rate_hz=self.temp_rate.value(),
            segment_samples=self.temp_samples.value(),
            segment_seconds=self.temp_samples.value() / self.temp_rate.value(),
            min_value=min_temp,
            max_value=max_temp,
            rtd_type=self.temp_rtd_type.currentText(),
            resistance_config=self.temp_wire.currentText(),
            excitation_current_a=self.temp_excitation.value(),
            r0_ohms=self.temp_r0.value(),
        )

    def _temperature_ntc_settings(self) -> TemperatureNtcSettings:
        min_temp = self.temp_min.value()
        max_temp = self.temp_max.value()
        if min_temp >= max_temp:
            raise ValueError("Temperature Min degC must be smaller than Max degC.")
        base = self.temperature_card_config
        config = Damx8013Config(
            model=base.model,
            port=base.port,
            slave_id=base.slave_id,
            baudrate=base.baudrate,
            data_bits=base.data_bits,
            parity=base.parity,
            stop_bits=base.stop_bits,
            timeout_s=base.timeout_s,
            channel_count=base.channel_count,
            sample_rate_hz=self.temp_rate.value(),
            segment_samples=self.temp_samples.value(),
            min_deg_c=min_temp,
            max_deg_c=max_temp,
            r_kohms=base.r_kohms,
            b_value=base.b_value,
            sync_parameters_on_start=base.sync_parameters_on_start,
        )
        save_temperature_card_config(TEMPERATURE_CARD_CONFIG_PATH, config)
        self.temperature_card_config = config
        return temperature_ntc_settings_from_config(config)

    def _experiment_record(self) -> ExperimentRecord:
        target_speed = self._optional_float(self.target_speed_edit, "Target rpm")
        actual_speed = self._optional_float(self.actual_speed_edit, "Actual rpm")
        if target_speed is None and self.spindle_device is not None and self.spindle_device.target_rpm > 0:
            target_speed = float(self.spindle_device.target_rpm)
        if actual_speed is None and self.latest_spindle_reading is not None:
            actual_speed = self.latest_spindle_reading.speed_rpm
        return ExperimentRecord(
            spindle=SpindleInfo(
                spindle_id=self.spindle_id_edit.text().strip(),
                model=self.spindle_model_edit.text().strip(),
                rated_speed_rpm=self._optional_float(self.spindle_rated_speed_edit, "Rated rpm"),
                max_speed_rpm=self._optional_float(self.spindle_max_speed_edit, "Max rpm"),
                test_date=self.test_date_edit.date().toString("yyyy-MM-dd"),
                accumulated_runtime_hours=self._optional_float(self.accum_runtime_edit, "Runtime h"),
            ),
            condition=OperatingCondition(
                target_speed_rpm=target_speed,
                actual_speed_rpm=actual_speed,
                ramp_method=self.ramp_method_edit.text().strip(),
                run_duration_s=self._optional_float(self.run_duration_edit, "Run duration s"),
                preheated=self.preheated_checkbox.isChecked(),
                thermal_state=self.thermal_state_combo.currentText(),
            ),
            temperature=TemperatureRecord(
                front_bearing_deg_c=self._optional_float(self.temp_front_edit, "Front bearing temperature"),
                rear_bearing_deg_c=self._optional_float(self.temp_rear_edit, "Rear bearing temperature"),
                motor_housing_deg_c=self._optional_float(self.temp_motor_edit, "Motor housing temperature"),
                ambient_deg_c=self._optional_float(self.temp_ambient_edit, "Ambient temperature"),
            ),
            speed=SpeedRecord(
                set_speed_rpm=target_speed,
                actual_speed_rpm=actual_speed,
                fluctuation_rpm=self.speed_fluctuation_edit.text().strip(),
                has_phase_signal=self.phase_signal_checkbox.isChecked(),
            ),
            exception=ExceptionRecord(
                abnormal_noise=self.abnormal_noise_checkbox.isChecked(),
                over_temperature=self.over_temperature_checkbox.isChecked(),
                alarm=self.alarm_checkbox.isChecked(),
                cable_loose=self.cable_loose_checkbox.isChecked(),
                acquisition_interrupted=self.acquisition_interrupted_checkbox.isChecked(),
                misoperation=self.misoperation_checkbox.isChecked(),
                note=self.exception_note_edit.text().strip(),
            ),
            followup=FollowupLabel(
                rotation_accuracy_measured=self.rotation_accuracy_checkbox.isChecked(),
                rotation_accuracy_value=self.rotation_accuracy_value_edit.text().strip(),
                measurement_position=self.rotation_accuracy_position_edit.text().strip(),
                measurement_condition=self.rotation_accuracy_condition_edit.text().strip(),
                label=self.sample_label_edit.text().strip(),
            ),
        )

    def _optional_float(self, edit: QtWidgets.QLineEdit, label: str) -> float | None:
        text = edit.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"{label} must be a number.") from exc

    def _browse_output_dir(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output folder", self.output_dir_edit.text())
        if path:
            self.output_dir_edit.setText(path)

    def _poll_events(self) -> None:
        self._poll_spindle_events()
        try:
            while True:
                event = self.controller.events.get_nowait()
                if event.kind == "data" and event.payload:
                    self._handle_data_event(event.group, event.payload)
                elif event.kind == "error":
                    self._log(f"ERROR [{event.group}] {event.message}")
                    if event.payload and event.payload.get("traceback"):
                        self._log(event.payload["traceback"])
                    QtWidgets.QMessageBox.critical(self, "Acquisition error", event.message)
                elif event.kind == "saved":
                    self.recorded_segment_count += 1
                    self.last_recorded_segment_count = self.recorded_segment_count
                    self._log(event.message)
                elif event.kind == "recording_started":
                    if self.recording_started_at is None:
                        self.recording_started_at = time.monotonic()
                    if event.payload and event.payload.get("run_dir"):
                        self.recording_run_dir = Path(event.payload["run_dir"])
                    self._log(event.message)
                elif event.kind in {"status", "started", "stopped"}:
                    self._log(event.message)
                else:
                    self._log(event.message or event.kind)
        except queue.Empty:
            pass

        if self.controller.running and self.controller.poll_finished():
            self._finish_monitoring()
        else:
            self._update_status_display()

    def _finish_monitoring(self) -> None:
        self._stop_spindle_recording()
        final_segments = self.last_recorded_segment_count
        self.monitoring_started_at = None
        self.recording_started_at = None
        self.recording_run_dir = None
        self.start_button.setEnabled(True)
        self.trigger_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        status = "Ready"
        if final_segments:
            status = f"Ready | last segments {final_segments}"
        self._set_status_text(status)

    def _update_status_display(self) -> None:
        if self.recording_started_at is not None:
            elapsed = time.monotonic() - self.recording_started_at
            self._set_status_text(
                f"Recording {format_elapsed(elapsed)} | segments {self.recorded_segment_count}"
            )
            return
        if self.monitoring_started_at is not None and self.controller.running:
            elapsed = time.monotonic() - self.monitoring_started_at
            self._set_status_text(f"Monitoring {format_elapsed(elapsed)} | not recording")
            return
        if not self.controller.running:
            if self.last_recorded_segment_count:
                self._set_status_text(f"Ready | last segments {self.last_recorded_segment_count}")
            else:
                self._set_status_text("Ready")

    def _set_status_text(self, text: str) -> None:
        if text.startswith("Recording"):
            self.status_label.setText("Recording")
        elif text.startswith("Monitoring"):
            self.status_label.setText("Monitoring")
        elif text.startswith("Ready"):
            self.status_label.setText("Ready")
        else:
            self.status_label.setText(text)
        self.statusBar().showMessage(text)

    def _handle_data_event(self, group: str, payload: dict) -> None:
        signal_type = SignalType(group)
        plot_signal_type = plot_signal_type_for(signal_type)
        channels = payload["channels"]
        time_s = np.asarray(payload["time_s"], dtype=float)
        data = np.asarray(payload["data"], dtype=float)
        unit = payload["unit"]
        window = self._plot_window_for(signal_type)
        sample_rate_hz = float(payload.get("sample_rate_hz") or 1.0)

        for index, channel in enumerate(channels):
            plot_channel = plot_channel_label(signal_type, channel)
            if signal_type in TEMPERATURE_SIGNAL_TYPES and data.shape[1]:
                latest_value = float(data[index, -1])
                if np.isfinite(latest_value):
                    self.latest_temperatures[plot_channel] = latest_value
                    if signal_type == SignalType.TEMPERATURE_NTC:
                        self.latest_ntc_temperatures[plot_channel] = latest_value
            buffer = self.plot_buffers[plot_signal_type].get(plot_channel)
            if buffer is None:
                buffer = PlotBuffer(unit=unit)
                self.plot_buffers[plot_signal_type][plot_channel] = buffer
            buffer.append(
                time_s,
                data[index],
                window_s=window,
                source_sample_rate_hz=sample_rate_hz,
                max_points=DISPLAY_MAX_POINTS_PER_CURVE,
            )
        self.plot_dirty.add(plot_signal_type)
        if signal_type in TEMPERATURE_SIGNAL_TYPES:
            self._update_temperature_badge()

    def _update_temperature_badge(self) -> None:
        if not hasattr(self, "current_temp_label"):
            return
        if not self.latest_ntc_temperatures:
            self.current_temp_label.setText("NTC -- degC")
            self.current_temp_label.setToolTip("No NTC temperature data received yet.")
            self.current_temp_label.setStyleSheet(
                "QLabel#tempBadge { padding: 8px 12px; border: 2px solid #94a3b8; "
                "border-radius: 5px; background: #e2e8f0; color: #172033; "
                "font-size: 18pt; font-weight: 900; }"
            )
            return

        channel, value = max(self.latest_ntc_temperatures.items(), key=lambda item: item[1])
        threshold = self.temp_alert_threshold.value()
        margin = max(1.0, min(5.0, abs(threshold) * 0.05))
        if value >= threshold:
            state = "OVER"
            colors = ("#7f1d1d", "#ffffff", "#b91c1c")
        elif value >= threshold - margin:
            state = "NEAR"
            colors = ("#92400e", "#ffffff", "#d97706")
        else:
            state = "OK"
            colors = ("#14532d", "#ffffff", "#16a34a")

        self.current_temp_label.setText(f"NTC {value:.1f} degC {state}")
        lines = [
            f"Alert threshold: {threshold:.1f} degC",
            f"Max NTC channel: {channel}",
            "",
        ]
        lines.extend(
            f"{name}: {temperature:.1f} degC"
            for name, temperature in sorted(
                self.latest_ntc_temperatures.items(),
                key=lambda item: plot_channel_sort_key(item[0]),
            )
        )
        self.current_temp_label.setToolTip("\n".join(lines))
        self.current_temp_label.setStyleSheet(
            "QLabel#tempBadge { padding: 8px 12px; border-radius: 5px; "
            f"border: 2px solid {colors[2]}; background: {colors[0]}; "
            f"color: {colors[1]}; font-size: 18pt; font-weight: 900; }}"
        )

    def _redraw_plots(self) -> None:
        for signal_type in list(self.plot_dirty):
            self.plot_panels[signal_type].update_buffers(
                self.plot_buffers[signal_type],
                window_s=self._plot_window_for(signal_type),
                y_range=self.plot_y_ranges.get(signal_type, DEFAULT_Y_RANGES[signal_type]),
            )
        self.plot_dirty.clear()

    def closeEvent(self, event: QtCore.QEvent) -> None:
        self.status_label.setText("Closing")
        if self.spindle_device is not None and self.spindle_device.target_rpm > 0:
            choice = QtWidgets.QMessageBox.question(
                self,
                "Spindle running",
                "Spindle target speed is greater than 0 rpm. Send stop command before closing?",
            )
            if choice == QtWidgets.QMessageBox.Yes:
                try:
                    self.spindle_device.stop()
                except Exception as exc:
                    self._log(f"Spindle stop on close failed: {type(exc).__name__}: {exc}")
        self.disconnect_spindle()
        self.controller.shutdown()
        try:
            release_results = unreserve_network_devices(self.reserved_device_names)
        except Exception as exc:
            self._log(f"NI reservation release skipped: {type(exc).__name__}: {exc}")
        else:
            for result in release_results:
                self._log(f"{result.device}: {'released' if result.ok else 'release failed'} {result.message}")
                if result.ok:
                    self.reserved_device_names.discard(result.device)
        event.accept()

    def _log(self, message: str) -> None:
        self.log_text.appendPlainText(message.rstrip())
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


class ChannelRowWidget(QtWidgets.QWidget):
    def __init__(self, module: str, product_type: str, channel: str, signal_type: SignalType) -> None:
        super().__init__()
        self.module = module
        self.product_type = product_type
        self.channel = channel
        self.signal_type = signal_type
        self.sensor_metadata = SensorMetadata()

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        module_label = QtWidgets.QLabel(module)
        module_label.setToolTip(module)
        module_label.setFixedWidth(118)
        channel_label = QtWidgets.QLabel(channel)
        channel_label.setToolTip(channel)
        channel_label.setFixedWidth(158)
        signal_label = QtWidgets.QLabel(signal_type.label)
        signal_label.setFixedWidth(110)
        self.plot_checkbox = QtWidgets.QCheckBox()
        self.plot_checkbox.setFixedWidth(38)
        self.save_checkbox = QtWidgets.QCheckBox()
        self.save_checkbox.setFixedWidth(38)
        self.metadata_button = QtWidgets.QPushButton("Meta")
        self.metadata_button.setFixedWidth(46)
        self.metadata_button.clicked.connect(self._edit_metadata)

        layout.addWidget(module_label)
        layout.addWidget(channel_label)
        layout.addWidget(signal_label)
        layout.addWidget(self.plot_checkbox)
        layout.addWidget(self.save_checkbox)
        layout.addWidget(self.metadata_button)

    def selection(self) -> ChannelSelection:
        return ChannelSelection(
            physical_name=self.channel,
            device_name=self.module,
            product_type=self.product_type,
            signal_type=self.signal_type,
            visualize=self.plot_checkbox.isChecked(),
            save=self.save_checkbox.isChecked(),
            sensor=self.sensor_metadata,
        )

    def _edit_metadata(self) -> None:
        dialog = ChannelMetadataDialog(self.sensor_metadata, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.sensor_metadata = dialog.metadata()
            has_metadata = any(
                (
                    self.sensor_metadata.sensor_id,
                    self.sensor_metadata.measurement_position,
                    self.sensor_metadata.direction,
                    self.sensor_metadata.mounting_method,
                )
            )
            self.metadata_button.setText("Meta*" if has_metadata else "Meta")


class ChannelMetadataDialog(QtWidgets.QDialog):
    def __init__(self, metadata: SensorMetadata, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Channel metadata")
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.sensor_id_edit = QtWidgets.QLineEdit(metadata.sensor_id)
        self.position_edit = QtWidgets.QLineEdit(metadata.measurement_position)
        self.direction_edit = QtWidgets.QLineEdit(metadata.direction)
        self.mounting_edit = QtWidgets.QLineEdit(metadata.mounting_method)
        form.addRow("Sensor ID", self.sensor_id_edit)
        form.addRow("Position", self.position_edit)
        form.addRow("Direction", self.direction_edit)
        form.addRow("Mounting", self.mounting_edit)
        layout.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def metadata(self) -> SensorMetadata:
        return SensorMetadata(
            sensor_id=self.sensor_id_edit.text().strip(),
            measurement_position=self.position_edit.text().strip(),
            direction=self.direction_edit.text().strip(),
            mounting_method=self.mounting_edit.text().strip(),
        )


@dataclass
class PlotTrace:
    widget: pg.PlotWidget
    curve_a: pg.PlotDataItem
    curve_b: pg.PlotDataItem
    y_range: tuple[float, float]
    window_s: float


class PlotPanel(QtWidgets.QWidget):
    def __init__(self, signal_type: SignalType) -> None:
        super().__init__()
        self.signal_type = signal_type
        self.plots: dict[str, PlotTrace] = {}
        self.y_range = DEFAULT_Y_RANGES[signal_type]
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(6)
        self.empty_label = QtWidgets.QLabel("No live data")
        self.empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self.layout.addWidget(self.empty_label, stretch=1)

    def clear(self) -> None:
        for trace in self.plots.values():
            trace.widget.setParent(None)
            trace.widget.deleteLater()
        self.plots.clear()
        if self.empty_label.parent() is None:
            self.layout.addWidget(self.empty_label, stretch=1)
        self.empty_label.show()

    def set_y_range(self, minimum: float, maximum: float) -> None:
        self.y_range = (minimum, maximum)
        for trace in self.plots.values():
            self._apply_view_limits(trace, trace.window_s, self.y_range)

    def update_buffers(
        self,
        buffers: dict[str, "PlotBuffer"],
        window_s: float,
        y_range: tuple[float, float],
    ) -> None:
        if not buffers:
            self.empty_label.show()
            return
        self.empty_label.hide()

        for channel, buffer in sorted(buffers.items(), key=lambda item: plot_channel_sort_key(item[0])):
            if buffer.count == 0:
                continue
            trace = self._ensure_plot(channel, buffer.unit, window_s, y_range)
            self._set_plot_title(trace.widget, plot_title(channel, buffer))
            self._apply_view_limits(trace, window_s, y_range)
            segments = buffer.display_segments()
            if segments:
                x_a, y_a = segments[0]
                trace.curve_a.setData(x_a, y_a)
                if len(segments) > 1:
                    x_b, y_b = segments[1]
                    trace.curve_b.setData(x_b, y_b)
                else:
                    trace.curve_b.setData([], [])

    def _ensure_plot(
        self,
        channel: str,
        unit: str,
        window_s: float,
        y_range: tuple[float, float],
    ) -> PlotTrace:
        existing = self.plots.get(channel)
        if existing is not None:
            return existing

        plot_widget = pg.PlotWidget(background="w")
        plot_widget.setMinimumHeight(120)
        plot_widget.showGrid(x=True, y=True, alpha=0.25)
        plot_widget.setLabel("left", unit)
        plot_widget.setLabel("bottom", "Task time", units="s")
        self._set_plot_title(plot_widget, channel)
        plot_widget.setMouseEnabled(x=False, y=False)
        plot_widget.setMenuEnabled(False)
        plot_widget.enableAutoRange(x=False, y=False)
        pen_color = plot_color_for_channel(channel)
        pen_width = 3.0 if self.signal_type in TEMPERATURE_SIGNAL_TYPES else 1.0
        curve_kwargs = {"pen": pg.mkPen(pen_color, width=pen_width)}
        curve_a = plot_widget.plot(**curve_kwargs)
        curve_b = plot_widget.plot(**curve_kwargs)
        for curve in (curve_a, curve_b):
            curve.setClipToView(True)
            curve.setDownsampling(auto=True, method="peak")
        self.layout.addWidget(plot_widget, stretch=1)
        trace = PlotTrace(
            widget=plot_widget,
            curve_a=curve_a,
            curve_b=curve_b,
            y_range=(0.0, 0.0),
            window_s=0.0,
        )
        self._apply_view_limits(trace, window_s, y_range)
        self.plots[channel] = trace
        self._reorder_plots()
        return trace

    def _set_plot_title(self, plot_widget: pg.PlotWidget, title: str) -> None:
        if self.signal_type in TEMPERATURE_SIGNAL_TYPES:
            plot_widget.setTitle(title, color="#111827", size="14pt")
            return
        plot_widget.setTitle(title, color="#344054", size="10pt")

    def _reorder_plots(self) -> None:
        for channel in sorted(self.plots, key=plot_channel_sort_key):
            trace = self.plots[channel]
            self.layout.removeWidget(trace.widget)
            self.layout.addWidget(trace.widget, stretch=1)

    def _apply_view_limits(
        self,
        trace: PlotTrace,
        window_s: float,
        y_range: tuple[float, float],
    ) -> None:
        if trace.window_s != window_s:
            trace.widget.setXRange(-window_s, 0.0, padding=0.0)
            trace.window_s = window_s
        if trace.y_range != y_range:
            trace.widget.setYRange(y_range[0], y_range[1], padding=0.0)
            trace.y_range = y_range


class SpindlePlotPanel(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.times: deque[float] = deque(maxlen=10000)
        self.speed_values: deque[float] = deque(maxlen=10000)
        self.current_values: deque[float] = deque(maxlen=10000)
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(6)

        self.speed_plot = self._make_plot("Speed -- rpm", "rpm")
        self.current_plot = self._make_plot("Current -- A", "A")
        self.speed_curve = self.speed_plot.plot(pen=pg.mkPen("#1d4ed8", width=2.0))
        self.current_curve = self.current_plot.plot(pen=pg.mkPen("#d97706", width=2.0))
        self.layout.addWidget(self.speed_plot, stretch=1)
        self.layout.addWidget(self.current_plot, stretch=1)

    def _make_plot(self, title: str, unit: str) -> pg.PlotWidget:
        plot_widget = pg.PlotWidget(background="w")
        plot_widget.setMinimumHeight(150)
        plot_widget.showGrid(x=True, y=True, alpha=0.25)
        plot_widget.setLabel("left", unit)
        plot_widget.setLabel("bottom", "Task time", units="s")
        plot_widget.setTitle(title, size="10pt")
        plot_widget.setMouseEnabled(x=False, y=False)
        plot_widget.setMenuEnabled(False)
        plot_widget.enableAutoRange(x=False, y=False)
        return plot_widget

    def append(self, timestamp: float, reading: SpindleReading, window_s: float) -> None:
        self.times.append(timestamp)
        self.speed_values.append(reading.speed_rpm)
        self.current_values.append(reading.current_a)
        self.speed_plot.setTitle(f"Speed {reading.speed_rpm:.2f} rpm", size="10pt")
        self.current_plot.setTitle(f"Current {reading.current_a:.2f} A", size="10pt")
        self._redraw(window_s)

    def _redraw(self, window_s: float) -> None:
        if not self.times:
            return
        times = np.asarray(self.times, dtype=np.float64)
        latest = times[-1]
        x = times - latest
        mask = x >= -window_s
        speed = np.asarray(self.speed_values, dtype=np.float64)[mask]
        current = np.asarray(self.current_values, dtype=np.float64)[mask]
        x = x[mask]
        self.speed_curve.setData(x, speed)
        self.current_curve.setData(x, current)
        self.speed_plot.setXRange(-window_s, 0.0, padding=0.0)
        self.current_plot.setXRange(-window_s, 0.0, padding=0.0)
        self._set_y_range(self.speed_plot, speed, minimum_span=10.0)
        self._set_y_range(self.current_plot, current, minimum_span=0.2)

    def clear(self) -> None:
        self.times.clear()
        self.speed_values.clear()
        self.current_values.clear()
        self.speed_curve.setData([], [])
        self.current_curve.setData([], [])
        self.speed_plot.setTitle("Speed -- rpm", size="10pt")
        self.current_plot.setTitle("Current -- A", size="10pt")

    def _set_y_range(self, plot_widget: pg.PlotWidget, values: np.ndarray, minimum_span: float) -> None:
        if not len(values):
            return
        low = min(float(np.min(values)), 0.0)
        high = max(float(np.max(values)), 0.0)
        span = max(high - low, minimum_span)
        center = (low + high) / 2.0
        plot_widget.setYRange(center - span * 0.6, center + span * 0.6, padding=0.0)


class PlotBuffer:
    def __init__(self, unit: str) -> None:
        self.unit = unit
        self.latest_value: float | None = None
        self.capacity = 0
        self.x = np.array([], dtype=np.float64)
        self.y = np.array([], dtype=np.float64)
        self.start = 0
        self.count = 0

    def append(
        self,
        time_s: np.ndarray,
        values: np.ndarray,
        window_s: float,
        source_sample_rate_hz: float,
        max_points: int,
    ) -> None:
        if not len(time_s):
            return
        latest_value = float(values[-1])
        if np.isfinite(latest_value):
            self.latest_value = latest_value
        target_capacity = self._target_capacity(time_s, window_s, source_sample_rate_hz, max_points)
        if target_capacity != self.capacity:
            self._resize(target_capacity, window_s)

        display_values = self._decimate(time_s, values, window_s)
        if not len(display_values):
            return
        self._append_to_ring(display_values)

    def display_segments(self) -> list[tuple[np.ndarray, np.ndarray]]:
        if self.count == 0:
            return []
        if self.count < self.capacity:
            return [(self.x[-self.count :], self.y[: self.count])]

        end = self.start + self.count
        if end <= self.capacity:
            return [(self.x, self.y[self.start:end])]

        first_count = self.capacity - self.start
        second_count = end - self.capacity
        return [
            (self.x[:first_count], self.y[self.start :]),
            (self.x[first_count : first_count + second_count], self.y[:second_count]),
        ]

    def _target_capacity(
        self,
        time_s: np.ndarray,
        window_s: float,
        source_sample_rate_hz: float,
        max_points: int,
    ) -> int:
        input_rate = source_sample_rate_hz
        if len(time_s) > 1:
            dt = np.diff(time_s)
            median_dt = float(np.median(dt[dt > 0])) if np.any(dt > 0) else 0.0
            if median_dt > 0:
                input_rate = 1.0 / median_dt
        return max(2, min(max_points, int(np.ceil(window_s * input_rate))))

    def _resize(self, capacity: int, window_s: float) -> None:
        old_segments = self.display_segments()
        old_values = np.concatenate([segment[1] for segment in old_segments]) if old_segments else np.array([])

        self.capacity = capacity
        self.x = np.linspace(-window_s, 0.0, capacity, dtype=np.float64)
        self.y = np.empty(capacity, dtype=np.float64)
        self.start = 0
        self.count = 0

        if len(old_values):
            self._append_to_ring(old_values[-capacity:])

    def _decimate(self, time_s: np.ndarray, values: np.ndarray, window_s: float) -> np.ndarray:
        desired_rate = self.capacity / window_s
        input_rate = desired_rate
        if len(time_s) > 1:
            dt = np.diff(time_s)
            median_dt = float(np.median(dt[dt > 0])) if np.any(dt > 0) else 0.0
            if median_dt > 0:
                input_rate = 1.0 / median_dt
        stride = max(1, int(np.ceil(input_rate / desired_rate)))
        return np.asarray(values[::stride], dtype=np.float64)

    def _append_to_ring(self, values: np.ndarray) -> None:
        if not len(values):
            return
        if len(values) >= self.capacity:
            self.y[:] = values[-self.capacity :]
            self.start = 0
            self.count = self.capacity
            return

        end = (self.start + self.count) % self.capacity
        overflow = max(0, self.count + len(values) - self.capacity)
        if overflow:
            self.start = (self.start + overflow) % self.capacity
            self.count = self.capacity
        else:
            self.count += len(values)

        first_count = min(len(values), self.capacity - end)
        self.y[end : end + first_count] = values[:first_count]
        remaining = len(values) - first_count
        if remaining:
            self.y[:remaining] = values[first_count:]


def infer_signal_type(product_type: str) -> SignalType | None:
    product = product_type.lower()
    if "9234" in product:
        return SignalType.ACCELERATION
    if "9216" in product:
        return SignalType.TEMPERATURE_RTD
    return None


def plot_signal_type_for(signal_type: SignalType) -> SignalType:
    if signal_type in TEMPERATURE_SIGNAL_TYPES:
        return SignalType.TEMPERATURE_NTC
    return signal_type


def plot_channel_label(signal_type: SignalType, channel: str) -> str:
    if signal_type == SignalType.TEMPERATURE_NTC:
        return f"NTC {channel}"
    if signal_type == SignalType.TEMPERATURE_RTD:
        return f"RTD {channel}"
    return channel


def plot_channel_sort_key(channel: str) -> tuple[int, str]:
    if channel.startswith("NTC "):
        return (0, channel)
    if channel.startswith("RTD "):
        return (1, channel)
    return (0, channel)


def plot_color_for_channel(channel: str) -> str:
    if channel.startswith("NTC "):
        return "#b42318"
    if channel.startswith("RTD "):
        return "#0f766e"
    return "#1f77b4"


def plot_title(channel: str, buffer: PlotBuffer) -> str:
    if buffer.latest_value is None:
        return channel
    if channel.startswith(("NTC ", "RTD ")):
        return f"{channel} | {buffer.latest_value:.1f} {buffer.unit}"
    return channel


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def run_self_test(override_reservation: bool = False) -> int:
    reserved_devices = []
    try:
        for result in reserve_network_devices(override=override_reservation):
            print(f"{result.device}: {'reserved' if result.ok else 'reservation failed'} {result.message}")
            if result.ok:
                reserved_devices.append(result.device)

        snapshot = get_system_snapshot()
        supported = []
        for device in snapshot["devices"]:
            signal = infer_signal_type(str(device.get("product_type") or ""))
            if signal is not None:
                supported.extend(device.get("ai_channels", []))
        print(f"NI-DAQmx driver: {snapshot['driver_version']}")
        print(f"Supported channels: {len(supported)}")
        for channel in supported:
            print(f"  {channel}")
        return 0 if supported else 1
    finally:
        for result in unreserve_network_devices(reserved_devices):
            print(f"{result.device}: {'released' if result.ok else 'release failed'} {result.message}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NI data collector desktop application.")
    parser.add_argument("--self-test", action="store_true", help="List supported hardware without opening the UI.")
    parser.add_argument(
        "--override-reservation",
        action="store_true",
        help="Override an existing network device reservation during self-test.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return run_self_test(override_reservation=args.override_reservation)

    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    window = DataCollectorQtApp()
    window.show()
    if owns_app:
        return app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
