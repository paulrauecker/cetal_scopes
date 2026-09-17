"""The timebase map: which windows the scope samples rather than interpolates."""

from __future__ import annotations

from probe_timebase_map import Point, render, sweep

from cetal_scopes.scopes.siglent import SiglentSDS6204L


class AutoMemoryTransport:
    """A scope in AUTO: it maximises the rate, capped by a memory depth."""

    def __init__(
        self,
        *,
        max_rate: float = 10e9,
        max_depth: float = 2.5e6,
        stale: bool = False,
    ) -> None:
        self.max_rate = max_rate
        self.max_depth = max_depth
        self.timebase = 5e-6
        # A stopped scope reports its last acquisition, not the pending one.
        self.stale = stale
        self.latched = 5e-6
        self.acquisitions = 0

    def open(self) -> None: ...

    def close(self) -> None: ...

    def write(self, command: str) -> None:
        if command.startswith(":TIMebase:SCALe "):
            self.timebase = float(command.split(" ", 1)[1])
        if command == ":TRIGger:RUN" and not self.stale:
            self.acquisitions += 1
            self.latched = self.timebase

    @property
    def rate(self) -> float:
        return min(self.max_rate, self.max_depth / (self.latched * 10))

    def query(self, command: str) -> str:
        if command == "*IDN?":
            return "Siglent,SDS6204L,TEST,1.0"
        if command == ":TIMebase:SCALe?":
            return f"{self.timebase:.6E}"
        if command == ":ACQuire:SRATe?":
            return f"{self.rate:.6E}"
        if command == ":ACQuire:MDEPth?":
            return "2.5M"
        if command == ":ACQuire:NUMACq?":
            return str(self.acquisitions)
        if command == ":TRIGger:MODE?":
            return "SINGle"
        raise AssertionError(f"unexpected query {command!r}")

    def query_block(self, command: str) -> bytes:  # pragma: no cover - unused
        raise AssertionError(command)


def swept(
    *, max_rate: float = 10e9, max_depth: float = 2.5e6, stale: bool = False
) -> list[Point]:
    transport = AutoMemoryTransport(max_rate=max_rate, max_depth=max_depth, stale=stale)
    scope = SiglentSDS6204L(channels=("C1",), transport=transport)
    scope.connect()
    return sweep(scope, timeout=0.0)


def test_short_windows_are_interpolated_and_long_ones_are_not() -> None:
    points = swept()
    assert not points[0].honest  # 1 ns/div: 10 GS/s, half of it invented
    assert points[-1].honest  # 1 ms/div: memory forces the rate down


def test_the_crossover_is_where_the_rate_stops_being_pinned_at_the_maximum() -> None:
    """2.5 Mpt at 5 GS/s is a 500 us window, so 50 us/div is the boundary."""
    honest = [item for item in swept() if item.honest]
    assert min(item.window for item in honest) == 500e-6


def test_points_are_the_rate_times_the_window() -> None:
    for item in swept():
        assert item.points == item.sample_rate * item.window


def test_render_names_the_shortest_measured_window_and_the_toml_line() -> None:
    text = render(swept())
    assert "Shortest fully-measured window: 500 us" in text
    assert 'record_length = "500us"' in text
    assert "INTERPOLATED" in text


def test_render_says_so_when_every_window_is_interpolated() -> None:
    """Memory big enough to push the crossover past the end of the sweep.

    The rate still varies across the sweep -- otherwise this would be the
    stale-read-back case, which is a different message.
    """
    points = swept(max_depth=6e7)
    assert len({item.sample_rate for item in points}) > 1
    assert not any(item.honest for item in points)
    assert "Every window in this sweep is interpolated" in render(points)


def test_a_scope_that_never_triggers_is_reported_as_stale() -> None:
    """The second real run: identical rate at every timebase means stale."""
    points = swept(stale=True)
    assert not any(item.acquired for item in points)
    text = render(points)
    assert "No acquisition completed" in text
    assert "stale: never triggered" in text


def test_an_unchanging_rate_is_called_out_even_when_acquisitions_land() -> None:
    text = render(swept(max_depth=1e18))
    assert "not tracking the setup" in text
