from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import messagebox, ttk

import matplotlib

matplotlib.use("TkAgg")
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .config import AppConfig, DEFAULT_CONFIG_PATH
from .device import Reading, SpindleDevice


BG = "#0f172a"
PANEL = "#111827"
PANEL_2 = "#1f2937"
TEXT = "#e5e7eb"
MUTED = "#94a3b8"
BLUE = "#38bdf8"
AMBER = "#f59e0b"
GREEN = "#22c55e"
RED = "#ef4444"


class SpindleGuiApp:
    def __init__(self, root: tk.Tk, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.root = root
        self.config_path = config_path
        self.config = AppConfig.load(config_path)
        self.device: SpindleDevice | None = None
        self.poll_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.read_queue: queue.Queue[tuple[float, Reading]] = queue.Queue()
        self.status_queue: queue.Queue[str] = queue.Queue()
        self.command_lock = threading.Lock()

        self.times: deque[float] = deque(maxlen=2000)
        self.speed_values: deque[float] = deque(maxlen=2000)
        self.current_values: deque[float] = deque(maxlen=2000)
        self.last_draw = 0.0

        self._build_vars()
        self._setup_root()
        self._build_ui()
        self.refresh_ports()
        self.root.after(50, self._process_readings)

    def _build_vars(self) -> None:
        self.port_var = tk.StringVar(value=self.config.serial.port)
        self.baud_var = tk.StringVar(value=str(self.config.serial.baudrate))
        self.rpm_var = tk.StringVar(value="500")
        self.connection_var = tk.StringVar(value="未连接")
        self.status_var = tk.StringVar(value="就绪")
        self.speed_var = tk.StringVar(value="0.00")
        self.current_var = tk.StringVar(value="0.00")
        self.read_quality_var = tk.StringVar(value="等待数据")

    def _setup_root(self) -> None:
        self.root.title("主轴控制")
        self.root.geometry("1120x780")
        self.root.minsize(980, 700)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Value.TLabel", background=PANEL, foreground=TEXT, font=("Microsoft YaHei UI", 28, "bold"))
        style.configure("Unit.TLabel", background=PANEL, foreground=MUTED, font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 7))
        style.map("TButton", background=[("active", PANEL_2)])
        style.configure("Accent.TButton", background=BLUE, foreground="#082f49")
        style.configure("Danger.TButton", background=RED, foreground="#ffffff")
        style.configure("TEntry", fieldbackground="#020617", foreground=TEXT, insertcolor=TEXT)
        style.configure("TCombobox", fieldbackground="#020617", foreground=TEXT)

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=24, pady=(20, 12))
        ttk.Label(header, text="主轴控制", style="Title.TLabel").pack(side="left")
        ttk.Label(header, textvariable=self.connection_var, foreground=GREEN, background=BG).pack(side="right")

        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=24, pady=8)

        conn = ttk.Frame(top, style="Panel.TFrame")
        conn.pack(side="left", fill="x", expand=True, padx=(0, 10), ipady=12)
        self._panel_title(conn, "连接")
        row = ttk.Frame(conn, style="Panel.TFrame")
        row.pack(fill="x", padx=16, pady=(8, 0))
        ttk.Label(row, text="COM", style="Muted.TLabel").pack(side="left")
        self.port_combo = ttk.Combobox(row, textvariable=self.port_var, width=14, state="readonly")
        self.port_combo.pack(side="left", padx=(8, 12))
        ttk.Button(row, text="刷新", command=self.refresh_ports).pack(side="left", padx=(0, 12))
        ttk.Label(row, text="波特率", style="Muted.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.baud_var, width=10).pack(side="left", padx=(8, 12))
        self.connect_button = ttk.Button(row, text="连接", style="Accent.TButton", command=self.toggle_connection)
        self.connect_button.pack(side="left")

        control = ttk.Frame(top, style="Panel.TFrame")
        control.pack(side="left", fill="x", expand=True, padx=(10, 0), ipady=12)
        self._panel_title(control, "转速设置")
        ctrl_row = ttk.Frame(control, style="Panel.TFrame")
        ctrl_row.pack(fill="x", padx=16, pady=(8, 0))
        ttk.Label(ctrl_row, text="目标转速 rpm", style="Muted.TLabel").pack(side="left")
        ttk.Entry(ctrl_row, textvariable=self.rpm_var, width=12).pack(side="left", padx=(8, 12))
        ttk.Button(ctrl_row, text="设置", style="Accent.TButton", command=self.set_speed).pack(side="left", padx=(0, 8))
        ttk.Button(ctrl_row, text="停止", style="Danger.TButton", command=self.stop_spindle).pack(side="left")

        metrics = ttk.Frame(self.root)
        metrics.pack(fill="x", padx=24, pady=8)
        self._metric_card(metrics, "实时转速", self.speed_var, "rpm", BLUE).pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._metric_card(metrics, "实时电流", self.current_var, "A", AMBER).pack(side="left", fill="x", expand=True, padx=(10, 0))

        chart_panel = ttk.Frame(self.root, style="Panel.TFrame")
        chart_panel.pack(fill="both", expand=True, padx=24, pady=(8, 14))
        self._panel_title(chart_panel, "实时曲线")
        self._build_chart(chart_panel)

        footer = ttk.Frame(self.root)
        footer.pack(fill="x", padx=24, pady=(0, 16))
        ttk.Label(footer, textvariable=self.status_var, foreground=MUTED, background=BG).pack(side="left")
        ttk.Label(footer, textvariable=self.read_quality_var, foreground=MUTED, background=BG).pack(side="right")

    def _panel_title(self, parent: ttk.Frame, title: str) -> None:
        ttk.Label(parent, text=title, background=PANEL, foreground=TEXT, font=("Microsoft YaHei UI", 11, "bold")).pack(
            anchor="w", padx=16, pady=(12, 0)
        )

    def _metric_card(self, parent: ttk.Frame, title: str, value_var: tk.StringVar, unit: str, color: str) -> ttk.Frame:
        card = ttk.Frame(parent, style="Panel.TFrame")
        ttk.Label(card, text=title, background=PANEL, foreground=MUTED, font=("Microsoft YaHei UI", 10)).pack(
            anchor="w", padx=18, pady=(14, 2)
        )
        ttk.Label(card, textvariable=value_var, style="Value.TLabel", foreground=color).pack(anchor="w", padx=18)
        ttk.Label(card, text=unit, style="Unit.TLabel").pack(anchor="w", padx=20, pady=(0, 14))
        return card

    def _build_chart(self, parent: ttk.Frame) -> None:
        self.figure = Figure(figsize=(8, 5.0), dpi=100, facecolor=PANEL)
        self.speed_ax = self.figure.add_subplot(211, facecolor="#020617")
        self.current_ax = self.figure.add_subplot(212, facecolor="#020617", sharex=self.speed_ax)
        self.speed_line, = self.speed_ax.plot([], [], color=BLUE, linewidth=2.0, label="转速 rpm")
        self.current_line, = self.current_ax.plot([], [], color=AMBER, linewidth=2.0, label="电流 A")

        for ax in (self.speed_ax, self.current_ax):
            ax.tick_params(colors=MUTED, labelsize=9)
            for spine in ax.spines.values():
                spine.set_color("#334155")
            ax.grid(True, color="#1e293b", linewidth=0.8)
        self.speed_ax.tick_params(labelbottom=False)
        self.speed_ax.set_ylabel("转速 rpm", color=BLUE)
        self.current_ax.set_xlabel("时间 s", color=MUTED)
        self.current_ax.set_ylabel("电流 A", color=AMBER)
        self.figure.tight_layout(pad=1.6)

        self.canvas = FigureCanvasTkAgg(self.figure, master=parent)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=16, pady=(8, 16))

    def refresh_ports(self) -> None:
        try:
            from serial.tools import list_ports

            ports = [port.device for port in list_ports.comports()]
        except Exception:
            ports = []
        if self.config.serial.port not in ports:
            ports.append(self.config.serial.port)
        self.port_combo["values"] = ports
        if self.port_var.get() not in ports and ports:
            self.port_var.set(ports[0])

    def toggle_connection(self) -> None:
        if self.device is None:
            self.connect()
        else:
            self.disconnect()

    def connect(self) -> None:
        try:
            self.config.serial.port = self.port_var.get().strip()
            self.config.serial.baudrate = int(self.baud_var.get().strip())
            self.config.save(self.config_path)
            self.device = SpindleDevice(self.config)
            self.device.connect()
        except Exception as exc:
            self.device = None
            messagebox.showerror("连接失败", str(exc))
            return

        self.stop_event.clear()
        self.poll_thread = threading.Thread(target=self._poll_loop, name="spindle-poll", daemon=True)
        self.poll_thread.start()
        self.connection_var.set(f"已连接 {self.config.serial.port}")
        self.connect_button.configure(text="断开")
        self.status_var.set("已连接，正在读取实时数据")

    def disconnect(self) -> None:
        self.stop_event.set()
        if self.poll_thread and self.poll_thread.is_alive():
            self.poll_thread.join(timeout=1.0)
        if self.device:
            self.device.close()
        self.device = None
        self.poll_thread = None
        self.connection_var.set("未连接")
        self.connect_button.configure(text="连接")
        self.status_var.set("已断开")

    def set_speed(self) -> None:
        device = self.device
        if device is None:
            messagebox.showwarning("未连接", "请先连接主轴")
            return
        try:
            rpm = float(self.rpm_var.get().strip())
        except ValueError:
            messagebox.showwarning("输入错误", "请输入有效转速")
            return
        self._run_command(lambda: device.set_speed_rpm(rpm, prepare=True), f"已设置转速 {rpm:g} rpm，运行保活已开启")

    def stop_spindle(self) -> None:
        device = self.device
        if device is None:
            return
        self._run_command(device.stop, "已发送停止命令")

    def _run_command(self, command, success_message: str) -> None:
        def worker() -> None:
            try:
                with self.command_lock:
                    command()
                self.root.after(0, lambda: self.status_var.set(success_message))
            except Exception as exc:
                self.root.after(0, lambda: messagebox.showerror("命令失败", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_loop(self) -> None:
        assert self.device is not None
        interval = max(0.05, self.config.ui.poll_interval_ms / 1000)
        keepalive_interval = max(0.05, self.config.ui.keepalive_interval_ms / 1000)
        last_keepalive = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now - last_keepalive >= keepalive_interval:
                try:
                    self.device.keepalive()
                    last_keepalive = now
                except Exception as exc:
                    last_keepalive = now
                    self.status_queue.put(f"运行保活写入失败：{exc}")
            timestamp = time.time()
            reading = self.device.read()
            self.read_queue.put((timestamp, reading))
            self.stop_event.wait(interval)

    def _process_readings(self) -> None:
        while True:
            try:
                self.status_var.set(self.status_queue.get_nowait())
            except queue.Empty:
                break

        updated = False
        while True:
            try:
                timestamp, reading = self.read_queue.get_nowait()
            except queue.Empty:
                break
            updated = True
            self.times.append(timestamp)
            self.speed_values.append(reading.speed_rpm)
            self.current_values.append(reading.current_a)
            self.speed_var.set(f"{reading.speed_rpm:.2f}")
            self.current_var.set(f"{reading.current_a:.2f}")
            quality = []
            quality.append("转速 OK" if reading.speed_ok else "转速沿用上次值")
            quality.append("电流 OK" if reading.current_ok else "电流沿用上次值")
            target = self.device.target_rpm if self.device else 0
            keepalive = "保活开启" if target > 0 else "保活关闭"
            self.read_quality_var.set(f"设定 {target} rpm / {keepalive} / " + " / ".join(quality))

        if updated and time.monotonic() - self.last_draw > 0.12:
            self._redraw_plot()
            self.last_draw = time.monotonic()
        self.root.after(50, self._process_readings)

    def _redraw_plot(self) -> None:
        if not self.times:
            return
        latest = self.times[-1]
        window = self.config.ui.plot_window_seconds
        xs = [t - latest for t in self.times if latest - t <= window]
        speed = list(self.speed_values)[-len(xs):]
        current = list(self.current_values)[-len(xs):]
        self.speed_line.set_data(xs, speed)
        self.current_line.set_data(xs, current)
        self.speed_ax.set_xlim(-window, 0)
        self.current_ax.set_xlim(-window, 0)
        self._set_ylim(self.speed_ax, speed, minimum_span=10, include_zero=True)
        self._set_ylim(self.current_ax, current, minimum_span=0.2, include_zero=True)
        self.canvas.draw_idle()

    @staticmethod
    def _set_ylim(ax, values: list[float], minimum_span: float, include_zero: bool) -> None:
        if not values:
            return
        low = min(values)
        high = max(values)
        if include_zero:
            low = min(low, 0)
            high = max(high, 0)
        span = max(high - low, minimum_span)
        center = (low + high) / 2
        ax.set_ylim(center - span * 0.6, center + span * 0.6)

    def _on_close(self) -> None:
        self.disconnect()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    SpindleGuiApp(root)
    root.mainloop()
