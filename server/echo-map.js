import fs from "node:fs";
import path from "node:path";
import { parseMcpPayload, pickRpc } from "./unreal.js";

export const DEFAULT_ECHO_MAP_DIR =
  "C:\\Users\\pscho\\Documents\\Unreal Projects\\MyProject2\\Saved\\EchoMaps";

function insideDir(dir, name) {
  const root = path.resolve(dir);
  const full = path.resolve(root, name);
  const relative = path.relative(root, full);
  if (relative.startsWith("..") || path.isAbsolute(relative)) return null;
  return full;
}

export function clearEchoMaps(dir) {
  if (!dir) {
    throw Object.assign(new Error("Echo map folder is not set."), { status: 500 });
  }
  const root = path.resolve(dir);
  fs.mkdirSync(root, { recursive: true });
  let removed = 0;
  for (const name of fs.readdirSync(root)) {
    if (!name.endsWith(".json") || name.startsWith(".")) continue;
    const full = insideDir(root, name);
    if (!full) continue;
    const stat = fs.lstatSync(full);
    if (!stat.isFile() || stat.isSymbolicLink()) continue;
    fs.unlinkSync(full);
    removed += 1;
  }
  const marker = insideDir(root, ".clear");
  if (!marker) throw Object.assign(new Error("Echo map folder is not a local folder."), { status: 500 });
  fs.writeFileSync(marker, "clear\n", "utf8");
  return { cleared: true, removed };
}

export function readEchoMap(dir) {
  if (!dir || !fs.existsSync(dir)) {
    return { empty: true, reason: "No echo map has been saved yet." };
  }
  if (insideDir(dir, ".clear") && fs.existsSync(insideDir(dir, ".clear"))) {
    return { empty: true, reason: "Map cleared. The next walk starts empty." };
  }
  const files = fs.readdirSync(dir).filter((name) => name.endsWith(".json") && !name.startsWith("."));
  if (!files.length) return { empty: true, reason: "No echo map has been saved yet." };
  let newest = null;
  let newestTime = -1;
  for (const name of files) {
    const full = path.resolve(dir, name);
    const relative = path.relative(dir, full);
    if (relative.startsWith("..") || path.isAbsolute(relative)) continue;
    const stat = fs.statSync(full);
    if (!stat.isFile() || stat.mtimeMs < newestTime) continue;
    newest = { full, name, mtimeMs: stat.mtimeMs };
    newestTime = stat.mtimeMs;
  }
  if (!newest) return { empty: true, reason: "No echo map has been saved yet." };
  const payload = JSON.parse(fs.readFileSync(newest.full, "utf8"));
  return {
    empty: false,
    level: path.basename(newest.name, ".json"),
    updatedAt: new Date(newest.mtimeMs).toISOString(),
    cell: Number(payload.cell) || 0,
    step: Number(payload.step) || 0,
    pawn: normalizePawn(payload.pawn),
    floors: Array.isArray(payload.floors) ? payload.floors : [],
    walls: Array.isArray(payload.walls) ? payload.walls : [],
    solids: Array.isArray(payload.solids) ? payload.solids : [],
  };
}

export function normalizePawn(pawn) {
  if (!Array.isArray(pawn) || pawn.length < 3) return null;
  const numbers = pawn.slice(0, 4).map((value) => Number(value));
  if (numbers.slice(0, 3).some((value) => !Number.isFinite(value))) return null;
  return {
    x: numbers[0],
    y: numbers[1],
    z: numbers[2],
    yaw: Number.isFinite(numbers[3]) ? numbers[3] : 0,
  };
}

export function mcpText(result) {
  const content = result?.content || result?.Content || [];
  const text = content.map((item) => item.text || item.Text || "").join("\n");
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed.returnValue === "string") return parsed.returnValue;
  } catch {
    // The tool already returned a sentence.
  }
  return text;
}

let pawnCache = { at: 0, pawn: null };

async function mcpRpc(url, body, session, signal) {
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
    "Mcp-Protocol-Version": "2025-11-25",
  };
  if (session) headers["Mcp-Session-Id"] = session;
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal,
  });
  const text = await response.text();
  const messages = text.trim() ? parseMcpPayload(response.headers.get("content-type"), text) : [];
  return {
    session: response.headers.get("mcp-session-id") || session,
    message: pickRpc(messages, body.id),
    status: response.status,
  };
}

export async function fetchLivePawn(mcpUrl) {
  const now = Date.now();
  if (now - pawnCache.at < 1500) return pawnCache.pawn;
  const signal = AbortSignal.timeout(800);
  try {
    const init = await mcpRpc(mcpUrl, {
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2025-11-25",
        capabilities: {},
        clientInfo: { name: "echo-map", version: "1.0.0" },
      },
    }, null, signal);
    if (!init.session || init.message?.error) throw new Error("no session");
    await mcpRpc(mcpUrl, {
      jsonrpc: "2.0",
      method: "notifications/initialized",
      params: {},
    }, init.session, signal);
    const call = await mcpRpc(mcpUrl, {
      jsonrpc: "2.0",
      id: 2,
      method: "tools/call",
      params: {
        name: "call_tool",
        arguments: {
          toolset_name: "player_bridge.PlayerTools",
          tool_name: "get_player_state",
          arguments: {},
        },
      },
    }, init.session, signal);
    const pawn = parsePlayerState(mcpText(call.message?.result));
    pawnCache = { at: Date.now(), pawn };
    return pawn;
  } catch {
    pawnCache = { at: Date.now(), pawn: null };
    return null;
  }
}

export function parsePlayerState(text) {
  const match = String(text).match(
    /at \((-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)\)\s*cm[\s\S]*?yaw\s+(-?\d+(?:\.\d+)?)/,
  );
  if (!match) return null;
  return {
    x: Number(match[1]),
    y: Number(match[2]),
    z: Number(match[3]),
    yaw: Number(match[4]),
  };
}
