* Put the conclusion first after experiments or debugging.
* Do not generate final HTML reports with Python scripts; summarize them manually.
* Reuse existing code where possible and prefer the smallest practical change.
* Clean temporary code, intermediate files, and `__pycache__`.

## Current Project Notes

* The architecture was simplified on 2026-07-07 and now uses three layers:
  * `src/nidata_collector/hardware/`: hardware communication and control boundaries.
  * `src/nidata_collector/core/`: acquisition control, worker scheduling, and data storage.
  * `src/nidata_collector/ui/`: Qt UI, plotting, and user interaction.
* The three devices are managed separately:
  * NI cDAQ: `hardware/ni.py`.
  * DAMX-8013 NTC temperature card: `hardware/damx8013.py`.
  * Spindle controller: `hardware/spindle.py`.
* Old root-level compatibility entry modules were intentionally removed. Current entries are:
  * UI: `scripts/run_monitor.py` -> `nidata_collector.ui.qt_app`.
  * NI probe: `scripts/ni_probe.py` -> `nidata_collector.hardware.ni`.
* NI, DAMX-8013, and spindle must remain independently usable. One offline device must not block the others.
* DAMX-8013 is fixed as a two-channel NTC temperature card. COM, R value, and B value come from `config/temperature_card.json`.
* The UI `Temperature` settings tab shares sample rate, segment length, and temperature range between RTD and NTC. RTD-only fields must be explicitly labeled `RTD`.
* Spindle serial settings, protocol addresses, polling, and safety limits come from `config/spindle_control.json`.
* Startup UI defaults are centralized in `config/app_startup.json`. Keep hardware protocol details in the device-specific config files.
* Per-channel Meta dialog defaults are stored in `config/app_startup.json` under `channel_metadata`, keyed by physical channel name.
* Raw acquisition segments are stored as structured `.npz.xz` files using `npz` payloads compressed with `lzma/xz`.
  * Each raw segment must include `time_s`, `data`, `channels`, `sample_start_index`, `sample_rate_hz`, `signal_type`, and `unit`.
  * `time_s[i]` corresponds to `data[:, i]`; do not rely on reconstructing time only from sample index.
  * Do not use `pickle` for experiment data storage.
* Do not generate per-segment JSON sidecars for new runs. Use one run-level `manifest.json`, `segment_records.csv`, and `segment_summary.csv`.
* Default segment duration is about 10 seconds: vibration `256000` samples at `25600 Hz`, temperature `100` samples at `10 Hz`. The UI may still override these values.
* `segment_summary.csv` uses fixed 1 second summary windows, even when raw `.npz.xz` files are saved as 10 second segments.
* Postprocessing after recording should generate `segment_summary.csv` and `trends/summary_overview.png`.
  * Vibration summary features: `mean_abs`, `max`, `min`.
  * Temperature summary features: `mean`, `max`, `min`.
  * Spindle speed/current summary features: `mean`, `max`, `min`.
  * Trend overview must be four stacked subplots: vibration channels together, temperature channels together, spindle speed alone, spindle current alone.
  * Do not plot spindle speed and current on the same subplot or twin Y axis.
* Use `scripts/inspect_npz_xz.py` as the independent temporary tool for reading and previewing `.npz.xz` raw segments.
* Channel metadata defaults are loaded on Refresh and saved back to `config/app_startup.json` on GUI close; blank metadata entries should not be persisted.
* 2026-07-07 spindle experiment conclusions after compressed-storage integration:
  * Main analyzed run: `data/runs/run_20260707_185820`.
  * This run supports the standard data format decision and the 6000 rpm standard acquisition flow.
  * The run stepped through 500, 1000, 2000, 3000, 4000, 5000, 6000, 7000, and 8000 rpm, then returned to 6000 rpm before shutdown.
  * For formal 6000 rpm standard acquisition, do not overshoot to 8000 rpm; ramp and stabilize directly at 6000 rpm.
  * Final switch to 6000 rpm occurred at about 121.015 s; actual speed reached 6000 rpm near 121.5 s.
  * 6000 rpm hold lasted about 582 s, or 9.7 min, before deceleration.
  * NTC is the primary temperature and safety/thermal-stability channel. In this run, NTC rose from about 23.98 degC at 6000 rpm stabilization to about 26.55 degC before deceleration.
  * 6000 rpm thermal stabilization estimate from NTC: at least 8 min after speed stabilizes; use 8.5-9 min for a conservative wait.
  * RTD changed little in this run and should be recorded but not used as the primary thermal-stability gate until its placement/sensitivity is improved.
  * Spindle current data has negative values and spikes; record it for now, but do not use it for load, thermal stability, or data-validity decisions until register meaning/scaling/sign are verified.
* Notion tracking for the 2026-07-07 experiment:
  * `01.04 acquisition-chain validation`: supplemented with the standard data acquisition format.
  * `03.02 stepped speed-up vibration/temperature acquisition`: supplemented with run `run_20260707_185820` and marked completed.
  * `03.04 thermal-stability analysis`: supplemented with the 6000 rpm thermal-stability analysis and marked in progress.
  * `03.05 pre-acquisition wait-time recommendation`: supplemented with the 6000 rpm standard acquisition flow V1 and marked completed.
* The full pre-refactor baseline is preserved at tag `baseline-before-architecture-refactor-20260707`.
* Current function and API docs are:
  * `docs/architecture.md`
  * `docs/api.md`
* Historical HTML reports, reference projects, one-off test data, and old test scripts were cleaned. Do not reintroduce unused docs or temporary test artifacts.

## Common Validation

* Compile check: `E:\software\conda\envs\NI\python.exe -B -m py_compile scripts\run_monitor.py scripts\ni_probe.py scripts\inspect_npz_xz.py src\nidata_collector\core\engine.py src\nidata_collector\core\storage.py src\nidata_collector\ui\startup_config.py src\nidata_collector\ui\qt_app.py`
* Use `QT_QPA_PLATFORM=offscreen` for offscreen UI checks.
* Clean generated `__pycache__` directories after validation.
