"""Interactive Dash/Plotly analysis for B-dot shots.

Downstream application built on :mod:`cetal_scopes`; the acquisition and figure
building live in :mod:`engine`. It arms a single-shot edge trigger, fetches one
record, and serves the calibrated ``dB/dt`` and ``B`` traces as an interactive
Plotly figure (hover, zoom, pan, legend toggles).

Run the dev server::

    uv run apps/bdot_web/bdot_web.py --address 192.168.5.197 --demo

Then open http://127.0.0.1:8050.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
from engine import (
    ProbeSession,
    ScopeConfig,
    empty_figure,
)

__all__ = ["create_app", "main"]

_DEMO_LABELS = {"label": "demo (synthetic source)", "value": "demo"}
_AUTO_LABELS = {"label": "auto re-arm", "value": "auto"}


def _parse_channels(text: str) -> tuple[str, ...]:
    names = tuple(text.replace(",", " ").split())
    return names or ("C1",)


def _parse_float(text: object) -> float | None:
    if text is None or text == "":
        return None
    return float(text)  # type: ignore[arg-type]


def _form_channel_labels(channels: Sequence[str]) -> tuple[str, ...]:
    default = ("X", "Y", "Z", "W")
    return tuple(default[index] for index in range(len(channels)))


def create_app(config: ScopeConfig) -> Dash:
    """Build the Dash app backed by a :class:`ProbeSession`."""
    session = ProbeSession(config)
    app = Dash(__name__, title="B-dot probe")
    app.layout = html.Div(
        style={"fontFamily": "monospace", "maxWidth": "1200px", "margin": "0 auto"},
        children=[
            html.H2("B-dot probe — interactive analysis"),
            html.Div(
                style={
                    "display": "grid",
                    "gridTemplateColumns": "repeat(4, 1fr)",
                    "gap": "8px",
                    "alignItems": "center",
                },
                children=[
                    html.Label("address"),
                    dcc.Input(id="address", value=config.address, type="text"),
                    html.Label("channels"),
                    dcc.Input(
                        id="channels", value=" ".join(config.channels), type="text"
                    ),
                    html.Label("sensitivity V/(T/s)"),
                    dcc.Input(
                        id="sensitivity",
                        value=config.sensitivity,
                        type="number",
                        step=1e-3,
                    ),
                    html.Label("fmin Hz (blank = 10·df)"),
                    dcc.Input(id="fmin", value=config.fmin, type="number"),
                    html.Label("fmax Hz (blank = Nyquist)"),
                    dcc.Input(id="fmax", value=config.fmax, type="number"),
                    html.Label("trigger level V"),
                    dcc.Input(
                        id="trigger-level", value=config.trigger_level, type="number"
                    ),
                    html.Label("trigger slope"),
                    dcc.Dropdown(
                        id="trigger-slope",
                        value=config.trigger_slope,
                        clearable=False,
                        options=[
                            {"label": "RISing", "value": "RISing"},
                            {"label": "FALLing", "value": "FALLing"},
                        ],
                    ),
                    html.Label("timebase s/div"),
                    dcc.Input(id="timebase", value=config.timebase, type="number"),
                    html.Label("vdiv V/div"),
                    dcc.Input(id="vdiv", value=config.vdiv, type="number"),
                    html.Label("impedance"),
                    dcc.Dropdown(
                        id="impedance",
                        value=config.impedance,
                        clearable=False,
                        options=[
                            {"label": "1 MOhm", "value": "1M"},
                            {"label": "50 Ohm", "value": "50"},
                        ],
                    ),
                    html.Label("auto interval ms"),
                    dcc.Input(id="interval-ms", value=1000, type="number"),
                ],
            ),
            html.Div(
                style={"margin": "10px 0", "display": "flex", "gap": "8px"},
                children=[
                    html.Button("Apply / arm", id="arm-btn"),
                    html.Button("Capture one shot", id="capture-btn"),
                    html.Button("Disconnect", id="disconnect-btn"),
                    html.Button("Clear log", id="clear-btn"),
                    dcc.Checklist(id="demo", options=[_DEMO_LABELS], value=[]),
                    dcc.Checklist(id="auto", options=[_AUTO_LABELS], value=[]),
                ],
            ),
            html.Div(id="status", style={"fontWeight": "bold", "minHeight": "1.2em"}),
            dcc.Graph(id="graph", figure=empty_figure()),
            html.Pre(
                id="log",
                style={
                    "maxHeight": "180px",
                    "overflowY": "auto",
                    "background": "#f6f6f6",
                    "padding": "6px",
                },
            ),
            dcc.Interval(id="tick", interval=1000, disabled=True),
        ],
    )

    def _config_from_form(
        address: str,
        channels: str,
        sensitivity: float,
        fmin: float,
        fmax: float,
        trigger_level: float,
        trigger_slope: str,
        timebase: float,
        vdiv: float,
        impedance: str,
        demo: Sequence[str] | None,
        trigger_timeout: float,
    ) -> ScopeConfig:
        names = _parse_channels(channels)
        return ScopeConfig(
            address=address,
            channels=names,
            labels=_form_channel_labels(names),
            sensitivity=float(sensitivity),
            fmin=_parse_float(fmin),
            fmax=_parse_float(fmax),
            trigger_level=float(trigger_level),
            trigger_slope=trigger_slope,
            trigger_timeout=trigger_timeout,
            timebase=_parse_float(timebase),
            vdiv=_parse_float(vdiv),
            impedance=impedance,
            demo=bool(demo),
        )

    def _log_text() -> str:
        return "\n".join(session.log)

    def _figure():
        if session.last_figure is None:
            return empty_figure()
        return session.last_figure

    @app.callback(
        Output("tick", "disabled"),
        Output("tick", "interval"),
        Input("auto", "value"),
        Input("interval-ms", "value"),
    )
    def _toggle_auto(auto: Sequence[str] | None, interval_ms: float | None):
        disabled = not auto
        interval = max(100, int(interval_ms or 1000))
        return disabled, interval

    @app.callback(
        Output("graph", "figure"),
        Output("status", "children"),
        Output("log", "children"),
        Input("arm-btn", "n_clicks"),
        Input("capture-btn", "n_clicks"),
        Input("tick", "n_intervals"),
        Input("disconnect-btn", "n_clicks"),
        Input("clear-btn", "n_clicks"),
        State("address", "value"),
        State("channels", "value"),
        State("sensitivity", "value"),
        State("fmin", "value"),
        State("fmax", "value"),
        State("trigger-level", "value"),
        State("trigger-slope", "value"),
        State("timebase", "value"),
        State("vdiv", "value"),
        State("impedance", "value"),
        State("demo", "value"),
        prevent_initial_call=True,
    )
    def _act(
        _arm: int | None,
        _capture: int | None,
        _tick: int | None,
        _disconnect: int | None,
        _clear: int | None,
        address: str,
        channels: str,
        sensitivity: float,
        fmin: float,
        fmax: float,
        trigger_level: float,
        trigger_slope: str,
        timebase: float,
        vdiv: float,
        impedance: str,
        demo: Sequence[str] | None,
    ):
        fired = ctx.triggered_id
        if fired == "disconnect-btn":
            session.disconnect()
            return no_update, "disconnected", _log_text()
        if fired == "clear-btn":
            session.log.clear()
            return no_update, no_update, _log_text()

        session.config = _config_from_form(
            address,
            channels,
            sensitivity,
            fmin,
            fmax,
            trigger_level,
            trigger_slope,
            timebase,
            vdiv,
            impedance,
            demo,
            session.config.trigger_timeout,
        )

        if fired == "arm-btn":
            try:
                status = session.arm()
            except Exception as exc:  # noqa: BLE001 - surface to the UI
                return no_update, f"error: {exc}", _log_text()
            return no_update, status, _log_text()

        # capture-btn or tick
        try:
            captured = session.capture_once()
        except Exception as exc:  # noqa: BLE001 - surface to the UI
            return no_update, f"error: {exc}", _log_text()
        if captured is None:
            return _figure(), "armed - waiting for trigger", _log_text()
        figure, result = captured
        status = (
            f"shot {session.shot}: |B| peak {result.magnitude_peak:.3e} T, "
            f"band {result.band[0]:.3g}-{result.band[1]:.3g} Hz"
        )
        return figure, status, _log_text()

    return app


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="bdot_web", description="Dash/Plotly B-dot analysis server."
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--port", type=int, default=8050, help="bind port")
    parser.add_argument("--address", default="192.168.5.193", help="scope IP")
    parser.add_argument(
        "--channels", nargs="+", default=["C1", "C2", "C3"], help="channels"
    )
    parser.add_argument("--sensitivity", type=float, default=1.0, help="V/(T/s)")
    parser.add_argument("--demo", action="store_true", help="start in demo mode")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the Dash server."""
    args = build_parser().parse_args(argv)
    config = ScopeConfig(
        address=args.address,
        channels=tuple(args.channels),
        labels=_form_channel_labels(args.channels),
        sensitivity=args.sensitivity,
        demo=args.demo,
    )
    app = create_app(config)
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
