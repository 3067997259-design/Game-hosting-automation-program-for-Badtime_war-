"""  
警察执法引擎（ver1.9重构）。  
管理：举报流程、执法攻击、追踪、队长系统、威信机制。  
"""  
from combat.damage_resolver import resolve_damage 
from combat.damage_resolver import quantize_damage  
from models.equipment import make_weapon, make_armor, WeaponRange, ArmorLayer  
from utils.attribute import Attribute, is_effective  
  
  
class PoliceEngine:  
    """警察执法引擎，操作 game_state.police 数据（ver1.9重构）"""  
  
    def __init__(self, game_state):  
        self.state = game_state  
        self.police = game_state.police  
  
        # 允许攻击警察的AOE手段  
        self.ALLOWED_AOE = {"地震", "地动山摇", "电磁步枪", "天星"}  
  
        # 警察允许装备的白名单  
        self.POLICE_ALLOWED_WEAPONS = {  
            "警棍", "高斯步枪", "地震", "地动山摇"  
        }  
        self.POLICE_ALLOWED_ARMOR = {  
            "盾牌", "陶瓷护甲", "魔法护盾", "AT力场"  
        }  
  
    # ============================================  
    #  犯罪检测  
    # ============================================  
  
    def check_and_record_crime(self, player_id, crime_type):  
        """  
        检查并记录犯罪行为。  
        返回：是否构成犯罪（bool）  
        """  
        if self.police.permanently_disabled:  
            return False  
  
        player = self.state.get_player(player_id)  
        if not player:  
            return False  
  
        # 警察（非队长）不能犯罪  
        if player.is_police and not player.is_captain:  
            return False  
  
        self.police.add_crime(player_id, crime_type)  
        player.is_criminal = True  
  
        # 举报者犯法 → 立刻失去警察保护  
        if self.police.reporter_id == player_id:  
            player.has_police_protection = False  
            self.state.markers.remove(player_id, "POLICE_PROTECT")  
  
        # 队长犯罪扣威信
        if player.is_captain:
            self.police.authority -= 1
            if self.police.authority <= 0:
                zero_msg = self._on_authority_zero()
                # [Issue 9] 记录威信归零详细信息到事件系统
                self.state.log_event("authority_zero_detail", message=zero_msg)  
  
        self.state.log_event("crime", player=player_id, crime_type=crime_type)  
        return True  
  
    # ============================================  
    #  举报流程  
    # ============================================  
  
    def can_report(self, reporter_id, target_id):  
        """检查举报合法性。返回 (bool, str原因)"""  
        if self.police.permanently_disabled:  
            return False, "警察系统已永久关闭"  
  
        reporter = self.state.get_player(reporter_id)  
        target = self.state.get_player(target_id)  
  
        if not reporter or not target:  
            return False, "玩家不存在"  
  
        if self.police.has_captain():  
            return False, "警队已有队长，不再受理举报（邮箱直通垃圾桶）"  
  
        if self.police.is_criminal(reporter_id):  
            return False, "你有犯罪记录，不能举报"  
  
        if not self.police.is_criminal(target_id):  
            return False, "目标没有犯罪记录"  
  
        if self.police.report_phase != "idle":  
            return False, "当前已有举报在处理中"  
  
        # 朝阳好市民可远程举报  
        can_remote = False  
        if reporter.talent and hasattr(reporter.talent, 'can_remote_report'):  
            can_remote = reporter.talent.can_remote_report()  
  
        if not can_remote and reporter.location != "警察局":  
            return False, "需要在警察局才能举报"  
  
        return True, ""  
  
    def do_report(self, reporter_id, target_id):  
        """执行举报"""  
        ok, reason = self.can_report(reporter_id, target_id)  
        if not ok:  
            return f"❌ {reason}"  
  
        target = self.state.get_player(target_id)  
        self.police.report_phase = "reported"  
        self.police.reporter_id = reporter_id  
        self.police.reported_target_id = target_id  
  
        self.state.log_event("report", reporter=reporter_id, target=target_id)  
        return f"📋 举报成功！目标：{target.name}。请在下一回合执行「集结」。"  
  
    # ============================================  
    #  集结  
    # ============================================  
  
    def can_assemble(self, player_id):  
        if self.police.permanently_disabled:  
            return False, "警察系统已永久关闭"  
        if self.police.report_phase != "reported":  
            return False, "当前没有待集结的举报"  
        if self.police.reporter_id != player_id:  
            return False, "只有举报者可以执行集结"  
        return True, ""  
  
    def do_assemble(self, player_id):  
        """执行集结：警察单位出现在警察局"""  
        ok, reason = self.can_assemble(player_id)  
        if not ok:  
            return f"❌ {reason}"  
  
        reporter = self.state.get_player(player_id)  
  
        # 警察单位出现在警察局  
        unit = self.police.units[0] if self.police.units else None  
        if unit and unit.is_alive():  
            unit.location = "警察局"  
  
        # 举报者获得警察保护  
        reporter.has_police_protection = True  
        self.state.markers.add(player_id, "POLICE_PROTECT")  
  
        self.police.report_phase = "assembled"  
        self.state.log_event("assemble", reporter=player_id)  
        return f"🚔 警察集结完成！警察单位已出现在警察局。{reporter.name} 获得警察保护。"  
  
    # ============================================  
    #  追踪指引  
    # ============================================  

    # [FIX] 重写 can_track_guide：不再依赖不存在的 is_tracking 属性
    # 追踪指引的前提：有存活的警察单位不在目标所在地点
    def can_track_guide(self, player_id):  
        """检查是否可以执行追踪指引"""  
        if self.police.permanently_disabled:  
            return False, "警察系统已永久关闭"  
        if self.police.reporter_id != player_id:  
            return False, "只有举报者可以执行追踪指引"  
        if self.police.report_phase not in ("assembled", "dispatched"):
            return False, "警察尚未集结完成"

        target_id = self.police.reported_target_id
        target = self.state.get_player(target_id)
        if not target or not target.is_alive():
            return False, "执法目标已不存在"

        # 检查是否有存活的警察单位不在目标位置（即需要追踪的单位）
        units_needing_track = [
            u for u in self.police.alive_units()
            if u.is_on_map() and u.location != target.location
        ]
        if not units_needing_track:  
            return False, "所有警察单位已在目标位置，无需追踪指引"  
        return True, ""  

    # [FIX] 重写 do_track_guide：不再使用 is_tracking / tracking_countdown / can_attack_this_round
    # 方式A：举报者花1回合指引 → 警察立刻到达目标位置，本轮可攻击
    def do_track_guide(self, player_id):  
        """举报者花1回合指引追踪 → 警察立刻到达目标位置"""  
        ok, reason = self.can_track_guide(player_id)  
        if not ok:  
            return f"❌ {reason}"  
  
        target_id = self.police.reported_target_id  
        target = self.state.get_player(target_id)  
        if not target or not target.is_alive():  
            return "❌ 执法目标已不存在"  
  
        # [FIX] 所有不在目标位置的存活警察单位立刻到达
        guided_units = []
        for unit in self.police.alive_units():  
            if unit.is_on_map() and unit.location != target.location:
                unit.location = target.location  
                guided_units.append(unit.unit_id)

        if self.police.report_phase == "assembled":
            self.police.report_phase = "dispatched"
  
        self.state.log_event("track_guide", reporter=player_id, target=target_id)  
        guided_str = "、".join(guided_units)
        return f"🔍 追踪指引成功！{guided_str} 已到达 {target.name} 所在地点 {target.location}，本轮可执行攻击。"

    def _dispatch_police(self):  
        """警察出动：移动到目标位置"""  
        target_id = self.police.reported_target_id  
        target = self.state.get_player(target_id)  
        if not target or not target.is_alive():  
            self.police.report_phase = "idle"  
            return "🚔 执法目标已不存在，警察撤回。"  
  
        target_loc = target.location  
  
        # 移动所有存活警察单位到目标位置  
        for unit in self.police.alive_units():  
            unit.location = target_loc  
  
        self.police.report_phase = "dispatched"  
        self.state.log_event("police_dispatch", target=target_id, location=target_loc)  
        return f"🚔 警察出动！已抵达{target_loc}，与{target.name}面对面！"

    # ============================================  
    #  执法攻击（轮末结算）  
    # ============================================  

    # [FIX] 删除 _get_enforcement_target：不再依赖不存在的 unit.enforcement_target
    # 直接使用 self.police.reported_target_id（队长通过 captain_designate_target 已设置）

    # [FIX] 删除旧的 _enforcement_attack 方法（从未被调用，且内部引用不存在的属性）
    # 其功能已完全由 process_end_of_round 的阶段3实现

    def _resolve_police_attack_on_player(self, weapon, target):  
        """  
        警察对玩家的攻击结算。  
        使用通用 resolve_damage（玩家有完整的 ArmorSlots）。  
        """  
        from combat.damage_resolver import resolve_damage  
        result = resolve_damage(  
            attacker=None,  
            target=target,  
            weapon=weapon,  
            game_state=self.state,  
        )  
        details = "; ".join(result.get("details", []))  
        if result.get("killed"):  
            return f"击杀！{details}"  
        elif result.get("stunned"):  
            return f"眩晕！{details}"  
        else:  
            return f"HP {result.get('target_hp', '?')} {details}"  
  
    # ============================================  
    #  攻击警察（玩家 → 警察）  
    # ============================================  
  
    def attack_police(self, attacker_id, police_target, attack_method):  
        """  
        玩家攻击警察单位。  
        只有AOE武器可以攻击警察。攻击警察视为犯法。  
        """  
        if self.police.permanently_disabled:  
            return "❌ 警察系统已永久关闭"  
  
        attacker = self.state.get_player(attacker_id)  
        if not attacker:  
            return "❌ 攻击者不存在"  
  
        # 验证AOE武器  
        if attack_method not in self.ALLOWED_AOE:  
            return f"❌ 只能用AOE手段攻击警察（允许：{', '.join(self.ALLOWED_AOE)}）"  
  
        weapon = make_weapon(attack_method)  
        if not weapon:  
            return f"❌ 找不到武器「{attack_method}」"  
  
        if weapon.weapon_range != WeaponRange.AREA:  
            return "❌ 只能用范围武器攻击警察"  
  
        # 找到同地点的警察单位  
        units_at_loc = self.police.units_at(attacker.location)  
        if not units_at_loc:  
            return "❌ 当前地点没有警察单位"  
  
        messages = []  
        for unit in units_at_loc:  
            if not unit.is_alive():  
                continue  
            result = self._resolve_attack_on_police(weapon, unit)  
            messages.append(f"  → {unit.unit_id}: {result}")  
  
            unit.last_attacker_id = attacker_id  
  
        # 攻击警察视为犯法  
        self.check_and_record_crime(attacker_id, "攻击警察")  
  
        # 检查是否全灭  
        self.police.check_all_dead()  
  
        return f"⚔️ {attacker.name} 用「{attack_method}」攻击警察！\n" + "\n".join(messages)  
  
    def _resolve_attack_on_police(self, weapon, unit, raw_damage_override=None, ignore_counter=False):
        """
        对警察单位的伤害结算（自定义，不走 resolve_damage）。
        警察护甲模型：最多1外层 + 1内层，简化结算。
        
        参数：
          weapon: 武器对象，为None时使用raw_damage_override作为原始伤害（weaponless模式）
          unit: 警察单位
          raw_damage_override: 当weapon为None时使用的原始伤害值（默认1.0）
          ignore_counter: 为True时跳过属性克制检查（天星无视属性克制）
        """
        if weapon is not None:
            raw_damage = weapon.get_effective_damage()
        else:
            raw_damage = raw_damage_override if raw_damage_override is not None else 1.0
        final_damage = quantize_damage(raw_damage)
        remaining = final_damage

        # 外层护甲优先
        if unit.outer_armor and not unit.outer_armor.is_broken:
            armor = unit.outer_armor
            # 属性克制检查（ignore_counter时跳过）
            if not ignore_counter and weapon is not None and not is_effective(weapon.attribute, armor.attribute):
                return f"武器「{weapon.attribute.value}」被护甲「{armor.name}({armor.attribute.value})」克制，无效！"
            # 扣减护甲
            if remaining >= armor.current_hp:
                remaining -= armor.current_hp
                armor.current_hp = 0
                armor.is_broken = True
                # 外层破碎后继续检查内层
            else:
                armor.current_hp -= remaining
                return f"护甲「{armor.name}」剩余 {armor.current_hp}/{armor.max_hp}"

        # 内层护甲
        if remaining > 0 and unit.inner_armor and not unit.inner_armor.is_broken:
            armor = unit.inner_armor
            if not ignore_counter and weapon is not None and not is_effective(weapon.attribute, armor.attribute):
                # [Issue 2 FIX] 内层克制时，剩余伤害无效（与外层行为一致）
                return f"武器「{weapon.attribute.value}」被内层护甲「{armor.name}({armor.attribute.value})」克制，剩余伤害无效！"
            elif remaining >= armor.current_hp:
                # 最后内层吸收溢出（除非无视克制）
                armor.current_hp = 0
                armor.is_broken = True
                remaining = 0  # 最后内层吸收
            else:
                armor.current_hp -= remaining
                return f"内层护甲「{armor.name}」剩余 {armor.current_hp}/{armor.max_hp}"

        # 扣减HP
        if remaining > 0:
            unit.hp = max(0, unit.hp - remaining)

        # [Issue 6] 电磁步枪震荡效果（weaponless模式跳过）
        if weapon is not None and weapon.special_tags and "stun_on_hit" in weapon.special_tags:
            if unit.is_alive() and unit.hp > 0:
                already_cc = unit.is_stunned or getattr(unit, 'is_shocked', False)
                if not already_cc:
                    unit.is_shocked = True
                    return f"⚡ {unit.unit_id} 进入震荡状态！HP: {unit.hp}"

        if unit.hp <= 0:
            return f"💀 {unit.unit_id} 被击杀！"
        elif unit.hp <= 0.5 and not unit.is_stunned:
            unit.is_stunned = True
            return f"💫 {unit.unit_id} 进入眩晕！HP: {unit.hp}"
        else:
            return f"HP: {unit.hp}"  
  
    # ============================================  
    #  警察保护  
    # ============================================  
  
    def is_protected_by_police(self, player_id):  
        """  
        检查玩家是否受警察保护。  
        条件：受保护者与未处于debuff的警察单位在同一location。  
        """  
        if self.police.permanently_disabled:  
            return False  
  
        player = self.state.get_player(player_id)  
        if not player:  
            return False  
  
        # 检查是否在幻想乡结界内 → 无视警察保护  
        if hasattr(self.state, 'active_barrier') and self.state.active_barrier:  
            barrier = self.state.active_barrier  
            if hasattr(barrier, 'is_in_barrier') and barrier.is_in_barrier(player_id):  
                return False  
  
        # 确定受保护者  
        if self.police.has_captain():  
            # 有队长时：只有队长受保护  
            if self.police.captain_id != player_id:  
                return False  
            if not player.is_captain:  
                return False  
        else:  
            # 无队长时：只有举报者受保护  
            if not player.has_police_protection:  
                return False  
            if self.police.reporter_id != player_id:  
                return False  
  
        # 检查同地点是否有未处于debuff的警察单位  
        active_at_loc = self.police.active_units_at(player.location)  
        return len(active_at_loc) > 0  
  
    # ============================================  
    #  唤醒警察  
    # ============================================  
  
    def wake_police(self, player_id, police_id):  
        """  
        玩家花1回合唤醒debuff中的警察。  
        条件：玩家与警察在同一地点，警察处于四种debuff之一。  
        """  
        if self.police.permanently_disabled:  
            return "❌ 警察系统已永久关闭"  
  
        player = self.state.get_player(player_id)  
        if not player:  
            return "❌ 玩家不存在"  
  
        unit = self.police.get_unit(police_id)  
        if not unit:  
            return f"❌ 找不到警察单位 {police_id}"  
  
        if not unit.is_alive():  
            return f"❌ {police_id} 已被击杀，无法唤醒"  
  
        if not unit.is_disabled():  
            return f"❌ {police_id} 没有处于需要唤醒的状态"  
  
        if unit.location != player.location:  
            return f"❌ 你与 {police_id} 不在同一地点"  
  
        # 检查沉沦+全息影像限制  
        if unit.is_submerged:  
            is_in_hologram = self._is_in_hologram_range(unit.location)  
            if is_in_hologram:  
                return f"❌ {police_id} 处于沉沦状态且在全息影像范围内，无法被唤醒"  
  
        # 执行唤醒  
        result = unit.wake_up()  
  
        if result.get("killed_by_petrify"):  
            self.police.check_all_dead()  
            return f"🚔 唤醒 {police_id} 时，石化解除造成0.5伤害，{police_id} 被击杀！"  
  
        msg = f"🚔 {police_id} 被唤醒！"  
        if result.get("was_petrified"):  
            msg += f" 石化解除扣0.5HP → HP: {result['new_hp']}"  
        else:  
            msg += f" HP恢复至 {result['new_hp']}"  
        return msg  
  
    def _is_in_hologram_range(self, location):  
        """检查某地点是否在全息影像范围内"""  
        for pid in self.state.player_order:  
            p = self.state.get_player(pid)  
            if p and p.talent and hasattr(p.talent, 'is_in_hologram'):  
                # 全息影像的location就是影像位置  
                if hasattr(p.talent, 'location') and hasattr(p.talent, 'active'):  
                    if p.talent.active and p.talent.location == location:  
                        return True  
        return False  
  
    # ============================================  
    #  加入警察  
    # ============================================  
  
    def can_recruit(self, player_id):  
        if self.police.permanently_disabled:  
            return False, "警察系统已永久关闭"  
        player = self.state.get_player(player_id)  
        if not player:  
            return False, "玩家不存在"  
        if player.is_police:  
            return False, "你已经是警察了"  
        if self.police.is_criminal(player_id):  
            return False, "你有犯罪记录，不能加入警察"  
        if player.location != "警察局":  
            return False, "需要在警察局才能加入"  
        return True, ""  
  
    def do_recruit(self, player_id):  
        """加入警察，三选二获得奖励"""  
        ok, reason = self.can_recruit(player_id)  
        if not ok:  
            return f"❌ {reason}", []  
  
        player = self.state.get_player(player_id)  
        player.is_police = True  
        self.state.log_event("recruit", player=player_id)  
  
        rewards = ["购买凭证", "警棍", "盾牌"]  
        return f"🚔 {player.name} 加入警察！请三选二：{', '.join(rewards)}", rewards  
  
    # ============================================  
    #  竞选队长  
    # ============================================  
  
    def can_election(self, player_id):  
        if self.police.permanently_disabled:  
            return False, "警察系统已永久关闭"  
        player = self.state.get_player(player_id)  
        if not player:  
            return False, "玩家不存在"  
        if not player.is_police:  
            return False, "只有警察才能竞选队长"  
        if self.police.has_captain():  
            return False, "已有队长"  
        if player.location != "警察局":  
            return False, "需要在警察局竞选"  
        return True, ""  
  
    def do_election(self, player_id):  
        """竞选队长（累计3回合进度）"""  
        ok, reason = self.can_election(player_id)  
        if not ok:  
            return f"❌ {reason}"  
  
        player = self.state.get_player(player_id)  
        progress_key = "captain_election"  
        current = player.progress.get(progress_key, 0) + 1  
        player.progress[progress_key] = current  
  
        required = 3  
        if player.talent and hasattr(player.talent, 'get_election_rounds_reduction'):  
            reduction = player.talent.get_election_rounds_reduction()  
            required = max(1, required - reduction)  
  
        if current >= required:  
            del player.progress[progress_key]  
            self._make_captain(player)  
            return f"👑 {player.name} 成为警队队长！威信：{self.police.authority}"  
        else:  
            return f"🏛️ 竞选进度：{current}/{required}"  
  
    def _make_captain(self, player):  
        """设置队长并生成3个警察单位"""  
        self.police.captain_id = player.player_id  
        self.police.authority = 3  
        player.is_captain = True  
        self.state.markers.add(player.player_id, "IS_CAPTAIN")  
        self._on_captain_elected()  
  
  
  
    # ============================================  
    #  队长指令：指定目标  
    # ============================================  


  
    # ============================================  
    #  队长指令：移动警察  
    # ============================================  
  
    def captain_move_police(self, captain_id, police_id, location):  
        """  
        队长移动警察单位。  
        如果目标地点已有警察 → 交换位置。  
        全息影像激活时可无视一个地点一个警察的限制。  
        """  
        if self.police.captain_id != captain_id:  
            return "❌ 只有队长可以移动警察"  
  
        unit = self.police.get_unit(police_id)  
        if not unit or not unit.is_alive():  
            return f"❌ 找不到存活的警察单位 {police_id}"  
  
        if unit.is_disabled():  
            return f"❌ {police_id} 处于debuff状态，无法移动"  
  
        if unit.location == location:  
            return f"❌ {police_id} 已经在 {location}"  
  
        # 检查目标地点是否已有警察  
        existing_at_target = self.police.units_at(location)  
        if existing_at_target:  
            # 检查全息影像豁免  
            if self._is_in_hologram_range(location):  
                # 允许多个警察在同一地点  
                unit.location = location  
                return f"👑 队长移动 {police_id} 到 {location}（全息影像区域，允许共存）"  
            else:  
                # 交换位置  
                other = existing_at_target[0]  
                old_loc = unit.location  
                unit.location = location  
                other.location = old_loc  
                return f"👑 {police_id} 与 {other.unit_id} 交换位置：{police_id}→{location}，{other.unit_id}→{old_loc}"  
        else:  
            unit.location = location  
            return f"👑 队长移动 {police_id} 到 {location}"  
  
    # ============================================  
    #  队长指令：更换装备  
    # ============================================  
  
    def captain_equip_police(self, captain_id, police_id, equipment_name, equipment_type=None):  
        """  
        队长为警察更换装备。  
        equipment_type: "weapon" 或 "armor"，None则自动判断。  
        """  
        if self.police.captain_id != captain_id:  
            return "❌ 只有队长可以更换装备"  
  
        unit = self.police.get_unit(police_id)  
        if not unit or not unit.is_alive():  
            return f"❌ 找不到存活的警察单位 {police_id}"  
  
        # 尝试作为武器  
        if equipment_type in (None, "weapon"):  
            if equipment_name in self.POLICE_ALLOWED_WEAPONS:  
                unit.weapon_name = equipment_name  
                return f"🚔 {police_id} 更换武器为「{equipment_name}」"  
            elif equipment_type == "weapon":  
                return f"❌ 警察不能装备武器「{equipment_name}」（允许：{', '.join(self.POLICE_ALLOWED_WEAPONS)}）"  
  
        # 尝试作为护甲  
        if equipment_type in (None, "armor"):  
            if equipment_name in self.POLICE_ALLOWED_ARMOR:  
                armor = make_armor(equipment_name)  
                if armor:  
                    unit.equip_armor(armor)  
                    return f"🚔 {police_id} 装备护甲「{equipment_name}」"  
                return f"❌ 无法创建护甲「{equipment_name}」"  
            elif equipment_type == "armor":  
                return f"❌ 警察不能装备护甲「{equipment_name}」（允许：{', '.join(self.POLICE_ALLOWED_ARMOR)}）"  
  
        return f"❌ 「{equipment_name}」不在警察允许的装备列表中"  
  
    # ============================================  
    #  队长指令：命令攻击  
    # ============================================  

  
    # [FIX] captain_attack 不再调用 _resolve_police_attack_on_target（该方法内部已做威信检查），
    # 改为调用 _resolve_police_attack_on_player 以避免双重扣威信。
    # 威信检查统一在 captain_attack 外层做。
    def captain_attack(self, captain_id, police_id, target_id):  
        """队长命令一个警察单位攻击指定目标（实际执行）"""  
        if self.police.captain_id != captain_id:  
            return "❌ 只有队长可以命令攻击"  
  
        unit = self.police.get_unit(police_id)  
        if not unit or not unit.is_alive():  
            return f"❌ 找不到存活的警察单位 {police_id}"  
        if unit.is_disabled():  
            return f"❌ {police_id} 处于行动阻碍状态，无法攻击"  
  
        target = self.state.get_player(target_id)  
        if not target or not target.is_alive():  
            return f"❌ 目标不存在或已死亡"  
  
        # 必须同地点  
        if unit.location != target.location:  
            return f"❌ {police_id} 与 {target.name} 不在同一地点（{police_id}在{unit.location}，目标在{target.location}）"  
  
        # [FIX] 构建武器并直接用 _resolve_police_attack_on_player 结算
        weapon = make_weapon(unit.weapon_name)
        if not weapon:
            weapon = make_weapon("警棍")
        if weapon is None:
            return f"⚠️ {unit.unit_id} 无法创建武器，攻击取消"

        # 高斯步枪强制不蓄力
        if weapon.name == "高斯步枪" and weapon.requires_charge:
            weapon.is_charged = False

        atk_result = self._resolve_police_attack_on_player(weapon, target)
        result = f"🚔 {unit.unit_id} 对 {target.name} 执法攻击（{weapon.name}）→ {atk_result}"

        # [Issue 3] 威信检查：攻击无辜者扣威信
        if not self.police.is_criminal(target_id):
            self.police.authority -= 1
            self.police.last_innocent_attacked = target_id
            result += f"\n  ⚠️ 攻击无辜者！威信-1（当前：{self.police.authority}）"
            if self.police.authority <= 0:
                zero_msg = self._on_authority_zero()
                result += f"\n{zero_msg}"

        self.state.log_event("captain_attack", captain=captain_id,  
                             police=police_id, target=target_id)  
        return result  
  
    # ============================================  
    #  队长指令：指定执法目标  
    # ============================================  
  
    def captain_designate_target(self, captain_id, target_id):  
        """队长指定执法目标（不限违法者）"""  
        if self.police.captain_id != captain_id:  
            return "❌ 只有队长可以指定目标"  
  
        target = self.state.get_player(target_id)  
        if not target or not target.is_alive():  
            return f"❌ 目标不存在或已死亡"  
  
        self.police.reported_target_id = target_id  
        if self.police.report_phase == "idle":
            self.police.report_phase = "dispatched"  # [Issue 12] 队长场景下直接进入执法状态，不触发自动出动

        self.state.log_event("captain_designate", captain=captain_id, target=target_id)
        return f"👑 队长指定执法目标：{target.name}"  
  
    # ============================================  
    #  队长指令：研究性学习（威信恢复）  
    # ============================================  
  
    def can_study(self, captain_id):  
        """检查队长是否可以研究性学习"""  
        if self.police.captain_id != captain_id:  
            return False, "只有队长可以研究性学习"  
        player = self.state.get_player(captain_id)  
        if not player:  
            return False, "玩家不存在"  
        if player.location != "警察局":  
            return False, "必须在警察局才能研究性学习"  
        return True, ""  
  
    def do_study(self, captain_id):  
        """队长在警察局研究性学习，威信+1"""  
        ok, reason = self.can_study(captain_id)  
        if not ok:  
            return f"❌ {reason}"  
  
        self.police.authority += 1  
        self.state.log_event("captain_study", captain=captain_id,  
                             authority=self.police.authority)  
        return f"📚 队长研究性学习完成！威信+1（当前：{self.police.authority}）"  
  
    # ============================================  
    #  队长上任处理  
    # ============================================  
  
    def _on_captain_elected(self):  
        """  
        队长上任处理：  
        1. 清空现有单位列表  
        2. 创建3个独立警察单位，全部在警察局  
        3. 关闭举报系统  
        """  
        from models.police import PoliceUnit  
  
        # 创建3个全新单位（默认装备：警棍+盾牌）  
        self.police.units = []  
        for i in range(1, 4):  
            unit = PoliceUnit(f"police{i}")  
            unit.location = "警察局"  
            self.police.units.append(unit)  
  
        # 关闭举报系统  
        self.police.report_phase = "idle"  
        self.police.reporter_id = None  
        self.police.reported_target_id = None  
  
        self.state.log_event("captain_elected", captain=self.police.captain_id)  
  
    # ============================================  
    #  威信归零处理  
    # ============================================  
  
    def _on_authority_zero(self):  
        """  
        威信归零处理（对应README 10.9）：  
        1. 队长身份解除  
        2. 保留1个单位，重置为默认装备，location=None  
        3. 原队长成为唯一违法者  
        4. 最后被攻击的无辜者成为举报者  
        5. 其他人犯罪记录清空  
        """  
        from models.police import PoliceUnit  
  
        messages = []  
        captain_id = self.police.captain_id  
        captain = self.state.get_player(captain_id)  
  
        # 1. 解除队长身份  
        if captain:  
            captain.is_captain = False  
            self.state.markers.remove(captain_id, "IS_CAPTAIN")  
        messages.append(f"⚠️ 威信归零！{captain.name if captain else captain_id} 队长身份解除！")  
  
        # 2. 重置为1个单位，默认装备，不在地图上  
        self.police.units = [PoliceUnit("police1")]  # __init__中location=None，默认装备  
        self.police.captain_id = None  
        self.police.authority = 0  
  
        # 3. 原队长成为唯一违法者
        self.police.clear_all_crimes_except(captain_id)
        if captain:
            captain.is_criminal = True
        self.police.add_crime(captain_id, "队长失职")

        # [Issue 4] 清除其他玩家的 is_criminal 标志
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and pid != captain_id:
                p.is_criminal = False

        messages.append(f"📋 {captain.name if captain else captain_id} 被记录为唯一违法者，其他人犯罪记录清空。")  
  
        # 4. 最后被攻击的无辜者成为举报者，自动启动执法流程  
        last_innocent = getattr(self.police, 'last_innocent_attacked', None)  
        if last_innocent:  
            innocent_player = self.state.get_player(last_innocent)  
            if innocent_player and innocent_player.is_alive():  
                self.police.reporter_id = last_innocent  
                self.police.reported_target_id = captain_id  
                self.police.report_phase = "assembled"  # 跳过举报和集结，直接进入出动阶段  
                innocent_player.has_police_protection = True  
                self.state.markers.add(last_innocent, "POLICE_PROTECT")  
                messages.append(  
                    f"📢 {innocent_player.name} 自动成为举报者，警察将对 "  
                    f"{captain.name if captain else captain_id} 执法。"  
                )  
            else:  
                messages.append("⏳ 无存活的无辜受害者，警察等待新的举报。")  
        else:  
            messages.append("⏳ 无被攻击的无辜者记录，警察等待新的举报。")  
  
        self.state.log_event("authority_zero", captain=captain_id)  
        return "\n".join(messages)  
  
    # ============================================  
    #  献予律法之诗效果  
    # ============================================  
  
    def process_poem_law_effect(self, target_player_id):  
        """  
        [已废弃] 献予律法之诗的效果现在由 g5_ripple._poem_law() 直接处理。  
        保留此方法仅为向后兼容。  
        """  
        return "⚠️ 请通过 _poem_law() 调用新版逻辑。"

    # ============================================  
    #  辅助方法  
    # ============================================  
  
    def summon_police_unit(self, location):  
        """  
        召唤一个新的默认装备警察单位到指定位置。  
        用于献予律法之诗的朝阳好市民特殊效果。  
        如果警察系统已永久禁用，同时解除禁用。  
        """  
        from models.police import PoliceUnit  
  
        # 生成新ID  
        existing_ids = {u.unit_id for u in self.police.units}  
        new_id = None  
        for i in range(1, 10):  
            candidate = f"police{i}"  
            if candidate not in existing_ids:  
                new_id = candidate  
                break  
        if not new_id:  
            new_id = f"police{len(self.police.units) + 1}"  
  
        # 创建新单位（默认装备：警棍+盾牌）  
        new_unit = PoliceUnit(new_id)  
        new_unit.location = location  
        self.police.units.append(new_unit)  
  
        # 解除永久禁用  
        if self.police.permanently_disabled:  
            self.police.permanently_disabled = False  
  
        self.state.log_event("police_summoned", unit_id=new_id, location=location)  
        return f"🚔 新警察单位 {new_id} 在 {location} 被召唤！（默认装备：警棍+盾牌）"    
  
  
    def _reset_enforcement(self):  
        """重置执法状态，所有存活单位返回警察局"""  
        self.police.report_phase = "idle"  
        self.police.reporter_id = None  
        self.police.reported_target_id = None  
  
        for unit in self.police.units:  
            if unit.is_alive():  
                unit.location = "警察局"  
  
    def _validate_police_equipment(self, name, eq_type):  
        """验证装备是否在白名单中"""  
        if eq_type == "weapon":  
            return name in self.POLICE_ALLOWED_WEAPONS  
        elif eq_type == "armor":  
            return name in self.POLICE_ALLOWED_ARMOR  
        return False  
  
    def get_police_status(self):  
        """获取警察系统状态描述（用于 police status 命令）"""  
        return self.police.describe()  
  
    def is_hologram_active_at(self, location):  
        """  
        检查指定地点是否有活跃的全息影像（用于位置限制豁免）。  
        由天赋系统提供，这里做安全检查。  
        """  
        if not hasattr(self.state, 'active_hologram'):  
            return False  
        hologram = self.state.active_hologram  
        if hologram and hasattr(hologram, 'location') and hasattr(hologram, 'is_active'):  
            return hologram.is_active and hologram.location == location  
        return False  
  
    def is_in_mythland(self, player_id):  
        """  
        检查玩家是否在幻想乡结界内（用于警察保护豁免）。  
        由天赋系统提供，这里做安全检查。  
        """  
        if not hasattr(self.state, 'active_barrier'):  
            return False  
        barrier = self.state.active_barrier  
        if barrier and hasattr(barrier, 'is_in_barrier'):  
            return barrier.is_in_barrier(player_id)  
        return False

    # [FIX] _resolve_police_attack_on_target 保留，但移除内部的威信检查逻辑
    # 威信检查统一由调用方负责（captain_attack 或 process_end_of_round）
    def _resolve_police_attack_on_target(self, unit, target):  
        """  
        单个警察单位对玩家目标执行一次攻击。  
        返回结果消息字符串。  
        注意：此方法不做威信检查，由调用方负责。
        """  
        weapon = make_weapon(unit.weapon_name)  
        if not weapon:  
            weapon = make_weapon("警棍")  
        if weapon is None:  
            return f"⚠️ {unit.unit_id} 无法创建武器，攻击取消"  
  
        # 高斯步枪强制不蓄力  
        if weapon.name == "高斯步枪" and weapon.requires_charge:  
            weapon.is_charged = False  
  
        # 使用 resolve_damage 进行伤害结算  
        result = resolve_damage(  
            attacker=None,      # 警察不是玩家  
            target=target,  
            weapon=weapon,  
            game_state=self.state,  
        )  
  
        # 构建消息  
        msg_parts = [f"🚔 {unit.unit_id} 对 {target.name} 执法攻击（{weapon.name}）"]  
        if result.get("success"):  
            msg_parts.append(f"  → 造成 {result.get('final_damage', 0)} 伤害")  
            if result.get("armor_hit"):  
                msg_parts.append(f"  → 命中护甲：{result['armor_hit']}")  
            if result.get("armor_broken"):  
                msg_parts.append(f"  → 护甲被击破！")  
            if result.get("killed"):  
                msg_parts.append(f"  → 💀 {target.name} 被击杀！")  
            elif result.get("stunned"):  
                msg_parts.append(f"  → 💫 {target.name} 陷入眩晕！")  
        else:  
            reason = result.get("reason", "未知原因")  
            msg_parts.append(f"  → 攻击无效：{reason}")  
  
        # [FIX] 移除此处的威信检查，由调用方统一处理

        return "\n".join(msg_parts)

    # [FIX] 重写 process_end_of_round：
    # - 移除 _just_arrived 临时属性，改用局部 set 追踪本轮刚到达的单位
    # - 阶段3中统一做威信检查
    def process_end_of_round(self):  
        """  
        全局轮次结束时的警察系统结算。  
        按顺序处理：出动 → 追踪到达 → 执法攻击。  
        """  
        if self.police.permanently_disabled:  
            return []  
  
        messages = []  
        # [FIX] 用局部集合代替临时属性 _just_arrived
        just_arrived_ids = set()

        # 阶段1：如果处于"assembled"状态，执行出动  
        if self.police.report_phase == "assembled":  
            dispatch_msg = self._dispatch_police()  
            if dispatch_msg:  
                messages.append(dispatch_msg)  
            # [FIX] 不再重复设置 report_phase，_dispatch_police 内部已设置  
  
        # [Issue 7] 阶段2：处理追踪中的警察（方式B：自动赶到目标位置，本轮不攻击）
        # 仅在无队长时自动追踪，有队长时由队长手动部署
        target_id = self.police.reported_target_id  
        if target_id and not self.police.has_captain():  
            target = self.state.get_player(target_id)  
            if target and target.is_alive():  
                for unit in self.police.alive_units():  
                    if unit.is_on_map() and unit.location != target.location:  
                        # 方式B：自动赶到，标记为刚到达  
                        unit.location = target.location  
                        just_arrived_ids.add(unit.unit_id)
                        messages.append(  
                            f"🚔 {unit.unit_id} 追踪到达 {target.location}（方式B，本轮不攻击）"  
                        )  
  
        # 阶段3：执法攻击（只有已在目标位置且非"刚到达"的单位才攻击）  
        if self.police.report_phase == "dispatched" and target_id:  
            target = self.state.get_player(target_id)  
            if target and target.is_alive():  
                for unit in self.police.active_units():  
                    if unit.location == target.location:  
                        # [FIX] 方式B的单位本轮不攻击，用局部集合判断
                        if unit.unit_id in just_arrived_ids:
                            continue  
                        atk_msg = self._resolve_police_attack_on_target(unit, target)  
                        messages.append(atk_msg)  

                        # [Issue 10] 仅在有队长时才扣威信（威信仅在队长系统中有意义）
                        if self.police.has_captain() and not self.police.is_criminal(target_id):
                            self.police.authority -= 1
                            self.police.last_innocent_attacked = target_id
                            messages.append(f"  ⚠️ 攻击无辜者！威信-1（当前：{self.police.authority}）")
                            if self.police.authority <= 0:
                                zero_msg = self._on_authority_zero()
                                messages.append(zero_msg)
                                break  # 威信归零后停止所有执法

        # 阶段4：检查全灭  
        if self.police.check_all_dead():  
            messages.append("⚠️ 所有警察单位已被消灭！警察局所有交互永久禁用。")  
  
        return messages
