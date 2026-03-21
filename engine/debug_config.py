"""
调试配置模块
═══════════════════════════════════════════════════
提供统一的调试输出控制，支持调试模式开关。
已集成到提示管理系统，调试文本可从data/prompts.json中修改。
"""

import sys
from typing import Any, Optional

# 导入提示管理器
from engine.prompt_manager import prompt_manager

# ============================================================================
# 全局调试配置
# ============================================================================

class DebugConfig:
    """调试配置单例类"""
    _instance = None
    _debug_mode = False
    _debug_level = 1  # 1=基本调试，2=详细调试，3=所有调试

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DebugConfig, cls).__new__(cls)
        return cls._instance

    @classmethod
    def set_debug_mode(cls, enabled: bool, level: int = 1):
        """设置调试模式"""
        cls._debug_mode = enabled
        cls._debug_level = level

    @classmethod
    def is_debug_enabled(cls) -> bool:
        """检查调试模式是否启用"""
        return cls._debug_mode

    @classmethod
    def get_debug_level(cls) -> int:
        """获取调试级别"""
        return cls._debug_level

    @classmethod
    def should_show(cls, min_level: int = 1) -> bool:
        """检查是否应该显示指定级别的调试信息"""
        return cls._debug_mode and cls._debug_level >= min_level

# ============================================================================
# 调试输出函数（使用提示管理器）
# ============================================================================

def debug_print(message: str, min_level: int = 1, prefix: str = "🤖", **kwargs):
    """
    统一的调试输出函数

    Args:
        message: 调试信息
        min_level: 最小调试级别（1-3），级别越高信息越详细
        prefix: 调试信息前缀，默认为"🤖"
        **kwargs: 传递给print的其他参数
    """
    if DebugConfig.should_show(min_level):
        formatted_message = f"{prefix} {message}" if prefix else message
        print(formatted_message, **kwargs)

def debug_ai(player_name: str, message: str, min_level: int = 1, **kwargs):
    """
    AI专用调试输出

    Args:
        player_name: AI玩家名称
        message: 调试信息
        min_level: 最小调试级别
        **kwargs: 传递给print的其他参数
    """
    if DebugConfig.should_show(min_level):
        # 尝试从提示管理器获取模板，如果不存在则使用默认
        template = prompt_manager.get_prompt(
            "debug", "ai.basic",
            default="🤖 [{player_name}] {message}"
        )
        formatted_message = template.format(
            player_name=player_name, message=message
        )
        print(formatted_message, **kwargs)

def debug_system(message: str, min_level: int = 1, **kwargs):
    """
    系统专用调试输出

    Args:
        message: 调试信息
        min_level: 最小调试级别
        **kwargs: 传递给print的其他参数
    """
    if DebugConfig.should_show(min_level):
        # 尝试从提示管理器获取模板
        template = prompt_manager.get_prompt(
            "debug", "system.basic",
            default="🔧 {message}"
        )
        formatted_message = template.format(message=message)
        print(formatted_message, **kwargs)

def debug_warning(message: str, **kwargs):
    """
    警告调试输出（始终显示，即使调试模式关闭）

    Args:
        message: 警告信息
        **kwargs: 传递给print的其他参数
    """
    # 使用提示管理器的警告显示，确保与其他警告统一
    prompt_manager.show_important("debug", "warning", message=message, **kwargs)

def debug_error(message: str, **kwargs):
    """
    错误调试输出（始终显示，即使调试模式关闭）

    Args:
        message: 错误信息
        **kwargs: 传递给print的其他参数
    """
    # 使用提示管理器的错误显示，确保与其他错误统一
    prompt_manager.show_critical("debug", "error", message=message, **kwargs)

def debug_info(message: str, **kwargs):
    """
    重要信息调试输出（始终显示，即使调试模式关闭）

    Args:
        message: 信息
        **kwargs: 传递给print的其他参数
    """
    # 使用提示管理器的信息显示
    prompt_manager.show_normal("debug", "info", message=message, **kwargs)

# ============================================================================
# 详细的AI调试函数（支持不同级别）
# ============================================================================

def debug_ai_basic(player_name: str, message: str, **kwargs):
    """AI基本调试输出（级别1）"""
    debug_ai(player_name, message, min_level=1, **kwargs)

def debug_ai_detailed(player_name: str, message: str, **kwargs):
    """AI详细调试输出（级别2）"""
    if DebugConfig.should_show(2):
        # 尝试获取详细调试模板
        template = prompt_manager.get_prompt(
            "debug", "ai.detailed.generic",
            default="🤖 [{player_name}] {message}"
        )
        formatted_message = template.format(
            player_name=player_name, message=message
        )
        print(formatted_message, **kwargs)

def debug_ai_full(player_name: str, message: str, **kwargs):
    """AI完整调试输出（级别3）"""
    if DebugConfig.should_show(3):
        # 尝试获取完整调试模板
        template = prompt_manager.get_prompt(
            "debug", "ai.full.generic",
            default="🤖 [{player_name}] {message}"
        )
        formatted_message = template.format(
            player_name=player_name, message=message
        )
        print(formatted_message, **kwargs)

def debug_ai_combat_state(player_name: str, state: str, **kwargs):
    """AI战斗状态调试"""
    if DebugConfig.should_show(1):
        template = prompt_manager.get_prompt(
            "debug", "ai.combat_state",
            default="🤖 [{player_name}] 战斗状态：{state}"
        )
        formatted_message = template.format(
            player_name=player_name, state=state
        )
        print(formatted_message, **kwargs)

def debug_ai_kill_opportunity(player_name: str, target_name: str, target_hp: float, **kwargs):
    """AI击杀机会调试"""
    if DebugConfig.should_show(1):
        template = prompt_manager.get_prompt(
            "debug", "ai.kill_opportunity",
            default="🤖 [{player_name}] 击杀机会：{target_name} (HP: {target_hp})"
        )
        formatted_message = template.format(
            player_name=player_name,
            target_name=target_name,
            target_hp=target_hp
        )
        print(formatted_message, **kwargs)

def debug_ai_missile_attack(player_name: str, target_name: str, **kwargs):
    """AI导弹攻击调试"""
    if DebugConfig.should_show(1):
        template = prompt_manager.get_prompt(
            "debug", "ai.missile_attack",
            default="🤖 [{player_name}] 导弹攻击 {target_name}"
        )
        formatted_message = template.format(
            player_name=player_name,
            target_name=target_name
        )
        print(formatted_message, **kwargs)

def debug_ai_candidate_commands(player_name: str, commands: list, **kwargs):
    """AI候选命令调试（级别2）"""
    if DebugConfig.should_show(2):
        template = prompt_manager.get_prompt(
            "debug", "ai.detailed.candidate_commands",
            default="🤖 [{player_name}] 候选命令：{commands}"
        )
        formatted_message = template.format(
            player_name=player_name,
            commands=commands
        )
        print(formatted_message, **kwargs)

def debug_ai_attack_generation(player_name: str, weapon: str, target: str, **kwargs):
    """AI攻击生成调试（级别2）"""
    if DebugConfig.should_show(2):
        template = prompt_manager.get_prompt(
            "debug", "ai.detailed.attack_generation",
            default="🤖 [{player_name}] 攻击生成：{weapon} 对 {target}"
        )
        formatted_message = template.format(
            player_name=player_name,
            weapon=weapon,
            target=target
        )
        print(formatted_message, **kwargs)

def debug_ai_development_plan(player_name: str, plan: str, **kwargs):
    """AI发育计划调试（级别2）"""
    if DebugConfig.should_show(2):
        template = prompt_manager.get_prompt(
            "debug", "ai.detailed.development_plan",
            default="🤖 [{player_name}] 发育计划：{plan}"
        )
        formatted_message = template.format(
            player_name=player_name,
            plan=plan
        )
        print(formatted_message, **kwargs)

def debug_ai_talent_selection(player_name: str, talent_name: str, **kwargs):
    """AI天赋选择调试"""
    if DebugConfig.should_show(2):
        template = prompt_manager.get_prompt(
            "debug", "system.talent_selection",
            default="🔧 AI天赋选择：{player_name} 选择 {talent_name}"
        )
        formatted_message = template.format(
            player_name=player_name,
            talent_name=talent_name
        )
        print(formatted_message, **kwargs)

# ============================================================================
# 调试装饰器
# ============================================================================

def debug_function(min_level: int = 1):
    """
    函数调试装饰器，记录函数调用和返回
    # 开发工具：可用于调试复杂天赋交互，示例：with DebugContext(): ...

    Args:
        min_level: 最小调试级别
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            if DebugConfig.should_show(min_level):
                func_name = func.__name__
                args_str = ", ".join([repr(arg) for arg in args])
                kwargs_str = ", ".join([f"{key}={repr(value)}" for key, value in kwargs.items()])
                all_args = ", ".join(filter(None, [args_str, kwargs_str]))

                debug_system(f"调用 {func_name}({all_args})", min_level)

            result = func(*args, **kwargs)

            if DebugConfig.should_show(min_level):
                func_name = func.__name__
                debug_system(f"{func_name} 返回: {repr(result)}", min_level)

            return result
        return wrapper
    return decorator

# ============================================================================
# 调试上下文管理器
# ============================================================================

class DebugContext:
    """
    调试上下文管理器，用于临时启用调试
    # 开发工具：可用于调试复杂天赋交互，示例：with DebugContext(): ...
    """
    def __init__(self, enabled: bool = True, level: int = 1):
        self.enabled = enabled
        self.level = level
        self.original_enabled = False
        self.original_level = 1

    def __enter__(self):
        self.original_enabled = DebugConfig.is_debug_enabled()
        self.original_level = DebugConfig.get_debug_level()
        DebugConfig.set_debug_mode(self.enabled, self.level)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        DebugConfig.set_debug_mode(self.original_enabled, self.original_level)
        return False

# ============================================================================
# 便捷函数
# ============================================================================

def enable_debug(level: int = 1):
    """启用调试模式"""
    DebugConfig.set_debug_mode(True, level)
    debug_system(f"调试模式已启用，级别: {level}")

def disable_debug():
    """禁用调试模式"""
    DebugConfig.set_debug_mode(False, 1)
    debug_system("调试模式已禁用")

def is_debug_enabled() -> bool:
    """检查调试模式是否启用"""
    return DebugConfig.is_debug_enabled()

def get_debug_level() -> int:
    """获取当前调试级别"""
    return DebugConfig.get_debug_level()
