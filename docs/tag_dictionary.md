# Tag Dictionary — Water Treatment Plant Digital Twin
# Generated: 2026-06-16 | Day 2

## Tag Namespace Convention

All tags follow the pattern: `{Plant}_{Equipment}_{Measurement}_{Type}`

| Prefix | Meaning |
|--------|---------|
| `WT`   | Water Treatment plant |
| `T101` | Treatment Tank 101 |
| `F101` | Flow instrument 101 (inlet) |
| `F102` | Flow instrument 102 (outlet) |
| `A101` | Analyser 101 (pH) |
| `P101` | Pump 101 (inlet) |
| `V101` | Valve 101 (outlet) |
| `D101` | Dosing pump 101 |
| `ALM`  | Alarm / status |

## Tag Definitions

| Tag | Description | Unit | Type | Sample Rate | Range | Notes |
|-----|-------------|------|------|-------------|-------|-------|
| `WT_T101_LEVEL_PV` | Treatment tank T-101 level — process value | % | Analog input (AI) | 1 s | 0–100 | 0 = empty, 100 = full |
| `WT_F101_INLET_FLOW_PV` | Inlet flow process value (from Raw Water Tank via P-101) | L/min | Analog input (AI) | 1 s | 0–20 | Near 0 when pump OFF |
| `WT_F102_OUTLET_FLOW_PV` | Outlet flow process value (to Clean Water Tank via V-101) | L/min | Analog input (AI) | 1 s | 0–20 | Near 0 when valve CLOSED |
| `WT_A101_PH_PV` | pH analyser process value inside T-101 | pH | Analog input (AI) | 1 s | 0–14 | Setpoint 7.0; alarm <6.0 or >8.5 |
| `WT_P101_PUMP_CMD` | Inlet pump P-101 command from PLC | ON/OFF | Digital output (DO) | 1 s | — | ON = run pump |
| `WT_P101_PUMP_FB` | Inlet pump P-101 feedback (motor running confirmation) | ON/OFF | Digital input (DI) | 1 s | — | Mismatch with CMD → equipment fault |
| `WT_V101_OUTLET_CMD` | Outlet valve V-101 command from PLC | OPEN/CLOSE | Digital output (DO) | 1 s | — | OPEN = drain tank |
| `WT_V101_OUTLET_FB` | Outlet valve V-101 position feedback | OPEN/CLOSE | Digital input (DI) | 1 s | — | Mismatch with CMD → equipment fault |
| `WT_D101_DOSING_CMD` | Chemical dosing pump DP-101 command | ON/OFF | Digital output (DO) | 1 s | — | ON = raise pH |
| `WT_ALM_ACTIVE` | Any active alarm flag | TRUE/FALSE | Alarm status | 1 s | — | TRUE = at least one alarm active |

## MQTT Payload Format

All tags are published as a single JSON object per scan cycle:

```json
{
  "timestamp": "2026-06-16 10:00:01",
  "WT_T101_LEVEL_PV": 50.3,
  "WT_F101_INLET_FLOW_PV": 10.1,
  "WT_F102_OUTLET_FLOW_PV": 0.0,
  "WT_A101_PH_PV": 7.021,
  "WT_P101_PUMP_FB": true,
  "WT_V101_OUTLET_FB": false,
  "WT_D101_DOSING_CMD": false,
  "WT_ALM_ACTIVE": false
}
```

**Topic**: `water_treatment/tags`

## Process Flow Summary

```
Raw Water Tank
    ↓  P-101 (inlet pump)        — cmd: WT_P101_PUMP_CMD
Treatment Tank T-101             — level: WT_T101_LEVEL_PV
    |  DP-101 (dosing pump)      — cmd: WT_D101_DOSING_CMD
    |  pH Adjustment Loop        — measured: WT_A101_PH_PV
    ↓  V-101 (outlet valve)      — cmd: WT_V101_OUTLET_CMD
Clean Water Tank
```

## Sensor Fault Modes (Day 2+)

| Mode | Behaviour | Detection |
|------|-----------|-----------|
| `normal` | True value + Gaussian noise | — |
| `stuck` | Reading frozen at last-good value | pH/level unchanged while other signals change |
| `drift` | Slow monotonic offset grows over time | Material balance cross-check |
| `spike` | Occasional large transient | Statistical outlier detection |
