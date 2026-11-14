"""Play modes for running civ6-mcp with a human at the keyboard.

Selected with the CIV_MCP_MODE environment variable:

    auto     The AI plays the game by itself.
    advisor  The human plays; the AI only reads state and gives advice
             (every tool that changes the game is removed).
    coop     Human and AI share the same seat. The AI acts when asked, and the
             human can take over at any time.

Unset keeps the upstream behaviour (unattended benchmark runs).

Any mode means a person is sitting at the game, so the unattended-run helpers
are switched off: no kill-and-reload on connection loss, no web dashboard, and
popups are only auto-dismissed when the AI is the one playing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

MODES = ("auto", "advisor", "coop")

MODE = os.environ.get("CIV_MCP_MODE", "").strip().lower()
if MODE and MODE not in MODES:
    log.warning("Unknown CIV_MCP_MODE=%r, ignoring", MODE)
    MODE = ""

# Base game: no Rise and Fall / Gathering Storm mechanics
BASE_GAME = os.environ.get("CIV_MCP_BASE_GAME", "").strip().lower() in ("1", "true", "yes")
EXTRA_INSTRUCTIONS = os.environ.get("CIV_MCP_EXTRA_INSTRUCTIONS", "").strip()

# A human is present: never kill / relaunch the game behind their back
ALLOW_AUTO_RESTART = not MODE
# The dashboard on port 8000 is for benchmark runs; two sessions would also fight over the port
WEB_API = not MODE
# Camera hops show the human what the AI is doing; pointless when the AI cannot act
CAMERA = MODE != "advisor"
# Off in every human-attended mode. It would close things the human is reading, and it
# polls the InGame UI twice a second: in a base-game session the turn stopped advancing
# while it was polling (upstream's end_turn notes warn that InGame queries during AI turn
# processing can stall the game). The AI still has dismiss_popup, and end_turn clears
# popups once before it ends the turn.
POPUP_WATCHER = not MODE

# Tools that start, stop or reload the game, or run arbitrary Lua
LIFECYCLE_TOOLS = {
    "run_lua",
    "list_saves",
    "load_save",
    "load_game_save",
    "kill_game",
    "launch_game",
    "load_save_from_menu",
    "restart_and_load",
}

# Tools for mechanics the base game does not have
EXPANSION_TOOLS = {
    "get_governors",
    "appoint_governor",
    "assign_governor",
    "promote_governor",
    "get_dedications",
    "choose_dedication",
    "get_world_congress",
    "queue_wc_votes",
}

CONTROL_TOOL = "set_control"

# The game accepts a single tuner connection. Each session's preset runs its own
# server process (the host starts them all up front), so a server only connects
# when a tool is called and lets go after this many idle seconds.
IDLE_RELEASE_SECONDS = float(os.environ.get("CIV_MCP_IDLE_RELEASE_SECONDS", "20") or 20)

_inflight = 0
_last_call = 0.0


def enter() -> None:
    """A tool call started."""
    global _inflight, _last_call
    _inflight += 1
    _last_call = time.monotonic()


def leave() -> None:
    """A tool call finished."""
    global _inflight, _last_call
    _inflight = max(0, _inflight - 1)
    _last_call = time.monotonic()


def active() -> bool:
    """True while a tool call is running or one finished recently.

    Background services (popup watcher, camera, game-over watchdog) check this
    before touching the game, so they never open a connection on their own.
    Always true when no mode is set (upstream behaviour).
    """
    if not MODE:
        return True
    return _inflight > 0 or (time.monotonic() - _last_call) < IDLE_RELEASE_SECONDS


async def idle_release_loop(conn) -> None:
    """Close the tuner connection once this server has been idle for a while."""
    while True:
        await asyncio.sleep(3)
        try:
            if conn.is_connected and not active():
                async with conn._lock:
                    if not active():
                        await conn.disconnect()
                        log.info("Idle: released the tuner connection")
        except Exception:
            log.debug("Idle release failed", exc_info=True)


_read_only: set[str] = set()
_control = "ai"  # who may change the game right now: "ai" or "player"


def control() -> str:
    return _control


def gate(tool_name: str) -> str | None:
    """Return a refusal message if this tool may not run right now, else None."""
    if not MODE or tool_name == CONTROL_TOOL or tool_name in _read_only:
        return None
    if _control == "player":
        return (
            "玩家已接管，游戏操作被拒绝（没有执行）。现在只能使用查询类工具。"
            "停下来告诉玩家你原本打算做什么，等玩家说可以继续（例如“你来”“交给你”）后，"
            f'先调用 {CONTROL_TOOL}(who="ai")，再重新查询局面后行动。'
        )
    return None


async def set_control(who: str) -> str:
    """切换由谁操作游戏。

    who="player"：玩家接管。之后所有会改变游戏的工具都会被拒绝，你只能查询局面、给建议。
        玩家说“我来”“我自己玩一会儿”“停一下”“别动”之类时调用。
    who="ai"：交还给你。玩家说“你来”“交给你”“继续”之类时调用。
        交还后局面可能已经被玩家改动过，先重新查询（get_game_overview、get_units、get_cities）再行动。
    """
    global _control
    who = (who or "").strip().lower()
    if who not in ("ai", "player"):
        return 'who 只能是 "ai" 或 "player"。'
    _control = who
    if who == "player":
        return "已切换：玩家接管。你现在只能查询局面和给建议，不能操作。"
    return "已切换：由你操作。局面可能已经变化，先重新查询再行动。"


# ---------------------------------------------------------------------------
# Instructions sent to the client when it connects
# ---------------------------------------------------------------------------

_COMMON = """\
- 游戏界面是中文，工具返回的名称是中文；工具参数里的类型名用英文大写标识（如 UNIT_SETTLER、TECH_POTTERY）。
- 你只知道自己查询过的信息。回答或动手前先查清楚，不要凭印象编坐标、单位编号、城市编号。
- 工具报错时读懂原因再调整，不要用同样的参数重复调用；解决不了就如实告诉玩家，不要假装成功。
- 和玩家说话用中文，简短自然。
"""

_BASE_GAME = """\
- 这局是**原版规则**，没有“迭起兴衰”和“风云变幻”资料片：没有总督、忠诚度、时代分数与黄金/黑暗时代、着力点、
  世界议会、外交支持、外交胜利、气候与自然灾害、电力、战略资源储量。手册里提到这些机制的内容一律忽略，
  相关数值显示为 0 是正常的。
"""

_INTRO = {
    "auto": """\
# 文明6 · AI 自己玩

你通过这组工具亲自游玩一局《文明6》。你就是玩家，目标是赢下这局。玩家在旁边看着，偶尔会和你说话。

- 每个回合：先查询局面，再逐个处理单位和城市，最后调用 end_turn。然后接着打下一回合，不用每回合都等玩家发话。
- 每隔几个回合用一两句话向玩家汇报进展：做了什么、接下来打算做什么。
- end_turn 的五个反思字段都要填，用中文写具体内容（单位、坐标、数值、后面几回合的计划）。
  这是你跨会话的记忆；接手一局已经在进行的游戏时，先用 get_diary 看之前的记录。
- end_turn 被阻止时（有单位没行动、没选科技等），按提示处理掉阻塞项，再调用一次。
- 玩家提出要求时优先照做；玩家说“我来”“停一下”之类，调用 set_control(who="player") 把操作权交出去。
""",
    "advisor": """\
# 文明6 · AI 当助理

玩家自己在玩《文明6》，你是坐在旁边的参谋。你**只有查询类工具，不能操作游戏**：不能移动单位、不能选生产、
不能结束回合。所有操作都由玩家自己在游戏里完成。

- 玩家问什么就查什么、答什么：局面分析、该造什么、该研究什么、哪里适合建城、区域怎么摆、对手威胁、胜利进度。
- 给建议要具体：说清楚哪个城市、哪个单位、哪个坐标、为什么，方便玩家照着在游戏里操作。
- 玩家没问的时候不要长篇大论。发现明显的危险或错过的机会（比如敌军逼近、有城市没在生产），可以主动提醒一句。
- 玩家每次操作后局面都会变，回答前重新查询，不要沿用之前查到的旧数据。
- 手册里“每回合必做”的流程和所有操作类工具，是给 AI 自己玩时用的，在这里只当作理解游戏的背景知识。
""",
    "coop": """\
# 文明6 · 一起玩

你和玩家坐在同一个位置上，共同玩同一局《文明6》。玩家可以随时自己在游戏里操作，也可以让你来操作。

- 玩家让你做什么就做什么：移动单位、选生产、选科技、处理外交，用工具去做，做完告诉玩家结果。
- 玩家只是问问题或征求意见时，只查询、给建议，不要擅自操作。
- **没有玩家明确同意，不要调用 end_turn。** 玩家说“结束回合”“下一回合”“这回合你来打”之类才可以。
  调用时五个反思字段都要用中文填写具体内容。
- 玩家说“你自己打 N 回合”“这几回合交给你”时，可以连续操作并结束回合，每回合结束后用一两句话汇报。
- **随时接手**：玩家说“我来”“我自己玩一会儿”“停一下”“别动”，立刻调用 set_control(who="player") 并停手；
  玩家说“你来”“交给你”“继续”，调用 set_control(who="ai") 后再行动。
- 玩家可能在你两次操作之间自己动过游戏，所以每次动手前都重新查询局面，不要沿用旧数据。
- 你有不同意见可以直说（比如玩家的决定有明显风险时提醒一句），但最后听玩家的。
- 手册里“每回合必做”的流程是给 AI 自己玩时用的；一起玩时以玩家的要求为准。
""",
}


def build_instructions() -> str:
    """Mode intro (Chinese) + the upstream agent playbook (AGENTS.md)."""
    parts = [_INTRO[MODE], "## 通用规则\n" + _COMMON + (_BASE_GAME if BASE_GAME else "")]
    if EXTRA_INSTRUCTIONS:
        parts.append("## 玩家的额外要求\n" + EXTRA_INSTRUCTIONS)
    manual = Path(__file__).resolve().parents[2] / "AGENTS.md"
    try:
        parts.append("## 工具操作手册（英文）\n\n" + manual.read_text(encoding="utf-8"))
    except OSError:
        log.warning("AGENTS.md not found at %s; sending mode instructions only", manual)
    return "\n\n".join(parts)


def apply(mcp) -> None:
    """Trim the tool list for the selected mode and set the server instructions.

    Call once, after every tool is registered and before the server starts.
    """
    global _read_only
    if not MODE:
        return
    manager = mcp._tool_manager
    tools = dict(manager._tools)
    _read_only = {
        name
        for name, tool in tools.items()
        if getattr(getattr(tool, "annotations", None), "readOnlyHint", False)
    }

    hidden = set(LIFECYCLE_TOOLS)
    if BASE_GAME:
        hidden |= EXPANSION_TOOLS
    hidden |= {
        t.strip() for t in os.environ.get("CIV_MCP_HIDE_TOOLS", "").split(",") if t.strip()
    }
    if MODE == "advisor":
        hidden |= {name for name in tools if name not in _read_only}

    for name in sorted(hidden & set(tools)):
        manager.remove_tool(name)

    if MODE != "advisor":
        mcp.add_tool(set_control, name=CONTROL_TOOL)

    mcp._mcp_server.instructions = build_instructions()
    log.info(
        "Mode %s: %d tools exposed (%d hidden)%s",
        MODE,
        len(manager._tools),
        len(hidden & set(tools)),
        ", base game" if BASE_GAME else "",
    )
