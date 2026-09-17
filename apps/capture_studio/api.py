"""HTTP and WebSocket surface of the capture studio."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from config import demo_inventory, parse_inventory
from export import export_shot
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from figures import (
    LAYOUTS,
    LayoutMode,
    coherence_figure,
    empty_figure,
    fft_figure,
    measurement_table,
    spectrogram_figure,
    time_figure,
    transfer_figure,
    vector_figure,
    xy_figure,
)
from processing import ProcessingStep, step_catalog
from pydantic import BaseModel, ConfigDict, Field
from session import StudioSession

from cetal_scopes.scopes.registry import DRIVERS

__all__ = ["create_app", "normalise_root_path", "with_root_path"]

STATIC = Path(__file__).parent / "static"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaptureRequest(_Body):
    """Options for one shot."""

    direct_trigger: bool | None = None
    """Force every capable instrument once armed. ``None`` honours the
    inventory's per-instrument setting."""
    timeout: float | None = None


class OffsetsRequest(_Body):
    """Manual per-capture time offsets, in seconds."""

    offsets: dict[str, float]


class AutofitRequest(_Body):
    """Options for the cross-correlation fit."""

    reference: str | None = None
    channels: dict[str, str] | str | None = None
    max_lag: float | None = None


class PipelineRequest(_Body):
    """The processing pipeline."""

    steps: list[ProcessingStep] = Field(default_factory=list)


class PathRequest(_Body):
    """A filesystem path for saving or loading."""

    path: str


class ForceRequest(_Body):
    """Which instruments to force."""

    labels: list[str] | None = None


def _error(exc: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=str(exc))


def normalise_root_path(root_path: str) -> str:
    """Return *root_path* as a bare prefix: leading slash, no trailing one."""
    prefix = root_path.strip().strip("/")
    return f"/{prefix}" if prefix else ""


def with_root_path(app: FastAPI, root_path: str) -> Any:
    """Serve *app* under *root_path*.

    Reverse proxies that mount an app at a URL prefix come in two kinds: those
    that strip the prefix before forwarding, and those that pass the request
    path through untouched. Open OnDemand's ``/node/<host>/<port>/`` proxy is
    the second kind, so the prefix has to come off here. Note that only the
    path is rewritten -- setting the ASGI ``root_path`` as well makes Starlette
    miss the ``/static`` mount.
    """
    prefix = normalise_root_path(root_path)
    if not prefix:
        return app

    async def wrapper(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            path = scope["path"]
            if path == prefix or path.startswith(f"{prefix}/"):
                scope = dict(scope, path=path[len(prefix) :] or "/")
        await app(scope, receive, send)

    return wrapper


def create_app(session: StudioSession, root_path: str = "") -> FastAPI:
    """Build the application around an existing :class:`StudioSession`.

    *root_path* is the URL prefix the page will be served under; it is baked
    into the page's ``<base>`` so the browser asks for ``api/...`` and
    ``static/...`` below the prefix. Use :func:`with_root_path` to route it.
    """
    base = f"{normalise_root_path(root_path)}/"

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        # Instruments must be released even when the server is killed
        # mid-shot, or the next run finds the hardware still armed.
        await session.disconnect()

    app = FastAPI(title="Capture studio", lifespan=lifespan)

    # -- inventory ---------------------------------------------------------

    @app.get("/api/inventory")
    def get_inventory() -> dict[str, Any]:
        return {
            "inventory": session.inventory.model_dump(),
            "drivers": sorted(DRIVERS),
            "config_path": str(session.config_path) if session.config_path else None,
        }

    @app.put("/api/inventory")
    async def put_inventory(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            inventory = parse_inventory(payload.get("inventory", payload))
            await session.set_inventory(inventory)
        except (ValueError, RuntimeError) as exc:
            raise _error(exc) from exc
        return {"inventory": session.inventory.model_dump()}

    @app.post("/api/inventory/save")
    def post_inventory_save(payload: PathRequest | None = None) -> dict[str, Any]:
        try:
            saved = session.save_inventory_file(Path(payload.path) if payload else None)
        except (ValueError, OSError) as exc:
            raise _error(exc) from exc
        return {"path": str(saved)}

    @app.get("/api/inventory/demo")
    def get_demo_inventory(instruments: int = 2) -> dict[str, Any]:
        try:
            return {"inventory": demo_inventory(instruments).model_dump()}
        except ValueError as exc:
            raise _error(exc) from exc

    # -- connection and shots ---------------------------------------------

    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        return _status_payload(session)

    @app.post("/api/connect")
    async def post_connect() -> dict[str, Any]:
        try:
            await session.connect()
        except (ValueError, RuntimeError, OSError) as exc:
            raise _error(exc) from exc
        return _status_payload(session)

    @app.post("/api/disconnect")
    async def post_disconnect() -> dict[str, Any]:
        await session.disconnect()
        return _status_payload(session)

    @app.post("/api/capture")
    async def post_capture(payload: CaptureRequest | None = None) -> dict[str, Any]:
        options = payload or CaptureRequest()
        try:
            await session.capture(
                direct_trigger=options.direct_trigger, timeout=options.timeout
            )
        except (ValueError, RuntimeError, OSError) as exc:
            raise _error(exc) from exc
        return _shot_payload(session)

    @app.post("/api/abort")
    async def post_abort() -> dict[str, Any]:
        await session.abort()
        return _status_payload(session)

    @app.post("/api/force-trigger")
    async def post_force(payload: ForceRequest | None = None) -> dict[str, Any]:
        try:
            await session.force_trigger(payload.labels if payload else None)
        except RuntimeError as exc:
            raise _error(exc) from exc
        return _status_payload(session)

    @app.get("/api/shot")
    def get_shot() -> dict[str, Any]:
        if session.shot is None:
            return {"shot": None, "status": _status_payload(session)}
        return _shot_payload(session)

    @app.post("/api/shot/offsets")
    def post_offsets(payload: OffsetsRequest) -> dict[str, Any]:
        try:
            session.set_offsets(payload.offsets)
        except (KeyError, RuntimeError, ValueError) as exc:
            raise _error(exc) from exc
        return _shot_payload(session)

    @app.post("/api/shot/autofit")
    def post_autofit(payload: AutofitRequest | None = None) -> dict[str, Any]:
        options = payload or AutofitRequest()
        try:
            session.autofit(
                reference=options.reference,
                channels=options.channels,
                max_lag=options.max_lag,
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            raise _error(exc) from exc
        return _shot_payload(session)

    @app.post("/api/shot/save")
    def post_shot_save(payload: PathRequest) -> dict[str, Any]:
        try:
            index = session.save_shot_to(payload.path)
        except (RuntimeError, ValueError, OSError) as exc:
            raise _error(exc) from exc
        return {"path": str(index)}

    @app.post("/api/shot/load")
    def post_shot_load(payload: PathRequest) -> dict[str, Any]:
        try:
            session.load_shot_from(payload.path)
        except (FileNotFoundError, ValueError) as exc:
            raise _error(exc) from exc
        return _shot_payload(session)

    # -- processing and figures -------------------------------------------

    @app.get("/api/processing")
    def get_processing() -> dict[str, Any]:
        return {
            "steps": [step.model_dump() for step in session.pipeline],
            "catalog": [info.model_dump() for info in step_catalog()],
        }

    @app.put("/api/processing")
    def put_processing(payload: PipelineRequest) -> dict[str, Any]:
        session.set_pipeline(payload.steps)
        return {"steps": [step.model_dump() for step in session.pipeline]}

    @app.get("/api/figure")
    def get_figure(
        panel: Literal[
            "time", "fft", "spectrogram", "xy", "coherence", "transfer", "vector"
        ] = "time",
        layout: LayoutMode = "overlay",
        channels: str | None = None,
        a: str | None = None,
        b: str | None = None,
        psd: bool = False,
        log_x: bool = True,
        log_y: bool = True,
        window: str = "hann",
        max_points: int = 4000,
        frequency: float | None = None,
        raw: bool = False,
    ) -> JSONResponse:
        if session.shot is None:
            return JSONResponse(
                {"figure": empty_figure("No shot yet."), "warnings": []}
            )
        if layout not in LAYOUTS:
            raise HTTPException(400, f"unknown layout {layout!r}")
        # A drawing budget, not an analysis parameter: nothing on a screen
        # shows more than a few thousand points per trace, and an unbounded
        # value here is a multi-megabyte response the browser cannot draw.
        max_points = int(min(max(max_points, 200), 20000))

        shot = session.shot
        # An explicit empty ``channels=`` means none, not all: it is what the
        # UI sends when every channel has been toggled off.
        selection = channels.split(",") if channels is not None else None
        if raw:
            processed, warnings = None, []
        else:
            processed, warnings = session.processed_channels()

        try:
            figure = _build_figure(
                panel,
                shot,
                processed,
                layout=layout,
                selection=selection,
                a=a,
                b=b,
                psd=psd,
                log_x=log_x,
                log_y=log_y,
                window=window,
                max_points=max_points,
                frequency=frequency,
            )
        except KeyError as exc:
            raise HTTPException(400, f"unknown channel {exc.args[0]!r}") from exc
        except ValueError as exc:
            raise _error(exc) from exc
        return JSONResponse({"figure": figure, "warnings": warnings})

    @app.get("/api/measurements")
    def get_measurements(channels: str | None = None, raw: bool = False) -> Any:
        if session.shot is None:
            return {"rows": [], "warnings": []}
        processed, warnings = (None, []) if raw else session.processed_channels()
        rows = measurement_table(
            session.shot,
            channels=channels.split(",") if channels else None,
            processed=processed,
        )
        return {"rows": rows, "warnings": warnings}

    @app.get("/api/export")
    def get_export(
        format: Literal["csv", "npz"] = "csv",
        channels: str | None = None,
        raw: bool = False,
        max_points: int = 0,
    ) -> Response:
        """Download the current shot's traces.

        PNG is deliberately absent: Plotly's own toolbar already saves the
        figure exactly as drawn, without a headless renderer here.
        """
        if session.shot is None:
            raise HTTPException(400, "no shot yet; capture or load one first")
        processed, _ = (None, []) if raw else session.processed_channels()
        selection = channels.split(",") if channels else None
        try:
            payload, media_type, filename = export_shot(
                session.shot,
                fmt=format,
                channels=selection,
                processed=processed,
                max_points=max_points,
            )
        except (KeyError, ValueError) as exc:
            raise _error(exc) from exc
        return Response(
            content=payload,
            media_type=media_type,
            headers={"content-disposition": f'attachment; filename="{filename}"'},
        )

    # -- log and events ----------------------------------------------------

    @app.get("/api/log")
    def get_log() -> dict[str, Any]:
        return {"log": session.log}

    @app.delete("/api/log")
    def delete_log() -> dict[str, Any]:
        session.clear_log()
        return {"log": []}

    @app.websocket("/ws")
    async def websocket(socket: WebSocket) -> None:
        await socket.accept()
        queue = session.subscribe()
        try:
            await socket.send_json({"type": "status", **_status_payload(session)})
            while True:
                payload = await queue.get()
                await socket.send_json(payload)
        except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
            pass
        finally:
            session.unsubscribe(queue)

    # -- static ------------------------------------------------------------

    @app.get("/")
    def index() -> HTMLResponse:
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(page.replace('<base href="/">', f'<base href="{base}">'))

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _build_figure(
    panel: str,
    shot: Any,
    processed: dict[str, Any] | None,
    *,
    layout: LayoutMode,
    selection: list[str] | None,
    a: str | None,
    b: str | None,
    psd: bool,
    log_x: bool,
    log_y: bool,
    window: str,
    max_points: int,
    frequency: float | None = None,
) -> dict[str, Any]:
    if panel == "time":
        return time_figure(
            shot,
            layout=layout,
            channels=selection,
            processed=processed,
            max_points=max_points,
        )
    if panel == "fft":
        return fft_figure(
            shot,
            channels=selection,
            processed=processed,
            window=window,
            psd=psd,
            log_x=log_x,
            log_y=log_y,
            max_points=max_points,
        )
    if panel == "spectrogram":
        if a is None:
            raise ValueError("a spectrogram needs a channel; pass 'a'")
        return spectrogram_figure(shot, a, processed=processed, window=window)
    if panel == "vector":
        if frequency is None:
            raise ValueError("a field vector needs a frequency; pass 'frequency'")
        if not selection or len(selection) != 3:
            raise ValueError(
                "a field vector needs exactly three channels; pass them as "
                "'channels=a,b,c'"
            )
        return vector_figure(shot, selection, frequency, processed=processed)
    if a is None or b is None:
        raise ValueError(f"the {panel} panel needs two channels; pass 'a' and 'b'")
    if panel == "xy":
        return xy_figure(shot, a, b, processed=processed)
    if panel == "coherence":
        return coherence_figure(shot, a, b, processed=processed, max_points=max_points)
    return transfer_figure(shot, a, b, processed=processed, max_points=max_points)


def _status_payload(session: StudioSession) -> dict[str, Any]:
    status = session.status
    return {
        "connected": session.connected,
        "busy": status.busy,
        "shot_id": status.shot_id,
        "complete": status.complete,
        "arm_spread_s": status.arm_spread_s,
        "failures": list(status.failures),
        "message": status.message,
        "has_shot": session.shot is not None,
    }


def _shot_payload(session: StudioSession) -> dict[str, Any]:
    shot = session.shot
    if shot is None:
        return {"shot": None, "status": _status_payload(session)}

    captures = [
        {
            "label": label,
            "channels": list(capture.channels),
            "keys": [f"{label}:{name}" for name in capture.channels],
            "n_samples": capture.n_samples,
            "dt": capture.dt,
            "t0": capture.t0,
            "sample_rate": 1.0 / capture.dt,
            "offset": shot.time_offset(label),
            "metadata": _jsonable(capture.metadata),
        }
        for label, capture in shot.captures.items()
    ]

    fitted = {
        label: {
            "offset": offset.offset,
            "correlation": offset.correlation,
            "inverted": offset.inverted,
            "max_lag": offset.max_lag,
        }
        for label, offset in session.fitted_offsets.items()
    }

    instruments: list[dict[str, Any]] = []
    if session.result is not None:
        instruments = [
            {
                "label": item.label,
                "state": item.state,
                "error": item.error,
                "forced": item.forced,
                "late_armed": item.late_armed,
                "required": item.required,
                "n_captures": len(item.captures),
            }
            for item in session.result.results
        ]

    return {
        "shot": {
            "reference": shot.reference,
            "captures": captures,
            "fitted_offsets": fitted,
            "instruments": instruments,
            "metadata": _jsonable(shot.metadata),
        },
        "status": _status_payload(session),
    }


def _jsonable(value: Any) -> Any:
    """Drop anything a metadata dict might hold that JSON cannot carry."""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
