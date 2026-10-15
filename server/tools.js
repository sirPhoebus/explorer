const DIRECT_LIMIT = 12;
const SEARCH_LIMIT = 8;
const SEARCH_NAMES = ["list_toolsets", "describe_toolset", "call_tool"];
const RECONNECT_NAME = "reconnect_unreal_editor";

export function classifyTools(tools) {
  if (!tools.length) return "empty";
  const names = new Set(tools.map((tool) => tool.name));
  const search = SEARCH_NAMES.every((name) => names.has(name));
  if (search && tools.length <= SEARCH_LIMIT) return "search";
  if (tools.length <= DIRECT_LIMIT) return "direct";
  return "indexed";
}

export function clip(value, max) {
  const text = String(value ?? "");
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

export function safeJson(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return JSON.stringify({ error: "Unserializable tool result" });
  }
}

export function clampText(value, max = 12000) {
  const text = typeof value === "string" ? value : safeJson(value);
  if (text.length <= max) return text;
  return `${text.slice(0, max)}… [truncated ${text.length - max} characters]`;
}

function sanitizeName(name) {
  const cleaned = String(name).replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 64);
  return /^[a-zA-Z]/.test(cleaned) ? cleaned : `t_${cleaned}`.slice(0, 64);
}

function normalizeSchema(schema) {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
    return { type: "object", properties: {}, additionalProperties: true };
  }
  const copy = { ...schema };
  delete copy.$schema;
  if (!copy.type) copy.type = "object";
  if (copy.type === "object" && !copy.properties) copy.properties = {};
  const encoded = safeJson(copy);
  if (encoded.length > 6000) {
    return {
      type: "object",
      additionalProperties: true,
      description: "JSON arguments for this Unreal tool.",
    };
  }
  return copy;
}

function toFunction(tool, name) {
  return {
    type: "function",
    name,
    description: clip(tool.description || `Unreal Editor tool ${tool.name}`, 500),
    parameters: normalizeSchema(tool.inputSchema),
  };
}

function reconnectFunction() {
  return {
    type: "function",
    name: RECONNECT_NAME,
    description:
      "Reconnect to the local Unreal Editor MCP server and refresh its tools. Call this when the editor was closed, the server was restarted, or a tool fails because Unreal is disconnected.",
    parameters: { type: "object", properties: {} },
  };
}

const INDEX_FUNCTIONS = [
  {
    type: "function",
    name: "list_unreal_tools",
    description:
      "Search the Unreal Editor tools by name or description. Call this before using an action you have not already described.",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Short substring such as light, blueprint, actor, material, or niagara.",
        },
      },
    },
  },
  {
    type: "function",
    name: "describe_unreal_tool",
    description: "Get the JSON schema for one Unreal Editor tool before calling it.",
    parameters: {
      type: "object",
      properties: {
        tool_name: { type: "string", description: "Exact tool name from list_unreal_tools." },
      },
      required: ["tool_name"],
    },
  },
  {
    type: "function",
    name: "call_unreal_tool",
    description: "Run one Unreal Editor tool. The editor executes one tool at a time.",
    parameters: {
      type: "object",
      properties: {
        tool_name: { type: "string", description: "Exact tool name from list_unreal_tools." },
        arguments: {
          type: "object",
          description: "Arguments matching the tool schema. Use an empty object when the tool takes none.",
          additionalProperties: true,
        },
      },
      required: ["tool_name"],
    },
  },
];

const PROMPTS = {
  search:
    "The editor is in tool-search mode. Call list_toolsets first. Then describe_toolset with toolset_name. Then call_tool with tool_name, the toolset_name, and arguments that match the schema. Do not invent toolset or tool names. Looking uses toolset player_bridge.PlayerTools. Walking, jumping, named keys, mouse buttons, and typed text use player_keys.PlayerKeyTools. W A S D and SpaceBar are the character bindings. Walking around, exploring, or mapping the open level uses player_echo.EchoTools walk_around. That level has cubes and ramps. walk_around looks forward in a wide cone, down toward the floor, and glances to either side. It maps those volumes, follows ramps, goes around cubes, and stops before walls. Space behind the pawn stays unseen until it turns. stop_walk_around stops it.",
  direct:
    "Call the Unreal tools by the names you were given. Read the description and schema before you call. If a tool fails, pick a more specific one.",
  indexed:
    "Hundreds of editor tools are available. Call list_unreal_tools with a short query, then describe_unreal_tool, then call_unreal_tool. Do not invent tool names.",
  empty:
    "No editor tools were advertised. Call reconnect_unreal_editor. If it still returns no tools, tell the user to enable the All Toolsets plugin and run ModelContextProtocol.RefreshTools.",
  offline:
    "Unreal MCP is not connected. When the user wants a change in the editor, call reconnect_unreal_editor and then use the tools that appear.",
};

export function planTools(mcpTools, { connected = true } = {}) {
  if (!connected) {
    return {
      mode: "offline",
      functions: [reconnectFunction()],
      aliases: new Map(),
      prompt: PROMPTS.offline,
    };
  }
  const mode = classifyTools(mcpTools);
  const aliases = new Map();
  const used = new Set([RECONNECT_NAME]);
  const functions = [];

  const claim = (original) => {
    let name = sanitizeName(original);
    let suffix = 2;
    while (used.has(name)) {
      name = `${sanitizeName(original).slice(0, 58)}_${suffix}`;
      suffix += 1;
    }
    used.add(name);
    aliases.set(name, original);
    return name;
  };

  if (mode === "search" || mode === "direct") {
    for (const tool of mcpTools) functions.push(toFunction(tool, claim(tool.name)));
  } else if (mode === "indexed") {
    functions.push(...INDEX_FUNCTIONS);
  }

  functions.push(reconnectFunction());
  return {
    mode,
    functions,
    aliases,
    prompt: PROMPTS[mode],
  };
}

export function searchTools(tools, query) {
  const needle = String(query || "").trim().toLowerCase();
  const matched = needle
    ? tools.filter((tool) => `${tool.name} ${tool.description}`.toLowerCase().includes(needle))
    : tools;
  const shown = matched.slice(0, 30).map((tool) => ({
    name: tool.name,
    description: clip(tool.description, 180),
  }));
  return {
    total: tools.length,
    matches: matched.length,
    tools: shown,
    truncated: matched.length > shown.length,
  };
}

function asObject(value) {
  if (value == null || value === "") return {};
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : { value: parsed };
    } catch {
      return { value };
    }
  }
  if (typeof value === "object" && !Array.isArray(value)) return value;
  return { value };
}

export async function runTool(plan, unreal, name, args) {
  const input = asObject(args);
  if (name === RECONNECT_NAME) {
    const snapshot = await unreal.connect();
    return { __refreshTools: true, ok: true, ...snapshot, toolNames: snapshot.toolNames.slice(0, 80) };
  }
  if (name === "list_unreal_tools") return searchTools(unreal.tools, input.query);
  if (name === "describe_unreal_tool") {
    const tool = unreal.tools.find((item) => item.name === input.tool_name);
    if (!tool) return { error: `Unknown Unreal tool: ${input.tool_name || "(missing)"}` };
    return { name: tool.name, description: tool.description, inputSchema: tool.inputSchema };
  }
  if (name === "call_unreal_tool") {
    if (!input.tool_name) return { error: "tool_name is required." };
    return unreal.callTool(input.tool_name, asObject(input.arguments));
  }
  const mcpName = plan?.aliases?.get(name) || name;
  return unreal.callTool(mcpName, input);
}
