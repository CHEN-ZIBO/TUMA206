# Day 4 — Historian & Time-Series Data Logging
## 2026-06-18 | TUMA206 Water Treatment Digital Twin

---

### 1. 今日目标

建立系统的"记忆层"：将每个扫描周期的所有 tag 持久化到磁盘，同时在内存中保留一个滑动窗口供仪表板和 AI 助手快速读取。

Historian 是 Industry 4.0 数据链路上的关键节点：`field device → controller → MQTT → **Historian** → Dashboard → AI`。没有 Historian，仪表板只能显示当前状态，无法展示趋势，AI 助手也没有历史数据可以分析。

---

### 2. 设计决策与权衡

#### 为什么选 CSV 而不是 InfluxDB / SQLite？

| 方案 | 优点 | 缺点 | 我们的选择 |
|------|------|------|-----------|
| CSV | 无依赖；人眼可读；git 可追踪；演示时打开文件即可验证 | 不支持查询语言；大文件慢 | ✅ 第一版 |
| SQLite | 支持 SQL 查询；事务安全 | 需要额外设置；不如 CSV 直观 | 可选升级 |
| InfluxDB | 时序优化；支持 Flux/PromQL | 需要独立进程；配置复杂 | 未来生产 |

结论：CSV 足够验证 time-series logging 概念，演示时直接展示文件内容比任何 DB 都有说服力。接口设计保持数据库无关（`write()` / `load_recent()`），升级到 SQLite 或 InfluxDB 只需替换内部实现。

---

#### 为什么需要双轨存储（ring buffer + CSV）？

仪表板每秒刷新一次，每次需要读取最近 300 秒的数据绘制趋势图。如果每次都从 CSV 读取：
- 文件运行 1 小时后有 3600 行
- 每秒读取 3600 行 × 每行 ~120 字节 ≈ 432 KB → 每分钟 25 MB I/O

内存 ring buffer 将读取代价从 O(file_size) 降到 O(n_seconds)，完全消除磁盘 I/O 压力：

```
Dashboard 读取路径：
  load_recent(300)  →  _ring[-300:]  →  O(300) 内存复制  →  完成

写入路径：
  write(tags)  →  _buffer.append()  →  O(1) 内存操作  →  返回
                                           ↓
                           [后台线程每 5 秒]
                                           ↓
                                    CSV append  →  磁盘
```

---

#### 线程安全模型

系统中有两个并发访问 Historian 的线程：
- **主循环线程**：每秒调用 `write()`
- **Dashboard / AI 线程**：调用 `load_recent()` 读取最新数据

使用单个 `threading.Lock` 保护 `_buffer` 和 ring buffer 的所有操作。

选择单锁而不是 `concurrent.futures` 或读写锁，是因为：
1. 写入是 O(1) 追加，持锁时间极短（< 1 μs）
2. 读取从 ring buffer 复制，也是 O(n) 内存操作，不需要长时间持锁
3. 单锁逻辑简单，viva 时容易解释

---

### 3. 文件清单与职责

#### `src/historian.py`

**架构图**：
```
主循环 (1 Hz)
    │  write(tags)
    ▼
┌────────────────────────────────────────────────┐
│  _buffer  (list, 最多 FLUSH_ROWS=10 行)         │  ← 待写磁盘
│  _ring    (deque, maxlen=IN_MEMORY_ROWS=3600)   │  ← 内存窗口
└───────────────┬────────────────────────────────┘
                │ 后台线程每 FLUSH_INTERVAL_S=5 秒
                │ 或 buffer 满 10 行时立即刷写
                ▼
        data/historian.csv  (append-only)
                ▲
        [重启后] _read_csv_tail()
                │
Dashboard / AI  ├─ load_recent(n_seconds)  → list[dict]
                └─ load_dataframe(n_seconds) → pd.DataFrame
```

---

#### 3.1 采样率策略（Sampling Rate Policy）

所有 tag 均以 `SAMPLE_RATE_S = 1.0 秒`（1 Hz）记录，原因：

1. **控制要求**：PLC 扫描周期为 1 秒，每个周期产生一组 tag
2. **故障检测**：`ALM_PUMP_FAIL` 需要 10 秒计数，必须有连续 1 Hz 数据
3. **存储开销**：每行 ~120 字节 × 3600 行/小时 ≈ 432 KB/小时 → 可接受
4. **趋势图**：1 秒分辨率足以展示液位填充/排放过程（完整周期约 15 分钟）

**未来升级方向**：
- 高频 tag（如流量脉冲计数）可以 100 ms 采样，仅存储变化时的记录（deadband recording）
- 低频 tag（如日常均值）可以 1 分钟降采样后写入 InfluxDB

---

#### 3.2 CSV 数据 Schema

```
timestamp, WT_T101_LEVEL_PV, WT_F101_INLET_FLOW_PV, WT_F102_OUTLET_FLOW_PV,
WT_A101_PH_PV, WT_P101_PUMP_FB, WT_V101_OUTLET_FB,
WT_P101_PUMP_CMD, WT_V101_OUTLET_CMD, WT_D101_DOSING_CMD,
WT_ALM_ACTIVE, plc_state
```

列顺序固定（由 `HISTORIAN_COLUMNS` 常量控制）。所有列名与 `tag_dictionary.md` 保持一致。

**示例行**：
```
2026-06-18 10:00:01,52.3,10.1,0.0,6.85,True,False,True,False,True,False,FILLING
```

---

#### 3.3 方法参考

##### `write(tags: dict)`
接受一个扫描周期的 tag 快照。

- **非阻塞**：将 row 追加到 `_buffer` 和 `_ring`，立即返回
- 自动添加 `timestamp`（如果 tags 中没有）
- 不在 HISTORIAN_COLUMNS 中的 key 被忽略；缺失的 column 写为空字符串
- 当 `_buffer` 达到 `FLUSH_ROWS=10` 行时，在调用线程内立即刷写（eager flush）

```python
historian.write({
    "WT_T101_LEVEL_PV": 52.3,
    "WT_A101_PH_PV": 6.85,
    "plc_state": "FILLING",
    # ... 其余 tag
})
```

---

##### `flush()`
强制将 `_buffer` 写入 CSV 文件。

- 在关闭系统前调用，确保最后几行数据不丢失
- 后台线程每 `FLUSH_INTERVAL_S=5 秒`自动调用
- 如果 CSV 文件写入失败（磁盘满、权限问题），rows 会放回 `_buffer` 不丢弃，并记录 ERROR 日志

---

##### `load_recent(n_seconds: int = 300) → list[dict]`
返回最近 n_seconds 行数据，按时间升序（oldest first）。

- **O(n)** 内存操作，无磁盘 I/O
- 如果 ring buffer 为空（程序刚重启），自动从 CSV 尾部读取（`_read_csv_tail`）
- `n_seconds` 超过 ring buffer 深度（3600）时，最多返回 3600 行

```python
rows = historian.load_recent(300)   # list of dicts
# 用法：dashboard 绘制 5 分钟趋势图
# 用法：AI assistant 分析最近 60 秒异常
```

---

##### `load_dataframe(n_seconds: int = 300) → pd.DataFrame`
与 `load_recent()` 相同，但返回 pandas DataFrame，并自动完成类型转换：

| 列 | 转换后类型 |
|----|-----------|
| `timestamp` | `datetime64[ns]` |
| `WT_T101_LEVEL_PV` 等模拟量 | `float64` |
| `WT_P101_PUMP_CMD` 等数字量 | `bool` |
| `plc_state` | `str` |

```python
df = historian.load_dataframe(300)
fig = px.line(df, x="timestamp", y="WT_T101_LEVEL_PV")
```

Raises `ImportError` if pandas not installed.

---

##### `stats() → dict`
返回 historian 运行状态，供仪表板健康面板展示：

```python
{
    "total_rows_written": 1234,     # 累计写入行数
    "rows_in_memory":     600,      # 当前 ring 深度
    "rows_in_buffer":     3,        # 待刷写缓冲行数
    "oldest_timestamp":  "2026-06-18 10:00:00",
    "newest_timestamp":  "2026-06-18 10:10:34",
    "csv_path":          "data/historian.csv",
    "csv_size_kb":       145.2,
}
```

---

##### `close()`
刷写剩余 buffer 并停止后台线程。与 context manager 配合使用时自动调用。

---

#### 3.4 重启恢复机制

当进程重启时，`_ring` 为空但 CSV 文件存在。`load_recent()` 自动调用 `_read_csv_tail(csv_path, n_rows)`，从 CSV 尾部读取最近 n 行数据，使 Dashboard 在重启后立即能显示历史趋势，而不需要等待 ring buffer 重新填满。

`_read_csv_tail` 使用 `deque(reader, maxlen=n_rows)` 实现，无论文件多大，内存占用始终为 O(n_rows)。

---

### 4. 测试覆盖（`tests/test_historian.py`）

| 测试用例 | 验证内容 |
|---------|---------|
| `test_csv_created_with_header` | 文件新建时第一行是正确的 header |
| `test_csv_header_not_duplicated_on_reopen` | 重新打开已有文件不会追加第二个 header |
| `test_write_and_load_recent` | 写入 10 行后可以全部读回 |
| `test_load_recent_respects_n_limit` | 写入 30 行，`load_recent(10)` 只返回 10 行 |
| `test_load_recent_returns_chronological_order` | 返回结果按写入顺序（时间升序）排列 |
| `test_missing_tag_written_as_empty` | 缺失 tag 写为空字符串，额外 tag 被忽略 |
| `test_rows_persisted_to_csv` | `close()` 后 CSV 文件包含所有 60 行数据 |
| `test_csv_tail_fallback_after_restart` | ring 为空时 `load_recent()` 从 CSV 尾部读取 |
| `test_ring_buffer_oldest_evicted` | 写入超过 3600 行后最旧的行被淘汰 |
| `test_stats_keys_present` | `stats()` 包含所有必要字段 |
| `test_csv_size_increases_after_flush` | flush 后文件大小增加 |
| `test_concurrent_writes_no_data_loss` | 10 线程各写 50 行，共 500 行无丢失无损坏 |
| `test_load_dataframe_types` | DataFrame 列类型正确（pandas 可用时） |
| `test_load_dataframe_empty_when_no_data` | 空 historian 返回空 DataFrame |
| `test_60_second_sample_run` | **验收标准**：60 步完整仿真，所有值在合理范围 |

**结果**：15 passed, 0 failed（pandas 未装时 2 个 DataFrame 测试 SKIP）

---

### 5. `main.py` 更新

移除了 Day 3 遗留的 lazy import 占位代码，替换为正式 historian 集成：

```python
from historian import Historian
# ...
historian = Historian()
# ...
historian.write(full_tags)   # 每个扫描周期调用
# ...
historian.close()   # Ctrl-C 退出时调用
```

Historian 实例在 `with` 块或 `close()` 时自动完成最后一次 flush，不丢失数据。

---

### 6. 验收标准检查

- [x] 每秒记录全部 process / sensor / command / alarm tag
- [x] 包含 timestamp 和采样率策略文档
- [x] `load_recent(n_seconds)` 供仪表板和 AI 助手使用
- [x] 长时间运行时 CSV append 稳定（测试 concurrent writes + recovery）
- [x] 60 秒数据可记录并验证（test_60_second_sample_run）
- [x] 线程安全（并发写入测试通过）
- [x] 重启恢复（CSV tail fallback 测试通过）

---

### 7. Viva 准备

**Q: 为什么不直接用 InfluxDB？**
> CSV 足够验证 time-series logging 概念，且无需额外进程依赖，演示更可靠。我们的接口设计（`write()` / `load_recent()`）是数据库无关的，升级到 InfluxDB 只需替换内部实现，不影响调用方。

**Q: ring buffer 和 CSV 有什么区别？**
> ring buffer 是内存中的滑动窗口，读取速度 O(1)，但进程重启后清空；CSV 是持久化存储，保留所有历史但读取速度取决于文件大小。两者互补。

**Q: 如何保证 Historian 不影响主循环的 1 秒定时？**
> `write()` 方法只做内存操作（list.append + deque.append），不做任何磁盘 I/O。I/O 全部在后台守护线程完成。即使磁盘 I/O 阻塞 5 秒，主循环也不会受影响。

**Q: 数据丢失了怎么办？**
> 后台线程写入失败时，rows 会放回 `_buffer`（不丢弃），并记录 ERROR 日志。下次刷写时重试。`close()` 时做最后一次 flush，确保进程结束前所有数据落盘。

**Q: historian.csv 的采样率是多少？为什么不用更高采样率？**
> 1 Hz，与 PLC 扫描周期一致。1 秒分辨率足以检测所有我们定义的故障（最快的是 pump fail，10 秒检测窗口）。更高采样率（如 100 ms）对 CSV 格式没有意义，且增加 10× 存储开销。
