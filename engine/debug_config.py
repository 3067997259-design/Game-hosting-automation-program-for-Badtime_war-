"""
调试配置模块
═══════════════════════════════════════════════════
提供统一的调试输出控制，支持调试模式开关。
"""

import sys
from typing import Any, Optional

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
# 调试输出函数
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
        formatted_message = f"🤖 [{player_name}] {message}"
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
        formatted_message = f"🔧 {message}"
        print(formatted_message, **kwargs)

def debug_warning(message: str, **kwargs):
    """
    警告调试输出（始终显示，即使调试模式关闭）
    
    Args:
        message: 警告信息
        **kwargs: 传递给print的其他参数
    """
    formatted_message = f"⚠️ {message}"
    print(formatted_message, **kwargs)

def debug_error(message: str, **kwargs):
    """
    错误调试输出（始终显示，即使调试模式关闭）
    
    Args:
        message: 错误信息
        **kwargs: 传递给print的其他参数
    """
    formatted_message = f"❌ {message}"
    print(formatted_message, **kwargs)

def debug_info(message: str, **kwargs):
    """
    重要信息调试输出（始终显示，即使调试模式关闭）
    
    Args:
        message: 信息
        **kwargs: 传递给print的其他参数
    """
    formatted_message = f"💡 {message}"
    print(formatted_message, **kwargs)

# ============================================================================
# 调试装饰器
# ============================================================================

def debug_function(min_level: int = 1):
    """
    函数调试装饰器，记录函数调用和返回
    
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