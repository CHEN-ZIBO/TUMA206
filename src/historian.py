# historian.py — thread-safe time-series logger for Water Treatment Digital Twin
#
# Architecture: dual-track storage
#   ┌─────────────┐    write()    ┌─────────────────────────────┐
#   │  main loop  │ ────────────► │  _buffer (list, max 10 rows)│
#   │  (1 Hz)     │               │  + _ring  (deque, 1h window)│
#   └─────────────┘               └────────────┬────────────────┘
#                                              │  background flush thread
#                                              │  every 5 s (or when buffer full)
#                                              ▼
#                                    data/historian.csv  (append)
#
#   dashboard / AI   ──  load_recent(n)  ──►  _ring   (O(1), no disk I/O)
#
# Thread safety: a single threading.Lock protects _buffer and CSV writes.
# The dashboard thread can call load_recent() concurrently with the main loop.

import csv
import os
import threading
import time
import logging
from collections import deque

try:
    import pandas as pd
    _PANDAS = True
except ImportError:
    _PANDAS = False

import config

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────
# Ordered list of CSV column names.  Any tag not in this list is silently
# ignored; any column missing from the tag dict is written as empty string.
HISTORIAN_COLUMNS = [
    "timestamp",
    # Analog inputs — process values
    "WT_T101_LEVEL_PV",
    "WT_F101_INLET_FLOW_PV",
    "WT_F102_OUTLET_FLOW_PV",
    "WT_A101_PH_PV",
    # Digital inputs — equipment feedback
    "WT_P101_PUMP_FB",
    "WT_V101_OUTLET_FB",
    # Digital outputs — PLC commands
    "WT_P101_PUMP_CMD",
    "WT_V101_OUTLET_CMD",
    "WT_D101_DOSING_CMD",
    # Derived / status
    "WT_ALM_ACTIVE",
    "plc_state",
]

# ── Tuning constants ──────────────────────────────────────────────────────────
IN_MEMORY_ROWS   = 3600   # ring buffer depth = 1 hour at 1 Hz
FLUSH_INTERVAL_S = 5      # background thread flushes every N seconds
FLUSH_ROWS       = 10     # also flush eagerly when buffer reaches this size


class Historian:
    """
    Thread-safe time-series historian combining an in-memory ring buffer
    (fast dashboard reads) with a persistent CSV file (long-term storage).

    Public API
    ----------
    write(tags: dict)
        Accept one scan-cycle snapshot. Non-blocking — returns immediately.
        Adds "timestamp" key if absent.

    flush()
        Force-write the write buffer to CSV. Called automatically by the
        background thread every FLUSH_INTERVAL_S seconds.

    load_recent(n_seconds: int = 300) -> list[dict]
        Return up to n_seconds most recent rows from the ring buffer.
        Falls back to CSV tail-read if the ring is empty (post-restart).

    load_dataframe(n_seconds: int = 300) -> pd.DataFrame
        Same as load_recent() but returns a pandas DataFrame with typed
        columns (timestamp as datetime64, numerics as float64).
        Raises ImportError if pandas is not installed.

    stats() -> dict
        Return diagnostic metrics: row count, file size, time range.
        Used by the dashboard health panel.

    close()
        Flush remaining buffer, stop background thread, log summary.

    Context manager:
        with Historian() as h:
            h.write(tags)
    """

    def __init__(self, csv_path: str = config.HISTORIAN_CSV):
        self._csv_path   = _resolve_path(csv_path)
        self._lock       = threading.Lock()
        self._buffer: list[dict]    = []
        self._ring:   deque[dict]   = deque(maxlen=IN_MEMORY_ROWS)
        self._row_count  = 0
        self._closed     = False

        self._ensure_header()

        # Daemon thread — dies automatically when main process exits
        self._flush_thread = threading.Thread(
            target=self._background_flush,
            daemon=True,
            name="historian-flush",
        )
        self._flush_thread.start()
        logger.info("Historian started — logging to %s", self._csv_path)

    # ── Write path ────────────────────────────────────────────────────────────

    def write(self, tags: dict):
        """
        Buffer one scan-cycle snapshot.

        Parameters
        ----------
        tags : dict
            Must contain at least the keys in HISTORIAN_COLUMNS (minus
            "timestamp").  Extra keys are silently ignored.
            A "timestamp" key is added automatically if missing.
        """
        if self._closed:
            return
        row = {"timestamp": tags.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))}
        for col in HISTORIAN_COLUMNS[1:]:
            row[col] = tags.get(col, "")

        with self._lock:
            self._buffer.append(row)
            self._ring.append(row)
            self._row_count += 1
            # Eager flush when buffer is full
            if len(self._buffer) >= FLUSH_ROWS:
                self._flush_locked()

    # ── Read path ─────────────────────────────────────────────────────────────

    def load_recent(self, n_seconds: int = 300) -> list[dict]:
        """
        Return up to n_seconds most recent rows.

        Reads from the in-memory ring buffer (O(1) copy), so it is safe to
        call from the dashboard thread at high frequency.

        If the ring is empty — e.g. on a fresh restart after a long run —
        falls back to reading the tail of the CSV file.

        Parameters
        ----------
        n_seconds : int
            Number of rows to return (1 row = 1 second at default sample rate).

        Returns
        -------
        list[dict]
            Chronological order, oldest first.
        """
        with self._lock:
            snapshot = list(self._ring)

        if snapshot:
            return snapshot[-n_seconds:]
        # Ring empty: read CSV tail
        return _read_csv_tail(self._csv_path, n_seconds)

    def load_dataframe(self, n_seconds: int = 300):
        """
        Return recent data as a typed pandas DataFrame.

        Columns
        -------
        timestamp               datetime64[ns]
        WT_T101_LEVEL_PV        float64   — % level
        WT_F101_INLET_FLOW_PV   float64   — L/min
        WT_F102_OUTLET_FLOW_PV  float64   — L/min
        WT_A101_PH_PV           float64   — pH
        WT_P101_PUMP_FB         bool
        WT_V101_OUTLET_FB       bool
        WT_P101_PUMP_CMD        bool
        WT_V101_OUTLET_CMD      bool
        WT_D101_DOSING_CMD      bool
        WT_ALM_ACTIVE           bool
        plc_state               str

        Raises
        ------
        ImportError  if pandas is not installed.
        """
        if not _PANDAS:
            raise ImportError(
                "pandas is required for load_dataframe(). "
                "Install with: pip install pandas"
            )
        rows = self.load_recent(n_seconds)
        if not rows:
            return pd.DataFrame(columns=HISTORIAN_COLUMNS)

        df = pd.DataFrame(rows, columns=HISTORIAN_COLUMNS)
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

        float_cols = [
            "WT_T101_LEVEL_PV", "WT_F101_INLET_FLOW_PV",
            "WT_F102_OUTLET_FLOW_PV", "WT_A101_PH_PV",
        ]
        bool_cols = [
            "WT_P101_PUMP_FB", "WT_V101_OUTLET_FB",
            "WT_P101_PUMP_CMD", "WT_V101_OUTLET_CMD",
            "WT_D101_DOSING_CMD", "WT_ALM_ACTIVE",
        ]
        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in bool_cols:
            if col in df.columns:
                # CSV stores True/False as strings; normalise to bool
                df[col] = df[col].map(
                    lambda v: str(v).strip().lower() in ("true", "1", "on")
                )
        return df

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """
        Return historian health metrics.

        Keys
        ----
        total_rows_written  int    — cumulative rows since startup
        rows_in_memory      int    — current ring buffer depth
        rows_in_buffer      int    — rows awaiting next CSV flush
        oldest_timestamp    str|None
        newest_timestamp    str|None
        csv_path            str
        csv_size_kb         float
        """
        with self._lock:
            total   = self._row_count
            ring_n  = len(self._ring)
            buf_n   = len(self._buffer)
            oldest  = self._ring[0]["timestamp"]  if self._ring else None
            newest  = self._ring[-1]["timestamp"] if self._ring else None

        file_kb = 0.0
        if os.path.exists(self._csv_path):
            file_kb = round(os.path.getsize(self._csv_path) / 1024, 1)

        return {
            "total_rows_written": total,
            "rows_in_memory":     ring_n,
            "rows_in_buffer":     buf_n,
            "oldest_timestamp":   oldest,
            "newest_timestamp":   newest,
            "csv_path":           self._csv_path,
            "csv_size_kb":        file_kb,
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def flush(self):
        """Force-write all buffered rows to CSV immediately."""
        with self._lock:
            self._flush_locked()

    def close(self):
        """Flush remaining buffer and stop the background flush thread."""
        self._closed = True
        self.flush()
        s = self.stats()
        logger.info(
            "Historian closed — %d rows written, CSV %.1f KB at %s",
            s["total_rows_written"], s["csv_size_kb"], s["csv_path"],
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _flush_locked(self):
        """Write _buffer to CSV.  Must be called with self._lock held."""
        if not self._buffer:
            return
        rows = self._buffer[:]
        self._buffer.clear()
        try:
            with open(self._csv_path, "a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(
                    fh, fieldnames=HISTORIAN_COLUMNS, extrasaction="ignore"
                )
                writer.writerows(rows)
        except OSError as exc:
            logger.error("Historian CSV write failed: %s", exc)
            # Put rows back so they are not lost permanently
            self._buffer[:0] = rows

    def _background_flush(self):
        """Daemon thread: flush buffer to CSV every FLUSH_INTERVAL_S seconds."""
        while not self._closed:
            time.sleep(FLUSH_INTERVAL_S)
            if not self._closed:
                with self._lock:
                    self._flush_locked()

    def _ensure_header(self):
        """Create CSV with header row if the file does not exist or is empty."""
        os.makedirs(os.path.dirname(self._csv_path) or ".", exist_ok=True)
        needs_header = (
            not os.path.exists(self._csv_path)
            or os.path.getsize(self._csv_path) == 0
        )
        if needs_header:
            with open(self._csv_path, "w", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=HISTORIAN_COLUMNS).writeheader()
            logger.info("Created new historian CSV at %s", self._csv_path)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _resolve_path(csv_path: str) -> str:
    """
    Convert a relative path (e.g. "data/historian.csv") to an absolute path
    anchored at the repository root (the directory above src/).
    Absolute paths are returned unchanged.
    """
    if os.path.isabs(csv_path):
        return csv_path
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.normpath(os.path.join(repo_root, csv_path))


def _read_csv_tail(csv_path: str, n_rows: int) -> list[dict]:
    """
    Read the last n_rows rows from a CSV file without loading the entire file.
    Uses collections.deque(maxlen=n_rows) to limit memory usage regardless of
    file size.  Returns an empty list if the file does not exist or is empty.
    """
    if not os.path.exists(csv_path):
        return []
    try:
        with open(csv_path, "r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            return list(deque(reader, maxlen=n_rows))
    except OSError:
        return []
