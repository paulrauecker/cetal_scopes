# DEBUGGING.md — session handoff

Context for continuing work on `cetal_scopes`. Earlier session: built the Siglent
driver, downstream analysis, and characterized the scope's ADC spurs. Latest
session: fixed `_fetch_codes` for the memory-vs-screen point mismatch (unblocks
the sample-rate sweep). Working tree has uncommitted changes on `main` (`fab7c4d`).

---

## TL;DR / where we are

- `cetal_scopes` is a vendor-agnostic scope/digitizer capture + analysis library.
- Implemented and unit-tested: `Capture`, `Channel`, `Scope` ABC, the
  `SiglentSDS6204L` driver, `cetal_scopes.analysis`, `cetal_scopes.plotting`.
- Still **not** implemented (milestone 1 remainder): `Antenna`, metadata models,
  `load_capture`/`save_capture` (`io.py`), `Shot`.
- Hardware is validated: SDS6204L over LAN, multi-channel capture works.
- The ADC comb is **root-caused**: a 256-sample pattern from the 16-bit transfer
  path, spurs at `k*fs/256`. The driver defaults to `WORD`
  (`sample_width="BYTE"` is a lossy top-byte truncation, not an escape), and
  `analysis.remove_adc_comb()` removes the comb in software. See the comb
  section below for the full picture.

---

## Hardware facts

- Instrument: **Siglent SDS6204L**, LAN raw socket. The address is DHCP and has
  moved; it is currently **`192.168.5.197:5025`** (the driver's
  `DEFAULT_ADDRESS` still says `.193`).
- `*IDN?` → `Siglent Technologies,SDS6204L,SDS6LA3CA00028,18.36.11.2.0.3.7`.
- Observed defaults on the bench unit: 1 V/div, 1 µs/div, **10 GS/s**,
  `:ACQuire:POINts?` = 100 000, `:ACQuire:MDEPth?` = 2.5M,
  `:ACQuire:MMANagement?` = `AUTO`, `:ACQuire:MODE?` = `YT`,
  `:ACQuire:TYPE?` = `NORMal`, channels C1–C4 `SWITch ON`.
- The front panel previously had C2–C4 **OFF**; the driver can turn them on with
  `:CHANnel<n>:SWITch ON`.
- Antennas on C1(X), C2(Y), C3(Z); C4 is the open/no-antenna reference channel.
  Source was a 500 MHz tone at +10 dBm.

### Connection / transports
- Raw socket (stdlib, primary): `TCPIP::<ip>::5025::SOCKET` or plain IP.
  **Every command must end with `\n`.** Parsed by `scopes/siglent.py`.
- PyVISA fallback (`_VisaTransport`, lazy import) for `USB…` / `…::INSTR`.
- `pyvisa` + `pyvisa-py` are hard deps; `scipy` + `matplotlib` for analysis.

---

## Commands / tooling

```bash
uv sync --all-groups          # direnv does this automatically
uv run ruff check src tests
uv run ruff format src tests  # CI enforces --check
uv run pyright src
uv run pytest                 # coverage on by default
uv run mkdocs build           # docs
```

Single test example: `uv run pytest tests/test_siglent.py -q`.

Python 3.12, `uv_build` backend, src layout, NumPy docstrings, CI on `main`
only. No ruff/pyright config (defaults; ruff line length 88, double quotes).

---

## What's implemented (module map)

```
src/cetal_scopes/
  __init__.py          # exports Capture, Channel, Scope, SiglentSDS6204L
  capture.py           # Capture: 2D raw/volts, t0, dt, channels dict
  channel.py           # Channel: row-view + derived .time
  scopes/
    base.py            # Scope ABC (connect/configure/acquire/close + ctx mgr)
    siglent.py         # SiglentSDS6204L, transports, WAVEDESC parsing
  analysis/
    results.py         # Spectrum, ChannelStats, Tone
    time.py            # gate, subtract_baseline, detrend, resample, remove_adc_comb
    spectral.py        # window_values, fft, band_amplitude, tone_amplitude
    analytic.py        # analytic_signal, envelope, inst. phase/frequency
    metrics.py         # stats
    _util.py           # DetrendMode, with_volts
  plotting.py          # plot_capture, plot_spectrum
```

Tests: `tests/test_capture.py`, `test_channel.py`, `test_scope_base.py`,
`test_siglent.py`, `test_analysis_{time,spectral,analytic,metrics}.py`,
`test_plotting.py`. 139 passing, ~94% coverage.

### Analysis API cheat-sheet
```python
from cetal_scopes.analysis import (
    gate, subtract_baseline, detrend, resample,          # time
    remove_adc_comb,                                      # ADC comb removal
    fft, band_amplitude, tone_amplitude, window_values,  # spectral
    analytic_signal, envelope,                            # analytic
    instantaneous_phase, instantaneous_frequency,
    stats,                                                # metrics
    Spectrum, ChannelStats, Tone,
)
```
- `fft(channel, window="hann", detrend="constant") -> Spectrum`
  (`freq`, one-sided `amplitude`, one-sided `psd`, `df`, `peak()`).
- `band_amplitude(channel, low=…, high=…) -> float` — max amplitude in a band
  (right tool when the tone frequency is unknown).
- `tone_amplitude(channel, frequency) -> Tone` — **coherent** detection;
  returns `amplitude` and `phase` (radians). Immune to the combs. Use for known
  tones and for any phase/vector work.
- `remove_adc_comb(channel, period=256) -> Channel` — subtracts the 256-phase
  pattern that produces the `k*fs/256` comb (DC preserved). Use for broadband /
  unknown-tone spectra; not needed for coherent `tone_amplitude`.
- `envelope(channel) -> Channel` — Hilbert envelope (drop `raw`).

---

## Git history (most recent first)

```
81e6485 Harden Siglent block reads against prefixes and desync
2176ba6 Document Siglent self-cal behaviour
592d8fd Add band and coherent tone analysis
6acf6ad Harden Siglent acquisition and socket reads
bdf259d Add downstream analysis and plotting
2a69e89 Fix Siglent timebase enum and make acquisition counter-based
c9a7da6 Add Siglent SDS6204L driver with socket and VISA transports
fadd1a2 Add Scope ABC driver template
2ac065a Add Capture and Channel containers
```

---

## Hardware gotchas (hard-won)

### Self-calibration — `:SYSTem:SELFCal`
- **Asynchronous and slow** (minutes; front panel: `doing self cal ... NN%`).
- **No reliable SCPI progress or completion signal:**
  - `*OPC?` returns `1` immediately.
  - `:SYSTem:SELFCal?` returns **`DONE` even mid-run** (observed at 78%!). Do not
    trust it.
  - `STATus:OPERation` / `:CONDition?` stay `0`.
  - `:SYSTem:SELFCal:PROGress?` does not exist (command error → `*ESR?` bit 5).
- While it runs, waveform queries time out and `:ACQuire:NUMACq?` may freeze.
  Wait for the front panel.
- Best practice: quiet inputs before calibrating; re-run after warm-up.
- After a run/glitch, `acquire()` may need `:TRIGger:STOP` before `RUN` to
  re-arm (the driver now does this).

### Streaming vs one-shot acquisition (`streaming=True`)
- `acquire()` defaults to one-shot: `STOP` -> `MODE` -> `RUN` -> wait -> `STOP`,
  then fetch. The stop/run toggle every frame makes the front panel flicker
  between `Auto` and `Stop`.
- `streaming=True` starts the scope once and leaves it running; later calls
  wait for the next `:ACQuire:NUMACq?` and fetch without stopping.
  `:TRIGger:STATus?` re-arms a scope that was stopped externally.
- **Gotcha:** `:TRIGger:RUN` **resets** `:ACQuire:NUMACq?` to `0` (`STOP` does
  not). Read the counter *after* arming, or the wait targets a stale, larger
  value and times out once the scope was already running.
- Bench A/B (C1, 100 k samples = 200 000 `DATA` bytes, `AUTO`, 1 µs/div,
  10 GS/s), medians of 6, interleaved:
  - one-shot: total **257 ms** = wait 47 ms + fetch 179 ms
  - streaming: total **316 ms** = wait 2 ms + **fetch 307 ms**
  Both modes transfer identical bytes, so the fetch really is ~1.6x slower
  while the scope is acquiring/displaying (acquisition + display contend with
  the SCPI readout). Streaming removes the `Auto`/`Stop` stutter and the ~50 ms
  poll wait, but is ~20% slower per frame. The viewer defaults to one-shot
  (faster, one coherent frame); `--stream` keeps the scope live.

### Vertical scale (V/div) range
- Measured on this unit: a **1-2-5 ladder** from **0.5 mV/div** to
  **10 V/div** at 1 MΩ; the 50 Ω path tops out at **1 V/div**. Out-of-range
  values clamp silently (below 0.5 mV -> 0.5 mV, above the max -> max).
- Standard default: **1 V/div**. For small signals use the most sensitive
  setting that keeps peaks within ~±4 divisions: it both avoids clipping and
  shrinks the fixed-code ADC comb relative to the signal (the comb scales with
  V/div).

### ADC comb ("the comb") — root-caused
- The SDS6204L is an **8-bit instrument**; its 16-bit `WORD` (HD transfer) path
  adds a deterministic pattern with a **256-sample period**, so the spurs sit at
  every multiple of `fs/256` (~39.06 MHz at 10 GS/s). `fs/8` (1.25 GHz), `fs/4`
  (2.5 GHz) and `fs/2` (5 GHz) are just the strongest harmonics — there are many
  more (verified by phase-mean removal for `P = 8,16,32,64,128,256`).
- Present on **every channel including open C4**; not from the analog front end
  (a 20 MHz `:CHANnel:BWLimit` does not remove it) and not removed by self-cal.
  `:ACQuire:RESolution` is locked at `16Bits` (8/10/12 are rejected).
- **Width does not matter.** `:WAVeform:WIDTh BYTE` returns exactly the top byte
  of the 16-bit word (`WORD >> 8`); it is a coarse staircase (~94 mV/step at
  1 V/div) that discards the waveform and **still carries the comb**. The driver
  now defaults to `WORD` (with a `sample_width` option); BYTE is not an escape.
- The comb is a **fixed ~10–12 ADC codes**, so its size in volts scales with
  `V/div` while a real signal does not. Fill the ADC range with the signal, or
  use `analysis.remove_adc_comb()` (subtracts the 256-phase pattern, DC
  preserved) for broadband work. Coherent `tone_amplitude` is immune.
- At 10 GS/s, 1 V/div: 1.25 GHz ~4 mV, 2.5 GHz ~2 mV, 5 GHz ~6–8 mV; the
  500 MHz tone is ~1.3–3 mV on C1–C3 (C4 open ~0.02 mV). At 20 mV/div the comb
  drops to ~0.1 mV. `remove_adc_comb` zeroes the comb bins.
- There is also a coupled ambient comb (~429/470/515/540/590/625 MHz) on C4.
- Live A/B figures from this session: `/tmp/opencode/ab_word_byte_vdiv_1.png`,
  `..._0.02.png`, `/tmp/opencode/comb_vs_vdiv.png`,
  `/tmp/opencode/spectra_4ch_20mvdiv.png`.

### Timebase enum off-by-one
- `WAVEDESC` offset `0x144` (`tdiv`) indexes the s/div table. On this firmware
  the table **starts at 100 ps**, one step below the programming guide's 200 ps,
  so the guide's indices are off by one. `TDIV_ENUM` in `siglent.py` is fixed.

### Transport quirks (Siglent raw socket)
- Block responses can be **prefixed**: e.g. `C1:WF DAT2,` before `#9<len>`.
- Stray **blank lines** can appear between responses.
- A **zero-length block** `#9000000000` means "no waveform ready".
- The reader now: finds `#` anywhere, skips leading blank lines, returns raw
  bytes on the ASCII fallback (no `encode("ascii")` crash), and `_fetch_codes`
  **raises** on a short/empty payload.
- `:WAVeform:PREamble?` is a definite-length block whose **346-byte payload can
  itself contain a `#` byte** (e.g. inside the `vdiv` float). `_read_block`
  already removes the `#N<len>` header, so `parse_wavedesc` must **not** strip
  again — doing so mis-parsed the payload and crashed with
  `int('<')` (seen at `--vdiv 0.01`). Header stripping now lives only in the
  transports; a regression test keeps a `#`/`<` byte pair in the payload.

### `MDEPth` / `MMANagement` trap
- If `:ACQuire:MMANagement` is `FMDepth` with `:ACQuire:MDEPth 2.5M`, the
  preamble's `one_frame_pts` reports **2 500 000** while `:WAVeform:DATA?`
  returns the **100 000** screen points → `_fetch_codes` raises
  `"scope returned 200000 bytes for 2500000 points"`.
- The bench unit is restored to `MMANagement AUTO`; `frame_points` is 100 000
  again. **Don't change `MDEPth`/`MMANagement` casually** (see next steps —
  `_fetch_codes` should be made robust to this).

---

## Bugs found and fixed (all committed)

In `scopes/siglent.py` / `tests/test_siglent.py`:
1. `_read_line` returned stray blank lines → `:ACQuire:NUMACq?` `''` →
   `ValueError`. Now skips blanks.
2. `acquire()` needed `:TRIGger:STOP` before re-arming. Added.
3. `_read_block` required `#` at position 0 and treated prefixed responses as
   ASCII "data"; `_fetch_codes` then turned 22 bytes into **11 bogus samples**.
   Now locates `#` anywhere.
4. ASCII fallback crashed on binary (`encode("ascii")`). Now returns raw bytes.
5. Leftover blank line before a block made `_read_block` return empty → 0-byte
   preamble. Now strips blanks before looking for `#`.
6. `_fetch_codes` now validates payload length and raises instead of silently
   producing garbage.
7. `_fetch_codes` no longer assumes `WAVEDESC.frame_points` (memory depth) equals
   the points `DATA?` returns (screen points under `MMANagement FMDepth`). It
   follows the payload length each chunk: a short chunk ends the record, and it
   only raises on a zero-length first chunk ("waveform not ready") or a payload
   that is not a whole number of samples. Unit-tested with the fake transport;
   live `AUTO` capture still yields `(4, 100000)`.

Also fixed earlier: `TDIV_ENUM` off-by-one; counter-based acquisition
(`:ACQuire:NUMACq?` instead of trigger status); `trigger_mode` option
(default `"SINGle"`, use `"AUTO"` on the bench).

---

## Next steps (tomorrow)

1. **Legacy fast path** (borrowed from a colleague's script). Test on hardware
   whether this scope supports the short-form "WF" command set:
   - `SARA?` (sample rate), `C1:VDIV?` (V/div), `C1:OFST?` (offset),
     `C1:WF? DAT2` (raw waveform).
   - Determine data width of `WF? DAT2` (colleague assumes **8-bit `int8`** and
     `vdiv/25.0`; our scope is in **16-bit word** mode, so his scaling loses
     resolution). Decide whether to add an optional fast-path transport/read to
     `SiglentSDS6204L`.
2. **Phase-correct 3-axis vector helper** in `analysis`. The colleague's 3D
   plot is conceptually wrong: it uses FFT **magnitudes** (`|Vx|,|Vy|,|Vz|`, all
   ≥ 0) so the vector can only point into the `+++` octant. A real field vector
   needs **complex** amplitude (amplitude + phase) at the tone on a common time
   base, plus per-channel cable-delay/skew. Proposed API:
   `vector_at(capture, frequency, channels=("C1","C2","C3"), delays=None)`
   built on `tone_amplitude`; returns signed components + magic vector. Also
   enables a correct 3D quiver plot.
3. **Milestone 1 remainder:** `antenna.py` (id, sensitivity, orientation,
   cable delay, calibration), pydantic metadata, `io.py`
   (`load_capture`/`save_capture`, JSON + `.volts.npy` + `.raw.npy`), `shot.py`.
4. **Sample-rate sweep:** done for 10→0.5 GS/s — the comb does not go away; it
   is tied to the internal 10 GS/s ADC (the 39.1 MHz line shifts/aliases, the
   1.25/2.5 GHz lines stay). No timebase escapes it. `FMDepth` (fixed memory
   depth) can still be exercised now that `_fetch_codes` is robust.
5. Done: `analysis.remove_adc_comb()` (256-phase subtraction). Still optional:
   learn a reusable ambient-spur list from the open C4.

---

## Reproduction snippets

Plain acquisition:
```python
import numpy as np
from cetal_scopes import SiglentSDS6204L

with SiglentSDS6204L("192.168.5.193", channels=("C1", "C2", "C3", "C4"),
                     trigger_mode="AUTO", timeout=20.0, acquire_timeout=20.0) as s:
    cap = s.acquire()
# cap.volts / cap.raw shape (n_channels, n_samples); cap.t0, cap.dt
```

Coherent tone measurement (the comb-immune way):
```python
from cetal_scopes.analysis import subtract_baseline, tone_amplitude, band_amplitude
span = cap.n_samples * cap.dt
ch = subtract_baseline(cap["C1"], t_start=cap.t0, t_end=cap.t0 + 0.1 * span)
tone = tone_amplitude(ch, 500e6)          # -> Tone(amplitude, phase)
print(tone.amplitude, tone.phase_deg)
band = band_amplitude(ch, low=480e6, high=520e6)   # when the exact tone is unknown
```

Live plotting (what the colleague wanted):
```python
from cetal_scopes.plotting import plot_capture, plot_spectrum
from cetal_scopes.analysis import fft
ax = plot_capture(cap, time_unit="us")     # returns matplotlib Axes
plot_spectrum(fft(cap["C1"]))
```

Colleague's script (for reference): raw socket on 5025; commands `SARA?`,
`C1:VDIV?`, `C1:OFST?`, `C1:WF? DAT2`; decodes `int8`; scales
`code*(vdiv/25.0)-voffset`; `rfft` with no window; 3-channel FFT-magnitude 3D
vector. Differences vs ours: legacy command set (no preamble), 8-bit vs 16-bit,
no probe/delay handling, no windowing/detrend, magnitude-only vector (needs
phase), and much less robust block parsing.

---

## Other notes

- `pyspcm` is **not** on PyPI; use `spcm` / `spcm_core` for the Spectrum driver
  later (M5i.3367-x16).
- `AGENTS.md` and `docs/scopes/SDS6204L/index.md` already document the self-cal
  and comb gotchas; keep them in sync if behavior changes.
- Generated plots from this session:
  `/tmp/opencode/scope_500mhz.png` (may not survive a reboot).
- The bench scope should currently be left running in `AUTO`/`RUN`.
