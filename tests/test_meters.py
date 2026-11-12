"""The dashboard's dials and trend lines."""

import time

import pytest


@pytest.fixture
def qt():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_a_trend_shows_its_span_and_breaks_across_silences(qt):
    from job_server.ui.meters import SPANS, Trend

    now = 10_000.0
    trend = Trend("%", 100, clock=lambda: now)
    trend.resize(200, 46)
    samples = [(now - 600 + 10 * step, 50.0) for step in range(30)]  # 10 to 5 min ago
    samples += [(now - 100 + 10 * step, 70.0) for step in range(10)]  # the last 100 s
    trend.set_samples(samples, gap=30)
    points = trend._points()
    breaks = [index for index, (_, _, point) in enumerate(points) if point is None]
    assert len(breaks) == 1, "A silence longer than the gap breaks the line once"
    trend.set_span(*SPANS[0])  # 5 min
    assert all(when >= now - 300 for when, _, _ in trend._points())
    assert trend.span_label == "5 min"
    assert all(0 <= point.y() <= 46 for _, _, point in points if point is not None)
    assert trend.describe(0, 37.4).endswith("37%")


def test_a_count_trend_scales_to_its_largest_value(qt):
    from job_server.ui.meters import Trend

    trend = Trend(clock=lambda: 3.0)
    trend.resize(100, 46)
    trend.set_samples([(1.0, 0), (2.0, 4)])
    low, high = (point for _, _, point in trend._points())
    assert high.y() < low.y()
    # The scale is a round number above the largest count, so the line has headroom.
    assert trend._scale() == 5
    from job_server.ui.meters import round_top

    assert [round_top(n) for n in (0, 1, 2, 4, 5, 9, 10, 37, 50)] == [1, 2, 5, 5, 10, 10, 20, 50, 100]
    assert trend.describe(0, 4.0).endswith("— 4")


def test_a_gauge_shows_unknown_until_it_has_a_value(qt):
    from job_server.ui.meters import Gauge

    gauge = Gauge()
    assert not gauge._known
    gauge.setValue(150)
    gauge.setKnown(True)
    assert gauge.value() == 100
    gauge.grab()  # Paints without error.


def test_the_dashboard_draws_the_server_history_and_switches_span(tmp_path, qt, monkeypatch):
    from job_server.settings import Settings
    from job_server.ui.dashboard import Dashboard

    config = tmp_path / "settings.json"
    Settings(data_dir=str(tmp_path)).save(config)
    board = Dashboard(config=config)
    board.timer.stop()
    try:
        board._set_usage("cpu", 6, 8)
        assert board.metric_bars["cpu"].value() == 75 and board.metric_bars["cpu"]._known
        board._set_usage("cpu", None, None)
        assert not board.metric_bars["cpu"]._known
        now = time.time()
        board._merge_history({"interval": 10, "keep_seconds": 21600, "samples": [
            [now - 20, 1, 0, 30.0, 40.0, 25.0, 10.0],
            [now - 10, 2, 1, 35.0, 41.0, 50.0, 20.0],
        ]})
        assert board.metric_trends["running"].samples[-1][1] == 2
        assert board.metric_trends["memory"].samples[0][1] == 10.0
        board._merge_history({"interval": 10, "samples": [[now - 10, 9, 9, 9, 9, 9, 9]]})
        assert len(board.history_rows) == 2, "A sample already held is not added twice"
        board.span_buttons.buttons()[2].click()
        assert board.metric_trends["cpu"].span_label == "1 h"
        board._clear_dashboard()
        assert all(trend.samples == [] for trend in board.metric_trends.values())
    finally:
        board.close()


def test_cpu_trend_marks_where_every_core_is_busy(qtbot=None):
    from job_server.ui.meters import Trend

    trend = Trend("%", 100, clock=lambda: 3.0)
    trend.resize(200, 80)
    trend.set_marker(100 * 8 / 12, "8 cores")
    assert trend.marker == (100 * 8 / 12, "8 cores")
    trend.grab()  # paints the dotted line and its label without error
    trend.set_marker(None)
    assert trend.marker is None
