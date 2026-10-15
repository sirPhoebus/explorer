import WebSocket from "ws";
import { MAX_TOOL_ROUNDS, MODEL, REALTIME_URL, SAMPLE_RATE } from "./config.js";
import { buildInstructions } from "./instructions.js";
import { clampText, planTools, runTool } from "./tools.js";
import { createTurnTracker } from "./turns.js";

const AUDIO_DELTA = new Set(["response.output_audio.delta", "response.audio.delta"]);
const ASSISTANT_DELTA = new Set([
  "response.output_audio_transcript.delta",
  "response.audio_transcript.delta",
]);
const ASSISTANT_DONE = new Set([
  "response.output_audio_transcript.done",
  "response.audio_transcript.done",
]);
const QUIET = new Set([
  ...AUDIO_DELTA,
  ...ASSISTANT_DELTA,
  "response.output_audio.done",
  "response.audio.done",
  "input_audio_buffer.committed",
  "input_audio_buffer.speech_stopped",
]);

function parseArgs(value) {
  if (value == null || value === "") return {};
  if (typeof value === "object") return value;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export class VoiceSession {
  constructor({ hub, socket }) {
    this.hub = hub;
    this.socket = socket;
    this.grok = null;
    this.ready = false;
    this.closed = false;
    this.voice = "eve";
    this.reasoning = "high";
    this.plan = planTools([]);
    this.turns = createTurnTracker();
    this.generation = 0;
    this.toolRounds = 0;
    this.blockTools = false;
    this.executing = false;
    this.toolChain = Promise.resolve();
    this.pendingText = [];
    this.audioQueue = [];
    this.audioBytes = 0;
    this.assistantText = "";
    this.readyTimer = null;
  }

  send(message) {
    if (this.closed || this.socket.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify(message));
  }

  sendAudio(buffer) {
    if (this.closed || this.socket.readyState !== WebSocket.OPEN) return;
    this.socket.send(buffer);
  }

  grokSend(message) {
    if (!this.grok || this.grok.readyState !== WebSocket.OPEN) return;
    this.grok.send(JSON.stringify(message));
  }

  onBrowser(data, isBinary) {
    if (this.closed) return;
    if (isBinary) {
      if (!this.ready) this.bufferAudio(data);
      else this.appendAudio(data);
      return;
    }
    let message;
    try {
      message = JSON.parse(data.toString());
    } catch {
      this.send({ type: "error", message: "Ignored a malformed client message." });
      return;
    }
    if (message.type === "start") {
      this.start(message).catch((error) => {
        this.send({ type: "error", message: error.message });
        this.send({ type: "status", grok: "error" });
      });
    } else if (message.type === "stop") {
      this.stop();
    } else if (message.type === "text") {
      const text = String(message.text || "").trim().slice(0, 2000);
      if (!text) return;
      if (!this.ready) this.pendingText.push(text);
      else if (this.executing) this.pendingText.push(text);
      else this.submitText(text);
    } else if (message.type === "unreal.reconnect") {
      this.reconnectUnreal().catch((error) => this.send({ type: "error", message: error.message }));
    }
  }

  async start(message) {
    if (!/^[a-z0-9_-]{1,64}$/i.test(message.voice || "eve")) {
      throw new Error("Voice id must be letters, numbers, underscore, or hyphen.");
    }
    this.voice = String(message.voice || "eve").toLowerCase();
    this.reasoning = message.reasoning === "none" ? "none" : "high";
    if (this.grok && this.grok.readyState === WebSocket.OPEN) return;

    this.ready = false;
    this.turns = createTurnTracker();
    this.toolRounds = 0;
    this.blockTools = false;
    this.send({ type: "status", grok: "connecting", model: MODEL });
    await this.prepareUnreal();
    await this.openGrok();
  }

  async prepareUnreal() {
    try {
      await this.hub.unreal.connect();
    } catch (error) {
      this.hub.unreal.lastError = error.message;
      this.send({ type: "unreal", ...this.hub.unreal.snapshot(), error: error.message });
    }
    this.adoptTools();
  }

  adoptTools() {
    this.plan = planTools(this.hub.unreal.tools, { connected: this.hub.unreal.connected });
    if (this.grok && this.grok.readyState === WebSocket.OPEN) this.sendSessionUpdate();
    this.send({ type: "unreal", ...this.hub.unreal.snapshot(), mode: this.plan.mode });
  }

  async openGrok({ force = false } = {}) {
    const token = await this.hub.auth.getAccessToken({ force });
    if (this.grok) {
      this.grok.removeAllListeners();
      this.grok.close();
    }
    const grok = new WebSocket(REALTIME_URL, {
      headers: { Authorization: `Bearer ${token}` },
    });
    this.grok = grok;
    const failTimer = setTimeout(() => {
      if (!this.ready && this.grok === grok) {
        grok.terminate();
        this.send({ type: "error", message: "Timed out opening Grok Voice 3." });
        this.send({ type: "status", grok: "error" });
      }
    }, 15000);

    let authHandled = false;
    const rejectAuth = () => {
      if (authHandled) return;
      authHandled = true;
      if (!force) {
        this.openGrok({ force: true }).catch((error) => {
          this.send({ type: "error", message: error.message });
          this.send({ type: "status", grok: "error" });
        });
        return;
      }
      this.send({
        type: "error",
        message: "The xAI subscription rejected the voice session. Sign in with Grok again.",
      });
      this.send({ type: "status", grok: "error" });
    };

    grok.on("open", () => {
      this.sendSessionUpdate();
      this.readyTimer = setTimeout(() => this.markReady(), 2000);
    });
    grok.on("message", (data, isBinary) => this.onGrok(data, isBinary));
    grok.on("unexpected-response", (_request, response) => {
      response.resume();
      if (response.statusCode === 401) rejectAuth();
      else this.send({ type: "error", message: `Voice connection failed (HTTP ${response.statusCode}).` });
    });
    grok.on("error", (error) => {
      if (/401/.test(error.message)) {
        rejectAuth();
        return;
      }
      this.send({ type: "error", message: `Voice connection failed: ${error.message}` });
    });
    grok.on("close", (code) => {
      clearTimeout(failTimer);
      clearTimeout(this.readyTimer);
      if (this.grok !== grok) return;
      this.ready = false;
      this.grok = null;
      this.send({ type: "status", grok: "closed", code });
    });
  }

  sendSessionUpdate() {
    this.grokSend({
      type: "session.update",
      session: {
        voice: this.voice,
        instructions: buildInstructions(this.plan.prompt),
        reasoning: { effort: this.reasoning },
        turn_detection: { type: "server_vad" },
        tools: this.plan.functions,
        audio: {
          input: {
            format: { type: "audio/pcm", rate: SAMPLE_RATE },
            transcription: {
              keyterms: ["Unreal", "Blueprint", "Niagara", "Static Mesh", "Material Instance", "toolset", "PIE"],
            },
          },
          output: { format: { type: "audio/pcm", rate: SAMPLE_RATE } },
        },
      },
    });
  }

  markReady() {
    if (this.ready || this.closed) return;
    this.ready = true;
    clearTimeout(this.readyTimer);
    this.flushAudio();
    const queued = this.pendingText.splice(0);
    for (const text of queued) this.sendUserItem(text);
    if (queued.length) this.grokSend({ type: "response.create" });
    this.send({
      type: "ready",
      model: MODEL,
      voice: this.voice,
      unreal: { ...this.hub.unreal.snapshot(), mode: this.plan.mode },
    });
    this.send({ type: "phase", phase: "listening" });
  }

  onGrok(data, isBinary) {
    if (isBinary) {
      this.sendAudio(data);
      this.send({ type: "phase", phase: "speaking" });
      return;
    }
    let event;
    try {
      event = JSON.parse(data.toString());
    } catch {
      return;
    }
    const type = event.type;
    if (!type) return;
    if (!QUIET.has(type)) console.log(`[voice] ${type}${event.name ? ` ${event.name}` : ""}`);

    if (type === "session.updated") {
      this.markReady();
      return;
    }
    if (type === "error") {
      const message = event.error?.message || event.message || "Grok Voice returned an error.";
      this.send({ type: "error", message });
      return;
    }
    if (AUDIO_DELTA.has(type)) {
      const payload = event.delta || event.audio;
      if (payload) {
        this.sendAudio(Buffer.from(payload, "base64"));
        this.send({ type: "phase", phase: "speaking" });
      }
      return;
    }
    if (type === "input_audio_buffer.speech_started") {
      this.generation += 1;
      this.toolRounds = 0;
      this.blockTools = false;
      this.assistantText = "";
      this.send({ type: "interrupt" });
      this.send({ type: "user_speaking" });
      this.send({ type: "phase", phase: "listening" });
      return;
    }
    if (ASSISTANT_DELTA.has(type) && event.delta) {
      this.assistantText += event.delta;
      this.send({ type: "transcript", role: "assistant", text: this.assistantText, final: false });
      return;
    }
    if (ASSISTANT_DONE.has(type)) {
      const text = event.transcript || event.text || this.assistantText;
      if (text) this.send({ type: "transcript", role: "assistant", text, final: true });
      this.assistantText = "";
      return;
    }
    if (type === "conversation.item.input_audio_transcription.completed" && event.transcript) {
      this.send({ type: "transcript", role: "user", text: event.transcript, final: true });
      return;
    }
    if (type === "response.created") this.assistantText = "";

    const decision = this.turns.onEvent(event);
    if (decision.action === "execute") {
      this.toolChain = this.toolChain
        .then(() => this.runCalls(decision.calls))
        .catch((error) => this.send({ type: "error", message: error.message }));
    } else if (decision.action === "drop") {
      this.send({ type: "phase", phase: "listening" });
    } else if (type === "response.done") {
      this.send({ type: "phase", phase: this.executing ? "working" : "listening" });
    }
  }

  async runCalls(calls) {
    const gen = this.generation;
    this.executing = true;
    this.send({ type: "phase", phase: "working" });
    try {
      if (this.blockTools) {
        for (const call of calls) {
          this.returnOutput(call, { error: "Paused. Tell the user where things stand and wait." });
        }
        return;
      }
      this.toolRounds += 1;
      const hitCap = this.toolRounds >= MAX_TOOL_ROUNDS;
      for (let index = 0; index < calls.length; index += 1) {
        const call = calls[index];
        const args = parseArgs(call.arguments);
        this.send({ type: "tool", phase: "start", name: call.name, arguments: clampText(args, 500) });
        let result;
        try {
          result = await runTool(this.plan, this.hub.unreal, call.name, args);
          if (result?.__refreshTools) {
            delete result.__refreshTools;
            this.adoptTools();
          }
        } catch (error) {
          result = { error: error.message };
        }
        if (hitCap && index === calls.length - 1) {
          result = {
            result,
            notice: "Tool round limit reached. Summarize what changed and wait for the user.",
          };
          this.blockTools = true;
        }
        const output = clampText(result);
        this.returnOutput(call, output);
        this.send({
          type: "tool",
          phase: result?.error ? "error" : "done",
          name: call.name,
          result: output,
        });
      }
      for (const text of this.pendingText.splice(0)) this.sendUserItem(text);
      if (this.generation === gen) this.grokSend({ type: "response.create" });
    } finally {
      this.executing = false;
      if (this.generation === gen) this.send({ type: "phase", phase: "listening" });
    }
  }

  returnOutput(call, output) {
    this.grokSend({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: call.callId,
        output: typeof output === "string" ? output : clampText(output),
      },
    });
  }

  sendUserItem(text) {
    this.toolRounds = 0;
    this.blockTools = false;
    this.grokSend({
      type: "conversation.item.create",
      item: {
        type: "message",
        role: "user",
        content: [{ type: "input_text", text }],
      },
    });
  }

  submitText(text) {
    this.sendUserItem(text);
    this.grokSend({ type: "response.create" });
  }

  async reconnectUnreal() {
    await this.hub.unreal.connect();
    this.adoptTools();
  }

  appendAudio(data) {
    const audio = Buffer.from(data);
    if (!audio.length || audio.length > 256 * 1024) return;
    this.grokSend({ type: "input_audio_buffer.append", audio: audio.toString("base64") });
  }

  bufferAudio(data) {
    const audio = Buffer.from(data);
    this.audioQueue.push(audio);
    this.audioBytes += audio.length;
    while (this.audioBytes > SAMPLE_RATE * 2 && this.audioQueue.length) {
      this.audioBytes -= this.audioQueue.shift().length;
    }
  }

  flushAudio() {
    for (const chunk of this.audioQueue) this.appendAudio(chunk);
    this.audioQueue = [];
    this.audioBytes = 0;
  }

  stop() {
    this.generation += 1;
    this.ready = false;
    clearTimeout(this.readyTimer);
    if (this.grok) {
      const grok = this.grok;
      this.grok = null;
      grok.close();
    }
    this.send({ type: "status", grok: "closed" });
    this.send({ type: "phase", phase: "idle" });
  }

  close() {
    this.closed = true;
    this.stop();
  }
}
