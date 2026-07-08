# DataAnalysis 分支数据说明

状态日期：2026-07-08

## 结论

本分支的核心目标是让后续分析者脱离采集程序代码，也能明确理解一组实验数据是如何采集、如何组织、如何读取和如何校验的。

当前标准数据形态是：

```text
一个 run 目录
+ run 级元数据
+ 原始采集段 .npz.xz
+ 原始段索引 segment_records.csv
+ 1 Hz 汇总表 segment_summary.csv
+ 趋势预览图 trends/summary_overview.png
```

原始波形和温度段统一保存为 `.npz.xz`：内部是 NumPy `npz` payload，外层使用 `lzma/xz` 压缩。每个原始段必须显式保存 `time_s`，后续分析应以文件内 `time_s` 为准，不要只用文件名、采样率或样本序号重建时间轴。

新数据不应使用 pickle 保存实验数据，不应再生成每段一个 JSON sidecar。run 级信息应集中在 `manifest.json`、`sensor_info.csv`、`segment_records.csv` 和 `segment_summary.csv` 中。

## 数据采集对象

一次 run 表示一次连续实验记录。当前数据主要来自四类信号：

| 信号 | `signal_type` | 数据来源 | 默认采样率 | 默认段长 | 单位 | 主要用途 |
| --- | --- | --- | ---: | ---: | --- | --- |
| 振动 | `acceleration` | NI 9234 | `25600 Hz` | `256000 samples` | `g` | 原始波形、频域分析、振动趋势 |
| NTC 温度 | `temperature_ntc` | DAMX-8013 两通道 NTC 温度卡 | `10 Hz` | `100 samples` | `degC` | 主要热稳定与安全温度通道 |
| RTD 温度 | `temperature_rtd` | NI 9216 | `10 Hz` | `100 samples` | `degC` | 辅助温度记录 |
| 主轴遥测 | `spindle_speed` / `spindle_current` | 主轴控制器轮询 | 配置决定 | 连续 CSV | `rpm` / `A` | 转速确认、电流记录 |

默认段长约为 10 秒：

- 振动：`256000 / 25600 = 10 s`
- 温度：`100 / 10 = 10 s`

NI、DAMX-8013 和主轴控制器在采集中相互独立。某一类设备离线时，不应阻塞其他可用设备继续记录。因此同一个 run 中可能只有部分信号类型存在，分析代码应按文件和表格内容判断实际可用数据。

## 推荐 run 目录结构

标准 run 目录建议如下：

```text
data/runs/run_YYYYMMDD_HHMMSS/
  manifest.json
  experiment_record.csv
  spindle_info.csv
  sensor_info.csv
  segment_records.csv
  segment_summary.csv
  spindle_telemetry.csv
  spindle_telemetry.json
  acceleration_25600Hz_256000samples/
    000001_segment_acceleration_25600Hz_256000samples_start0.npz.xz
    000002_segment_acceleration_25600Hz_256000samples_start256000.npz.xz
  temperature_ntc_10Hz_100samples/
    000001_segment_temperature_ntc_10Hz_100samples_start0.npz.xz
  temperature_rtd_10Hz_100samples/
    000001_segment_temperature_rtd_10Hz_100samples_start0.npz.xz
  trends/
    summary_overview.png
```

并非每个 run 都必须包含所有目录。例如没有连接主轴时，可以没有 `spindle_telemetry.csv`；没有 RTD 时，可以没有 `temperature_rtd_10Hz_100samples/`。但只要存在原始采集段，就必须在 `segment_records.csv` 中有对应记录。

## 原始段 `.npz.xz` 规范

`.npz.xz` 是标准 NumPy `npz` 文件再经过 `xz` 压缩后的文件。读取时应禁用 pickle。

每个原始段必须包含以下数组：

| 字段 | dtype | shape | 含义 |
| --- | --- | --- | --- |
| `time_s` | `float64` | `(sample_count,)` | 每个样本的 run 内相对时间，单位秒 |
| `data` | `float64` | `(channel_count, sample_count)` | 多通道数据；第 0 轴是通道，第 1 轴是样本 |
| `channels` | string | `(channel_count,)` | 与 `data` 第 0 轴一一对应的通道名 |
| `sample_start_index` | integer scalar | scalar array | 当前段第一个样本在该信号记录内的样本序号 |
| `sample_rate_hz` | float scalar | scalar array | 当前段采样率 |
| `signal_type` | string scalar | scalar array | 信号类型，例如 `acceleration` |
| `unit` | string scalar | scalar array | 单位，例如 `g` 或 `degC` |

必须满足的对齐规则：

- `time_s[i]` 对应 `data[:, i]`。
- `len(time_s) == data.shape[1]`。
- `len(channels) == data.shape[0]`。
- `channels` 的顺序必须与 `data` 第 0 轴一致。
- `time_s` 是分析事实来源，不要依赖文件名重建时间。
- 标量字段在 `npz` 中也按数组保存，读取时可用 `.item()` 转成 Python 标量。

最小读取示例：

```python
from pathlib import Path
import lzma
import numpy as np

path = Path("data/runs/run_YYYYMMDD_HHMMSS/acceleration_25600Hz_256000samples/000001_segment_acceleration_25600Hz_256000samples_start0.npz.xz")

with lzma.open(path, "rb") as f:
    payload = np.load(f, allow_pickle=False)
    time_s = payload["time_s"]
    data = payload["data"]
    channels = payload["channels"].astype(str)
    sample_rate_hz = float(payload["sample_rate_hz"].item())
    signal_type = str(payload["signal_type"].item())
    unit = str(payload["unit"].item())

print(signal_type, data.shape, sample_rate_hz, unit)
```

## run 级文件说明

### `manifest.json`

`manifest.json` 是 run 的主索引，建议包含：

- `run_id`
- `created_at_local`
- `output_dir`
- `storage_format`，当前标准为 `npz_xz_float64_with_time`
- `time_axis` 说明
- 原始段格式说明
- 采集配置快照
- 可用设备快照
- 后处理结果，例如 `postprocess`

分析程序不应假设 `manifest.json` 一定包含完整硬件配置。旧数据迁移或异常采集时，缺失字段应保留为空或在说明字段中解释，而不是伪造。

### `experiment_record.csv`

一行 run 级实验记录，用于描述实验条件和标签。常见用途：

- 按 `run_id` 关联其他文件。
- 按 `spindle_id`、`target_speed_rpm`、`label`、异常标记筛选实验。
- 记录热状态、实验日期、备注等人工信息。

### `spindle_info.csv`

一行主轴基础信息，通常包含主轴 ID、型号、额定转速、最高转速、测试日期和累计运行时间。跨 run 分析主轴长期趋势时，应优先用这里的信息识别设备。

### `sensor_info.csv`

每个传感器或物理通道一行，用于解释原始数据列的含义。建议字段：

| 字段 | 含义 |
| --- | --- |
| `signal_type` | 信号类型 |
| `channel` | 物理通道名 |
| `device_name` | 采集设备名 |
| `product_type` | 设备型号 |
| `sensor_id` | 传感器 ID，跨 run 合并时优先使用 |
| `measurement_position` | 测点位置 |
| `direction` | 测量方向 |
| `mounting_method` | 安装方式 |
| `sample_rate_hz` | 采样率 |
| `unit` | 单位 |
| `plot` | 是否默认绘图 |
| `save` | 是否保存 |

生成 `segment_summary.csv` 时，列名优先使用 `sensor_id`。如果 `sensor_id` 为空，则回退到物理通道名。因此迁移旧数据或整理新数据时，应尽量补齐稳定的 `sensor_id`。

### `segment_records.csv`

`segment_records.csv` 是原始段索引。每个保存的 `.npz.xz` 段应有一行记录。

核心字段：

| 字段 | 含义 |
| --- | --- |
| `segment_index` | 段序号 |
| `signal_type` | 信号类型 |
| `channels` | 本段包含的通道 |
| `unit` | 单位 |
| `sample_rate_hz` | 采样率 |
| `sample_count` | 样本数 |
| `sample_duration_s` | 段持续时间 |
| `sample_start_index` | 段起始样本序号 |
| `sample_end_index` | 段结束样本序号 |
| `time_start_s` | 段起始时间 |
| `time_center_s` | 段中心时间 |
| `time_end_s` | 段结束时间 |
| `partial` | 是否为不足完整段长的尾段 |
| `data_format` | 原始段格式，当前为 `.npz.xz` |
| `data_file` | 原始段文件名 |
| `data_path` | 原始段相对路径或路径 |

读取原始波形时，推荐先用 `segment_records.csv` 筛选时间范围和信号类型，再打开对应 `.npz.xz`。

### `segment_summary.csv`

`segment_summary.csv` 是后处理生成的 1 Hz 宽表。即使原始段是 10 秒一段，汇总仍按 `time_s` 拆成固定 1 秒窗口。

基础时间列：

- `time_start_s`
- `time_center_s`
- `time_end_s`

常见信号列命名：

```text
acceleration__<sensor_or_channel>__mean_abs
acceleration__<sensor_or_channel>__max
acceleration__<sensor_or_channel>__min
temperature_ntc__<sensor_or_channel>__mean
temperature_ntc__<sensor_or_channel>__max
temperature_ntc__<sensor_or_channel>__min
temperature_rtd__<sensor_or_channel>__mean
temperature_rtd__<sensor_or_channel>__max
temperature_rtd__<sensor_or_channel>__min
spindle_speed__mean
spindle_speed__max
spindle_speed__min
spindle_current__mean
spindle_current__max
spindle_current__min
```

趋势分析、看板和长时间热稳定分析应优先读取 `segment_summary.csv`。只有需要原始波形、频域特征、冲击片段或更细粒度时，才回读 `.npz.xz`。

### `spindle_telemetry.csv`

主轴连接时，会额外记录遥测 CSV。推荐列：

```text
sample_index,time_s,target_rpm,actual_speed_rpm,current_a,speed_ok,current_ok,keepalive_enabled
```

主轴转速和电流会进一步进入 `segment_summary.csv` 的 1 Hz 汇总列。

注意：当前已知主轴电流数据存在负值和尖峰。在寄存器含义、缩放和符号完全验证前，电流只建议用于展示和记录，不应作为负载、热稳定或数据有效性判据。

### `trends/summary_overview.png`

停止记录后的标准趋势预览图应包含四个堆叠子图：

1. 振动通道趋势，使用 `mean_abs`。
2. 温度通道趋势，使用 `mean`，NTC 优先于 RTD。
3. 主轴转速单独一图。
4. 主轴电流单独一图。

不要把主轴转速和电流画在同一个子图或 twin Y 轴上。

## 时间轴约定

原始段中的 `time_s` 是 run 内相对时间，单位秒。

不同设备的时间来源不同：

- NI 振动和 RTD 使用采集设备的采样时钟。
- DAMX-8013 NTC 使用串口轮询计数和配置采样率形成时间轴。
- 主轴遥测使用主轴记录器启动后的单调时钟差值。

通常趋势分析可直接按 `time_s` 的 1 秒窗口合并。若要做亚秒级跨设备同步、严格相位分析或设备延迟分析，需要检查具体 run 的触发顺序、设备延迟和采样时钟来源。

当前格式没有为每个样本保存绝对墙钟时间。若旧数据迁移时存在真实时间戳，应优先写入真实相对 `time_s`；如果只有样本序号和采样率，才使用 `sample_index / sample_rate_hz` 重建，并在迁移说明中标注。

## 读取建议

### 趋势和热稳定分析

优先读取：

```text
segment_summary.csv
```

推荐方式：

- 使用 `time_center_s` 作为横轴。
- 振动趋势使用 `acceleration__*__mean_abs`。
- 温度趋势优先使用 `temperature_ntc__*__mean`。
- RTD 可作为辅助温度记录。
- 主轴转速和主轴电流分开分析。

### 原始波形和频域分析

读取路径：

```text
segment_records.csv -> 筛选目标时间段 -> 打开对应 .npz.xz
```

推荐方式：

- 使用 `.npz.xz` 内的 `sample_rate_hz` 作为 FFT、阶次分析或滤波依据。
- 使用 `.npz.xz` 内的 `time_s` 对齐原始数据。
- 不要假设文件名中的 `start...` 一定等同于有效时间轴。

### 异常片段定位

推荐流程：

1. 在 `segment_summary.csv` 中定位异常时间窗口。
2. 用 `segment_records.csv` 找到覆盖该时间范围的原始段。
3. 读取 `.npz.xz` 并按 `time_s` 截取局部数据。
4. 回到 `sensor_info.csv` 确认通道、传感器 ID、测点位置和方向。

## 6000 rpm 标准采集背景

2026-07-07 的压缩存储集成实验支持当前标准格式和 6000 rpm 标准采集流程。主要参考 run：

```text
data/runs/run_20260707_185820
```

该 run 曾逐级经过 500、1000、2000、3000、4000、5000、6000、7000、8000 rpm，随后回到 6000 rpm。后续正式 6000 rpm 标准采集不建议再超调到 8000 rpm，应直接爬升并稳定在 6000 rpm。

该 run 中最终切换到 6000 rpm 约在 `121.015 s`，实际转速约在 `121.5 s` 到达 6000 rpm。6000 rpm 保持约 `582 s`，即约 `9.7 min`。NTC 从约 `23.98 degC` 上升到约 `26.55 degC` 后开始减速。

基于该 run，6000 rpm 热稳定等待时间建议至少 8 分钟；保守使用 `8.5-9 min`。NTC 是当前主要热稳定和安全温度通道。RTD 应继续记录，但在安装位置和灵敏度改善前，不建议作为主要热稳定门限。

## 数据迁移和整理规则

旧数据迁移到本标准时，应满足：

1. 每个实验使用一个独立 run 目录，推荐命名为 `run_YYYYMMDD_HHMMSS`。
2. 每个 run 至少补齐 `manifest.json`、`sensor_info.csv`、`segment_records.csv`。
3. 如有实验标签和设备信息，补齐 `experiment_record.csv`、`spindle_info.csv`。
4. 每个原始段转换为标准 `.npz.xz`。
5. `data` 一律保存为 `(channel_count, sample_count)`。
6. `channels` 顺序必须与 `data` 第 0 轴一致。
7. `signal_type` 使用当前规范枚举值。
8. 振动单位使用 `g`，温度单位使用 `degC`。
9. `segment_records.csv` 中的样本数、起止样本、起止时间必须与 `.npz.xz` 内容一致。
10. 后处理汇总固定使用 1 秒窗口。
11. 不生成每段 JSON sidecar。
12. 不使用 pickle 保存实验数据。

## 校验清单

分析或迁移前建议检查：

- run 目录存在 `manifest.json`、`segment_records.csv`、`sensor_info.csv`。
- `manifest.json` 中的 `storage_format` 为 `npz_xz_float64_with_time` 或明确说明等价格式。
- `segment_records.csv` 中每条 `data_path` 都能指向存在的 `.npz.xz`。
- 每个 `.npz.xz` 都能用 `np.load(..., allow_pickle=False)` 读取。
- 每个 `.npz.xz` 都包含 `time_s`、`data`、`channels`、`sample_start_index`、`sample_rate_hz`、`signal_type`、`unit`。
- `len(time_s) == data.shape[1]`。
- `len(channels) == data.shape[0]`。
- `signal_type`、`unit` 与采集来源一致。
- `segment_summary.csv` 使用固定 1 秒窗口。
- `trends/summary_overview.png` 如存在，应为四个堆叠子图。
- 新数据中没有每段 JSON sidecar。
- 实验数据中没有 pickle 格式文件。
