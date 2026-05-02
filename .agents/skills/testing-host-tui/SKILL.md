---
name: testing-host-tui
description: End-to-end testing of the host TUI, remote client, lobby management, chat, and game-mode layout switch. Use when verifying any TUI / network / lobby change.
---

# Testing Host TUI + Remote Client end-to-end

This app has two entry points that talk over TCP:

- `python3 main_server.py --port 9527 --players N`  — host TUI (Textual) + lobby + game logic.
- `python3 main_client.py --host 127.0.0.1 --port 9527 --name <name>`  — remote client TUI.

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

## Driving the TUI via computer-use

Textual focuses widgets in tab order. The command input is the bottom-most interactive widget; its placeholder text reads:

- Host lobby phase: `ai/rl/policy/status 管理 | /chat 聊天 | /whisper <玩家> 私聊`
- Game phase / non-host: `游戏指令 | /chat 聊天 | /whisper <玩家> 私聊 | help 帮助 | F1 完整帮助`

Reliable way to focus the input:

1. Click directly on the placeholder text line at the bottom of the screen. Coordinates depend on screen size — typically around `y=720` on a 1024x768 display.
2. If unsure, press `Tab` repeatedly to cycle focus until the input border highlights.

After clicking the placeholder line, the input becomes editable. `type` then `key Return` to submit.

## Useful invariants for assertions

| Behavior | Visible state |
| --- | --- |
| Lobby mode (host) | `HostPanel` (title `═══ 房间管理 ═══` + slot list + management help + buttons) occupies the main area. `#game-log` is hidden via `lobby-mode-hidden` class. |
| Game mode | `HostPanel` hidden (no `lobby-mode` class), `#game-log` visible with game events. |
| Management cmd success | Slot row updates AND a `✓` line appears below the slot list. |
| Empty-slot start | `[系统] 还有空位未填满，无法开始` appears inside `HostPanel`'s slot list (NOT in hidden game-log). |

## Known quirks (not bugs in #241, but useful to know)

1. **HostPanel doesn't auto-refresh** when remote players join. The host TUI doesn't subscribe to its own `LOBBY_UPDATE` broadcasts (host's `self.client is None`). Workaround: after a client connects, type `status` or click 刷新 to re-render the slot list.
2. **Private chat creates two tabs per conversation**, one per side, because `ChatPanel.add_message` uses `label = target or sender`. A whisper from A→B creates `私聊:B` on both ends; the reply creates `私聊:A` on both ends. The label being the target/sender (not the always-other-party) is pre-existing behavior in `tui/widgets/chat_panel.py`.
3. **Widget IDs must be ASCII** (Textual `BadIdentifier`). Anything dynamic that gets used as an `id=` for `RichLog`/`TabPane` must use a counter or hash, never a player name. PR #241 fixed `_add_private_tab` to use `_tab_counter`.

## Smoke test path

1. Launch host with `--players 3`. Confirm full-screen `HostPanel` (slot 1 host + slots 2/3 empty).
2. Click 开始游戏 with empty slots. Confirm 「还有空位未填满」 appears in HostPanel.
3. `ai 3 aggressive` → slot 3 = `basic_ai | AI-aggressive`. `policy 3 ai` → `ai_takeover`. `status` → list refreshes.
4. Launch CJK client. Type `status` on host. Confirm slot 2 = `human_remote | 测试玩家 | 已连接`.
5. Host: `/whisper 测试玩家 你好`. Confirm new tab `私聊:测试玩家` (no BadIdentifier).
6. Client: `/whisper 房主 收到`. Confirm host's TUI gets new tab `私聊:房主` with `测试玩家: 收到` (no BadIdentifier).
7. Click 开始游戏. Confirm HostPanel disappears, game-log fills the area, placeholder changes to game phase.

## Devin secrets needed

None. All testing is local on the VM.
