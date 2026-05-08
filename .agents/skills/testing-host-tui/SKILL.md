---
name: testing-host-tui
description: End-to-end testing of the host TUI, remote client, lobby management, chat, and game-mode layout switch. Use when verifying any TUI / network / lobby change.
---

# Testing Host TUI + Remote Client end-to-end

This app has two entry points that talk over TCP:

- `python3 main_server.py --port 9527 --players N`  — host TUI (Textual) + lobby + game logic.
- `python3 main_client.py --host 127.0.0.1 --port 9527 --name <name>`  — remote client TUI.
- `python3 main_client.py --host 127.0.0.1 --port 9527 --name <name> --cli`  — remote client in **CLI mode**. Use this for adversarial assertions: stdout is line-by-line text and can be `grep`-ed deterministically. Pair `--cli` with `stdbuf -oL -eL python3 -u` to defeat output buffering when piping through `tee`.

With `--players N --no-host-play` slots 1..N start as `EMPTY`. Default (`--players 2`) makes slot 1 = `HUMAN_LOCAL` (host plays).

Slot IDs are **1-based** (`PlayerSlot.slot_id = i + 1`). Management commands use those IDs (e.g. `ai 3 aggressive` targets slot id 3).

## Quickest E2E setup on the VM

```bash
# Terminal 1 — host TUI (xterm so we can drive it via the GUI)
DISPLAY=:0 xterm -geometry 130x50+0+0 -fa "DejaVu Sans Mono" -fs 12 -bg black -fg white -title "HOST" \
  -e bash -lc "cd /home/ubuntu/repos/Game-hosting-automation-program-for-Badtime-war- \
    && python3 main_server.py --port 9527 --players 3; echo END; bash" &

DISPLAY=:0 wmctrl -i -r $(DISPLAY=:0 wmctrl -l | awk '/HOST$/{print $1; exit}') -b add,maximized_vert,maximized_horz

# Terminal 2 — CJK-named remote client
DISPLAY=:0 xterm -geometry 100x40+700+50 -fa "DejaVu Sans Mono" -fs 11 -bg "#001830" -fg white -title "CLIENT" \
  -e bash -lc "cd /home/ubuntu/repos/Game-hosting-automation-program-for-Badtime-war- \
    && python3 main_client.py --host 127.0.0.1 --port 9527 --name 测试玩家; echo END; bash" &
```

Requires `xterm` + `wmctrl` on the box: `sudo apt-get install -y wmctrl xterm`.

UTF-8 names pass through `xterm -e bash -lc "..."` correctly when `LANG=C.UTF-8` (default in this VM). Verify by checking the xterm child process args: `ps -ww -p <pid> -o args= | xxd`.

For side-by-side recording (small windows), use `wmctrl -e 0,X,Y,W,H` to fix geometry instead of maximizing — keeps both visible at 1024×768. Example: HOST left half (`0,0,0,560,760`), CLIENT right half (`0,520,0,500,760`).

## Driving the TUI via computer-use

Textual focuses widgets in tab order. The command input is the bottom-most interactive widget; its placeholder text reads:

- Host lobby phase: `ai/rl/policy/status 管理 | /chat 聊天 | /whisper <玩家> 私聊`
- Game phase / non-host: `游戏指令 | /chat 聊天 | /whisper <玩家> 私聊 | help 帮助 | F1 完整帮助`

Reliable way to focus the input:

1. Click directly on the placeholder text line at the bottom of the screen. Coordinates depend on screen size — typically around `y=720` on a 1024x768 display.
2. If unsure, press `Tab` repeatedly to cycle focus until the input border highlights.

After clicking the placeholder line, the input becomes editable. `type` then `key Return` to submit.

### Fallback: deterministic Python harness when TUI input fails

Occasionally a re-launched xterm's host TUI cmd-input refuses focus from `xdotool windowactivate+type` and `computer.left_click+type` (Textual `dock: bottom` widget focus quirk; multiple methods may fail in a row on the same window). When you have to test a code path gated on `cmd_input.wait_for_input(...)` and GUI input is broken, write a deterministic harness that mocks the heavy collaborators and supplies the desired input directly:

```python
# /tmp/test_xxx.py
import sys
sys.path.insert(0, "/home/ubuntu/repos/Game-hosting-automation-program-for-Badtime-war-")
import main_server
from network.protocol import MessageType

broadcasts, pushes = [], []

class FakeServer:
    def broadcast_sync(self, msg): broadcasts.append(msg)
    def send_to_sync(self, cid, msg): pass

class FakeCmdInput:
    def wait_for_input(self, timeout=None): return "n"  # <-- the input you want

class FakeApp:
    _game_starting = True
    def push_game_event(self, msg): pushes.append(msg)
    def query_one(self, sel): return FakeCmdInput()

# FakeLobby/FakeSlot/FakeLobbyState/FakeBroadcaster/FakeRoundManager:
# minimal stubs, see /home/ubuntu/test_n_branch.py from PR #249 testing for a full template.

main_server.DisplayBroadcaster = FakeBroadcaster
main_server.RoundManager = FakeRoundManager
main_server._setup_ai_chat = lambda *a, **k: None
main_server._patch_engine_context = lambda *a, **k: None
main_server._start_game_tui(FakeServer(), FakeLobby(), object(), True, FakeApp())
assert any(c.get("args") == ["  天赋系统未启用。"] for c in broadcasts)
```

This bypasses Textual entirely; it's effectively a unit test of the function under test, and it can prove an exact line of the diff fires with the exact payload. Use as a fallback only — real two-process TCP runs with the CLI client are still the primary verification path. Watch out for `FakeLobby.slots` needing real `SlotType` enum values from `main_server.SlotType` (e.g. `HUMAN_LOCAL`, `HUMAN_REMOTE`, `BASIC_AI`, `RL_AI`, `EMPTY`).

## Useful invariants for assertions

| Behavior | Visible state |
| --- | --- |
| Lobby mode (host) | `HostPanel` (title `═══ 房间管理 ═══` + slot list + management help + buttons) occupies the main area. `#game-log` is hidden via `lobby-mode-hidden` class. |
| Game mode | `HostPanel` hidden (no `lobby-mode` class), `#game-log` visible with game events. |
| Management cmd success | Slot row updates AND a `✓` line appears below the slot list. |
| Empty-slot start | `[系统] 还有空位未填满，无法开始` appears inside `HostPanel`'s slot list (NOT in hidden game-log). |
| Client REQUEST_COMMAND | `#game-log` shows 4-line block: `▶ 轮到你行动 [<name>]`, `HP: x/y \| 位置: <loc>`, `可选行动: ...`, `请在下方输入框中输入指令 (输入 help 查看帮助)`. None of these appear pre-PR-#243. |
| Chat during pending REQUEST_* | After `/chat <内容>` or `/whisper <玩家> <内容>`, `#game-log` shows `[聊天已发送，<行动指令\|选择\|多选\|确认>仍在等候你输入]`. Pending request preserved (next non-chat input still consumes). |
| Bare `/chat` / malformed `/whisper` | `#game-log` shows `⚠ /chat 需要内容...` / `⚠ /whisper 需要目标...`. No public/private broadcast. |
| Whisper tab title (post-PR #243) | Each side's `ChatPanel` labels the tab with the OTHER party's name: HOST→`私聊:测玩`, CLIENT→`私聊:房主`. Pre-PR-#243 both sides showed the same label (target name on both ends). |

### Talent selection broadcast invariants (post-PR-#249)

`_network_talent_selection_tui` + `_start_game_tui` send result-bearing messages to host AND all remote clients via `_broadcast_all`; input errors stay host-only via `app.push_game_event`; the chooser additionally gets a personal confirmation via `_send_to_client`.

| Trigger | HOST TUI `#game-log` | All CLIENTs (CLI stdout / `#game-log`) | Chooser only (personal) |
| --- | --- | --- | --- |
| Host reaches `是否启用天赋系统？` | `是否启用天赋系统？在输入框输入 y 或 n` | `  ⏳ 等待房主决定是否启用天赋系统...` | — |
| Host types `y` | `  天赋系统已启用，开始选择天赋...` | same | — |
| Host types `n` | `  天赋系统未启用。` | same | — |
| Talent selection starts | full 14-line `可选天赋…` block + `0. 不选天赋` | same | — |
| Each player's turn begins | `══ 轮到 <name> 选择天赋 ══` | same | — |
| Active player's turn (other remotes) | — | `  ⏳ 等待 <name> 选择天赋...` (only to OTHER remote clients, not host) | — |
| Player picks talent | `  ✓ <name> 获得天赋【YYY】！` | same | (remote chooser) `  ✅ 你已成功选择天赋【YYY】` |
| Player picks `0. 不选天赋` | `  <name> 选择不使用天赋。` | same | (remote chooser) `  你选择了不使用天赋。` |
| Host input error (bad number / taken / non-int) | `[错误] 无效编号。` / `[错误] 该天赋已被其他玩家选走` / `[错误] 请输入有效编号。` | **NOTHING** (host-only) | — |
| Selection complete | `  ─── 天赋分配结果 ───` + `    👤 <name>: <talent>` rows + `    🤖 <name>: <talent>` rows | same | — |

Negative regression tests to keep:
- `grep -E "无效编号\|请输入有效编号\|该天赋已被" client.log` MUST yield zero matches after a host-side input-error session.
- The chooser's `  ✅ 你已成功选择...` line MUST NOT appear on non-chooser clients (it goes via `_send_to_client`, not `broadcast_sync`).

## Smoke test path

1. Launch host with `--players 3`. Confirm full-screen `HostPanel` (slot 1 host + slots 2/3 empty).
2. Click 开始游戏 with empty slots. Confirm 「还有空位未填满」 appears in HostPanel.
3. `ai 3 aggressive` → slot 3 = `basic_ai | AI-aggressive`. `policy 3 ai` → `ai_takeover`. `status` → list refreshes.
4. Launch CJK client. Type `status` on host. Confirm slot 2 = `human_remote | 测试玩家 | 已连接`.
5. Host: `/whisper 测试玩家 你好`. Confirm new tab `私聊:测试玩家` on host (no BadIdentifier) and `私聊:房主` on client (post-PR-#243 label).
6. Client: `/whisper 房主 收到`. Confirm host's TUI gets new tab `私聊:房主` (post-PR-#243) — actually the CLIENT side now sees `私聊:房主` and host already had `私聊:测试玩家`, so each tab is per-other-party.
7. Click 开始游戏. Confirm HostPanel disappears, game-log fills the area, placeholder changes to game phase.

## Fastest path to a clean REQUEST_COMMAND on the client

For PRs that touch the client REQUEST_* / chat-during-pending flow, the fastest deterministic path is **talents-disabled**:

1. HOST: `start` (or click 开始游戏). Game-log shows `[系统] 游戏即将开始...` then `─── 游戏已开始 ───` and the help block.
2. HOST: type `n` (the `_start_game_tui` flow asks `是否启用天赋系统？` via `wait_for_input`; the prompt may not visually render in narrow windows, but `wait_for_input` is still active — just type `n`).
3. Game advances through round 1 settlement and into round 2 action phase. CLIENT's `#game-log` then receives the REQUEST_COMMAND 4-line block.
4. HOST: `forfeit` is the cleanest no-op action that always advances the turn — useful when you need to pass control to the remote player.

**Why disable talents for E2E testing of OTHER flows:** the talent menu (`REQUEST_CHOOSE` from `_network_talent_selection_tui`) is now fully observable on remote clients post-PR-#249 (see invariants table), so testing the talent flow itself is fine — but for testing combat/chat/REQUEST_COMMAND flows it remains faster to skip past it via `n`. Some talents (e.g. 剪刀手一突's `警觉`) trigger `response_window` deadlocks if the server-side controller's `wait_for_input` doesn't get a visible prompt; skipping talents avoids that.

## Known quirks

1. **HostPanel doesn't auto-refresh** when remote players join. The host TUI doesn't subscribe to its own `LOBBY_UPDATE` broadcasts (host's `self.client is None`). Workaround: after a client connects, type `status` or click 刷新 to re-render the slot list.
2. **RichLog auto-scroll disables on manual scroll**. If you scroll the `#game-log` widget with the mouse wheel during testing, subsequent `log.write(...)` calls won't push the view down — new content lands below the visible area silently, looking like "the write didn't happen". To re-enable auto-scroll: click the widget and press `End`, or scroll all the way to the bottom. **Avoid scrolling the game-log with the mouse during tests** unless you explicitly need to inspect history.
3. **`CommandInput.on_input_submitted` pre-classifies prefixes**. `/chat <内容>` (with trailing space + content) is dispatched as `cmd_type="chat"` BEFORE `_respond_to_pending` runs; the bare `/chat` (no space) falls through to `cmd_type="game"` and goes through `_respond_to_pending`. Any feedback that should fire for both paths must live in `on_command_submitted`'s chat/whisper branches (see `_log_pending_chat_feedback`), not only inside `_dispatch_inline_chat`. PR #243 commit `2a380c7` fixed this.
4. **Widget IDs must be ASCII** (Textual `BadIdentifier`). Anything dynamic that gets used as an `id=` for `RichLog`/`TabPane` must use a counter or hash, never a player name. PR #241 fixed `_add_private_tab` to use `_tab_counter`.
5. **xterm cmd-input may refuse focus on re-launch.** A second host xterm in the same session sometimes ignores `xdotool windowactivate+type` and `computer.left_click+type` for the `dock: bottom` cmd-input even though the first run worked. If multiple focus methods fail in a row, switch to the deterministic Python harness pattern in “Driving the TUI via computer-use → Fallback”.
6. **`textual==0.8.2.5` is yanked from PyPI.** Use `textual==8.2.5` (not `0.8.2.5`). The `.0` prefix from older releases is gone; importing it works regardless of the version pin.

## Devin secrets needed

None. All testing is local on the VM.
