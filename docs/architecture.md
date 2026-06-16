# Architecture — Water Treatment Plant Digital Twin
# TUMA206 Modern Developments in Industry | 2026

## 1. System Overview

This digital twin replicates the full industrial stack of a water treatment plant
on a single laptop.  Every layer — from physical process to AI assistant — runs
as a local Python process communicating over standard industrial protocols.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         ISA-95 / Purdue Reference Model                      │
│                                                                              │
│  Level 3/4  ┌─────────────────────────────────────────────────────────────┐ │
│  (MES/ERP)  │  AI Assistant + Operator Dashboard   (dashboard.py)         │ │
│             │  Streamlit web UI  •  localhost:8501                         │ │
│             └──────────────────────────┬────────────────────────────────── │
│                                        │  load_recent() / load_dataframe() │
│  Level 2    ┌──────────────────────────▼────────────────────────────────── │
│  (SCADA/    │  Historian   (historian.py)                                   │ │
│  Historian) │  data/historian.csv  •  ring buffer (3600 rows in memory)    │ │
│             └──────────────────────────┬────────────────────────────────── │
│                                        │  subscribe water_treatment/#      │
│  Level 2    ┌──────────────────────────▼────────────────────────────────── │
│  (SCADA/    │  MQTT Broker   (Mosquitto, localhost:1883)                    │ │
│  HMI)       │  Topics: /tags  /alarms  /faults  /commands                  │ │
│             └──────────────────────────▲────────────────────────────────── │
│                                        │  publish (JSON, 1 Hz)             │
│  Level 1    ┌──────────────────────────┴────────────────────────────────── │
│  (Control)  │  Simulated PLC   (plc_controller.py)                         │ │
│             │  Scan cycle 1 s  •  state machine  •  on/off + pseudo-PID   │ │
│             └──────────────────────────┬────────────────────────────────── │
│                                        │  read_all_tags()                  │
│  Level 0    ┌──────────────────────────▼────────────────────────────────── │
│  (Field)    │  Sensor Simulator   (sensor_simulator.py)                     │ │
│             │  noise / stuck / drift / spike fault modes                    │ │
│             └──────────────────────────┬────────────────────────────────── │
│                                        │  step() / state                   │
│  Level 0    ┌──────────────────────────▼────────────────────────────────── │
│  (Process)  │  Process Simulator  (process_simulator.py)                   │ │
│             │  T-101 tank  •  P-101 pump  •  V-101 valve  •  DP-101 dosing │ │
│             └────────────────────────────────────────────────────────────── │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 2. ISA-95 / Purdue Layer Mapping

| Purdue Level | ISA-95 Role | This Digital Twin |
|:---:|---|---|
| 0 | Physical process | `process_simulator.py` — tank, pumps, valves, pH physics |
| 0 | Field instruments | `sensor_simulator.py` — level / pH / flow sensors with noise & faults |
| 1 | Basic control | `plc_controller.py` — deterministic scan cycle, on/off, pseudo-PID |
| 2 | Supervisory control | `mqtt_client.py` + Mosquitto — tag transport between field and SCADA |
| 2 | Historian / SCADA | `historian.py` — time-series logging; `dashboard.py` — operator HMI |
| 3/4 | MES / Analytics | `ai_assistant.py` — fault diagnosis and operator recommendations |

**OT/IT boundary**: The MQTT broker sits at the Level 1/2 boundary.  Everything
below (process + sensors + PLC) is Operational Technology (OT); everything above
(historian, dashboard, AI) is Information Technology (IT).

## 3. Runtime Topology — Three Terminals

```
Terminal 1                Terminal 2                    Terminal 3
──────────────────        ──────────────────────────    ─────────────────────
mosquitto -v              python src/main.py             streamlit run
                                                         src/dashboard.py
Mosquitto MQTT broker     ┌─ process_simulator           Browser → localhost:8501
localhost:1883            ├─ sensor_simulator
                          ├─ plc_controller        publish ──► broker
                          ├─ mqtt_client    ────────────────────────►
                          └─ historian      ◄──────────────────────── subscribe
                                                         dashboard reads from
                                                         historian (direct CSV/
                                                         ring buffer, no broker)
```

The dashboard reads from the historian's in-memory ring buffer directly —
it does **not** subscribe to MQTT.  This means the dashboard continues to show
historical data even if the MQTT broker is temporarily down.

## 4. MQTT Topic Design

### 4.1 Topic Namespace

```
water_treatment/
├── tags         ← all process + sensor + command tags, 1 Hz, JSON
├── alarms       ← active alarm events, published only when ALM_ACTIVE = True
├── faults       ← fault injection events (from fault_injector.py)
└── commands     ← reserved for external operator commands (future use)
```

Namespace rule: `{plant}/{message_type}`

- `water_treatment` identifies the plant — allows multiple plants on one broker
- Second level is message **type**, not tag name — keeps subscriber logic simple
- All tag values travel together in one JSON object per scan cycle on `/tags`

### 4.2 Payload Format

#### `/tags` — published every scan cycle

```json
{
  "seq": 1234,
  "payload": {
    "timestamp":               "2026-06-19 10:00:01",
    "WT_T101_LEVEL_PV":        52.3,
    "WT_F101_INLET_FLOW_PV":   10.1,
    "WT_F102_OUTLET_FLOW_PV":  0.0,
    "WT_A101_PH_PV":           6.85,
    "WT_P101_PUMP_FB":         true,
    "WT_V101_OUTLET_FB":       false,
    "WT_P101_PUMP_CMD":        true,
    "WT_V101_OUTLET_CMD":      false,
    "WT_D101_DOSING_CMD":      true,
    "WT_ALM_ACTIVE":           false,
    "plc_state":               "TREATING"
  }
}
```

`seq` is a monotonically increasing integer.  Consumers can detect gaps
(missed messages) by checking for non-consecutive sequence numbers.

#### `/alarms` — published when `WT_ALM_ACTIVE = True`

```json
{
  "seq": 1235,
  "payload": {
    "alarms": {
      "ALM_LEVEL_HIGH":  false,
      "ALM_LEVEL_CRIT":  false,
      "ALM_PH_LOW":      true,
      "ALM_PUMP_FAIL":   false
    },
    "plc_state": "FAULT"
  }
}
```

#### `/faults` — published by fault_injector.py

```json
{
  "seq": 1,
  "payload": {
    "fault_type":  "sensor",
    "tag":         "WT_A101_PH_PV",
    "mode":        "stuck",
    "injected_at": "2026-06-19 10:01:00",
    "active":      true
  }
}
```

### 4.3 QoS Policy

| Topic | QoS | Reason |
|-------|:---:|--------|
| `water_treatment/tags` | 0 | High-frequency; occasional loss acceptable; latest reading matters more than delivery guarantee |
| `water_treatment/alarms` | 1 | Operator must see alarms; at-least-once delivery required |
| `water_treatment/faults` | 1 | Audit trail of fault injections; at-least-once delivery required |
| `water_treatment/commands` | 1 | Control commands must not be silently lost |

## 5. MQTT Client Design (mqtt_client.py)

### 5.1 Connection State Machine

```
            ┌──────────────┐
  start ──► │  CONNECTING  │
            └──────┬───────┘
                   │ on_connect(rc=0)
                   ▼
            ┌──────────────┐       on_disconnect (unexpected)
            │  CONNECTED   │ ──────────────────────────────────►
            └──────────────┘                                    │
                                                                ▼
            ┌──────────────┐   back-off delay elapsed    ┌─────────────┐
            │  RECONNECTING│ ◄───────────────────────────│ DISCONNECTED│
            └──────┬───────┘                             └─────────────┘
                   │ on_connect(rc=0)
                   ▼
            ┌──────────────┐
            │  CONNECTED   │  (re-subscribes all topics on reconnect)
            └──────────────┘
```

### 5.2 Non-Blocking Publish Queue

```
main loop (1 Hz)                 sender thread (daemon)
────────────────                 ──────────────────────
publish(topic, payload)          while not shutdown:
  → _send_queue.put_nowait()       envelope = queue.get(timeout=1)
  returns immediately              if connected:
                                     client.publish(...)
                                   else:
                                     queue.put(envelope)  # hold until reconnect
```

If the queue reaches 200 messages (broker down > 3 min), oldest messages are
dropped and `_dropped` counter increments.  This is reported via `stats()` and
visible in the dashboard health panel.

### 5.3 Infrastructure Fault Detection

```python
@property
def infra_alarm(self) -> bool:
    if connected:
        return False
    elapsed = now - disconnect_since
    return elapsed >= INFRA_FAULT_TIMEOUT_S   # 30 s
```

When `infra_alarm` is True, `main.py` publishes `WT_ALM_INFRA_STALE = True`
to the full tags dict.  The AI assistant distinguishes this from a process
alarm: "Communication layer disruption — verify MQTT broker is running.
PLC continues local safe-mode control."

## 6. Edge vs Cloud Decision

| Factor | Edge (local laptop) | Cloud |
|--------|--------------------|----|
| **Latency** | < 1 ms | 10–500 ms |
| **Availability** | Works offline | Internet required |
| **Demo reliability** | High | Depends on network |
| **Cost** | Free | Metered API / storage |
| **Security** | No exposure | Firewall / auth needed |
| **Scale** | Single plant | Multi-plant possible |

**Decision: Edge-only for this project.**

Rationale: 1-second PLC control loops require deterministic latency that cloud
round-trips cannot guarantee.  The demo must work in a classroom with unreliable
Wi-Fi.  The historian stores all data locally; the AI assistant uses rule-based
logic that requires no cloud API call.

Future upgrade path: publish a 1-minute downsampled summary to a free-tier cloud
MQTT broker (e.g., HiveMQ Cloud) for remote monitoring — without changing any
local control logic.

## 7. Data Flow Diagram (End-to-End)

```
Physical Process
  T-101 level, pH, flow
        │
        │  step(dt=1s)
        ▼
Sensor Simulator                    ← fault injection (stuck/drift/spike)
  WT_T101_LEVEL_PV = 52.3 %
  WT_A101_PH_PV    = 6.85 pH
  WT_F101_INLET_FLOW_PV = 10.1 L/min
        │
        │  read_all_tags() → dict
        ▼
PLC Controller
  state = TREATING
  pump_cmd = True
  dosing_cmd = True
  ALM_ACTIVE = False
        │
        │  commands dict
        ▼
main.py — assembles full_tags dict
        │
        ├──── mqtt.publish_tags(full_tags)
        │           │
        │           ▼  water_treatment/tags  (JSON, QoS 0)
        │     Mosquitto broker
        │           │
        │           └──► subscribers (future: cloud bridge, SCADA HMI)
        │
        └──── historian.write(full_tags)
                    │
                    ├──► _ring  (deque, last 3600 rows, in memory)
                    └──► data/historian.csv  (append, background flush)
                                │
                    dashboard.load_recent(300) → pd.DataFrame
                                │
                    Streamlit trend charts / alarm panel / AI panel
```

## 8. Technology Stack Justification

| Component | Choice | Why not the alternative |
|-----------|--------|------------------------|
| Language | Python 3.10+ | Simulation + control + data + AI in one language; readable for viva |
| MQTT broker | Mosquitto | Lightweight, zero-config, no container needed; standard in IIoT |
| MQTT client | paho-mqtt | De facto Python MQTT library; mirrors OT client behavior |
| Dashboard | Streamlit | No frontend code; runs in browser; real-time widgets built-in |
| Historian | CSV + deque | Zero dependencies; auditable; shows the concept without DB overhead |
| AI assistant | Rule-based + optional LLM | Demo works fully offline; LLM is opt-in upgrade, not a dependency |

## 9. Module Dependency Graph

```
config.py ◄──── (imported by all modules)

process_simulator.py
    └── config

sensor_simulator.py
    ├── config
    └── process_simulator

plc_controller.py
    └── config

mqtt_client.py
    ├── config
    └── paho-mqtt  (optional, graceful degradation)

historian.py
    ├── config
    └── pandas  (optional, for load_dataframe())

main.py
    ├── config
    ├── process_simulator
    ├── sensor_simulator
    ├── plc_controller
    ├── mqtt_client
    └── historian

dashboard.py  (Day 6)
    ├── config
    ├── historian
    ├── mqtt_client
    ├── fault_injector
    └── ai_assistant

ai_assistant.py  (Day 7)
    ├── config
    └── historian
```

No circular imports.  `config.py` is the only shared global state.
