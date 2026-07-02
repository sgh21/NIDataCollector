from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class OutputPaths:
    csv_path: Path
    png_path: Path
    json_path: Path


def build_output_paths(output_dir: Path, prefix: str) -> OutputPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = output_dir / f"{prefix}_{stamp}"
    return OutputPaths(
        csv_path=base.with_suffix(".csv"),
        png_path=base.with_suffix(".png"),
        json_path=base.with_suffix(".json"),
    )


def save_csv(path: Path, time_s: np.ndarray, data: np.ndarray, channels: list[str]) -> None:
    headers = ["time_s", *[f"{channel}_g" for channel in channels]]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for sample_index, sample_time in enumerate(time_s):
            writer.writerow([sample_time, *data[:, sample_index]])


def save_metadata(path: Path, metadata: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)


def save_waveform_plot(
    path: Path,
    time_s: np.ndarray,
    data: np.ndarray,
    channels: list[str],
    show: bool = False,
) -> None:
    from PySide6 import QtWidgets
    import pyqtgraph as pg
    import pyqtgraph.exporters

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pg.setConfigOptions(antialias=False)

    rows = len(channels)
    widget = pg.GraphicsLayoutWidget()
    widget.resize(1100, max(360, rows * 220))
    widget.setWindowTitle("Vibration Acceleration")

    previous_plot = None
    for index, channel in enumerate(channels):
        plot = widget.addPlot(row=index, col=0, title=channel)
        plot.showGrid(x=True, y=True, alpha=0.25)
        plot.setLabel("left", "g")
        plot.plot(time_s, data[index], pen=pg.mkPen("#1f77b4", width=1))
        if previous_plot is not None:
            plot.setXLink(previous_plot)
        previous_plot = plot
    if previous_plot is not None:
        previous_plot.setLabel("bottom", "Time", units="s")

    app.processEvents()
    exporter = pyqtgraph.exporters.ImageExporter(widget.scene())
    exporter.parameters()["width"] = 1650
    exporter.export(str(path))

    if show:
        widget.show()
        app.exec()
    else:
        widget.close()
