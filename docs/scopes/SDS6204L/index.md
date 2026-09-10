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
- **ADC interleave spurs.** Expect deterministic spurs at `fs/8`, `fs/4` and
  `fs/2` (e.g. 1.25 / 2.5 / 5 GHz at 10 GS/s) on every channel including open
  ones, plus a coupled ambient comb. Analyze a band or use coherent detection
  (`cetal_scopes.analysis.tone_amplitude`) rather than the global FFT peak.
- `*ESR?` bit 4 (value 16) can be latched by earlier command errors; read it to
  clear.
