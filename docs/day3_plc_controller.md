# Day 3 — PLC Controller (Simulated Scan Cycle)
## 2026-06-17 | TUMA206 Water Treatment Digital Twin

---

### 1. 今日目标

实现 Treatment Tank T-101 的模拟 PLC 控制器，以及把所有模块串联起来的主循环。

PLC 是整个系统的"大脑"——它读取传感器、执行控制逻辑、输出执行器命令。今天产出的内容使系统从"只会读数据"升级为"能自动控制"。

---

### 2. 核心概念：PLC 扫描循环

真实 PLC 的核心是**确定性扫描循环（Deterministic Scan Cycle）**：

```
┌─────────────────────────────────────────────┐
│              PLC Scan Cycle                 │
│                                             │
│  1. Read Inputs   ← sensor tags dict        │
│         ↓                                   │
│  2. Execute Logic ← state machine + control │
│         ↓                                   │
│  3. Update Outputs → command tags dict      │
│                                             │
│  repeat every SAMPLE_RATE_S = 1.0 s         │
└─────────────────────────────────────────────┘
```

用 Python 模拟这个循环而不用真实 PLC 硬件，是因为任务书明确要求 **simulated PLC**。Python 实现保留了扫描循环的确定性结构，可以在 viva 中完整解释。

---

### 3. 文件清单与职责

#### `src/plc_controller.py`

**职责**：实现完整的 PLC 仿真，包含：
- 状态机（5 个状态）
- 液位 on/off 控制
- pH 迟滞 on/off 加药控制（含伪 PID 积分状态）
- 6 种报警
- 设备故障检测（泵命令-反馈不一致）
- 手动复位接口（供故障注入器使用）

---

#### 3.1 状态机设计

| 状态 | 触发条件 | 执行器状态 |
|------|---------|-----------|
| `IDLE` | 启动或复位后 | 全部关闭 |
| `FILLING` | level < 40% | 进水泵 ON；加药按需 |
| `TREATING` | 40% ≤ level ≤ 80% | 维持液位；加药按需 |
| `DISCHARGING` | level > 80% | 泵 OFF；出水阀 OPEN |
| `FAULT` | 任意紧急报警 | 全部 OFF（安全模式） |

**状态转移图**：

```
           level < 40%
  IDLE ──────────────► FILLING
                          │
                   level ≥ 40%
                          ▼
                      TREATING ◄──────── level ≤ 80%
                          │                   │
                   level > 80%                │
                          ▼                   │
                     DISCHARGING ─────────────┘

  任意状态 ──[紧急报警]──► FAULT
  FAULT   ──[reset_fault()]──► IDLE
```

---

#### 3.2 控制规则

**液位控制（On/Off）**：

| 条件 | 逻辑 | 输出 |
|------|------|------|
| `LEVEL_PV < 40%` | 液位低 → 需要进水 | 进水泵 P-101 ON |
| `LEVEL_PV > 80%` | 液位高 → 需要排水 | 阀门 V-101 OPEN，泵 OFF |

**pH 控制（迟滞 On/Off）**：

| 条件 | 逻辑 | 输出 |
|------|------|------|
| `pH < 6.8` | pH 过低 → 需要加碱 | 加药泵 DP-101 ON |
| `pH > 7.3` | pH 恢复 → 停止加药 | 加药泵 DP-101 OFF |

迟滞死区（6.8–7.3）防止了频繁开关（继电器抖振），这是工业控制中标准做法。伪 PID 积分状态为后续升级为真正 PID 控制保留了接口。

---

#### 3.3 报警设计

| 报警名称 | 触发条件 | 级别 | 后续动作 |
|---------|---------|------|---------|
| `ALM_LEVEL_LOW` | level < 40% | Warning | 进入 FILLING |
| `ALM_LEVEL_HIGH` | level > 80% | Warning | 进入 DISCHARGING |
| `ALM_LEVEL_CRIT` | level > 90% | **Critical** | 进入 FAULT |
| `ALM_PH_LOW` | pH < 6.0 | **Critical** | 进入 FAULT |
| `ALM_PH_HIGH` | pH > 8.5 | **Critical** | 进入 FAULT |
| `ALM_PUMP_FAIL` | PUMP_FB=ON 且 flow≈0 持续 10s | **Critical** | 进入 FAULT |

**设备故障检测逻辑（ALM_PUMP_FAIL）**：

```python
if pump_fb == True and inlet_flow < 0.5 L/min:
    pump_fail_counter += 1
else:
    pump_fail_counter = 0

if pump_fail_counter >= PUMP_FAULT_TIMEOUT_S:   # 10 s
    raise ALM_PUMP_FAIL
```

这模拟了工业中 `command-vs-feedback mismatch` 检测：PLC 发出 ON 命令但电机实际没有转动（或转了但没有流量），持续超过阈值时间则判定为设备故障。

---

#### 3.4 接口

```python
plc = PLCController()

# 每个扫描周期调用一次
commands = plc.execute(sensor_tags)
# 返回 dict:
# {
#   "WT_P101_PUMP_CMD":   bool,
#   "WT_V101_OUTLET_CMD": bool,
#   "WT_D101_DOSING_CMD": bool,
#   "WT_ALM_ACTIVE":      bool,
#   "alarms":             dict,   # 各报警的独立状态
#   "state":              str,    # 当前状态机状态名
# }

# 手动复位（用于故障注入器测试）
plc.reset_fault()
plc.force_state(State.TREATING)
```

---

#### `src/main.py`

**职责**：主控循环，按正确顺序串联所有模块。

**循环顺序（critical — 必须按此顺序）**：

```python
while running:
    # 1. 先步进物理过程（使用上一周期设置的命令）
    process.step(dt)

    # 2. 读取传感器（含噪声/故障）
    sensor_tags = sensors.read_all_tags()

    # 3. PLC 扫描：输入 → 逻辑 → 输出
    commands = plc.execute(sensor_tags)

    # 4. 把命令写回物理过程（下一周期生效）
    process.apply_commands(pump_cmd, valve_cmd, dosing_cmd)

    # 5. 发布到 MQTT
    mqtt.publish_tags(full_tags)

    # 6. 写入 Historian
    historian.write(full_tags)

    # 7. 等待下一个扫描周期
    sleep(remaining_time)
```

**为什么 step() 在 apply_commands() 之前？**
这模拟了真实 PLC 的行为：上一周期发出的命令驱动当前周期的物理过程，然后读取结果，再计算下一周期命令。如果顺序反了，会出现"零延迟响应"，与真实控制系统不一致。

---

### 4. 测试覆盖（`tests/test_plc_controller.py`）

| 测试用例 | 验证内容 |
|---------|---------|
| `test_idle_to_filling_when_level_low` | level<40% 触发 IDLE→FILLING，泵 ON |
| `test_filling_to_treating` | level≥40% 时进入 TREATING |
| `test_treating_to_discharging` | level>80% 时进入 DISCHARGING，阀 OPEN |
| `test_discharging_returns_to_treating` | level≤80% 时返回 TREATING |
| `test_dosing_on_when_ph_low` | pH<6.8 时加药泵 ON |
| `test_dosing_off_when_ph_normal` | pH>7.3 时加药泵 OFF |
| `test_critical_level_alarm` | level>90% 触发 FAULT + ALM_LEVEL_CRIT |
| `test_fault_state_all_outputs_off` | FAULT 状态下所有输出均为 OFF |
| `test_ph_low_alarm` | pH<6.0 触发 ALM_PH_LOW |
| `test_ph_high_alarm` | pH>8.5 触发 ALM_PH_HIGH |
| `test_pump_fail_alarm_after_timeout` | 泵 FB=ON 但 flow≈0 持续 10s → ALM_PUMP_FAIL |
| `test_pump_fail_counter_resets` | 流量恢复后计数器归零 |
| `test_reset_fault_clears_state` | reset_fault() 返回 IDLE，清除所有报警 |

**结果**：13/13 通过

---

### 5. 验收标准检查

- [x] 实现确定性扫描循环（read inputs → execute logic → update outputs）
- [x] 实现状态机：IDLE / FILLING / TREATING / DISCHARGING / FAULT
- [x] 实现液位 on/off 控制（进水泵 + 出水阀）
- [x] 实现 pH 迟滞 on/off 加药控制
- [x] 生成命令 tags：pump_cmd / valve_cmd / dosing_cmd
- [x] 实现液位高/低/紧急、pH 越界 4 种过程报警
- [x] 实现设备故障检测（pump command-vs-feedback mismatch）
- [x] 代码可读，适合 viva 时逐行解释

---

### 6. Viva 准备

**Q: Python 怎么模拟 PLC 扫描周期？**
> PLC 的核心是确定性循环 `read → execute → write`。我们用 `while True` 加 `time.sleep(1.0)` 实现固定 1 秒扫描周期。scan cycle 内的执行时间通过 `time.monotonic()` 测量，从睡眠时间中扣除，保证每次循环正好 1 秒。

**Q: 为什么用状态机而不是简单的 if/else？**
> 状态机明确了系统在不同工况下的行为边界，防止了逻辑冲突（例如同时 FILLING 和 DISCHARGING）。这也是 IEC 61131-3 Sequential Function Chart (SFC) 的基础概念。

**Q: pH 控制为什么不直接用 PID？**
> 加药泵是开关量设备（ON/OFF），不支持连续调节。迟滞 on/off 是离散执行器的标准控制方式。伪 PID 积分状态已保留，可以在有连续调节阀的情况下升级。

**Q: ALM_PUMP_FAIL 如何在 60 秒内检测？**
> 泵命令为 ON 但流量接近 0 时，每个扫描周期（1 秒）计数器加一。`PUMP_FAULT_TIMEOUT_S = 10`，即 10 秒后报警——远小于 60 秒要求。
