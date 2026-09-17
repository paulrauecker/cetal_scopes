"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  shot: null,
  // Keys the user switched off. Hiding rather than listing what is shown
  // means a new shot's channels arrive visible.
  hidden: new Set(),
  status: {},
  catalog: [],
  pipeline: [],
};

async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail || response.statusText;
    setStatus(`error: ${detail}`, "bad");
    throw new Error(detail);
  }
  return payload;
}

const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });

const put = (path, body) =>
  api(path, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

function setStatus(text, kind) {
  const node = $("status");
  node.textContent = text;
  node.className = "status" + (kind ? ` ${kind}` : "");
}

function applyStatus(status) {
  state.status = status;
  $("connect").disabled = status.connected;
  $("disconnect").disabled = !status.connected;
  $("capture").disabled = status.busy;
  $("abort").disabled = !status.busy;
  let kind = "";
  if (status.busy) kind = "busy";
  else if (status.complete === false) kind = "bad";
  else if (status.complete === true) kind = "good";
  setStatus(status.message || "idle", kind);
}

// ---------------------------------------------------------------------------
// Channel pickers

function channelKeys() {
  if (!state.shot) return [];
  return state.shot.captures.flatMap((capture) => capture.keys);
}

function fillChannelPickers() {
  const keys = channelKeys();
  for (const id of ["chan-a", "chan-b", "chan-c"]) {
    const select = $(id);
    const previous = select.value;
    select.innerHTML = "";
    for (const key of keys) {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = key;
      select.append(option);
    }
    if (keys.includes(previous)) select.value = previous;
    else if (id === "chan-b" && keys.length > 1) select.value = keys[1];
    else if (id === "chan-c" && keys.length > 2) select.value = keys[2];
  }
}

function visibleChannels() {
  return channelKeys().filter((key) => !state.hidden.has(key));
}

function renderChannelToggles() {
  const host = $("channel-toggles");
  host.innerHTML = "";
  if (!state.shot) return;

  for (const capture of state.shot.captures) {
    const name = document.createElement("span");
    name.className = "capture-name";
    name.textContent = capture.label;
    host.append(name);

    for (const key of capture.keys) {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = !state.hidden.has(key);
      box.addEventListener("change", () => {
        if (box.checked) state.hidden.delete(key);
        else state.hidden.add(key);
        refreshFigures();
      });

      const label = document.createElement("label");
      label.append(box, key.split(":").slice(1).join(":") || key);
      label.title = key;
      host.append(label);
    }
  }
}

function setAllChannels(visible) {
  if (visible) state.hidden.clear();
  else for (const key of channelKeys()) state.hidden.add(key);
  renderChannelToggles();
  refreshFigures();
}

function updatePairControls() {
  const panel = $("pair-panel").value;
  const vector = panel === "vector";
  $("chan-c-wrap").hidden = !vector;
  $("vector-f-wrap").hidden = !vector;
  // A spectrogram is a single-channel view; B would be ignored.
  $("chan-b").parentElement.hidden = panel === "spectrogram";
}

// ---------------------------------------------------------------------------
// Offsets

function renderOffsets() {
  const host = $("offsets");
  host.innerHTML = "";
  if (!state.shot) {
    host.textContent = "";
    return;
  }
  const fitted = state.shot.fitted_offsets || {};
  for (const capture of state.shot.captures) {
    const row = document.createElement("div");
    row.className = "offset-row";

    const label = document.createElement("span");
    label.className = "label";
    label.textContent = capture.label;
    if (capture.label === state.shot.reference) label.textContent += " (ref)";
    row.append(label);

    const input = document.createElement("input");
    input.type = "number";
    input.step = "0.1";
    input.value = (capture.offset * 1e9).toFixed(3);
    input.title = "Offset in nanoseconds added to this capture's time axis";
    input.addEventListener("change", async () => {
      const offsets = {};
      offsets[capture.label] = Number(input.value) * 1e-9;
      const payload = await post("api/shot/offsets", { offsets });
      adoptShot(payload);
      await refreshFigures();
    });
    row.append(input);

    const unit = document.createElement("span");
    unit.className = "fit";
    unit.textContent = "ns";
    row.append(unit);

    const fit = fitted[capture.label];
    if (fit) {
      const note = document.createElement("span");
      note.className = "fit";
      note.textContent =
        `fit r=${fit.correlation.toFixed(3)}` + (fit.inverted ? ", inverted" : "");
      // A low correlation means the fit found nothing convincing; say so
      // rather than letting the number look authoritative.
      if (Math.abs(fit.correlation) < 0.5) note.textContent += " — weak";
      row.append(note);
    }
    host.append(row);
  }
}

function renderInstruments() {
  const host = $("instruments");
  host.innerHTML = "";
  if (!state.shot || !state.shot.instruments.length) return;

  const table = document.createElement("table");
  table.innerHTML =
    "<thead><tr><th>instrument</th><th>state</th><th>captures</th>" +
    "<th>forced</th><th>note</th></tr></thead>";
  const body = document.createElement("tbody");
  for (const item of state.shot.instruments) {
    const row = document.createElement("tr");
    const note = [];
    if (item.late_armed) note.push("late-armed");
    if (!item.required) note.push("optional");
    if (item.error) note.push(item.error);
    row.innerHTML =
      `<td>${item.label}</td>` +
      `<td class="${item.state === "ok" ? "state-ok" : "state-bad"}">${item.state}</td>` +
      `<td>${item.n_captures}</td>` +
      `<td>${item.forced ? "yes" : ""}</td>` +
      `<td style="text-align:left">${note.join("; ")}</td>`;
    body.append(row);
  }
  table.append(body);
  host.append(table);

  const spread = state.status.arm_spread_s;
  if (spread != null) {
    const note = document.createElement("div");
    note.className = "note";
    note.style.padding = "6px 0 0";
    note.textContent =
      `arm spread ${(spread * 1e3).toFixed(2)} ms — the window between the ` +
      "first and last instrument arming, not clock skew.";
    host.append(note);
  }
}

// ---------------------------------------------------------------------------
// Processing

function renderCatalog() {
  const select = $("step-name");
  select.innerHTML = "";
  for (const info of state.catalog) {
    const option = document.createElement("option");
    option.value = info.name;
    option.textContent = info.name;
    option.title = info.summary;
    select.append(option);
  }
  select.addEventListener("change", showSummary);
  showSummary();
}

function showSummary() {
  const info = state.catalog.find((item) => item.name === $("step-name").value);
  $("step-summary").textContent = info ? info.summary : "";
}

function renderPipeline() {
  const host = $("pipeline");
  host.innerHTML = "";
  state.pipeline.forEach((step, index) => {
    const info = state.catalog.find((item) => item.name === step.name);
    const row = document.createElement("div");
    row.className = "step";

    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = step.enabled !== false;
    toggle.title = "Enable this step";
    toggle.addEventListener("change", () => {
      step.enabled = toggle.checked;
      savePipeline();
    });
    row.append(toggle);

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = step.name;
    if (info) name.title = info.summary;
    row.append(name);

    for (const key of Object.keys(info ? info.params : step.params)) {
      const label = document.createElement("label");
      label.textContent = key;
      const input = document.createElement("input");
      const current = step.params[key];
      const fallback = info ? info.params[key] : null;
      const numeric =
        typeof current === "number" ||
        typeof fallback === "number" ||
        current == null;
      input.type = numeric && typeof fallback !== "string" ? "number" : "text";
      input.step = "any";
      input.value = current == null ? "" : current;
      input.placeholder = fallback == null ? "default" : String(fallback);
      input.addEventListener("change", () => {
        const raw = input.value.trim();
        if (raw === "") delete step.params[key];
        else step.params[key] = input.type === "number" ? Number(raw) : raw;
        savePipeline();
      });
      label.append(input);
      row.append(label);
    }

    const remove = document.createElement("button");
    remove.className = "small";
    remove.textContent = "remove";
    remove.addEventListener("click", () => {
      state.pipeline.splice(index, 1);
      savePipeline();
    });
    row.append(remove);

    host.append(row);
  });
}

async function savePipeline() {
  const payload = await put("api/processing", { steps: state.pipeline });
  state.pipeline = payload.steps;
  renderPipeline();
  await refreshFigures();
}

// ---------------------------------------------------------------------------
// Figures

const PLOT_CONFIG = { responsive: true, displaylogo: false };

async function drawFigure(target, query) {
  const payload = await api(`api/figure?${new URLSearchParams(query)}`);
  const figure = payload.figure;
  Plotly.react(target, figure.data, figure.layout, PLOT_CONFIG);
  return payload.warnings || [];
}

async function refreshFigures() {
  if (!state.shot) return;
  const raw = $("raw").checked ? "true" : "false";
  // Always explicit: an empty value means none, which is what an empty
  // selection should draw.
  const channels = visibleChannels().join(",");

  const warnings = await drawFigure("time-plot", {
    panel: "time",
    layout: $("layout").value,
    channels,
    raw,
  });
  $("pipeline-warnings").textContent = warnings.join(" · ");

  await drawFigure("fft-plot", {
    panel: "fft",
    channels,
    psd: $("psd").checked,
    log_x: $("logx").checked,
    log_y: $("logy").checked,
    window: $("window").value,
    raw,
  });

  await refreshPairFigure();
}

async function refreshPairFigure() {
  updatePairControls();
  if (!state.shot) return;
  const panel = $("pair-panel").value;
  const a = $("chan-a").value;
  const b = $("chan-b").value;
  const c = $("chan-c").value;
  if (!a) return;

  const query = { panel, raw: $("raw").checked, window: $("window").value };
  if (panel === "vector") {
    if (!b || !c) return;
    query.channels = [a, b, c].join(",");
    query.frequency = $("vector-f").value;
  } else {
    if (panel !== "spectrogram" && !b) return;
    query.a = a;
    query.b = b;
  }

  try {
    await drawFigure("pair-plot", query);
  } catch {
    /* the error is already in the status line */
  }
}

async function refreshMeasurements() {
  const payload = await api(`api/measurements?raw=${$("raw").checked}`);
  const host = $("measurements");
  host.innerHTML = "";
  if (!payload.rows.length) return;

  const columns = [
    ["channel", null],
    ["unit", null],
    ["rms", 5],
    ["peak_to_peak", 5],
    ["amplitude", 5],
    ["rise_time", 4],
    ["fall_time", 4],
    ["fwhm", 4],
    ["overshoot", 3],
    ["peak_time", 5],
  ];
  const table = document.createElement("table");
  table.innerHTML =
    "<thead><tr>" +
    columns.map(([name]) => `<th>${name.replace(/_/g, " ")}</th>`).join("") +
    "</tr></thead>";
  const body = document.createElement("tbody");
  for (const item of payload.rows) {
    const row = document.createElement("tr");
    row.innerHTML = columns
      .map(([name, digits]) => {
        const value = item[name];
        if (value == null) return "<td>—</td>";
        if (digits == null) return `<td>${value}</td>`;
        return `<td>${Number(value).toPrecision(digits)}</td>`;
      })
      .join("");
    body.append(row);
  }
  table.append(body);
  host.append(table);
}

// ---------------------------------------------------------------------------
// Shot state

function adoptShot(payload) {
  if (payload.status) applyStatus(payload.status);
  if (payload.shot !== undefined) state.shot = payload.shot;
  fillChannelPickers();
  renderChannelToggles();
  renderOffsets();
  renderInstruments();
  renderShotMeta();
}

function renderShotMeta() {
  if (!state.shot) {
    $("shot-meta").textContent = "";
    return;
  }
  const parts = state.shot.captures.map(
    (capture) =>
      `${capture.label}: ${capture.channels.length} ch, ` +
      `${capture.n_samples} pts @ ${(capture.sample_rate / 1e6).toPrecision(4)} MS/s`,
  );
  $("shot-meta").textContent = parts.join(" · ");
}

// ---------------------------------------------------------------------------
// Log and events

function appendLog(text) {
  const node = $("log");
  node.textContent += text + "\n";
  node.scrollTop = node.scrollHeight;
}

function describeEvent(event) {
  if (event.type === "log") return event.message;
  if (event.type === "phase") {
    const spread = event.detail && event.detail.arm_spread_s;
    const extra = spread != null ? ` (spread ${(spread * 1e3).toFixed(2)} ms)` : "";
    return `— ${event.phase}${extra}`;
  }
  if (event.type === "instrument") {
    const detail = event.detail || {};
    const bits = Object.entries(detail)
      .filter(([, value]) => value !== null && value !== false)
      .map(([key, value]) => `${key}=${value}`);
    return `  ${event.label}: ${event.state}${bits.length ? " " + bits.join(" ") : ""}`;
  }
  if (event.type === "result") {
    const detail = event.detail || {};
    const failures = detail.failures || [];
    return (
      `= ${detail.complete ? "complete" : "incomplete"}` +
      (failures.length ? ` — failed: ${failures.join(", ")}` : "")
    );
  }
  return null;
}

function openSocket() {
  // Relative to <base>, so the socket follows the app under a URL prefix.
  const url = new URL("ws", document.baseURI);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(url);
  socket.addEventListener("message", (message) => {
    const event = JSON.parse(message.data);
    if (event.type === "status") {
      applyStatus(event);
      return;
    }
    const line = describeEvent(event);
    if (line) appendLog(line);
    if (event.type === "instrument" && event.state) {
      setStatus(`${event.label}: ${event.state}`, "busy");
    }
  });
  // The page is useless without progress, so keep trying to get it back.
  socket.addEventListener("close", () => setTimeout(openSocket, 2000));
}

// ---------------------------------------------------------------------------
// Wiring

function wire() {
  $("connect").addEventListener("click", async () => {
    applyStatus(await post("api/connect"));
  });
  $("disconnect").addEventListener("click", async () => {
    applyStatus(await post("api/disconnect"));
  });
  $("abort").addEventListener("click", () => post("api/abort"));
  $("channels-all").addEventListener("click", () => setAllChannels(true));
  $("channels-none").addEventListener("click", () => setAllChannels(false));
  $("capture").addEventListener("click", async () => {
    const payload = await post("api/capture", {
      direct_trigger: $("direct").checked ? true : null,
    });
    adoptShot(payload);
    await refreshFigures();
    await refreshMeasurements();
  });

  $("autofit").addEventListener("click", async () => {
    const payload = await post("api/shot/autofit");
    adoptShot(payload);
    const fitted = Object.entries(payload.shot.fitted_offsets || {});
    $("autofit-note").textContent = fitted.length
      ? fitted
          .map(([label, fit]) => `${label} ${(fit.offset * 1e9).toFixed(2)} ns`)
          .join(" · ")
      : "nothing to fit";
    await refreshFigures();
  });

  $("refresh").addEventListener("click", refreshFigures);
  $("measure").addEventListener("click", refreshMeasurements);
  for (const id of ["layout", "raw"]) {
    $(id).addEventListener("change", refreshFigures);
  }
  for (const id of ["psd", "logx", "logy", "window"]) {
    $(id).addEventListener("change", refreshFigures);
  }
  for (const id of ["pair-panel", "chan-a", "chan-b", "chan-c", "vector-f"]) {
    $(id).addEventListener("change", refreshPairFigure);
  }

  $("step-add").addEventListener("click", () => {
    state.pipeline.push({ name: $("step-name").value, params: {}, enabled: true });
    savePipeline();
  });

  $("shot-save").addEventListener("click", async () => {
    const path = $("shot-path").value.trim();
    if (!path) return;
    const payload = await post("api/shot/save", { path });
    appendLog(`saved ${payload.path}`);
  });
  $("shot-load").addEventListener("click", async () => {
    const path = $("shot-path").value.trim();
    if (!path) return;
    adoptShot(await post("api/shot/load", { path }));
    await refreshFigures();
    await refreshMeasurements();
  });

  for (const [id, format] of [["export-csv", "csv"], ["export-npz", "npz"]]) {
    $(id).addEventListener("click", () => {
      // A plain navigation, so the browser handles the download itself.
      const raw = $("raw").checked;
      window.location.href = `api/export?format=${format}&raw=${raw}`;
    });
  }

  $("log-clear").addEventListener("click", async () => {
    await api("api/log", { method: "DELETE" });
    $("log").textContent = "";
  });
}

async function start() {
  wire();
  updatePairControls();
  openSocket();

  const processing = await api("api/processing");
  state.catalog = processing.catalog;
  state.pipeline = processing.steps;
  renderCatalog();
  renderPipeline();

  applyStatus(await api("api/status"));
  const shot = await api("api/shot");
  if (shot.shot) {
    adoptShot(shot);
    await refreshFigures();
    await refreshMeasurements();
  }
}

start();
