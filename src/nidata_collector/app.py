# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import queue
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np

from .config import (
    AccelerationSettings,
    AcquisitionGroup,
    ChannelSelection,
    RunConfiguration,
    SignalType,
    TemperatureRtdSettings,
)
from .daq_engine import AcquisitionController
from .devices import get_system_snapshot, reserve_network_devices


ROOT = Path(__file__).resolve().parents[2]
EVENT_POLL_INTERVAL_MS = 50
PLOT_REDRAW_INTERVAL_MS = 80


class DataCollectorApp(tk.Tk):
    def __init__(self, initial_refresh: bool = True) -> None:
        super().__init__()
        self.title("NI Data Collector")
        self.geometry("1280x820")
        self.minsize(1100, 720)

        self.controller = AcquisitionController()
        self.channel_rows: list[ChannelRow] = []
        self.plot_buffers: dict[SignalType, dict[str, PlotBuffer]] = {
            signal_type: {} for signal_type in SignalType
        }
        self.plot_dirty: dict[SignalType, bool] = {signal_type: False for signal_type in SignalType}
        self.plot_figures = {}
        self.plot_canvases = {}
        self._syncing_fields = False

        self._build_style()
        self._build_vars()
        self._bind_setting_sync()
        self._build_layout()
        if initial_refresh:
            self.refresh_devices()
        self.after(EVENT_POLL_INTERVAL_MS, self._poll_events)
        self.after(PLOT_REDRAW_INTERVAL_MS, self._redraw_plot)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.configure(background="#f4f6f8")
        style.configure("TFrame", background="#f4f6f8")
        style.configure("Card.TFrame", background="#ffffff", relief="flat")
        style.configure("TLabel", background="#f4f6f8", foreground="#172033", font=("Microsoft YaHei UI", 9))
        style.configure("Card.TLabel", background="#ffffff", foreground="#172033", font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background="#ffffff", foreground="#111827", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Small.TLabel", background="#ffffff", foreground="#667085", font=("Microsoft YaHei UI", 8))
        style.configure("TButton", font=("Microsoft YaHei UI", 9), padding=(10, 5))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 7))
        style.configure("Danger.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 7))
        style.configure("TCheckbutton", background="#ffffff", foreground="#172033", font=("Microsoft YaHei UI", 9))
        style.configure("TNotebook", background="#f4f6f8", borderwidth=0)
        style.configure("TNotebook.Tab", font=("Microsoft YaHei UI", 9), padding=(12, 6))

    def _build_vars(self) -> None:
        self.output_dir_var = tk.StringVar(value=str(ROOT / "data" / "runs"))
        self.note_var = tk.StringVar()
        self.plot_window_var = tk.DoubleVar(value=10.0)

        self.accel_rate_var = tk.DoubleVar(value=5120.0)
        self.accel_segment_samples_var = tk.IntVar(value=5120)
        self.accel_segment_seconds_var = tk.DoubleVar(value=1.0)
        self.accel_min_var = tk.DoubleVar(value=-50.0)
        self.accel_max_var = tk.DoubleVar(value=50.0)
        self.accel_sensitivity_var = tk.DoubleVar(value=100.0)
        self.accel_excitation_var = tk.DoubleVar(value=0.004)
        self.accel_coupling_var = tk.StringVar(value="AC")
        self.accel_settle_var = tk.DoubleVar(value=0.5)

        self.temp_rate_var = tk.DoubleVar(value=1.0)
        self.temp_segment_samples_var = tk.IntVar(value=10)
        self.temp_segment_seconds_var = tk.DoubleVar(value=10.0)
        self.temp_min_var = tk.DoubleVar(value=-50.0)
        self.temp_max_var = tk.DoubleVar(value=200.0)
        self.temp_rtd_type_var = tk.StringVar(value="PT_3851")
        self.temp_wire_var = tk.StringVar(value="FOUR_WIRE")
        self.temp_excitation_var = tk.DoubleVar(value=0.001)
        self.temp_r0_var = tk.DoubleVar(value=100.0)

        self.status_var = tk.StringVar(value="Ready")

    def _bind_setting_sync(self) -> None:
        for var in (self.accel_rate_var, self.accel_segment_samples_var):
            var.trace_add("write", lambda *_args: self._sync_segment_fields("accel", "rate_or_samples"))
        self.accel_segment_seconds_var.trace_add(
            "write", lambda *_args: self._sync_segment_fields("accel", "seconds")
        )

        for var in (self.temp_rate_var, self.temp_segment_samples_var):
            var.trace_add("write", lambda *_args: self._sync_segment_fields("temp", "rate_or_samples"))
        self.temp_segment_seconds_var.trace_add(
            "write", lambda *_args: self._sync_segment_fields("temp", "seconds")
        )

    def _sync_segment_fields(self, group: str, changed: str) -> None:
        if self._syncing_fields:
            return
        if group == "accel":
            rate_var = self.accel_rate_var
            samples_var = self.accel_segment_samples_var
            seconds_var = self.accel_segment_seconds_var
        else:
            rate_var = self.temp_rate_var
            samples_var = self.temp_segment_samples_var
            seconds_var = self.temp_segment_seconds_var

        try:
            rate = float(rate_var.get())
            if rate <= 0:
                return
            self._syncing_fields = True
            if changed == "seconds":
                seconds = float(seconds_var.get())
                if seconds > 0:
                    samples_var.set(max(1, int(round(rate * seconds))))
            else:
                samples = int(samples_var.get())
                if samples > 0:
                    seconds_var.set(round(samples / rate, 6))
        except (tk.TclError, ValueError):
            return
        finally:
            self._syncing_fields = False

    def _build_layout(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=0, minsize=460)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left = ttk.Frame(main)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(1, weight=1)

        right = ttk.Frame(main)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self._build_channel_panel(left)
        self._build_settings_panel(left)
        self._build_plot_panel(right)
        self._build_log_panel(right)

    def _build_channel_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Card.TFrame", padding=12)
        panel.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        panel.columnconfigure(0, weight=1)

        header = ttk.Frame(panel, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Devices and channels", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Refresh", command=self.refresh_devices).grid(row=0, column=1, padx=(8, 0))

        table_header = ttk.Frame(panel, style="Card.TFrame")
        table_header.grid(row=1, column=0, sticky="ew", pady=(12, 4))
        widths = (18, 24, 12, 8, 8)
        for col, (text, width) in enumerate(
            zip(("Module", "Channel", "Signal", "Plot", "Save"), widths, strict=True)
        ):
            label = ttk.Label(table_header, text=text, style="Small.TLabel", width=width)
            label.grid(row=0, column=col, sticky="w", padx=(0, 4))

        self.channel_canvas = tk.Canvas(panel, height=250, background="#ffffff", highlightthickness=0)
        self.channel_scroll = ttk.Scrollbar(panel, orient=tk.VERTICAL, command=self.channel_canvas.yview)
        self.channel_frame = ttk.Frame(self.channel_canvas, style="Card.TFrame")
        self.channel_frame.bind(
            "<Configure>",
            lambda _event: self.channel_canvas.configure(scrollregion=self.channel_canvas.bbox("all")),
        )
        self.channel_canvas.create_window((0, 0), window=self.channel_frame, anchor="nw")
        self.channel_canvas.configure(yscrollcommand=self.channel_scroll.set)
        self.channel_canvas.grid(row=2, column=0, sticky="nsew")
        self.channel_scroll.grid(row=2, column=1, sticky="ns")

    def _build_settings_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Card.TFrame", padding=12)
        panel.grid(row=1, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        output = ttk.Frame(panel, style="Card.TFrame")
        output.grid(row=0, column=0, sticky="ew")
        output.columnconfigure(1, weight=1)
        ttk.Label(output, text="Output", style="Title.TLabel").grid(row=0, column=0, sticky="w", columnspan=3)
        ttk.Label(output, text="Folder", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(output, textvariable=self.output_dir_var).grid(row=1, column=1, sticky="ew", padx=6, pady=(10, 0))
        ttk.Button(output, text="Browse", command=self._browse_output).grid(row=1, column=2, pady=(10, 0))
        ttk.Label(output, text="Note", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(output, textvariable=self.note_var).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=(8, 0))

        notebook = ttk.Notebook(panel)
        notebook.grid(row=1, column=0, sticky="nsew", pady=(12, 8))
        panel.rowconfigure(1, weight=1)
        accel = ttk.Frame(notebook, style="Card.TFrame", padding=10)
        temp = ttk.Frame(notebook, style="Card.TFrame", padding=10)
        notebook.add(accel, text="Vibration")
        notebook.add(temp, text="RTD Temperature")
        self._build_accel_settings(accel)
        self._build_temp_settings(temp)

        controls = ttk.Frame(panel, style="Card.TFrame")
        controls.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        controls.columnconfigure(2, weight=1)
        self.start_button = ttk.Button(controls, text="Start", style="Accent.TButton", command=self.start_acquisition)
        self.start_button.grid(row=0, column=0, sticky="w")
        self.stop_button = ttk.Button(controls, text="Stop", style="Danger.TButton", command=self.stop_acquisition, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(controls, textvariable=self.status_var, style="Card.TLabel").grid(row=0, column=2, sticky="e")

    def _build_accel_settings(self, parent: ttk.Frame) -> None:
        fields = (
            ("Sample rate Hz", self.accel_rate_var),
            ("Segment samples", self.accel_segment_samples_var),
            ("Segment seconds", self.accel_segment_seconds_var),
            ("Min g", self.accel_min_var),
            ("Max g", self.accel_max_var),
            ("Sensitivity mV/g", self.accel_sensitivity_var),
            ("IEPE A", self.accel_excitation_var),
            ("Settle seconds", self.accel_settle_var),
        )
        for index, (label, var) in enumerate(fields):
            row = index // 2
            col = (index % 2) * 2
            ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=col, sticky="w", pady=4)
            ttk.Entry(parent, textvariable=var, width=12).grid(row=row, column=col + 1, sticky="ew", padx=(6, 14), pady=4)
        ttk.Label(parent, text="Coupling", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Combobox(
            parent,
            textvariable=self.accel_coupling_var,
            values=("AC", "DC", "GND", "NONE"),
            width=10,
            state="readonly",
        ).grid(row=4, column=1, sticky="w", padx=(6, 14), pady=4)
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(3, weight=1)

    def _build_temp_settings(self, parent: ttk.Frame) -> None:
        fields = (
            ("Sample rate Hz", self.temp_rate_var),
            ("Segment samples", self.temp_segment_samples_var),
            ("Segment seconds", self.temp_segment_seconds_var),
            ("Min degC", self.temp_min_var),
            ("Max degC", self.temp_max_var),
            ("Excitation A", self.temp_excitation_var),
            ("R0 ohms", self.temp_r0_var),
        )
        for index, (label, var) in enumerate(fields):
            row = index // 2
            col = (index % 2) * 2
            ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=col, sticky="w", pady=4)
            ttk.Entry(parent, textvariable=var, width=12).grid(row=row, column=col + 1, sticky="ew", padx=(6, 14), pady=4)
        ttk.Label(parent, text="RTD type", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Combobox(
            parent,
            textvariable=self.temp_rtd_type_var,
            values=("PT_3851", "PT_3750", "PT_3911", "PT_3916", "PT_3920", "PT_3928"),
            width=12,
            state="readonly",
        ).grid(row=4, column=1, sticky="w", padx=(6, 14), pady=4)
        ttk.Label(parent, text="Wiring", style="Card.TLabel").grid(row=4, column=2, sticky="w", pady=4)
        ttk.Combobox(
            parent,
            textvariable=self.temp_wire_var,
            values=("FOUR_WIRE", "THREE_WIRE", "TWO_WIRE"),
            width=12,
            state="readonly",
        ).grid(row=4, column=3, sticky="w", padx=(6, 14), pady=4)
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(3, weight=1)

    def _build_plot_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Card.TFrame", padding=10)
        panel.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(panel, style="Card.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="Live plot", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(toolbar, text="Window s", style="Card.TLabel").grid(row=0, column=2, sticky="e", padx=(0, 6))
        ttk.Entry(toolbar, textvariable=self.plot_window_var, width=8).grid(row=0, column=3, sticky="e")

        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        notebook = ttk.Notebook(panel)
        notebook.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        for signal_type in SignalType:
            tab = ttk.Frame(notebook, style="Card.TFrame")
            tab.rowconfigure(0, weight=1)
            tab.columnconfigure(0, weight=1)
            notebook.add(tab, text=signal_type.label)
            figure = Figure(figsize=(7, 5), dpi=100)
            canvas = FigureCanvasTkAgg(figure, master=tab)
            canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
            self.plot_figures[signal_type] = figure
            self.plot_canvases[signal_type] = canvas
            self._draw_empty_plot(signal_type)

    def _build_log_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Card.TFrame", padding=10)
        panel.grid(row=1, column=0, sticky="ew")
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text="Log", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.log_text = tk.Text(
            panel,
            height=7,
            wrap=tk.WORD,
            borderwidth=0,
            background="#0f172a",
            foreground="#e5e7eb",
            insertbackground="#e5e7eb",
            font=("Consolas", 9),
        )
        self.log_text.grid(row=1, column=0, sticky="ew", pady=(8, 0))

    def refresh_devices(self) -> None:
        for child in self.channel_frame.winfo_children():
            child.destroy()
        self.channel_rows.clear()

        try:
            for result in reserve_network_devices(override=False):
                self._log(f"{result.device}: {'reserved' if result.ok else 'reservation failed'} {result.message}")
            snapshot = get_system_snapshot()
        except Exception as exc:
            messagebox.showerror("DAQmx", f"Device refresh failed:\n{exc}")
            return

        row_index = 0
        for device in snapshot["devices"]:
            product = str(device.get("product_type") or "")
            signal_type = infer_signal_type(product)
            if signal_type is None:
                continue
            for channel in device.get("ai_channels", []):
                row = ChannelRow(
                    parent=self.channel_frame,
                    row=row_index,
                    module=device["name"],
                    product_type=product,
                    channel=channel,
                    signal_type=signal_type,
                )
                self.channel_rows.append(row)
                row_index += 1

        if not self.channel_rows:
            ttk.Label(
                self.channel_frame,
                text="No supported NI 9234 or NI 9216 AI channels detected.",
                style="Card.TLabel",
            ).grid(row=0, column=0, sticky="w", pady=8)
        self._log("Device refresh completed.")

    def start_acquisition(self) -> None:
        try:
            config = self._build_run_configuration()
            run_dir = self.controller.start(config)
        except Exception as exc:
            messagebox.showerror("Start failed", str(exc))
            return

        self.plot_buffers = {signal_type: {} for signal_type in SignalType}
        self.plot_dirty = {signal_type: True for signal_type in SignalType}
        self.status_var.set("Running")
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self._log(f"Run started: {run_dir}")

    def stop_acquisition(self) -> None:
        self.controller.stop()
        self.status_var.set("Stopping")
        self.stop_button.configure(state=tk.DISABLED)
        self._log("Stop requested.")

    def _build_run_configuration(self) -> RunConfiguration:
        selections = [row.selection() for row in self.channel_rows if row.visualize_var.get() or row.save_var.get()]
        if not selections:
            raise ValueError("Select at least one channel for Plot or Save.")

        groups: list[AcquisitionGroup] = []
        for signal_type in SignalType:
            channels = [selection for selection in selections if selection.signal_type == signal_type]
            if not channels:
                continue
            if signal_type == SignalType.ACCELERATION:
                settings = self._acceleration_settings()
            else:
                settings = self._temperature_settings()
            groups.append(AcquisitionGroup(signal_type=signal_type, channels=channels, settings=settings))

        return RunConfiguration(
            output_dir=Path(self.output_dir_var.get()).expanduser(),
            groups=groups,
            operator_note=self.note_var.get().strip(),
        )

    def _acceleration_settings(self) -> AccelerationSettings:
        rate = positive_float(self.accel_rate_var.get(), "Vibration sample rate")
        segment_seconds = positive_float(self.accel_segment_seconds_var.get(), "Vibration segment seconds")
        segment_samples = segment_samples_from(rate, segment_seconds, int(self.accel_segment_samples_var.get()))
        min_g = float(self.accel_min_var.get())
        max_g = float(self.accel_max_var.get())
        if min_g >= max_g:
            raise ValueError("Vibration Min g must be smaller than Max g.")
        return AccelerationSettings(
            sample_rate_hz=rate,
            segment_samples=segment_samples,
            segment_seconds=segment_samples / rate,
            min_value=min_g,
            max_value=max_g,
            sensitivity_mv_per_g=positive_float(self.accel_sensitivity_var.get(), "Sensitivity"),
            excitation_current_a=positive_float(self.accel_excitation_var.get(), "IEPE current"),
            coupling=self.accel_coupling_var.get(),
            settle_seconds=max(0.0, float(self.accel_settle_var.get())),
        )

    def _temperature_settings(self) -> TemperatureRtdSettings:
        rate = positive_float(self.temp_rate_var.get(), "Temperature sample rate")
        segment_seconds = positive_float(self.temp_segment_seconds_var.get(), "Temperature segment seconds")
        segment_samples = segment_samples_from(rate, segment_seconds, int(self.temp_segment_samples_var.get()))
        min_temp = float(self.temp_min_var.get())
        max_temp = float(self.temp_max_var.get())
        if min_temp >= max_temp:
            raise ValueError("Temperature Min degC must be smaller than Max degC.")
        return TemperatureRtdSettings(
            sample_rate_hz=rate,
            segment_samples=segment_samples,
            segment_seconds=segment_samples / rate,
            min_value=min_temp,
            max_value=max_temp,
            rtd_type=self.temp_rtd_type_var.get(),
            resistance_config=self.temp_wire_var.get(),
            excitation_current_a=positive_float(self.temp_excitation_var.get(), "RTD excitation"),
            r0_ohms=positive_float(self.temp_r0_var.get(), "R0"),
        )

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_dir_var.get() or str(ROOT))
        if selected:
            self.output_dir_var.set(selected)

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.controller.events.get_nowait()
                if event.kind == "data" and event.payload:
                    self._handle_data_event(event.group, event.payload)
                elif event.kind == "error":
                    self._log(f"ERROR [{event.group}] {event.message}")
                    if event.payload and event.payload.get("traceback"):
                        self._log(event.payload["traceback"])
                    messagebox.showerror("Acquisition error", event.message)
                elif event.kind in {"status", "saved", "started", "stopped"}:
                    self._log(event.message)
                else:
                    self._log(event.message or event.kind)
        except queue.Empty:
            pass

        if self.controller.running and self.controller.poll_finished():
            self.status_var.set("Ready")
            self.start_button.configure(state=tk.NORMAL)
            self.stop_button.configure(state=tk.DISABLED)
        self.after(EVENT_POLL_INTERVAL_MS, self._poll_events)

    def _handle_data_event(self, group: str, payload: dict) -> None:
        signal_type = SignalType(group)
        channels = payload["channels"]
        time_s = np.asarray(payload["time_s"], dtype=float)
        data = np.asarray(payload["data"], dtype=float)
        unit = payload["unit"]
        window = max(0.5, float(self.plot_window_var.get()))

        for index, channel in enumerate(channels):
            buffer = self.plot_buffers[signal_type].get(channel)
            if buffer is None:
                buffer = PlotBuffer(unit=unit)
            buffer.append(time_s, data[index], window)
            self.plot_buffers[signal_type][channel] = buffer
        self.plot_dirty[signal_type] = True

    def _redraw_plot(self) -> None:
        for signal_type, is_dirty in list(self.plot_dirty.items()):
            if not is_dirty:
                continue
            figure = self.plot_figures[signal_type]
            canvas = self.plot_canvases[signal_type]
            figure.clear()
            buffers = {
                channel: buffer
                for channel, buffer in self.plot_buffers[signal_type].items()
                if len(buffer.time_s)
            }
            if not buffers:
                self._draw_empty_plot(signal_type)
            else:
                axes = figure.subplots(len(buffers), 1, sharex=True)
                if len(buffers) == 1:
                    axes = [axes]
                for axis, (channel, buffer) in zip(axes, buffers.items(), strict=True):
                    time_s, values = buffer.downsampled(max_points=3000)
                    axis.plot(time_s, values, linewidth=0.85)
                    axis.set_title(channel, fontsize=9)
                    axis.set_ylabel(buffer.unit)
                    axis.grid(True, linewidth=0.4, alpha=0.35)
                axes[-1].set_xlabel("Task time (s)")
                figure.tight_layout()
                canvas.draw_idle()
            self.plot_dirty[signal_type] = False
        self.after(PLOT_REDRAW_INTERVAL_MS, self._redraw_plot)

    def _draw_empty_plot(self, signal_type: SignalType) -> None:
        figure = self.plot_figures[signal_type]
        canvas = self.plot_canvases[signal_type]
        figure.clear()
        axis = figure.add_subplot(111)
        axis.text(0.5, 0.5, "No live data", ha="center", va="center", fontsize=13, color="#667085")
        axis.set_xticks([])
        axis.set_yticks([])
        figure.tight_layout()
        canvas.draw_idle()

    def _log(self, message: str) -> None:
        self.log_text.insert(tk.END, message.rstrip() + "\n")
        self.log_text.see(tk.END)


class ChannelRow:
    def __init__(
        self,
        parent: ttk.Frame,
        row: int,
        module: str,
        product_type: str,
        channel: str,
        signal_type: SignalType,
    ) -> None:
        self.module = module
        self.product_type = product_type
        self.channel = channel
        self.signal_type = signal_type
        self.visualize_var = tk.BooleanVar(value=False)
        self.save_var = tk.BooleanVar(value=False)

        ttk.Label(parent, text=module, style="Card.TLabel", width=18).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Label(parent, text=channel, style="Card.TLabel", width=24).grid(row=row, column=1, sticky="w", pady=2)
        ttk.Label(parent, text=signal_type.label, style="Card.TLabel", width=12).grid(row=row, column=2, sticky="w", pady=2)
        ttk.Checkbutton(parent, variable=self.visualize_var).grid(row=row, column=3, sticky="w", pady=2)
        ttk.Checkbutton(parent, variable=self.save_var).grid(row=row, column=4, sticky="w", pady=2)

    def selection(self) -> ChannelSelection:
        return ChannelSelection(
            physical_name=self.channel,
            device_name=self.module,
            product_type=self.product_type,
            signal_type=self.signal_type,
            visualize=self.visualize_var.get(),
            save=self.save_var.get(),
        )


class PlotBuffer:
    def __init__(self, unit: str) -> None:
        self.unit = unit
        self.time_s = np.array([], dtype=float)
        self.values = np.array([], dtype=float)

    def append(self, time_s: np.ndarray, values: np.ndarray, window_s: float) -> None:
        if not len(time_s):
            return
        self.time_s = np.concatenate([self.time_s, time_s])
        self.values = np.concatenate([self.values, values])
        cutoff = self.time_s[-1] - window_s
        keep = self.time_s >= cutoff
        self.time_s = self.time_s[keep]
        self.values = self.values[keep]

    def downsampled(self, max_points: int) -> tuple[np.ndarray, np.ndarray]:
        if len(self.time_s) <= max_points:
            return self.time_s, self.values
        step = int(np.ceil(len(self.time_s) / max_points))
        return self.time_s[::step], self.values[::step]


def infer_signal_type(product_type: str) -> SignalType | None:
    product = product_type.lower()
    if "9234" in product:
        return SignalType.ACCELERATION
    if "9216" in product:
        return SignalType.TEMPERATURE_RTD
    return None


def positive_float(value: float, name: str) -> float:
    result = float(value)
    if result <= 0:
        raise ValueError(f"{name} must be positive.")
    return result


def segment_samples_from(rate: float, seconds: float, explicit_samples: int) -> int:
    if explicit_samples > 0:
        return explicit_samples
    return max(1, int(round(rate * seconds)))


def run_self_test() -> int:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NI data collector desktop application.")
    parser.add_argument("--self-test", action="store_true", help="List supported hardware without opening the UI.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return run_self_test()
    app = DataCollectorApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
