# Day 5 — MQTT / SCADA 架构 + 通信层加固
## 2026-06-19 | TUMA206 Water Treatment Digital Twin

---

### 1. 今日目标

建立系统的"神经系统"：完整的工业通信架构文档，以及将 Day 2 的简单 MQTT wrapper 升级为具备自动重连、非阻塞发布、多 topic 订阅和基础设施故障检测的生产级通信层。

今天的交付物直接对应课程 Learning Outcome LO3（设计 tag namespace 和端到端数据路径）和 LO6（比较和论证架构选择）。

---

### 2. 文件清单与职责

#### `docs/architecture.md`
系统架构完整文档。覆盖：
- ISA-95 / Purdue 层次模型映射
- 三终端运行拓扑
- MQTT topic 设计与 payload 格式
- QoS 策略表
- 边缘 vs 云的选择决策
- 端到端数据流图
- 模块依赖图
- 技术栈选型理由

#### `src/mqtt_client.py`（重写）
从 Day 2 的简单 wrapper 升级为完整通信层。

---

### 3. MQTT Client 新增功能详解

#### 3.1 架构变化对比

| 特性 | Day 2 版本 | Day 5 版本 |
|------|-----------|-----------|
| 连接失败处理 | 一次性尝试，失败则 `_client = None` | 后台线程指数退避重试（1→2→4→…→30 s）|
| 发布方式 | 主线程直接调用 `client.publish()` | 存入 `_send_queue`，sender 线程异步发送 |
| 订阅 | 单一全局 `on_message` | 每个 topic 独立 callback 列表 |
| 基础设施故障检测 | 无 | `infra_alarm` 属性，断开超 30 s 为 True |
| 诊断信息 | `connected` 属性 | `stats()` 返回 published / dropped / reconnects 等 |
| 重连后恢复订阅 | 无（订阅丢失） | `_on_connect` 中重新订阅所有已注册 topic |
| 消息序列号 | 无 | 每条消息附带 `seq`，消费者可检测消息丢失 |

---

#### 3.2 连接状态机

```
         init
           │
           ▼
     ┌─────────────┐  broker 无响应   ┌───────────────┐
     │ CONNECTING  │ ────────────────► │  WAIT(back-off)│
     └─────┬───────┘                  └───────┬───────┘
           │ on_connect(rc=0)                 │ delay 到期
           ▼                                  │
     ┌─────────────┐  on_disconnect(rc≠0)    │
     │  CONNECTED  │ ──────────────────────► RECONNECTING ◄────┘
     └─────────────┘                              │
                                                  │ on_connect(rc=0)
                                                  │ 重新订阅所有 topic
                                                  ▼
                                           CONNECTED
```

**指数退避（Exponential Back-off）**：
- 第 1 次重试等待 1 s
- 第 2 次等待 2 s
- 第 3 次等待 4 s
- …直到上限 30 s
- 防止 broker 重启时大量客户端同时重连（惊群问题）

---

#### 3.3 非阻塞发布队列

主循环（1 Hz）必须在 1 秒内完成所有操作。如果直接调用 `client.publish()`，在网络抖动时可能阻塞数百毫秒，影响 PLC 扫描周期精度。

解决方案：**发布队列 + 独立 sender 线程**

```
主循环线程                         sender 线程（守护线程）
────────────────                  ──────────────────────
publish(topic, payload)           while not shutdown:
  → queue.put_nowait(envelope)      envelope = queue.get(timeout=1)
  → return  (< 1 μs)               if connected:
                                      client.publish(...)
                                    else:
                                      queue.put(envelope)  # 持有待重连
```

队列满（200 条，约 3 分钟）时丢弃最旧消息，`_dropped` 计数器递增，`stats()` 中可见。

---

#### 3.4 基础设施故障检测（Infrastructure Fault）

对应 Day 7 四层故障中的 **Infrastructure Layer**。

```python
@property
def infra_alarm(self) -> bool:
    if self._connected:
        return False
    if self._disconnect_since is None:
        return False
    elapsed = time.monotonic() - self._disconnect_since
    return elapsed >= INFRA_FAULT_TIMEOUT_S   # 30 秒
```

`main.py` 每个扫描周期检查此属性，一旦为 True 则：
1. 在 `full_tags` 中设置 `WT_ALM_INFRA_STALE = True`
2. 写入 historian（本地记录不受影响）
3. 显示在 Dashboard 报警面板

AI assistant 对此故障的建议："通信层中断——验证 MQTT broker 是否运行。PLC 继续本地控制，数据将在连接恢复后重新同步。"

---

#### 3.5 多 topic 订阅与 callback 路由

```python
# 注册方式
mqtt.subscribe("water_treatment/commands", handle_operator_command)
mqtt.subscribe("water_treatment/tags",     log_tag_snapshot)
# 同一 topic 可注册多个 callback
mqtt.subscribe("water_treatment/alarms",   dashboard_alarm_handler)
mqtt.subscribe("water_treatment/alarms",   ai_alert_handler)

# 注销
mqtt.unsubscribe("water_treatment/commands")
```

内部数据结构：`dict[topic, list[callable]]`，每条收到的消息广播给该 topic 的所有 callback。单个 callback 抛异常不影响其他 callback 的执行。

---

### 4. MQTT Topic 设计原则

#### 4.1 Namespace 规则

```
{plant}/{message_type}
water_treatment/tags
water_treatment/alarms
water_treatment/faults
water_treatment/commands
```

- 第一级 `water_treatment`：标识工厂，支持未来多工厂同 broker 部署
- 第二级是**消息类型**，不是 tag 名称
  - 原因：按类型订阅比按 tag 订阅更自然（Dashboard 订阅 `/tags`，告警系统订阅 `/alarms`）
  - 替代方案（每个 tag 一个 topic）会导致订阅 explosion（10 个 topic vs 4 个）

#### 4.2 为什么所有 tag 打包成一个 JSON 而不是每 tag 一条消息？

| 方式 | 优点 | 缺点 |
|------|------|------|
| 每 tag 一条消息 | 消费者可选择性订阅单个 tag | 10 个 tag = 10 条消息/秒；消费者需拼合同一时刻数据 |
| 所有 tag 一条消息 | 原子性：同一时刻的所有 tag 一起到达 | 消费者收到不需要的 tag |

选择一条消息：因为 PLC 的扫描周期产生的是一组**同时刻**数据，原子性更重要。Dashboard 需要同一时刻的 level + pH + flow 才能绘制一致的趋势图。

#### 4.3 QoS 策略

| Topic | QoS | 理由 |
|-------|:---:|------|
| `water_treatment/tags` | 0 | 1 Hz 高频；最新值比历史每条都到达更重要；丢一条无所谓 |
| `water_treatment/alarms` | 1 | 操作员必须看到报警；至少送达一次 |
| `water_treatment/faults` | 1 | 故障注入事件需要审计轨迹 |
| `water_treatment/commands` | 1 | 控制命令不能静默丢失 |

---

### 5. 架构文档（`docs/architecture.md`）覆盖内容

| 章节 | 内容 |
|------|------|
| 1. System Overview | 含 ISA-95 Purdue 层次图的架构全貌 |
| 2. ISA-95 / Purdue Layer Mapping | 每个 Python 模块对应哪个 Purdue 层次 |
| 3. Runtime Topology | 三终端运行示意图，Dashboard 读历史而非订阅 MQTT 的说明 |
| 4. MQTT Topic Design | namespace 规则、4 个 topic 的 payload 格式、QoS 策略表 |
| 5. MQTT Client Design | 连接状态机、非阻塞发布队列、基础设施故障检测 |
| 6. Edge vs Cloud Decision | 决策矩阵 + 理由 + 未来升级路径 |
| 7. Data Flow Diagram | 从物理过程到 Dashboard 的完整数据路径 |
| 8. Technology Stack Justification | 每项技术选型理由 + 为何不选替代品 |
| 9. Module Dependency Graph | 无循环依赖的模块关系图 |

---

### 6. 测试覆盖（`tests/test_mqtt_client.py`）

| 测试用例 | 验证内容 |
|---------|---------|
| `test_not_connected_on_unreachable_broker` | broker 不可达时 `connected = False` |
| `test_publish_does_not_raise_when_offline` | 离线时所有 publish 方法不抛异常 |
| `test_send_queue_accumulates_while_offline` | 离线时消息在队列中积压 |
| `test_send_queue_drops_oldest_when_full` | 队列满时 dropped 计数器递增 |
| `test_stats_keys_present` | `stats()` 包含所有必要字段 |
| `test_subscribe_registers_callback` | `subscribe()` 注册 callback 到内部字典 |
| `test_multiple_callbacks_per_topic` | 同一 topic 支持多个 callback |
| `test_unsubscribe_removes_callbacks` | `unsubscribe()` 清除所有 callback |
| `test_infra_alarm_false_before_timeout` | 超时前 `infra_alarm = False` |
| `test_infra_alarm_triggers_after_disconnect` | 断开超阈值后 `infra_alarm = True` |
| `test_seq_increments_on_each_publish` | 每次 publish 序列号递增 |
| `test_disconnect_does_not_raise` | 多次调用 `disconnect()` 安全 |
| `test_message_routing_to_callback` | 收到消息后正确路由到 callback |
| `test_malformed_message_does_not_crash` | 非 JSON 消息静默丢弃 |

**结果**：14/14 通过

---

### 7. 验收标准检查

- [x] 定义 4 个 MQTT topic：`/tags` / `/alarms` / `/faults` / `/commands`
- [x] 说明 tag namespace 和 payload JSON 格式（在 architecture.md）
- [x] 说明边缘 vs 上游服务（Edge vs Cloud 决策章节）
- [x] 实现重连和 broker 不可用处理（指数退避 + 连接状态机）
- [x] 包含 ASCII 架构图和设计论证（architecture.md 全文）

---

### 8. Viva 准备

**Q: 为什么系统用 MQTT 而不是 OPC-UA？**
> MQTT 轻量、发布/订阅结构天然适合传感器数据广播；paho-mqtt 零配置可用；教室环境下 Mosquitto 比 OPC-UA server 更稳定。OPC-UA 的优势（信息模型、安全通道）在本 prototype 中不是首要需求。真实工厂中两者常共存：OPC-UA 用于 PLC↔SCADA，MQTT 用于 IIoT/云上报。

**Q: QoS 0 的 `/tags` 消息丢了怎么办？**
> `/tags` 是 1 Hz 连续流，丢一条只影响一个数据点，下一秒就有新数据填补。historian 在本地连续记录，不依赖 MQTT 传输。Dashboard 从 historian 读趋势图，不从 MQTT 订阅，所以 QoS 0 不影响展示连续性。

**Q: 如何区分 MQTT 断线和真实过程报警？**
> `infra_alarm` 只在 `disconnect_since` 超过阈值（30 s）时为 True，而过程报警来自 PLC 的 alarm dict（`ALM_LEVEL_CRIT` 等）。两者来源完全不同，不会混淆。AI assistant 的 rule-based 诊断对两者有不同的建议文本。

**Q: 序列号有什么用？**
> `seq` 是单调递增整数。消费者检测到不连续序列号（例如从 100 跳到 105）就知道丢了 4 条消息，可以在 Dashboard 显示"数据间隙"警告，帮助区分"系统正常但网络抖动"和"进程崩溃"两种情况。
