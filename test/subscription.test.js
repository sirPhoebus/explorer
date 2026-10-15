import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createSubscriptionAuth, readSession, tokenIsFresh } from "../server/subscription.js";

test("a fresh subscription token is reused without a refresh", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "voice-auth-"));
  const authPath = path.join(dir, "auth.json");
  const expires = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  fs.writeFileSync(authPath, JSON.stringify({
    "https://auth.x.ai::client": {
      key: "access-old",
      auth_mode: "oidc",
      first_name: "Ada",
      refresh_token: "refresh-old",
      expires_at: expires,
      oidc_issuer: "https://auth.x.ai",
      oidc_client_id: "client",
    },
  }));
  const auth = createSubscriptionAuth({
    authPath,
    fetchImpl: async () => {
      throw new Error("refresh should not run");
    },
  });
  assert.equal(auth.describe().name, "Ada");
  assert.equal(await auth.getAccessToken(), "access-old");
  assert.equal(tokenIsFresh(expires), true);
});

test("an expired subscription token is refreshed and saved", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "voice-auth-"));
  const authPath = path.join(dir, "auth.json");
  fs.writeFileSync(authPath, JSON.stringify({
    "https://auth.x.ai::client": {
      key: "access-old",
      auth_mode: "oidc",
      first_name: "Ada",
      refresh_token: "refresh-old",
      expires_at: new Date(Date.now() - 60_000).toISOString(),
      oidc_client_id: "client",
    },
  }));

  const tokenServer = http.createServer((req, res) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => {
      const params = new URLSearchParams(body);
      assert.equal(params.get("grant_type"), "refresh_token");
      assert.equal(params.get("refresh_token"), "refresh-old");
      assert.equal(params.get("client_id"), "client");
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        access_token: "access-new",
        refresh_token: "refresh-new",
        expires_in: 3600,
      }));
    });
  });
  await new Promise((resolve) => tokenServer.listen(0, "127.0.0.1", resolve));

  const originalUrl = "https://auth.x.ai/oauth2/token";
  const auth = createSubscriptionAuth({
    authPath,
    fetchImpl: (url, options) => {
      assert.equal(url, originalUrl);
      const port = tokenServer.address().port;
      return fetch(`http://127.0.0.1:${port}/token`, options);
    },
  });

  try {
    assert.equal(await auth.getAccessToken(), "access-new");
    const saved = readSession(JSON.parse(fs.readFileSync(authPath, "utf8")));
    assert.equal(saved.key, "access-new");
    assert.equal(saved.refresh_token, "refresh-new");
    assert.equal(tokenIsFresh(saved.expires_at), true);
  } finally {
    await new Promise((resolve) => tokenServer.close(resolve));
  }
});
