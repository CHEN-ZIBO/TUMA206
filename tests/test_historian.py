# tests/test_historian.py — comprehensive unit tests for Historian
#
# Run:  python -m pytest tests/test_historian.py -v
#   or: python tests/test_historian.py
# Saves chart to tests/charts/day4_historian.png

import sys, os, csv, time, threading, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import config
from historian import Historian, HISTORIAN_COLUMNS, _read_csv_tail


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _tmp_csv():
    """Return a fresh temp file path (deleted after each test)."""
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="test_historian_")
    os.close(fd)
    os.unlink(path)   # let Historian create it fresh
    return path


def _make_tags(i: int = 0) -> dict:
    """Return a minimal valid tag dict."""
    return {
        "WT_T101_LEVEL_PV":       50.0 + i * 0.1,
        "WT_F101_INLET_FLOW_PV":  10.0,
        "WT_F102_OUTLET_FLOW_PV": 0.0,
        "WT_A101_PH_PV":          7.0,
        "WT_P101_PUMP_FB":        True,
        "WT_V101_OUTLET_FB":      False,
        "WT_P101_PUMP_CMD":       True,
        "WT_V101_OUTLET_CMD":     False,
        "WT_D101_DOSING_CMD":     False,
        "WT_ALM_ACTIVE":          False,
        "plc_state":              "TREATING",
    }


# ── Test: CSV file creation ───────────────────────────────────────────────────

def test_csv_created_with_header():
    """Historian creates CSV with correct header on first instantiation."""
    path = _tmp_csv()
    h = Historian(csv_path=path)
    h.close()
    with open(path, newline="") as f:
        header = next(csv.reader(f))
    assert header == HISTORIAN_COLUMNS, f"Wrong header: {header}"
    print("  PASS  test_csv_created_with_header")


def test_csv_header_not_duplicated_on_reopen():
    """Re-opening an existing non-empty CSV does not add a second header."""
    path = _tmp_csv()
    h = Historian(csv_path=path)
    h.write(_make_tags(0))
    h.flush()
    h.close()

    h2 = Historian(csv_path=path)
    h2.write(_make_tags(1))
    h2.flush()
    h2.close()

    with open(path, newline="") as f:
        rows = list(csv.reader(f))

    # First row = header, second and third = data
    assert rows[0] == HISTORIAN_COLUMNS, "Header row must be first"
    assert len(rows) == 3, f"Expected 3 rows (1 header + 2 data), got {len(rows)}"
    print("  PASS  test_csv_header_not_duplicated_on_reopen")


# ── Test: write and read back ─────────────────────────────────────────────────

def test_write_and_load_recent():
    """Write 10 rows and verify load_recent returns them all."""
    path = _tmp_csv()
    with Historian(csv_path=path) as h:
        for i in range(10):
            h.write(_make_tags(i))
        rows = h.load_recent(10)

    assert len(rows) == 10, f"Expected 10 rows, got {len(rows)}"
    # Verify first and last values
    assert float(rows[0]["WT_T101_LEVEL_PV"]) == 50.0
    assert abs(float(rows[9]["WT_T101_LEVEL_PV"]) - 50.9) < 0.01
    print("  PASS  test_write_and_load_recent")


def test_load_recent_respects_n_limit():
    """load_recent(n) never returns more than n rows."""
    path = _tmp_csv()
    with Historian(csv_path=path) as h:
        for i in range(30):
            h.write(_make_tags(i))
        rows = h.load_recent(10)

    assert len(rows) == 10, f"Expected 10, got {len(rows)}"
    print("  PASS  test_load_recent_respects_n_limit")


def test_load_recent_returns_chronological_order():
    """Rows returned by load_recent are oldest-first."""
    path = _tmp_csv()
    with Historian(csv_path=path) as h:
        for i in range(5):
            h.write(_make_tags(i))
        rows = h.load_recent(5)

    levels = [float(r["WT_T101_LEVEL_PV"]) for r in rows]
    assert levels == sorted(levels), f"Not ascending: {levels}"
    print("  PASS  test_load_recent_returns_chronological_order")


def test_missing_tag_written_as_empty():
    """Tags not in HISTORIAN_COLUMNS are ignored; missing tags become empty."""
    path = _tmp_csv()
    sparse_tags = {"WT_T101_LEVEL_PV": 55.0, "extra_key": "ignored"}
    with Historian(csv_path=path) as h:
        h.write(sparse_tags)
        h.flush()
        rows = h.load_recent(1)

    assert rows[0]["WT_T101_LEVEL_PV"] == 55.0
    assert rows[0]["WT_A101_PH_PV"] == ""   # missing → empty
    print("  PASS  test_missing_tag_written_as_empty")


# ── Test: CSV persistence ─────────────────────────────────────────────────────

def test_rows_persisted_to_csv():
    """After close(), all written rows are present in the CSV file."""
    path = _tmp_csv()
    N = 60
    with Historian(csv_path=path) as h:
        for i in range(N):
            h.write(_make_tags(i))

    # Count data rows in CSV (exclude header)
    with open(path, newline="") as f:
        data_rows = list(csv.DictReader(f))
    assert len(data_rows) == N, f"Expected {N} rows in CSV, got {len(data_rows)}"
    print("  PASS  test_rows_persisted_to_csv")


def test_csv_tail_fallback_after_restart():
    """
    When a new Historian is created with an existing CSV (ring buffer empty),
    load_recent() falls back to reading the CSV tail correctly.
    """
    path = _tmp_csv()
    with Historian(csv_path=path) as h:
        for i in range(20):
            h.write(_make_tags(i))

    # New instance — ring buffer starts empty
    h2 = Historian(csv_path=path)
    rows = h2.load_recent(10)
    h2.close()

    assert len(rows) == 10, f"Expected 10 tail rows, got {len(rows)}"
    # Tail should be the last 10 rows (i=10..19)
    levels = [float(r["WT_T101_LEVEL_PV"]) for r in rows]
    assert all(v >= 51.0 for v in levels), f"Expected tail of run, got {levels}"
    print("  PASS  test_csv_tail_fallback_after_restart")


# ── Test: ring buffer capacity ────────────────────────────────────────────────

def test_ring_buffer_oldest_evicted():
    """
    Writing more than IN_MEMORY_ROWS rows evicts oldest entries from ring.
    Only latest IN_MEMORY_ROWS rows are kept in memory.
    """
    from historian import IN_MEMORY_ROWS
    path = _tmp_csv()
    with Historian(csv_path=path) as h:
        for i in range(IN_MEMORY_ROWS + 100):
            h.write(_make_tags(i))
        rows = h.load_recent(IN_MEMORY_ROWS + 100)

    assert len(rows) == IN_MEMORY_ROWS, (
        f"Ring should hold exactly {IN_MEMORY_ROWS} rows, got {len(rows)}"
    )
    print("  PASS  test_ring_buffer_oldest_evicted")


# ── Test: stats ───────────────────────────────────────────────────────────────

def test_stats_keys_present():
    """stats() returns all expected keys with correct types."""
    path = _tmp_csv()
    with Historian(csv_path=path) as h:
        for i in range(5):
            h.write(_make_tags(i))
        s = h.stats()

    required = {
        "total_rows_written", "rows_in_memory", "rows_in_buffer",
        "oldest_timestamp", "newest_timestamp", "csv_path", "csv_size_kb",
    }
    assert required.issubset(s.keys()), f"Missing keys: {required - s.keys()}"
    assert s["total_rows_written"] == 5
    assert s["rows_in_memory"] == 5
    print("  PASS  test_stats_keys_present")


def test_csv_size_increases_after_flush():
    """CSV file size grows after flushing data."""
    path = _tmp_csv()
    h = Historian(csv_path=path)
    size_before = h.stats()["csv_size_kb"]
    for i in range(20):
        h.write(_make_tags(i))
    h.flush()
    size_after = h.stats()["csv_size_kb"]
    h.close()
    assert size_after > size_before, (
        f"CSV should grow after flush: {size_before} → {size_after} KB"
    )
    print("  PASS  test_csv_size_increases_after_flush")


# ── Test: thread safety ───────────────────────────────────────────────────────

def test_concurrent_writes_no_data_loss():
    """
    Ten threads each write 50 rows concurrently.
    Total rows written must equal 500 with no corruption.
    """
    path = _tmp_csv()
    h = Historian(csv_path=path)
    errors = []

    def _worker(thread_id):
        try:
            for i in range(50):
                tags = _make_tags(i)
                tags["plc_state"] = f"thread_{thread_id}"
                h.write(tags)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_worker, args=(t,)) for t in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    h.close()
    assert not errors, f"Thread errors: {errors}"
    assert h.stats()["total_rows_written"] == 500, (
        f"Expected 500 rows, got {h.stats()['total_rows_written']}"
    )
    print("  PASS  test_concurrent_writes_no_data_loss")


# ── Test: pandas DataFrame ────────────────────────────────────────────────────

def test_load_dataframe_types():
    """load_dataframe() returns correct column dtypes."""
    try:
        import pandas as pd
    except ImportError:
        print("  SKIP  test_load_dataframe_types (pandas not installed)")
        return

    path = _tmp_csv()
    with Historian(csv_path=path) as h:
        for i in range(5):
            h.write(_make_tags(i))
        df = h.load_dataframe(5)

    assert "datetime64" in str(df["timestamp"].dtype), \
        f"Expected datetime64, got {df['timestamp'].dtype}"
    assert str(df["WT_T101_LEVEL_PV"].dtype) == "float64"
    assert df["WT_P101_PUMP_CMD"].dtype == bool
    assert len(df) == 5
    print("  PASS  test_load_dataframe_types")


def test_load_dataframe_empty_when_no_data():
    """load_dataframe() on empty historian returns empty DataFrame."""
    try:
        import pandas as pd
    except ImportError:
        print("  SKIP  test_load_dataframe_empty_when_no_data")
        return
    path = _tmp_csv()
    with Historian(csv_path=path) as h:
        df = h.load_dataframe(10)
    assert len(df) == 0
    print("  PASS  test_load_dataframe_empty_when_no_data")


# ── Test: 60-second simulation (acceptance criterion) ────────────────────────

def test_60_second_sample_run():
    """
    Simulate 60 write() calls (one per second).
    Verify: 60 rows in CSV, all values in valid ranges.
    This directly matches the Day 4 acceptance criterion.
    """
    import config as _cfg
    from process_simulator import ProcessSimulator
    from sensor_simulator  import SensorSimulator
    from plc_controller    import PLCController

    path = _tmp_csv()
    process = ProcessSimulator()
    sensors = SensorSimulator(process)
    plc     = PLCController()
    process.apply_commands(pump_on=True, valve_open=False, dosing_on=False)

    with Historian(csv_path=path) as h:
        for _ in range(60):
            process.step(_cfg.SAMPLE_RATE_S)
            sensor_tags = sensors.read_all_tags()
            commands    = plc.execute(sensor_tags)
            process.apply_commands(
                commands["WT_P101_PUMP_CMD"],
                commands["WT_V101_OUTLET_CMD"],
                commands["WT_D101_DOSING_CMD"],
            )
            full_tags = {
                **sensor_tags,
                "WT_P101_PUMP_CMD":   commands["WT_P101_PUMP_CMD"],
                "WT_V101_OUTLET_CMD": commands["WT_V101_OUTLET_CMD"],
                "WT_D101_DOSING_CMD": commands["WT_D101_DOSING_CMD"],
                "WT_ALM_ACTIVE":      commands["WT_ALM_ACTIVE"],
                "plc_state":          commands["state"],
            }
            h.write(full_tags)

        s = h.stats()
        assert s["total_rows_written"] == 60

    # Verify CSV content
    with open(path, newline="") as f:
        data_rows = list(csv.DictReader(f))

    assert len(data_rows) == 60, f"Expected 60 rows, got {len(data_rows)}"
    for row in data_rows:
        level = float(row["WT_T101_LEVEL_PV"])
        ph    = float(row["WT_A101_PH_PV"])
        assert 0 <= level <= 100, f"Level out of range: {level}"
        assert 4 <= ph <= 10,     f"pH out of range: {ph}"

    print("  PASS  test_60_second_sample_run — 60 rows, all values in range")


# ── Chart data collection ─────────────────────────────────────────────────────

def collect_chart_data():
    """60-step integration run: capture level, pH, state, and row-count growth."""
    import config as _cfg
    from process_simulator import ProcessSimulator
    from sensor_simulator  import SensorSimulator
    from plc_controller    import PLCController

    process = ProcessSimulator()
    sensors = SensorSimulator(process)
    plc     = PLCController()
    process.apply_commands(pump_on=True, valve_open=False, dosing_on=False)

    path = _tmp_csv()
    levels, phs, states, rows_written = [], [], [], []

    with Historian(csv_path=path) as h:
        for i in range(60):
            process.step(_cfg.SAMPLE_RATE_S)
            sensor_tags = sensors.read_all_tags()
            commands    = plc.execute(sensor_tags)
            process.apply_commands(
                commands["WT_P101_PUMP_CMD"],
                commands["WT_V101_OUTLET_CMD"],
                commands["WT_D101_DOSING_CMD"],
            )
            full_tags = {
                **sensor_tags,
                "WT_P101_PUMP_CMD":   commands["WT_P101_PUMP_CMD"],
                "WT_V101_OUTLET_CMD": commands["WT_V101_OUTLET_CMD"],
                "WT_D101_DOSING_CMD": commands["WT_D101_DOSING_CMD"],
                "WT_ALM_ACTIVE":      commands["WT_ALM_ACTIVE"],
                "plc_state":          commands["state"],
            }
            h.write(full_tags)
            levels.append(sensor_tags["WT_T101_LEVEL_PV"])
            phs.append(sensor_tags["WT_A101_PH_PV"])
            states.append(commands["state"])
            rows_written.append(h.stats()["total_rows_written"])

    # State distribution for bar chart
    from collections import Counter
    state_counts = Counter(states)
    return levels, phs, rows_written, state_counts


def save_chart(levels, phs, rows_written, state_counts):
    charts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts")
    os.makedirs(charts_dir, exist_ok=True)
    out_path = os.path.join(charts_dir, "day4_historian.png")

    steps = list(range(1, len(levels) + 1))
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    (ax1, ax2), (ax3, ax4) = axes

    # Top-left: Tank level over 60 steps
    ax1.plot(steps, levels, color="steelblue", linewidth=1.5, label="Level (%)")
    ax1.axhline(40, color="navy",   linestyle="--", linewidth=1.0, label="LOW 40%")
    ax1.axhline(80, color="orange", linestyle="--", linewidth=1.0, label="HIGH 80%")
    ax1.set_title("Day 4 – Historian: Tank Level (60 s integration run)")
    ax1.set_xlabel("Simulation step (s)")
    ax1.set_ylabel("Tank Level (%)")
    ax1.set_ylim(0, 105)
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)

    # Top-right: pH over 60 steps
    ax2.plot(steps, phs, color="green", linewidth=1.5, label="pH")
    ax2.axhline(6.8, color="orange",    linestyle="--", linewidth=1.0, label="DOSE_ON 6.8")
    ax2.axhline(7.3, color="darkgreen", linestyle="--", linewidth=1.0, label="DOSE_OFF 7.3")
    ax2.set_title("Day 4 – Historian: pH (60 s integration run)")
    ax2.set_xlabel("Simulation step (s)")
    ax2.set_ylabel("pH")
    ax2.set_ylim(4, 10)
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3)

    # Bottom-left: Cumulative rows written (ring buffer fill)
    ax3.plot(steps, rows_written, color="purple", linewidth=1.5, label="Rows in historian")
    ax3.set_title("Day 4 – Historian: Cumulative rows written")
    ax3.set_xlabel("Simulation step (s)")
    ax3.set_ylabel("Rows written")
    ax3.set_ylim(0, max(rows_written) * 1.15)
    ax3.legend(fontsize=9)
    ax3.grid(True, alpha=0.3)

    # Bottom-right: PLC state distribution (bar chart)
    STATE_ORDER = ["IDLE", "FILLING", "TREATING", "DISCHARGING", "FAULT"]
    state_names = [s for s in STATE_ORDER if s in state_counts]
    counts      = [state_counts[s] for s in state_names]
    colors      = ["grey", "steelblue", "green", "orange", "red"][:len(state_names)]
    bars = ax4.bar(state_names, counts, color=colors, edgecolor="black", linewidth=0.7)
    for bar, cnt in zip(bars, counts):
        ax4.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                 str(cnt), ha="center", va="bottom", fontsize=9)
    ax4.set_title("Day 4 – Historian: PLC state distribution (60 steps)")
    ax4.set_ylabel("Steps in state")
    ax4.grid(True, axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(out_path, dpi=100)
    plt.close()
    print(f"\nChart saved to: {out_path}")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_csv_created_with_header,
        test_csv_header_not_duplicated_on_reopen,
        test_write_and_load_recent,
        test_load_recent_respects_n_limit,
        test_load_recent_returns_chronological_order,
        test_missing_tag_written_as_empty,
        test_rows_persisted_to_csv,
        test_csv_tail_fallback_after_restart,
        test_ring_buffer_oldest_evicted,
        test_stats_keys_present,
        test_csv_size_increases_after_flush,
        test_concurrent_writes_no_data_loss,
        test_load_dataframe_types,
        test_load_dataframe_empty_when_no_data,
        test_60_second_sample_run,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed out of {len(tests)} tests")
    if failed:
        sys.exit(1)

    data = collect_chart_data()
    save_chart(*data)
