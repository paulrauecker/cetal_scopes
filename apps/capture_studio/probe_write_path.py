"""Find out why setting writes to the SDS6204L have no effect.

The memory-depth probe found that ``:ACQuire:MDEPth`` and
``:ACQuire:MMANagement`` both read back unchanged after every write, while
every *query* answers correctly. That rules out the connection and rules out
the depth ladder; what is left is either that the instrument is rejecting the
commands, or that it is accepting them and declining to act.

``*ESR?`` bit 4 (value 16) tells those apart: it latches when the instrument
did not understand a command or would not take its parameter. Reading clears
it, so each write here is bracketed by a read.

The probe runs three groups:

1. **A control.** ``:TIMebase:SCALe`` is a setting the driver has used
   successfully for real captures. If this does not stick either, the problem
   is not specific to acquisition memory and nothing below matters.
2. **Spellings.** The same depth written several ways -- ``10k``, ``10K``,
   ``10000``, ``1.0E+04`` -- since a parameter format the instrument dislikes
   looks exactly like a command it ignores.
3. **Long and short forms** of the two commands in question, in case this
   firmware wants one and not the other.

Nothing is armed and no trigger is touched. Settings are restored on the way
out, including after Ctrl-C.

    uv run apps/capture_studio/probe_write_path.py --address 192.168.5.171
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

from cetal_scopes.scopes.siglent import SiglentSDS6204L

__all__ = ["Attempt", "build_parser", "main", "try_write"]

#: ``*ESR?`` bit 4: the instrument did not understand the command.
COMMAND_ERROR = 16


@dataclass(frozen=True)
class Attempt:
    """One write, and everything the instrument said about it."""

    command: str
    query: str
    before: str
    after: str
    esr: int

    @property
    def changed(self) -> bool:
        """Whether the read-back moved at all."""
        return self.before.strip() != self.after.strip()

    @property
    def command_error(self) -> bool:
        """Whether the instrument latched a command error for this write."""
        return bool(self.esr & COMMAND_ERROR)

    @property
    def verdict(self) -> str:
        """What this attempt proves, in a word."""
        if self.command_error:
            return "REJECTED (command error)"
        if self.changed:
            return "took effect"
        return "accepted, ignored"


def try_write(scope: SiglentSDS6204L, command: str, query: str) -> Attempt:
    """Write one command and report whether it was understood and whether it stuck."""
    before = scope.raw_query(query)
    scope.event_status()  # read to clear, so the next read is about this write
    scope.raw_write(command)
    esr = scope.event_status()
    return Attempt(
        command=command,
        query=query,
        before=before,
        after=scope.raw_query(query),
        esr=esr,
    )


def attempts_for(current_timebase: float) -> list[tuple[str, str]]:
    """The (command, query) pairs to try, as (control, spellings, forms)."""
    other = 2e-5 if abs(current_timebase - 2e-5) > 1e-12 else 5e-6
    return [
        # 1. Control: a setting known to work in real captures.
        (f":TIMebase:SCALe {other:.6g}", ":TIMebase:SCALe?"),
        # 2. Parameter spellings for one depth.
        (":ACQuire:MDEPth 10k", ":ACQuire:MDEPth?"),
        (":ACQuire:MDEPth 10K", ":ACQuire:MDEPth?"),
        (":ACQuire:MDEPth 10000", ":ACQuire:MDEPth?"),
        (":ACQuire:MDEPth 1.0E+04", ":ACQuire:MDEPth?"),
        (":ACQuire:MDEPth 1M", ":ACQuire:MDEPth?"),
        # 3. Long and short command forms.
        (":ACQ:MDEP 10k", ":ACQ:MDEP?"),
        (":ACQuire:MMANagement FMDepth", ":ACQuire:MMANagement?"),
        (":ACQuire:MMANagement FMDEPTH", ":ACQuire:MMANagement?"),
        (":ACQ:MMAN FMDE", ":ACQ:MMAN?"),
        (":ACQuire:MMANagement FSRate", ":ACQuire:MMANagement?"),
    ]


def render(results: Sequence[Attempt]) -> str:
    """A table of what each write did, and a one-line reading of the whole run."""
    lines = [
        f"{'command':<34} {'before':>10} {'after':>10} {'ESR':>4}  verdict",
        "-" * 88,
    ]
    for item in results:
        lines.append(
            f"{item.command:<34} {item.before.strip():>10} "
            f"{item.after.strip():>10} {item.esr:>4}  {item.verdict}"
        )

    control, rest = results[0], results[1:]
    lines.append("")
    if not control.changed:
        lines.append(
            "The CONTROL write did not stick either, so this is not about "
            "memory depth: no setting write is reaching the instrument. "
            "Check for a remote lock or a front-panel dialog holding the "
            "acquisition settings."
        )
    elif any(item.command_error for item in rest):
        taken = [item.command for item in rest if item.changed]
        lines.append(
            "Some spellings were REJECTED outright, so the parameter format "
            "is the problem, not the command."
        )
        lines.append(
            f"Spellings that took effect: {taken or 'none -- try the front panel'}"
        )
    elif any(item.changed for item in rest):
        lines.append(f"These took effect: {[i.command for i in rest if i.changed]}")
    else:
        lines.append(
            "Every acquisition write was accepted without a command error and "
            "changed nothing. The instrument understands them and is "
            "declining to act -- most likely the depth is not settable in "
            "this acquisition state (check Sequence mode, or a running "
            "acquisition that :TRIGger:STOP did not actually stop)."
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Find out why SDS6204L setting writes have no effect."
    )
    parser.add_argument(
        "--address", default="192.168.5.171", help="Instrument address."
    )
    parser.add_argument("--port", type=int, default=5025, help="Socket port.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the write-path probe. Returns a process exit status."""
    args = build_parser().parse_args(argv)

    scope = SiglentSDS6204L(address=args.address, port=args.port, channels=("C1",))
    try:
        scope.connect()
    except OSError as exc:
        print(f"cannot reach {args.address}:{args.port}: {exc}")
        return 2

    try:
        print(f"instrument: {scope.idn}")
        was_depth = scope.memory_depth()
        was_timebase = scope.timebase()
        was_mode = scope.raw_query(":ACQuire:MMANagement?").strip()
        print(f"before: depth {was_depth}, {was_timebase:g} s/div, mode {was_mode}")
        print(f"sequence mode: {scope.raw_query(':ACQuire:SEQuence?').strip()}")
        print(f"trigger status: {scope.trigger_status()}\n")

        try:
            scope.abort()
            results = [
                try_write(scope, command, query)
                for command, query in attempts_for(was_timebase)
            ]
            print(render(results))
        finally:
            scope.raw_write(f":ACQuire:MMANagement {was_mode}")
            scope.set_memory_depth(was_depth)
            scope.set_timebase(scale=was_timebase)
    finally:
        scope.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
