# plc_controller.py — simulated PLC scan cycle for Water Treatment Tank T-101
#
# Mirrors the three-step deterministic loop of a real PLC:
#   1. Read Inputs   — receive sensor tag dict
#   2. Execute Logic — state machine + on/off + pseudo-PID pH control
#   3. Update Outputs — return command dict
#
# State machine states:
#   IDLE        — system initialising or manually stopped
#   FILLING     — inlet pump ON, building level toward HIGH threshold
#   TREATING    — level in operating band; pH dosing active as needed
#   DISCHARGING — level exceeded HIGH; outlet valve open to drain
#   FAULT       — critical alarm active; safe mode (all off)

from enum import Enum, auto
import time
import config

class State(Enum):
    IDLE        = auto()
    FILLING     = auto()
    TREATING    = auto()
    DISCHARGING = auto()
    FAULT       = auto()


class PLCController:
    """
    Simulated PLC for Treatment Tank T-101.

    Usage:
        plc = PLCController()
        commands = plc.execute(sensor_tags)   # call once per scan cycle
        # commands: dict with pump_cmd, valve_cmd, dosing_cmd, alarms
    """

    def __init__(self):
        self.state       = State.IDLE
        self.alarms: dict[str, bool] = {
            "ALM_LEVEL_HIGH":   False,
            "ALM_LEVEL_LOW":    False,
            "ALM_LEVEL_CRIT":   False,
            "ALM_PH_LOW":       False,
            "ALM_PH_HIGH":      False,
            "ALM_PUMP_FAIL":    False,
        }
        # Pump-fail detection: count seconds where CMD=ON but flow≈0
        self._pump_fail_counter = 0

        # Pseudo-PID state for pH dosing
        self._ph_integral   = 0.0
        self._ph_prev_error = 0.0

    # ── Main entry point — called once per scan cycle ─────────────────────────

    def execute(self, tags: dict) -> dict:
        """
        Step 1+2+3: consume sensor readings, run logic, return commands.

        tags keys (from SensorSimulator.read_all_tags):
            WT_T101_LEVEL_PV, WT_A101_PH_PV,
            WT_F101_INLET_FLOW_PV, WT_P101_PUMP_FB, WT_V101_OUTLET_FB

        returns dict:
            WT_P101_PUMP_CMD   bool
            WT_V101_OUTLET_CMD bool
            WT_D101_DOSING_CMD bool
            WT_ALM_ACTIVE      bool
            alarms             dict  (individual alarm flags)
            state              str
        """
        level   = tags.get("WT_T101_LEVEL_PV",      50.0)
        ph      = tags.get("WT_A101_PH_PV",          7.0)
        flow_in = tags.get("WT_F101_INLET_FLOW_PV",  0.0)
        pump_fb = tags.get("WT_P101_PUMP_FB",        False)

        # -- Step 2a: update alarms --
        self._update_alarms(level, ph)

        # -- Step 2b: equipment fault detection --
        self._check_pump_fault(pump_fb, flow_in)

        # -- Step 2c: state machine transition --
        self._transition(level, ph)

        # -- Step 2d: compute output commands from current state --
        pump_cmd, valve_cmd, dosing_cmd = self._compute_outputs(level, ph)

        any_alarm = any(self.alarms.values())

        return {
            "WT_P101_PUMP_CMD":   pump_cmd,
            "WT_V101_OUTLET_CMD": valve_cmd,
            "WT_D101_DOSING_CMD": dosing_cmd,
            "WT_ALM_ACTIVE":      any_alarm,
            "alarms":             dict(self.alarms),
            "state":              self.state.name,
        }

    # ── State machine ─────────────────────────────────────────────────────────

    def _transition(self, level: float, ph: float):
        # Critical fault overrides everything
        if self.alarms["ALM_LEVEL_CRIT"] or self.alarms["ALM_PH_LOW"] or \
           self.alarms["ALM_PH_HIGH"] or self.alarms["ALM_PUMP_FAIL"]:
            self.state = State.FAULT
            return

        # Clear FAULT only when all critical alarms clear
        if self.state == State.FAULT:
            return   # operator must manually reset (not implemented in sim, auto-clears via alarm logic)

        if self.state == State.IDLE:
            if level < config.LEVEL_LOW_PCT:
                self.state = State.FILLING

        elif self.state == State.FILLING:
            if level >= config.LEVEL_LOW_PCT:
                self.state = State.TREATING

        elif self.state == State.TREATING:
            if level > config.LEVEL_HIGH_PCT:
                self.state = State.DISCHARGING
            elif level < config.LEVEL_LOW_PCT:
                self.state = State.FILLING

        elif self.state == State.DISCHARGING:
            if level <= config.LEVEL_HIGH_PCT:
                self.state = State.TREATING

    def _compute_outputs(self, level: float, ph: float):
        if self.state == State.FAULT:
            # Safe mode: everything off
            self._reset_pid()
            return False, False, False

        if self.state == State.IDLE:
            return False, False, False

        # FILLING: pump on, valve closed, dosing as needed
        if self.state == State.FILLING:
            pump  = True
            valve = False
            dose  = self._ph_dosing(ph)
            return pump, valve, dose

        # TREATING: pump off/on by level, dosing active
        if self.state == State.TREATING:
            pump  = level < config.LEVEL_HIGH_PCT   # keep filling until high threshold
            valve = False
            dose  = self._ph_dosing(ph)
            return pump, valve, dose

        # DISCHARGING: pump off, valve open, dosing off while draining
        if self.state == State.DISCHARGING:
            return False, True, False

        return False, False, False

    # ── pH dosing logic (on/off with hysteresis, informed by pseudo-PID) ──────

    def _ph_dosing(self, ph: float) -> bool:
        """
        Simple proportional pH control with on/off hysteresis:
          - Dose ON  when pH < PH_DOSE_ON  (6.8)
          - Dose OFF when pH > PH_DOSE_OFF (7.3)
        A pseudo-PID integral prevents over-shooting the setpoint.
        """
        error = config.PH_SETPOINT - ph
        self._ph_integral   += error * config.SAMPLE_RATE_S
        self._ph_prev_error  = error

        # Simple hysteresis band on/off
        if ph < config.PH_DOSE_ON:
            return True
        if ph > config.PH_DOSE_OFF:
            return False
        # Within band: keep current state (caller tracks last dosing_cmd)
        return False

    def _reset_pid(self):
        self._ph_integral   = 0.0
        self._ph_prev_error = 0.0

    # ── Alarm logic ───────────────────────────────────────────────────────────

    def _update_alarms(self, level: float, ph: float):
        self.alarms["ALM_LEVEL_HIGH"] = level > config.LEVEL_HIGH_PCT
        self.alarms["ALM_LEVEL_LOW"]  = level < config.LEVEL_LOW_PCT
        self.alarms["ALM_LEVEL_CRIT"] = level > config.LEVEL_CRITICAL_PCT
        self.alarms["ALM_PH_LOW"]     = ph < config.PH_LOW_ALARM
        self.alarms["ALM_PH_HIGH"]    = ph > config.PH_HIGH_ALARM

    def _check_pump_fault(self, pump_fb: bool, flow_in: float):
        """
        Equipment fault: PUMP_CMD=ON but measured flow near zero for >PUMP_FAULT_TIMEOUT_S.
        Uses pump feedback from sensor layer as proxy for CMD state.
        """
        if pump_fb and flow_in < 0.5:
            self._pump_fail_counter += 1
        else:
            self._pump_fail_counter = 0

        self.alarms["ALM_PUMP_FAIL"] = (
            self._pump_fail_counter >= config.PUMP_FAULT_TIMEOUT_S
        )

    # ── Manual control (for fault injection / operator override) ─────────────

    def reset_fault(self):
        """Clear FAULT state and all alarms — operator manual reset."""
        for k in self.alarms:
            self.alarms[k] = False
        self._pump_fail_counter = 0
        self.state = State.IDLE
        self._reset_pid()

    def force_state(self, state: State):
        """Override state — used by fault injector and tests."""
        self.state = state
