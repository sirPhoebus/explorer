import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createApp } from "../server/index.js";
import { clearEchoMaps, parsePlayerState, readEchoMap } from "../server/echo-map.js";
import { lookAt, transformPoint, unrealToView, unrealYawForward } from "../public/echo-frame.js";

function equalVec(actual, expected) {
  assert.equal(actual.length, expected.length);
  for (let index = 0; index < expected.length; index += 1) assert.equal(actual[index], expected[index]);
}

test("Unreal Z-up stays right-handed in the Y-up view", () => {
  const x = unrealToView(1, 0, 0);
  const y = unrealToView(0, 1, 0);
  const z = unrealToView(0, 0, 1);
  equalVec(x, [1, 0, 0]);
  equalVec(z, [0, 1, 0]);
  equalVec(y, [0, 0, -1]);
  const triple = x[0] * (y[1] * z[2] - y[2] * z[1])
    + x[1] * (y[2] * z[0] - y[0] * z[2])
    + x[2] * (y[0] * z[1] - y[1] * z[0]);
  assert.equal(triple, 1);
  equalVec(unrealYawForward(0).map((value) => Math.round(value)), [1, 0, 0]);
  equalVec(unrealYawForward(90).map((value) => Math.round(value)), [0, 0, -1]);
});

test("the orbit camera looks toward its target", () => {
  const view = lookAt([0, 0, 10], [0, 0, 0], [0, 1, 0]);
  const origin = transformPoint(view, [0, 0, 0]);
  assert.ok(origin[2] < 0);
  const above = transformPoint(view, [0, 1, 0]);
  assert.ok(above[1] > 0);
});

test("a saved echo map is served to the page", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "echo-map-"));
  fs.writeFileSync(
    path.join(dir, "Lvl_ThirdPerson.json"),
    JSON.stringify({
      cell: 35,
      step: 45,
      floors: [[0, 0, 210]],
      walls: [[1, 0, 100]],
      solids: [[1, 0, 2]],
      pawn: [140, 577, 262, 20],
    }),
  );
  const loaded = readEchoMap(dir);
  assert.equal(loaded.level, "Lvl_ThirdPerson");
  assert.equal(loaded.solids.length, 1);
  assert.equal(loaded.pawn.z, 262);
  assert.deepEqual(parsePlayerState("Playing as BP at (140, 577, 262) cm, speed 0 cm/s, yaw 20. standing."), {
    x: 140,
    y: 577,
    z: 262,
    yaw: 20,
  });

  const app = createApp({
    unrealUrl: "http://127.0.0.1:9/mcp",
    echoMapDir: dir,
    auth: { describe() { return { signedIn: true, name: "Ada" }; }, async getAccessToken() { return "secret"; } },
  });
  await new Promise((resolve) => app.server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = app.server.address();
    const page = await fetch(`http://127.0.0.1:${port}/`);
    const html = await page.text();
    assert.match(html, /id="echo-view"/);
    assert.match(html, /What the pawn sees/);
    assert.match(html, /id="clear-map"/);
    const body = await (await fetch(`http://127.0.0.1:${port}/api/echo-map`)).json();
    assert.equal(body.empty, false);
    assert.equal(body.solids[0][0], 1);
    assert.equal(body.pawn.x, 140);
    fs.writeFileSync(path.join(dir, "notes.txt"), "keep");
    const cleared = await (await fetch(`http://127.0.0.1:${port}/api/echo-map/clear`, { method: "POST" })).json();
    assert.equal(cleared.ok, true);
    assert.equal(cleared.removed, 1);
    assert.equal(fs.existsSync(path.join(dir, "Lvl_ThirdPerson.json")), false);
    assert.equal(fs.existsSync(path.join(dir, "notes.txt")), true);
    assert.equal(fs.readFileSync(path.join(dir, ".clear"), "utf8"), "clear\n");
    const fresh = await (await fetch(`http://127.0.0.1:${port}/api/echo-map`)).json();
    assert.equal(fresh.empty, true);
    assert.match(fresh.reason, /next walk starts empty/);
    const hidden = clearEchoMaps(dir);
    assert.equal(hidden.removed, 0);
    fs.writeFileSync(path.join(dir, "Again.json"), "{}");
    assert.equal(readEchoMap(dir).empty, true);
  } finally {
    await app.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
