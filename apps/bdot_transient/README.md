# bdot_transient

Single-shot capture of a transient (an EMP, or a piezo lighter's spark as a
bench stand-in) on the 3D B-dot at C1-C3 of the SDS6204L, and an offline
replay of the field vector over the event, sample by sample.

    uv run apps/bdot_transient/bdot_transient.py capture shot1 --source C2 --level 0.02
    uv run apps/bdot_transient/bdot_transient.py view shot1

`capture` arms one edge trigger, waits until it fires (Ctrl-C gives up), warns
about clipped channels and saves `shot1.json` plus its `.npy` sidecars.
`view` shows an arrow at the current sample, the path so far coloured by time
(viridis), the rest of it faint, the per-axis traces zoomed on the cursor
(`--zoom` ns) and an overview of the whole record: click it to jump, e.g. to
one strike of a multi-strike shot. Slider or left/right step one sample; the
play button or space plays at the speed slider's ns of signal per second
(`--speed` sets the start). Only measured samples are drawn; fast playback
skips samples rather than interpolating.

The field is uncalibrated: one flat area per axis (`--area`, one value or
three) and a running integral from the pretrigger baseline. That is only right
where the loop is an ideal dB/dt sensor, and channel skew is ignored; `--raw`
shows the volts instead.
