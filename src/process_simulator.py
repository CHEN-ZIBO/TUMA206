# process_simulator.py — physical process model for Treatment Tank T-101
#
# Models: tank level dynamics, inlet/outlet flow, pH disturbance + dosing response.
# This represents the "real world" that sensors read and the PLC controls.

import config

class ProcessSimulator:
    """
    Simulates the water treatment process at Treatment Tank T-101.

    State variables updated every SAMPLE_RATE_S seconds:
      level_pct     — tank fill level (0–100 %)
      ph            — pH of water in tank
      inlet_flow    — actual inlet flow L/min (depends on pump state)
      outlet_flow   — actual outlet flow L/min (depends on valve state)
    """

    def __init__(self):
        self.level_pct   = config.INITIAL_LEVEL_PCT
        self.ph          = config.PH_INITIAL
        self.inlet_flow  = 0.0   # L/min
        self.outlet_flow = 0.0   # L/min

        # Actuator states set by PLC via apply_commands()
        self._pump_on   = False
        self._valve_open = False
        self._dosing_on  = False

    # ── Called by PLC scan cycle ───────────────────────────────────────────────

    def apply_commands(self, pump_on: bool, valve_open: bool, dosing_on: bool):
        """Receive actuator commands from PLC controller."""
        self._pump_on    = pump_on
        self._valve_open = valve_open
        self._dosing_on  = dosing_on

    def step(self, dt: float = config.SAMPLE_RATE_S):
        """Advance process physics by dt seconds."""
        self._update_flows()
        self._update_level(dt)
        self._update_ph(dt)

    # ── Internal physics ──────────────────────────────────────────────────────

    def _update_flows(self):
        # Pump P-101 controls inlet flow
        self.inlet_flow = config.INLET_FLOW_NOMINAL_LPM if self._pump_on else 0.0
        # Valve V-101 controls outlet flow; can't drain below 0 %
        if self._valve_open and self.level_pct > 0.1:
            self.outlet_flow = config.OUTLET_FLOW_NOMINAL_LPM
        else:
            self.outlet_flow = 0.0

    def _update_level(self, dt: float):
        # Convert L/min flows to % change over dt seconds
        #   delta_litres = (inlet - outlet) * dt/60
        #   delta_pct    = delta_litres / TANK_CAPACITY_L * 100
        delta_l = (self.inlet_flow - self.outlet_flow) * dt / 60.0
        self.level_pct += (delta_l / config.TANK_CAPACITY_L) * 100.0
        self.level_pct = max(0.0, min(100.0, self.level_pct))

    def _update_ph(self, dt: float):
        # Inlet water is slightly acidic → lowers pH when pump runs
        if self._pump_on:
            self.ph -= config.PH_DISTURBANCE * dt
        # Chemical dosing raises pH
        if self._dosing_on:
            self.ph += config.PH_DOSING_EFFECT * dt
        # Clamp to physically realistic range
        self.ph = max(4.0, min(10.0, self.ph))

    # ── Read-only state access ────────────────────────────────────────────────

    @property
    def state(self) -> dict:
        return {
            "level_pct":    round(self.level_pct, 2),
            "ph":           round(self.ph, 3),
            "inlet_flow":   round(self.inlet_flow, 2),
            "outlet_flow":  round(self.outlet_flow, 2),
            "pump_on":      self._pump_on,
            "valve_open":   self._valve_open,
            "dosing_on":    self._dosing_on,
        }
