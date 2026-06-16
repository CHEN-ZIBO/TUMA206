# sensor_simulator.py — converts process state into sensor readings with optional faults
#
# Sensor fault modes (selectable per tag):
#   normal  — value + small Gaussian noise
#   stuck   — reading freezes at last-good value
#   drift   — value drifts slowly away from true value
#   spike   — occasional random spike
#
# Run standalone:  python src/sensor_simulator.py
# Runs for 60 s, saves sample_run.csv, optionally publishes to MQTT.

import time
import random
import csv
import os
import sys

# Allow running from repo root or from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import config
from process_simulator import ProcessSimulator
from mqtt_client import MQTTClient

# ── Noise parameters ───────────────────────────────────────────────────────────
_NOISE_LEVEL  = 0.3    # % std-dev on level sensor
_NOISE_PH     = 0.02   # pH units std-dev
_NOISE_FLOW   = 0.2    # L/min std-dev

# ── Fault modes available ──────────────────────────────────────────────────────
FAULT_MODES = ("normal", "stuck", "drift", "spike")


class SensorSimulator:
    """
    Wraps a ProcessSimulator and adds sensor-layer behaviour:
    noise, drift, stuck and spike fault injection per tag.
    """

    def __init__(self, process: ProcessSimulator):
        self.process = process
        # Per-tag fault mode; default all normal
        self.fault_mode: dict[str, str] = {
            "WT_T101_LEVEL_PV":      "normal",
            "WT_F101_INLET_FLOW_PV": "normal",
            "WT_F102_OUTLET_FLOW_PV":"normal",
            "WT_A101_PH_PV":         "normal",
        }
        # State for stuck/drift faults
        self._stuck_values: dict[str, float] = {}
        self._drift_offset: dict[str, float] = {k: 0.0 for k in self.fault_mode}

    def set_fault(self, tag: str, mode: str):
        """Switch a tag into a fault mode. mode must be in FAULT_MODES."""
        if mode not in FAULT_MODES:
            raise ValueError(f"Unknown fault mode '{mode}'. Choose from {FAULT_MODES}")
        self.fault_mode[tag] = mode
        # Reset stuck value to current true reading when stuck mode starts
        if mode == "stuck":
            self._stuck_values[tag] = self._true_value(tag)
        if mode == "normal":
            self._drift_offset[tag] = 0.0

    def _true_value(self, tag: str) -> float:
        s = self.process.state
        mapping = {
            "WT_T101_LEVEL_PV":       s["level_pct"],
            "WT_F101_INLET_FLOW_PV":  s["inlet_flow"],
            "WT_F102_OUTLET_FLOW_PV": s["outlet_flow"],
            "WT_A101_PH_PV":          s["ph"],
        }
        return mapping[tag]

    def read_tag(self, tag: str) -> float:
        true_val = self._true_value(tag)
        mode = self.fault_mode[tag]

        if mode == "stuck":
            return self._stuck_values.get(tag, true_val)

        if mode == "drift":
            self._drift_offset[tag] += random.uniform(0.01, 0.03)
            return true_val + self._drift_offset[tag]

        if mode == "spike":
            if random.random() < 0.05:   # 5 % chance of spike each second
                return true_val + random.choice([-1, 1]) * random.uniform(2, 5)
            # fall through to normal noise

        # normal (+ spike non-spike case)
        noise_map = {
            "WT_T101_LEVEL_PV":       _NOISE_LEVEL,
            "WT_F101_INLET_FLOW_PV":  _NOISE_FLOW,
            "WT_F102_OUTLET_FLOW_PV": _NOISE_FLOW,
            "WT_A101_PH_PV":          _NOISE_PH,
        }
        return true_val + random.gauss(0, noise_map[tag])

    def read_all_tags(self) -> dict:
        """Return a dict of all sensor tag readings plus digital feedback tags."""
        s = self.process.state
        return {
            "WT_T101_LEVEL_PV":       round(max(0, self.read_tag("WT_T101_LEVEL_PV")), 2),
            "WT_F101_INLET_FLOW_PV":  round(max(0, self.read_tag("WT_F101_INLET_FLOW_PV")), 2),
            "WT_F102_OUTLET_FLOW_PV": round(max(0, self.read_tag("WT_F102_OUTLET_FLOW_PV")), 2),
            "WT_A101_PH_PV":          round(max(0, min(14, self.read_tag("WT_A101_PH_PV"))), 3),
            # Digital feedbacks mirror actuator states
            "WT_P101_PUMP_FB":        s["pump_on"],
            "WT_V101_OUTLET_FB":      s["valve_open"],
            "WT_D101_DOSING_CMD":     s["dosing_on"],
            "WT_ALM_ACTIVE":          False,   # updated by PLC / main loop
        }


# ── Standalone runner ──────────────────────────────────────────────────────────

def run_standalone(duration_s: int = 60):
    """
    Run sensor simulation standalone for duration_s seconds.
    Prints each reading and saves to sample_run.csv.
    """
    process = ProcessSimulator()
    sensors = SensorSimulator(process)
    mqtt    = MQTTClient()   # will silently skip publish if broker unavailable

    # Default: pump on, valve closed — let level fill from ~50 %
    process.apply_commands(pump_on=True, valve_open=False, dosing_on=False)

    # Ensure data/ directory exists relative to repo root
    csv_path = os.path.join(os.path.dirname(__file__), "..", config.SAMPLE_RUN_CSV)
    csv_path = os.path.normpath(csv_path)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    fieldnames = [
        "timestamp",
        "WT_T101_LEVEL_PV", "WT_F101_INLET_FLOW_PV", "WT_F102_OUTLET_FLOW_PV",
        "WT_A101_PH_PV", "WT_P101_PUMP_FB", "WT_V101_OUTLET_FB",
        "WT_D101_DOSING_CMD", "WT_ALM_ACTIVE",
    ]

    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

        for _ in range(duration_s):
            process.step(config.SAMPLE_RATE_S)
            tags = sensors.read_all_tags()
            ts   = time.strftime("%Y-%m-%d %H:%M:%S")

            # Console output
            print(
                f"{ts} | level={tags['WT_T101_LEVEL_PV']:5.1f}% "
                f"| pH={tags['WT_A101_PH_PV']:.3f} "
                f"| inlet={tags['WT_F101_INLET_FLOW_PV']:.1f} L/min "
                f"| outlet={tags['WT_F102_OUTLET_FLOW_PV']:.1f} L/min"
            )

            # Write CSV row
            writer.writerow({"timestamp": ts, **tags})

            # Publish to MQTT (non-blocking, skips if broker absent)
            mqtt.publish_tags(tags)

            time.sleep(config.SAMPLE_RATE_S)

    print(f"\nSaved {duration_s} seconds of data to {csv_path}")
    mqtt.disconnect()


if __name__ == "__main__":
    run_standalone(duration_s=60)
