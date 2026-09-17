# Bench probes

Small scripts that ask an instrument what it actually does, rather than what
its programming guide says it does. Each one exists because a documented
command turned out to behave differently on real hardware, and each prints a
result you can paste into the driver or into `bench.toml`.

They live in `apps/capture_studio/` because the capture studio's planning is
what depends on the answers. None of them arms an acquisition or touches the
trigger configuration; every setting they change is restored on the way out,
including after `Ctrl-C`.

## Why probes rather than the manual

The SDS6204L investigation is the cautionary tale. The driver derived memory
depth from a table, rounded it up to the next step, and wrote it on every
`configure()`. Three separate diagnoses were built on that arithmetic — a
wrong ladder, a depth overshoot, then memory management blocking the write —
and all three were wrong for the same reason: **the write was being rejected
and never reached the instrument at all.**

What settled it was not more reasoning. It was `*ESR?`.

### The three questions a probe has to separate

A setting write on a SCPI instrument can fail in three ways that look
identical from the outside, because a write is not acknowledged:

| | read-back moves | `*ESR?` bit 4 | meaning |
|---|---|---|---|
| took effect | yes | clear | the command works |
| rejected | no | **set** | wrong command, or wrong parameter format |
| accepted, ignored | no | clear | understood, but declined in this state |

Reading `*ESR?` clears it, so bracket each write: read once to clear, write,
read again. Without that bit, "rejected" and "ignored" are indistinguishable,
and they have opposite fixes.

### Two traps worth knowing before writing another probe

**Always include a control.** A write known to work — `:TIMebase:SCALe` on
this scope — proves the write path is healthy before you conclude anything
about the command under test. Had the first probe included one, it would have
been obvious immediately that the connection was fine and the command was not.

**A stopped instrument may describe its past.** On the SDS6204L,
`:ACQuire:SRATe?` and `:ACQuire:MDEPth?` report the *last completed
acquisition*, not the setup you just wrote. A probe that stops the scope
first therefore measures nothing, and — worse — returns plausible-looking
constant values. If a swept parameter comes back identical at every step,
suspect the read, not the instrument.

## The probes

### `probe_memory_depth.py`

Walks candidate `:ACQuire:MDEPth` values and reads each back, under each
`:ACQuire:MMANagement` mode.

```bash
uv run apps/capture_studio/probe_memory_depth.py --address 192.168.5.171 --channels C1,C2,C3
```

**Measured on an SDS6204L, firmware 18.36.11.2.0.3.7:** nothing is offered.
Every candidate reads back unchanged, in all three memory-management modes,
and the instrument sits at a depth (`2.5M`) that the driver's table does not
even contain.

### `probe_write_path.py`

Answers *why* a write has no effect: control, parameter spellings, and long
versus short command forms, each bracketed by `*ESR?`.

```bash
uv run apps/capture_studio/probe_write_path.py --address 192.168.5.171
```

**Measured:**

| command | result |
|---|---|
| `:TIMebase:SCALe 2e-05` | took effect (control) |
| `:ACQuire:MDEPth` — `10k`, `10K`, `10000`, `1.0E+04`, `1M`, short form | **rejected**, every spelling |
| `:ACQuire:MMANagement FMDepth` / `FSRate` | accepted, ignored |
| `:ACQ:MMAN FMDE` | rejected (short form unsupported) |

So memory depth is readable but **not writable**, memory management cannot be
changed, and the instrument is permanently in `AUTO` — choosing both sample
rate and record depth itself. The timebase is the only acquisition knob.

### `probe_timebase_map.py`

Given that, the useful question becomes "for a given window, what does the
scope choose?" This sweeps the timebase, letting each step acquire before
reading back.

```bash
uv run apps/capture_studio/probe_timebase_map.py --address 192.168.5.171 --channels C1,C2,C3
```

**Measured (3 channels):**

| window | rate | points | |
|---|---|---|---|
| ≤ 200 µs | 10 GS/s | rate × window | interpolated ("ESR") |
| **500 µs** | **5 GS/s** | **2.5 Mpt** | **fully measured** |
| 1 ms | 1 GS/s | 1 Mpt | measured |
| 2 ms | 1 GS/s | 2 Mpt | measured |
| 5 ms | 0.5 GS/s | 2.5 Mpt | measured |
| 10 ms | 0.25 GS/s | 2.5 Mpt | measured |

The scope maximises the rate, which means taking the interpolated 10 GS/s
whenever memory allows. **500 µs is the shortest window it samples rather than
interpolates** — the point where 10 GS/s would overrun memory and it falls
back to the native 5 GS/s per channel. That is why `bench.toml` uses
`record_length = "500us"`.

Note the rate ladder is coarse and not purely memory-bound: 10, 5, 1, 0.5,
0.25 GS/s, with nothing at 2.5, and the 1 ms window filling only 1 Mpt of an
available 2.5M.

A shorter window is still perfectly usable and loses no real information — the
ADC runs at 5 GS/s either way — but half the points it returns are padding.
Decimating by two recovers the measured samples.

## What this changed in the driver

- `set_acquisition` writes **only** `:TIMebase:SCALe`. It previously also sent
  `:ACQuire:MDEPth`, which achieved nothing and latched a command error on
  every `configure()`.
- `MDEPTH_ENUM` survives to describe what *would* be requested;
  `AcquisitionPlan.memory_depth` is intent, not a setting that was applied.
- `WaveDesc`, read at acquire time, is the only source of truth for sample
  rate and record length.
- `SiglentSDS6204L` gained the read-backs the probes needed:
  `sample_rate()`, `memory_depth()`, `timebase()`, `memory_management()` and
  `event_status()`, plus `raw_write` / `raw_query` as explicit bench-probing
  escape hatches.

## Related instrument findings

Two on the M5i.3367-x16, found the same way — see
[its overview](scopes/M5i.3367-x16/index.md):

- Sample rates are `base / 2ⁿ` only, so a "round" 2 GS/s is not reachable and
  silently becomes 1.25 GS/s. Capture metadata now carries
  `sample_rate_requested_hz` beside the read-back so the gap is visible.
- The pretrigger is on the 32-sample grid too, not just the record length.
  Half of a 32-aligned record is only 32-aligned when the record is
  64-aligned, so `record_length = 250000` produced a pretrigger the card
  rejected with `ERR_VALUE`.

The general lesson both share: **record what was asked alongside what was
achieved, and surface the difference.** The capture studio's shot summary does
this for sample rate, record length and trigger level, which is how the
remaining discrepancies were spotted at all.
