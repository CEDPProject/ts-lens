"""Original source boundaries and completeness checks for stitched displays."""
import numpy as np
import pandas as pd


def sampling_interval(timestamps):
    ts = pd.to_datetime(timestamps, errors="coerce")
    deltas = [b - a for a, b in zip(ts[:-1], ts[1:])
              if not pd.isna(a) and not pd.isna(b) and b > a]
    if not deltas:
        return None
    return pd.Timedelta(pd.Series(deltas).mode().min())


def complete_window(timestamps, values, start, end, interval):
    if interval is None or interval <= pd.Timedelta(0):
        return False
    if interval > end - start or (end - start).value % interval.value:
        return False
    expected_count = (end - start).value // interval.value
    actual = timestamps if isinstance(timestamps, pd.DatetimeIndex) else pd.to_datetime(timestamps, errors="coerce")
    return (expected_count == len(values) == len(actual)
            and len(actual) > 0 and actual[0] == start and not actual.isna().any()
            and (actual[1:] - actual[:-1] == interval).all()
            and np.isfinite(np.asarray(values, dtype=float)).all())


def span_for_window(entry, start, end):
    return next((span for span in entry.get("source_spans", [])
                 if span["start_idx"] <= start <= end < span["end_idx"]), None)


def window_source(entry, start, end):
    """Resolve an inclusive display-index range; never join source spans."""
    span = span_for_window(entry, start, end)
    if span:
        local_start, local_end = start - span["start_idx"], end - span["start_idx"]
        timestamps = span["timestamps"][local_start:local_end + 1]
        return {"source_key": span["source_key"], "source_label": span["source_label"],
                "parent_segment_key": span["parent_segment_key"],
                "start_ts": timestamps[0], "end_ts": timestamps[-1],
                "timestamps": timestamps}
    return None
