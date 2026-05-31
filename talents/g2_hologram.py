"""
神代天赋2：请一直，注视着我  (G2 Reset: ish-bosheth 舞台结界)

主动1次，T0启动，消耗行动回合。
展开 ish-bosheth 转场结界：将全场拉入舞台空间，引入情绪系统
（入戏/抽离/反抗）、Regard 注视值资源、曲目演唱、旋律伤害、破幕终结等机制。
"""

from talents.base_talent import BaseTalent
from cli import display
from engine.prompt_manager import prompt_manager
from engine.ish_bosheth import (
    IshBosheth,
    ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO, EMOTION_LABELS,
)


class Hologram(BaseTalent):
    name = "请一直，注视着我"
    description = (
        "主动1次：展开 ish-bosheth 舞台结界，将全场玩家拉入转场空间。"
        "通过曲目演唱控制舞台，Regard 归零或持续 8 轮后谢幕。"
    )
    tier = "神代"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.used = False
        self.max_uses = 1       # 涟漪献诗可 +1
        self.enhanced = False    # 涟漪献诗增强

    # ================================================================
    #  发动限制
    # ================================================================
    def _calc_min_round(self) -> int:
        """最早发动轮次 = 10 + 2 × (初始人数 - 2)。临时降为 1 以便调试。"""
        # return 10 + 2 * (len(self.state.player_order) - 2)
        return 1  # DEBUG: 临时改为第 1 轮即可发动

    # ================================================================
    #  T0 选项
    # ================================================================
    def get_t0_option(self, player):
        if player.player_id != self.player_id:
            return None
        if self.used and self.max_uses <= 0:
            return None
        if self.state.ish_bosheth is not None:
            return None  # 已有一个活跃结界
        if self.state.current_round < self._calc_min_round():
            return None  # 未到最早发动轮次
        return (
            f"发动天赋：{self.name}（展开 ish-bosheth 舞台结界）"
        )

    # ================================================================
    #  T0 执行
    # ================================================================
    def execute_t0(self, player):
        if self.state.ish_bosheth is not None:
            prompt_manager.show("g2reset", "stage.already_active")
            return None, "cancelled"

        if self.max_uses > 0:
            self.max_uses -= 1
        if self.max_uses <= 0:
            self.used = True

        lines = [
            f"\n{'='*50}",
            prompt_manager.get_prompt("g2reset", "stage.activate",
                                     player_name=player.name),
            f"  🏠 舞台中心：{player.name} 的家",
            f"{'='*50}",
        ]

        ish = IshBosheth(self.player_id)
        open_lines = ish.open(self.state, player)
        lines.extend(open_lines)

        # 展示座位分配结果，再触发第一音节
        msg = "\n".join(lines)
        display.show_result(msg)

        # 第一音节（旋律）：G2 选座位
        from controllers.human import HumanController
        if isinstance(player.controller, HumanController):
            display.show_info(
                f"\n{'='*50}\n"
                f"  🎵 第一音节 —— 请 {player.name} 选择旋律目标座位\n"
                f"{'='*50}")
        ish.execute_melody(self.state, player)
        lines.append("  🎵 第一音节已奏响。")

        self.state.log_event("ish_bosheth_activate",
                             player=self.player_id,
                             location=player.location)

        return "\n".join(lines), "talent"

    # ================================================================
    #  骰子加成
    # ================================================================
    def on_d4_bonus(self, player):
        if self.state.ish_bosheth and self.state.ish_bosheth.phase == "active":
            if player.player_id == self.player_id:
                return 3
        return 0

    def on_d6_bonus(self, player):
        if self.state.ish_bosheth and self.state.ish_bosheth.phase == "active":
            if player.player_id == self.player_id:
                return 6
        return 0

    # ================================================================
    #  轮次钩子
    # ================================================================
    # ================================================================
    #  描述
    # ================================================================
    def describe_status(self):
        if self.state.ish_bosheth and self.state.ish_bosheth.phase == "active":
            ish = self.state.ish_bosheth
            return (
                f"ish-bosheth 活跃 | Regard: {ish.regard}/{ish.regard_cap} "
                f"| R4#{ish.r4_count}"
            )
        if self.used and self.max_uses <= 0:
            return "已使用"
        return "可用"

    # ================================================================
    #  曲目执行（由 action_turn special 调用）
    # ================================================================
    def execute_sing(self, player, game_state):
        """G2 发动者的演唱行动入口。"""
        ish = game_state.ish_bosheth
        if not ish or ish.phase != "active":
            prompt_manager.show("g2reset", "stage.not_active")
            return "❌ ish-bosheth 未激活"

        # 选曲目
        songs = ish.get_available_songs()
        if not songs:
            prompt_manager.show("g2reset", "stage.no_songs")
            return "❌ 没有可用曲目"

        song_options = [
            f"{s['name']} ({s['desc']}) [消耗{s['cost']}]" for s in songs
        ]
        song_options.append("放弃演唱")

        choice = player.controller.choose(
            "选择演唱曲目：",
            song_options,
            context={"situation": "g2_sing_song"},
        )
        if "放弃" in choice:
            return "放弃演唱"

        # 解析选中曲目
        selected_song = None
        for s in songs:
            if s['name'] in choice:
                selected_song = s
                break
        if not selected_song:
            return "放弃演唱"

        # 选节奏
        rhythms = selected_song['rhythms']
        if not rhythms:
            return "放弃演唱"
        if len(rhythms) == 1:
            selected_rhythm = rhythms[0]
        else:
            rhythm_options = [
                f"{r['name']} [消耗{r['cost']}]" for r in rhythms
            ]
            rhythm_choice = player.controller.choose(
                "选择节奏：",
                rhythm_options,
                context={"situation": "g2_sing_rhythm"},
            )
            selected_rhythm = next(
                (r for r in rhythms if r['name'] in rhythm_choice),
                rhythms[0])

        total_cost = selected_rhythm['cost']
        if ish.regard < total_cost:
            prompt_manager.show("g2reset", "stage.regard_insufficient",
                               needed=total_cost, current=ish.regard)
            return "❌ Regard 不足"

        # Before light 不需选听者
        if selected_song['name'] == "Before light":
            self._execute_before_light(player, ish, selected_rhythm, total_cost)
            return f"🎵 Before light"

        # 旋律不需选听者（有自己的 choose 流程）
        if "旋律" in selected_song['name']:
            if "第二间章" in selected_song['name']:
                ish.melody_2_used = True
            elif "第三间章" in selected_song['name']:
                ish.melody_3_used = True
            ish.execute_melody(game_state, player)
            return f"🎵 {selected_song['name']}"

        # 选听者
        targets = ish.get_legal_sing_targets(game_state,
                                              selected_song['name'],
                                              selected_rhythm['name'])
        if not targets:
            prompt_manager.show("g2reset", "stage.no_targets")
            return "❌ 没有合法听者"

        target_names = [t.name for t in targets]
        target_choice = player.controller.choose(
            "选择听者：",
            target_names,
            context={"situation": "g2_sing_target"},
        )
        target = next((t for t in targets if t.name == target_choice), targets[0])

        # 执行曲目效果
        ish.regard -= total_cost

        if selected_song['name'] == "追寻那道光":
            if "Sognando" in selected_rhythm['name'] or "追寻" in selected_rhythm['name']:
                self._execute_sognando(player, target, ish, game_state)
            else:
                self._execute_soave(player, target, ish, game_state)
        elif selected_song['name'] == "拼接遗憾":
            if "Zeffiroso" in selected_rhythm['name'] or "遗憾" in selected_rhythm['name']:
                self._execute_zeffiroso(player, target, ish, game_state)
            else:
                self._execute_placido(player, target, ish)

        prompt_manager.show("g2reset", "song.regard_status",
                           regard=ish.regard, cap=ish.regard_cap)
        return f"🎵 {selected_song['name']} → {target.name}"

    # ── Soave: 聚光灯+额外行动 ──────────────────────────────────
    def _execute_soave(self, g2_player, target, ish, game_state):
        target.stage_statuses = getattr(target, 'stage_statuses', set())
        target.stage_statuses.add("spotlight")

        # 牵连
        for pid in ish.participants:
            if pid == ish.g2_owner_id:
                continue
            p = game_state.get_player(pid)
            if p and p.is_alive() and p.player_id != getattr(target, 'player_id', None):
                ent = getattr(target, 'stage_entangle', [])
                ent.append(p.player_id)
                target.stage_entangle = ent

        # 额外行动回合
        target._g2_spotlight_extra_turn = True
        # 记录授予轮次（在 R4 衰减时判断是否到期）
        target._spotlight_granted_r4 = ish.r4_count

        # Accarezzevole 加成
        if getattr(target, 'emotion', None) == ACCAREZZEVOLE:
            target.temp_hp_g2 = getattr(target, 'temp_hp_g2', 0) + 1.0
            target.temp_atk_g2 = getattr(target, 'temp_atk_g2', 0) + 1.0

        prompt_manager.show("g2reset", "song.soave", target_name=target.name)

    # ── Sognando: 强制入戏 + Soave效果 + 情绪锁 ──────────────────
    def _execute_sognando(self, g2_player, target, ish, game_state):
        from engine.ish_bosheth import ACCAREZZEVOLE
        target.emotion = ACCAREZZEVOLE
        target.stage_statuses = getattr(target, 'stage_statuses', set())
        target.stage_statuses.add("sognando_lock")
        self._execute_soave(g2_player, target, ish, game_state)
        prompt_manager.show("g2reset", "song.sognando", target_name=target.name)

    # ── Placido: 安可（取最高层数）────────────────────────────────
    def _execute_placido(self, g2_player, target, ish):
        current = getattr(target, 'encore_layers', 0)
        target.encore_layers = max(current, 1)
        prompt_manager.show("g2reset", "song.placido",
                           target_name=target.name, layers=target.encore_layers)

    # ── Zeffiroso: Placido + 情绪上调 + 已入戏则安可=2 ──────────
    def _execute_zeffiroso(self, g2_player, target, ish, game_state):
        from engine.ish_bosheth import ACCAREZZEVOLE, adjust_emotion_up
        self._execute_placido(g2_player, target, ish)
        adjust_emotion_up(target, game_state, ish)
        if target.emotion == ACCAREZZEVOLE:
            target.encore_layers = max(getattr(target, 'encore_layers', 0), 2)
        prompt_manager.show("g2reset", "song.zeffiroso",
                           target_name=target.name, layers=target.encore_layers)

    # ── Before light: 光色 ───────────────────────────────────────
    def _execute_before_light(self, g2_player, ish, rhythm, cost):
        ish.regard -= cost
        if "Riposato" in rhythm['name'] or "休息" in rhythm['name']:
            ish.before_light = "riposato"
            prompt_manager.show("g2reset", "song.riposato")
        elif "Dolente" in rhythm['name'] or "悲伤" in rhythm['name']:
            ish.before_light = "dolente"
            prompt_manager.show("g2reset", "song.dolente")
        prompt_manager.show("g2reset", "song.regard_status",
                           regard=ish.regard, cap=ish.regard_cap)
