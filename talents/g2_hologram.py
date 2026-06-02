"""
神代天赋2：请一直，注视着我（G2 Reset v0.6）

主动1次，T0启动，消耗行动回合。
展开 ish-bosheth 舞台结界：三声部阵营、物料牌系统、Regard、旋律、曲目。
"""

import random

from talents.base_talent import BaseTalent
from cli import display
from engine.prompt_manager import prompt_manager
from engine.ish_bosheth import (
    IshBosheth,
    ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO, VOICE_LABELS,
)


class Hologram(BaseTalent):
    name = "请一直，注视着我"
    description = (
        "主动1次：展开 ish-bosheth 舞台结界，引入三声部阵营与物料牌系统。"
    )
    tier = "神代"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.used = False
        self.max_uses = 1
        self.enhanced = False

    # ================================================================
    #  发动限制
    # ================================================================
    def _calc_min_round(self) -> int:
        return 1  # DEBUG

    # ================================================================
    #  T0 选项
    # ================================================================
    def get_t0_option(self, player):
        if player.player_id != self.player_id:
            return None
        if self.used and self.max_uses <= 0:
            return None
        if self.state.ish_bosheth is not None:
            return None
        if self.state.current_round < self._calc_min_round():
            return None
        return f"发动天赋：{self.name}（展开 ish-bosheth 舞台结界）"

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

        ish = IshBosheth(self.player_id)
        open_lines = ish.open(self.state, player)

        display.show_result("\n".join(open_lines))

        # 触发第一音节
        from controllers.human import HumanController
        if isinstance(player.controller, HumanController):
            display.show_info(
                f"\n{'='*50}\n"
                f"  🎵 第一音节 —— 请 {player.name} 选择旋律目标座位\n"
                f"{'='*50}")
        ish.execute_melody(self.state, player)

        self.state.log_event("ish_bosheth_activate",
                             player=self.player_id,
                             location=player.location)

        return "\n".join(open_lines), "talent"

    # ================================================================
    #  骰子加成（v0.6: G2 固定 D4=0，此处不再提供加成）
    # ================================================================
    def on_d4_bonus(self, player):
        return 0

    def on_d6_bonus(self, player):
        return 0

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
    #  曲目执行（v0.6 新效果）
    # ================================================================
    def execute_sing(self, player, game_state):
        """G2 发动者的演唱行动入口。"""
        ish = game_state.ish_bosheth
        if not ish or ish.phase != "active":
            return "❌ ish-bosheth 未激活"

        songs = ish.get_available_songs()
        if not songs:
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

        selected_song = None
        for s in songs:
            if s['name'] in choice:
                selected_song = s
                break
        if not selected_song:
            return "放弃演唱"

        rhythms = selected_song['rhythms']
        if not rhythms:
            return "放弃演唱"
        if len(rhythms) == 1:
            selected_rhythm = rhythms[0]
        else:
            rhythm_options = [f"{r['name']} [消耗{r['cost']}]" for r in rhythms]
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
            return "❌ Regard 不足"

        # 旋律不需选目标
        if "旋律" in selected_song['name']:
            if "第二间章" in selected_song['name']:
                ish.melody_2_used = True
            elif "第三间章" in selected_song['name']:
                ish.melody_3_used = True
            ish.execute_melody(game_state, player)
            return f"🎵 {selected_song['name']}"

        # Before light 不需选听者
        if selected_song['name'] == "Before light":
            self._execute_before_light(player, ish, selected_rhythm, total_cost)
            return f"🎵 Before light"

        # Zeffiroso 需选两名听者
        if "Zeffiroso" in selected_rhythm['name'] or "遗憾" in selected_rhythm['name']:
            return self._execute_zeffiroso(player, ish, game_state, total_cost)

        # 其余：选一名听者
        targets = ish.get_legal_sing_targets(game_state,
                                              selected_song['name'],
                                              selected_rhythm['name'])
        if not targets:
            return "❌ 没有合法听者"

        target_names = [f"{t.name}[{VOICE_LABELS.get(getattr(t, 'emotion', None), '?')}]"
                        for t in targets]
        target_choice = player.controller.choose(
            "选择听者：",
            target_names,
            context={"situation": "g2_sing_target"},
        )
        target = next((t for t in targets if t.name in target_choice), targets[0])

        ish.regard -= total_cost

        if "Soave" in selected_rhythm['name'] or "温柔" in selected_rhythm['name']:
            self._execute_soave_v06(player, target, ish, game_state)
        elif "Sognando" in selected_rhythm['name'] or "追寻" in selected_rhythm['name']:
            self._execute_sognando_v06(player, target, ish, game_state)
        elif "Placido" in selected_rhythm['name'] or "平静" in selected_rhythm['name']:
            self._execute_placido_v06(player, target, ish, game_state)

        return f"🎵 {selected_song['name']} → {target.name}"

    # ── v0.6 Soave ──────────────────────────────────────────────
    def _execute_soave_v06(self, g2_player, target, ish, game_state):
        """Soave: 聚光灯 + 摸牌 + 声部特效。"""
        target.stage_statuses = getattr(target, 'stage_statuses', set())
        target.stage_statuses.add("spotlight")
        target._spotlight_granted_r4 = ish.r4_count

        # 摸 1 张牌
        is_real = not getattr(target, 'is_chorus', False)
        if ish.deck:
            if is_real:
                card = ish.deck._draw_one()
                if card:
                    hand = ish.deck.hands.setdefault(target.player_id, [])
                    if len(hand) < 3:
                        hand.append(card)
            else:
                ish.deck.chorus_draw(target.player_id)

        # 真实玩家：可额外打 1 牌
        if is_real:
            target._card_extra_play = True

        voice = getattr(target, 'emotion', None)
        # Acc: +0.5 临时 ATK
        if voice == ACCAREZZEVOLE:
            target.temp_atk_g2 = getattr(target, 'temp_atk_g2', 0) + 0.5
            prompt_manager.show("g2reset", "song.soave_acc",
                               target_name=target.name)
        # Ind: 可免费换牌 1 次（下一轮换牌不消耗次数）
        elif voice == INDIFFERENZA:
            if is_real and ish.deck:
                ish.deck.traded_this_round.discard(target.player_id)
            prompt_manager.show("g2reset", "song.soave_ind",
                               target_name=target.name)
        # Str: 手牌保持公开（已是公开，标记提醒）
        elif voice == STRAPPANDO:
            prompt_manager.show("g2reset", "song.soave_str",
                               target_name=target.name)

        # 额外行动回合
        g2_player._g2_spotlight_target_id = target.player_id

    # ── v0.6 Sognando ───────────────────────────────────────────
    def _execute_sognando_v06(self, g2_player, target, ish, game_state):
        """Sognando: 聚光灯 + 摸 2 弃至上限 + Chorus 指挥。"""
        target.stage_statuses = getattr(target, 'stage_statuses', set())
        target.stage_statuses.add("spotlight")
        target._spotlight_granted_r4 = ish.r4_count

        is_real = not getattr(target, 'is_chorus', False)
        if ish.deck:
            if is_real:
                for _ in range(2):
                    card = ish.deck._draw_one()
                    if card:
                        hand = ish.deck.hands.setdefault(target.player_id, [])
                        hand.append(card)
                # 弃至 3 上限
                hand = ish.deck.hands.get(target.player_id, [])
                while len(hand) > 3:
                    # AI/人类选弃哪张——占位：随机弃最后一张
                    discarded = hand.pop()
                    ish.deck.discard_pile.append(discarded)
            else:
                ish.deck.chorus_draw(target.player_id)

        if is_real:
            target._card_extra_play = True

        # Chorus：G2 可指定其下一次行动目标
        if not is_real:
            targets = ish.get_legal_sing_targets(game_state, "", "")
            legal = [t for t in targets
                     if getattr(t, 'player_id', None) != target.player_id]
            if legal:
                tnames = [t.name for t in legal]
                chosen_name = g2_player.controller.choose(
                    "指定 Chorus 攻击目标：",
                    tnames,
                    context={"situation": "g2_command_chorus"},
                )
                chosen = next((t for t in legal if t.name == chosen_name), None)
                if chosen:
                    target._g2_commanded_target_id = chosen.player_id

        g2_player._g2_spotlight_target_id = target.player_id
        prompt_manager.show("g2reset", "song.sognando", target_name=target.name)

    # ── v0.6 Placido ────────────────────────────────────────────
    def _execute_placido_v06(self, g2_player, target, ish, game_state):
        """Placido: 临时 HP + 牌替换。"""
        target.temp_hp_g2 = getattr(target, 'temp_hp_g2', 0) + 0.5
        is_chorus = getattr(target, 'is_chorus', False)
        if is_chorus:
            target.temp_hp_g2 += 0.5

        # G2 可选目标 1 张牌放牌堆底，目标摸 1 张
        if ish.deck:
            if is_chorus:
                card = ish.deck.chorus_slots.get(target.player_id)
                if card:
                    ish.deck.chorus_slots[target.player_id] = None
                    ish.deck.draw_pile.insert(0, card)
                    ish.deck.chorus_draw(target.player_id)
            else:
                hand = ish.deck.hands.get(target.player_id, [])
                if hand:
                    # 占位：AI 选牌，这里简单取第一张
                    card = hand.pop(0)
                    ish.deck.draw_pile.insert(0, card)
                    new_card = ish.deck._draw_one()
                    if new_card:
                        hand.append(new_card)

        prompt_manager.show("g2reset", "song.placido",
                           target_name=target.name)

    # ── v0.6 Zeffiroso ──────────────────────────────────────────
    def _execute_zeffiroso(self, g2_player, ish, game_state, total_cost):
        """Zeffiroso: 选两名观众，换牌 + Chorus 复活。"""
        targets = ish.get_legal_sing_targets(game_state, "拼接遗憾", "遗憾")
        if len(targets) < 2:
            return "❌ 需要至少两名观众"

        # 选第一名
        tnames1 = [f"{t.name}[{VOICE_LABELS.get(getattr(t, 'emotion', None), '?')}]"
                   for t in targets]
        c1 = g2_player.controller.choose(
            "选择第一名观众：", tnames1,
            context={"situation": "g2_sing_target"},
        )
        t1 = next((t for t in targets if t.name in c1), targets[0])

        # 选第二名
        remaining = [t for t in targets if t.player_id != t1.player_id]
        tnames2 = [f"{t.name}[{VOICE_LABELS.get(getattr(t, 'emotion', None), '?')}]"
                   for t in remaining]
        c2 = g2_player.controller.choose(
            "选择第二名观众：", tnames2,
            context={"situation": "g2_sing_target"},
        )
        t2 = next((t for t in remaining if t.name in c2), remaining[0] if remaining else t1)

        ish.regard -= total_cost

        # 交换牌
        if ish.deck:
            self._swap_cards(ish.deck, t1, t2)

        # 若至少一名是 Chorus：Regard +0.5
        if getattr(t1, 'is_chorus', False) or getattr(t2, 'is_chorus', False):
            ish.regard = min(ish.regard + 0.5, ish.regard_cap)

        # 若一名 Chorus 已死亡：复活
        dead_chorus = [c for c in ish.chorus_list if not c.is_alive()]
        if dead_chorus:
            c = dead_chorus[0]
            c.hp = 1.0
            c.emotion = self._minority_voice(ish, game_state)
            c.location = random.choice(sorted(ish.SEATS))
            prompt_manager.show("g2reset", "song.zeffiroso_revive",
                               chorus_name=c.name, voice=VOICE_LABELS.get(c.emotion, '?'))

        prompt_manager.show("g2reset", "song.zeffiroso",
                           t1_name=t1.name, t2_name=t2.name)
        return f"🎵 拼接遗憾·Zeffiroso → {t1.name} ↔ {t2.name}"

    @staticmethod
    def _swap_cards(deck, u1, u2):
        """交换两个单位的各 1 张牌。"""
        is_c1 = getattr(u1, 'is_chorus', False)
        is_c2 = getattr(u2, 'is_chorus', False)
        card1 = deck.chorus_slots.get(u1.player_id) if is_c1 else (
            deck.hands.get(u1.player_id, [None])[0] if deck.hands.get(u1.player_id) else None)
        card2 = deck.chorus_slots.get(u2.player_id) if is_c2 else (
            deck.hands.get(u2.player_id, [None])[0] if deck.hands.get(u2.player_id) else None)

        if card1 and card2:
            if is_c1:
                deck.chorus_slots[u1.player_id] = card2
            else:
                hand = deck.hands[u1.player_id]
                if card1 in hand:
                    hand[hand.index(card1)] = card2
            if is_c2:
                deck.chorus_slots[u2.player_id] = card1
            else:
                hand = deck.hands[u2.player_id]
                if card2 in hand:
                    hand[hand.index(card2)] = card1

    @staticmethod
    def _minority_voice(ish, game_state) -> str:
        """返回当前数量最少的声部。"""
        counts = {ACCAREZZEVOLE: 0, INDIFFERENZA: 0, STRAPPANDO: 0}
        for pid in ish.participants:
            p = game_state.get_player(pid)
            if p and p.is_alive():
                v = getattr(p, 'emotion', None)
                if v in counts:
                    counts[v] += 1
        for c in ish.chorus_list:
            if c.is_alive() and c.emotion in counts:
                counts[c.emotion] += 1
        return min(counts, key=counts.get)

    # ── v0.6 Before light ───────────────────────────────────────
    def _execute_before_light(self, g2_player, ish, rhythm, cost):
        ish.regard -= cost
        if "Riposato" in rhythm['name'] or "休息" in rhythm['name']:
            ish.before_light = "riposato"
            prompt_manager.show("g2reset", "song.riposato_v06")
        elif "Dolente" in rhythm['name'] or "悲伤" in rhythm['name']:
            ish.before_light = "dolente"
            prompt_manager.show("g2reset", "song.dolente_v06")
