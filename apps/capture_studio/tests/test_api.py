"""The HTTP surface, driven end to end against synthetic instruments."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from api import create_app, normalise_root_path, with_root_path
from config import demo_inventory
from fastapi.testclient import TestClient
from session import StudioSession


@pytest.fixture
def session() -> StudioSession:
    # Two instruments, different skews and sample rates, triggering promptly.
    inventory = demo_inventory(2, channels_each=2)
    for item in inventory.instruments:
        item.settings["record_length"] = 2048
        item.options["trigger_delay"] = 0.0
        item.direct_trigger = True
    inventory.default_timeout = 5.0
    inventory.poll_interval = 0.01
    return StudioSession(inventory)


@pytest.fixture
def client(session: StudioSession) -> Iterator[TestClient]:
    with TestClient(create_app(session)) as test_client:
        yield test_client


def capture(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/capture", json={"direct_trigger": True})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def test_the_inventory_is_served_with_the_driver_list(client: TestClient) -> None:
    payload = client.get("/api/inventory").json()

    assert [item["label"] for item in payload["inventory"]["instruments"]] == [
        "demo1",
        "demo2",
    ]
    assert "siglent_sds6204l" in payload["drivers"]


def test_the_inventory_can_be_replaced(client: TestClient) -> None:
    replacement = demo_inventory(3).model_dump()
    response = client.put("/api/inventory", json={"inventory": replacement})

    assert response.status_code == 200
    assert len(response.json()["inventory"]["instruments"]) == 3


def test_an_invalid_inventory_is_rejected_with_a_reason(client: TestClient) -> None:
    response = client.put(
        "/api/inventory",
        json={"inventory": {"instruments": [{"label": "a", "driver": "nope"}]}},
    )

    assert response.status_code == 400
    assert "unknown driver" in response.json()["detail"]


def test_the_inventory_can_be_written_to_disk(
    client: TestClient, tmp_path: Path
) -> None:
    target = tmp_path / "bench.toml"
    response = client.post("/api/inventory/save", json={"path": str(target)})

    assert response.status_code == 200
    assert target.is_file()
    assert "[[instrument]]" in target.read_text()


def test_saving_without_a_path_is_refused(client: TestClient) -> None:
    response = client.post("/api/inventory/save", json=None)
    assert response.status_code == 400
    assert "no inventory path" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Connect and capture
# ---------------------------------------------------------------------------


def test_connecting_reports_the_instruments(client: TestClient) -> None:
    payload = client.post("/api/connect").json()

    assert payload["connected"] is True
    assert "demo1" in payload["message"]


def test_capture_produces_a_complete_shot(client: TestClient) -> None:
    payload = capture(client)

    assert payload["status"]["complete"] is True
    assert payload["status"]["failures"] == []
    assert [item["label"] for item in payload["shot"]["captures"]] == [
        "demo1",
        "demo2",
    ]
    assert payload["shot"]["reference"] == "demo1"
    assert all(item["state"] == "ok" for item in payload["shot"]["instruments"])


def test_capture_connects_on_demand(client: TestClient) -> None:
    assert client.get("/api/status").json()["connected"] is False
    capture(client)
    assert client.get("/api/status").json()["connected"] is True


def test_the_arm_spread_is_reported(client: TestClient) -> None:
    payload = capture(client)
    spread = payload["status"]["arm_spread_s"]

    assert spread is not None
    assert spread >= 0.0


def test_an_instrument_that_never_triggers_leaves_a_partial_shot(
    session: StudioSession,
) -> None:
    # The second instrument waits for an external trigger that never comes.
    session.inventory.instruments[1].direct_trigger = False
    session.inventory.instruments[1].options["trigger_delay"] = None
    session.inventory.instruments[1].timeout = 0.2

    with TestClient(create_app(session)) as client:
        payload = client.post("/api/capture", json={}).json()

    assert payload["status"]["complete"] is False
    assert payload["status"]["failures"] == ["demo2"]
    # The good capture survives; the timed-out one contributes nothing, since
    # an untriggered instrument's buffer is from the wrong shot.
    assert [item["label"] for item in payload["shot"]["captures"]] == ["demo1"]
    states = {item["label"]: item["state"] for item in payload["shot"]["instruments"]}
    assert states == {"demo1": "ok", "demo2": "timeout"}


def test_status_before_any_shot(client: TestClient) -> None:
    payload = client.get("/api/shot").json()
    assert payload["shot"] is None
    assert payload["status"]["has_shot"] is False


def test_disconnect_releases_the_instruments(client: TestClient) -> None:
    client.post("/api/connect")
    payload = client.post("/api/disconnect").json()
    assert payload["connected"] is False


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def test_offsets_can_be_set_by_hand(client: TestClient) -> None:
    capture(client)
    payload = client.post(
        "/api/shot/offsets", json={"offsets": {"demo2": 1.25e-8}}
    ).json()

    offsets = {item["label"]: item["offset"] for item in payload["shot"]["captures"]}
    assert offsets["demo2"] == pytest.approx(1.25e-8)


def test_autofit_measures_the_skew_between_instruments(client: TestClient) -> None:
    capture(client)
    payload = client.post("/api/shot/autofit", json={}).json()

    fitted = payload["shot"]["fitted_offsets"]
    assert set(fitted) == {"demo2"}
    # demo2 is built with a 7.5 ns skew relative to demo1.
    assert fitted["demo2"]["offset"] == pytest.approx(-7.5e-9, abs=3e-9)
    assert fitted["demo2"]["correlation"] > 0.5


def test_autofit_without_a_shot_is_refused(client: TestClient) -> None:
    response = client.post("/api/shot/autofit", json={})
    assert response.status_code == 400
    assert "no shot yet" in response.json()["detail"]


def test_setting_an_offset_for_an_unknown_capture_is_refused(
    client: TestClient,
) -> None:
    capture(client)
    response = client.post("/api/shot/offsets", json={"offsets": {"ghost": 1.0}})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["overlay", "per-capture", "per-channel"])
def test_every_layout_renders(client: TestClient, layout: str) -> None:
    capture(client)
    payload = client.get(f"/api/figure?panel=time&layout={layout}").json()

    assert len(payload["figure"]["data"]) == 4  # two instruments, two channels
    json.dumps(payload)  # the whole thing must be JSON-serialisable


@pytest.mark.parametrize("panel", ["time", "fft"])
def test_a_channel_selection_narrows_the_all_channel_panels(
    client: TestClient, panel: str
) -> None:
    capture(client)
    selection = "demo1:CH1,demo2:CH2"
    payload = client.get(f"/api/figure?panel={panel}&channels={selection}").json()

    names = [trace["name"] for trace in payload["figure"]["data"]]
    assert names == ["demo1:CH1", "demo2:CH2"]


@pytest.mark.parametrize("panel", ["time", "fft"])
def test_an_empty_channel_selection_draws_nothing(
    client: TestClient, panel: str
) -> None:
    # Distinct from omitting the parameter, which draws every channel: this is
    # what the UI sends once every channel has been toggled off.
    capture(client)
    payload = client.get(f"/api/figure?panel={panel}&channels=").json()

    assert payload["figure"]["data"] == []
    assert client.get(f"/api/figure?panel={panel}").json()["figure"]["data"]


def test_an_unknown_layout_is_refused(client: TestClient) -> None:
    capture(client)
    assert client.get("/api/figure?panel=time&layout=spiral").status_code == 422


def test_the_fft_panel_renders(client: TestClient) -> None:
    capture(client)
    payload = client.get("/api/figure?panel=fft").json()

    assert len(payload["figure"]["data"]) == 4
    assert payload["figure"]["layout"]["xaxis"]["type"] == "log"


@pytest.mark.parametrize("panel", ["coherence", "transfer", "xy"])
def test_the_two_channel_panels_render(client: TestClient, panel: str) -> None:
    capture(client)
    response = client.get(f"/api/figure?panel={panel}&a=demo1:CH1&b=demo2:CH1")

    assert response.status_code == 200, response.text
    assert response.json()["figure"]["data"]


def test_a_two_channel_panel_without_channels_says_so(client: TestClient) -> None:
    capture(client)
    response = client.get("/api/figure?panel=coherence")

    assert response.status_code == 400
    assert "needs two channels" in response.json()["detail"]


def test_an_unknown_channel_is_reported(client: TestClient) -> None:
    capture(client)
    response = client.get("/api/figure?panel=xy&a=ghost&b=demo1:CH1")

    assert response.status_code == 400
    assert "unknown channel" in response.json()["detail"]


def test_the_spectrogram_panel_renders(client: TestClient) -> None:
    capture(client)
    payload = client.get("/api/figure?panel=spectrogram&a=demo1:CH1").json()
    assert payload["figure"]["data"][0]["type"] == "heatmap"


def test_a_figure_without_a_shot_is_empty_rather_than_an_error(
    client: TestClient,
) -> None:
    payload = client.get("/api/figure?panel=time").json()
    assert payload["figure"]["data"] == []
    assert "No shot" in payload["figure"]["layout"]["annotations"][0]["text"]


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


def test_the_step_catalog_is_served(client: TestClient) -> None:
    payload = client.get("/api/processing").json()

    names = {item["name"] for item in payload["catalog"]}
    assert {"lowpass", "bandpass", "b_field", "remove_adc_comb"} <= names
    assert payload["steps"] == []


def test_a_pipeline_changes_the_rendered_traces(client: TestClient) -> None:
    capture(client)
    before = client.get("/api/figure?panel=time").json()["figure"]["data"][0]["y"]

    client.put(
        "/api/processing",
        json={"steps": [{"name": "lowpass", "params": {"cutoff": 1e6}}]},
    )
    after = client.get("/api/figure?panel=time").json()["figure"]["data"][0]["y"]

    assert max(map(abs, after)) < max(map(abs, before))


def test_raw_bypasses_the_pipeline(client: TestClient) -> None:
    capture(client)
    client.put(
        "/api/processing",
        json={"steps": [{"name": "lowpass", "params": {"cutoff": 1e6}}]},
    )
    processed = client.get("/api/figure?panel=time").json()["figure"]["data"][0]["y"]
    raw = client.get("/api/figure?panel=time&raw=true").json()["figure"]["data"][0]["y"]

    assert max(map(abs, raw)) > max(map(abs, processed))


def test_a_step_that_cannot_run_is_warned_about_not_fatal(client: TestClient) -> None:
    capture(client)
    client.put("/api/processing", json={"steps": [{"name": "b_field", "params": {}}]})
    payload = client.get("/api/figure?panel=time").json()

    assert payload["figure"]["data"]  # the figure still renders
    assert payload["warnings"]
    assert "b_field skipped" in payload["warnings"][0]


def test_measurements_are_served(client: TestClient) -> None:
    capture(client)
    payload = client.get("/api/measurements").json()

    assert len(payload["rows"]) == 4
    assert {row["channel"] for row in payload["rows"]} == {
        "demo1:CH1",
        "demo1:CH2",
        "demo2:CH1",
        "demo2:CH2",
    }
    json.dumps(payload)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_a_shot_round_trips_through_disk(client: TestClient, tmp_path: Path) -> None:
    capture(client)
    client.put(
        "/api/processing",
        json={"steps": [{"name": "detrend", "params": {"type": "linear"}}]},
    )
    client.post("/api/shot/autofit", json={})
    fitted = client.get("/api/shot").json()["shot"]["captures"]
    offsets = {item["label"]: item["offset"] for item in fitted}

    target = tmp_path / "shot001"
    assert client.post("/api/shot/save", json={"path": str(target)}).status_code == 200

    payload = client.post("/api/shot/load", json={"path": str(target)}).json()
    reloaded = {item["label"]: item["offset"] for item in payload["shot"]["captures"]}

    assert reloaded == offsets
    # The pipeline travels with the shot, which is what makes it reproducible.
    assert payload["shot"]["metadata"]["processing"][0]["name"] == "detrend"


def test_saving_without_a_shot_is_refused(client: TestClient, tmp_path: Path) -> None:
    response = client.post("/api/shot/save", json={"path": str(tmp_path / "s")})
    assert response.status_code == 400
    assert "no shot yet" in response.json()["detail"]


def test_loading_a_missing_shot_is_refused(client: TestClient, tmp_path: Path) -> None:
    response = client.post("/api/shot/load", json={"path": str(tmp_path / "nope")})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Log, events and the page itself
# ---------------------------------------------------------------------------


def test_the_log_accumulates_and_can_be_cleared(client: TestClient) -> None:
    capture(client)
    assert client.get("/api/log").json()["log"]

    client.delete("/api/log")
    assert client.get("/api/log").json()["log"] == []


def test_the_websocket_streams_progress(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        first = socket.receive_json()
        assert first["type"] == "status"

        client.post("/api/capture", json={"direct_trigger": True})

        phases = []
        for _ in range(40):
            event = socket.receive_json()
            if event.get("type") == "phase":
                phases.append(event["phase"])
            if event.get("type") == "result":
                break
        assert "arming" in phases
        assert "armed" in phases


def test_the_page_and_its_assets_are_served(client: TestClient) -> None:
    assert client.get("/").status_code == 200
    assert "Capture studio" in client.get("/").text
    for asset in ("app.js", "styles.css", "plotly.min.js"):
        assert client.get(f"/static/{asset}").status_code == 200


def test_the_app_can_also_be_driven_over_asgi_transport(
    session: StudioSession,
) -> None:
    # The same surface, without TestClient's thread, as a smoke test that
    # nothing in the request path needs a running event loop of its own.
    async def main() -> dict[str, Any]:
        transport = httpx.ASGITransport(app=create_app(session))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://studio"
        ) as http:
            response = await http.post(
                "/api/capture", json={"direct_trigger": True}, timeout=30.0
            )
            response.raise_for_status()
            return response.json()

    import asyncio

    payload = asyncio.run(main())
    assert payload["status"]["complete"] is True


# ---------------------------------------------------------------------------
# Vector panel and export
# ---------------------------------------------------------------------------


def test_the_vector_panel_needs_three_channels_from_one_capture(
    client: TestClient,
) -> None:
    capture(client)
    response = client.get(
        "/api/figure?panel=vector&frequency=2e7&channels=demo1:CH1,demo1:CH2"
    )

    assert response.status_code == 400
    assert "exactly three channels" in response.json()["detail"]


def test_the_vector_panel_needs_a_frequency(client: TestClient) -> None:
    capture(client)
    response = client.get("/api/figure?panel=vector&channels=demo1:CH1")

    assert response.status_code == 400
    assert "needs a frequency" in response.json()["detail"]


def test_the_vector_panel_renders_for_a_three_channel_capture(
    session: StudioSession,
) -> None:
    for item in session.inventory.instruments:
        item.channels = ["X", "Y", "Z"]
        item.settings["channels"] = ["X", "Y", "Z"]

    with TestClient(create_app(session)) as client:
        capture(client)
        response = client.get(
            "/api/figure?panel=vector&frequency=2e7&channels=demo1:X,demo1:Y,demo1:Z"
        )

    assert response.status_code == 200, response.text
    assert response.json()["figure"]["data"][0]["type"] == "scatter3d"


def test_csv_export_is_downloadable(client: TestClient) -> None:
    capture(client)
    response = client.get("/api/export?format=csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.splitlines()[0].startswith("time_s,demo1:CH1")


def test_npz_export_is_downloadable(client: TestClient) -> None:
    import io

    import numpy as np

    capture(client)
    response = client.get("/api/export?format=npz")
    loaded = np.load(io.BytesIO(response.content), allow_pickle=False)

    assert response.status_code == 200
    assert "demo1__CH1__v" in loaded


def test_export_without_a_shot_is_refused(client: TestClient) -> None:
    response = client.get("/api/export?format=csv")
    assert response.status_code == 400
    assert "no shot yet" in response.json()["detail"]


def test_an_unknown_export_format_is_refused(client: TestClient) -> None:
    capture(client)
    assert client.get("/api/export?format=png").status_code == 422


# ---------------------------------------------------------------------------
# Serving under a URL prefix
# ---------------------------------------------------------------------------


def test_the_page_is_served_with_a_relative_base(client: TestClient) -> None:
    body = client.get("/").text
    assert '<base href="/">' in body
    assert 'href="static/styles.css"' in body


@pytest.mark.parametrize("prefix", ["/node/pc-oscilloscope/8000", "node/host/8000/"])
def test_a_prefixed_app_serves_every_route_below_the_prefix(
    session: StudioSession, prefix: str
) -> None:
    root = normalise_root_path(prefix)
    app = with_root_path(create_app(session, prefix), prefix)

    with TestClient(app) as client:
        page = client.get(f"{root}/")
        assert page.status_code == 200
        assert f'<base href="{root}/">' in page.text
        assert client.get(f"{root}/api/status").status_code == 200
        assert client.get(f"{root}/static/app.js").status_code == 200
        # Unprefixed paths still work, so the host itself can reach the app
        # on the bind address without going through the proxy.
        assert client.get("/api/status").status_code == 200


def test_without_a_prefix_the_app_is_returned_unwrapped(session: StudioSession) -> None:
    app = create_app(session)
    assert with_root_path(app, "") is app
    assert with_root_path(app, "/") is app


# ---------------------------------------------------------------------------
# Drawing cost
# ---------------------------------------------------------------------------


def test_processed_channels_are_reused_across_a_refresh(
    session: StudioSession, client: TestClient
) -> None:
    # One screen refresh asks four endpoints for the same processed channels;
    # filtering the whole shot once per panel is most of the wait.
    capture(client)
    client.put(
        "/api/processing",
        json={"steps": [{"name": "lowpass", "params": {"cutoff": 5e7}}]},
    )

    first, _ = session.processed_channels()
    second, _ = session.processed_channels()
    assert second is first


@pytest.mark.parametrize(
    ("mutate", "kwargs"),
    [
        ("set_offsets", {"offsets": {"demo2": 5e-9}}),
        ("set_pipeline", {"steps": []}),
    ],
)
def test_the_processed_cache_follows_what_it_depends_on(
    session: StudioSession, client: TestClient, mutate: str, kwargs: dict[str, Any]
) -> None:
    capture(client)
    client.put(
        "/api/processing",
        json={"steps": [{"name": "lowpass", "params": {"cutoff": 5e7}}]},
    )
    first, _ = session.processed_channels()

    getattr(session, mutate)(**kwargs)

    assert session.processed_channels()[0] is not first


def test_the_drawing_budget_is_clamped(client: TestClient) -> None:
    # It is a drawing budget, not an analysis parameter: an unbounded value
    # is a response the browser cannot draw.
    capture(client)
    figure = client.get("/api/figure?panel=fft&max_points=100000000").json()["figure"]
    assert all(len(trace["x"]) <= 20000 for trace in figure["data"])
