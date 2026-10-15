import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";
import { McpError, UnrealMcp, assertLocalUrl, parseMcpPayload, pickRpc } from "../server/unreal.js";

test("SSE and JSON MCP payloads both parse", () => {
  const sse = [
    "event: message",
    'data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-11-25"}}',
    "",
    "data: [DONE]",
    "",
  ].join("\n");
  const messages = parseMcpPayload("text/event-stream", sse);
  assert.equal(messages.length, 1);
  assert.equal(pickRpc(messages, 1).result.protocolVersion, "2025-11-25");

  const json = parseMcpPayload("application/json", '{"jsonrpc":"2.0","id":2,"result":{"tools":[]}}');
  assert.equal(json[0].id, 2);
});

test("only a loopback Unreal MCP URL is accepted", () => {
  assert.equal(assertLocalUrl("http://127.0.0.1:8000/mcp"), "http://127.0.0.1:8000/mcp");
  assert.match(assertLocalUrl("http://localhost:8000/mcp"), /^http:\/\/localhost:8000\/mcp$/);
  assert.throws(() => assertLocalUrl("http://example.com/mcp"), McpError);
  assert.throws(() => assertLocalUrl("http://192.168.1.20:8000/mcp"), McpError);
  assert.throws(() => assertLocalUrl("file:///C:/Windows"), McpError);
});

function listen(handler) {
  const server = http.createServer(handler);
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

test("the client handshakes, lists tools, and serializes calls", async () => {
  let inFlight = 0;
  let maxInFlight = 0;
  const server = await listen((req, res) => {
    if (req.method === "GET" || req.method === "DELETE") {
      res.writeHead(405);
      res.end();
      return;
    }
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => {
      const message = JSON.parse(body || "{}");
      if (message.method === "initialize") {
        res.writeHead(200, {
          "Content-Type": "application/json",
          "Mcp-Session-Id": "session-1",
        });
        res.end(JSON.stringify({
          jsonrpc: "2.0",
          id: message.id,
          result: {
            protocolVersion: "2025-11-25",
            serverInfo: { name: "unreal-mcp", version: "5.8" },
          },
        }));
        return;
      }
      if (!req.headers["mcp-session-id"]) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, error: { message: "Missing session" } }));
        return;
      }
      if (message.method === "notifications/initialized") {
        res.writeHead(202);
        res.end();
        return;
      }
      if (message.method === "tools/list") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({
          jsonrpc: "2.0",
          id: message.id,
          result: {
            tools: [
              { name: "list_toolsets", description: "List", inputSchema: { type: "object", properties: {} } },
              { name: "describe_toolset", description: "Describe", inputSchema: { type: "object" } },
              { name: "call_tool", description: "Call", inputSchema: { type: "object" } },
            ],
          },
        }));
        return;
      }
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      setTimeout(() => {
        inFlight -= 1;
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({
          jsonrpc: "2.0",
          id: message.id,
          result: { content: [{ type: "text", text: message.params.name }] },
        }));
      }, 40);
    });
  });

  try {
    const { port } = server.address();
    const client = new UnrealMcp(`http://127.0.0.1:${port}/mcp`);
    const snapshot = await client.connect();
    assert.equal(snapshot.serverName, "unreal-mcp");
    assert.equal(snapshot.protocolVersion, "2025-11-25");
    assert.equal(snapshot.toolCount, 3);
    const [first, second] = await Promise.all([
      client.callTool("list_toolsets", {}),
      client.callTool("call_tool", { tool_name: "ping" }),
    ]);
    assert.equal(first.content[0].text, "list_toolsets");
    assert.equal(second.content[0].text, "call_tool");
    assert.equal(maxInFlight, 1);
    await client.disconnect();
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("a dead session is reinitialized once", async () => {
  let calls = 0;
  let initializes = 0;
  const server = await listen((req, res) => {
    if (req.method !== "POST") {
      res.writeHead(405);
      res.end();
      return;
    }
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => {
      const message = JSON.parse(body || "{}");
      if (message.method === "initialize") {
        initializes += 1;
        res.writeHead(200, { "Content-Type": "application/json", "Mcp-Session-Id": `s-${initializes}` });
        res.end(JSON.stringify({
          jsonrpc: "2.0",
          id: message.id,
          result: { protocolVersion: "2025-11-25", serverInfo: { name: "unreal-mcp", version: "5.8" } },
        }));
        return;
      }
      if (message.method === "notifications/initialized") {
        res.writeHead(202);
        res.end();
        return;
      }
      if (message.method === "tools/list") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, result: { tools: [] } }));
        return;
      }
      calls += 1;
      if (calls === 1) {
        res.writeHead(404, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, error: { message: "Unknown session" } }));
        return;
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, result: { ok: true } }));
    });
  });

  try {
    const { port } = server.address();
    const client = new UnrealMcp(`http://127.0.0.1:${port}/mcp`);
    await client.connect();
    const result = await client.callTool("call_tool", { tool_name: "list_toolsets" });
    assert.equal(result.ok, true);
    assert.equal(initializes, 2);
    await client.disconnect();
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
