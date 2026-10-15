const voices = ["eve", "ara", "rex", "sal", "leo"];
const examples = [
  "What can you change in this Unreal level?",
  "What actors are in the level?",
  "Add a warm point light two meters above the origin.",
];

const state = {
  voice: localStorage.getItem("voice") || "eve",
  signedIn: false,
  live: false,
  muted: false,
  ws: null,
  ctx: null,
  gain: null,
  mic: null,
  nextTime: 0,
  sources: [],
  userPlaceholder: null,
};

const $ = (id) => document.getElementById(id);
const orb = $("orb");
const activity = $("activity");
const transcript = $("transcript");
const toolList = $("tools");
const errorBox = $("error");

function setPill(id, text, kind) {
  const node = $(id);
  node.textContent = text;
  node.className = `pill${kind ? ` ${kind}` : ""}`;
}

function showError(message) {
  if (!message) {
    errorBox.hidden = true;
    errorBox.textContent = "";
    return;
  }
  errorBox.hidden = false;
  errorBox.textContent = message;
}

function setPhase(phase) {
  orb.className = `orb ${phase || "idle"}`;
  const labels = {
    idle: "Idle",
    listening: "Listening",
    speaking: "Speaking",
    working: "Working in Unreal",
  };
  if (labels[phase]) activity.textContent = labels[phase];
}

function addBubble(role, text, final) {
  const item = document.createElement("li");
  item.className = `bubble ${role}`;
  const who = document.createElement("small");
  who.textContent = role === "user" ? "You" : "Grok";
  const body = document.createElement("div");
  body.textContent = text;
  item.append(who, body);
  item.dataset.final = final ? "yes" : "no";
  transcript.append(item);
  while (transcript.children.length > 200) transcript.firstChild.remove();
  transcript.scrollTop = transcript.scrollHeight;
  return item;
}

function upsertAssistant(text, final) {
  const last = transcript.lastElementChild;
  if (last?.classList.contains("assistant") && last.dataset.final !== "yes") {
    last.querySelector("div").textContent = text;
    last.dataset.final = final ? "yes" : "no";
  } else {
    addBubble("assistant", text, final);
  }
  transcript.scrollTop = transcript.scrollHeight;
}

function addTool(message) {
  const item = document.createElement("li");
  const title = document.createElement("div");
  title.textContent = `${message.phase === "error" ? "failed" : message.phase} · ${message.name}`;
  if (message.phase === "error") title.className = "bad";
  item.append(title);
  const detail = message.result || message.arguments;
  if (detail) {
    const pre = document.createElement("div");
    pre.textContent = String(detail).slice(0, 700);
    item.append(pre);
  }
  toolList.prepend(item);
  while (toolList.children.length > 40) toolList.lastChild.remove();
}

function renderVoices() {
  const host = $("voices");
  host.replaceChildren();
  for (const voice of voices) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = voice;
    button.setAttribute("aria-pressed", voice === state.voice ? "true" : "false");
    button.addEventListener("click", () => {
      state.voice = voice;
      localStorage.setItem("voice", voice);
      renderVoices();
    });
    host.append(button);
  }
}

function renderExamples() {
  const host = $("examples");
  for (const sample of examples) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = sample;
    button.addEventListener("click", () => {
      $("text").value = sample;
      if (state.live) $("composer").requestSubmit();
    });
    host.append(button);
  }
}

function renderTools(tools) {
  const list = $("tool-preview");
  list.replaceChildren();
  for (const tool of tools || []) {
    const item = document.createElement("li");
    item.textContent = tool.description ? `${tool.name} — ${tool.description}` : tool.name;
    list.append(item);
  }
}

async function refreshStatus() {
  const response = await fetch("/api/status");
  const status = await response.json();
  state.signedIn = Boolean(status.signedIn);
  $("account").textContent = status.signedIn
    ? `Using your xAI subscription${status.name ? ` (${status.name})` : ""}.`
    : "Sign in with Grok, then reload this page.";
  $("start").disabled = !status.signedIn;
  applyUnreal(status.unreal);
}

function applyUnreal(unreal) {
  if (!unreal) return;
  if (unreal.url) $("unreal-url").value = unreal.url;
  if (unreal.connected) {
    setPill("unreal-pill", `${unreal.serverName || "unreal-mcp"} · ${unreal.toolCount} tools`, "live");
    $("unreal-state").textContent = unreal.mode
      ? `Connected. Tool mode: ${unreal.mode}.`
      : "Connected.";
    renderTools((unreal.toolNames || []).slice(0, 12).map((name) => ({ name })));
  } else {
    setPill("unreal-pill", "Editor offline", unreal.error ? "bad" : "");
    if (unreal.error) $("unreal-state").textContent = unreal.error;
  }
}

async function ensureSocket() {
  if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) {
    if (state.ws.readyState === WebSocket.OPEN) return state.ws;
    await new Promise((resolve, reject) => {
      state.ws.addEventListener("open", resolve, { once: true });
      state.ws.addEventListener("error", () => reject(new Error("Voice server socket failed.")), { once: true });
    });
    return state.ws;
  }
  const socket = new WebSocket(`ws://${location.host}/ws`);
  socket.binaryType = "arraybuffer";
  state.ws = socket;
  socket.addEventListener("message", onMessage);
  socket.addEventListener("close", () => {
    state.live = false;
    setPhase("idle");
    setPill("grok-pill", "Voice idle", "");
    $("mute").disabled = true;
    $("stop").disabled = true;
    $("start").disabled = !state.signedIn;
  });
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", () => reject(new Error("Could not reach the local voice server.")), { once: true });
  });
  return socket;
}

function onMessage(event) {
  if (event.data instanceof ArrayBuffer) {
    playPcm(event.data);
    return;
  }
  const message = JSON.parse(event.data);
  if (message.type === "status") {
    const labels = { connecting: "Connecting", live: "Live", closed: "Voice idle", error: "Voice error" };
    setPill("grok-pill", labels[message.grok] || message.grok, message.grok === "live" || message.grok === "connecting" ? "warn" : message.grok === "error" ? "bad" : "");
    if (message.grok === "connecting") activity.textContent = "Connecting to Grok Voice 3";
  } else if (message.type === "ready") {
    setPill("grok-pill", "Live", "live");
    setPhase("listening");
    applyUnreal(message.unreal);
  } else if (message.type === "phase") {
    setPhase(message.phase);
  } else if (message.type === "unreal") {
    applyUnreal(message);
  } else if (message.type === "user_speaking") {
    state.userPlaceholder = addBubble("user", "…", false);
  } else if (message.type === "transcript" && message.role === "user") {
    if (state.userPlaceholder) {
      state.userPlaceholder.querySelector("div").textContent = message.text;
      state.userPlaceholder.dataset.final = "yes";
      state.userPlaceholder = null;
    } else {
      addBubble("user", message.text, true);
    }
  } else if (message.type === "transcript" && message.role === "assistant") {
    upsertAssistant(message.text, message.final);
  } else if (message.type === "tool") {
    if (message.phase === "start") activity.textContent = `Unreal · ${message.name}`;
    addTool(message);
  } else if (message.type === "interrupt") {
    stopPlayback();
  } else if (message.type === "error") {
    showError(message.message);
  }
}

async function startMic() {
  if (!state.ctx) {
    state.ctx = new AudioContext();
    state.gain = state.ctx.createGain();
    state.gain.gain.value = Number($("volume").value);
    state.gain.connect(state.ctx.destination);
    await state.ctx.audioWorklet.addModule("/mic-worklet.js");
  }
  if (state.ctx.state === "suspended") await state.ctx.resume();
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const source = state.ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(state.ctx, "mic-capture");
  node.port.onmessage = (event) => {
    if (!state.live || state.muted || state.ws?.readyState !== WebSocket.OPEN) return;
    state.ws.send(event.data.pcm);
    orb.style.setProperty("--level", String(Math.min(1, event.data.rms * 4)));
  };
  source.connect(node);
  state.mic = { stream, source, node };
}

function stopMic() {
  state.mic?.stream.getTracks().forEach((track) => track.stop());
  state.mic?.source.disconnect();
  state.mic?.node.disconnect();
  state.mic = null;
  orb.style.setProperty("--level", "0");
}

function playPcm(arrayBuffer) {
  if (!state.ctx) return;
  if (state.ctx.state === "suspended") state.ctx.resume();
  const pcm = new Int16Array(arrayBuffer);
  const audio = new Float32Array(pcm.length);
  for (let index = 0; index < pcm.length; index += 1) audio[index] = pcm[index] / 32768;
  const buffer = state.ctx.createBuffer(1, audio.length, 24000);
  buffer.copyToChannel(audio, 0);
  const source = state.ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(state.gain);
  const now = state.ctx.currentTime;
  if (state.nextTime < now + 0.02) state.nextTime = now + 0.05;
  source.start(state.nextTime);
  state.nextTime += buffer.duration;
  state.sources.push(source);
  source.onended = () => {
    state.sources = state.sources.filter((item) => item !== source);
  };
}

function stopPlayback() {
  for (const source of state.sources) {
    try { source.stop(); } catch { /* already stopped */ }
  }
  state.sources = [];
  state.nextTime = 0;
}

$("unreal-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  $("connect").disabled = true;
  activity.textContent = "Connecting to Unreal MCP";
  try {
    const response = await fetch("/api/unreal/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: $("unreal-url").value.trim() }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Unreal MCP refused the connection.");
    applyUnreal(body.unreal);
    const found = await fetch(`/api/unreal/tools?q=${encodeURIComponent($("tool-query").value)}`);
    renderTools((await found.json()).tools);
  } catch (error) {
    setPill("unreal-pill", "Editor offline", "bad");
    $("unreal-state").textContent = error.message;
    showError(error.message);
  } finally {
    $("connect").disabled = false;
  }
});

$("tool-query").addEventListener("change", async () => {
  const response = await fetch(`/api/unreal/tools?q=${encodeURIComponent($("tool-query").value)}`);
  if (response.ok) renderTools((await response.json()).tools);
});

$("start").addEventListener("click", async () => {
  showError("");
  try {
    const socket = await ensureSocket();
    await startMic().catch((error) => {
      showError(`${error.message}. You can still type.`);
    });
    socket.send(JSON.stringify({
      type: "start",
      voice: state.voice,
      reasoning: $("reasoning").checked ? "high" : "none",
    }));
    state.live = true;
    state.muted = false;
    $("mute").textContent = "Mute";
    $("mute").disabled = false;
    $("stop").disabled = false;
    $("start").disabled = true;
    setPill("grok-pill", "Connecting", "warn");
  } catch (error) {
    showError(error.message);
  }
});

$("mute").addEventListener("click", () => {
  state.muted = !state.muted;
  $("mute").textContent = state.muted ? "Unmute" : "Mute";
  activity.textContent = state.muted ? "Muted" : "Listening";
});

$("stop").addEventListener("click", () => {
  state.live = false;
  stopMic();
  stopPlayback();
  if (state.ws?.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify({ type: "stop" }));
  setPhase("idle");
  $("start").disabled = !state.signedIn;
  $("mute").disabled = true;
  $("stop").disabled = true;
});

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("text").value.trim();
  if (!text) return;
  showError("");
  try {
    const socket = await ensureSocket();
    if (!state.live) {
      socket.send(JSON.stringify({
        type: "start",
        voice: state.voice,
        reasoning: $("reasoning").checked ? "high" : "none",
      }));
      state.live = true;
      $("stop").disabled = false;
      $("start").disabled = true;
    }
    socket.send(JSON.stringify({ type: "text", text }));
    addBubble("user", text, true);
    $("text").value = "";
  } catch (error) {
    showError(error.message);
  }
});

$("clear").addEventListener("click", () => {
  transcript.replaceChildren();
  toolList.replaceChildren();
});

$("volume").addEventListener("input", () => {
  if (state.gain) state.gain.gain.value = Number($("volume").value);
});

document.addEventListener("keydown", (event) => {
  if (event.code === "Space" && event.target === document.body && state.live) {
    event.preventDefault();
    $("mute").click();
  }
});

renderVoices();
renderExamples();
refreshStatus().catch((error) => showError(error.message));
