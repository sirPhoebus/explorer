import assert from "node:assert/strict";
import test from "node:test";
import { buildInstructions } from "../server/instructions.js";
import { planTools, runTool, searchTools } from "../server/tools.js";

const searchToolsFixture = [
  { name: "list_toolsets", description: "List toolsets", inputSchema: { type: "object", properties: {} } },
  {
    name: "describe_toolset",
    description: "Describe one toolset",
    inputSchema: {
      type: "object",
      properties: { toolset_name: { type: "string" } },
      required: ["toolset_name"],
    },
  },
  {
    name: "call_tool",
    description: "Run one tool",
    inputSchema: {
      type: "object",
      properties: { tool_name: { type: "string" }, arguments: { type: "object" } },
      required: ["tool_name"],
    },
  },
];

test("Unreal tool search exposes the three meta tools", () => {
  const plan = planTools(searchToolsFixture);
  assert.equal(plan.mode, "search");
  assert.ok(plan.functions.some((tool) => tool.name === "call_tool"));
  const call = plan.functions.find((tool) => tool.name === "call_tool");
  assert.equal(call.parameters.properties.tool_name.type, "string");
  assert.match(plan.prompt, /toolset_name/);
  assert.match(plan.prompt, /player_echo\.EchoTools/);
});

test("a large tool list is indexed instead of registered one by one", () => {
  const tools = Array.from({ length: 40 }, (_, index) => ({
    name: `tool_${index}`,
    description: index === 3 ? "spawn a point light" : "editor action",
    inputSchema: { type: "object" },
  }));
  const plan = planTools(tools);
  assert.equal(plan.mode, "indexed");
  assert.ok(plan.functions.some((tool) => tool.name === "call_unreal_tool"));
  assert.equal(plan.functions.some((tool) => tool.name === "tool_0"), false);
  const found = searchTools(tools, "point light");
  assert.equal(found.matches, 1);
  assert.equal(found.tools[0].name, "tool_3");
});

test("an offline editor only offers reconnect", () => {
  const plan = planTools([], { connected: false });
  assert.equal(plan.mode, "offline");
  assert.deepEqual(plan.functions.map((tool) => tool.name), ["reconnect_unreal_editor"]);
  assert.match(buildInstructions(plan.prompt), /Do what the user asks/);
  assert.match(buildInstructions(plan.prompt), /player_bridge\.PlayerTools/);
  assert.match(buildInstructions(plan.prompt), /player_keys\.PlayerKeyTools/);
  assert.match(buildInstructions(plan.prompt), /player_echo\.EchoTools/);
  assert.match(buildInstructions(plan.prompt), /level open in this Unreal editor/);
});

test("indexed calls dispatch to the named Unreal tool", async () => {
  const seen = [];
  const unreal = {
    tools: [{ name: "spawn_light", description: "Spawn", inputSchema: { type: "object" } }],
    async connect() {
      return { toolNames: ["spawn_light"], toolCount: 1 };
    },
    async callTool(name, args) {
      seen.push({ name, args });
      return { content: [{ type: "text", text: "spawned" }] };
    },
  };
  const plan = planTools(unreal.tools);
  const result = await runTool(plan, unreal, "call_unreal_tool", {
    tool_name: "spawn_light",
    arguments: "{\"intensity\":5000}",
  });
  assert.equal(seen[0].name, "spawn_light");
  assert.equal(seen[0].args.intensity, 5000);
  assert.equal(result.content[0].text, "spawned");
});
