# Siglent SDS6204L

4-channel digital oscilloscope driven over LAN SCPI. The driver lives in
`cetal_scopes.scopes.siglent`.

## Connection

- Raw socket (stdlib): `TCPIP::<ip>::5025::SOCKET`, or just the IP address.
  Every command must end with `\n`.
- VISA fallback via PyVISA for USB / VXI-11 resource strings.
- Bench unit observed at `192.168.5.193`; `*IDN?` reports firmware
  `18.36.11.2.0.3.7`.

## Waveform transfer

`:WAVeform:PREamble?` returns a 346-byte `WAVEDESC` block (`#9<9 digits>`)
followed by `:WAVeform:DATA?` raw codes. Scaling is
`V = code * (vdiv * probe / code_per_div) - voffset * probe`. The time axis is
`t = delay - timebase * grid_num / 2 + i * interval`.

The `tdiv` field at offset `0x144` indexes the timebase table. On this firmware
the table starts at **100 ps**, one step below the 200 ps that the programming
guide's table begins at — the guide's indices are off by one.

Block responses may carry a prefix (e.g. `C1:WF DAT2,` before the `#9<length>`
header) and stray blank lines can appear between responses; the transport skips
both. A zero-length block (`#9000000000`) or a short payload means the waveform
is not ready — `_fetch_codes` raises rather than returning garbage.

## Gotchas

- **Self-calibration is asynchronous and slow, with no reliable SCPI status.**
  `:SYSTem:SELFCal` returns immediately; it takes minutes and the front panel
  shows `doing self cal ... NN%`.
  - **There is no usable progress or completion query** (verified on the bench
    unit while the panel read 78%):
    - `*OPC?` returns `1` immediately.
    - `:SYSTem:SELFCal?` returns `DONE` **even mid-run** — do not trust it.
    - `STATus:OPERation` / `:CONDition?` stay `0`.
    - `:SYSTem:SELFCal:PROGress?` does not exist (it raises a command error,
      latched in `*ESR?` bit 5 = value 32).
  - Watch the front panel. While it runs, waveform queries time out and
    `:ACQuire:NUMACq?` may stop advancing.
  - Best practice: quiet inputs (source off) before calibrating; re-run after
    the scope is warm.
  - After a run, `acquire()` may need a `:TRIGger:STOP` before `RUN` will
    re-arm the acquisition engine.
- **ADC comb (16-bit path).** The SDS6204L is an 8-bit instrument whose `WORD`
  (16-bit HD) transfer path adds a deterministic pattern with a **256-sample
  period**: spurs at every multiple of `fs / 256` (~39.06 MHz at 10 GS/s), of
  which `fs/8`, `fs/4` and `fs/2` are the strongest. It appears on every channel
  including open ones, is generated after the analog front end (a 20 MHz
  bandwidth limit does not remove it; `:ACQuire:RESolution` is locked at
  `16Bits`), and persists at reduced sample rates.
  - The comb is a fixed ~10–12 ADC codes, so its size in volts scales with
    `V/div` while the signal does not. Fill the ADC range with the signal
    (coarsest useful `V/div` avoided), or use
    `cetal_scopes.analysis.remove_adc_comb` to subtract the 256-phase pattern.
  - `:WAVeform:WIDTh BYTE` is **not** an escape: it returns the top byte of the
    same 16-bit word (a coarse staircase, ~94 mV per step at 1 V/div) and still
    carries the comb. The driver defaults to `WORD` (`sample_width="BYTE"` is
    available for compatibility).
  - For narrowband work, coherent detection
    (`cetal_scopes.analysis.tone_amplitude`) ignores the comb; a band amplitude
    (`band_amplitude`) is the fallback when the tone frequency is unknown.
- `*ESR?` bit 4 (value 16) can be latched by earlier command errors; read it to
  clear.
