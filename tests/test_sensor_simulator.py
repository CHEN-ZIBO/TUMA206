# tests/test_sensor_simulator.py — standalone tests for SensorSimulator
#
# Run:  python tests/test_sensor_simulator.py
# Saves chart to tests/charts/day2_sensor_simulator.png

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import config
from process_simulator import ProcessSimulator
from sensor_simulator import SensorSimulator

PH_TAG    = "WT_A101_PH_PV"
LEVEL_TAG = "WT_T101_LEVEL_PV"


# ── Individual tests ──────────────────────────────────────────────────────────

def test_normal_reading_close_to_true():
    """50 readings in normal mode: mean abs error pH < 0.1, level < 1.0."""
    proc = ProcessSimulator()
    proc.apply_commands(pump_on=False, valve_open=False, dosing_on=False)
    sens = SensorSimulator(proc)
    ph_errors = []
    level_errors = []
    for _ in range(50):
        ph_errors.append(abs(sens.read_tag(PH_TAG) - proc.ph))
        level_errors.append(abs(sens.read_tag(LEVEL_TAG) - proc.level_pct))
    mean_ph    = sum(ph_errors) / len(ph_errors)
    mean_level = sum(level_errors) / len(level_errors)
    if mean_ph >= 0.1:
        return False, f"mean pH error too high: {mean_ph:.4f} (threshold 0.1)"
    if mean_level >= 1.0:
        return False, f"mean level error too high: {mean_level:.4f} (threshold 1.0)"
    return True, ""


def test_stuck_reading_frozen():
    """Set stuck on pH tag, do 20 steps: all readings must be identical."""
    proc = ProcessSimulator()
    proc.apply_commands(pump_on=True, valve_open=False, dosing_on=False)
    sens = SensorSimulator(proc)
    sens.set_fault(PH_TAG, "stuck")
    readings = []
    for _ in range(20):
        proc.step(1.0)
        readings.append(sens.read_tag(PH_TAG))
    if len(set(readings)) != 1:
        return False, f"stuck readings not all identical; unique values: {set(readings)}"
    return True, ""


def test_drift_reading_increases():
    """Set drift on pH, do 30 steps: sensor value must exceed true value."""
    proc = ProcessSimulator()
    proc.apply_commands(pump_on=False, valve_open=False, dosing_on=False)
    sens = SensorSimulator(proc)
    sens.set_fault(PH_TAG, "drift")
    for _ in range(30):
        proc.step(1.0)
        sens.read_tag(PH_TAG)   # advance drift accumulator
    sensor_val = sens.read_tag(PH_TAG)
    true_val   = proc.ph
    if sensor_val <= true_val:
        return False, f"drift sensor ({sensor_val:.4f}) not above true ({true_val:.4f})"
    return True, ""


def test_spike_mode_accepted():
    """Set spike mode, do 100 steps: no exception and all values are numeric."""
    proc = ProcessSimulator()
    proc.apply_commands(pump_on=True, valve_open=False, dosing_on=False)
    sens = SensorSimulator(proc)
    sens.set_fault(PH_TAG, "spike")
    for _ in range(100):
        proc.step(1.0)
        val = sens.read_tag(PH_TAG)
        if not isinstance(val, (int, float)):
            return False, f"non-numeric value returned: {val!r}"
    return True, ""


def test_reset_to_normal_clears_drift():
    """Set drift 10 steps, reset to normal: drift_offset for tag must be 0."""
    proc = ProcessSimulator()
    proc.apply_commands(pump_on=False, valve_open=False, dosing_on=False)
    sens = SensorSimulator(proc)
    sens.set_fault(PH_TAG, "drift")
    for _ in range(10):
        proc.step(1.0)
        sens.read_tag(PH_TAG)
    sens.set_fault(PH_TAG, "normal")
    offset = sens._drift_offset[PH_TAG]
    if offset != 0.0:
        return False, f"drift_offset not reset to 0 after normal: got {offset}"
    return True, ""


def test_invalid_fault_mode_raises():
    """set_fault with unknown mode must raise ValueError."""
    proc = ProcessSimulator()
    sens = SensorSimulator(proc)
    try:
        sens.set_fault(PH_TAG, "explode")
        return False, "ValueError was not raised for unknown fault mode"
    except ValueError:
        return True, ""


def test_read_all_tags_keys():
    """read_all_tags() must contain all 8 expected keys."""
    proc = ProcessSimulator()
    sens = SensorSimulator(proc)
    tags = sens.read_all_tags()
    expected = {
        "WT_T101_LEVEL_PV", "WT_F101_INLET_FLOW_PV", "WT_F102_OUTLET_FLOW_PV",
        "WT_A101_PH_PV", "WT_P101_PUMP_FB", "WT_V101_OUTLET_FB",
        "WT_D101_DOSING_CMD", "WT_ALM_ACTIVE",
    }
    missing = expected - set(tags.keys())
    if missing:
        return False, f"missing keys in read_all_tags(): {missing}"
    return True, ""


def test_values_clamped_in_range():
    """100 steps across all fault modes: level in [0,100], pH in [0,14], flows >= 0."""
    for mode in ("normal", "stuck", "drift", "spike"):
        proc = ProcessSimulator()
        proc.apply_commands(pump_on=True, valve_open=False, dosing_on=False)
        sens = SensorSimulator(proc)
        sens.set_fault(PH_TAG, mode)
        for i in range(100):
            proc.step(1.0)
            t = sens.read_all_tags()
            if not (0 <= t["WT_T101_LEVEL_PV"] <= 100):
                return False, f"[{mode}] level out of [0,100] at step {i+1}: {t['WT_T101_LEVEL_PV']}"
            if not (0 <= t["WT_A101_PH_PV"] <= 14):
                return False, f"[{mode}] pH out of [0,14] at step {i+1}: {t['WT_A101_PH_PV']}"
            if t["WT_F101_INLET_FLOW_PV"] < 0:
                return False, f"[{mode}] inlet flow negative at step {i+1}"
            if t["WT_F102_OUTLET_FLOW_PV"] < 0:
                return False, f"[{mode}] outlet flow negative at step {i+1}"
    return True, ""


# ── Chart data collection ─────────────────────────────────────────────────────

def collect_chart_data():
    STEPS = 60
    modes = ["normal", "stuck", "drift", "spike"]
    true_ph_series = []
    sensor_ph = {}

    for mode in modes:
        proc = ProcessSimulator()
        proc.apply_commands(pump_on=True, valve_open=False, dosing_on=False)
        sens = SensorSimulator(proc)
        sens.set_fault(PH_TAG, mode)
        true_vals   = []
        sensor_vals = []
        for _ in range(STEPS):
            proc.step(1.0)
            true_vals.append(proc.ph)
            sensor_vals.append(sens.read_tag(PH_TAG))
        sensor_ph[mode] = sensor_vals
        if mode == "normal":
            true_ph_series = true_vals

    # subplot 2 — level
    proc2 = ProcessSimulator()
    proc2.apply_commands(pump_on=True, valve_open=False, dosing_on=False)
    sens2 = SensorSimulator(proc2)
    true_levels   = []
    sensor_levels = []
    for _ in range(STEPS):
        proc2.step(1.0)
        true_levels.append(proc2.level_pct)
        sensor_levels.append(sens2.read_tag(LEVEL_TAG))

    return true_ph_series, sensor_ph, true_levels, sensor_levels


def save_chart(true_ph_series, sensor_ph, true_levels, sensor_levels):
    charts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'charts')
    os.makedirs(charts_dir, exist_ok=True)
    out_path = os.path.join(charts_dir, 'day2_sensor_simulator.png')

    steps = list(range(1, 61))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))

    # Subplot 1 — pH fault mode comparison
    ax1.plot(steps, true_ph_series, color='green', linewidth=2.0,
             label='True pH', zorder=5)
    ax1.plot(steps, sensor_ph["normal"], 'o', color='blue', markersize=3,
             alpha=0.6, label='Normal')
    ax1.plot(steps, sensor_ph["stuck"], color='red', linestyle='--', linewidth=1.5,
             label='Stuck')
    ax1.plot(steps, sensor_ph["drift"], color='orange', linewidth=1.5,
             label='Drift')
    ax1.plot(steps, sensor_ph["spike"], 'o', color='purple', markersize=3,
             alpha=0.5, label='Spike')
    ax1.set_title('Day 2 – Sensor Simulator: Fault Modes Comparison')
    ax1.set_ylabel('pH')
    ax1.legend(loc='best', fontsize=9)
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(1, 60)

    # Subplot 2 — Level: true vs normal sensor
    ax2.plot(steps, true_levels, color='blue', linewidth=2.0,
             label='True Level (%)', zorder=5)
    ax2.plot(steps, sensor_levels, 'o', color='grey', markersize=3,
             alpha=0.6, label='Sensor Level (normal)')
    ax2.set_xlabel('Simulation step (s)')
    ax2.set_ylabel('Tank Level (%)')
    ax2.legend(loc='best', fontsize=9)
    ax2.grid(True, alpha=0.3)
    ax2.set_xlim(1, 60)

    plt.tight_layout()
    plt.savefig(out_path, dpi=100)
    plt.close()
    print(f"\nChart saved to: {out_path}")


# ── Test registry and runner ──────────────────────────────────────────────────

TESTS = [
    ("test_normal_reading_close_to_true",   test_normal_reading_close_to_true),
    ("test_stuck_reading_frozen",            test_stuck_reading_frozen),
    ("test_drift_reading_increases",         test_drift_reading_increases),
    ("test_spike_mode_accepted",             test_spike_mode_accepted),
    ("test_reset_to_normal_clears_drift",    test_reset_to_normal_clears_drift),
    ("test_invalid_fault_mode_raises",       test_invalid_fault_mode_raises),
    ("test_read_all_tags_keys",              test_read_all_tags_keys),
    ("test_values_clamped_in_range",         test_values_clamped_in_range),
]


if __name__ == "__main__":
    passed = 0
    failed = 0
    for name, fn in TESTS:
        try:
            ok, reason = fn()
            if ok:
                print(f"  PASS  {name}")
                passed += 1
            else:
                print(f"  FAIL  {name}: {reason}")
                failed += 1
        except Exception as exc:
            print(f"  FAIL  {name}: {exc}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")

    true_ph, sensor_ph, true_levels, sensor_levels = collect_chart_data()
    save_chart(true_ph, sensor_ph, true_levels, sensor_levels)

