"""The dashboard's dials and trend lines."""

import pytest


@pytest.fixture
def qt():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_a_trend_keeps_fifteen_minutes_and_marks_gaps(qt):
    from job_server.ui.meters import HISTORY_SECONDS, Trend

    trend = Trend("%", 100)
    trend.resize(200, 30)
    for step in range(400):
        trend.add(None if step == 399 else step % 100, when=step * 3.0)
    assert trend.samples[0][0] >= 399 * 3.0 - HISTORY_SECONDS
    assert trend.samples[-1][1] is None
    points = trend._points()
    assert points[-1][2] is None, "A gap draws no point"
    assert all(0 <= point.y() <= 30 for _, _, point in points if point is not None)
    assert trend.describe(0, 37.4).endswith("37%")


def test_a_count_trend_scales_to_its_largest_value(qt):
    from job_server.ui.meters import Trend

    trend = Trend()
    trend.resize(100, 30)
    trend.add(0, when=1.0)
    trend.add(4, when=2.0)
    low, high = (point for _, _, point in trend._points())
    assert high.y() < low.y()
    assert trend._scale() == 4
    assert trend.describe(0, 4.0).endswith("— 4")


def test_a_gauge_shows_unknown_until_it_has_a_value(qt):
    from job_server.ui.meters import Gauge

    gauge = Gauge()
    assert not gauge._known
    gauge.setValue(150)
    gauge.setKnown(True)
    assert gauge.value() == 100
    gauge.grab()  # Paints without error.


def test_the_dashboard_records_history_and_a_gap_when_offline(tmp_path, qt, monkeypatch):
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
        board._clear_dashboard()
        assert all(trend.samples[-1][1] is None for trend in board.metric_trends.values())
    finally:
        board.close()
