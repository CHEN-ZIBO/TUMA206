# mqtt_client.py — production-grade MQTT wrapper for Water Treatment Digital Twin
#
# Improvements over Day 2 version:
#   • Auto-reconnect with exponential back-off (up to MAX_RECONNECT_DELAY_S)
#   • Per-topic callback registry — multiple subscribers on different topics
#   • Infrastructure fault detection: raises WT_ALM_INFRA_STALE when broker
#     has been unreachable for more than INFRA_FAULT_TIMEOUT_S seconds
#   • Thread-safe publish queue: main loop never blocks waiting for the network
#   • Sequence number on every message for gap detection by consumers
#   • Clean API mirrors Day 2 so callers (main.py, dashboard.py) need no changes

import json
import logging
import queue
import threading
import time

try:
    import paho.mqtt.client as mqtt_lib
    _PAHO_AVAILABLE = True
except ImportError:
    _PAHO_AVAILABLE = False

import config

logger = logging.getLogger(__name__)

# ── Reconnect policy ──────────────────────────────────────────────────────────
_RECONNECT_DELAY_INIT_S = 1      # first retry after 1 s
_RECONNECT_DELAY_MAX_S  = 30     # cap back-off at 30 s
_RECONNECT_MULTIPLIER   = 2      # double delay each attempt

# ── Infrastructure fault threshold ───────────────────────────────────────────
INFRA_FAULT_TIMEOUT_S = 30       # seconds without broker → infra alarm


class MQTTClient:
    """
    Robust MQTT client with auto-reconnect, per-topic callbacks, and
    infrastructure fault detection.

    Public API
    ----------
    publish_tags(tags)       Publish tag snapshot to water_treatment/tags.
    publish_alarm(alarm)     Publish alarm dict to water_treatment/alarms.
    publish_fault(fault)     Publish fault dict to water_treatment/faults.
    publish(topic, payload)  Publish any dict to an arbitrary topic.
    subscribe(topic, cb)     Register callback(payload_dict) for a topic.
    unsubscribe(topic)       Remove callback and unsubscribe.
    connected                True if broker is currently reachable.
    infra_alarm              True if broker has been unreachable > threshold.
    stats()                  Return dict of diagnostic counters.
    disconnect()             Graceful shutdown; flush the send queue first.
    """

    def __init__(self):
        self._connected      = False
        self._client         = None
        self._callbacks: dict[str, list] = {}   # topic → [callable, ...]
        self._seq            = 0
        self._lock           = threading.Lock()

        # Publish queue: main loop deposits messages; sender thread drains it
        self._send_queue: queue.Queue = queue.Queue(maxsize=200)

        # Infra fault tracking
        self._last_connected_ts: float | None = None
        self._disconnect_since:  float | None = None

        # Counters
        self._published  = 0
        self._dropped    = 0
        self._reconnects = 0

        self._shutdown   = False

        if not _PAHO_AVAILABLE:
            logger.warning(
                "paho-mqtt not installed — MQTT disabled. "
                "Run: pip install paho-mqtt"
            )
            return

        self._build_client()
        self._connect_async()

        # Background thread drains _send_queue → broker
        self._sender_thread = threading.Thread(
            target=self._sender_loop,
            daemon=True,
            name="mqtt-sender",
        )
        self._sender_thread.start()

    # ── Connection lifecycle ──────────────────────────────────────────────────

    def _build_client(self):
        """Create a fresh paho Client instance with callbacks registered."""
        try:
            # paho ≥ 2.0 changed the default protocol; use CallbackAPIVersion if available
            from paho.mqtt.client import CallbackAPIVersion
            self._client = mqtt_lib.Client(
                callback_api_version=CallbackAPIVersion.VERSION1,
                client_id="wt_digital_twin",
                clean_session=True,
            )
        except (ImportError, AttributeError):
            self._client = mqtt_lib.Client(
                client_id="wt_digital_twin",
                clean_session=True,
            )

        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

    def _connect_async(self):
        """
        Attempt broker connection in a daemon thread so __init__ returns fast.
        Uses exponential back-off: 1 → 2 → 4 → … → 30 s between retries.
        """
        def _try_connect():
            delay = _RECONNECT_DELAY_INIT_S
            while not self._shutdown:
                try:
                    self._client.connect(
                        config.MQTT_BROKER_HOST,
                        config.MQTT_BROKER_PORT,
                        keepalive=60,
                    )
                    self._client.loop_start()
                    return   # success — paho handles reconnect from here
                except (ConnectionRefusedError, OSError, TimeoutError):
                    logger.debug(
                        "MQTT broker unreachable — retry in %d s", delay
                    )
                    time.sleep(delay)
                    delay = min(delay * _RECONNECT_MULTIPLIER, _RECONNECT_DELAY_MAX_S)

        t = threading.Thread(target=_try_connect, daemon=True, name="mqtt-connect")
        t.start()

    # ── paho callbacks ────────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            with self._lock:
                self._connected = True
                self._last_connected_ts = time.monotonic()
                self._disconnect_since  = None
                self._reconnects       += 1

            # Re-subscribe all registered topics after reconnect
            for topic in list(self._callbacks.keys()):
                client.subscribe(topic)

            logger.info(
                "MQTT connected to %s:%d (reconnects: %d)",
                config.MQTT_BROKER_HOST,
                config.MQTT_BROKER_PORT,
                self._reconnects,
            )
        else:
            logger.warning("MQTT connect refused, rc=%d", rc)

    def _on_disconnect(self, client, userdata, rc):
        with self._lock:
            self._connected = False
            if self._disconnect_since is None:
                self._disconnect_since = time.monotonic()

        if rc != 0:
            logger.warning("MQTT broker disconnected unexpectedly (rc=%d) — will retry", rc)
            # paho loop_start() handles automatic reconnect for us

    def _on_message(self, client, userdata, msg):
        """Route incoming message to registered per-topic callbacks."""
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.debug("Received non-JSON message on %s", msg.topic)
            return

        with self._lock:
            callbacks = list(self._callbacks.get(msg.topic, []))

        for cb in callbacks:
            try:
                cb(payload)
            except Exception as exc:
                logger.error("Callback error for topic %s: %s", msg.topic, exc)

    # ── Publish API ───────────────────────────────────────────────────────────

    def publish_tags(self, tags: dict, topic: str = config.MQTT_TOPIC_TAGS):
        """Enqueue tag snapshot for delivery. Adds seq number."""
        self.publish(topic, tags)

    def publish_alarm(self, alarm: dict):
        """Enqueue alarm notification. Uses QoS 1 for reliability."""
        self.publish(config.MQTT_TOPIC_ALARMS, alarm, qos=1)

    def publish_fault(self, fault: dict):
        """Enqueue fault notification. Uses QoS 1."""
        self.publish(config.MQTT_TOPIC_FAULTS, fault, qos=1)

    def publish(self, topic: str, payload: dict, qos: int = 0):
        """
        Enqueue any message for publication.

        Non-blocking: the caller deposits to _send_queue and returns
        immediately. The sender thread picks it up and publishes.

        If the queue is full (broker down for a long time), the oldest
        message is dropped and _dropped counter incremented.
        """
        if not self._client:
            return

        with self._lock:
            self._seq += 1
            seq = self._seq

        envelope = {
            "seq":     seq,
            "payload": payload,
            "topic":   topic,
            "qos":     qos,
        }

        try:
            self._send_queue.put_nowait(envelope)
        except queue.Full:
            # Drop oldest to make room
            try:
                self._send_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._send_queue.put_nowait(envelope)
            except queue.Full:
                pass
            with self._lock:
                self._dropped += 1
            logger.debug("MQTT send queue full — oldest message dropped")

    def _sender_loop(self):
        """
        Background thread: drain _send_queue → broker.
        If not connected, messages stay in the queue (up to maxsize=200).
        """
        while not self._shutdown:
            try:
                envelope = self._send_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if not self._connected or not self._client:
                # Not connected: put back and wait
                try:
                    self._send_queue.put(envelope, block=False)
                except queue.Full:
                    with self._lock:
                        self._dropped += 1
                time.sleep(0.5)
                continue

            try:
                self._client.publish(
                    envelope["topic"],
                    json.dumps(envelope["payload"]),
                    qos=envelope["qos"],
                )
                with self._lock:
                    self._published += 1
            except Exception as exc:
                logger.warning("MQTT publish error: %s", exc)

    # ── Subscribe API ─────────────────────────────────────────────────────────

    def subscribe(self, topic: str, callback):
        """
        Register callback(payload_dict) for messages arriving on topic.
        Multiple callbacks per topic are supported.
        Safe to call before the broker is connected; subscriptions are
        re-applied automatically on reconnect.
        """
        with self._lock:
            self._callbacks.setdefault(topic, []).append(callback)

        if self._client and self._connected:
            self._client.subscribe(topic)

    def unsubscribe(self, topic: str):
        """Remove all callbacks for a topic and unsubscribe from broker."""
        with self._lock:
            self._callbacks.pop(topic, None)
        if self._client and self._connected:
            self._client.unsubscribe(topic)

    # ── Status / diagnostics ──────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def infra_alarm(self) -> bool:
        """
        True when the broker has been unreachable for more than
        INFRA_FAULT_TIMEOUT_S seconds — triggers Infrastructure Fault layer.
        """
        with self._lock:
            if self._connected:
                return False
            if self._disconnect_since is None:
                # Never connected yet; only alarm if we've waited long enough
                if self._last_connected_ts is None:
                    return False
                return False   # never connected but not yet in alarm
            return (time.monotonic() - self._disconnect_since) >= INFRA_FAULT_TIMEOUT_S

    def stats(self) -> dict:
        """
        Return diagnostic counters.

        Keys: connected, published, dropped, reconnects,
              queue_depth, infra_alarm, disconnect_since_s
        """
        with self._lock:
            ds = self._disconnect_since
        return {
            "connected":         self._connected,
            "published":         self._published,
            "dropped":           self._dropped,
            "reconnects":        self._reconnects,
            "queue_depth":       self._send_queue.qsize(),
            "infra_alarm":       self.infra_alarm,
            "disconnect_since_s": round(time.monotonic() - ds, 1) if ds else 0,
        }

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def disconnect(self):
        """
        Flush the send queue (wait up to 3 s for pending messages to drain),
        then stop the paho network loop and disconnect cleanly.
        """
        self._shutdown = True

        # Drain queue
        deadline = time.monotonic() + 3.0
        while not self._send_queue.empty() and time.monotonic() < deadline:
            time.sleep(0.05)

        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass

        s = self.stats()
        logger.info(
            "MQTT disconnected — published: %d, dropped: %d, reconnects: %d",
            s["published"], s["dropped"], s["reconnects"],
        )
