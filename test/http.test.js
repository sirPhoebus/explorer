import assert from "node:assert/strict";
import test from "node:test";
import { createApp } from "../server/index.js";

test("the page loads and does not ask for an API key", async () => {
  const secret = "subscription-token-should-not-leak";
  const app = createApp({
    unrealUrl: "http://127.0.0.1:9/mcp",
    auth: {
      describe() {
        return { signedIn: true, name: "Ada" };
      },
      async getAccessToken() {
        return secret;
      },
    },
  });
  await new Promise((resolve) => app.server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = app.server.address();
    const page = await fetch(`http://127.0.0.1:${port}/`);
    const html = await page.text();
    assert.equal(page.status, 200);
    assert.match(html, /xAI subscription/);
    assert.equal(/api key/i.test(html), false);
    assert.equal(html.includes(secret), false);

    const status = await fetch(`http://127.0.0.1:${port}/api/status`);
    const body = await status.json();
    assert.equal(body.signedIn, true);
    assert.equal(body.name, "Ada");
    assert.equal(body.model, "grok-voice-3.0");
    assert.equal(JSON.stringify(body).includes(secret), false);
  } finally {
    await app.close();
  }
});
