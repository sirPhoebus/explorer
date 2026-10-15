import assert from "node:assert/strict";
import test from "node:test";
import { createTurnTracker } from "../server/turns.js";

test("a response with no tool call does not execute", () => {
  const turns = createTurnTracker();
  assert.equal(turns.onEvent({ type: "response.created" }).action, "none");
  assert.equal(turns.onEvent({ type: "response.done", response: { status: "completed" } }).action, "none");
});

test("several function calls wait until response.done", () => {
  const turns = createTurnTracker();
  turns.onEvent({ type: "response.created" });
  assert.equal(
    turns.onEvent({ type: "response.function_call_arguments.done", name: "list_toolsets", call_id: "a", arguments: "{}" }).action,
    "none",
  );
  assert.equal(
    turns.onEvent({ type: "response.function_call_arguments.done", name: "call_tool", call_id: "b", arguments: "{\"tool_name\":\"x\"}" }).action,
    "none",
  );
  const decision = turns.onEvent({ type: "response.done", response: { status: "completed" } });
  assert.equal(decision.action, "execute");
  assert.deepEqual(decision.calls.map((call) => call.name), ["list_toolsets", "call_tool"]);
});

test("a cancelled response drops its tool calls", () => {
  const turns = createTurnTracker();
  turns.onEvent({ type: "response.function_call_arguments.done", name: "call_tool", call_id: "a", arguments: "{}" });
  const decision = turns.onEvent({ type: "response.done", response: { status: "cancelled" } });
  assert.equal(decision.action, "drop");
});

test("duplicate call ids are ignored", () => {
  const turns = createTurnTracker();
  turns.onEvent({ type: "response.function_call_arguments.done", name: "call_tool", call_id: "a", arguments: "{}" });
  turns.onEvent({ type: "response.function_call_arguments.done", name: "call_tool", call_id: "a", arguments: "{}" });
  const decision = turns.onEvent({ type: "response.done", response: { status: "completed" } });
  assert.equal(decision.calls.length, 1);
});
