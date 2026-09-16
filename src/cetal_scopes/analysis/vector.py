"""Phase-correct vector reconstruction from a three-axis probe.

The naive way to plot a field vector from a triad is to take the FFT magnitude
of each axis and treat the three numbers as components. That is wrong, and
visibly so: magnitudes are non-negative, so the resulting vector can only ever
point into the ``+++`` octant. A real vector needs the *complex* amplitude --
amplitude and phase together -- at a common frequency on a common time base,
with each axis's cable delay removed first.

That is what :func:`vector_at` does, on top of
:func:`~cetal_scopes.analysis.spectral.tone_amplitude`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from cetal_scopes.analysis.spectral import tone_amplitude
from cetal_scopes.capture import Capture

__all__ = ["FieldVector", "vector_at"]


@dataclass(frozen=True)
class FieldVector:
    """A vector quantity reconstructed at one frequency.

    Attributes
    ----------
    frequency : float
        The frequency it was measured at, in hertz.
    components : ndarray of complex
        Complex amplitude per axis, in the order the channels were given.
    channels : tuple of str
        The channel names, in component order.
    axes : ndarray or None
        Unit orientation vectors from each channel's antenna, stacked as rows,
        when every channel has one. ``None`` when any is missing, in which case
        the components are assumed already to be along orthogonal lab axes.
    reference_phase : float
        Phase, in radians, that was subtracted from every component so the
        vector is expressed relative to one axis.
    """

    frequency: float
    components: NDArray[np.complex128]
    channels: tuple[str, ...]
    axes: NDArray[np.float64] | None = None
    reference_phase: float = 0.0

    @property
    def real(self) -> NDArray[np.float64]:
        """The in-phase (signed) components -- the vector to draw.

        This is what replaces the three FFT magnitudes: taken at the instant
        the reference axis peaks, so a component genuinely pointing the other
        way comes out negative.
        """
        return np.real(self.components).astype(np.float64)

    @property
    def quadrature(self) -> NDArray[np.float64]:
        """Components 90 degrees out of phase with the reference axis.

        Large values mean the field is elliptically polarized rather than
        linear, so a single arrow does not fully describe it.
        """
        return np.imag(self.components).astype(np.float64)

    @property
    def magnitude(self) -> float:
        """Root-sum-square of the component magnitudes."""
        return float(np.sqrt(np.sum(np.abs(self.components) ** 2)))

    @property
    def lab_vector(self) -> NDArray[np.float64]:
        """The in-phase components projected onto the lab frame.

        When :attr:`axes` is set, the per-axis amplitudes are combined using
        each probe's true orientation, which matters for any triad that is not
        perfectly orthogonal.
        """
        if self.axes is None:
            return self.real
        return np.asarray(self.axes.T @ self.real, dtype=np.float64)

    @property
    def ellipticity(self) -> float:
        """Quadrature magnitude over total magnitude, in ``[0, 1]``.

        ``0`` is a linearly polarized field that one arrow describes fully;
        values approaching ``1`` mean the vector rotates through the cycle.
        """
        total = self.magnitude
        if total == 0.0:
            return 0.0
        return float(np.linalg.norm(self.quadrature) / total)

    def __repr__(self) -> str:
        return (
            f"FieldVector(frequency={self.frequency!r}, "
            f"channels={self.channels!r}, magnitude={self.magnitude!r})"
        )


def vector_at(
    capture: Capture,
    frequency: float,
    *,
    channels: Sequence[str] = ("C1", "C2", "C3"),
    delays: Mapping[str, float] | None = None,
    reference: str | None = None,
    window: str = "hann",
) -> FieldVector:
    """Reconstruct a phase-correct vector at ``frequency`` from several channels.

    Each channel is coherently detected at ``frequency``, its cable delay is
    removed as a phase rotation, and the whole set is referred to one axis's
    phase so the components are signed and mutually consistent.

    Parameters
    ----------
    capture : Capture
        Source capture. All channels share its timebase, which is what makes
        the relative phases meaningful.
    frequency : float
        Frequency to evaluate, in hertz.
    channels : sequence of str, optional
        The channels forming the vector, in component order.
    delays : mapping of str to float, optional
        Per-channel delay in seconds to remove. Defaults to each channel's
        antenna ``delay``, so a calibrated probe needs nothing here.
    reference : str, optional
        Channel whose phase the vector is expressed relative to. Defaults to
        the first of ``channels``.
    window : str, optional
        Window used by the coherent detection.

    Returns
    -------
    FieldVector
        The complex components, with the probe orientations when available.

    Raises
    ------
    ValueError
        If fewer than two channels are given, or ``frequency`` is negative.
    KeyError
        If a named channel is not in the capture.

    Examples
    --------
    >>> vector = vector_at(capture, 500e6)  # doctest: +SKIP
    >>> vector.real            # signed components, not magnitudes  # doctest: +SKIP
    array([ 0.42, -0.11,  0.08])
    """
    names = tuple(channels)
    if len(names) < 2:
        raise ValueError("a vector needs at least two channels")
    if frequency < 0.0:
        raise ValueError(f"frequency must be non-negative, got {frequency!r}")
    missing = [name for name in names if name not in capture.channels]
    if missing:
        raise KeyError(f"capture has no channels {missing!r}")

    reference_name = reference if reference is not None else names[0]
    if reference_name not in names:
        raise ValueError(
            f"reference {reference_name!r} is not one of the channels {names!r}"
        )

    components: list[complex] = []
    axes: list[NDArray[np.float64]] = []
    have_axes = True
    for name in names:
        channel = capture.channels[name]
        tone = tone_amplitude(channel, frequency, window=window)

        if delays is not None:
            delay = float(delays.get(name, 0.0))
        elif channel.antenna is not None:
            delay = float(channel.antenna.delay)
        else:
            delay = 0.0
        # A cable delay is a pure phase rotation at a single frequency;
        # removing it here is what puts the axes on a common time base.
        phase = tone.phase + 2.0 * np.pi * frequency * delay
        components.append(tone.amplitude * np.exp(1j * phase))

        axis = None if channel.antenna is None else channel.antenna.unit_axis
        if axis is None:
            have_axes = False
        else:
            axes.append(axis)

    reference_index = names.index(reference_name)
    reference_phase = float(np.angle(components[reference_index]))
    rotated = np.asarray(components, dtype=np.complex128) * np.exp(
        -1j * reference_phase
    )

    return FieldVector(
        frequency=float(frequency),
        components=rotated,
        channels=names,
        axes=np.vstack(axes) if have_axes and axes else None,
        reference_phase=reference_phase,
    )
