"""The memory-depth probe: what the instrument reports back is the answer."""

from __future__ import annotations

import pytest
from probe_memory_depth import CANDIDATES, Probe, probe_depths, render

from cetal_scopes.scopes.siglent import SiglentSDS6204L


class DepthOnlyTransport:
    """A scope that offers only `offered`, ignoring any other depth written."""

    def __init__(
        self, offered: set[str], *, depth: str = "1M", mode: str = "FMDepth"
    ) -> None:
        self.offered = offered
        self.depth = depth
        self.mode = mode
        self.window = 200e-6

    def open(self) -> None: ...

    def close(self) -> None: ...

    def write(self, command: str) -> None:
        if command.startswith(":ACQuire:MMANagement "):
            self.mode = command.split(" ", 1)[1]
        if command.startswith(":ACQuire:MDEPth "):
            # In AUTO the instrument picks memory itself and ignores this.
            if self.mode.upper().startswith("AUTO"):
                return
            asked = command.split(" ", 1)[1]
            if asked in self.offered:
                self.depth = asked

    def query(self, command: str) -> str:
        if command == "*IDN?":
            return "Siglent,SDS6204L,TEST,1.0"
        if command == ":ACQuire:MDEPth?":
            return self.depth
        if command == ":ACQuire:SRATe?":
            scale = {"k": 1e3, "M": 1e6, "G": 1e9}[self.depth[-1]]
            return str(float(self.depth[:-1]) * scale / self.window)
        if command == ":TIMebase:SCALe?":
            return "2.00E-05"
        if command == ":ACQuire:MMANagement?":
            return self.mode
        raise AssertionError(f"unexpected query {command!r}")

    def query_block(self, command: str) -> bytes:  # pragma: no cover - unused
        raise AssertionError(command)


def probe_with(
    offered: set[str], *, starting_at: str = "700k", mode: str = "FMDepth"
) -> list[Probe]:
    # Start somewhere that is not a candidate, so "reads back as asked" can
    # only mean the write landed -- not that the scope was already there.
    transport = DepthOnlyTransport(offered, depth=starting_at, mode=mode)
    scope = SiglentSDS6204L(channels=("C1",), transport=transport)
    scope.connect()
    return probe_depths(scope)


def test_a_depth_the_scope_offers_reads_back_unchanged() -> None:
    results = {item.asked: item for item in probe_with({"10k", "1M"})}
    assert results["10k"].accepted
    assert results["1M"].accepted


def test_a_depth_the_scope_refuses_leaves_the_previous_value() -> None:
    """The rejection is silent, which is exactly why the probe reads back."""
    results = {item.asked: item for item in probe_with({"10k", "1M"})}
    assert not results["250k"].accepted
    assert not results["2.5M"].accepted


def test_the_ladder_the_docs_assume_is_distinguishable_from_the_dense_one() -> None:
    sparse = {item.asked for item in probe_with({"10k", "1M"}) if item.accepted}
    dense = {
        item.asked
        for item in probe_with({"10k", "100k", "250k", "1M"})
        if item.accepted
    }
    assert sparse == {"10k", "1M"}
    assert dense == {"10k", "100k", "250k", "1M"}


def test_render_emits_a_pasteable_enum_of_what_was_offered() -> None:
    text = render(probe_with({"10k", "250k", "1M"}), window=200e-6)
    assert "MDEPTH_ENUM: tuple[tuple[int, str], ...] = (" in text
    assert "(10_000, '10k')," in text
    assert "(250_000, '250k')," in text
    assert "(1_000_000, '1M')," in text
    assert "not offered" in text


def test_render_says_so_when_nothing_was_accepted() -> None:
    text = render(probe_with(set()), window=200e-6)
    assert "No candidate was accepted" in text


def test_a_depth_the_scope_already_sits_at_counts_as_offered() -> None:
    """It is using that depth, so it has it -- however the probe got there."""
    results = {item.asked: item for item in probe_with(set(), starting_at="1M")}
    assert results["1M"].accepted


@pytest.mark.parametrize("label", CANDIDATES)
def test_every_candidate_label_is_understood_by_the_normaliser(label: str) -> None:
    results = {item.asked: item for item in probe_with({label})}
    assert results[label].accepted


def test_auto_mode_ignores_every_depth_write() -> None:
    """The failure the first real probe run hit: AUTO measures nothing."""
    results = probe_with({"10k", "250k", "1M"}, mode="AUTO")
    assert not any(item.accepted for item in results)


def test_a_fixed_depth_mode_is_what_makes_the_ladder_measurable() -> None:
    offered = {"10k", "250k", "1M"}
    auto = {i.asked for i in probe_with(offered, mode="AUTO") if i.accepted}
    fixed = {i.asked for i in probe_with(offered, mode="FMDepth") if i.accepted}
    assert auto == set()
    assert fixed == offered
