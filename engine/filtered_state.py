"""致盲状态下的游戏状态过滤代理"""

import copy


class FrozenPlayer:
    """快照玩家：所有属性冻结在闪光弹命中时刻"""

    def __init__(self, player):
        self.player_id = player.player_id
        self.name = player.name
        self.hp = player.hp
        self.max_hp = getattr(player, 'max_hp', 10)
        self.location = player.location
        self.is_awake = player.is_awake
        self._was_alive = player.is_alive()
        self.is_stunned = player.is_stunned
        self.is_shocked = getattr(player, 'is_shocked', False)
        self.is_invisible = getattr(player, 'is_invisible', False)
        self.money = getattr(player, 'money', 0)
        self.kill_count = getattr(player, 'kill_count', 0)
        self.has_military_pass = getattr(player, 'has_military_pass', False)
        self.has_detection = getattr(player, 'has_detection', False)
        self.vouchers = getattr(player, 'vouchers', 0)
        self.talent_name = getattr(player, 'talent_name', None)
        # 冻结天赋状态字符串，不保留实时引用
        if player.talent and hasattr(player.talent, 'describe_status'):
            self._frozen_talent_status = player.talent.describe_status()
        else:
            self._frozen_talent_status = ""
        try:
            self.weapons = copy.deepcopy(player.weapons)
        except Exception:
            self.weapons = list(player.weapons) if player.weapons else []
        try:
            self.armor = copy.deepcopy(player.armor)
        except Exception:
            self.armor = list(player.armor) if player.armor else []
        try:
            self.items = copy.deepcopy(getattr(player, 'items', []))
        except Exception:
            self.items = []

    def is_alive(self):
        return self._was_alive

    def is_on_map(self):
        return self.is_awake and self._was_alive

    def describe_status(self):
        """复制 Player.describe_status() 的输出格式，使用冻结数据"""
        lines = []
        lines.append(f"  玩家：{self.name} ({self.player_id})")
        if self.talent_name:
            lines.append(f"  天赋：{self.talent_name}" +
                         (f" ({self._frozen_talent_status})"
                          if self._frozen_talent_status else ""))
        if not self.is_awake:
            lines.append("  状态：💤 睡眠中")
            return "\n".join(lines)
        lines.append(f"  位置：{self.location}")
        lines.append(f"  HP：{self.hp}/{self.max_hp}")
        lines.append(f"  购买凭证：{self.vouchers}")
        weapon_str = ", ".join(str(w) for w in self.weapons)
        lines.append(f"  武器：{weapon_str}")
        armor_desc = self.armor.describe() if hasattr(self.armor, 'describe') else "无"
        lines.append(f"  护甲：{armor_desc}")
        item_str = ", ".join(str(i) for i in self.items) if self.items else "无"
        lines.append(f"  物品：{item_str}")
        return "\n".join(lines)


class FrozenMarkers:
    """快照标记：冻结闪光弹命中时刻的标记状态"""

    def __init__(self, real_markers, blinded_pid, snapshot_pids):
        self._real = real_markers
        self._blinded_pid = blinded_pid
        # 为快照中的每个玩家冻结标记
        self._frozen_simple = {}
        self._frozen_relations = {}
        for pid in snapshot_pids:
            self._frozen_simple[pid] = real_markers.get_all_simple(pid)
            self._frozen_relations[pid] = {
                "LOCKED_BY": set(real_markers.get_related(pid, "LOCKED_BY")),
                "ENGAGED_WITH": set(real_markers.get_related(pid, "ENGAGED_WITH")),
                "DETECTED_BY": set(real_markers.get_related(pid, "DETECTED_BY")),
            }

    def describe_markers(self, player_id):
        """对自己返回实时标记，对其他玩家返回冻结标记"""
        if player_id == self._blinded_pid:
            return self._real.describe_markers(player_id)
        # 使用冻结数据构建描述
        simple = self._frozen_simple.get(player_id, set())
        relations = self._frozen_relations.get(player_id, {})
        parts = []
        display_map = {
            "INVISIBLE": "🫥隐身",
            "INVISIBLE_SUPPRESSED": "🫥隐身(压制中)",
            "STUNNED": "💫眩晕",
            "SHOCKED": "⚡震荡",
            "PETRIFIED": "🗿石化",
            "MISSILE_CTRL": "🚀导弹控制权",
            "POLICE_PROTECT": "🛡️警察保护",
        }
        for m in sorted(simple):
            if m == "SLEEPING":
                continue
            parts.append(display_map.get(m, m))
        for lid in relations.get("LOCKED_BY", set()):
            parts.append(f"🎯被{lid}锁定")
        for eid in relations.get("ENGAGED_WITH", set()):
            parts.append(f"👊与{eid}面对面")
        return " ".join(parts) if parts else "无异常"

    def __getattr__(self, name):
        return getattr(self._real, name)


def create_snapshot(game_state, blinded_player_id):
    """为被致盲的玩家创建所有其他玩家的快照"""
    snapshot = {}
    for pid in game_state.player_order:
        if pid == blinded_player_id:
            continue
        p = game_state.get_player(pid)
        if p:
            snapshot[pid] = FrozenPlayer(p)
    return snapshot


class FilteredGameState:
    """
    致盲状态下的游戏状态代理。
    get_player() 对其他玩家返回快照，对自己返回实时数据。
    markers 对其他玩家返回冻结标记。
    其他属性透传真实 state。
    """

    def __init__(self, real_state, blinded_player_id):
        self._real = real_state
        self._blinded_pid = blinded_player_id
        self._snapshot = getattr(
            real_state.get_player(blinded_player_id),
            '_hoshino_blind_snapshot', {}
        )
        self._markers = FrozenMarkers(
            real_state.markers, blinded_player_id, self._snapshot.keys()
        )

    @property
    def markers(self):
        return self._markers

    def get_player(self, player_id):
        if player_id == self._blinded_pid:
            return self._real.get_player(player_id)
        return self._snapshot.get(player_id, None)

    def players_at_location(self, location):
        result = []
        for pid in self._real.player_order:
            p = self.get_player(pid)
            if p and p.is_alive() and getattr(p, 'location', None) == location:
                result.append(p)
        return result

    def __getattr__(self, name):
        return getattr(self._real, name)