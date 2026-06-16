# config.py — central configuration constants for the Water Treatment Digital Twin

# ── MQTT ──────────────────────────────────────────────────────────────────────
MQTT_BROKER_HOST = "localhost"
MQTT_BROKER_PORT = 1883
MQTT_TOPIC_TAGS     = "water_treatment/tags"
MQTT_TOPIC_ALARMS   = "water_treatment/alarms"
MQTT_TOPIC_FAULTS   = "water_treatment/faults"
MQTT_TOPIC_COMMANDS = "water_treatment/commands"

# Seconds the broker must be unreachable before infrastructure fault alarm
MQTT_INFRA_FAULT_TIMEOUT_S = 30

# Maximum messages held in publish queue while broker is down
MQTT_SEND_QUEUE_MAX = 200

# ── Sampling ───────────────────────────────────────────────────────────────────
SAMPLE_RATE_S = 1.0          # seconds per scan cycle

# ── Tank / process initial conditions ─────────────────────────────────────────
TANK_CAPACITY_L   = 1000.0   # litres — treatment tank T-101
INITIAL_LEVEL_PCT = 50.0     # % fill at startup

INLET_FLOW_NOMINAL_LPM  = 10.0  # L/min when pump P-101 is ON
OUTLET_FLOW_NOMINAL_LPM = 12.0  # L/min when valve V-101 is OPEN
DOSING_FLOW_LPM         = 0.5   # L/min chemical addition via DP-101

# ── pH ─────────────────────────────────────────────────────────────────────────
PH_SETPOINT      = 7.0
PH_INITIAL       = 7.0
PH_DISTURBANCE   = 0.03   # pH units per second disturbance when inlet runs
PH_DOSING_EFFECT = 0.05   # pH correction per second when dosing pump ON

# ── PLC control thresholds ─────────────────────────────────────────────────────
LEVEL_LOW_PCT      = 40.0   # start inlet pump below this
LEVEL_HIGH_PCT     = 80.0   # stop pump + open outlet valve above this
LEVEL_CRITICAL_PCT = 90.0   # process alarm

PH_DOSE_ON   = 6.8   # start dosing below this
PH_DOSE_OFF  = 7.3   # stop dosing above this
PH_LOW_ALARM = 6.0
PH_HIGH_ALARM = 8.5

# Pump command-vs-feedback mismatch timeout (seconds)
PUMP_FAULT_TIMEOUT_S = 10

# ── Historian ──────────────────────────────────────────────────────────────────
HISTORIAN_CSV = "data/historian.csv"
SAMPLE_RUN_CSV = "data/sample_run.csv"
