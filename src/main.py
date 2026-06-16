# main.py — master control loop for Water Treatment Digital Twin
#
# Wires together: process_simulator → sensor_simulator → plc_controller
#                 → mqtt_client → historian
#
# Run:  python src/main.py
# Stop: Ctrl-C

import sys
import os
import time
import signal
import logging

# Allow import from src/ whether running from repo root or src/
sys.path.insert(0, os.path.dirname(__file__))

import config
from process_simulator import ProcessSimulator
from sensor_simulator   import SensorSimulator
from plc_controller     import PLCController
from mqtt_client        import MQTTClient
from historian          import Historian

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Graceful shutdown ─────────────────────────────────────────────────────────
_running = True

def _handle_sigint(sig, frame):
    global _running
    logger.info("Shutdown signal received — stopping main loop.")
    _running = False

signal.signal(signal.SIGINT, _handle_sigint)


def main():
    logger.info("=== Water Treatment Digital Twin — Starting ===")

    process   = ProcessSimulator()
    sensors   = SensorSimulator(process)
    plc       = PLCController()
    mqtt      = MQTTClient()
    historian = Historian()

    # Start with a default initial state: pump on, fill from 50 %
    process.apply_commands(pump_on=True, valve_open=False, dosing_on=False)

    logger.info("Main loop running. Press Ctrl-C to stop.")
    logger.info(f"MQTT broker: {'connected' if mqtt.connected else 'unavailable (offline mode)'}")

    cycle = 0
    while _running:
        loop_start = time.monotonic()
        cycle += 1

        # 1. Advance physical process
        process.step(config.SAMPLE_RATE_S)

        # 2. Read sensor tags
        sensor_tags = sensors.read_all_tags()

        # 3. PLC scan: execute control logic
        commands = plc.execute(sensor_tags)

        # 4. Apply actuator commands back to process
        process.apply_commands(
            pump_on    = commands["WT_P101_PUMP_CMD"],
            valve_open = commands["WT_V101_OUTLET_CMD"],
            dosing_on  = commands["WT_D101_DOSING_CMD"],
        )

        # 5. Build full tag payload for messaging + logging
        full_tags = {
            **sensor_tags,
            "WT_P101_PUMP_CMD":    commands["WT_P101_PUMP_CMD"],
            "WT_V101_OUTLET_CMD":  commands["WT_V101_OUTLET_CMD"],
            "WT_D101_DOSING_CMD":  commands["WT_D101_DOSING_CMD"],
            "WT_ALM_ACTIVE":       commands["WT_ALM_ACTIVE"] or mqtt.infra_alarm,
            "plc_state":           commands["state"],
        }

        # 6. Publish to MQTT
        mqtt.publish_tags(full_tags)
        if commands["WT_ALM_ACTIVE"]:
            mqtt.publish_alarm({
                "alarms":    commands["alarms"],
                "plc_state": commands["state"],
            })
        if mqtt.infra_alarm:
            mqtt.publish_fault({"fault_type": "infrastructure", "detail": "broker_unreachable"})

        # 7. Write to historian
        historian.write(full_tags)

        # 8. Console status every 5 cycles
        if cycle % 5 == 0:
            s = process.state
            logger.info(
                f"[{commands['state']:12s}] "
                f"level={s['level_pct']:5.1f}% "
                f"pH={s['ph']:.3f} "
                f"inlet={s['inlet_flow']:.1f} "
                f"outlet={s['outlet_flow']:.1f} L/min "
                f"pump={'ON ' if commands['WT_P101_PUMP_CMD'] else 'OFF'} "
                f"dose={'ON ' if commands['WT_D101_DOSING_CMD'] else 'OFF'} "
                f"alm={'!' if commands['WT_ALM_ACTIVE'] else ' '}"
            )

        # 9. Sleep remainder of scan cycle
        elapsed = time.monotonic() - loop_start
        sleep_s = max(0.0, config.SAMPLE_RATE_S - elapsed)
        time.sleep(sleep_s)

    mqtt.disconnect()
    historian.close()
    logger.info("=== Main loop stopped cleanly ===")


if __name__ == "__main__":
    main()
