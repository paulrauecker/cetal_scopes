"""A serialisable post-processing pipeline applied to a shot's channels.

Each step is a thin, named wrapper over one :mod:`cetal_scopes.analysis`
function. Keeping the pipeline as data rather than as code means it round-trips
through the browser and can be stored in a shot's metadata, which is what makes
a saved shot reproducible: the recorded samples plus the exact steps that were
applied to them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cetal_scopes.analysis import (
    b_field,
    b_field_rate,
    bandpass,
    bandstop,
    detrend,
    envelope,
    gate,
    highpass,
    lowpass,
    moving_average,
    remove_adc_comb,
    resample,
    savgol,
    subtract_baseline,
    wiener,
)
from cetal_scopes.channel import Channel

__all__ = [
    "STEPS",
    "ProcessingGroup",
    "ProcessingStep",
    "StepInfo",
    "apply_pipeline",
    "group_of",
    "groups_to_metadata",
    "pipeline_to_metadata",
    "step_catalog",
    "validate_groups",
]

StepFunction = Callable[..., Channel]


class StepInfo(BaseModel):
    """What one pipeline step is and what it accepts, for the UI to render."""

    model_config = ConfigDict(extra="forbid")

    name: str
    summary: str
    params: dict[str, Any] = Field(default_factory=dict)
    """Parameter name to default value. ``None`` means the step's own default."""


def _b_field_rate(channel: Channel, **kwargs: Any) -> Channel:
    return b_field_rate(channel, **kwargs)


#: Step name to (function, defaults, one-line summary). The defaults are what
#: the UI offers; a step is free to be given only some of them.
STEPS: dict[str, tuple[StepFunction, dict[str, Any], str]] = {
    "gate": (
        gate,
        {"t_start": None, "t_end": None},
        "Keep only the samples inside a time window.",
    ),
    "subtract_baseline": (
        subtract_baseline,
        {"t_start": None, "t_end": None, "mode": "mean"},
        "Remove the level measured over a quiet window.",
    ),
    "detrend": (detrend, {"type": "linear"}, "Remove a constant or linear trend."),
    "remove_adc_comb": (
        remove_adc_comb,
        {"period": None},
        "Subtract the Siglent's deterministic 25.6 ns ADC comb.",
    ),
    "lowpass": (lowpass, {"cutoff": None, "order": 4}, "Zero-phase low-pass."),
    "highpass": (highpass, {"cutoff": None, "order": 4}, "Zero-phase high-pass."),
    "bandpass": (
        bandpass,
        {"low": None, "high": None, "order": 4},
        "Zero-phase band-pass.",
    ),
    "bandstop": (
        bandstop,
        {"low": None, "high": None, "order": 4},
        "Zero-phase band-stop.",
    ),
    "moving_average": (
        moving_average,
        {"window_s": None},
        "Centred boxcar smoothing.",
    ),
    "savgol": (
        savgol,
        {"window_s": None, "polyorder": 3},
        "Savitzky-Golay smoothing; preserves peak height and width.",
    ),
    "wiener": (
        wiener,
        {
            "noise_start": None,
            "noise_end": 0.0,
            "signal_start": None,
            "signal_end": None,
            "resolution": None,
        },
        "Wiener filter: weight each frequency by its SNR against a quiet window.",
    ),
    "resample": (resample, {"dt": None, "n": None}, "Change the sample interval."),
    "envelope": (envelope, {}, "Hilbert amplitude envelope."),
    "b_field_rate": (
        _b_field_rate,
        {"outside": "raise"},
        "Calibrate a B-dot's volts to dB/dt (needs an antenna).",
    ),
    "b_field": (
        b_field,
        {"f_min": None, "f_max": None, "outside": "raise"},
        "Integrate a calibrated B-dot to B (needs an antenna).",
    ),
}


class ProcessingStep(BaseModel):
    """One configured step of the pipeline."""

    model_config = ConfigDict(extra="forbid")

    name: str
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    def apply(self, channel: Channel) -> Channel:
        """Run this step on ``channel``.

        Parameters left as ``None`` are dropped rather than passed through, so
        the underlying function's own default applies.

        Raises
        ------
        ValueError
            If the step name is unknown, or the function rejects the
            parameters.
        """
        try:
            function, _, _ = STEPS[self.name]
        except KeyError:
            raise ValueError(
                f"unknown processing step {self.name!r}; expected one of "
                f"{sorted(STEPS)}"
            ) from None
        kwargs = {key: value for key, value in self.params.items() if value is not None}
        try:
            return function(channel, **kwargs)
        except TypeError as exc:
            raise ValueError(f"step {self.name!r} rejected {kwargs!r}: {exc}") from exc


class ProcessingGroup(BaseModel):
    """A named set of channels that share their own pipeline.

    Channels are ``"label:channel"`` keys, so one group can span instruments.
    A channel belongs to at most one group -- otherwise it would have two
    processed versions and every figure would have to pick one -- and a
    channel in no group gets the session's default pipeline.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    channels: list[str] = Field(default_factory=list)
    steps: list[ProcessingStep] = Field(default_factory=list)


def validate_groups(groups: Sequence[ProcessingGroup]) -> None:
    """Check that group names are unique and no channel is in two groups.

    Raises
    ------
    ValueError
        On a blank, repeated or reserved (``"default"``) name, or a channel
        claimed twice.
    """
    names: set[str] = set()
    owner: dict[str, str] = {}
    for group in groups:
        name = group.name.strip()
        if not name:
            raise ValueError("a processing group needs a name")
        if name in names:
            raise ValueError(f"two processing groups are named {name!r}")
        if name == "default":
            # The ungrouped channels' pipeline already goes by that name.
            raise ValueError('"default" is the ungrouped pipeline; pick another name')
        names.add(name)
        for key in group.channels:
            if key in owner and owner[key] != name:
                raise ValueError(
                    f"channel {key!r} is in both {owner[key]!r} and {name!r}; "
                    "a channel belongs to at most one group"
                )
            owner[key] = name


def group_of(groups: Sequence[ProcessingGroup]) -> dict[str, ProcessingGroup]:
    """Map each grouped channel key to its group."""
    return {key: group for group in groups for key in group.channels}


def step_catalog() -> list[StepInfo]:
    """Describe every available step, for the UI to build its menu from."""
    return [
        StepInfo(name=name, summary=summary, params=dict(defaults))
        for name, (_, defaults, summary) in STEPS.items()
    ]


def apply_pipeline(
    channel: Channel,
    steps: Sequence[ProcessingStep],
    *,
    skip_errors: bool = True,
) -> tuple[Channel, list[str]]:
    """Apply every enabled step in order.

    Parameters
    ----------
    channel : Channel
        Source channel.
    steps : sequence of ProcessingStep
        The pipeline, in order.
    skip_errors : bool, optional
        When ``True`` (the default), a step that cannot run on this particular
        channel is skipped and reported instead of aborting. That is what lets
        one pipeline serve a whole shot: ``b_field`` is meaningful only on
        channels that carry an antenna, and ``remove_adc_comb`` only on the
        Siglent, but the operator should not have to build a separate pipeline
        per channel to say so.

    Returns
    -------
    channel : Channel
        The processed channel.
    warnings : list of str
        One message per step that was skipped.
    """
    warnings: list[str] = []
    current = channel
    for step in steps:
        if not step.enabled:
            continue
        try:
            current = step.apply(current)
        except Exception as exc:  # a step that cannot run here is reported, not fatal
            if not skip_errors:
                raise
            warnings.append(f"{channel.name}: {step.name} skipped ({exc})")
    return current, warnings


def pipeline_to_metadata(steps: Sequence[ProcessingStep]) -> list[Mapping[str, Any]]:
    """Render the pipeline for storage in a shot's metadata."""
    return [step.model_dump() for step in steps if step.enabled]


def groups_to_metadata(
    groups: Sequence[ProcessingGroup],
) -> list[Mapping[str, Any]]:
    """Render the processing groups for storage in a shot's metadata."""
    return [
        {
            "name": group.name,
            "channels": list(group.channels),
            "steps": pipeline_to_metadata(group.steps),
        }
        for group in groups
    ]
