# Grok Voice 3 for Unreal

A local voice chat app. You talk, Grok Voice 3 (`grok-voice-3.0`) calls tools, and this machine forwards those calls to Unreal MCP in the open editor.

xAI cannot reach the editor. Unreal MCP listens only on `127.0.0.1:8000` and has no authentication, so this app proxies tool calls on your machine. Voice uses the xAI subscription already stored by Grok in `~/.grok/auth.json`. Tool results go back to the voice model so it can tell you what changed.

## Run

```powershell
cd "F:\repo\voice grok 3"
copy .env.example .env
npm install
npm start
```

Sign in with Grok first. The app uses that subscription and does not ask for an API key. Open http://127.0.0.1:8787

## Unreal Editor

The project at `MyProject2` needs both plugins enabled:

1. Edit → Plugins. Enable **Unreal MCP** and **All Toolsets**. Restart the editor.
2. Edit → Editor Preferences → General → Model Context Protocol. Turn on **Auto Start Server**.
3. The server is `http://127.0.0.1:8000/mcp`. The Output Log should say it is starting on port 8000.

Those plugins are now listed in `MyProject2.uproject`, and auto-start is set in the project’s editor settings. The editor that is already open will not load them until it restarts.

Then press **Connect** in the voice app, press **Start**, and say what you want done. For example: “Add a warm point light two meters above the origin.”

The editor runs in tool-search mode. Grok calls `list_toolsets`, `describe_toolset`, and `call_tool` instead of guessing from hundreds of tools.

## Checks

```powershell
npm test
```
