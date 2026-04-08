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
        self.talent = player.talent
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
        if self.talent and hasattr(self.talent, 'describe_status'):
            return self.talent.describe_status()
        return ""


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
    其他属性透传真实 state。
    """

    def __init__(self, real_state, blinded_player_id):
        self._real = real_state
        self._blinded_pid = blinded_player_id
        self._snapshot = getattr(
            real_state.get_player(blinded_player_id),
            '_hoshino_blind_snapshot', {}
        )

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