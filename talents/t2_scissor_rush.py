"""
天赋2：剪刀手一突（原初·融合重做）
融合原初天赋2（你给路打油）、5（不良少年）、8（警觉）

常驻效果：
  - 伤人免罪（攻击造成伤害但未击杀不构成犯罪，击杀仍犯罪）
  - 犯罪再动：每触发一种新犯罪类型 → 获得1额外行动回合
  - 警觉：首次被他人找到 + 首次主动找到他人 → 各获得1额外行动回合
  - 攻击回盾：每2次攻击，第2次若对护甲造成伤害 → 获得被命中护甲50%HP
  - 隐身盘活：击杀数为0时，攻击不解除隐身
响应窗口（继承自你给路打油）：
  - R3期间可声明获得额外行动回合，全局2次，每地点1次
"""

from talents.base_talent import BaseTalent, PromptLevel
from models.equipment import ArmorPiece, ArmorLayer
from utils.attribute import Attribute
from engine.prompt_manager import prompt_manager
from cli import display


class ScissorRush(BaseTalent):
    name = "剪刀手一突"
    description = "综合泛用攻击性天赋，萌新迈向神代天赋的最好引导者！"
    tier = "原初"

    lore = [
        "「海的那边是什么样？我不知道，小时候没上过学，字都不识几个」",
        "「但如果你又是一楼锁小美，然后十枪空十一枪；又是进点不给屏障还往心夏枪口底下冲，甚至点个小绘精灵都不知道给盾」",
        "「我下把就排在你对面把你打成筛子」",
    ]

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)

        # ---- 犯罪再动（继承自不良少年）----
        self.triggered_crime_types = set()

        # ---- 响应窗口（继承自你给路打油）----
        self.response_uses_remaining = 2
        self.response_triggered_locations = set()

        # ---- 警觉 ----
        self.vigilance_uses = 2          # 总共2次
        self.found_triggered = False     # 首次被他人找到
        self.find_triggered = False      # 首次主动找到他人

        # ---- 攻击回盾 ----
        self.attack_count = 0            # 攻击计数器
        self.stealth_on_zero_kills = True  # 隐身盘活标记（供 damage_resolver 检查）



    # ================================================================
    #  被动：伤人免罪 + 犯罪再动
    # ================================================================
    def on_crime_check(self, player_id, crime_type):
        if player_id != self.player_id:
            return None

        if crime_type == "伤害玩家":
            # 检查最近一次攻击是否击杀了目标
            killed = self._last_attack_killed()
            if not killed:
                # 伤人不杀 → 免罪
                return {"immune": True}
            # 击杀 → 犯罪，但检查是否是新犯罪类型
            effective_type = "击杀玩家"
            if effective_type not in self.triggered_crime_types:
                self.triggered_crime_types.add(effective_type)
                return {
                    "extra_turn": True,
                    "message": prompt_manager.get_prompt(
                        "talent", "t2scissorrush.crime_trigger",
                        default="⚔️ 剪刀手一突：首次犯罪「{crime_type}」→ 获得额外行动回合！"
                    ).format(crime_type=effective_type)
                }
            return None  # 已触发过，正常犯罪

        # 非攻击类犯罪（进入军事基地、释放病毒等）
        if crime_type not in self.triggered_crime_types:
            self.triggered_crime_types.add(crime_type)
            return {
                "extra_turn": True,
                "message": prompt_manager.get_prompt(
                    "talent", "t2scissorrush.crime_trigger",
                    default="⚔️ 剪刀手一突：首次犯罪「{crime_type}」→ 获得额外行动回合！"
                ).format(crime_type=crime_type)
            }
        return None

    def _last_attack_killed(self):
        """检查最近一次攻击是否击杀了目标"""
        for event in reversed(self.state.event_log):
            if (event.get("type") == "attack"
                    and event.get("attacker") == self.player_id
                    and event.get("round") == self.state.current_round):
                result = event.get("result", {})
                return result.get("killed", False)
        return False

    # ================================================================
    #  响应窗口（继承自你给路打油）
    # ================================================================
    def check_response_window(self, actor, action_type):
        if self.response_uses_remaining <= 0:
            return False
        me = self.state.get_player(self.player_id)
        if not me or not me.is_alive() or not me.is_on_map():
            return False
        if actor.player_id == self.player_id:
            return False
        if me.location in self.response_triggered_locations:
            return False
        return True

    def execute_response(self, player):
        self.response_uses_remaining -= 1
        self.response_triggered_locations.add(player.location)
        self.state.log_event("scissor_rush_response", player=self.player_id,
                             location=player.location,
                             remaining=self.response_uses_remaining)
        return prompt_manager.get_prompt(
            "talent", "t2scissorrush.response",
            default="⚔️ {player_name} 使用「剪刀手一突·紧急战斗策略」！\n    获得1个额外行动回合！（剩余{remaining}次）"
        ).format(player_name=player.name, remaining=self.response_uses_remaining)

    # ================================================================
    #  警觉：find 钩子
    # ================================================================
    def on_find_someone(self, player, target_id):
        """主动找到他人时触发"""
        if self.find_triggered or self.vigilance_uses <= 0:
            return
        self.find_triggered = True
        self.vigilance_uses -= 1
        player.vigilance_extra_turn = True
        display.show_info(prompt_manager.get_prompt(
            "talent", "t2scissorrush.vigilance_find",
            default="👁️ 剪刀手一突·警觉：{player_name} 首次主动找到他人 → 获得额外行动回合！"
        ).format(player_name=player.name))

    def on_found_by_someone(self, player, finder_id):
        """被他人找到时触发"""
        if self.found_triggered or self.vigilance_uses <= 0:
            return
        self.found_triggered = True
        self.vigilance_uses -= 1
        player.vigilance_extra_turn = True
        display.show_info(prompt_manager.get_prompt(
            "talent", "t2scissorrush.vigilance_found",
            default="👁️ 剪刀手一突·警觉：{player_name} 首次被他人找到 → 获得额外行动回合！"
        ).format(player_name=player.name))

    # ================================================================
    #  攻击回盾
    # ================================================================
    def on_attack_shield_recovery(self, attacker, hit_piece):
        """攻击结算后触发（由 damage_resolver 调用）
        hit_piece: 被命中的护甲 ArmorPiece 对象
        """
        if attacker.player_id != self.player_id:
            return

        self.attack_count += 1

        # 每2次攻击，第2次触发
        if self.attack_count % 2 != 0:
            return

        # 排除铁之荷鲁斯等特殊护甲
        EXCLUDED_ARMORS = {"铁之荷鲁斯"}
        if hit_piece.name in EXCLUDED_ARMORS:
            return

        # 计算回盾量：被命中护甲 max_hp 的 50%，量化
        raw_recovery = hit_piece.max_hp * 0.5
        recovery = self._quantize_shield(raw_recovery)
        if recovery <= 0:
            return

        # 检查自己是否已有同名护甲
        existing = None
        for piece in attacker.armor.get_active(hit_piece.layer):
            if piece.name == hit_piece.name and not piece.is_broken:
                existing = piece
                break
        # 也检查已破碎的同名护甲（可以修复）
        if existing is None:
            all_pieces = getattr(attacker.armor, 'outer', []) + getattr(attacker.armor, 'inner', [])
            for piece in all_pieces:
                if piece.name == hit_piece.name and piece.is_broken:
                    existing = piece
                    break

        if existing:
            # 已有同名护甲 → 回复HP
            if existing.is_broken:
                existing.is_broken = False
                existing.current_hp = recovery
            else:
                existing.current_hp = min(existing.max_hp, existing.current_hp + recovery)
            display.show_info(prompt_manager.get_prompt(
                "talent", "t2scissorrush.shield_recovery_restore",
                default="🛡️ 攻击回盾：{player_name} 的「{armor_name}」恢复 {recovery} HP！（当前 {current}/{max}）"
            ).format(
                player_name=attacker.name,
                armor_name=existing.name,
                recovery=recovery,
                current=existing.current_hp,
                max=existing.max_hp
            ))
        else:
            # 没有同名护甲 → 创建新护甲
            new_piece = ArmorPiece(
                name=hit_piece.name,
                attribute=hit_piece.attribute,
                layer=hit_piece.layer,
                max_hp=recovery,
                priority=hit_piece.priority,
            )
            new_piece.current_hp = recovery
            success, msg = attacker.add_armor(new_piece)
            if success:
                display.show_info(prompt_manager.get_prompt(
                    "talent", "t2scissorrush.shield_recovery",
                    default="🛡️ 攻击回盾：{player_name} 获得「{armor_name}」（{recovery} HP）！"
                ).format(
                    player_name=attacker.name,
                    armor_name=hit_piece.name,
                    recovery=recovery
                ))
            else:
                display.show_info(f"🛡️ 攻击回盾：无法装备「{hit_piece.name}」（{msg}）")

    @staticmethod
    def _quantize_shield(value):
        """量化回盾值：<0.5→0.5, 0.5~1→0.5, 1~1.5→1, 1.5~2→1.5, ..."""
        if value <= 0:
            return 0
        # 向下取整到0.5的倍数，但最低为0.5
        quantized = int(value / 0.5) * 0.5
        if quantized < 0.5:
            quantized = 0.5
        return quantized

    # ================================================================
    #  状态描述
    # ================================================================
    def describe_status(self):
        parts = []
        # 犯罪再动
        triggered = ", ".join(self.triggered_crime_types) if self.triggered_crime_types else "无"
        parts.append(f"已触发犯罪类型：{triggered}")
        # 响应窗口
        locs = ", ".join(self.response_triggered_locations) if self.response_triggered_locations else "无"
        parts.append(f"紧急战斗策略：{self.response_uses_remaining}/2，已触发地点：{locs}")
        # 警觉
        v_parts = []
        if self.found_triggered:
            v_parts.append("被找到✓")
        else:
            v_parts.append("被找到✗")
        if self.find_triggered:
            v_parts.append("找到他人✓")
        else:
            v_parts.append("找到他人✗")
        parts.append(f"警觉：{', '.join(v_parts)}")
        # 攻击回盾
        parts.append(f"攻击回盾：攻击计数={self.attack_count}（偶数次触发）")
        # 隐身盘活
        player = self.state.get_player(self.player_id)
        kills = getattr(player, 'kill_count', 0) if player else 0
        parts.append(f"隐身盘活：击杀数={kills}（0时攻击不解除隐身）")
        # 警察伤害免疫
        if player and getattr(player, '_immune_next_police_damage', False):
            parts.append("🛡️ 免疫下一次警察伤害")
        return " | ".join(parts)