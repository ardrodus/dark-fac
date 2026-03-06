"""Sparkline widget unit tests (TS-009: UN-41 through UN-44)."""

from __future__ import annotations

from dark_factory.ui.widgets.sparkline import Sparkline, SparklineProtocol


class TestSparklineData:
    """UN-41 through UN-43: Data management."""

    def test_un41_push_and_data(self) -> None:
        """UN-41: push(5.0) adds data point; data returns [5.0]."""
        spark = Sparkline()
        spark.push(5.0)
        assert list(spark.data) == [5.0]

    def test_un42_maxlen_100_bound(self) -> None:
        """UN-42: Push 101 values; only most recent 100 retained (deque maxlen)."""
        spark = Sparkline()
        for i in range(101):
            spark.push(float(i))
        assert len(spark.data) == 100
        # Oldest value (0.0) should have been evicted
        assert spark.data[0] == 1.0

    def test_un43_clear_empties_data(self) -> None:
        """UN-43: clear() empties all data."""
        spark = Sparkline()
        spark.push(1.0)
        spark.push(2.0)
        spark.clear()
        assert len(spark.data) == 0


def test_un44_conforms_to_protocol() -> None:
    """UN-44: Sparkline conforms to SparklineProtocol."""
    spark = Sparkline()
    assert isinstance(spark, SparklineProtocol)
