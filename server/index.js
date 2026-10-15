import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer } from "ws";
import { DEFAULT_PORT, DEFAULT_UNREAL_URL, MODEL } from "./config.js";
import { DEFAULT_ECHO_MAP_DIR, clearEchoMaps, fetchLivePawn, readEchoMap } from "./echo-map.js";
import { loadEnv } from "./env.js";
import { createSubscriptionAuth } from "./subscription.js";
import { classifyTools, searchTools } from "./tools.js";
import { McpError, UnrealMcp, assertLocalUrl } from "./unreal.js";
import { VoiceSession } from "./voice.js";

const root = fileURLToPath(new URL("..", import.meta.url));
const publicDir = fileURLToPath(new URL("../public/", import.meta.url));
const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

function readJson(req, limit = 8192) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(Object.assign(new Error("Body too large"), { status: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      if (!chunks.length) return resolve({});
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch {
        reject(Object.assign(new Error("Invalid JSON"), { status: 400 }));
      }
    });
    req.on("error", reject);
  });
}

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  res.end(payload);
}

function safePublicPath(urlPath) {
  const decoded = decodeURIComponent(urlPath.split("?")[0]);
  const relativeUrl = decoded === "/" ? "index.html" : decoded.replace(/^\/+/, "");
  const full = path.resolve(publicDir, relativeUrl);
  const relative = path.relative(publicDir, full);
  if (relative.startsWith("..") || path.isAbsolute(relative)) return null;
  return full;
}

export function createApp(options = {}) {
  const hub = {
    auth: options.auth || createSubscriptionAuth(),
    unreal: new UnrealMcp(options.unrealUrl || process.env.UNREAL_MCP_URL || DEFAULT_UNREAL_URL),
    echoMapDir: options.echoMapDir || process.env.ECHO_MAP_DIR || DEFAULT_ECHO_MAP_DIR,
    sessions: new Set(),
  };

  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url || "/", "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/api/status") {
        const account = hub.auth.describe();
        sendJson(res, 200, {
          signedIn: account.signedIn,
          name: account.name,
          model: MODEL,
          unreal: {
            ...hub.unreal.snapshot(),
            mode: hub.unreal.connected ? classifyTools(hub.unreal.tools) : "offline",
          },
        });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/unreal/connect") {
        const body = await readJson(req);
        if (body.url) {
          const nextUrl = assertLocalUrl(body.url);
          if (nextUrl !== hub.unreal.url) {
            await hub.unreal.disconnect();
            hub.unreal = new UnrealMcp(nextUrl);
          }
        }
        const snapshot = await hub.unreal.connect();
        for (const session of hub.sessions) session.adoptTools();
        sendJson(res, 200, { ok: true, unreal: snapshot });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/echo-map") {
        let map;
        try {
          map = readEchoMap(hub.echoMapDir);
        } catch {
          map = { empty: true, reason: "The echo map could not be read." };
        }
        const pawn = await fetchLivePawn(hub.unreal.url);
        if (pawn) map.pawn = pawn;
        sendJson(res, 200, map);
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/echo-map/clear") {
        const result = clearEchoMaps(hub.echoMapDir);
        sendJson(res, 200, { ok: true, ...result });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/unreal/tools") {
        sendJson(res, 200, searchTools(hub.unreal.tools, url.searchParams.get("q") || ""));
        return;
      }
      if (req.method !== "GET" && req.method !== "HEAD") {
        sendJson(res, 405, { error: "Method not allowed." });
        return;
      }
      const filePath = safePublicPath(url.pathname);
      if (!filePath || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
        sendJson(res, 404, { error: "Not found." });
        return;
      }
      const body = fs.readFileSync(filePath);
      res.writeHead(req.method === "HEAD" ? 200 : 200, {
        "Content-Type": types[path.extname(filePath)] || "application/octet-stream",
        "Content-Length": body.length,
        "Cache-Control": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy":
          "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; worker-src 'self'",
        "Referrer-Policy": "no-referrer",
      });
      res.end(req.method === "HEAD" ? undefined : body);
    } catch (error) {
      const status = error instanceof McpError ? 502 : error.status || 500;
      sendJson(res, status, { error: error.message || "Server error." });
    }
  });

  const wss = new WebSocketServer({ server, path: "/ws" });
  wss.on("connection", (socket) => {
    const session = new VoiceSession({ hub, socket });
    hub.sessions.add(session);
    socket.on("message", (data, isBinary) => {
      try {
        session.onBrowser(data, isBinary);
      } catch (error) {
        session.send({ type: "error", message: error.message });
      }
    });
    socket.on("close", () => {
      session.close();
      hub.sessions.delete(session);
    });
  });

  return {
    server,
    hub,
    async close() {
      for (const session of hub.sessions) session.close();
      await new Promise((resolve) => wss.close(resolve));
      await hub.unreal.disconnect().catch(() => {});
      if (server.listening) await new Promise((resolve) => server.close(resolve));
    },
  };
}

const isDirect =
  process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));

if (isDirect) {
  loadEnv(path.join(root, ".env"));
  const app = createApp();
  const port = Number(process.env.PORT) || DEFAULT_PORT;
  app.server.listen(port, "127.0.0.1", () => {
    console.log(`Grok Voice Unreal listening on http://127.0.0.1:${port}`);
    console.log(`Voice model: ${MODEL}`);
    console.log(`Unreal MCP: ${app.hub.unreal.url}`);
  });
}
