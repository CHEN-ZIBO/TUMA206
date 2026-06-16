# tests/test_process_simulator.py — standalone tests for ProcessSimulator
#
# Run:  python tests/test_process_simulator.py
# Saves chart to tests/charts/day2_process_simulator.png

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import config
from process_simulator import ProcessSimulator


# ── Individual tests ──────────────────────────────────────────────────────────

def test_initial_conditions():
    """Level starts at INITIAL_LEVEL_PCT and pH starts at PH_INITIAL."""
    sim = ProcessSimulator()
    if sim.level_pct != config.INITIAL_LEVEL_PCT:
        return False, f"level_pct={sim.level_pct}, expected {config.INITIAL_LEVEL_PCT}"
    if sim.ph != config.PH_INITIAL:
        return False, f"ph={sim.ph}, expected {config.PH_INITIAL}"
    return True, ""


def test_pump_on_raises_level():
    """60 steps with pump ON, valve/dosing OFF: level must increase."""
    sim = ProcessSimulator()
    initial = sim.level_pct
    sim.apply_commands(pump_on=True, valve_open=False, dosing_on=False)
    for _ in range(60):
        sim.step(1.0)
    if sim.level_pct <= initial:
        return False, f"level did not increase: {initial:.2f} -> {sim.level_pct:.2f}"
    return True, ""


def test_valve_open_lowers_level():
    """Start at 70%, valve OPEN, pump OFF: level must decrease."""
    sim = ProcessSimulator()
    sim.level_pct = 70.0
    sim.apply_commands(pump_on=False, valve_open=True, dosing_on=False)
    for _ in range(30):
        sim.step(1.0)
    if sim.level_pct >= 70.0:
        return False, f"level did not decrease from 70: ended at {sim.level_pct:.2f}"
    return True, ""


def test_dosing_raises_ph():
    """Pump OFF, dosing ON for 20 steps: pH must increase."""
    sim = ProcessSimulator()
    initial_ph = sim.ph
    sim.apply_commands(pump_on=False, valve_open=False, dosing_on=True)
    for _ in range(20):
        sim.step(1.0)
    if sim.ph <= initial_ph:
        return False, f"pH did not increase: {initial_ph:.3f} -> {sim.ph:.3f}"
    return True, ""


def test_pump_disturbs_ph():
    """Pump ON, dosing OFF for 20 steps: pH must decrease."""
    sim = ProcessSimulator()
    initial_ph = sim.ph
    sim.apply_commands(pump_on=True, valve_open=False, dosing_on=False)
    for _ in range(20):
        sim.step(1.0)
    if sim.ph >= initial_ph:
        return False, f"pH did not decrease: {initial_ph:.3f} -> {sim.ph:.3f}"
    return True, ""


def test_level_clamps_at_100():
    """Pump ON always for 2000 steps: level must never exceed 100."""
    sim = ProcessSimulator()
    sim.apply_commands(pump_on=True, valve_open=False, dosing_on=False)
    for i in range(2000):
        sim.step(1.0)
        if sim.level_pct > 100.0:
            return False, f"level exceeded 100 at step {i + 1}: {sim.level_pct:.4f}"
    return True, ""


def test_level_clamps_at_zero():
    """Valve OPEN always, start at 10%, 200 steps: level must never go below 0."""
    sim = ProcessSimulator()
    sim.level_pct = 10.0
    sim.apply_commands(pump_on=False, valve_open=True, dosing_on=False)
    for i in range(200):
        sim.step(1.0)
        if sim.level_pct < 0.0:
            return False, f"level went below 0 at step {i + 1}: {sim.level_pct:.4f}"
    return True, ""


def test_ph_clamps_in_range():
    """Extreme dosing for 1000 steps: pH must stay in [4.0, 10.0]."""
    sim = ProcessSimulator()
    sim.apply_commands(pump_on=False, valve_open=False, dosing_on=True)
    for i in range(1000):
        sim.step(1.0)
        if sim.ph < 4.0 or sim.ph > 10.0:
            return False, f"pH out of [4, 10] at step {i + 1}: {sim.ph:.4f}"
    return True, ""


# ── Chart data collection ─────────────────────────────────────────────────────

def collect_chart_data():
    """200-step run: pump ON, valve OFF, dosing OFF, starting from 50%."""
    sim = ProcessSimulator()
    sim.level_pct = 50.0
    sim.apply_commands(pump_on=True, valve_open=False, dosing_on=False)
    levels = []
    phs = []
    for _ in range(200):
        sim.step(1.0)
        levels.append(sim.level_pct)
        phs.append(sim.ph)
    return levels, phs


def save_chart(levels, phs):
    charts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'charts')
    os.makedirs(charts_dir, exist_ok=True)
    out_path = os.path.join(charts_dir, 'day2_process_simulator.png')

    steps = list(range(1, len(levels) + 1))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))

    # Subplot 1 — Level
    ax1.plot(steps, levels, color='blue', linewidth=1.5, label='Level (%)')
    ax1.axhline(config.LEVEL_LOW_PCT, color='steelblue', linestyle='--', linewidth=1.0,
                label=f'LEVEL_LOW_PCT = {config.LEVEL_LOW_PCT}%')
    ax1.axhline(config.LEVEL_HIGH_PCT, color='navy', linestyle='--', linewidth=1.0,
                label=f'LEVEL_HIGH_PCT = {config.LEVEL_HIGH_PCT}%')
    ax1.set_title('Day 2 – Process Simulator: Level & pH Dynamics')
    ax1.set_ylabel('Tank Level (%)')
    ax1.legend(loc='lower right', fontsize=9)
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(1, 200)

    # Subplot 2 — pH
    ax2.plot(steps, phs, color='green', linewidth=1.5, label='pH')
    ax2.axhline(config.PH_DOSE_ON, color='orange', linestyle='--', linewidth=1.0,
                label=f'PH_DOSE_ON = {config.PH_DOSE_ON}')
    ax2.axhline(config.PH_SETPOINT, color='darkgreen', linestyle='--', linewidth=1.0,
                label=f'PH_SETPOINT = {config.PH_SETPOINT}')
    ax2.set_xlabel('Simulation step (s)')
    ax2.set_ylabel('pH')
    ax2.legend(loc='upper right', fontsize=9)
    ax2.grid(True, alpha=0.3)
    ax2.set_xlim(1, 200)

    plt.tight_layout()
    plt.savefig(out_path, dpi=100)
    plt.close()
    print(f"\nChart saved to: {out_path}")


# ── Test registry and runner ──────────────────────────────────────────────────

TESTS = [
    ("test_initial_conditions",      test_initial_conditions),
    ("test_pump_on_raises_level",     test_pump_on_raises_level),
    ("test_valve_open_lowers_level",  test_valve_open_lowers_level),
    ("test_dosing_raises_ph",         test_dosing_raises_ph),
    ("test_pump_disturbs_ph",         test_pump_disturbs_ph),
    ("test_level_clamps_at_100",      test_level_clamps_at_100),
    ("test_level_clamps_at_zero",     test_level_clamps_at_zero),
    ("test_ph_clamps_in_range",       test_ph_clamps_in_range),
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

    levels, phs = collect_chart_data()
    save_chart(levels, phs)

