# bdot_vector

Real-time 3D view of the field vector seen by the 3D B-dot on C1-C3 of the
SDS6204L. Each capture is reduced to one phasor per axis at the strongest tone
(or `--freq`). Each capture is shown on its own (no averaging or
smoothing); the view draws the field as one arrow along its
oscillation axis, length = peak field (with shadows on the
walls). Only reads the scope; bring your own field source.

    uv run apps/bdot_vector/bdot_vector.py --address 192.168.5.162

Field = V / (j omega A) with one area `--area` for all axes (default 6.8e-4 m²,
C1's loop-fixture value). C2/C3 and cross-coupling are uncalibrated, and the
28 mm B-dot is not a clean dB/dt sensor above ~130 MHz, so treat direction and
magnitude as indicative.
