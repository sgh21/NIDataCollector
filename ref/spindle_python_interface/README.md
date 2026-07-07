# Spindle GUI

精雕主轴独立 GUI 控制程序，支持：

- COM 口和波特率设置
- 连接/断开主轴
- 设置主轴转速
- 停止主轴
- 实时显示转速和电流
- 分别绘制转速和电流曲线
- 目标转速大于 0 时周期性写入运行使能和目标转速，模拟原 LabVIEW 软件的运行保活

## 运行

关闭原 LabVIEW 控制软件，避免占用 `COM4`。

```powershell
conda activate NI
cd F:\WorkSpace\spindle_python_interface
python run_gui.py
```

## 配置

配置文件：

```text
spindle_gui_config.json
```

常用可改项：

```json
{
  "serial": {
    "port": "COM4",
    "baudrate": 19200,
    "timeout": 0.35
  },
  "ui": {
    "poll_interval_ms": 100,
    "keepalive_interval_ms": 200,
    "plot_window_seconds": 30
  },
  "signals": {
    "speed": {
      "address": "0x3008",
      "response_id": "0x02",
      "scale": 0.01
    },
    "current": {
      "address": "0x3002",
      "response_id": "0x03",
      "scale": 0.01
    }
  }
}
```

读取失败时，界面会保留并显示上一次成功读取的值，避免曲线出现 `nan` 和界面抖动。

调试主轴是否响应时，建议先在界面中设置 `300` 到 `500` rpm。点击“设置”后底部状态栏应显示“保活开启”；点击“停止”会写入 `0 rpm`、停止使能并保持读取反馈。
