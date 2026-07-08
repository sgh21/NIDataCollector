# 数据迁移与分析背景说明

状态日期：2026-07-08

## 结论先行

当前项目的标准数据格式是“一个 run 目录 + run 级元数据 + 压缩原始段 + 1Hz 汇总表”。原始采集段统一保存为 `.npz.xz`，内部是 NumPy `npz` payload，再用 `lzma/xz` 压缩；每个原始段必须包含 `time_s`、`data`、`channels`、`sample_start_index`、`sample_rate_hz`、`signal_type`、`unit`。分析和迁移时必须以文件内的 `time_s` 为准，不能只靠 `sample_start_index` 和采样率重建时间轴。

新数据不要再生成每段 JSON sidecar，也不要用 `pickle` 保存实验数据。迁移旧数据时，目标应是补齐 run 级 `manifest.json`、`segment_records.csv`、`segment_summary.csv`，并把原始波形/温度段转换成标准 `.npz.xz`。

## 项目与采集链路背景

项目现在分为三层：

- `src/nidata_collector/hardware/`：硬件通信边界。
- `src/nidata_collector/core/`：采集调度、记录触发、数据存储和后处理。
- `src/nidata_collector/ui/`：Qt UI、实时绘图、用户交互和标准流程控制。

设备按独立边界管理：

- NI cDAQ：`hardware/ni.py`，用于 NI 9234 振动和 NI 9216 RTD 温度采集。
- DAMX-8013：`hardware/damx8013.py`，固定为两通道 NTC 温度卡。
- 主轴控制器：`hardware/spindle.py`，用于转速设置、反馈读取、电流遥测和安全限制。

一个设备离线不应阻塞其它设备。NI、DAMX-8013、主轴控制彼此独立；混合采集时，如果某一类采集组不可用，系统会尽量保留其它可用采集组继续运行。

## 当前配置来源

启动 UI 默认值集中在 `config/app_startup.json`：

- 输出目录：默认 `data/runs`。
- 振动默认值：`25600 Hz`，`256000 samples`，约 10 秒一段。
- 温度默认值：`10 Hz`，`100 samples`，约 10 秒一段。
- 温度设置页中的采样率、段长度、温度范围由 RTD 和 NTC 共用。
- NTC 设备配置路径：`config/temperature_card.json`。
- 主轴配置路径：`config/spindle_control.json`。
- 通道元数据默认值：`channel_metadata`，按物理通道名保存。

DAMX-8013 的 COM 口、Modbus 参数、NTC R/B 值来自 `config/temperature_card.json`。主轴串口、协议地址、轮询和安全限制来自 `config/spindle_control.json`。硬件协议细节应保留在对应设备配置文件中，不要迁移到 run 数据或 UI 启动配置之外。

## 标准 run 目录结构

记录触发后，数据写入：

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

采集组目录名由 `signal_type`、`sample_rate_hz`、`segment_samples` 组成，实际目录名会经过安全字符清理。

## run 级文件

### `manifest.json`

`manifest.json` 是 run 的主索引，至少包含：

- `run_id`
- `created_at_local`
- `time_axis` 说明
- `storage_format`，当前为 `npz_xz_float64_with_time`
- 原始段文件格式说明
- `output_dir`
- 序列化后的 `RunConfiguration`
- NI 设备快照
- 串口温度卡快照
- 可选主轴遥测说明
- 后处理完成后追加的 `postprocess`

迁移数据时，`manifest.json` 应明确目标格式和数据来源。如果旧数据没有完整设备快照，建议保留可恢复字段，并在额外说明字段中标注缺失原因。

### `experiment_record.csv`

一行 run 级实验记录，包含主轴、实验日期、热状态、温度记录、异常标记、标签等信息。该文件适合做 run 级筛选，例如按 `label`、`spindle_id`、`target_speed_rpm` 或异常标记过滤实验。

### `spindle_info.csv`

一行主轴基础信息，包含主轴 ID、型号、额定转速、最高转速、测试日期和累计运行时间。分析时可用它和多个 run 关联，形成设备维度的长期趋势。

### `sensor_info.csv`

每个通道一行，包含：

- `signal_type`
- `channel`
- `device_name`
- `product_type`
- `sensor_id`
- `measurement_position`
- `direction`
- `mounting_method`
- `sample_rate_hz`
- `unit`
- `plot`
- `save`

后处理生成 `segment_summary.csv` 时，会优先使用 `sensor_id` 作为汇总列名的一部分；如果 `sensor_id` 为空，则回退到物理通道名。迁移时应尽量补齐 `sensor_id`，否则跨 run 合并时列名会更难稳定。

### `segment_records.csv`

每个保存的原始段一行。核心字段包括：

- run 级实验字段。
- `segment_index`
- `signal_type`
- `channels`
- `unit`
- `sample_rate_hz`
- `sample_count`
- `sample_duration_s`
- `sample_start_index`
- `sample_end_index`
- `time_start_s`
- `time_center_s`
- `time_end_s`
- `partial`
- `data_format`
- `data_file`
- `data_path`

`segment_records.csv` 是原始段和 run 元数据之间的桥。迁移时不要只转换 `.npz.xz`，也要同步生成或修正这里的索引记录。

### `segment_summary.csv`

后处理输出的宽表，每行是固定 1 秒窗口。即使原始 `.npz.xz` 是 10 秒一段，汇总也按 `time_s` 拆成 1Hz 窗口。

基础列：

- `time_start_s`
- `time_center_s`
- `time_end_s`

信号列命名模式：

- `acceleration__<sensor_or_channel>__mean_abs`
- `acceleration__<sensor_or_channel>__max`
- `acceleration__<sensor_or_channel>__min`
- `temperature_ntc__<sensor_or_channel>__mean`
- `temperature_ntc__<sensor_or_channel>__max`
- `temperature_ntc__<sensor_or_channel>__min`
- `temperature_rtd__<sensor_or_channel>__mean`
- `temperature_rtd__<sensor_or_channel>__max`
- `temperature_rtd__<sensor_or_channel>__min`
- `spindle_speed__mean`
- `spindle_speed__max`
- `spindle_speed__min`
- `spindle_current__mean`
- `spindle_current__max`
- `spindle_current__min`

趋势分析优先使用 `segment_summary.csv`，频域分析、冲击分析和更细粒度的时域分析再回读原始 `.npz.xz`。

### `trends/summary_overview.png`

停止记录后的后处理会生成 `trends/summary_overview.png`，包含四个堆叠子图：

- 振动通道放在同一子图，使用 `mean_abs`。
- 温度通道放在同一子图，使用 `mean`，NTC 在 RTD 前。
- 主轴转速单独一个子图。
- 主轴电流单独一个子图。

不要把主轴转速和电流画在同一子图或 twin Y 轴上。

### `spindle_telemetry.csv` 和 `spindle_telemetry.json`

主轴连接时，会额外记录主轴遥测：

```text
sample_index,time_s,target_rpm,actual_speed_rpm,current_a,speed_ok,current_ok,keepalive_enabled
```

`spindle_telemetry.json` 保存遥测来源、主轴配置和 CSV 文件名。主轴速度和电流的 1Hz 汇总会并入 `segment_summary.csv`。

## 原始段 `.npz.xz` 格式

`.npz.xz` 文件是标准 NumPy `npz` payload 的 xz 压缩版本。读取时应禁用 pickle。

必需数组：

| 数组 | dtype | shape | 含义 |
| --- | --- | --- | --- |
| `time_s` | `float64` | `(sample_count,)` | 每个样本的时间，单位秒 |
| `data` | `float64` | `(channel_count, sample_count)` | 多通道数据，通道为第 0 轴，样本为第 1 轴 |
| `channels` | string | `(channel_count,)` | 与 `data` 第 0 轴对齐的通道名 |
| `sample_start_index` | `int64` | scalar array | 该段第一点在记录内的样本序号 |
| `sample_rate_hz` | `float64` | scalar array | 该段采样率 |
| `signal_type` | string | scalar array | `acceleration`、`temperature_ntc` 或 `temperature_rtd` |
| `unit` | string | scalar array | `g` 或 `degC` |

关键对齐规则：

- `time_s[i]` 对应 `data[:, i]`。
- `len(time_s)` 必须等于 `data.shape[1]`。
- `len(channels)` 必须等于 `data.shape[0]`。
- 不要依赖文件名重建时间，文件内 `time_s` 是分析事实来源。
- 标量字段在 npz 中也保存为单元素数组，迁移工具应保持这一点。

读取示例：

```python
from pathlib import Path
from nidata_collector.core.storage import read_segment_npz_xz

payload = read_segment_npz_xz(Path("segment.npz.xz"))
time_s = payload["time_s"]
data = payload["data"]
channels = payload["channels"]
```

独立检查工具：

```powershell
E:\software\conda\envs\NI\python.exe scripts\inspect_npz_xz.py data\runs\run_xxx\...\segment.npz.xz --head 8
E:\software\conda\envs\NI\python.exe scripts\inspect_npz_xz.py data\runs\run_xxx\...\segment.npz.xz --plot preview.png
```

## 信号类型与默认采集参数

| `signal_type` | 数据源 | 默认采样率 | 默认段长度 | 单位 | 典型用途 |
| --- | --- | ---: | ---: | --- | --- |
| `acceleration` | NI 9234 | `25600 Hz` | `256000 samples` | `g` | 振动趋势、频域、故障特征 |
| `temperature_ntc` | DAMX-8013 | `10 Hz` | `100 samples` | `degC` | 主温度与热稳定判断 |
| `temperature_rtd` | NI 9216 | `10 Hz` | `100 samples` | `degC` | 辅助温度记录 |

NTC 是当前热稳定和安全监控的主温度通道。RTD 应继续记录，但在安装位置和灵敏度改善前，不作为主要热稳定门限。

## 时间轴和跨设备对齐

原始段中的 `time_s` 是记录内相对时间：

- NI 采集组使用 DAQmx 硬件定时样本。
- DAMX-8013 NTC 采集组使用串口轮询计数和配置采样率生成时间轴。
- 主轴遥测使用主轴记录器启动后的单调时钟差值。

记录触发后，各保存流从自己的记录起点开始写 `time_s`。通常趋势分析可以直接按 `time_s` 的 1 秒窗口合并；如果需要亚秒级跨设备同步或严格相位分析，应检查具体 run 的触发顺序、设备延迟和采样时钟来源。当前格式没有为每个样本保存绝对墙钟时间。

迁移旧数据时，如果原始时间戳存在，应优先写入真实 `time_s`。如果旧数据只有样本序号和采样率，才使用 `sample_index / sample_rate_hz` 重建，并在迁移说明中标注。

## 数据迁移目标规则

迁移工具或手工迁移应满足以下规则：

1. 每个 run 使用一个独立目录，命名建议为 `run_YYYYMMDD_HHMMSS`。
2. 每个 run 至少生成 `manifest.json`、`experiment_record.csv`、`spindle_info.csv`、`sensor_info.csv`、`segment_records.csv`。
3. 每个原始段转换为标准 `.npz.xz`，并包含全部必需数组。
4. `data` 一律使用 `(channel_count, sample_count)`，不要保存为 `(sample_count, channel_count)`。
5. `channels` 顺序必须和 `data` 第 0 轴一致。
6. `signal_type` 只使用当前枚举值：`acceleration`、`temperature_ntc`、`temperature_rtd`。
7. 振动单位使用 `g`，温度单位使用 `degC`。
8. `segment_records.csv` 中的 `sample_count`、`sample_start_index`、`time_start_s`、`time_end_s` 必须与 `.npz.xz` 内容一致。
9. 后处理生成 `segment_summary.csv` 时固定使用 1 秒窗口。
10. 新 run 不生成每段 JSON sidecar，不使用 pickle。

建议迁移流程：

1. 盘点旧数据：确认每个文件的数据源、通道、单位、采样率、时间轴和 run 元数据。
2. 设计通道映射：把旧通道映射到当前 `channel` 和 `sensor_id`。
3. 逐段转换原始数据：写入 `.npz.xz`，保留真实 `time_s`。
4. 生成 run 级索引：写 `manifest.json`、`sensor_info.csv`、`segment_records.csv`。
5. 执行后处理：生成 `segment_summary.csv` 和 `trends/summary_overview.png`。
6. 抽样验证：读取若干 `.npz.xz`，检查 shape、时间范围、通道名、单位和汇总结果。

## 分析使用建议

趋势和看板分析：

- 优先读取 `segment_summary.csv`。
- 按 `time_center_s` 作为横轴。
- 振动趋势使用 `acceleration__*__mean_abs`。
- 温度趋势使用 `temperature_ntc__*__mean` 和 `temperature_rtd__*__mean`。
- 主轴速度和电流分开分析，不共用 Y 轴。

原始波形分析：

- 读取 `.npz.xz` 的 `data` 和 `time_s`。
- 对振动频域分析，使用 `sample_rate_hz` 作为 FFT 或阶次分析的采样率依据。
- 对长时间热趋势，不需要回读全部原始温度段，优先用 1Hz 汇总。
- 对异常片段定位，可以先在 `segment_summary.csv` 中定位时间窗口，再通过 `segment_records.csv` 找到覆盖该时间的原始段。

通道和传感器命名：

- 跨 run 合并时优先使用 `sensor_id`。
- `sensor_id` 缺失时，列名会回退到物理通道名，可能包含设备名或 COM 口，迁移时应尽量补齐。
- 空白 metadata 不应持久化；只有非空字段才应该写回 `config/app_startup.json` 的 `channel_metadata`。

## 已知实验背景与解释口径

2026-07-07 的压缩存储集成后，主要分析 run 是：

```text
data/runs/run_20260707_185820
```

该 run 支持当前标准数据格式和 6000 rpm 标准采集流程的决策。它曾逐级经过 500、1000、2000、3000、4000、5000、6000、7000、8000 rpm，然后回到 6000 rpm。后续正式 6000 rpm 标准采集不应再超调到 8000 rpm，应直接爬升并稳定在 6000 rpm。

该 run 中，最终切到 6000 rpm 约在 121.015 s，实际转速约在 121.5 s 到达 6000 rpm。6000 rpm 保持约 582 s，也就是约 9.7 分钟。NTC 从约 23.98 degC 上升到约 26.55 degC 后开始减速。基于该 run，6000 rpm 热稳定等待时间建议至少 8 分钟，保守使用 8.5 到 9 分钟。

主轴电流数据当前存在负值和尖峰。分析中可以记录和展示，但在寄存器含义、缩放和符号验证前，不应用于负载、热稳定或数据有效性判断。

## 验证清单

迁移或分析前建议逐项检查：

- run 目录包含 `manifest.json`、`segment_records.csv`、`sensor_info.csv`。
- `manifest.json.storage_format` 是 `npz_xz_float64_with_time`。
- 每个 `segment_records.csv.data_path` 指向存在的 `.npz.xz`。
- 每个 `.npz.xz` 都能用 `read_segment_npz_xz` 或 `scripts/inspect_npz_xz.py` 读取。
- `time_s` 长度等于 `data.shape[1]`。
- `channels` 长度等于 `data.shape[0]`。
- `signal_type` 和 `unit` 与采集源一致。
- `segment_summary.csv` 的窗口为固定 1 秒。
- `trends/summary_overview.png` 存在，且四个子图含义正确。
- 没有新生成的每段 JSON sidecar。
- 没有 pickle 格式的实验数据。
