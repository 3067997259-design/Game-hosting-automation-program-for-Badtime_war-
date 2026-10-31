"""十四诗篇共享接受权、爱愿和永久改造登记，不维护第二份角色资源。"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from engine.ga.extensions import IncomingModifier, ask, high_impact
from engine.ga.state import touch
from engine.ga.talents.base import timer

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.contracts import ActionPlan, EffectSpec, TimedEffect, TurnStage
    from engine.ga.gameplay import ActionOption
    from engine.ga.state import ActionGrant, GaPlayer


POEMS = {
    "G0": ("明天", "打工加值、信用消费减费（手术除外）；下一炮火不烧血，调整呼吸中释放另回HP", "无永久丧失"),
    "G1": ("飞萤", "安全完全燃烧；繁育中则抹除绝死倒计时并恢复健康肉身",
           "繁育救回后永久失去机甲/完全燃烧/超新星，失熵与卸甲负修正一并移除"),
    "G2": ("追光", "清孤独/加白键和一次驻足；炉心中则停止烧血并恢复操控",
           "炉心救回后保持单人，不逆转腰斩、永久加速或变成世末之雨"),
    "G3": ("永恒", "回魔4，下一结界首段免费、连发多一段与指定目标首槽震荡", "无永久丧失"),
    "G4": ("负世", "补火种或毁伤4；救世主时限冻结并附烈阳火伤",
           "救世主每个自身T0强制获得真实灼烧，普通净化不能免除该代价"),
    "G5": ("爱与记忆", "真实有限星光箭，固定加值每总池一次", "不施加爱愿"),
    "G6": ("欢愉", "笑点置满、未来4R4当身升级（我站在了命运的顶点！）及有窗口礼盒群体代拆（我超，盒！）", "架设仍花T0与笑点"),
    "G7": ("守夜人", "色彩永久移除；Terror剩余生命转换并恢复战术能力",
           "永久失去色彩与Terror，实际最多扣2额外生命维修已融合的盾"),
    "T1": ("游侠", "下一斩追近已锁定目标，实际破甲/击杀独立返1SP", "无永久丧失"),
    "T2": ("夜行", "一次有限额外槽、隐匿、重置如影随形及一次免费锁定或偷窃", "不突破每轮再动白名单"),
    "T3": ("群星", "已有石化改尘世锁、套满玉璋、下一天星后两颗流星", "无永久丧失"),
    "T4": ("阴阳", "两层天机，每个起卦目标用一层，从五种非再动结果直接选择", "不钦定或跃在渊"),
    "T6": ("律法", "原编队重建补满最高装备，合法齐刺或满盾墙",
           "不能解除雨/倒流/Terror拒绝执法，不复制编队或普通攻击额度"),
    "T7": ("彼岸", "复活未生效时给满SP装备；已用掉时最多再挂一次", "全局最多复活两次"),
}


class Poems:
    """诗篇的接受事件与爱愿归本局，实际转换由受赠角色显式实现。"""

    def __init__(self) -> None:
        self.love: dict[tuple[str, str], TimedEffect] = {}
        self.accepted: set[tuple[str, str]] = set()

    def register(self, state: GameState) -> None:
        """共享接口对全部十四角色安装，不另开旧献诗版本。"""
        state.ga_poems = self
        state.ga_option_filters.append(self.options)
        state.ga_before_action.append(self.before_action)
        state.ga_incoming_modifiers.append(self.incoming)
        state.ga_hooks.setdefault("R4.expiry", []).append(self.expire)

    def give(self, state: GameState, giver: str, recipient: str, root: str) -> bool:
        """强制回答前展示强化、爱愿和永久失去的能力；不默认接受。"""
        role = state.ga_roles[recipient]
        name, benefit, loss = POEMS[role.talent_id]
        answer = ask(state, giver, recipient, ("accept", "decline"), kind="poem_offer", context={
            "poem": name, "talent": role.talent_id, "benefit": benefit, "permanent_loss": loss,
            "love": "未来4R4不能伤害献诗者，献诗者攻击即破碎" if len(state.players) > 2 else "双人免爱愿",
        })
        if answer != "accept":
            return False
        self.accepted.add((recipient, role.talent_id))
        if len(state.players) > 2 and giver != recipient:
            self.love[recipient, giver] = timer(state, recipient, "poem/love", state.ga_config.get(
                "poems", "love_ticks"))
        high_impact(state, giver, root, recipient)
        state.ga_roles[giver].record_poetry(state)
        role.receive_poem(state, giver)
        touch(state)
        return True

    def can_harm(self, state: GameState, actor: str | None, target: str) -> bool:
        """爱愿是独立契约，净化状态不会清掉它。"""
        return (actor, target) not in self.love

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """范围攻击可避开受保护者，不能通过副目标偷偷施加伤害。"""
        result = []
        for option in options:
            blocked = {target for target in option.request.target_ids if option.hostile and
                       not self.can_harm(state, grant.actor_id, target)}
            if not blocked:
                result.append(option)
                continue
            targets = tuple(t for t in option.request.target_ids if t not in blocked)
            if not targets:
                continue
            effects = tuple(replace(e, target_ids=tuple(t for t in e.target_ids if
                                                       t not in blocked))
                            for e in option.effects if any(t not in blocked for t in e.target_ids))
            result.append(replace(option, request=replace(option.request, target_ids=targets),
                effects=effects, definition=replace(option.definition, minimum_targets=len(targets),
                                                     maximum_targets=len(targets))))
        return tuple(result)

    def before_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """献诗者主动攻击时立即解约，后续段不因旧契约获得保护。"""
        if option.hostile:
            for target in plan.request.target_ids:
                self.love.pop((target, plan.request.actor_id), None)

    def incoming(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> IncomingModifier:
        """独立弹射等后段仍复核契约。"""
        return IncomingModifier(immune=not self.can_harm(state, effect.actor_id, target.player_id))

    def expire(self, state: GameState) -> None:
        """四个未来实际R4，不受世界时钟倒流/加速干扰。"""
        for key, value in tuple(self.love.items()):
            updated = value.on_r4(state.current_round)
            if updated.remaining:
                self.love[key] = updated
            else:
                del self.love[key]
