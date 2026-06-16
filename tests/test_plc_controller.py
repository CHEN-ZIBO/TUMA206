# tests/test_plc_controller.py — unit tests for PLCController
#
# Run:  python -m pytest tests/test_plc_controller.py -v
#   or: python tests/test_plc_controller.py
# Saves chart to tests/charts/day3_plc_controller.png

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import config
from plc_controller import PLCController, State


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_tags(level=50.0, ph=7.0, flow_in=10.0, pump_fb=True):
    return {
        "WT_T101_LEVEL_PV":      level,
        "WT_A101_PH_PV":         ph,
        "WT_F101_INLET_FLOW_PV": flow_in,
        "WT_P101_PUMP_FB":       pump_fb,
        "WT_V101_OUTLET_FB":     False,
    }


# ── Test: normal operating cycle ─────────────────────────────────────────────

def test_idle_to_filling_when_level_low():
    """PLC starts IDLE; transitions to FILLING when level < LOW threshold."""
    plc = PLCController()
    cmds = plc.execute(make_tags(level=30.0, ph=7.0))
    assert plc.state == State.FILLING
    assert cmds["WT_P101_PUMP_CMD"] is True,  "Pump should be ON in FILLING"
    assert cmds["WT_V101_OUTLET_CMD"] is False, "Valve should be CLOSED in FILLING"


def test_filling_to_treating():
    """Once level reaches LOW threshold PLC moves to TREATING."""
    plc = PLCController()
    plc.force_state(State.FILLING)
    cmds = plc.execute(make_tags(level=config.LEVEL_LOW_PCT + 1, ph=7.0))
    assert plc.state == State.TREATING


def test_treating_to_discharging():
    """Level above HIGH threshold triggers DISCHARGING."""
    plc = PLCController()
    plc.force_state(State.TREATING)
    cmds = plc.execute(make_tags(level=config.LEVEL_HIGH_PCT + 1, ph=7.0))
    assert plc.state == State.DISCHARGING
    assert cmds["WT_V101_OUTLET_CMD"] is True, "Outlet valve should be OPEN in DISCHARGING"
    assert cmds["WT_P101_PUMP_CMD"] is False,  "Pump should be OFF in DISCHARGING"


def test_discharging_returns_to_treating():
    """After draining below HIGH threshold, state returns to TREATING."""
    plc = PLCController()
    plc.force_state(State.DISCHARGING)
    cmds = plc.execute(make_tags(level=config.LEVEL_HIGH_PCT - 1, ph=7.0))
    assert plc.state == State.TREATING


# ── Test: pH dosing ───────────────────────────────────────────────────────────

def test_dosing_on_when_ph_low():
    """Dosing pump activates when pH < PH_DOSE_ON."""
    plc = PLCController()
    plc.force_state(State.TREATING)
    cmds = plc.execute(make_tags(level=50.0, ph=config.PH_DOSE_ON - 0.1))
    assert cmds["WT_D101_DOSING_CMD"] is True, "Dosing should be ON when pH is low"


def test_dosing_off_when_ph_normal():
    """Dosing pump stops when pH > PH_DOSE_OFF."""
    plc = PLCController()
    plc.force_state(State.TREATING)
    cmds = plc.execute(make_tags(level=50.0, ph=config.PH_DOSE_OFF + 0.1))
    assert cmds["WT_D101_DOSING_CMD"] is False, "Dosing should be OFF when pH is normal"


# ── Test: alarms ──────────────────────────────────────────────────────────────

def test_critical_level_alarm():
    """Level above CRITICAL threshold raises alarm and sets FAULT state."""
    plc = PLCController()
    plc.force_state(State.TREATING)
    cmds = plc.execute(make_tags(level=config.LEVEL_CRITICAL_PCT + 1, ph=7.0))
    assert cmds["WT_ALM_ACTIVE"] is True
    assert cmds["alarms"]["ALM_LEVEL_CRIT"] is True
    assert plc.state == State.FAULT


def test_fault_state_all_outputs_off():
    """In FAULT state, all actuator commands must be False (safe mode)."""
    plc = PLCController()
    plc.force_state(State.FAULT)
    # Execute with normal values; state is already FAULT
    cmds = plc.execute(make_tags(level=50.0, ph=7.0))
    assert cmds["WT_P101_PUMP_CMD"]   is False
    assert cmds["WT_V101_OUTLET_CMD"] is False
    assert cmds["WT_D101_DOSING_CMD"] is False


def test_ph_low_alarm():
    plc = PLCController()
    plc.force_state(State.TREATING)
    cmds = plc.execute(make_tags(level=50.0, ph=config.PH_LOW_ALARM - 0.1))
    assert cmds["alarms"]["ALM_PH_LOW"] is True
    assert cmds["WT_ALM_ACTIVE"] is True


def test_ph_high_alarm():
    plc = PLCController()
    plc.force_state(State.TREATING)
    cmds = plc.execute(make_tags(level=50.0, ph=config.PH_HIGH_ALARM + 0.1))
    assert cmds["alarms"]["ALM_PH_HIGH"] is True


# ── Test: equipment fault ─────────────────────────────────────────────────────

def test_pump_fail_alarm_after_timeout():
    """
    Pump feedback ON but flow near zero for PUMP_FAULT_TIMEOUT_S cycles
    must raise ALM_PUMP_FAIL.
    """
    plc = PLCController()
    plc.force_state(State.TREATING)
    tags = make_tags(level=50.0, ph=7.0, flow_in=0.1, pump_fb=True)
    for _ in range(config.PUMP_FAULT_TIMEOUT_S):
        cmds = plc.execute(tags)
    assert cmds["alarms"]["ALM_PUMP_FAIL"] is True, (
        f"Expected pump fail alarm after {config.PUMP_FAULT_TIMEOUT_S} cycles"
    )


def test_pump_fail_counter_resets():
    """Counter resets when flow is restored."""
    plc = PLCController()
    plc.force_state(State.TREATING)
    # Fill up counter
    for _ in range(config.PUMP_FAULT_TIMEOUT_S - 1):
        plc.execute(make_tags(level=50.0, ph=7.0, flow_in=0.1, pump_fb=True))
    # Restore flow
    plc.execute(make_tags(level=50.0, ph=7.0, flow_in=10.0, pump_fb=True))
    assert plc._pump_fail_counter == 0


# ── Test: manual reset ────────────────────────────────────────────────────────

def test_reset_fault_clears_state():
    plc = PLCController()
    plc.force_state(State.FAULT)
    plc.reset_fault()
    assert plc.state == State.IDLE
    assert not any(plc.alarms.values())


# ── Chart data collection ─────────────────────────────────────────────────────

def collect_chart_data():
    """200-step closed-loop run starting at level=20% to force full state cycle."""
    from process_simulator import ProcessSimulator
    from sensor_simulator  import SensorSimulator

    process = ProcessSimulator()
    process.level_pct = 20.0
    sensors = SensorSimulator(process)
    plc     = PLCController()
    process.apply_commands(pump_on=False, valve_open=False, dosing_on=False)

    STATE_ORDER = ["IDLE", "FILLING", "TREATING", "DISCHARGING", "FAULT"]
    state_map   = {s: i for i, s in enumerate(STATE_ORDER)}

    steps, levels, phs, state_nums = [], [], [], []
    pump_cmds, dose_cmds, alm_flags = [], [], []

    for step in range(1, 201):
        process.step(config.SAMPLE_RATE_S)
        tags = sensors.read_all_tags()
        cmds = plc.execute(tags)
        process.apply_commands(
            cmds["WT_P101_PUMP_CMD"],
            cmds["WT_V101_OUTLET_CMD"],
            cmds["WT_D101_DOSING_CMD"],
        )
        steps.append(step)
        levels.append(tags["WT_T101_LEVEL_PV"])
        phs.append(tags["WT_A101_PH_PV"])
        state_nums.append(state_map.get(cmds["state"], -1))
        pump_cmds.append(1 if cmds["WT_P101_PUMP_CMD"]   else 0)
        dose_cmds.append(1 if cmds["WT_D101_DOSING_CMD"] else 0)
        alm_flags.append(1 if cmds["WT_ALM_ACTIVE"]      else 0)

    return steps, levels, phs, state_nums, pump_cmds, dose_cmds, alm_flags


def save_chart(steps, levels, phs, state_nums, pump_cmds, dose_cmds, alm_flags):
    charts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts")
    os.makedirs(charts_dir, exist_ok=True)
    out_path = os.path.join(charts_dir, "day3_plc_controller.png")

    STATE_LABELS = ["IDLE", "FILLING", "TREATING", "DISCHARGING", "FAULT"]
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    # Subplot 1 — Tank level
    ax1.plot(steps, levels, color="steelblue", linewidth=1.5, label="Level (%)")
    ax1.fill_between(steps, 0, 100,
                     where=[p == 1 for p in pump_cmds],
                     alpha=0.12, color="green", label="Pump ON")
    ax1.axhline(config.LEVEL_LOW_PCT,      color="navy",   linestyle="--", linewidth=1.0,
                label=f"LOW {config.LEVEL_LOW_PCT}%")
    ax1.axhline(config.LEVEL_HIGH_PCT,     color="orange", linestyle="--", linewidth=1.0,
                label=f"HIGH {config.LEVEL_HIGH_PCT}%")
    ax1.axhline(config.LEVEL_CRITICAL_PCT, color="red",    linestyle="--", linewidth=1.0,
                label=f"CRIT {config.LEVEL_CRITICAL_PCT}%")
    ax1.set_title("Day 3 – PLC Controller: Closed-Loop State Machine (200 steps)")
    ax1.set_ylabel("Tank Level (%)")
    ax1.legend(loc="lower right", fontsize=8, ncol=2)
    ax1.grid(True, alpha=0.3)
    ax1.set_ylim(0, 105)

    # Subplot 2 — pH + dosing regions
    ax2.plot(steps, phs, color="green", linewidth=1.5, label="pH")
    ax2.fill_between(steps, 4, 10,
                     where=[d == 1 for d in dose_cmds],
                     alpha=0.15, color="purple", label="Dosing ON")
    ax2.axhline(config.PH_DOSE_ON,   color="orange",   linestyle="--", linewidth=1.0,
                label=f"DOSE_ON {config.PH_DOSE_ON}")
    ax2.axhline(config.PH_DOSE_OFF,  color="darkgreen",linestyle="--", linewidth=1.0,
                label=f"DOSE_OFF {config.PH_DOSE_OFF}")
    ax2.axhline(config.PH_LOW_ALARM, color="red",      linestyle=":",  linewidth=1.0,
                label=f"ALM_LOW {config.PH_LOW_ALARM}")
    ax2.set_ylabel("pH")
    ax2.set_ylim(4, 10)
    ax2.legend(loc="lower right", fontsize=8, ncol=2)
    ax2.grid(True, alpha=0.3)

    # Subplot 3 — State machine
    ax3.step(steps, state_nums, where="post", color="darkblue", linewidth=1.5)
    ax3.set_yticks(range(len(STATE_LABELS)))
    ax3.set_yticklabels(STATE_LABELS, fontsize=8)
    ax3.set_xlabel("Simulation step (s)")
    ax3.set_ylabel("PLC State")
    ax3.set_ylim(-0.5, len(STATE_LABELS) - 0.5)
    ax3.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(out_path, dpi=100)
    plt.close()
    print(f"\nChart saved to: {out_path}")


# ── Standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_idle_to_filling_when_level_low,
        test_filling_to_treating,
        test_treating_to_discharging,
        test_discharging_returns_to_treating,
        test_dosing_on_when_ph_low,
        test_dosing_off_when_ph_normal,
        test_critical_level_alarm,
        test_fault_state_all_outputs_off,
        test_ph_low_alarm,
        test_ph_high_alarm,
        test_pump_fail_alarm_after_timeout,
        test_pump_fail_counter_resets,
        test_reset_fault_clears_state,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)

    data = collect_chart_data()
    save_chart(*data)
