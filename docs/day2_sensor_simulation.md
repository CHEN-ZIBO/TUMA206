# Day 2 — Sensor Simulation Layer
## 2026-06-16 | TUMA206 Water Treatment Digital Twin

---

### 1. 今日目标

建立系统的"感知层"：虚拟水处理厂的传感器和基础数据流。今天产出的内容是整个系统的数据源头，后续所有模块（PLC、Historian、Dashboard、AI）都依赖这一层的输出。

---

### 2. 工艺流程确认

```
Raw Water Tank
    ↓  P-101 (inlet pump)      ← cmd: WT_P101_PUMP_CMD
Treatment Tank T-101           ← level: WT_T101_LEVEL_PV
    ↕  DP-101 (dosing pump)    ← cmd: WT_D101_DOSING_CMD
    |  pH Adjustment Loop      ← measured: WT_A101_PH_PV
    ↓  V-101 (outlet valve)    ← cmd: WT_V101_OUTLET_CMD
Clean Water Tank
```

选择这个四设备流程的理由：覆盖了任务书要求的 tanks、pumps、valves、level sensors、pH sensors 和 chemical dosing loop，同时简单到可以在 viva 中完整解释每个信号的物理含义。

---

### 3. 文件清单与职责

#### `src/config.py`
**职责**：所有可调常量的单一来源（Single Source of Truth）。

| 常量 | 值 | 说明 |
|------|----|------|
| `MQTT_BROKER_HOST` | `"localhost"` | 本地 Mosquitto broker |
| `MQTT_TOPIC_TAGS` | `"water_treatment/tags"` | 主要 tag 发布 topic |
| `SAMPLE_RATE_S` | `1.0` | 扫描周期（秒） |
| `TANK_CAPACITY_L` | `1000.0` | Treatment Tank T-101 容积（升） |
| `INITIAL_LEVEL_PCT` | `50.0` | 启动时初始液位（%） |
| `INLET_FLOW_NOMINAL_LPM` | `10.0` | P-101 开启时进水流量（L/min） |
| `OUTLET_FLOW_NOMINAL_LPM` | `12.0` | V-101 开启时出水流量（L/min） |
| `PH_SETPOINT` | `7.0` | pH 控制目标值 |
| `PH_DOSE_ON` | `6.8` | pH 低于此值启动加药泵 |
| `PH_DOSE_OFF` | `7.3` | pH 高于此值停止加药泵 |
| `LEVEL_LOW_PCT` | `40.0` | 液位低于此值启动进水泵 |
| `LEVEL_HIGH_PCT` | `80.0` | 液位高于此值停泵并开出水阀 |
| `LEVEL_CRITICAL_PCT` | `90.0` | 液位高于此值触发紧急报警 |
| `PUMP_FAULT_TIMEOUT_S` | `10` | 泵故障判断超时（秒） |

**设计决定**：把所有阈值和参数集中到 config.py，方便在 viva 时说明"如何调整控制参数而不需要修改控制逻辑"，也方便后续为不同工况创建配置文件。

---

#### `src/process_simulator.py`
**职责**：模拟 Treatment Tank T-101 的真实物理过程。

这一层代表"现实世界"——传感器读取的对象、PLC 控制的对象。

**状态变量**（每 `SAMPLE_RATE_S` 秒更新一次）：

| 变量 | 单位 | 物理含义 |
|------|------|----------|
| `level_pct` | % | 水箱液位 |
| `ph` | pH | 水箱内 pH 值 |
| `inlet_flow` | L/min | 实际进水流量（取决于泵状态） |
| `outlet_flow` | L/min | 实际出水流量（取决于阀门状态） |

**物理模型说明**：

- **液位动力学**：`Δlevel = (inlet_flow - outlet_flow) × dt / 60 / TANK_CAPACITY_L × 100`
  - 进水增加液位，出水减少液位，1 秒内变化约 ±0.017%（1000L 水箱）
- **pH 动力学**：
  - 进水时 pH 每秒下降 `PH_DISTURBANCE = 0.03`（模拟原水偏酸）
  - 加药时 pH 每秒上升 `PH_DOSING_EFFECT = 0.05`（NaOH 碱化）
  - pH 限制在 4.0–10.0

**接口**：
```python
process = ProcessSimulator()
process.apply_commands(pump_on=True, valve_open=False, dosing_on=False)
process.step(dt=1.0)   # advance physics
state = process.state  # dict: level_pct, ph, inlet_flow, outlet_flow, ...
```

---

#### `src/sensor_simulator.py`
**职责**：将 ProcessSimulator 的真实过程变量转换为带误差的传感器读数，并支持四种故障注入模式。

**故障模式对照表**：

| 模式 | 行为 | 模拟的工业故障 |
|------|------|---------------|
| `normal` | 真实值 + 高斯噪声 | 正常传感器 |
| `stuck` | 读数冻结在故障触发时的值 | 传感器卡死（如 pH 探头污染） |
| `drift` | 每秒累积单调偏差 | 传感器漂移（需要重新校准） |
| `spike` | 5% 概率产生大幅瞬态 | 电气干扰、接线松动 |

**噪声参数**：
| 传感器 | 噪声标准差 |
|--------|-----------|
| 液位 | ±0.3 % |
| pH | ±0.02 pH |
| 流量 | ±0.2 L/min |

**接口**：
```python
sensors = SensorSimulator(process)
sensors.set_fault("WT_A101_PH_PV", "stuck")   # 触发 pH 传感器故障
tags = sensors.read_all_tags()   # dict: 所有 tag 读数
```

**设计决定**：故障模式设计在传感器层（而不是过程层），是因为真实工业中传感器故障不影响物理过程本身——水箱的 pH 仍然在变化，只是读数错了。这样可以在 Dashboard 中同时展示"传感器读数"和"真实过程值"的偏差，更有说服力。

---

#### `src/mqtt_client.py`
**职责**：paho-mqtt 的轻量封装，实现消息发布/订阅，并在 broker 不可用时静默降级。

**设计决定**：使用 `graceful degradation`（静默降级）而非抛异常，原因是：
1. 演示时 MQTT broker 可能还没启动
2. 断网情况下系统的控制逻辑应该继续运行
3. 仿真、控制、历史记录都不应该因为通信层故障而停止

```
broker 可用  →  正常发布/订阅
broker 不可用 →  publish/subscribe 变成 no-op，日志一次警告
```

---

### 4. 验收标准检查

- [x] `python src/sensor_simulator.py` 独立运行，不依赖 Dashboard 或 MQTT
- [x] 每秒输出 level / pH / inlet_flow / outlet_flow
- [x] 数据范围合理：level 0–100%，pH 5–9，flow ≥ 0
- [x] 支持 normal / stuck / drift / spike 四种故障模式
- [x] 保存 60 秒 `data/sample_run.csv`
- [x] MQTT broker 可用时发布 JSON；不可用时静默跳过
- [x] `docs/tag_dictionary.md` 所有 tag 有 description / unit / type / sample rate

---

### 5. 运行命令

```bash
# 独立运行（无需 MQTT）
python src/sensor_simulator.py

# 与 MQTT broker 联合运行
mosquitto -v                          # Terminal 1
python src/sensor_simulator.py        # Terminal 2
```

---

### 6. Viva 准备

**Q: 为什么把 ProcessSimulator 和 SensorSimulator 分开？**
> 工业系统中，物理过程（水箱液位）和传感测量（传感器读数）是两个独立层次。传感器故障不会改变物理过程——pH 传感器卡死时水箱里的水仍然在酸化。分开设计使故障注入更真实，也让我们可以在仪表板中展示"sensor reading vs. true process value"的差异。

**Q: 为什么选用 Gaussian 噪声？**
> 电子传感器的热噪声在统计上接近高斯分布，是工业传感器噪声的合理近似。drift 和 spike 模式模拟系统性误差（校准漂移、电气干扰），不能用高斯模型。

**Q: MQTT 静默降级是否安全？**
> 在仿真环境中合理——通信层故障不应该使控制逻辑崩溃。真实工业系统中，MQTT 故障会触发 Infrastructure Fault alarm（Day 4/7 实现）。
