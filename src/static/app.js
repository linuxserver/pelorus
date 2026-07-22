/* ── State ── */
let ws = null;
let busy = false;
let sessions = [];
let activeSessionId = null;
let nextSessionId = 0;
let servers = [];
let defaultServerId = null;
let editServerId = null;
let modelAutoFetchReady = {};

const stream = {
  pane: null, thoughtToggle: null, thoughtProcess: null, thoughtScroll: null,
  currentStepBody: null, streamingThinkEl: null, streamingTextEl: null, stepCount: 0,
};

/* ── DOM refs ── */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const chatInput = $("#chat-input");
const btnSend = $("#btn-send");
const btnStop = $("#btn-stop");
const toastContainer = $("#toast-container");
const tabBar = $("#tab-bar");
const sessionContainer = $("#session-container");
const settingsModal = $("#settings-modal");
const serverSelector = $("#server-selector");

/* ── localStorage ── */
const LS_SETTINGS = "pelorus_settings";
const LS_DEFAULT_SERVER = "pelorus_default_server";

function loadSettings() {
  try { return JSON.parse(localStorage.getItem(LS_SETTINGS)); } catch { return null; }
}
function saveSettings(s) { localStorage.setItem(LS_SETTINGS, JSON.stringify(s)); }

/* ── Toast ── */
function toast(msg, type) {
  const el = document.createElement("div");
  el.className = `toast ${type || ""}`;
  const icon = type === "error" ? "exclamation-circle" : type === "success" ? "check-circle" : "info-circle";
  el.innerHTML = `<i class="fas fa-${icon}"></i> ${escapeHtml(msg)}`;
  toastContainer.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity 0.3s";
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 300);
  }, 3500);
}

/* ── Image Modal ── */
const imgModal = $("#img-modal");
const imgModalImg = $("#img-modal-img");

function openImgModal(src) {
  imgModalImg.src = src;
  imgModal.classList.add("open");
}
imgModal.querySelector(".img-modal-close").addEventListener("click", () => imgModal.classList.remove("open"));
imgModal.addEventListener("click", (e) => { if (e.target === imgModal) imgModal.classList.remove("open"); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") imgModal.classList.remove("open"); });

function makeImgClickable(img) {
  img.style.cursor = "zoom-in";
  img.addEventListener("click", () => openImgModal(img.src));
}

/* ── Escape HTML ── */
function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

/* ── Auto-resize textarea ── */
chatInput.addEventListener("input", () => {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + "px";
});

/* ═══════════════════════════════════════════
   Server Management
   ═══════════════════════════════════════════ */
async function loadServers() {
  try {
    const r = await fetch("api/servers");
    const data = await r.json();
    servers = data.servers || [];
    const backendDefault = servers.find(s => s.default);
    if (backendDefault) {
      defaultServerId = backendDefault.id;
    } else {
      const saved = localStorage.getItem(LS_DEFAULT_SERVER);
      if (saved && servers.some(s => s.id === saved)) {
        defaultServerId = saved;
      } else if (servers.length > 0) {
        defaultServerId = servers[0].id;
      }
    }
    localStorage.setItem(LS_DEFAULT_SERVER, defaultServerId);
    return servers;
  } catch { return []; }
}

function getServer(id) { return servers.find(s => s.id === id); }
function getDefaultServer() { return getServer(defaultServerId) || servers[0] || null; }

async function addServer(data) {
  const r = await fetch("api/servers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...data, default: servers.length === 0 }),
  });
  const result = await r.json();
  servers.push(result.server);
  if (servers.length === 1) {
    defaultServerId = result.server.id;
    localStorage.setItem(LS_DEFAULT_SERVER, defaultServerId);
  }
  renderServerList();
  populateServerDropdown();
  return result.server;
}

async function updateServer(id, data) {
  const r = await fetch(`api/servers/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const result = await r.json();
  const idx = servers.findIndex(s => s.id === id);
  if (idx >= 0) servers[idx] = result.server;
  renderServerList();
  return result.server;
}

async function deleteServer(id) {
  if (servers.length <= 1) { toast("Cannot delete the last server", "error"); return; }
  const wasDefault = defaultServerId === id;
  await fetch(`api/servers/${id}`, { method: "DELETE" });
  servers = servers.filter(s => s.id !== id);
  if (wasDefault) {
    defaultServerId = servers[0].id;
    localStorage.setItem(LS_DEFAULT_SERVER, defaultServerId);
    await updateServer(defaultServerId, { default: true }).catch(() => {});
    populateServerDropdown();
  }
  renderServerList();
}

async function setDefaultServer(id) {
  const oldDefault = defaultServerId;
  defaultServerId = id;
  localStorage.setItem(LS_DEFAULT_SERVER, id);
  if (oldDefault && oldDefault !== id) {
    await updateServer(oldDefault, { default: false }).catch(() => {});
  }
  await updateServer(id, { default: true }).catch(() => {});
  populateServerDropdown();
  renderServerList();
}

function buildConfig() {
  const server = getDefaultServer();
  if (!server) return null;
  return {
    provider: server.provider,
    endpoint: server.endpoint,
    model: server.model,
    api_key: server.api_key,
    vision: server.vision || false,
  };
}

/* ═══════════════════════════════════════════
   Server Dropdown (top bar)
   ═══════════════════════════════════════════ */
function populateServerDropdown() {
  if (!serverSelector) return;
  const svr = getDefaultServer();
  serverSelector.innerHTML = servers.map(s =>
    `<option value="${escapeHtml(s.id)}"${s.id === defaultServerId ? ' selected' : ''}>${escapeHtml(s.name)}</option>`
  ).join("");
  const modelEl = document.getElementById("server-badge-model");
  if (modelEl) {
    modelEl.textContent = svr ? `· ${svr.model}` : "";
  }
}

serverSelector.addEventListener("change", () => {
  const id = serverSelector.value;
  if (id && id !== defaultServerId) {
    setDefaultServer(id);
  }
  updateOpencodeBadge();
});

/* ═══════════════════════════════════════════
   Model Auto-Fetch (add/edit forms)
   ═══════════════════════════════════════════ */
function setupModelAutoFetch(prefix) {
  if (modelAutoFetchReady[prefix]) return;
  modelAutoFetchReady[prefix] = true;

  const providerEl = document.getElementById(`${prefix}-provider`);
  const endpointEl = document.getElementById(`${prefix}-endpoint`);
  const apiKeyEl = document.getElementById(`${prefix}-apikey`);
  const container = document.getElementById(`${prefix}-model-container`);

  let timer;
  async function tryFetch() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const provider = providerEl.value;
      const endpoint = endpointEl.value.trim();
      const apiKey = apiKeyEl.value;
      if (!endpoint) return;

      try {
        const r = await fetch("api/models/fetch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider, endpoint, api_key: apiKey }),
        });
        const data = await r.json();
        if (data.models && data.models.length > 0) {
          const current = document.getElementById(`${prefix}-model`).value;
          let list = data.models;
          if (current && !list.includes(current)) list = [current, ...list];
          container.innerHTML = `<select id="${prefix}-model" class="form-control">
            <option value="">-- Select a model --</option>
            ${list.map(m =>
              `<option value="${escapeHtml(m)}"${m === current ? ' selected' : ''}>${escapeHtml(m)}</option>`
            ).join("")}
          </select>`;
        }
      } catch { /* keep text input */ }
    }, 400);
  }

  providerEl.addEventListener("change", tryFetch);
  endpointEl.addEventListener("input", tryFetch);
  apiKeyEl.addEventListener("input", tryFetch);
}

function triggerModelFetch(prefix) {
  const endpointEl = document.getElementById(`${prefix}-endpoint`);
  const apiKeyEl = document.getElementById(`${prefix}-apikey`);
  if (endpointEl.value.trim()) {
    endpointEl.dispatchEvent(new Event("input"));
  }
}

/* ═══════════════════════════════════════════
   Setup View
   ═══════════════════════════════════════════ */
async function populateSetupFromEnv() {
  try {
    const r = await fetch("api/env");
    const env = await r.json();
    if (env.endpoint) $("#setup-endpoint").value = env.endpoint;
    if (env.model) $("#setup-model").value = env.model;
    if (env.provider) $("#setup-provider").value = env.provider;
  } catch { /* ignore */ }
}

function showSetup() {
  $("#setup-view").classList.remove("hidden");
  $("#chat-view").classList.add("hidden");
  populateSetupFromEnv();
  setupModelAutoFetch("setup");
  setTimeout(() => triggerModelFetch("setup"), 600);
}

$("#setup-connect").addEventListener("click", async () => {
  const data = {
    name: ($("#setup-name").value || "").trim() || "My Server",
    provider: $("#setup-provider").value,
    endpoint: $("#setup-endpoint").value.trim(),
    model: $("#setup-model").value.trim(),
    api_key: $("#setup-apikey").value,
    vision: $("#setup-vision").checked,
  };
  if (!data.endpoint || !data.model) { toast("Endpoint and model are required", "error"); return; }
  try {
    const server = await addServer(data);
    defaultServerId = server.id;
    localStorage.setItem(LS_DEFAULT_SERVER, defaultServerId);
    initChat();
  } catch (e) {
    toast("Failed to add server: " + e.message, "error");
  }
});

/* ═══════════════════════════════════════════
   Settings Modal (tabbed)
   ═══════════════════════════════════════════ */
function switchSettingsTab(name) {
  $$(".settings-tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  $$(".settings-tab-content").forEach(c => c.classList.toggle("active", c.id === `tab-${name}`));
}

$$(".settings-tab").forEach(tab => {
  tab.addEventListener("click", () => switchSettingsTab(tab.dataset.tab));
});

function openSettings() {
  switchSettingsTab("servers");
  renderServerList();
  const s = loadSettings() || {};
  $("#settings-suffix").value = s.system_prompt_suffix || "";
  $("#settings-steps").value = s.max_steps || 50;
  settingsModal.classList.add("open");
}

$("#btn-settings").addEventListener("click", openSettings);

$("#modal-close").addEventListener("click", () => {
  settingsModal.classList.remove("open");
  hideServerForm();
});
$("#modal-save").addEventListener("click", () => {
  saveSettings({
    system_prompt_suffix: $("#settings-suffix").value,
    max_steps: parseInt($("#settings-steps").value, 10) || 50,
  });
  settingsModal.classList.remove("open");
  hideServerForm();
  toast("Settings saved", "success");
});
settingsModal.addEventListener("click", (e) => {
  if (e.target === settingsModal) {
    settingsModal.classList.remove("open");
    hideServerForm();
  }
});

/* ── Server list rendering ── */
function renderServerList() {
  const container = $("#server-list");
  if (!container) return;
  if (servers.length === 0) {
    container.innerHTML = '<div style="color:#7e8494;text-align:center;padding:24px;">No servers configured. Click "Add Server" below.</div>';
    return;
  }
  container.innerHTML = servers.map(s => {
    const isDefault = s.id === defaultServerId;
    const providerIcon = s.provider === "ollama" ? "fa-brain" : s.provider === "gemini" ? "fa-gem" : "fa-cloud";
    return `<div class="server-item${isDefault ? ' default' : ''}">
      <div class="server-item-info">
        <div class="server-item-name">
          ${escapeHtml(s.name)}
          ${isDefault ? '<span class="server-default-badge">Default</span>' : ''}
        </div>
        <div class="server-item-meta">
          <span><i class="fas ${providerIcon}"></i> ${escapeHtml(s.provider)}</span>
          <span><i class="fas fa-link"></i> ${escapeHtml(s.endpoint)}</span>
          <span><i class="fas fa-cube"></i> ${escapeHtml(s.model)}</span>
          <span><i class="fas ${s.vision ? 'fa-eye green' : 'fa-eye-slash'}" style="${s.vision ? 'color:#6ee7b7' : 'color:#7e8494'}"></i> ${s.vision ? 'Vision' : 'No vision'}</span>
        </div>
      </div>
      <div class="server-item-actions">
        ${!isDefault ? `<button class="btn btn-ghost btn-xs set-default-btn" data-id="${s.id}" title="Set as default"><i class="fas fa-star"></i></button>` : ''}
        <button class="btn btn-ghost btn-xs edit-server-btn" data-id="${s.id}" title="Edit"><i class="fas fa-pen"></i></button>
        <button class="btn btn-ghost btn-xs delete-server-btn" data-id="${s.id}" title="Delete"><i class="fas fa-trash"></i></button>
      </div>
    </div>`;
  }).join("");

  container.querySelectorAll(".set-default-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      await setDefaultServer(btn.dataset.id);
      renderServerList();
    });
  });
  container.querySelectorAll(".edit-server-btn").forEach(btn => {
    btn.addEventListener("click", () => showServerForm(btn.dataset.id));
  });
  container.querySelectorAll(".delete-server-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (confirm("Delete this server connection?")) {
        await deleteServer(btn.dataset.id);
      }
    });
  });
}

/* ── Server form (add/edit) ── */
function showServerForm(id) {
  editServerId = id;
  const form = $("#server-form");
  form.classList.remove("hidden");

  const mc = document.getElementById("sf-model-container");
  mc.innerHTML = `<input id="sf-model" class="form-control" type="text" placeholder="gemma4:12b">`;

  if (id) {
    $("#server-form-title").textContent = "Edit Server";
    const s = getServer(id);
    $("#sf-name").value = s.name;
    $("#sf-provider").value = s.provider;
    $("#sf-endpoint").value = s.endpoint;
    $("#sf-model").value = s.model;
    $("#sf-apikey").value = s.api_key;
    $("#sf-vision").checked = s.vision || false;
    setupModelAutoFetch("sf");
    triggerModelFetch("sf");
  } else {
    $("#server-form-title").textContent = "Add Server";
    $("#sf-name").value = "";
    $("#sf-provider").value = "openai";
    $("#sf-endpoint").value = "";
    $("#sf-model").value = "";
    $("#sf-apikey").value = "";
    $("#sf-vision").checked = false;
    setupModelAutoFetch("sf");
  }
}

$("#sf-provider").addEventListener("change", () => {
  if (!editServerId && $("#sf-provider").value === "gemini") {
    $("#sf-endpoint").value = "https://generativelanguage.googleapis.com";
  }
});

function hideServerForm() {
  editServerId = null;
  const form = $("#server-form");
  if (form) form.classList.add("hidden");
}

$("#btn-add-server").addEventListener("click", () => showServerForm(null));
$("#sf-cancel").addEventListener("click", hideServerForm);
$("#sf-save").addEventListener("click", async () => {
  const data = {
    name: ($("#sf-name").value || "").trim() || "Unnamed Server",
    provider: $("#sf-provider").value,
    endpoint: $("#sf-endpoint").value.trim(),
    model: $("#sf-model").value.trim(),
    api_key: $("#sf-apikey").value,
    vision: $("#sf-vision").checked,
  };
  if (!data.endpoint || !data.model) { toast("Endpoint and model are required", "error"); return; }
  try {
    if (editServerId) {
      await updateServer(editServerId, data);
      if (editServerId === defaultServerId) {
        populateServerDropdown();
      }
      toast("Server updated", "success");
    } else {
      const server = await addServer(data);
      if (server.id === defaultServerId) {
        populateServerDropdown();
      }
      toast("Server added", "success");
    }
    hideServerForm();
    renderServerList();
  } catch (e) {
    toast("Failed to save server: " + e.message, "error");
  }
});

/* ═══════════════════════════════════════════
   Chat Init
   ═══════════════════════════════════════════ */
function updateOpencodeBadge() {
  const badge = document.getElementById("opencode-badge");
  if (!badge) return;
  const svr = getDefaultServer();
  if (svr && svr.id === "svr_1aa2bfab16f3") {
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

async function initChat() {
  $("#setup-view").classList.add("hidden");
  $("#chat-view").classList.remove("hidden");

  populateServerDropdown();

  chatInput.disabled = false;
  btnSend.disabled = false;
  chatInput.focus();

  updateOpencodeBadge();
  createSession();
  connectWs();
}

/* ═══════════════════════════════════════════
   Session Management
   ═══════════════════════════════════════════ */
function createSession() {
  const id = nextSessionId++;

  const pane = document.createElement("div");
  pane.className = "session-pane";
  pane.dataset.session = id;
  sessionContainer.appendChild(pane);

  const tab = document.createElement("div");
  tab.className = "session-tab";
  tab.dataset.session = id;
  tab.innerHTML = `<i class="fas fa-comment" style="font-size:11px;color:#7e8494;"></i> Session ${sessions.length + 1}<span class="close-tab"><i class="fas fa-times"></i></span>`;
  tab.querySelector(".close-tab").addEventListener("click", (e) => {
    e.stopPropagation();
    closeSession(id);
  });
  tab.addEventListener("click", () => activateSession(id));

  const addBtn = tabBar.querySelector(".tab-add");
  tabBar.insertBefore(tab, addBtn);

  const session = { id, pane, tab, messages: [] };
  sessions.push(session);
  activateSession(id);
  return session;
}

function closeSession(id) {
  if (sessions.length <= 1) { toast("Cannot close the last session", "error"); return; }
  if (busy) { toast("Wait for the current task to finish", "error"); return; }
  const idx = sessions.findIndex(s => s.id === id);
  if (idx === -1) return;
  const s = sessions[idx];
  s.pane.remove();
  s.tab.remove();
  sessions.splice(idx, 1);
  renumberTabs();
  if (activeSessionId === id) {
    const next = sessions[Math.min(idx, sessions.length - 1)];
    if (next) activateSession(next.id);
  }
}

function activateSession(id) {
  activeSessionId = id;
  sessions.forEach(s => {
    s.pane.classList.toggle("active", s.id === id);
    s.tab.classList.toggle("active", s.id === id);
  });
  resetStream();
}

function renumberTabs() {
  sessions.forEach((s, i) => {
    const childNodes = s.tab.childNodes;
    for (const node of childNodes) {
      if (node.nodeType === Node.TEXT_NODE && node.textContent.trim().startsWith('Session')) {
        node.textContent = ` Session ${i + 1} `;
        break;
      }
    }
  });
}

function getActiveSession() {
  return sessions.find(s => s.id === activeSessionId);
}

/* ═══════════════════════════════════════════
   Stream state
   ═══════════════════════════════════════════ */
function resetStream() {
  Object.assign(stream, {
    pane: null, thoughtToggle: null, thoughtProcess: null, thoughtScroll: null,
    currentStepBody: null, streamingThinkEl: null, streamingTextEl: null, stepCount: 0,
  });
}

/* ═══════════════════════════════════════════
   WebSocket
   ═══════════════════════════════════════════ */
function connectWs() {
  if (ws) { ws.close(); }
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const wsBase = location.pathname.replace(/\/?$/, '/');
  ws = new WebSocket(`${proto}//${location.host}${wsBase}ws`);
  ws.onopen = () => {};
  ws.onclose = () => { if (!busy) setTimeout(connectWs, 3000); };
  ws.onerror = () => {};
  ws.onmessage = (e) => {
    try { handleEvent(JSON.parse(e.data)); } catch { /* skip */ }
  };
}

/* ═══════════════════════════════════════════
   Event Handling
   ═══════════════════════════════════════════ */
function handleEvent(evt) {
  switch (evt.type) {
    case "init_screenshot": onInitScreenshot(evt.data); break;
    case "status": onStatus(evt.message); break;
    case "step_start": onStepStart(evt.iteration); break;
    case "think": onThink(evt.delta); break;
    case "text": onText(evt.delta); break;
    case "tool_call": onToolCall(evt.name, evt.input); break;
    case "tool_screenshot": onToolScreenshot(evt.data); break;
    case "tool_output": onToolOutput(evt.text); break;
    case "tool_error": onToolError(evt.text); break;
    case "step_end": onStepEnd(evt); break;
    case "done": onDone(evt); break;
    case "error": onError(evt.message || "An error occurred"); break;
  }
}

/* ── Events ── */
function onInitScreenshot(b64) {
  const pane = getActiveSession()?.pane;
  if (!pane) return;

  const statusMsg = pane.querySelector(".status-badge");
  if (statusMsg) statusMsg.remove();

  const box = document.createElement("div");
  box.className = "init-screenshot";
  box.innerHTML = `<img src="data:image/png;base64,${b64}" alt="Initial screen">`;
  makeImgClickable(box.querySelector("img"));
  pane.appendChild(box);

  const toggle = document.createElement("div");
  toggle.className = "thought-toggle open";
  toggle.innerHTML = `<i class="fas fa-chevron-right"></i> Thought Process <span class="step-num" style="color:#7e8494;margin-left:4px;">· 0 steps</span>`;
  toggle.addEventListener("click", () => {
    toggle.classList.toggle("open");
    const tp = toggle.nextElementSibling;
    if (tp) tp.classList.toggle("open");
  });
  pane.appendChild(toggle);

  const tp = document.createElement("div");
  tp.className = "thought-process open";
  pane.appendChild(tp);

  stream.pane = pane;
  stream.thoughtToggle = toggle;
  stream.thoughtProcess = tp;
  stream.thoughtScroll = tp;
  stream.stepCount = 0;
}

function onStatus(msg) {
  const pane = getActiveSession()?.pane;
  if (!pane) return;
  const el = document.createElement("div");
  el.className = "status-badge";
  el.innerHTML = `<i class="fas fa-spinner"></i> ${escapeHtml(msg)}`;
  pane.appendChild(el);
  pane.scrollTop = pane.scrollHeight;
}

function onStepStart(iteration) {
  stream.stepCount = iteration;
  const countSpan = stream.thoughtToggle?.querySelector(".step-num");
  if (countSpan) countSpan.textContent = `· ${iteration} step${iteration > 1 ? "s" : ""}`;

  const step = document.createElement("div");
  step.className = "step";
  step.dataset.iteration = iteration;
  step.innerHTML = `
    <div class="step-header">
      <i class="fas fa-circle" style="font-size:6px;color:#60a5fa;"></i>
      <span class="step-num">Step ${iteration}</span>
      <span class="step-status"><i class="fas fa-spinner fa-spin"></i></span>
    </div>`;
  stream.currentStepBody = document.createElement("div");
  stream.currentStepBody.className = "step-body";
  step.appendChild(stream.currentStepBody);
  stream.thoughtScroll.appendChild(step);
  stream.pane.scrollTop = stream.pane.scrollHeight;
}

function onThink(delta) {
  if (!stream.currentStepBody) return;
  if (!stream.streamingThinkEl) {
    stream.streamingThinkEl = document.createElement("div");
    stream.streamingThinkEl.className = "think-block";
    stream.currentStepBody.appendChild(stream.streamingThinkEl);
  }
  stream.streamingThinkEl.textContent += delta;
  stream.pane.scrollTop = stream.pane.scrollHeight;
}

function onText(delta) {
  if (!stream.currentStepBody) return;
  if (!stream.streamingTextEl) {
    stream.streamingTextEl = document.createElement("div");
    stream.streamingTextEl.className = "text-block streaming";
    stream.currentStepBody.appendChild(stream.streamingTextEl);
  }
  stream.streamingTextEl.textContent += delta;
  stream.pane.scrollTop = stream.pane.scrollHeight;
}

function onToolCall(name, input) {
  stream.streamingThinkEl = null;
  stream.streamingTextEl = null;
  if (!stream.currentStepBody) return;
  const el = document.createElement("div");
  el.className = "tool-call";
  el.textContent = `${name}(${JSON.stringify(input, null, 1)})`;
  stream.currentStepBody.appendChild(el);
  stream.pane.scrollTop = stream.pane.scrollHeight;
}

function onToolScreenshot(b64) {
  if (!stream.currentStepBody) return;
  const box = document.createElement("div");
  box.className = "screenshot-box";
  box.innerHTML = `<img src="data:image/png;base64,${b64}" alt="Screenshot" loading="lazy">`;
  makeImgClickable(box.querySelector("img"));
  stream.currentStepBody.appendChild(box);
  stream.pane.scrollTop = stream.pane.scrollHeight;
}

function onToolOutput(text) {
  if (!stream.currentStepBody) return;
  const el = document.createElement("div");
  el.className = "tool-result";
  el.textContent = text;
  stream.currentStepBody.appendChild(el);
  stream.pane.scrollTop = stream.pane.scrollHeight;
}

function onToolError(text) {
  if (!stream.currentStepBody) return;
  const el = document.createElement("div");
  el.className = "tool-error";
  el.textContent = text;
  stream.currentStepBody.appendChild(el);
  stream.pane.scrollTop = stream.pane.scrollHeight;
}

function onStepEnd(evt) {
  const step = stream.thoughtScroll?.querySelector(`.step[data-iteration="${evt.iteration}"]`);
  if (step) {
    const status = step.querySelector(".step-status");
    if (evt.tool_calls_count > 0) {
      status.innerHTML = `<span style="color:#fbbf24;"><i class="fas fa-tools"></i> ${evt.tool_calls_count} action${evt.tool_calls_count > 1 ? "s" : ""}</span>`;
    } else {
      status.innerHTML = `<span style="color:#6ee7b7;"><i class="fas fa-check"></i> done</span>`;
    }
  }
  stream.streamingThinkEl = null;
  stream.streamingTextEl = null;
}

function onDone(evt) {
  busy = false;
  chatInput.disabled = false;
  btnSend.disabled = false;
  btnStop.classList.add("hidden");
  chatInput.focus();

  if (stream.streamingTextEl) stream.streamingTextEl.classList.remove("streaming");

  if (stream.pane) {
    stream.pane.querySelectorAll(".status-badge").forEach(el => el.remove());
  }

  if (stream.thoughtToggle) {
    stream.thoughtToggle.classList.remove("open");
    if (stream.thoughtProcess) stream.thoughtProcess.classList.remove("open");
  }

  const pane = stream.pane;
  if (!pane) return;

  if (evt.final_text || evt.final_screenshot) {
    const box = document.createElement("div");
    box.className = "final-box";
    if (evt.final_text) {
      box.innerHTML += `<div class="final-text">${escapeHtml(evt.final_text)}</div>`;
    }
    if (evt.final_screenshot) {
      const imgSrc = `data:image/png;base64,${evt.final_screenshot}`;
      box.innerHTML += `<div class="screenshot-box"><img src="${imgSrc}" alt="Final screen"></div>`;
      makeImgClickable(box.querySelector(".screenshot-box img"));
    }
    pane.appendChild(box);
  }

  resetStream();
  pane.scrollTop = pane.scrollHeight;
}

function onError(msg) {
  toast(msg, "error");
  finishError();
}

function finishError() {
  busy = false;
  chatInput.disabled = false;
  btnSend.disabled = false;
  btnStop.classList.add("hidden");
  resetStream();
}

/* ═══════════════════════════════════════════
   Send Message
   ═══════════════════════════════════════════ */
function sendMessage() {
  const text = chatInput.value.trim();
  if (!text || busy) return;

  const cfg = buildConfig();
  if (!cfg) { toast("No server configured", "error"); return; }
  if (!ws || ws.readyState !== WebSocket.OPEN) { toast("Disconnected from server", "error"); return; }

  busy = true;
  chatInput.disabled = true;
  btnSend.disabled = true;
  btnStop.classList.remove("hidden");
  chatInput.value = "";
  chatInput.style.height = "auto";

  const session = getActiveSession();

  if (session) {
    const userMsg = document.createElement("div");
    userMsg.className = "user-msg";
    userMsg.innerHTML = `<p>${escapeHtml(text)}</p>`;
    session.pane.appendChild(userMsg);
    session.pane.scrollTop = session.pane.scrollHeight;
  }

  const settings = loadSettings() || {};
  ws.send(JSON.stringify({
    type: "run",
    config: cfg,
    text: text,
    settings: {
      system_prompt_suffix: settings.system_prompt_suffix || "",
      max_steps: settings.max_steps || 50,
    },
  }));
}

btnSend.addEventListener("click", sendMessage);
btnStop.addEventListener("click", () => {
  if (!busy) return;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "stop" }));
  }
  busy = false;
  chatInput.disabled = false;
  btnSend.disabled = false;
  btnStop.classList.add("hidden");
  chatInput.focus();
  resetStream();
});
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

/* ── Tab add button ── */
const tabAddBtn = document.createElement("button");
tabAddBtn.className = "tab-add";
tabAddBtn.innerHTML = '<i class="fas fa-plus"></i>';
tabAddBtn.title = "New Session";
tabAddBtn.addEventListener("click", () => {
  if (busy) { toast("Wait for the current task to finish", "error"); return; }
  createSession();
});
tabBar.appendChild(tabAddBtn);

/* ═══════════════════════════════════════════
   Init
   ═══════════════════════════════════════════ */
async function main() {
  await loadServers();
  if (servers.length === 0) {
    showSetup();
    return;
  }
  initChat();
}

main();
