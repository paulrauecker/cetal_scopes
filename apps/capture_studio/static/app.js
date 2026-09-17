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
  inventory: null,
  drivers: [],
  configPath: null,
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
        scheduleRefresh();
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
  scheduleRefresh();
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

// Every figure request carries the generation it was issued in. A slower
// panel answering after the user has moved on used to repaint the plot with
// a stale figure, which looked exactly like "the plot did not update".
let generation = 0;
let inFlight = 0;
let refreshTimer = null;

function setDrawing(active) {
  inFlight += active ? 1 : -1;
  $("drawing").hidden = inFlight <= 0;
}

async function drawFigure(target, query, token) {
  setDrawing(true);
  try {
    const payload = await api(`api/figure?${new URLSearchParams(query)}`);
    if (token !== generation) return null;
    const figure = payload.figure;
    Plotly.react(target, figure.data, figure.layout, PLOT_CONFIG);
    return payload.warnings || [];
  } finally {
    setDrawing(false);
  }
}

/** Coalesce the bursts of changes a single interaction produces. */
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refreshFigures().catch(() => {
      /* the error is already in the status line */
    });
  }, 120);
}

async function refreshFigures() {
  if (!state.shot) return;
  const token = ++generation;
  const raw = $("raw").checked ? "true" : "false";
  // Always explicit: an empty value means none, which is what an empty
  // selection should draw.
  const channels = visibleChannels().join(",");
  const detail = $("detail").value;

  // The three panels are independent requests, so they go out together and
  // one failing does not stop the others: a two-channel panel that needs a
  // selection the shot cannot satisfy must not keep the traces and the
  // spectrum from redrawing.
  const traces = drawFigure(
    "time-plot",
    { panel: "time", layout: $("layout").value, channels, raw, max_points: detail },
    token,
  ).then((warnings) => {
    if (warnings) $("pipeline-warnings").textContent = warnings.join(" · ");
  });

  const spectrum = drawFigure(
    "fft-plot",
    {
      panel: "fft",
      channels,
      psd: $("psd").checked,
      log_x: $("logx").checked,
      log_y: $("logy").checked,
      window: $("window").value,
      max_points: detail,
      raw,
    },
    token,
  );

  await Promise.allSettled([traces, spectrum, refreshPairFigure(token)]);
}

async function refreshPairFigure(token) {
  updatePairControls();
  if (!state.shot) return;
  if (token === undefined) token = ++generation;
  const panel = $("pair-panel").value;
  const a = $("chan-a").value;
  const b = $("chan-b").value;
  const c = $("chan-c").value;
  if (!a) return;

  const query = {
    panel,
    raw: $("raw").checked,
    window: $("window").value,
    max_points: $("detail").value,
  };
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
    await drawFigure("pair-plot", query, token);
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
// Inventory (what the instruments are told before a shot)
//
// The inventory is the same structure as the TOML file, so what the UI edits
// is what a bench setup is made of; "Apply" hands it to the session and
// "Save to file" writes it back. Only the shared physical vocabulary gets its
// own field. Panel-native keys and the per-channel mapping form (a dict of
// channel to value) stay in the JSON box rather than being flattened into a
// single number they are not.

const SETTING_FIELDS = [
  ["sample_rate", "number", "Hz"],
  ["record_length", "number", "samples"],
  ["pretrigger", "number", "fraction of the record (0-1), or whole samples"],
  ["range", "number", "V full-scale (the channel spans ±range)"],
  ["offset", "number", "V"],
  ["coupling", "text", "DC / AC"],
  ["impedance", "number", "ohms"],
];

const TRIGGER_FIELDS = [
  ["source", "text", "e.g. C1, EXT, EX5"],
  ["level", "number", "V"],
  ["slope", "text", "RISing / FALLing"],
];

function field(label, node, hint) {
  const wrap = document.createElement("label");
  wrap.append(label, node);
  if (hint) wrap.title = hint;
  return wrap;
}

function textInput(value, { type = "text", width = "8em", step = "any" } = {}) {
  const input = document.createElement("input");
  input.type = type;
  if (type === "number") input.step = step;
  input.style.width = width;
  input.value = value == null ? "" : value;
  return input;
}

function readNumber(input) {
  const raw = input.value.trim();
  if (raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

/** Settings that get their own field, given what the instrument currently holds. */
function splitSettings(settings) {
  const scalar = {};
  const advanced = {};
  const known = new Set(SETTING_FIELDS.map(([name]) => name));
  for (const [key, value] of Object.entries(settings || {})) {
    if (key === "trigger" && value && typeof value === "object") continue;
    if (key === "channels") continue; // edited as the instrument's channel list
    if (known.has(key) && (value === null || typeof value !== "object")) {
      scalar[key] = value;
    } else {
      advanced[key] = value;
    }
  }
  return { scalar, advanced };
}

function jsonBox(value, rows = 2) {
  const box = document.createElement("textarea");
  box.rows = rows;
  box.spellcheck = false;
  box.value =
    value && Object.keys(value).length ? JSON.stringify(value, null, 1) : "";
  return box;
}

function readJson(box, what) {
  const raw = box.value.trim();
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("not an object");
    }
    box.classList.remove("bad-input");
    return parsed;
  } catch (error) {
    box.classList.add("bad-input");
    throw new Error(`${what} is not a JSON object: ${error.message}`);
  }
}

function renderInventory() {
  const host = $("inventory");
  host.innerHTML = "";
  const inventory = state.inventory;
  $("inv-path").textContent = state.configPath
    ? `file: ${state.configPath}`
    : "no inventory file; give a path when saving";
  if (!inventory) return;

  $("inv-poll").value = inventory.poll_interval;
  $("inv-default-timeout").value = inventory.default_timeout;
  $("inv-arm-timeout").value = inventory.arm_timeout;

  inventory.instruments.forEach((item, index) => {
    const card = document.createElement("div");
    card.className = "instrument";
    if (!item.enabled) card.classList.add("off");

    const head = document.createElement("div");
    head.className = "instrument-head";

    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.checked = item.enabled;
    enabled.addEventListener("change", () => {
      item.enabled = enabled.checked;
      card.classList.toggle("off", !enabled.checked);
    });
    head.append(field("on", enabled, "Keep the instrument in the file but out of the run"));

    const label = textInput(item.label, { width: "8em" });
    label.addEventListener("change", () => {
      item.label = label.value.trim();
    });
    head.append(field("label", label, "Becomes the capture's key in the shot"));

    const driver = document.createElement("select");
    for (const name of state.drivers) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      driver.append(option);
    }
    driver.value = item.driver;
    driver.addEventListener("change", () => {
      item.driver = driver.value;
    });
    head.append(field("driver", driver));

    const address = textInput(item.address, { width: "11em" });
    address.addEventListener("change", () => {
      item.address = address.value.trim() || null;
    });
    head.append(field("address", address, "IP for the Siglent, device node for the M5i"));

    const channels = textInput((item.channels || []).join(", "), { width: "11em" });
    channels.addEventListener("change", () => {
      item.channels = channels.value
        .split(",")
        .map((name) => name.trim())
        .filter(Boolean);
    });
    head.append(field("channels", channels, "Comma separated, e.g. C1, C2"));

    const timeout = textInput(item.timeout, { type: "number", width: "5em" });
    timeout.addEventListener("change", () => {
      item.timeout = readNumber(timeout);
    });
    head.append(field("timeout", timeout, "Seconds to wait for this instrument's trigger"));

    for (const [name, hint] of [
      ["direct_trigger", "Force this instrument in software once everything is armed"],
      ["required", "Whether this instrument failing makes the shot incomplete"],
    ]) {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = item[name];
      box.addEventListener("change", () => {
        item[name] = box.checked;
      });
      head.append(field(name.replace("_", " "), box, hint));
    }

    const remove = document.createElement("button");
    remove.className = "small";
    remove.textContent = "remove";
    remove.addEventListener("click", () => {
      inventory.instruments.splice(index, 1);
      renderInventory();
    });
    head.append(remove);
    card.append(head);

    const { scalar, advanced } = splitSettings(item.settings);
    const body = document.createElement("div");
    body.className = "instrument-body";
    for (const [name, type, hint] of SETTING_FIELDS) {
      const input = textInput(scalar[name], {
        type,
        width: type === "number" ? "7em" : "5em",
      });
      input.addEventListener("change", () => {
        const value = type === "number" ? readNumber(input) : input.value.trim() || null;
        if (value === null) delete item.settings[name];
        else item.settings[name] = value;
      });
      body.append(field(name.replace(/_/g, " "), input, hint));
    }

    const trigger =
      item.settings.trigger && typeof item.settings.trigger === "object"
        ? item.settings.trigger
        : null;
    for (const [name, type, hint] of TRIGGER_FIELDS) {
      const input = textInput(trigger ? trigger[name] : null, {
        type,
        width: type === "number" ? "6em" : "6em",
      });
      input.addEventListener("change", () => {
        const value = type === "number" ? readNumber(input) : input.value.trim() || null;
        const current =
          item.settings.trigger && typeof item.settings.trigger === "object"
            ? item.settings.trigger
            : {};
        if (value === null) delete current[name];
        else current[name] = value;
        if (Object.keys(current).length) item.settings.trigger = current;
        else delete item.settings.trigger;
      });
      body.append(field(`trigger ${name}`, input, hint));
    }
    card.append(body);

    const extras = document.createElement("div");
    extras.className = "instrument-extras";
    const settingsBox = jsonBox(advanced);
    settingsBox.placeholder = '{"timebase": 5e-6}';
    settingsBox.dataset.role = "settings";
    extras.append(
      field("other settings", settingsBox, "Panel-native configure() keys, as JSON"),
    );
    const optionsBox = jsonBox(item.options);
    optionsBox.placeholder = '{"acquire_timeout": 10.0}';
    optionsBox.dataset.role = "options";
    extras.append(
      field("driver options", optionsBox, "Constructor arguments, as JSON"),
    );
    card.append(extras);

    // The JSON boxes are read on Apply rather than on change, so a
    // half-typed object does not throw while it is being typed.
    card.dataset.index = String(index);
    host.append(card);
  });
}

function collectInventory() {
  const inventory = state.inventory;
  inventory.poll_interval = readNumber($("inv-poll")) ?? inventory.poll_interval;
  inventory.default_timeout =
    readNumber($("inv-default-timeout")) ?? inventory.default_timeout;
  inventory.arm_timeout = readNumber($("inv-arm-timeout")) ?? inventory.arm_timeout;

  for (const card of $("inventory").children) {
    const item = inventory.instruments[Number(card.dataset.index)];
    const advanced = readJson(
      card.querySelector('textarea[data-role="settings"]'),
      `${item.label}: other settings`,
    );
    item.options = readJson(
      card.querySelector('textarea[data-role="options"]'),
      `${item.label}: driver options`,
    );
    const { scalar } = splitSettings(item.settings);
    const trigger = item.settings.trigger;
    item.settings = { ...advanced, ...scalar };
    if (trigger && Object.keys(trigger).length) item.settings.trigger = trigger;
  }
  return inventory;
}

async function loadInventory() {
  const payload = await api("api/inventory");
  state.inventory = payload.inventory;
  state.drivers = payload.drivers;
  state.configPath = payload.config_path;
  renderInventory();
}

function wireInventory() {
  $("inv-reload").addEventListener("click", async () => {
    await loadInventory();
    $("inv-note").textContent = "reloaded";
  });

  $("inv-apply").addEventListener("click", async () => {
    let inventory;
    try {
      inventory = collectInventory();
    } catch (error) {
      setStatus(`error: ${error.message}`, "bad");
      return;
    }
    const payload = await put("api/inventory", { inventory });
    state.inventory = payload.inventory;
    renderInventory();
    applyStatus(await api("api/status"));
    $("inv-note").textContent =
      "applied — instruments disconnected; the next capture reconnects them";
  });

  $("inv-save").addEventListener("click", async () => {
    try {
      collectInventory();
    } catch (error) {
      setStatus(`error: ${error.message}`, "bad");
      return;
    }
    const path = state.configPath || $("shot-path").value.trim();
    if (!path) {
      $("inv-note").textContent =
        "no inventory file is configured; start the app with --config, or put a path in the Shot box";
      return;
    }
    // Saving writes what the session holds, so apply first.
    await put("api/inventory", { inventory: state.inventory });
    const saved = await post("api/inventory/save", { path });
    state.configPath = saved.path;
    $("inv-note").textContent = `saved to ${saved.path}`;
    renderInventory();
  });

  $("inv-add").addEventListener("click", () => {
    if (!state.inventory) return;
    const n = state.inventory.instruments.length + 1;
    state.inventory.instruments.push({
      label: `scope${n}`,
      driver: state.drivers[0] || "demo",
      address: null,
      channels: [],
      settings: {},
      timeout: null,
      direct_trigger: false,
      required: true,
      enabled: true,
      options: {},
    });
    renderInventory();
  });

  $("inv-demo").addEventListener("click", async () => {
    const payload = await api("api/inventory/demo?instruments=2");
    state.inventory = payload.inventory;
    renderInventory();
    $("inv-note").textContent = "demo setup loaded into the editor — not applied yet";
  });
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
  wireInventory();
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

  $("refresh").addEventListener("click", scheduleRefresh);
  $("measure").addEventListener("click", refreshMeasurements);
  for (const id of ["layout", "raw", "psd", "logx", "logy", "window", "detail"]) {
    $(id).addEventListener("change", scheduleRefresh);
  }
  for (const id of ["pair-panel", "chan-a", "chan-b", "chan-c", "vector-f"]) {
    $(id).addEventListener("change", () => {
      refreshPairFigure().catch(() => {});
    });
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

  await loadInventory();

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
