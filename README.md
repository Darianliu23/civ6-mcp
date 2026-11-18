# civ6-mcp

> **About this fork / 关于这个分叉** — branch `base-game-compat`
>
> This is a fork of [lmwilki/civ6-mcp](https://github.com/lmwilki/civ6-mcp) used by the DSH plugin [dsh-civ6](https://github.com/Darianliu23/dsh-civ6). Everything below this box is the upstream README. Changes on this branch:
>
> - **Base game (no expansions)**: queries that called Rise and Fall / Gathering Storm–only APIs (era score, loyalty, favor, resource stockpiles, grievances, victory points) fall back to defaults instead of raising Lua errors; governor / dedication / World Congress queries return empty results. `CIV_MCP_BASE_GAME=1` also hides those tools.
> - **Play modes for a human at the keyboard** (`CIV_MCP_MODE=auto|advisor|coop`, see `src/civ_mcp/modes.py`): `advisor` exposes read-only tools only; `auto` and `coop` add a `set_control` tool — once the player takes over, every tool that changes the game is refused. Lifecycle tools (launch / kill / load save / `run_lua`) are hidden. The server instructions become a Chinese mode intro followed by `AGENTS.md`.
> - **Nothing kills or reloads the game while a human is playing**: the hang and connection-loss auto-restarts, the popup auto-dismisser and the web dashboard are off in these modes (the dashboard also binds to 127.0.0.1 now).
> - **Sharing the tuner connection**: the game accepts one tuner connection, and a host may start several server processes, so in these modes a server connects on first use and lets go after 20 idle seconds.
> - **End turn**: closes the tutorial advisor popup, which holds an engine event and freezes AI turns; reports a units blocker in seconds instead of polling for 10 minutes when `UI.CanEndTurn()` wrongly says yes.
> - **Diary across sessions**: `get_diary` merges every run's diary for the same game.
> - Smaller fixes: UTF-8 file reads on Chinese Windows, binding the game on any tool call so logs are not held in memory.
>
> Tested on Windows 11 with the base game only. Without `CIV_MCP_MODE` the server behaves as upstream.
>
> 这是 [lmwilki/civ6-mcp](https://github.com/lmwilki/civ6-mcp) 的分叉，给 DSH 插件 [dsh-civ6](https://github.com/Darianliu23/dsh-civ6) 用。改动在 `base-game-compat` 分支：原版规则兼容、三种有人在场的玩法和接管开关、有人在玩时绝不杀游戏或读档、多个进程轮流共用调试连接、结束回合自动关顾问弹窗、日记跨会话合并读取。只在 Windows 11 + 原版文明6 上测过。

An MCP server that lets LLM agents play full games of Civilization VI.

Connect any MCP-compatible client — Claude Code, Codex, Gemini CLI, or your own — to a running Civ 6 game. The agent reads game state, moves units, manages cities, conducts diplomacy, and ends turns, all through the game's own rule-enforcing APIs. No cheats, no vision model required.

<!-- TODO: Add screenshot or GIF of agent playing -->

## Capabilities

76 tools covering the full gameplay loop:

- **Units** — list, move, attack, fortify, found cities, build improvements, promote, upgrade
- **Cities** — inspect, set production, purchase units/buildings with gold, manage focus
- **Map** — explore terrain, resources, fog of war; get settle and district placement advice
- **Research** — browse tech and civic trees, set research targets
- **Diplomacy** — relationships, modifiers, delegations, embassies, alliances, peace deals
- **Trade** — propose and respond to deals, manage trade routes and destinations
- **Government** — swap policy cards, change governments, choose era dedications
- **Governors** — appoint, assign to cities, promote
- **Religion** — found pantheons and religions, select beliefs, track spread
- **Great People** — recruit, patronize, reject
- **World Congress** — vote on resolutions, manage diplomatic favor
- **Victory** — track progress across all victory conditions
- **Game lifecycle** — save, load, launch, restart, kill

Every turn, `end_turn` takes before/after snapshots and reports what happened: units damaged, cities grew, production completed, threats spotted near your cities.

## Quick start

### 1. Configure Civ 6

Enable the FireTuner debug interface and configure recommended settings:

| Setting | Value | Why |
|---------|-------|-----|
| **Tuner** | Enabled | Required — opens the TCP debug port the MCP server connects to. Disables achievements. |
| **Auto End Turn** | Disabled | The agent controls when turns end. Auto-end interferes with the blocker resolution flow. |
| **Windowed mode** | Recommended | Lets you watch the game while the agent plays. Required for OCR-based save loading. |

**Windows:** All three settings are available in the in-game Options menu. The Tuner setting appears as "Tuner (disables achievements)" under gameplay options.

**macOS:** The Tuner setting is not exposed in the menu. Edit `AppOptions.txt` directly and set `EnableTuner 1`:

```
~/Library/Application Support/Sid Meier's Civilization VI/Firaxis Games/Sid Meier's Civilization VI/AppOptions.txt
```

**Linux:** Same as macOS — edit `AppOptions.txt` directly and set `EnableTuner 1`:

```
~/.local/share/aspyr-media/Sid Meier's Civilization VI/AppOptions.txt
```

<details>
<summary><strong>Windows: additional setup</strong></summary>

**Install the Civ 6 SDK** — the tuner server is part of the SDK, not the base game:
1. In Steam, go to Library → filter by Tools
2. Find and install "Sid Meier's Civilization VI SDK"

**Important notes:**
- Close `FireTuner.exe` (the SDK's GUI tool) before running civ6-mcp — the game only allows **one** tuner connection at a time
- Do **not** run from WSL — the network bridging between WSL2 and Windows is unreliable and the tuner server locks up after failed connections
- If the connection fails, **restart the game** — the tuner often hangs after a bad handshake and won't recover until the process is recycled
</details>

<details>
<summary><strong>Linux: additional notes</strong></summary>

- The **native Linux port** is required — the FireTuner debug interface is built into the native binary. Proton/Wine builds do not expose it.
- The game runs as a single `Civ6` process launched via Steam Linux Runtime (scout-on-soldier).
- GUI automation (OCR-based menu navigation) requires **X11**. On Wayland, the game typically runs under XWayland which should work, but a native X11 session is most reliable.
</details>

Restart Civ 6. The game will listen on TCP port 4318 for connections.

### 2. Install

```bash
git clone https://github.com/lmwilki/civ6-mcp.git
cd civ6-mcp
uv sync
```

For GUI automation features (screenshot, OCR-based menu navigation):

```bash
# macOS
uv pip install 'civ6-mcp[launcher-macos]'

# Windows (uses built-in Windows OCR — no external binaries needed)
uv pip install 'civ6-mcp[launcher-windows]'

# Linux (Ubuntu/Debian)
sudo apt install xdotool tesseract-ocr
uv pip install 'civ6-mcp[launcher-linux]'
```

### 3. Test the connection

With Civ 6 running and a game loaded:

```bash
uv run python scripts/test_connection.py
```

You should see a successful handshake and a list of Lua states (GameCore_Tuner, InGame, etc.).

### 4. Configure your MCP client

The server runs over stdio. Point your client at it:

<details>
<summary><strong>Claude Code</strong></summary>

The repo includes `.mcp.json` — detected automatically:

```bash
cd civ6-mcp
claude
```
</details>

<details>
<summary><strong>Claude Desktop</strong></summary>

Add to your config file:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "civ6": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/civ6-mcp", "civ-mcp"]
    }
  }
}
```
</details>

<details>
<summary><strong>Codex</strong></summary>

Add to `.codex/config.toml` in the project root:

```toml
[mcp_servers.civ6]
command = "uv"
args = ["run", "--directory", "/path/to/civ6-mcp", "civ-mcp"]
```
</details>

<details>
<summary><strong>Gemini CLI</strong></summary>

Add to `.gemini/settings.json` in the project root:

```json
{
  "mcpServers": {
    "civ6": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/civ6-mcp", "civ-mcp"]
    }
  }
}
```
</details>

<details>
<summary><strong>Other MCP clients</strong></summary>

The server speaks stdio JSON-RPC:

```bash
uv run civ-mcp
```
</details>

### 5. Play

Load a game in Civ 6, connect your client, and try:

```
Play my Civ 6 game. Start by getting an overview, then check units and
cities, and play through the turn.
```

The agent will orient with `get_game_overview`, scan the map for threats, move units, set production and research, handle diplomacy, and end the turn.

## As a benchmark

Civilization VI is a compelling environment for evaluating LLM strategic reasoning. Games run 300+ turns with compounding decisions, incomplete information, and multiple competing objectives — a significant step up from single-turn or short-horizon tasks.

- **Multi-turn planning** — decisions compound over hundreds of turns with delayed payoffs
- **Incomplete information** — fog of war, hidden AI intentions, unexplored map
- **Resource management** — balancing gold, production, science, culture, faith, and military
- **Opponent modeling** — reading diplomatic signals, anticipating AI behavior
- **Strategic adaptation** — responding to threats, shifting priorities mid-game

The MCP interface provides a clean abstraction: the model receives narrated game state as text and responds with tool calls. All game rules are enforced by the engine. A companion web app lets you replay sessions turn by turn.

## How it works

```
Claude / Any MCP Client
    |  stdio (JSON-RPC)
    v
MCP Server (Python)    <- 70+ tools
    |
    |  Generates Lua code at runtime
    |  TCP :4318
    v
Civilization VI        <- Game is the TCP server
```

The server maintains a persistent TCP connection to Civ 6 via the FireTuner debug protocol. It generates Lua code, executes it inside the game's two Lua VMs (GameCore for reading state, InGame for issuing commands), parses the output, and returns narrated text to the LLM.

The repo includes an [AGENTS.md](AGENTS.md) playbook (symlinked as `CLAUDE.md` for Claude Code) with detailed instructions for agents: turn loop, combat, diplomacy, common pitfalls. See the [devlog](docs/devlog/) for the full development story, including reverse-engineering the FireTuner protocol and the many API quirks discovered along the way.

## Requirements

- **macOS, Windows, or Linux** with Civilization VI (Steam version, Gathering Storm DLC)
- **Python 3.12+** with [uv](https://docs.astral.sh/uv/)
- An **MCP client** (Claude Code, Codex, Gemini CLI, or any MCP-compatible client)

## License

MIT
