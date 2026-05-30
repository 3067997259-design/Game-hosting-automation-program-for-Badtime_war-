"""
AIRI Bot Bridge
═══════════════
将游戏服务器的 TCP 协议翻译为 AIRI 的 WebSocket 协议。
本脚本作为独立进程运行，不需要修改游戏或 AIRI 的任何源码。

启动方式：
  python bot_bridge.py
  python bot_bridge.py --config config/airi_bridge_config.json
"""

import argparse
from collections import deque
import json
import logging
import re
import sys
import threading
import uuid
from typing import Any, Deque, Dict, List, Optional

# 复用游戏项目的网络客户端
from network.client import NetworkClient
from network.protocol import MessageType
from ai_chat.airi_connection import AiriConnection

logging.basicConfig(
    level=logging.INFO,
    format="  [Bridge %(levelname)s] %(message)s"
)
log = logging.getLogger("bot_bridge")


# ══════════════════════════════════════════════════════════════════
#  回复解析器
# ══════════════════════════════════════════════════════════════════

class ResponseParser:
    """从 AIRI 的自然语言回复中提取游戏指令。"""

    # 行动指令的正则模式
    ACTION_PATTERNS = [
        r"ACTION:\s*(.+)",            # 标准格式: ACTION: move 商店
        r"行动:\s*(.+)",               # 中文格式
        r"我(?:选择|决定|要)(.+)",       # 自然语言: 我选择移动到商店
    ]

    # 选择指令的正则模式
    CHOOSE_PATTERNS = [
        r"CHOOSE:\s*(\d+)",           # 标准格式: CHOOSE: 3
        r"选择:\s*(\d+)",
        r"我选(?:择)?(?:第)?(\d+)",
    ]

    # 确认指令的正则模式
    CONFIRM_PATTERNS = [
        r"CONFIRM:\s*(y|n|yes|no|是|否)",
        r"确认:\s*(y|n|yes|no|是|否)",
    ]

    # 游戏指令关键词 → 指令前缀映射
    COMMAND_KEYWORDS = {
        "移动": "move", "去": "move", "前往": "move",
        "攻击": "attack", "打": "attack",
        "交互": "interact", "使用": "interact",
        "锁定": "lock", "找到": "find",
        "放弃": "forfeit", "跳过": "forfeit",
        "起床": "wake",
        "举报": "report", "集结": "assemble",
        "追踪": "track", "加入警察": "recruit",
        "竞选": "election", "指定": "designate",
        "研究": "study",
    }

    # 短文本最大长度（超过此长度的非标记文本被视为解释性回复）
    _MAX_RAW_COMMAND_LENGTH = 80

    # 无参数也合法的指令前缀
    _BARE_OK_COMMAND_PREFIXES = frozenset({
        "forfeit", "wake", "assemble", "track", "recruit", "election", "study",
    })

    @staticmethod
    def _action_usage(action: Any) -> str:
        """Return the command text for a string or enriched action descriptor."""
        if isinstance(action, dict):
            usage = action.get("usage") or action.get("name") or ""
            return str(usage).strip()
        if isinstance(action, str):
            return action.strip()
        return str(action).strip()

    @classmethod
    def _available_action_usages(cls, available_actions: List[Any]) -> List[str]:
        return [
            usage for usage in (
                cls._action_usage(action) for action in (available_actions or [])
            )
            if usage
        ]

    @classmethod
    def extract_action(cls, text: str, available_actions: List[Any]) -> Optional[str]:
        """从回复中提取行动指令。返回 None 表示无法解析。

        修复：对非标记文本（无 ACTION:/CHOOSE:/CONFIRM: 前缀）施加长度和
        解释性关键词检测，避免将 AIRI 的战术分析/闲聊误识别为游戏指令。
        """
        text = text.strip()
        if not text:
            return None
        action_usages = cls._available_action_usages(available_actions)

        has_format_marker = any(
            text.upper().startswith(prefix)
            for prefix in ("ACTION:", "CHOOSE:", "CONFIRM:", "行动:", "选择:", "确认:")
        )

        # 1. 尝试标准格式（带标记的优先）
        for pattern in cls.ACTION_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                cmd = m.group(1).strip()
                # 验证指令是否以合法行动开头
                for action in action_usages:
                    if cmd.lower().startswith(action.lower()):
                        return cmd
                return cmd  # 即使不在列表中也返回，让服务器验证

        # 如果文本没有格式标记且太长，很可能是解释性文字，直接拒绝
        if not has_format_marker and len(text) > cls._MAX_RAW_COMMAND_LENGTH:
            return None

        # 检查是否包含明显解释性关键词（且没有格式标记保护）
        if not has_format_marker:
            explanation_keywords = [
                "因为", "所以", "但是", "不过", "我觉得", "我认为",
                "建议", "应该", "可能", "也许", "考虑", "分析",
                "首先", "然后", "最后", "综上", "总之", "因此",
                "由于", "如果", "那么", "否则",
            ]
            if any(keyword in text for keyword in explanation_keywords):
                return None

        # 2. 尝试关键词匹配
        for keyword, prefix in cls.COMMAND_KEYWORDS.items():
            if keyword in text:
                # 尝试提取关键词后面的参数
                idx = text.index(keyword) + len(keyword)
                rest = text[idx:].strip().strip("到去了").strip()
                if rest:
                    # 验证提取的参数不是解释性长文本
                    if len(rest) > 60:
                        continue
                    return f"{prefix} {rest}"
                if prefix in cls._BARE_OK_COMMAND_PREFIXES:
                    return prefix

        # 3. 直接检查回复是否就是一个合法指令
        #    额外约束：文本长度不能超过合理指令长度
        if len(text) <= cls._MAX_RAW_COMMAND_LENGTH:
            for action in action_usages:
                if text.lower().startswith(action.lower()):
                    return text

        return None

    @classmethod
    def extract_choice(cls, text: str, options: List[str]) -> Optional[str]:
        """从回复中提取选择。"""
        for pattern in cls.CHOOSE_PATTERNS:
            m = re.search(pattern, text)
            if m:
                try:
                    idx = int(m.group(1)) - 1
                    if 0 <= idx < len(options):
                        return options[idx]
                except ValueError:
                    pass

        # 直接匹配选项文本
        for opt in options:
            if opt and opt in text:
                return opt

        return None

    @classmethod
    def extract_confirm(cls, text: str) -> Optional[bool]:
        """从回复中提取确认结果。"""
        for pattern in cls.CONFIRM_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                val = m.group(1).lower()
                return val in ("y", "yes", "是")

        # 模糊匹配
        positive = any(w in text for w in ("好", "可以", "确认", "同意", "是的", "没问题"))
        negative = any(w in text for w in ("不", "否", "拒绝", "算了", "取消"))
        if positive and not negative:
            return True
        if negative:
            return False

        return None


# ══════════════════════════════════════════════════════════════════
#  指令战略意图解释器
# ══════════════════════════════════════════════════════════════════

class CommandIntentExplainer:
    """将游戏指令映射为人类可读的战略意图说明。

    目标：
    - 让 AIRI 不只看到"可选: move/attack/forfeit"这种孤立列表，
      还能理解每个指令在《起闯战争》游戏规则下的战略意义。
    - 避免 AIRI 因为不懂规则而产生胡乱选择或误解（例如把 forfeit
      理解为投降）。

    属性克制（来源：README §8.2）：
        普通 → 魔法（有效）
        魔法 → 科技（有效）
        科技 → 普通（有效）
        同属性相互有效
        若武器属性被护甲属性克制，攻击无效（护甲不消耗）。
    即三种属性形成循环克制 普通 → 魔法 → 科技 → 普通；同属性互克。
    电流武器（电磁步枪、高斯步枪）特性：可穿透陶瓷护甲（陶瓷免疫电流），
    属于科技属性。
    """

    # 指令前缀（小写）→ 简短战略意图说明
    INTENT_MAP: Dict[str, str] = {
        "move": (
            "移动到指定地点；用于接近目标、跑去关键设施（商店、医院、"
            "军事基地、警察局等）或脱离危险区域。是控制位置的核心手段。"
            "特殊：若你拥有 G1 火萤的『超新星过载』，下一次 move 可指定"
            "目的地为当前地点（原地触发），对当地所有单位造成无视克制伤害。"
        ),
        "attack": (
            "使用某件武器攻击目标。属性克制循环为：普通→魔法→科技→普通，"
            "同属性相互有效；若武器被护甲克制则攻击无效（护甲不消耗）。"
            "格式：attack <目标> <武器> [层 属性]。优先打能造成有效伤害的目标。"
            "电流武器（电磁步枪/高斯步枪）可穿透陶瓷护甲；AOE 武器"
            "（电磁步枪、地震/地动山摇、天星）才能伤到警察单位。"
        ),
        "interact": (
            "与当前地点的设施/物品互动（购买、手术、研究、领取奖励等）。"
            "通常用于补给、强化、获取关键凭证。"
            "必须带参数（interact <物品/项目>），裸 interact 会被服务器拒绝。"
        ),
        "lock": (
            "锁定一个目标玩家用于后续追踪/打击；不会直接造成伤害，"
            "是一种信息/战术先手布置。"
        ),
        "find": (
            "暴露/找到一个隐匿的玩家，使其位置可见；适合在情报缺失时"
            "打破对手隐身或应对潜伏威胁。"
        ),
        "forfeit": (
            "放弃本次行动（既不进攻也不移动也不交互）。仅在没有任何更优"
            "选择、或主动避战节奏时使用，并非投降游戏。"
        ),
        "wake": (
            "起床动作：自己尚未起床时使其出现在自己家中；或唤醒同地点"
            "处于 debuff 的警察单位（wake <警察ID>）。"
        ),
        "report": (
            "在警察局举报有犯罪记录的玩家，启动警察响应流程；"
            "是不直接出手却能借刀杀人的关键政治手段。"
        ),
        "assemble": (
            "作为举报人集结警察出动，开始对被举报者的执法行动。"
        ),
        "track": (
            "作为举报人引导警察立刻追踪目标到达其位置；用于关键时刻"
            "快速锁敌。"
        ),
        # 服务器端注册的内部 action 名是 track_guide（cli/parser.py 把 track 映射到
        # track_guide），这里同时收录两种 key，避免 build_intent_block 漏掉。
        "track_guide": (
            "作为举报人引导警察立刻追踪目标到达其位置；用于关键时刻"
            "快速锁敌。"
        ),
        "recruit": (
            "在警察局加入警队（无犯罪记录、无既有警察时可用），"
            "换取三选二奖励、获得警察身份与执法资格。"
        ),
        "election": (
            "竞选警察队长，需在警察局连续推进进度；当上队长后可指定执法"
            "目标、做研究性学习、控制威信资源。"
        ),
        "designate": (
            "队长专属：指定警察的执法目标；用于把警力对准你想清除的玩家。"
        ),
        "study": (
            "队长专属：在警察局做研究性学习，威信+1；威信归零会重置警察"
            "系统，因此守住威信很重要。"
        ),
        "special": (
            "使用角色专属/特殊操作（天赋技能等）；具体效果取决于当前"
            "角色，通常是改变战局的高价值操作。"
        ),
        "split": (
            "作为队长拆分警队（split <警队ID>）：把一支警队拆成两支独立警队，"
            "用于扩大警力覆盖、分散兵力或解除原警队的纠缠状态。"
        ),
        # 队长操控警察。命令前缀同时有 police 和 police_command 两种来源：
        # - 服务器 _get_available_actions 返回 action 名 "police_command"
        # - 玩家输入是 "police move/equip/attack ..."
        # 两个 key 都收录，确保 INTENT_MAP 命中。
        "police": (
            "队长专属：直接操控警察执行 move/equip/attack 等子命令；"
            "用于亲自调度警队、配装、指定打击目标。"
        ),
        "police_command": (
            "队长专属：直接操控警察执行 move/equip/attack 等子命令；"
            "用于亲自调度警队、配装、指定打击目标。"
        ),
        # 唤醒处于 debuff 的警察单位（wake_police <警察ID>）；
        # 玩家也可以用 wake <警察ID> 触发同一行为。
        "wake_police": (
            "唤醒处于 debuff 状态的同地点警察单位，使其恢复行动能力。"
        ),
    }

    # 各 interact 物品 / 项目的简短意图说明（基于 README §10、§12）。
    # 注意：所有描述都需要严格匹配 README，不得虚构属性或战略价值。
    INTERACT_ITEM_INTENT_MAP: Dict[str, str] = {
        # 商店 / 通用补给
        "打工": (
            "基础功能：消耗1个行动回合，在商店获得1张凭证。"
            "战略价值：中。用于积累购物资金，是早期补给的核心手段。"
        ),
        "凭证": (
            "基础功能：直接领取/获得凭证。战略价值：中。"
            "凭证是商店、医院、军事基地购物/手术的通用通货。"
        ),
        # 武器：属性克制要严格按 README §8.2 描述
        "小刀": (
            "基础功能：近战武器（普通属性，伤害1）。战略价值：低（初始可用，"
            "但易被升级武器替代）。属性克制：普通属性克制魔法护甲；"
            "被科技护甲克制。使用建议：早期接战或与磨刀石组合升级。"
        ),
        "磨刀石": (
            "基础功能：消耗以升级一把已有的小刀（伤害+1）。战略价值：中。"
            "使用建议：拥有未升级的小刀且不缺凭证时购入。"
        ),
        "盾牌": (
            "基础功能：普通属性外层护甲（1层）。战略价值：中。"
            "属性克制：被普通武器克制（普通→魔法→科技→普通循环，盾牌按"
            "普通属性结算）。常作为穿越普通武器威胁前的过渡装备。"
        ),
        "陶瓷护甲": (
            "基础功能：陶瓷外层护甲。战略价值：高。"
            "陶瓷属性免疫电流武器（电磁步枪/高斯步枪），是反制电流流的关键。"
            "使用建议：对手可能携带电流武器时优先获取。"
        ),
        "隐身衣": (
            "基础功能：使用后进入隐身状态。战略价值：高（信息战核心）。"
            "使用建议：避免被锁定/找到时使用；攻击会破隐身（除非满足保留条件）。"
        ),
        "热成像仪": (
            "基础功能：获得探测能力，可破解隐身。战略价值：中。"
            "使用建议：场上有隐身玩家时优先获取。"
        ),
        "防毒面具": (
            "基础功能：免疫病毒效果。战略价值：高（病毒期间）/低（无病毒）。"
            "病毒期间商店免费、医院仍需凭证；使用建议：病毒触发后立即购入。"
        ),
        # 医院手术（消耗所有凭证，但能获得内层护甲）
        "晶化皮肤手术": (
            "基础功能：消耗所有凭证（≥1），获得内层护甲『晶化皮肤』（科技属性，1层）。"
            "战略价值：中-高。属性克制：晶化皮肤被普通武器克制，克制魔法武器。"
        ),
        "额外心脏手术": (
            "基础功能：消耗所有凭证（≥1），获得内层护甲『额外心脏』（普通属性，1层）。"
            "战略价值：中-高。属性克制：额外心脏被科技武器克制，克制魔法武器（同属性互克）。"
        ),
        "不老泉手术": (
            "基础功能：消耗所有凭证（≥1），获得内层护甲『不老泉』（魔法属性，1层）。"
            "战略价值：中-高。属性克制：不老泉被普通武器克制，克制科技武器。"
        ),
        # 魔法所（魔法属性体系）
        "魔法护盾": (
            "基础功能：学习/展开魔法护盾（魔法属性外层护甲）。战略价值：中-高。"
            "属性克制：魔法属性克制科技护甲；被普通武器克制。可被天赋『死者苏生』"
            "复活时重置成展开状态。"
        ),
        "魔法弹幕": (
            "基础功能：近战魔法武器。战略价值：中。"
            "属性克制：魔法属性克制科技护甲；被普通护甲克制。"
        ),
        "远程魔法弹幕": (
            "基础功能：远程魔法武器，需先 lock 目标。战略价值：中-高。"
            "属性克制：同魔法弹幕。"
        ),
        "封闭": (
            "基础功能：法术，封闭一个地点的进出通道。战略价值：中。"
            "使用建议：用于阻断对手撤退或形成区域控制。"
        ),
        "地震": (
            "基础功能：AOE 魔法法术。战略价值：高。"
            "AOE 武器才能伤害警察单位；适合处理聚集的非玩家单位。"
        ),
        "地动山摇": (
            "基础功能：强化版 AOE 魔法。战略价值：高。"
            "AOE 武器才能伤害警察单位。"
        ),
        "隐身术": (
            "基础功能：法术形式的隐身。战略价值：高。"
            "使用建议：信息战节点（被锁定前/逃跑前）。"
        ),
        "探测魔法": (
            "基础功能：法术形式的探测。战略价值：中。可破解隐身。"
        ),
        "办理通行证": (
            "基础功能：办理军事基地通行证。战略价值：高。"
            "通行证是军事基地内 AT力场/电磁步枪/雷达 等装备的前置。"
        ),
        # 军事基地（科技属性体系，需通行证）
        "AT力场": (
            "基础功能：科技属性外层护甲（可重新展开）。战略价值：高。"
            "属性克制：科技属性克制普通护甲；被魔法武器克制。"
            "可被天赋『死者苏生』复活时重置成展开状态。"
        ),
        "电磁步枪": (
            "基础功能：远程电流武器（科技属性）。战略价值：高。"
            "属性克制：科技属性克制普通护甲；被魔法护甲克制。"
            "特殊：电流武器可穿透陶瓷护甲；AOE 武器，可伤害警察单位。"
        ),
        "高斯步枪": (
            "基础功能：远程电流武器（科技属性，单体）。战略价值：高。"
            "属性克制：与电磁步枪同，可穿透陶瓷护甲；但非 AOE，不能伤警察。"
        ),
        "导弹控制权": (
            "基础功能：获得军事基地导弹控制权标记。战略价值：高。"
            "使用建议：作为对所有玩家的高威慑筹码或决胜手段（具体效果以"
            "当局规则为准，不要凭空假定细节）。"
        ),
        "雷达": (
            "基础功能：获得探测能力。战略价值：中。可破解隐身。"
        ),
        "隐形涂层": (
            "基础功能：科技体系的隐身。战略价值：高。"
            "使用建议：信息战节点；不要在已隐身时重复获取。"
        ),
    }

    @classmethod
    def explain(cls, command: str) -> str:
        """返回指令的战略意图说明。command 可以是完整指令（含参数）
        或仅指令前缀；解析失败时返回空字符串。"""
        if not command:
            return ""
        prefix = command.strip().split()[0].lower()
        return cls.INTENT_MAP.get(prefix, "")

    @classmethod
    def explain_item(cls, item: str) -> str:
        """返回 interact 物品/项目的战略意图说明；未知项目返回空串。"""
        if not item:
            return ""
        return cls.INTERACT_ITEM_INTENT_MAP.get(item.strip(), "")

    @classmethod
    def explain_action_dict(cls, action: Any) -> str:
        """针对服务器下发的 action 描述（可能是 str 或 dict）返回意图说明。"""
        if isinstance(action, dict):
            usage = action.get("usage") or action.get("name") or ""
            return cls.explain(usage)
        if isinstance(action, str):
            return cls.explain(action)
        return ""

    @classmethod
    def build_intent_block(cls, actions: List[Any]) -> str:
        """为一组可选行动生成多行的意图说明文本，用作 prompt 增强。"""
        lines: List[str] = []
        seen_prefixes = set()
        for action in actions:
            if isinstance(action, dict):
                key = (action.get("usage") or action.get("name") or "").strip()
            else:
                key = str(action).strip()
            if not key:
                continue
            prefix = key.split()[0].lower()
            if prefix in seen_prefixes:
                continue
            intent = cls.INTENT_MAP.get(prefix)
            if not intent:
                continue
            seen_prefixes.add(prefix)
            lines.append(f"- {prefix}: {intent}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  天赋 T0 / 子决策 / 神代资源 战略意图说明
# ══════════════════════════════════════════════════════════════════
#
# 严格遵循 README 12.1 / 12.2 章节的天赋描述，不得虚构机制。
# 说明：这些字典只用于「向 AIRI 解释当前决策点的战略含义」，
# 不影响游戏内的实际效果结算。

# 天赋 T0 发动是否启动的战略意图（README §12.1 / §12.2）
TALENT_T0_INTENT_MAP: Dict[str, Dict[str, str]] = {
    "一刀缭断": {
        "intent": "无视克制的高爆发近战",
        "explanation": (
            "选择一件已装备的近战武器与一个同地点且面对面的目标，"
            "立刻进行一次近战攻击；该次攻击伤害提高 100% 且无视属性克制。"
            "使用次数：2 次。若本次攻击击破最后一层内层护甲，溢出按"
            "「无视生克的伤害」处理，不触发最后内层吸收溢出。"
        ),
        "trigger_condition": "同地点存在面对面目标，且自己装备了至少一件近战武器",
        "strategic_value": "高（适合击穿克制护甲或处决残血）",
    },
    "天星": {
        "intent": "群体石化控制 + 群伤",
        "explanation": (
            "对你所在地点除自己以外的所有玩家与非玩家单位各造成"
            "（1 + 0.5 × 命中单位数）点无视属性克制伤害（不超过 3）；"
            "所有目标获得「石化」状态：石化单位在其下一个行动回合只能"
            "选择解除石化或不解除（受攻击则石化自动解除，解除时额外受"
            "0.5 点无视生克伤害）。使用次数：2 次。"
        ),
        "trigger_condition": "同地点聚集多个敌方/警察单位",
        "strategic_value": "高（反警察体系与群体控场）",
    },
    "六爻": {
        "intent": "猜拳决定效果的高方差爆发",
        "explanation": (
            "选择另一名玩家进行一次猜拳，按结果结算：双剪刀=潜龙勿用"
            "（任选 1 名玩家受 1 点无视克制伤害）；双石头=飞龙在天"
            "（夺甲，复制目标 1 件外层护甲）；双布=元亨利贞（金身，"
            "免疫伤害与 debuff，至下个 R0 消散，无视克制伤害穿透）；"
            "你赢=亢龙有悔（随机禁用目标 1 件非拳击武器 2 个全局轮次，"
            "若仅有拳击改为眩晕）；你输=或跃在渊（你获得 2 个连续额外行动回合，"
            "不能再发动六爻）；平局=群龙无首（解除自己所有「被发现/被锁定」并"
            "进入隐身，对手被强制传送）。每 5 轮充能 1 次，最多储存 2 次。"
        ),
        "trigger_condition": "有可用充能且需要博弈翻盘",
        "strategic_value": "高（但结果由猜拳决定，方差大）",
    },
    "不良少年": {
        "intent": "犯罪即获得额外行动回合",
        "explanation": (
            "常驻效果：开局获得「热那亚之刃」（视为小刀，攻击玩家不构成"
            "可被举报的违法行为）。每触发一种「犯罪类型」立即获得 1 个"
            "额外行动回合，每种犯罪类型最多触发 1 次。"
        ),
        "trigger_condition": "常驻；额外回合在首次触发某类犯罪时自动结算",
        "strategic_value": "高（攻击型连续行动）",
    },
    "combo": {
        "intent": "连续行动奖励关",
        "explanation": (
            "连续三个全局轮次行动 → 下个全局轮次开始时获得「奖励关」："
            "本回合投掷阶段 D4 必为 4、D6 必为 6；并获得 1 临时额外攻击力 + 1 临时 HP，"
            "持续到奖励关结束。"
        ),
        "trigger_condition": "连续 3 个全局轮次都行动了",
        "strategic_value": "中-高",
    },
    "朝阳好市民": {
        "intent": "举报无需到警局 + 扩展违法清单 + 队长加速",
        "explanation": (
            "「举报热线」举报无需前往警察局；本局违法名单额外加入：进入他人家、"
            "进入军事基地（无论是否花费回合获得通行证）、释放病毒。"
            "竞选警队队长所需行动回合数 −1。"
        ),
        "trigger_condition": "常驻",
        "strategic_value": "中（政治型）",
    },
    "死者苏生": {
        "intent": "把目标挂载「死后复活一次」",
        "explanation": (
            "需先在魔法所连续 2 个行动回合学习，习得后消耗 1 个行动回合"
            "将效果挂载到任意 1 名玩家（可含自己）。挂载目标死亡时立刻"
            "在自己家中重生，保留所有物品；所有「破碎护盾」（魔法护盾、AT 力场）"
            "重置为正常展开状态；下个行动回合不需要起床。结算完成后本天赋永久失效。"
            "全场最多挂载 1 名玩家。"
        ),
        "trigger_condition": "学习已完成且尚未挂载过任何玩家",
        "strategic_value": "高",
    },
    "火萤Ⅳ型-完全燃烧": {
        "intent": "强力常驻 + 「炽愿」/「超新星过载」资源",
        "explanation": (
            "造成伤害 +100%，受到伤害 −50%；HP 降至 0.5 时不眩晕，下回合恢复至 1。"
            "失熵症 debuff 从「15 + (开局人数−2) × 3」轮开始；超新星过载是替代"
            "「移动」的特殊技能，下次 move 可指定为当前地点，对当地所有单位造成"
            "1 点无视克制伤害（享受 +100% 加成）并施加「灼烧」。"
            "命中和击杀都会积累「炽愿」，可抵扣 debuff 或换取超新星。"
        ),
        "trigger_condition": "常驻；T0 提示通常对应主动管理资源时机",
        "strategic_value": "高",
    },
    "请一直，注视着我": {
        "intent": "全息影像区域控制",
        "explanation": (
            "在自己所在地点展开「全息影像」，持续 min(3 + (存活−2), 6) 轮："
            "所有非玩家单位被强制聚集并沉沦；其他玩家投 D6≥3 被强制拉入并震荡；"
            "释放完成后立刻获得 1 个额外行动回合。影像内：隐身无效、单位受到额外"
            "+0.5 无视克制伤害、其他玩家禁用 lock/find、玩家相互强制找到。"
        ),
        "trigger_condition": "希望群控/反警察 + 强制聚集敌人",
        "strategic_value": "高",
    },
    "神话之外": {
        "intent": "拉入幻想乡结界 1v1",
        "explanation": (
            "选择同地点至多 1 名其他玩家拉入「遗世独立的幻想乡」结界："
            "结界内每轮由你与目标猜拳决定唯一行动者，其他玩家暂停。"
            "结界内不能 interact/不能 move/不能锁结界外玩家；隐身无效、"
            "六爻解除锁定/发现不生效；发动者免疫控制；目标天赋（主动+被动）"
            "被完全压制。展开瞬间立刻获得 1 个额外行动回合。"
        ),
        "trigger_condition": "同地点有 1 名想单独处理的高威胁目标",
        "strategic_value": "高",
    },
    "愿负世，照拂黎明": {
        "intent": "致命攻击触发的救世主形态",
        "explanation": (
            "受到致命攻击时自动消耗所有「火种」（上限 12）进入「救世主」状态："
            "免疫该次致死伤害；每点火种 → +0.5 临时 HP / +0.5 近战攻击 / "
            "AOE 伤害提高 0.5（>6 时提高 1）/ 持续 +1 轮；状态期间禁用远程。"
            "结束时未消耗的临时 HP 永久化（总永久 HP 上限 +3）。本天赋唯一触发后永久失效。"
        ),
        "trigger_condition": "自动触发；本提示通常出现在主动决策环节（极少见）",
        "strategic_value": "高",
    },
    "往世的涟漪": {
        "intent": "「献予 X 之诗」与「锚定命运」",
        "explanation": (
            "高度复杂的辅助/控制型神代天赋，依赖「追忆」层数（首次 24，之后 12）"
            "发动「锚定命运」或献诗（武器/护甲/律法/夜望/六爻 等）。"
            "T0 决策通常涉及为盟友/敌人挂载效果，详见 README 12.2 章节。"
        ),
        "trigger_condition": "追忆层数足够 + 需要主动改变局势",
        "strategic_value": "高（但执行非常复杂）",
    },
    "铁之荷鲁斯": {
        "intent": "G7 战术宏体系 + 架盾射击",
        "explanation": (
            "G7 星野角色，使用「战术指令宏」串接基础动作。Cost 体力条管理："
            "每轮 R0 恢复 5；架盾/射击消耗 2 Cost；持盾/冲刺/投掷消耗 1 Cost。"
            "宏内的射击需要先装填弹药，建议用「排弹」优化弹序。"
            "T0 决策通常对应是否进入战斗宏模式（不是自主组合，而是从预制宏选）。"
        ),
        "trigger_condition": "有目标 + Cost ≥ 2 + 有弹药/可装填",
        "strategic_value": "高",
    },
}

# 天赋子决策 situation → 战略意图（README §12 + 实际 situation 名取自代码）
TALENT_SUB_DECISION_INTENT: Dict[str, Dict[str, str]] = {
    # T1 一刀缭断
    "oneslash_pick_weapon": {
        "intent": "选择「一刀缭断」使用的近战武器",
        "explanation": (
            "一刀缭断无视属性克制且伤害 +100%，因此武器选择只需关心基础伤害。"
        ),
        "suggestion": "选择基础伤害最高的近战武器（如已升级的小刀）",
    },
    "oneslash_pick_target": {
        "intent": "选择「一刀缭断」的攻击目标",
        "explanation": (
            "目标必须是同地点且与你处于「面对面」关系的玩家。无视克制 = 不必"
            "考虑目标护甲属性。"
        ),
        "suggestion": "选择威胁度最高、或最容易处决（残血/关键威胁）的目标",
    },
    # T3 天星
    "star_ripple_bounce": {
        "intent": "天星的弹射目标选择（涟漪强化场景）",
        "explanation": (
            "天星造成 (1 + 0.5×命中) 点无视克制伤害（不超过 3）并施加石化；"
            "弹射时按顺序选择目标。"
        ),
        "suggestion": "优先选择威胁度高或聚集中的目标",
    },
    # T4 六爻 —— 注意：实际 situation 名取自 talents/t4_hexagram.py
    "hexagram_pick_opponent": {
        "intent": "选择六爻的猜拳对手",
        "explanation": (
            "你将和该玩家猜拳，结果决定卦象（潜龙勿用/飞龙在天/元亨利贞/"
            "亢龙有悔/或跃在渊/群龙无首）。每种卦象效果差异巨大。"
        ),
        "suggestion": "选择威胁度适中的对手；若想夺甲优先选有外层护甲的；"
                      "若想禁武优先选高伤害武器持有者。",
    },
    "hexagram_my_choice": {
        "intent": "你出拳（石头/剪刀/布）",
        "explanation": (
            "结果效果：双剪刀=潜龙勿用、双石头=飞龙在天（夺甲）、双布=元亨利贞（金身）、"
            "你赢=亢龙有悔（禁武）、你输=或跃在渊（额外回合）、平局=群龙无首（遁走）。"
        ),
        "suggestion": "可随机出拳，或根据当前需要的卦象偏向选择（结果由双方共同决定）。",
    },
    "hexagram_opp_choice": {
        "intent": "对手出拳（石头/剪刀/布）",
        "explanation": "你被卷入猜拳。同上结果矩阵。",
        "suggestion": "可随机出拳。",
    },
    "hexagram_thunder_target": {
        "intent": "潜龙勿用——天雷目标",
        "explanation": "选择任意 1 名玩家（可以不是猜拳对手），对其造成 1 点无视属性克制伤害。",
        "suggestion": "选择 HP 最低的高威胁目标处决",
    },
    "hexagram_steal_target": {
        "intent": "飞龙在天——夺甲目标",
        "explanation": "选择 1 名玩家。你将从其外层护甲中复制 1 件给自己（不破坏其护甲）。",
        "suggestion": "选择拥有你目前缺少的关键外层护甲（如 AT 力场、陶瓷护甲）的玩家",
    },
    "hexagram_steal_pick": {
        "intent": "飞龙在天——选择要复制的护甲",
        "explanation": "从目标的外层护甲中挑一件复制；遵循护甲层数与同属性上限。",
        "suggestion": "优先选你目前没有、且属性互补的护甲",
    },
    "hexagram_disarm_target": {
        "intent": "亢龙有悔——禁武目标",
        "explanation": "随机禁用目标 1 件非拳击武器持续 2 个全局轮次；若仅有拳击则改为眩晕。",
        "suggestion": "选择威胁度最高的武器持有者",
    },
    "hexagram_free_target": {
        "intent": "六爻自由目标（涟漪/特定效果触发）",
        "explanation": "由触发效果决定具体含义，请结合上下文。",
        "suggestion": "按上下文与威胁度选择",
    },
    # G2 ish-bosheth
    "g2_emotion_choice": {
        "intent": "选择你在 G2 舞台中的态度",
        "explanation": (
            "入戏=可攻击其他玩家+享受聚光灯加成但受光色增伤; "
            "抽离=安全旁观但动手即自动入戏; "
            "反抗=可攻击G2发动者执行破幕但不能攻击其他人"
        ),
        "suggestion": "威胁低→抽离; 想破幕→反抗; 想借舞台打人→入戏",
    },
    "g2_sing_song": {
        "intent": "选择演唱的曲目类型",
        "explanation": (
            "追寻那道光=给聚光灯(临时HP+攻击+额外行动); "
            "拼接遗憾=给安可阻止目标离场; "
            "Before light=设置光色修改全场伤害"
        ),
        "suggestion": "需要控场→拼接遗憾; 需要输出→追光+聚光灯; 需要群体增伤→Before light",
    },
    "g2_sing_rhythm": {
        "intent": "选择曲目的强度（节奏）",
        "explanation": (
            "温柔=基础效果消耗1 Regard; 更强节奏=增强效果但消耗2 Regard"
        ),
        "suggestion": "Regard 充裕→选强节奏; Regard 紧张→选温柔",
    },
    "g2_sing_target": {
        "intent": "选择演唱的目标听者",
        "suggestion": "选威胁最大或最适合控制的目标",
    },
    "g2_melody_target": {
        "intent": "选择旋律的目标",
        "explanation": "旋律是G2的直接伤害手段，会造成1/1/0.5/0.5的连锁伤害",
        "suggestion": "优先选血量低的目标",
    },
    "g2_melody_propagate": {
        "intent": "选择旋律传播的下一个目标",
        "explanation": "旋律击中当前目标后需选择下一个传播目标",
        "suggestion": "优先选尚未被命中的目标",
    },
    # G3 神话之外
    "mythland_pick_target": {
        "intent": "选择拉入幻想乡结界的目标",
        "explanation": (
            "结界内仅你和目标存在，外部时间暂停；目标天赋（主动+被动）被压制；"
            "结界内不能 interact/不能 move/不能锁结界外玩家。"
        ),
        "suggestion": "选择最希望单独处理的高威胁目标",
    },
    # G5 涟漪/锚定/献诗
    "ripple_choose_method": {
        "intent": "涟漪选择发动方式（锚定 / 献诗 / 取消）",
        "explanation": "锚定 = 改变命运（高 Cost），献诗 = 增益/控制；具体见 README 12.2。",
        "suggestion": "按追忆资源量和局势选择",
    },
    "ripple_poem_target": {
        "intent": "献诗的目标玩家",
        "explanation": "献诗效果由所选诗的种类决定（武器/护甲/律法/夜望/六爻）。",
        "suggestion": "选择能最大化诗篇收益的玩家",
    },
    "ripple_anchor_type": {
        "intent": "锚定命运的类型",
        "explanation": "锚定可强行使某玩家击杀目标 / 获得护甲 / 到达地点 / 获得物品等。",
        "suggestion": "按追忆资源和局势选择",
    },
    "ripple_anchor_kill_target": {
        "intent": "锚定命运——击杀目标",
        "explanation": "锚定后该玩家下一个行动回合必须尝试击杀此目标。",
        "suggestion": "选择高威胁敌方",
    },
    "ripple_anchor_armor_target": {
        "intent": "锚定命运——破坏护甲目标",
        "explanation": "锚定后被锚定者的一件护甲将被强制摧毁。",
        "suggestion": "选择关键护甲持有者",
    },
    "ripple_anchor_armor_pick": {
        "intent": "锚定命运——选择要破坏的护甲层",
        "explanation": "从目标护甲中指定一件破坏。",
        "suggestion": "优先破坏外层关键护甲",
    },
    "ripple_anchor_acquire_item": {
        "intent": "锚定命运——获得物品",
        "explanation": "锚定后被锚定者下一回合必须前往获取此物品。",
        "suggestion": "选择最具战略价值的物品",
    },
    "ripple_anchor_arrive_loc": {
        "intent": "锚定命运——前往地点",
        "explanation": "锚定后被锚定者下一回合必须移动到该地点。",
        "suggestion": "选择能将其暴露/陷入危险的地点",
    },
    "ripple_anchor_fail": {
        "intent": "锚定失败后的回退选项",
        "explanation": "按 README 12.2「往世的涟漪」给出的失败处理选项。",
        "suggestion": "按当前损失最小的方向选",
    },
    "ripple_hexagram_free_choice": {
        "intent": "诗篇·六爻——自由选择要触发的卦象效果",
        "explanation": "通过献诗·六爻可绕过猜拳直接选定效果。",
        "suggestion": "结合当前需要（伤害/夺甲/金身/禁武/额外回合/遁走）选择",
    },
    "poem_law_extra_action": {
        "intent": "献予律法之诗——分配额外行动",
        "explanation": "向警察体系倾斜的诗篇；按 README 12.2 处理。",
        "suggestion": "选择能加速警察执法的单位",
    },
    "poem_nightwatch_choice": {
        "intent": "献予夜望之诗——接受/拒绝",
        "explanation": "对应献予夜望之诗的双方确认环节。",
        "suggestion": "结合双方关系与局势权衡",
    },
    # G7 星野
    "hoshino_form": {
        "intent": "星野选择初始形态",
        "explanation": "决定后续战术宏走向（铁之荷鲁斯 vs Terror）。",
        "suggestion": "按本局阵容与威胁度选择",
    },
    "hoshino_self_doubt_choice": {
        "intent": "星野「自我怀疑」抉择",
        "explanation": "影响星野后续状态走向（README §12.2 G7 章节）。",
        "suggestion": "按 README 给出的两条文本权衡",
    },
    # 加入警察 / 队长竞选 等其他常见 situation
    "recruit_pick_1": {
        "intent": "加入警察——选择第 1 个奖励",
        "explanation": "三选二，详细物品列表见 README §10.8.1。",
        "suggestion": "优先互补当前缺口（武器/护甲/凭证）",
    },
    "recruit_pick_2": {
        "intent": "加入警察——选择第 2 个奖励",
        "explanation": "三选二之第二项。",
        "suggestion": "与第一项形成互补",
    },
    "captain_election": {
        "intent": "队长竞选投票",
        "explanation": "确定本局警察队长，享有 designate/study/police 等权限。",
        "suggestion": "若自己有政治优势可投自己；否则投威胁较低的人",
    },
    "petrified": {
        "intent": "石化决策：解除 / 保持",
        "explanation": (
            "石化单位的下个行动回合只能二选一：解除（额外受 0.5 无视克制伤害）或不解除。"
            "若不解除，受到攻击会自动解除（同样触发 0.5 伤害）。"
        ),
        "suggestion": "如果当前 HP 充裕且预期被攻击概率高 → 保持；否则解除",
    },
    # ── 神代3 神话之外（mythland_rps） ──────────────────────────
    "mythland_rps": {
        "intent": "幻想乡结界内的猜拳",
        "explanation": (
            "在被拉入幻想乡结界后，与发动者进行石头/剪刀/布的较量，"
            "结果影响后续结界内交互（详见 README §12.2 神话之外）。"
        ),
        "suggestion": "若无博弈矩阵提示，可均匀随机出招避免被读心",
    },
    # ── 原初7 死者苏生（resurrection_pick_target） ──────────────
    "resurrection_pick_target": {
        "intent": "选择苏生目标",
        "explanation": (
            "选择一名已出局的玩家作为「死者苏生」的复苏目标。"
            "复苏者将在你的下一个行动回合开始时获得复活资格（详见 README §12.1）。"
        ),
        "suggestion": "优先选择对战局立场友好或能继续承担警察/票数贡献的玩家",
    },
    # ── G7 铁之荷鲁斯 战术指令宏 子决策 ────────────────────────
    # 说明：以下子决策都属于「G7 战术宏模式」。请配合
    # TACTICAL_MACRO_INTENT_MAP 使用预制宏，避免自主拼接长序列。
    "hoshino_form": {
        "intent": "选择星野的形态",
        "explanation": (
            "在天赋注册阶段选择星野的初始形态："
            "水着 / 临战 / 等（详见 README §12.2 铁之荷鲁斯）。"
            "形态影响后续战术指令的免费维持、起床装备等。"
        ),
        "suggestion": "若不确定，使用建议选项或默认；不同形态各有取舍",
    },
    "hoshino_tactical_equip": {
        "intent": "战术装备配发",
        "explanation": (
            "首次同时持有融合装备后，可在战术道具 / 药物 / 子弹中三选一进行配发。"
            "每次可从以下选项中选择 1 项：1 个战术道具 / 1 种药物 / 2 发某属性子弹。"
        ),
        "suggestion": "前期偏向药物与子弹保证基础输出；中后期可补战术道具",
    },
    "hoshino_repair_material": {
        "intent": "选择修复材料",
        "explanation": (
            "通过 special 修复 <护甲名> 指令以一件护甲为代价为「铁之荷鲁斯」恢复护甲值。"
            "可选材料：盾牌 / AT 力场（READ §12.2 战术天才）。"
        ),
        "suggestion": "保留稀有/属性护甲，优先以普通盾牌作为修复材料",
    },
    "hoshino_tactical_input": {
        "intent": "战术指令宏中的下一个动作",
        "explanation": (
            "你正处于 G7 战术指令宏模式：请从预制宏中选择一个，"
            "不要自主拼接长序列。预制宏包括 基础攻击宏 / 反队长接近宏 / "
            "反队长无盾宏 / 补刀+转火宏 / 全力射击宏（详见 TACTICAL_MACRO_INTENT_MAP）。"
        ),
        "suggestion": (
            "若同地点有警察保护 → 反队长接近宏 / 反队长无盾宏；"
            "Cost 充裕 → 全力射击宏；其余默认基础攻击宏。"
        ),
    },
    "hoshino_shield_shoot_target": {
        "intent": "架盾射击目标",
        "explanation": (
            "处于「架盾射击」状态下，从你正面的玩家中选取射击目标。"
            "弹丸分布依正面玩家数量自动判定（README §12.2）。"
        ),
        "suggestion": "选择威胁度最高或残血的正面目标",
    },
    "hoshino_reload": {
        "intent": "重新装填的牺牲品",
        "explanation": (
            "摧毁一件自己持有、且非「荷鲁斯之眼/铁之荷鲁斯」的有属性物品或护甲，"
            "为「荷鲁斯之眼」填充 4 发对应属性子弹（README §12.2）。"
        ),
        "suggestion": "优先消耗即将过期或低战略价值的有属性物品",
    },
    "hoshino_throw_item": {
        "intent": "选择投掷的战术道具",
        "explanation": (
            "可选战术道具：闪光弹（禁用锁定）/ 烟雾弹（隐身、禁 find/lock）"
            " / 燃烧瓶（灼烧 0.5×2）（README §12.2）。"
        ),
        "suggestion": "依对手是否依赖 lock/远程武器 选择闪光或烟雾；爆发选燃烧瓶",
    },
    "hoshino_throw_location": {
        "intent": "投掷目标地点",
        "explanation": (
            "选择战术道具投掷到的地点。架盾状态下，道具仅对结束时处于你正面的单位生效。"
        ),
        "suggestion": "选择敌人聚集或将要途经的地点",
    },
    "hoshino_medicine": {
        "intent": "选择服用药物",
        "explanation": (
            "可选 EPO（cost +1）/ 海豚巧克力（回复 1 层光环）。"
            "肾上腺素不能在宏中使用（README §12.2）。"
        ),
        "suggestion": "Cost 不足 → EPO；光环受损 → 海豚巧克力",
    },
    "hoshino_dash": {
        "intent": "冲刺目的地",
        "explanation": (
            "持盾状态下的战术移动指令，目标为特定地点；每个宏最多 1 次（README §12.2）。"
        ),
        "suggestion": "选择能立刻接战目标或夺取关键资源的地点",
    },
    "hoshino_reorder_ammo": {
        "intent": "排弹（重排弹匣顺序）",
        "explanation": (
            "在装填后调整弹匣中子弹的发射顺序，使属性克制更优（README §12.2）。"
            "每个宏最多 1 次。"
        ),
        "suggestion": "把最克制当前目标护甲属性的子弹排到最前",
    },
    "hoshino_self_doubt_choice": {
        "intent": "色彩反转：是否进入自我怀疑",
        "explanation": (
            "当「色彩」≥ 6 时，可在回合开始时选择是否进入自我怀疑（之后会进入 Terror）。"
            "进入后下一次行动回合被跳过（README §12.2 色彩反转）。"
        ),
        "suggestion": "若已被压制、需要博一次翻盘 → 进入；优势局保持当前节奏",
    },
    # ── G5 涟漪：法度 / 命运伤害 子决策 ─────────────────────────
    "poem_law_police_action": {
        "intent": "法度诗篇：警察行动方向",
        "explanation": (
            "在「法度诗篇」激活时，决定警察单位下一行动的具体走向（README §12.2 涟漪诗篇）。"
        ),
        "suggestion": "围绕重点目标制造保护或转移注意力",
    },
    "ripple_destiny_damage": {
        "intent": "命运伤害的承受/转移决策",
        "explanation": (
            "在涟漪命运结算时，确认伤害承受或转移方式（README §12.2 往世的涟漪）。"
        ),
        "suggestion": "尽量转嫁高威胁伤害，保留己方关键单位",
    },
}

# 神代天赋资源管理（README §12.2）
GOD_TIER_RESOURCE_MANAGEMENT: Dict[str, Dict[str, str]] = {
    "G1_炽愿": {
        "resource": "炽愿（最多 3 层）",
        "description": (
            "每次 debuff 首次开始生效时获得 1 层；超新星过载每命中 1 个目标获得 1 层；"
            "每完成击杀额外获得 1 层。可用于抵扣 debuff 或换取超新星过载次数。"
        ),
        "strategy": "保留炽愿应对致命 debuff；超新星过载用于敌人聚集场景",
    },
    "G1_超新星过载": {
        "resource": "超新星过载次数（不可叠加，最多 1）",
        "description": (
            "有次数时下一次 move 可指定目的地为当前地点，对当地所有单位造成"
            "1 点无视克制伤害（享受源式萤火 +100% 加成）并施加灼烧；发动后"
            "失熵症 debuff 后延 3 轮。"
        ),
        "strategy": "等敌人/警察聚集再原地触发，最大化群伤；可被 RL 的 in-place move 表达",
    },
    "G4_火种": {
        "resource": "火种（上限 12 层）",
        "description": (
            "被其他玩家攻击 / 接受正面天赋 → 获得 1 火种；若来自限定次数天赋额外 +1。"
            "受到致命攻击时全数消耗，进入「救世主」状态：临时 HP / 攻击力 / AOE 加成 / "
            "持续时间均按消耗层数线性放大。"
        ),
        "strategy": "尽量积累，触发后状态结束时部分临时 HP 永久化",
    },
    "G5_追忆": {
        "resource": "追忆层数（首次 24，之后每 12 可发动）",
        "description": (
            "锚定命运 / 献诗 等核心操作依赖追忆；详见 README 12.2「往世的涟漪」。"
        ),
        "strategy": "积满 24 优先锚定关键命运；之后保持献诗节奏",
    },
    "G7_Cost": {
        "resource": "Cost 体力条（每轮 R0 恢复 5）",
        "description": (
            "架盾 = 2、射击 = 2、持盾 = 1、冲刺 = 1、投掷 = 1、服药 = 0、"
            "重新装填 = 0、排弹 = 0（每宏最多一次）。"
        ),
        "strategy": "确保核心动作（架盾 + 射击）有足够 Cost，避免无谓消耗",
    },
}


# ══════════════════════════════════════════════════════════════════
#  G7 战术宏 / 战术动作 战略意图说明
# ══════════════════════════════════════════════════════════════════
#
# G7（星野/铁之荷鲁斯）通过「战术指令宏」串接基础动作，AIRI 应当
# 从预制宏中选择，而不是自主组合。该说明字典仅用于解释意图，不直接
# 驱动宏的执行（实际执行由 BasicAI 完成）。

TACTICAL_MACRO_INTENT_MAP: Dict[str, Dict[str, str]] = {
    "基础攻击宏": {
        "intent": "标准战斗",
        "explanation": "接近目标 → 架盾 → 连续射击。适合常规场景。",
        "typical_sequence": "（投掷如需）→（冲刺如需）→ 架盾 → 射击 × N",
        "when_to_use": "有弹药且有目标，无特殊威胁",
    },
    "反队长接近宏": {
        "intent": "反警察接近",
        "explanation": "投掷压制 → 排弹 → 持盾 → 冲刺 → 架盾 → 射击。对付有警察保护的对手。",
        "typical_sequence": "投掷闪光/烟雾 → 排弹 → 持盾 → 冲刺 → 架盾 → 射击 × N",
        "when_to_use": "目标有警察保护且需要接近",
    },
    "反队长无盾宏": {
        "intent": "搏命反警察",
        "explanation": "同地点直接架盾 → 全力射击。同地点警察保护下无法接近时使用。",
        "typical_sequence": "架盾 → 射击 × N",
        "when_to_use": "同地点有警察保护且无法接近",
    },
    "补刀+转火宏": {
        "intent": "多目标处理",
        "explanation": "先补刀残血 → 转火主目标。",
        "typical_sequence": "射击残血目标 → 射击主目标 × N",
        "when_to_use": "有多个残血/威胁",
    },
    "全力射击宏": {
        "intent": "Cost 充足时最大化输出",
        "explanation": "架盾 → 服药 → 排弹 → 连续射击。",
        "typical_sequence": "架盾 → 服药 EPO/巧克力 → 排弹 → 射击 × N",
        "when_to_use": "Cost ≥ 10 且需最大化伤害",
    },
}

TACTICAL_ACTION_INTENT_MAP: Dict[str, Dict[str, str]] = {
    "架盾": {
        "intent": "防御姿态（划分正/背面）",
        "explanation": (
            "进入「架盾」状态：快照护甲值并划分正面/背面。架盾期间禁止 move 与 interact；"
            "正面伤害按快照阈值过滤、背面伤害全额承受。"
        ),
        "cost": "2 Cost",
        "strategic_value": "高",
    },
    "射击": {
        "intent": "远程攻击",
        "explanation": (
            "用荷鲁斯之眼射击已锁定的目标；需要先装填弹药。架盾时射击只能针对正面玩家。"
        ),
        "cost": "2 Cost",
        "strategic_value": "高",
    },
    "重新装填": {
        "intent": "弹药补给",
        "explanation": "消耗战术道具装填；弹序未知，建议配合「排弹」优化。",
        "cost": "0 Cost",
        "strategic_value": "中",
    },
    "持盾": {
        "intent": "可移动的弱化防御",
        "explanation": "持盾时可 move 但禁止 interact。",
        "cost": "1 Cost",
        "strategic_value": "中",
    },
    "投掷": {
        "intent": "区域控制",
        "explanation": (
            "投掷战术道具到指定地点：闪光弹禁用锁定、烟雾弹禁用保护、破片手雷造成伤害。"
        ),
        "cost": "1 Cost",
        "strategic_value": "高",
    },
    "服药": {
        "intent": "状态增强",
        "explanation": "海豚巧克力 → 恢复 HP；EPO → Cost +X。",
        "cost": "0 Cost",
        "strategic_value": "中",
    },
    "冲刺": {
        "intent": "快速接近 / 撤离",
        "explanation": "快速移动到目标地点；每宏最多 1 次。",
        "cost": "1 Cost",
        "strategic_value": "高",
    },
    "排弹": {
        "intent": "弹序优化",
        "explanation": "重排弹匣顺序，优化属性克制；每宏最多 1 次。",
        "cost": "0 Cost",
        "strategic_value": "中",
    },
}


# ══════════════════════════════════════════════════════════════════
#  核心 Bridge 类
# ══════════════════════════════════════════════════════════════════

class BotBridge:
    """核心桥接逻辑：连接游戏服务器和 AIRI。"""

    # 用于过滤聊天回复中的格式化指令
    _FORMAT_CMD_RE = re.compile(r"(ACTION|CHOOSE|CONFIRM):\s*.+", re.IGNORECASE)
    _TIMESTAMP_RE = re.compile(
        r"^\[(?:\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?"
        r"|\d{2}:\d{2}(?::\d{2})?)\]\s*"
    )
    _BRACKETED_NUMBER_RE = re.compile(r"^\[\s*(\d+)\s*\]$")

    def __init__(self, config: dict):
        self.config = config
        self.bot_name = config.get("bot_name", "AIRI_Bot")
        self.action_timeout = config.get("action_timeout", 60)
        self.chat_timeout = config.get("chat_reply_timeout", 30)

        # 游戏客户端
        self.game_client = NetworkClient(
            host=config.get("game_server_host", "127.0.0.1"),
            port=config.get("game_server_port", 9527),
        )

        # AIRI 连接
        self.airi = AiriConnection(
            ws_url=config.get("airi_ws_url", "ws://localhost:6121/ws"),
            module_id=config.get("module_id", "badtime-war-bridge"),
            auth_token=config.get("airi_auth_token", ""),
            heartbeat_interval=int(config.get("heartbeat_interval", 30)),
            max_reconnect_attempts=int(
                config.get("max_reconnect_attempts", 10)
            ),
        )

        # 游戏状态追踪
        self.game_started = threading.Event()
        self.game_finished = threading.Event()
        self.game_events: List[str] = []  # 最近的游戏事件日志
        self.pending_request: Dict[str, Any] = {"msg": None, "msg_type": None}
        self.pending_lock = threading.Lock()
        self.pending_event = threading.Event()

        # AIRI context:update 通道
        # 使用固定 contextId，确保始终操作同一个桶（AIRI 无删除桶机制，
        # 换 ID 会留下孤儿桶）。游戏结束时由 _context_frozen 防止旧事件覆盖清空。
        self._game_state_context_id = "game-state"
        self._last_game_state_text: str = ""
        self._context_frozen = False  # 游戏结束后禁止推送

        # 聊天路由：记录每个入站聊天消息的 channel/target，
        # 使 _flush_idle_chat 能将 AIRI 回复发回正确的频道
        self._idle_chat_routes: Deque[Dict[str, Optional[str]]] = deque()
        self._idle_chat_lock = threading.Lock()

    @classmethod
    def _clean_selection_reply(cls, reply: str) -> str:
        """Remove only real leading timestamps from AIRI selection replies."""
        return cls._TIMESTAMP_RE.sub("", (reply or "").strip()).strip()

    @classmethod
    def _parse_selection_number(cls, reply: str) -> Optional[int]:
        """Parse a bare or bracketed 1-based selection number."""
        clean_reply = cls._clean_selection_reply(reply)
        bracketed = cls._BRACKETED_NUMBER_RE.fullmatch(clean_reply)
        if bracketed:
            return int(bracketed.group(1))
        try:
            return int(clean_reply)
        except ValueError:
            return None

    # ──────────────────────────────────────────
    #  AIRI context:update 通道
    # ──────────────────────────────────────────

    def _push_game_state(self, state_text: str):
        """通过 context:update 推送游戏状态，使 AIRI 有持久化的游戏背景。"""
        if not state_text:
            return
        if self._context_frozen:
            return  # 游戏已结束，不再推送
        self._last_game_state_text = state_text
        context = {
            "id": str(uuid.uuid4()),
            "contextId": self._game_state_context_id,
            "strategy": "replace-self",
            "text": state_text,
            "lane": "game-state",
        }
        self.airi.send_context(context)

    def _clear_airi_context(self):
        """清空AIRI的游戏状态上下文，用于游戏终止时。"""
        # 发送空文本的context:update，配合replace-self策略清空上下文
        context = {
            "id": str(uuid.uuid4()),
            "contextId": self._game_state_context_id,
            "strategy": "replace-self",
            "text": "游戏已结束。上下文已清空。",
            "lane": "game-state",
        }
        self.airi.send_context(context)
        log.info("已清空AIRI的游戏状态上下文")

    def _build_game_state_text(
        self, msg: dict, context: Dict[str, Any]
    ) -> str:
        """从 msg（含 hp/location）和 context（含 round/phase）构建游戏状态文本。
        同时附加最近的游戏事件摘要（最近 10 条）。
        """
        lines: List[str] = []
        round_no = context.get("round", "?")
        phase = context.get("phase", "")
        location = msg.get("location", "")
        hp = msg.get("hp")
        max_hp = msg.get("max_hp")

        lines.append(f"当前轮次：第 {round_no} 轮")
        if phase:
            lines.append(f"阶段：{phase}")
        if location:
            lines.append(f"当前位置：{location}")
        if hp is not None and max_hp is not None and max_hp != "":
            lines.append(f"生命值：{hp}/{max_hp}")

        # G2 ish-bosheth 舞台状态摘要
        gs = getattr(self, '_game_state', None)
        ish = getattr(gs, 'ish_bosheth', None) if gs else None
        if ish and ish.phase == "active":
            lines.append("")
            lines.append("【G2 舞台状态】")
            lines.append(f"- Regard: {ish.regard}/{ish.regard_cap}")
            if ish.before_light:
                bl_desc = {"riposato": "Riposato (入戏+0.5伤害，反抗-0.5伤害)",
                           "dolente": "Dolente (入戏+1.0, 抽离+0.5伤害)"}
                lines.append(f"- 光色: {bl_desc.get(ish.before_light, ish.before_light)}")
            # 我的情绪
            my_pid = msg.get("player_id", "")
            my_player = gs.get_player(my_pid) if gs and my_pid else None
            if my_player:
                from engine.ish_bosheth import EMOTION_LABELS
                my_emo = getattr(my_player, 'emotion', None)
                if my_emo and my_emo in EMOTION_LABELS:
                    lines.append(f"- 你的情绪: {EMOTION_LABELS[my_emo]}")
            # 场内玩家
            members = []
            for pid in ish.participants:
                p = gs.get_player(pid) if gs else None
                if p:
                    from engine.ish_bosheth import EMOTION_LABELS as _EL
                    emo_tag = _EL.get(getattr(p, 'emotion', None), "")
                    members.append(f"{p.name}[{emo_tag}]")
            for c in ish.chorus_list:
                if c.is_alive():
                    from engine.ish_bosheth import EMOTION_LABELS as _EL2
                    emo_tag = _EL2.get(getattr(c, 'emotion', None), "")
                    members.append(f"{c.name}[{emo_tag}]")
            if members:
                lines.append(f"- 场内: {', '.join(members)}")
            g2p = gs.get_player(ish.g2_owner_id) if gs else None
            if g2p:
                lines.append(f"- G2发动者: {g2p.name} (HP {g2p.hp}/{g2p.max_hp})")

        # 附加最近的事件（最多 10 条，最新的排在前面）
        if self.game_events:
            lines.append("")
            lines.append("【近期事件】")
            for ev in reversed(self.game_events[-10:]):
                lines.append(f"  · {ev}")

        return "\n".join(lines)

    # ──────────────────────────────────────────
    #  启动
    # ──────────────────────────────────────────

    def start(self):
        """启动 Bridge：连接游戏服务器和 AIRI。"""
        # 1. 连接 AIRI
        log.info(f"正在连接 AIRI: {self.config.get('airi_ws_url')}")
        self.airi.connect()
        log.info("AIRI 连接成功")

        # 2. 向 AIRI 发送角色设定
        self._send_role_setup()

        # 3. 连接游戏服务器
        host = self.config.get("game_server_host", "127.0.0.1")
        port = self.config.get("game_server_port", 9527)
        log.info(f"正在连接游戏服务器: {host}:{port}")
        self.game_client.connect(self.bot_name)
        log.info("游戏服务器连接成功")

        # 4. 注册游戏事件处理器
        self._register_handlers()

        # 5. 主循环
        self._main_loop()

    def _send_role_setup(self):
        """向 AIRI 发送游戏角色设定。"""
        setup_text = (
            "你现在正在参与一个叫《起闯战争》的回合制桌游。"
            "你的目标是成为最后存活的玩家。"
            "游戏中你需要做出行动决策（移动、攻击、交互等）和社交决策（聊天、结盟、欺骗等）。\n\n"
            "当我告诉你'轮到你行动了'时，请用以下格式回复你的行动：\n"
            "ACTION: <你的行动指令>\n"
            "例如：ACTION: move 商店\n"
            "例如：ACTION: attack 玩家A 小刀\n"
            "例如：ACTION: forfeit\n\n"
            "当我让你选择时，请用以下格式回复：\n"
            "CHOOSE: <选项编号>\n\n"
            "当我让你确认时，请用以下格式回复：\n"
            "CONFIRM: y 或 CONFIRM: n\n\n"
            "其他时候你可以自由聊天，你的聊天内容会被发送到游戏的公屏。"
        )
        self.airi.send_text(setup_text)
        # 等待 AIRI 处理角色设定（丢弃这次回复）
        self.airi.wait_for_response(timeout=15)
        self._drain_pending_airi_responses()  # 清空队列
        log.info("角色设定已发送")

    # ──────────────────────────────────────────
    #  游戏事件处理
    # ──────────────────────────────────────────

    def _register_handlers(self):
        """注册游戏服务器的消息处理器。"""
        self.game_client.on(MessageType.GAME_EVENT, self._on_game_event)
        self.game_client.on(MessageType.LOBBY_UPDATE, self._on_lobby_update)
        self.game_client.on(MessageType.CHAT_MESSAGE, self._on_chat_message)
        self.game_client.on(MessageType.DISCONNECT_NOTICE, self._on_disconnect)

        # 服务器请求 → pending_request
        for mt in (
            MessageType.REQUEST_COMMAND,
            MessageType.REQUEST_CHOOSE,
            MessageType.REQUEST_CHOOSE_MULTI,
            MessageType.REQUEST_CONFIRM,
        ):
            self.game_client.on(mt, self._make_request_handler(mt))

    def _make_request_handler(self, msg_type):
        def handler(msg):
            with self.pending_lock:
                self.pending_request["msg"] = msg
                self.pending_request["msg_type"] = msg_type
            self.pending_event.set()
        return handler

    def _on_game_event(self, msg):
        """处理游戏事件：记录日志 + 转发给 AIRI。"""
        event = msg.get("event", "")
        args = msg.get("args", [])

        # 构建人类可读的事件描述
        desc = self._format_event(event, args)
        if desc:
            self.game_events.append(desc)
            # 保留最近 50 条
            if len(self.game_events) > 50:
                self.game_events = self.game_events[-50:]
            # 转发给 AIRI
            self.airi.send_notify(desc)
            log.info(f"游戏事件: {desc[:80]}")

        # 重要事件时更新 game-state 上下文
        if event in ("show_round_header", "show_death"):
            events_summary = "\n".join(
                f"  · {ev}" for ev in self.game_events[-10:]
            )
            state_text = f"【游戏状态】\n近期事件：\n{events_summary}" if events_summary else ""
            if state_text:
                self._push_game_state(state_text)

        if event == "game_finished":
            self._context_frozen = True  # 冻结上下文，防止后续事件覆盖清空消息
            self.game_finished.set()
            self.pending_event.set()
            self._clear_airi_context()

    def _format_event(self, event: str, args: list) -> str:
        """将游戏事件格式化为自然语言。"""
        if event == "show_round_header":
            return f"=== 第 {args[0] if args else '?'} 轮 ==="
        if event == "show_phase":
            return f"--- {args[0] if args else ''} ---"
        if event == "show_action_turn_header":
            return f"轮到 {args[0] if args else '?'} 行动"
        if event == "show_result":
            return str(args[0]) if args else ""
        if event == "show_info":
            return str(args[0]) if args else ""
        if event == "show_death":
            name = args[0] if args else "?"
            cause = args[1] if len(args) > 1 else "未知"
            return f"{name} 死亡！原因：{cause}"
        if event == "show_victory":
            return f"{args[0] if args else '?'} 获得了最终胜利！"
        if event == "show_error":
            return f"[错误] {args[0] if args else ''}"
        return ""

    def _on_lobby_update(self, msg):
        state = msg.get("room_state", "")
        slots = msg.get("slots", [])
        bot_connected = any(s.get("player_name") == self.bot_name for s in slots)

        if bot_connected and not self.game_started.is_set():
            self.game_events.clear()
            log.info(f"进入大厅，contextId: {self._game_state_context_id}")

        if state == "in_game":
            log.info("游戏开始！")
            self._context_frozen = False
            self.game_started.set()
            self.airi.send_notify("游戏正式开始了！准备好战斗吧。")
            self._push_game_state("游戏已开始。等待第一个回合的行动请求。")
        for s in slots:
            log.info(
                f"  [{s.get('slot_id', '?')}] "
                f"{str(s.get('slot_type', '')):12s} | "
                f"{s.get('player_name', '空')}"
            )

    def _on_chat_message(self, msg):
        """收到聊天消息 → 转发给 AIRI（非阻塞）。
        AIRI 的聊天回复由 _flush_idle_chat 在主线循环中统一收取。
        """
        sender = msg.get("sender", "")
        content = msg.get("content", "")
        channel = msg.get("channel", "public")

        if sender == self.bot_name:
            return

        channel_label = "私聊" if channel == "private" else "公屏"
        text = f"（{channel_label}，来自 {sender}）: {content}"
        log.info(f"聊天: {text}")

        # 只转发给 AIRI，不等待回复——避免与行动请求的 wait_for_response 竞态
        with self._idle_chat_lock:
            self._idle_chat_routes.append({
                "channel": "private" if channel == "private" else "public",
                "target": sender if channel == "private" else None,
            })
        self.airi.send_text(text)

    def _on_disconnect(self, msg):
        name = msg.get("player_name", "")
        action = msg.get("action", "")
        log.info(f"断线通知: {name} {action}")
        self.airi.send_notify(f"玩家 {name} {action}")

    def _drain_pending_airi_responses(self) -> List[str]:
        """Drain queued AIRI replies and discard matching idle-chat routes.

        Chat messages are sent to AIRI asynchronously and their routes are
        queued separately. If a request prompt drains those replies before
        _flush_idle_chat sees them, the corresponding routes must be consumed
        too or later chat replies can be routed to the wrong channel/player.
        """
        responses = self.airi.drain_responses()
        if not responses:
            return responses

        with self._idle_chat_lock:
            for _ in range(min(len(responses), len(self._idle_chat_routes))):
                self._idle_chat_routes.popleft()

        return responses

    # ──────────────────────────────────────────
    #  主循环
    # ──────────────────────────────────────────

    def _flush_idle_chat(self):
        """处理 AIRI 的主动聊天（非请求触发的回复）。
        使用入站消息记录的路由信息，将回复发回正确的频道。
        """
        for reply in self.airi.drain_responses():
            # Every AIRI response corresponds to one queued chat route, even
            # COMMAND replies that are not sent back as chat messages.
            with self._idle_chat_lock:
                route = (
                    self._idle_chat_routes.popleft()
                    if self._idle_chat_routes
                    else {"channel": "public", "target": None}
                )
            if reply.startswith("COMMAND:"):
                continue
            clean = self._FORMAT_CMD_RE.sub("", reply).strip()
            if clean:
                chat_msg = {
                    "type": MessageType.CHAT_SEND,
                    "sender": self.bot_name,
                    "content": clean,
                    "channel": route["channel"],
                }
                if route["channel"] == "private" and route["target"]:
                    chat_msg["target"] = route["target"]
                self.game_client.send_sync(chat_msg)

    def _main_loop(self):
        """主循环：等待游戏开始，然后处理服务器请求。"""
        log.info("等待游戏开始...")

        try:
            while not self.game_started.is_set():
                if self.game_started.wait(timeout=0.5):
                    break
                # 处理可能在游戏正式开始前到达的请求（如天赋选择）
                with self.pending_lock:
                    req_msg = self.pending_request["msg"]
                    req_type = self.pending_request["msg_type"]
                    self.pending_request["msg"] = None
                    self.pending_request["msg_type"] = None
                if req_msg is not None:
                    self._handle_request(req_msg, req_type)
                    self.pending_event.clear()
                self._flush_idle_chat()

            log.info("游戏已开始，进入主循环")

            while self.game_client.is_connected and not self.game_finished.is_set():
                # 检查挂起的请求
                with self.pending_lock:
                    req_msg = self.pending_request["msg"]
                    req_type = self.pending_request["msg_type"]
                    self.pending_request["msg"] = None
                    self.pending_request["msg_type"] = None

                if req_msg is not None:
                    self._handle_request(req_msg, req_type)
                    self.pending_event.clear()
                    continue

                # 等待下一个请求
                self.pending_event.wait(timeout=1.0)
                if self.pending_event.is_set():
                    self.pending_event.clear()
                    continue

                # 处理 AIRI 的主动聊天
                self._flush_idle_chat()

        except KeyboardInterrupt:
            log.info("Bridge 被中断")
        finally:
            self.game_client.disconnect()
            log.info("已断开连接")

    # ──────────────────────────────────────────
    #  请求分发
    # ──────────────────────────────────────────

    def _handle_request(self, msg: dict, msg_type):
        """处理服务器的请求：转发给 AIRI，解析回复，发送响应。"""
        if msg_type == MessageType.REQUEST_COMMAND:
            self._handle_command_request(msg)
        elif msg_type == MessageType.REQUEST_CHOOSE:
            self._handle_choose_request(msg)
        elif msg_type == MessageType.REQUEST_CHOOSE_MULTI:
            self._handle_choose_multi_request(msg)
        elif msg_type == MessageType.REQUEST_CONFIRM:
            self._handle_confirm_request(msg)

    def _handle_command_request(self, msg: dict):
        """处理行动请求：分层枚举方案（两阶段交互）。

        第一阶段：AIRI 从高层类别中选择指令类型。
        第二阶段：根据选择的类型枚举合法参数组合让 AIRI 选择。

        若分层枚举失败（超时/无法解析），回退到传统的单次 prompt + 重试。
        """
        actions = msg.get("available_actions", [])
        context = msg.get("context", {})

        # 推送游戏状态
        state_text = self._build_game_state_text(msg, context)
        self._push_game_state(state_text)

        # 提取可用 action 前缀集合（用于第二阶段参数枚举过滤）
        action_prefixes = self._extract_action_prefixes(actions)

        log.info(f"请求行动: 可选 {actions}")

        # ── 第一阶段：选择指令类型 ──
        action_type = self._ask_action_type(actions, context)
        if not action_type:
            log.warning("分层枚举第一阶段失败，回退到传统重试模式")
            command = self._try_get_command_with_retry(msg, actions, context)
            if not command:
                command = self._smart_fallback_command(msg, actions, context)
                log.warning(f"AIRI 无有效回复，使用智能 fallback: {command}")
            self.game_client.send_sync({
                "type": MessageType.COMMAND_RESPONSE,
                "command": command,
            })
            return

        log.info(f"AIRI 选择了指令类型: {action_type}")

        # ── 第二阶段：枚举参数并选择 ──
        command = self._ask_action_parameters(
            action_type, msg, context, action_prefixes
        )
        if not command:
            # 第二阶段失败：回退到传统重试
            log.warning(
                f"分层枚举第二阶段失败（类型={action_type}），"
                "回退到传统重试模式"
            )
            command = self._try_get_command_with_retry(msg, actions, context)
            if not command:
                command = self._smart_fallback_command(msg, actions, context)
                log.warning(f"AIRI 无有效回复，使用智能 fallback: {command}")

        self.game_client.send_sync({
            "type": MessageType.COMMAND_RESPONSE,
            "command": command,
        })

    # ── 分层枚举辅助方法 ──

    def _ask_action_type(
        self, actions: List[Any], context: Dict[str, Any]
    ) -> Optional[str]:
        """第一阶段：让 AIRI 从高层类别中选择指令类型。

        将 actions 聚合为去重后的指令前缀列表，加上战略意图说明，
        让 AIRI 选择。返回选中的指令前缀（如 "move", "interact"），
        失败返回 None。
        """
        # 从 actions 中提取去重的指令前缀，保持顺序
        prefixes = self._extract_action_prefixes(actions)
        if not prefixes:
            log.warning("没有可用的行动前缀")
            return None

        # 构建选项列表
        action_lines = []
        for i, prefix in enumerate(prefixes, 1):
            intent = CommandIntentExplainer.explain(prefix)
            if intent:
                action_lines.append(f"{i}. {prefix} — {intent[:80]}")
            else:
                action_lines.append(f"{i}. {prefix}")

        prompt = (
            "轮到你行动了！请先从以下类型中选择一个：\n\n"
            "【可选指令类型】\n" + "\n".join(action_lines) + "\n\n"
            "请只回复数字或指令名称（例如：1 或 move）"
        )

        self._drain_pending_airi_responses()
        self.airi.send_text(prompt)
        log.info(f"第一阶段 prompt 已发送，可选前缀: {prefixes}")

        reply = self.airi.wait_for_response(timeout=self.action_timeout)
        if not reply:
            log.warning("第一阶段：AIRI 超时未回复")
            return None

        log.info(f"第一阶段 AIRI 原始回复: {reply[:200]}")

        # 尝试解析为数字
        selection_number = self._parse_selection_number(reply)
        if selection_number is not None:
            idx = selection_number - 1
            if 0 <= idx < len(prefixes):
                return prefixes[idx]

        # 尝试匹配指令名称
        reply_lower = self._clean_selection_reply(reply).lower()
        for prefix in prefixes:
            if prefix.lower() in reply_lower:
                return prefix

        log.warning(f"第一阶段：无法从回复中解析指令类型: {reply[:100]}")
        return None

    def _ask_action_parameters(
        self,
        action_type: str,
        msg: dict,
        context: Dict[str, Any],
        action_prefixes: List[str],
    ) -> Optional[str]:
        """第二阶段：根据 action_type 枚举合法参数组合，让 AIRI 选择。

        返回完整指令字符串（如 "move 商店", "interact 打工"），
        失败返回 None。
        """
        # 按类型分发到各自的枚举方法
        options = self._enumerate_params_for_type(
            action_type, msg, context, action_prefixes
        )

        if not options:
            # 该类型在 _BARE_OK_PREFIXES 中 → 无需参数，直接返回
            if action_type in self._BARE_OK_PREFIXES:
                log.info(f"第二阶段：{action_type} 无需参数枚举，直接返回")
                return action_type
            # 需要参数但无可用选项（如无目标可 lock/find）→ 回退失败
            log.warning(
                f"第二阶段：{action_type} 需要参数但服务端未提供可用选项，回退"
            )
            return None

        # 构建选项列表
        option_lines = []
        for i, opt in enumerate(options, 1):
            option_lines.append(f"{i}. {opt}")

        prompt = (
            f"你选择了「{action_type}」。请选择具体参数：\n\n"
            "【可选参数】\n" + "\n".join(option_lines) + "\n\n"
            "请只回复数字或完整指令（例如：1）"
        )

        self._drain_pending_airi_responses()
        self.airi.send_text(prompt)
        log.info(
            f"第二阶段 prompt 已发送，类型={action_type}，选项数={len(options)}"
        )

        reply = self.airi.wait_for_response(timeout=self.action_timeout)
        if not reply:
            log.warning(f"第二阶段：AIRI 超时未回复（类型={action_type}）")
            return None

        log.info(f"第二阶段 AIRI 原始回复: {reply[:200]}")

        # 尝试解析为数字
        selection_number = self._parse_selection_number(reply)
        if selection_number is not None:
            idx = selection_number - 1
            if 0 <= idx < len(options):
                return options[idx]

        # 尝试模糊匹配选项文本
        reply_lower = self._clean_selection_reply(reply).lower()
        for opt in options:
            if opt.lower() in reply_lower:
                return opt

        log.warning(
            f"第二阶段：无法从回复中解析参数（类型={action_type}）: {reply[:100]}"
        )
        return None

    def _enumerate_params_for_type(
        self,
        action_type: str,
        msg: dict,
        context: Dict[str, Any],
        action_prefixes: List[str],
    ) -> List[str]:
        """根据指令类型枚举所有合法的参数组合。

        优先从服务端预枚举的 context.action_options 中读取（这是
        engine/action_enumerator.py 生成的精确选项）。
        如果服务端未提供（向后兼容），回退到从 available_actions 过滤。

        返回格式化的指令字符串列表（如 ["move 商店", "move 医院"]）。
        如果该类型不需要参数或无可选项，返回空列表。
        """
        # ── 优先：服务端预枚举的参数化选项 ─────────────────────────
        action_options: Dict[str, List[str]] = (context or {}).get("action_options", {})
        if action_type in action_options:
            opts = action_options[action_type]
            if opts:
                return list(opts)
            # 服务端说该类型无可用参数组合 → 返回空，由调用方处理回退
            return []

        # ── 回退：从 available_actions 过滤（向后兼容旧版服务端）───
        all_actions = msg.get("available_actions", [])

        # 将 actions 标准化为字符串列表
        action_strings: List[str] = []
        for a in all_actions:
            if isinstance(a, dict):
                usage = a.get("usage", "")
                if usage:
                    action_strings.append(usage.strip())
            elif isinstance(a, str):
                action_strings.append(a.strip())

        # 筛选：匹配前缀且在合法前缀列表中
        prefix_lower = action_type.lower().strip()
        matched: List[str] = []
        seen = set()
        for cmd in action_strings:
            cmd_lower = cmd.lower()
            if cmd_lower.startswith(prefix_lower + " ") or cmd_lower == prefix_lower:
                norm = self._normalize_param_option(cmd)
                if norm not in seen:
                    seen.add(norm)
                    matched.append(cmd)

        return matched

    @staticmethod
    def _normalize_param_option(cmd: str) -> str:
        """归一化参数选项用于去重。"""
        return cmd.strip().lower()

    # ──────────────────────────────────────────
    #  Prompt 构建 / 多层重试 / 智能 fallback
    # ──────────────────────────────────────────

    def _build_command_prompt(
        self,
        msg: dict,
        actions: List[Any],
        context: Dict[str, Any],
        attempt: int,
    ) -> str:
        """构建行动 prompt。attempt=0 是首次，>=1 是重试（更明确）。

        字段布局（与 NetworkController.get_command 一致）：
        - 顶层 msg：hp / max_hp / location / player_name / available_actions
        - 嵌套 context：phase / round / attempt 等
        因此生命/位置从 msg 读，轮次/阶段从 context 读。
        """
        action_lines = []
        for action in actions:
            if isinstance(action, dict):
                usage = action.get("usage", "")
                name = action.get("name", "")
                desc = action.get("description", "")
                if usage:
                    line = f"- {usage}"
                    if name and name not in usage:
                        line += f"（{name}）"
                    if desc:
                        line += f" — {desc}"
                    action_lines.append(line)
            else:
                action_lines.append(f"- {action}")

        actions_text = "\n".join(action_lines) if action_lines else "(无可用行动)"
        intent_block = CommandIntentExplainer.build_intent_block(actions)

        # 复用 _build_game_state_text 确保 context:update 与 action prompt 一致
        situation = self._build_game_state_text(msg, context)
        if not situation:
            # 回退：确保有基本信息
            round_no = context.get("round", "?")
            phase = context.get("phase", "")
            location = msg.get("location", "")
            hp = msg.get("hp")
            max_hp = msg.get("max_hp")
            lines = [f"当前轮次：第 {round_no} 轮"]
            if phase:
                lines.append(f"阶段：{phase}")
            if location:
                lines.append(f"你的位置：{location}")
            if hp is not None and max_hp is not None and max_hp != "":
                lines.append(f"生命值：{hp}/{max_hp}")
            situation = "\n".join(lines)

        if attempt == 0:
            header = "轮到你行动了！请基于游戏状态选择最合适的行动。"
            tail = (
                "请用以下格式回复（必须以 ACTION: 开头，否则会被忽略）：\n"
                "ACTION: <完整指令>\n"
                "例如：ACTION: move 商店\n"
                "例如：ACTION: attack 玩家A 小刀 外层 普通\n"
                "例如：ACTION: forfeit"
            )
        elif attempt == 1:
            header = (
                "⚠️ 上一次回复无法解析。请严格遵守格式要求重新决策。"
            )
            tail = (
                "必须回复一行以 ACTION: 开头的指令，紧跟一个合法行动前缀。\n"
                "合法前缀只能是：move / attack / interact / lock / find / "
                "forfeit / wake / report / assemble / track / recruit / "
                "election / designate / study / special / split / police。\n"
                "示例：ACTION: forfeit"
            )
        else:
            header = (
                "⚠️ 仍然无法识别你的行动。请只输出一行内容，不要解释、"
                "不要思考、不要 [THINK]/[REPLY]/[ADJUST] 段。"
            )
            tail = (
                "只回复一行，例如：ACTION: forfeit\n"
                "或：ACTION: move <地点>\n"
                "否则系统会自动为你选一个稳妥的默认行动。"
            )

        parts = [header, "", "【当前状况】", situation, "", "【可选行动】", actions_text]
        if intent_block:
            parts.extend(["", "【指令战略意图】", intent_block])

        # 行动限制提示（来源：NetworkController.get_command 中按天赋状态注入）
        restrictions = (context or {}).get("action_restrictions") or {}
        restriction_lines: List[str] = []
        if restrictions.get("move_disabled"):
            restriction_lines.append(
                "- 你目前不能 move（"
                f"{restrictions.get('reason', '当前天赋状态限制')}）。"
            )
        if restrictions.get("interact_disabled"):
            restriction_lines.append(
                "- 你目前不能 interact（"
                f"{restrictions.get('reason', '当前天赋状态限制')}）。"
            )
        if restrictions.get("supernova_available"):
            restriction_lines.append(
                "- 你拥有 G1 火萤的『超新星过载』：下一次 move 可指定目的地"
                "为当前地点（原地触发），对当地所有单位造成 1 点无视克制伤害"
                "并施加灼烧；触发后失熵症 debuff 后延 3 轮。"
            )
        if restrictions.get("tactical_macro_mode"):
            restriction_lines.append(
                "- 你目前处于 G7 战术宏模式：请从 BasicAI 预制宏中选择，"
                "不要自主组合宏序列。预制宏包括：基础攻击宏 / 反队长接近宏 / "
                "反队长无盾宏 / 补刀+转火宏 / 全力射击宏。"
            )
        if restriction_lines:
            parts.extend(["", "【行动限制】"] + restriction_lines)

        parts.extend(["", tail])
        return "\n".join(parts)

    def _try_get_command_with_retry(
        self,
        msg: dict,
        actions: List[Any],
        context: Dict[str, Any],
        max_attempts: int = 3,
    ) -> Optional[str]:
        """多层重试机制：尝试解析 AIRI 的行动回复，失败时使用渐进式提示重试。

        返回解析成功的指令字符串；全部失败时返回 None，由调用方触发
        智能 fallback。
        """
        action_prefixes = self._extract_action_prefixes(actions)

        for attempt in range(max_attempts):
            prompt = self._build_command_prompt(msg, actions, context, attempt)
            self._drain_pending_airi_responses()
            self.airi.send_text(prompt)

            reply = self.airi.wait_for_response(timeout=self.action_timeout)
            if not reply:
                log.warning(f"AIRI 第 {attempt + 1} 次尝试超时未回复")
                continue

            log.info(f"AIRI 第 {attempt + 1} 次原始回复: {reply[:200]}")
            parsed = ResponseParser.extract_action(reply, action_prefixes)
            if parsed:
                intent = CommandIntentExplainer.explain(parsed)
                if intent:
                    log.info(f"解析出行动: {parsed}（意图：{intent[:60]}）")
                else:
                    log.info(f"解析出行动: {parsed}")
                return parsed

            log.warning(
                f"第 {attempt + 1} 次无法解析行动。原始回复: {reply[:200]}"
            )

        return None

    def _extract_action_prefixes(self, actions: List[Any]) -> List[str]:
        """从 actions（可能是 str 或 dict 列表）中抽取可用指令前缀。"""
        prefixes: List[str] = []
        for action in actions:
            if isinstance(action, dict):
                usage = action.get("usage", "")
                if usage:
                    prefix = usage.strip().split()[0]
                    if prefix and prefix not in prefixes:
                        prefixes.append(prefix)
            elif isinstance(action, str):
                prefix = action.strip().split()[0]
                if prefix and prefix not in prefixes:
                    prefixes.append(prefix)
        return prefixes

    # 这些指令前缀在 cli/parser.py 中允许「无参数」直接解析成功；fallback 只能从
    # 这个集合里挑「裸前缀」回复，否则游戏服务器会因 "len(parts) < 2" 而拒绝指令。
    _BARE_OK_PREFIXES = frozenset({
        "forfeit",
        "wake",
        "assemble",
        "track",       # parser 接受 track，并映射到 track_guide
        "recruit",
        "election",
        "study",
    })

    def _smart_fallback_command(
        self,
        msg: dict,
        actions: List[Any],
        context: Dict[str, Any],
    ) -> str:
        """智能 fallback：在 AIRI 全部回复都无法解析时，根据当前上下文
        选择一个相对稳妥的默认行动，而不是无脑 forfeit 卡死局面。

        关键约束：服务器解析器要求 move/interact/attack/lock/find/report/designate
        /special/split/police_command/wake_police 等指令必须带参数，否则会被
        直接拒绝。因此 fallback 只能返回：
          - 一个完整的「带参指令」（例如 "move 医院"），或
          - 一个属于 _BARE_OK_PREFIXES 的无参指令（forfeit / wake / ...）。

        优先级：
        1. 若尚未起床：wake（起床），让自己进入可行动状态。
        2. 若血量低且可以 move：move 医院（带参，安全）。
        3. 若在补给/强化型地点且可以 interact：interact <默认项目>（带参）。
        4. 若 _BARE_OK_PREFIXES 中存在唯一非 forfeit 的可选项：直接选它。
        5. 否则 forfeit。

        hp / max_hp / location 从顶层 msg 读取（NetworkController 在那里下发），
        不是从 context。
        """
        prefixes = self._extract_action_prefixes(actions)
        location = msg.get("location", "")
        hp = msg.get("hp")
        max_hp = msg.get("max_hp")

        if "wake" in prefixes:
            return "wake"

        # 血量危险且可移动：去医院（带参，安全）
        try:
            if (hp is not None and max_hp not in (None, 0)
                    and float(hp) / float(max_hp) <= 0.4
                    and "move" in prefixes
                    and location != "医院"):
                return "move 医院"
        except (TypeError, ValueError):
            pass

        # 在补给/强化型地点且可 interact：用一个该地点的默认交互项目
        # （bare "interact" 会被 cli/parser.py:38-39 拒绝）。
        if "interact" in prefixes:
            default_item = self._default_interact_item_for(location)
            if default_item:
                return f"interact {default_item}"

        # 在 _BARE_OK_PREFIXES 内挑一个唯一的非 forfeit 选项；带参指令不能裸返回。
        bare_ok_non_forfeit = [
            p for p in prefixes
            if p != "forfeit" and p in self._BARE_OK_PREFIXES
        ]
        if len(bare_ok_non_forfeit) == 1:
            return bare_ok_non_forfeit[0]

        if "forfeit" in prefixes:
            return "forfeit"
        # 走到这里说明服务器没下发 forfeit（极少见，比如 wake-only 状态），
        # 此时只能从允许裸返回的前缀里硬挑一个，避免发出会被拒绝的带参指令。
        if bare_ok_non_forfeit:
            return bare_ok_non_forfeit[0]
        return "forfeit"

    @staticmethod
    def _default_interact_item_for(location: str) -> str:
        """给 interact 选一个该地点最可能成功的默认项目；未知地点返回空串。"""
        # 仅给出最稳妥的兜底；不试图穷举所有项目，避免误用罕见交互。
        defaults = {
            "医院": "打工",
            "商店": "打工",
            "军事基地": "办理通行证",
            "魔法所": "魔法护盾",
        }
        return defaults.get((location or "").strip(), "")

    def _handle_choose_request(self, msg: dict):
        """处理选择请求：识别 situation 注入战略意图说明 + 渐进式提示 + 强健 fallback。

        支持的特殊 situation：
          - "talent_t0"：是否发动天赋；解释具体天赋的战略含义
          - 任意 TALENT_SUB_DECISION_INTENT 中的 key：注入子决策意图
        其他 situation：走通用 choose 流程，但仍尽量加上「选项即指令前缀」时的意图说明。
        """
        prompt_text = msg.get("prompt", "请选择")
        options = msg.get("options", [])
        context = msg.get("context", {}) or {}
        situation = context.get("situation", "")

        if not options:
            log.warning("choose 请求选项为空，回复空串")
            self.game_client.send_sync({
                "type": MessageType.CHOOSE_RESPONSE,
                "choice": "",
            })
            return

        log.info(f"请求选择: situation={situation!r} options={options}")
        intent_block = self._build_choose_intent_block(
            situation, options, context, prompt_text
        )
        choice: Optional[str] = None

        for attempt in range(2):
            options_block = "\n".join(
                f"  {i}. {opt}" for i, opt in enumerate(options, 1)
            )
            if attempt == 0:
                parts = [prompt_text]
                if intent_block:
                    parts.extend(["", intent_block])
                parts.extend([
                    "",
                    "【可选项】",
                    options_block,
                    "",
                    "请用 CHOOSE: <编号> 的格式回复（编号从 1 开始）。",
                ])
                text = "\n".join(parts)
            else:
                text = (
                    "⚠️ 上一次回复无法解析为选项编号。请只回复一行：\n"
                    f"CHOOSE: <1~{len(options)}>\n\n"
                    f"{prompt_text}\n"
                    f"{options_block}"
                )

            self._drain_pending_airi_responses()
            self.airi.send_text(text)
            reply = self.airi.wait_for_response(timeout=self.action_timeout)
            if not reply:
                log.warning(f"choose 第 {attempt + 1} 次超时未回复")
                continue
            log.info(f"AIRI 第 {attempt + 1} 次原始回复: {reply[:200]}")
            parsed = ResponseParser.extract_choice(reply, options)
            if parsed:
                choice = parsed
                log.info(f"解析出选择: {choice}")
                break
            log.warning(f"第 {attempt + 1} 次无法解析选择")

        if choice is None:
            choice = self._smart_fallback_choice(situation, options, context)
            log.warning(
                f"choose 全部尝试失败，使用 fallback: {choice}"
                f"（situation={situation!r}）"
            )

        self.game_client.send_sync({
            "type": MessageType.CHOOSE_RESPONSE,
            "choice": choice,
        })

    # ──────────────────────────────────────────
    #  choose 战略意图 / fallback 辅助
    # ──────────────────────────────────────────

    def _build_choose_intent_block(
        self,
        situation: str,
        options: List[str],
        context: Dict[str, Any],
        prompt_text: str,
    ) -> str:
        """根据 situation 构建 choose 的战略意图说明块。

        - talent_t0：基于 TALENT_T0_INTENT_MAP[talent_name] 给出发动建议
        - 任意 TALENT_SUB_DECISION_INTENT[situation]：给出子决策意图说明
        - 其他 situation：若选项本身是 INTENT_MAP 的指令前缀，附加意图说明
        """
        lines: List[str] = []

        if situation == "talent_t0":
            talent_name = (context or {}).get("talent_name", "") or ""
            talent_desc = (context or {}).get("talent_desc", "") or ""
            info = TALENT_T0_INTENT_MAP.get(talent_name, {})
            lines.append(f"【天赋 T0 决策】当前天赋：{talent_name or '未知'}")
            if talent_desc:
                lines.append(f"  原始描述：{talent_desc}")
            if info:
                lines.append(f"  战略意图：{info.get('intent', '')}")
                expl = info.get("explanation", "")
                if expl:
                    lines.append(f"  解释：{expl}")
                trig = info.get("trigger_condition", "")
                if trig:
                    lines.append(f"  推荐触发条件：{trig}")
                val = info.get("strategic_value", "")
                if val:
                    lines.append(f"  战略价值：{val}")
            else:
                lines.append(
                    "  （没有内置该天赋的意图说明，请严格依据原始描述与当前局势判断。）"
                )
            lines.append("一般来说，选项形如 [发动天赋, 不发动，正常行动]。")
            return "\n".join(lines)

        sub_info = TALENT_SUB_DECISION_INTENT.get(situation)
        if sub_info:
            lines.append(f"【子决策意图】situation = {situation}")
            lines.append(f"  战略意图：{sub_info.get('intent', '')}")
            expl = sub_info.get("explanation", "")
            if expl:
                lines.append(f"  解释：{expl}")
            sug = sub_info.get("suggestion", "")
            if sug:
                lines.append(f"  参考建议：{sug}")
            return "\n".join(lines)

        # 通用：若选项看起来是指令前缀（如「move 商店」/「forfeit」），
        # 顺手拼接 INTENT_MAP 说明，帮助 AIRI 理解选项含义。
        block = CommandIntentExplainer.build_intent_block(options)
        if block:
            return f"【选项战略意图】\n{block}"

        return ""

    @staticmethod
    def _smart_fallback_choice(
        situation: str,
        options: List[str],
        context: Dict[str, Any],
    ) -> str:
        """为 choose 失败时挑一个相对安全的选项。

        - talent_t0：保守起见默认不发动（选含「不发动」字样的项；找不到则首项）
        - hexagram_my_choice / hexagram_opp_choice：随机一项（用首项的稳定 fallback）
        - 其他 situation：首个选项
        """
        if not options:
            return ""

        if situation == "talent_t0":
            for opt in options:
                if isinstance(opt, str) and ("不发动" in opt or "否" == opt):
                    return opt
            return options[0]

        # 出拳：直接返回首项（保持确定性，便于复现）；
        # 若需要更真实的随机出拳，可改成 random.choice。
        if situation in ("hexagram_my_choice", "hexagram_opp_choice"):
            return options[0]

        # G2 ish-bosheth 情绪选择：默认抽离（最安全）
        if situation == "g2_emotion_choice":
            for opt in options:
                if isinstance(opt, str) and ("抽离" in opt or "Indifferenza" in opt):
                    return opt
            return options[1] if len(options) > 1 else options[0]

        return options[0]

    def _handle_choose_multi_request(self, msg: dict):
        """处理多选请求。"""
        prompt_text = msg.get("prompt", "请选择")
        options = msg.get("options", [])
        max_count = msg.get("max_count", 1)
        min_count = msg.get("min_count", 0)

        text = f"{prompt_text} (选 {min_count}~{max_count} 个)\n"
        for i, opt in enumerate(options, 1):
            text += f"  {i}. {opt}\n"
        text += "请用 CHOOSE: <编号1>,<编号2> 的格式回复（逗号分隔）。"

        self._drain_pending_airi_responses()
        self.airi.send_text(text)
        log.info(f"请求多选: {options}")

        selected: List[str] = []
        reply = self.airi.wait_for_response(timeout=self.action_timeout)
        if reply:
            log.info(f"AIRI 原始回复: {reply[:200]}")
            # 提取所有数字
            numbers = re.findall(r"\d+", reply)
            for n in numbers:
                try:
                    idx = int(n) - 1
                except ValueError:
                    continue
                if 0 <= idx < len(options) and options[idx] not in selected:
                    selected.append(options[idx])
                    if len(selected) >= max_count:
                        break

        # 不足 min_count 时尝试用前 N 个选项补齐，避免服务器超时
        if len(selected) < min_count:
            for opt in options:
                if opt not in selected:
                    selected.append(opt)
                    if len(selected) >= min_count:
                        break

        self.game_client.send_sync({
            "type": MessageType.CHOOSE_MULTI_RESPONSE,
            "choices": selected,
        })

    def _handle_confirm_request(self, msg: dict):
        """处理确认请求：渐进式提示 + 安全 fallback（默认拒绝）。"""
        prompt_text = msg.get("prompt", "确认？")
        log.info(f"请求确认: {prompt_text}")

        result: Optional[bool] = None

        for attempt in range(2):
            if attempt == 0:
                text = (
                    f"{prompt_text}\n"
                    "请用 CONFIRM: y（同意）或 CONFIRM: n（拒绝）回复。"
                )
            else:
                text = (
                    "⚠️ 上一次回复无法识别为是/否。请只回复一行：\n"
                    "CONFIRM: y  或  CONFIRM: n\n\n"
                    f"问题：{prompt_text}"
                )

            self._drain_pending_airi_responses()
            self.airi.send_text(text)
            reply = self.airi.wait_for_response(timeout=self.action_timeout)
            if not reply:
                log.warning(f"confirm 第 {attempt + 1} 次超时未回复")
                continue
            log.info(f"AIRI 第 {attempt + 1} 次原始回复: {reply[:200]}")
            parsed = ResponseParser.extract_confirm(reply)
            if parsed is not None:
                result = parsed
                log.info(f"解析出确认: {result}")
                break
            log.warning(f"第 {attempt + 1} 次无法解析确认")

        if result is None:
            # 安全 fallback：未确认则视为拒绝，避免误触发不可逆操作
            result = False
            log.warning("confirm 全部尝试失败，使用安全默认值: False（拒绝）")

        self.game_client.send_sync({
            "type": MessageType.CONFIRM_RESPONSE,
            "result": result,
        })


# ══════════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AIRI Bot Bridge - 起闯战争")
    parser.add_argument(
        "--config", type=str, default="config/airi_bridge_config.json",
        help="配置文件路径",
    )
    parser.add_argument("--name", type=str, default=None, help="Bot 名称（覆盖配置文件）")
    parser.add_argument("--debug", action="store_true", help="启用调试日志")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # 加载配置
    try:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        log.error(f"配置文件不存在: {args.config}")
        log.info(
            "请复制 config/airi_bridge_config.example.json "
            "为 config/airi_bridge_config.json 并修改"
        )
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error(f"配置文件格式错误: {e}")
        sys.exit(1)

    if args.name:
        config["bot_name"] = args.name

    print(f"\n  ═══════════════════════════════════════")
    print(f"    起闯战争 - AIRI Bot Bridge")
    print(f"  ═══════════════════════════════════════")
    print(f"  AIRI:    {config.get('airi_ws_url', 'ws://localhost:6121/ws')}")
    print(
        f"  游戏服务器: "
        f"{config.get('game_server_host', '127.0.0.1')}:"
        f"{config.get('game_server_port', 9527)}"
    )
    print(f"  Bot 名称: {config.get('bot_name', 'AIRI_Bot')}")
    print()

    bridge = BotBridge(config)
    try:
        bridge.start()
    except ConnectionError as e:
        log.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
