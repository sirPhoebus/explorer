const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);
// UE 5.8 Unreal MCP speaks 2025-11-25, 2025-06-18, and 2024-11-05.
const PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2024-11-05"];

export class McpError extends Error {
  constructor(message, { status = 0, code = null, cause = null } = {}) {
    super(message, cause ? { cause } : undefined);
    this.name = "McpError";
    this.status = status;
    this.code = code;
  }
}

// Unreal MCP has no authentication and binds to loopback. Refuse any other host
// so this process cannot be pointed at an arbitrary URL.
export function assertLocalUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new McpError("Unreal MCP URL is not a valid URL.");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new McpError("Unreal MCP URL must start with http:// or https://.");
  }
  if (url.username || url.password) {
    throw new McpError("Unreal MCP URL cannot include a username or password.");
  }
  if (!LOCAL_HOSTS.has(url.hostname.toLowerCase())) {
    throw new McpError(
      "Unreal MCP must be on this machine (localhost or 127.0.0.1). The editor server has no authentication.",
    );
  }
  return url.toString();
}

export function parseMcpPayload(contentType, text) {
  const raw = text ?? "";
  if (!raw.trim()) return [];
  const type = String(contentType || "").toLowerCase();
  const looksLikeSse =
    type.includes("text/event-stream") || /^\s*(?:data:|event:|id:)/m.test(raw);
  if (!looksLikeSse) {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [parsed];
  }
  const messages = [];
  for (const block of raw.split(/\r?\n\r?\n/)) {
    const dataLines = [];
    for (const line of block.split(/\r?\n/)) {
      if (!line || line.startsWith(":")) continue;
      if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
    }
    if (!dataLines.length) continue;
    const data = dataLines.join("\n");
    if (!data || data === "[DONE]") continue;
    messages.push(JSON.parse(data));
  }
  return messages;
}

export function pickRpc(messages, id) {
  if (!messages?.length) return null;
  const wanted = String(id);
  return (
    messages.find((message) => message && String(message.id) === wanted) ||
    messages.find((message) => message && (message.result !== undefined || message.error)) ||
    messages[messages.length - 1]
  );
}

function pick(object, ...names) {
  if (!object || typeof object !== "object") return undefined;
  for (const name of names) {
    if (object[name] !== undefined) return object[name];
  }
  return undefined;
}

function isUnreachable(error) {
  const code = error?.cause?.code || error?.code;
  return ["ECONNREFUSED", "ENOTFOUND", "EHOSTUNREACH", "ECONNRESET"].includes(code);
}

export class UnrealMcp {
  constructor(url) {
    this.url = assertLocalUrl(url);
    this.sessionId = null;
    this.protocolVersion = null;
    this.serverInfo = null;
    this.tools = [];
    this.connected = false;
    this.lastError = null;
    this.nextId = 1;
    this.tail = Promise.resolve();
    this.sseAbort = null;
  }

  snapshot() {
    return {
      url: this.url,
      connected: this.connected,
      serverName: this.serverInfo?.name ?? null,
      serverVersion: this.serverInfo?.version ?? null,
      protocolVersion: this.protocolVersion,
      toolCount: this.tools.length,
      toolNames: this.tools.map((tool) => tool.name),
      error: this.lastError,
    };
  }

  enqueue(task) {
    const run = this.tail.then(task, task);
    this.tail = run.then(
      () => {},
      () => {},
    );
    return run;
  }

  async connect() {
    try {
      const snapshot = await this.enqueue(async () => {
        if (this.connected) {
          try {
            await this._refreshTools();
            this.lastError = null;
            return this.snapshot();
          } catch {
            await this._stopSse();
            this.sessionId = null;
            this.connected = false;
          }
        }
        await this._connect();
        return this.snapshot();
      });
      console.log(
        `[unreal] ${snapshot.serverName || "unreal-mcp"} ${snapshot.toolCount} tools @ ${this.url}`,
      );
      return snapshot;
    } catch (error) {
      this.lastError = error.message;
      this.connected = false;
      throw error;
    }
  }

  async disconnect() {
    await this.enqueue(async () => {
      const sessionId = this.sessionId;
      const version = this.protocolVersion;
      await this._stopSse();
      this.connected = false;
      this.sessionId = null;
      this.tools = [];
      if (!sessionId) return;
      try {
        await fetch(this.url, {
          method: "DELETE",
          headers: {
            "Mcp-Protocol-Version": version || PROTOCOL_VERSIONS[0],
            "Mcp-Session-Id": sessionId,
          },
        });
      } catch {
        // The editor may not implement session deletion.
      }
    });
  }

  async callTool(name, args) {
    return this.enqueue(() => this._callTool(name, args, false));
  }

  async _connect() {
    let lastError = null;
    for (const version of PROTOCOL_VERSIONS) {
      try {
        await this._stopSse();
        this.sessionId = null;
        this.connected = false;
        this.protocolVersion = version;
        const result = await this._rpc(
          "initialize",
          {
            protocolVersion: version,
            capabilities: {},
            clientInfo: { name: "grok-voice-unreal", version: "1.0.0" },
          },
          { timeoutMs: 8000 },
        );
        const negotiated = pick(result, "protocolVersion", "ProtocolVersion");
        if (negotiated) this.protocolVersion = negotiated;
        const info = pick(result, "serverInfo", "ServerInfo");
        this.serverInfo = info
          ? { name: pick(info, "name", "Name") ?? null, version: pick(info, "version", "Version") ?? null }
          : null;
        await this._rpc("notifications/initialized", {}, { notify: true, timeoutMs: 8000 });
        await this._refreshTools();
        this.connected = true;
        this.lastError = null;
        this._openSse();
        return;
      } catch (error) {
        lastError = error;
        this.connected = false;
        if (error.status === 0) throw error;
        const versionProblem = /version|protocol/i.test(error.message || "") || error.status === 400;
        if (!versionProblem) throw error;
      }
    }
    throw lastError;
  }

  async _refreshTools() {
    const tools = [];
    let cursor;
    for (let page = 0; page < 30; page += 1) {
      const result = await this._rpc("tools/list", cursor ? { cursor } : {}, { timeoutMs: 20000 });
      for (const tool of pick(result, "tools", "Tools") || []) {
        const name = pick(tool, "name", "Name");
        if (!name) continue;
        tools.push({
          name: String(name),
          description: String(pick(tool, "description", "Description") || pick(tool, "title", "Title") || ""),
          inputSchema: pick(tool, "inputSchema", "InputSchema") || { type: "object", properties: {} },
        });
      }
      cursor = pick(result, "nextCursor", "NextCursor");
      if (!cursor) break;
    }
    this.tools = tools;
  }

  async _callTool(name, args, retried) {
    try {
      if (!this.connected) await this._connect();
      return await this._rpc(
        "tools/call",
        { name, arguments: args && typeof args === "object" ? args : {} },
        { timeoutMs: 120000 },
      );
    } catch (error) {
      if (error.status === 404 && !retried) {
        this.connected = false;
        this.sessionId = null;
        await this._connect();
        return this._callTool(name, args, true);
      }
      throw error;
    }
  }

  async _rpc(method, params, { notify = false, timeoutMs = 30000 } = {}) {
    const id = notify ? undefined : this.nextId++;
    const body = notify
      ? { jsonrpc: "2.0", method, params }
      : { jsonrpc: "2.0", id, method, params };
    let response;
    try {
      response = await fetch(this.url, {
        method: "POST",
        headers: this._headers(),
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      if (error?.name === "TimeoutError" || error?.name === "AbortError") {
        throw new McpError("Unreal MCP timed out. The editor may be busy.", { status: 0, cause: error });
      }
      if (isUnreachable(error)) {
        throw new McpError(
          "Unreal MCP is not running. In the editor enable Unreal MCP and All Toolsets, then start the server on port 8000.",
          { status: 0, cause: error },
        );
      }
      throw new McpError(error.message || "Unreal MCP request failed.", { status: 0, cause: error });
    }

    const sessionId = response.headers.get("mcp-session-id");
    if (sessionId) this.sessionId = sessionId;

    const text = await response.text();
    if (notify && (response.status === 202 || response.status === 204 || !text.trim())) return null;

    let messages = [];
    try {
      messages = parseMcpPayload(response.headers.get("content-type"), text);
    } catch (error) {
      if (!response.ok) {
        throw new McpError(text.slice(0, 300) || `Unreal MCP HTTP ${response.status}`, {
          status: response.status,
        });
      }
      throw new McpError(`Unreal MCP returned invalid JSON: ${error.message}`, { status: response.status });
    }

    if (!messages.length) {
      if (!response.ok) {
        throw new McpError(`Unreal MCP HTTP ${response.status}`, { status: response.status });
      }
      return null;
    }

    const message = pickRpc(messages, id);
    if (message?.error) {
      throw new McpError(message.error.message || "Unreal MCP rejected the request.", {
        status: response.status,
        code: message.error.code ?? null,
      });
    }
    if (!response.ok) {
      throw new McpError(`Unreal MCP HTTP ${response.status}`, { status: response.status });
    }
    return message?.result ?? null;
  }

  _headers() {
    const headers = {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
    };
    if (this.protocolVersion) headers["Mcp-Protocol-Version"] = this.protocolVersion;
    if (this.sessionId) headers["Mcp-Session-Id"] = this.sessionId;
    return headers;
  }

  _openSse() {
    if (!this.sessionId) return;
    this.sseAbort = new AbortController();
    const headers = this._headers();
    headers.Accept = "text/event-stream";
    fetch(this.url, { method: "GET", headers, signal: this.sseAbort.signal })
      .then((response) => {
        if (!response.ok || !response.body) {
          response.body?.cancel?.();
          return;
        }
        const reader = response.body.getReader();
        const pump = () =>
          reader.read().then(({ done }) => {
            if (done || this.sseAbort?.signal.aborted) return undefined;
            return pump();
          });
        return pump();
      })
      .catch(() => {});
  }

  async _stopSse() {
    this.sseAbort?.abort();
    this.sseAbort = null;
  }
}
