"""
提示管理系统
═══════════════════════════════════════════════════
集中管理游戏中的所有提示文本，支持玩家自定义修改和后期调试。

设计目标：
1. 集中存储所有游戏提示（文本字符串）
2. 支持分类（UI、战斗、天赋、系统等）
3. 允许配置（详细程度、颜色、格式等）
4. 与调试系统整合
5. 支持本地化（未来扩展）
6. 允许玩家通过配置文件修改提示
"""

import json
import os
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

# ============================================================================
# 提示级别配置
# ============================================================================

class PromptLevel:
    """提示级别定义"""
    CRITICAL = 0    # 关键：死亡、胜利、致命错误
    IMPORTANT = 1   # 重要：攻击结果、状态变更
    NORMAL = 2      # 普通：移动、交互
    DEBUG = 3       # 调试：AI决策、详细过程
    VERBOSE = 4     # 详细：所有细节
    
    @classmethod
    def get_name(cls, level: int) -> str:
        """获取级别名称"""
        names = {
            cls.CRITICAL: "CRITICAL",
            cls.IMPORTANT: "IMPORTANT", 
            cls.NORMAL: "NORMAL",
            cls.DEBUG: "DEBUG",
            cls.VERBOSE: "VERBOSE"
        }
        return names.get(level, f"UNKNOWN({level})")

# ============================================================================
# 提示管理器
# ============================================================================

class PromptManager:
    """
    提示管理器单例类
    
    管理游戏中的所有提示文本，支持动态加载和格式化。
    """
    
    _instance = None
    _prompts: Dict[str, Any] = {}
    _config: Dict[str, Any] = {
        "verbosity": PromptLevel.NORMAL,  # 默认显示级别
        "show_timestamps": False,         # 是否显示时间戳
        "use_colors": True,               # 是否使用颜色
        "language": "zh_CN",              # 语言设置
        "show_full_lore": False,          # 是否显示完整叙事文案
        "lore_display_level": PromptLevel.NORMAL,  # 叙事文案显示级别
    }
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PromptManager, cls).__new__(cls)
        return cls._instance
    
    # ========================================================================
    # 配置管理
    # ========================================================================
    
    @classmethod
    def load_config(cls, config_path: Optional[str] = None):
        """
        加载配置文件
        
        Args:
            config_path: 配置文件路径，如果为None则使用默认路径
        """
        if config_path is None:
            config_path = "config/prompt_config.json"
        
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                cls._config.update(user_config)
                print(f"📝 已加载提示配置文件: {config_path}")
        except Exception as e:
            print(f"⚠️  加载提示配置文件失败: {e}")
    
    @classmethod
    def save_config(cls, config_path: Optional[str] = None):
        """
        保存配置文件
        
        Args:
            config_path: 配置文件路径，如果为None则使用默认路径
        """
        if config_path is None:
            config_path = "config/prompt_config.json"
        
        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(cls._config, f, ensure_ascii=False, indent=2)
            print(f"💾 已保存提示配置文件: {config_path}")
        except Exception as e:
            print(f"❌ 保存提示配置文件失败: {e}")
    
    @classmethod
    def set_verbosity(cls, level: int):
        """设置详细级别"""
        cls._config["verbosity"] = max(PromptLevel.CRITICAL, min(PromptLevel.VERBOSE, level))
    
    @classmethod
    def get_verbosity(cls) -> int:
        """获取详细级别"""
        return cls._config["verbosity"]
    
    @classmethod
    def should_show(cls, level: int) -> bool:
        """检查是否应该显示指定级别的提示"""
        return level <= cls._config["verbosity"]
    
    # ========================================================================
    # 提示加载与管理
    # ========================================================================
    
    @classmethod
    def load_prompts(cls, prompts_path: Optional[str] = None):
        """
        加载提示数据
        
        Args:
            prompts_path: 提示文件路径，如果为None则使用默认路径
        """
        if prompts_path is None:
            prompts_path = "data/prompts.json"
        
        try:
            if os.path.exists(prompts_path):
                with open(prompts_path, 'r', encoding='utf-8') as f:
                    cls._prompts = json.load(f)
                print(f"📚 已加载提示数据: {prompts_path}")
            else:
                print(f"⚠️  提示文件不存在: {prompts_path}")
                cls._load_default_prompts()
        except Exception as e:
            print(f"❌ 加载提示数据失败: {e}")
            cls._load_default_prompts()
    
    @classmethod
    def _load_default_prompts(cls):
        """加载默认提示（硬编码备份）"""
        print("📝 使用默认提示数据")
        # 这里可以硬编码一些关键的默认提示
        cls._prompts = {
            "error": {
                "file_not_found": "文件不存在: {path}",
                "load_failed": "加载失败: {error}",
            },
            "system": {
                "game_start": "游戏开始！",
                "game_end": "游戏结束！",
            }
        }
    
    @classmethod
    def reload_prompts(cls, prompts_path: Optional[str] = None):
        """重新加载提示数据"""
        cls._prompts.clear()
        cls.load_prompts(prompts_path)
    
    @classmethod
    def save_prompts(cls, prompts_path: Optional[str] = None):
        """
        保存提示数据（用于导出或备份）
        
        Args:
            prompts_path: 提示文件路径，如果为None则使用默认路径
        """
        if prompts_path is None:
            prompts_path = "data/prompts.json"
        
        try:
            os.makedirs(os.path.dirname(prompts_path), exist_ok=True)
            with open(prompts_path, 'w', encoding='utf-8') as f:
                json.dump(cls._prompts, f, ensure_ascii=False, indent=2)
            print(f"💾 已保存提示数据: {prompts_path}")
        except Exception as e:
            print(f"❌ 保存提示数据失败: {e}")
    
    # ========================================================================
    # 提示获取与格式化
    # ========================================================================
    
    @classmethod
    def get_prompt(cls, category: str, key: str, default: Optional[str] = None, **kwargs) -> str:
        """
        获取格式化后的提示文本
        
        Args:
            category: 提示类别，如 "ui", "combat", "talent"
            key: 提示键名
            default: 如果找不到提示时返回的默认值
            **kwargs: 格式化参数
        
        Returns:
            格式化后的提示文本
        """
        try:
            # 尝试从嵌套结构中获取提示
            category_parts = category.split('.')
            value = cls._prompts
            
            for part in category_parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    raise KeyError(f"Category part '{part}' not found")
            
            if key not in value:
                raise KeyError(f"Key '{key}' not found in category '{category}'")
            
            template = value[key]
            
            # 格式化字符串
            if kwargs:
                try:
                    return template.format(**kwargs)
                except KeyError as e:
                    return f"格式化错误: 缺少参数 {e}。原始文本: {template}"
                except Exception as e:
                    return f"格式化错误: {e}。原始文本: {template}"
            else:
                return template
            
        except (KeyError, AttributeError, TypeError):
            if default is not None:
                # 格式化默认值
                try:
                    return default.format(**kwargs) if kwargs else default
                except Exception:
                    return default
            else:
                return f"[Missing prompt: {category}.{key}]"
    
    @classmethod
    def has_prompt(cls, category: str, key: str) -> bool:
        """检查是否存在指定提示"""
        try:
            category_parts = category.split('.')
            value = cls._prompts
            
            for part in category_parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return False
            
            return key in value
        except Exception:
            return False
    
    @classmethod
    def set_prompt(cls, category: str, key: str, value: str):
        """
        设置或更新提示
        
        Args:
            category: 提示类别
            key: 提示键名
            value: 提示文本
        """
        category_parts = category.split('.')
        target = cls._prompts
        
        # 创建或遍历类别路径
        for i, part in enumerate(category_parts):
            if part not in target:
                target[part] = {}
            if i == len(category_parts) - 1:
                target = target[part]
            else:
                target = target[part]
        
        # 设置提示
        target[key] = value
    
    @classmethod
    def get_all_prompts(cls, category: Optional[str] = None) -> Dict[str, Any]:
        """
        获取所有提示或指定类别的提示
        
        Args:
            category: 可选，指定类别
        
        Returns:
            提示字典
        """
        if category is None:
            return cls._prompts.copy()
        
        try:
            category_parts = category.split('.')
            value = cls._prompts
            
            for part in category_parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return {}
            
            if isinstance(value, dict):
                return value.copy()
            else:
                return {}
        except Exception:
            return {}
    
    # ========================================================================
    # 天赋叙事文案显示
    # ========================================================================
    
    @classmethod
    def show_talent_lore(cls, talent_key: str, level: int = PromptLevel.NORMAL):
        """
        显示天赋的叙事文案
        
        Args:
            talent_key: 天赋键名，如 "g1mythfire", "t1oneslash"
            level: 显示级别
        """
        if not cls.should_show(level):
            return
        
        # 获取叙事文案
        lore = cls.get_prompt("talent", f"{talent_key}.lore", default=[])
        
        if not lore:
            print(f"📖 天赋「{talent_key}」暂无叙事文案")
            return
        
        # 显示格式化的叙事文案
        print("═══════════════════════════════════════════════════════════════")
        print(f"  📖 天赋叙事：{talent_key}")
        print("═══════════════════════════════════════════════════════════════")
        
        for line in lore:
            print(f"  {line}")
        
        print("═══════════════════════════════════════════════════════════════")
    
    @classmethod
    def show_formatted_lore(cls, category: str, content: str, level: int = PromptLevel.NORMAL):
        """
        显示格式化的叙事内容
        
        Args:
            category: 内容类别，如 "lore", "story"
            content: 叙事内容文本
            level: 显示级别
        """
        if not cls.should_show(level):
            return
        
        print("═══════════════════════════════════════════════════════════════")
        print(f"  📖 {category}")
        print("═══════════════════════════════════════════════════════════════")
        print(content)
        print("═══════════════════════════════════════════════════════════════")
    
    # ========================================================================
    # 便捷输出方法
    # ========================================================================
    
    @classmethod
    def show(cls, category: str, key: str, level: int = PromptLevel.NORMAL, 
             prefix: Optional[str] = None, **kwargs):
        """
        显示提示（根据级别决定是否输出）
        
        Args:
            category: 提示类别
            key: 提示键名
            level: 提示级别
            prefix: 可选前缀（如 "⚠️", "❌" 等）
            **kwargs: 格式化参数
        """
        if cls.should_show(level):
            text = cls.get_prompt(category, key, **kwargs)
            if prefix:
                text = f"{prefix} {text}"
            print(text)
    
    @classmethod
    def show_critical(cls, category: str, key: str, **kwargs):
        """显示关键提示"""
        cls.show(category, key, PromptLevel.CRITICAL, "🚨", **kwargs)
    
    @classmethod
    def show_important(cls, category: str, key: str, **kwargs):
        """显示重要提示"""
        cls.show(category, key, PromptLevel.IMPORTANT, "❗", **kwargs)
    
    @classmethod
    def show_normal(cls, category: str, key: str, **kwargs):
        """显示普通提示"""
        cls.show(category, key, PromptLevel.NORMAL, "", **kwargs)
    
    @classmethod
    def show_debug(cls, category: str, key: str, **kwargs):
        """显示调试提示"""
        cls.show(category, key, PromptLevel.DEBUG, "🔍", **kwargs)
    
    @classmethod
    def show_verbose(cls, category: str, key: str, **kwargs):
        """显示详细提示"""
        cls.show(category, key, PromptLevel.VERBOSE, "📋", **kwargs)
    
    @classmethod
    def show_error(cls, category: str, key: str, **kwargs):
        """显示错误提示（总是显示）"""
        text = cls.get_prompt(category, key, **kwargs)
        print(f"❌ {text}")
    
    @classmethod
    def show_warning(cls, category: str, key: str, **kwargs):
        """显示警告提示（总是显示）"""
        text = cls.get_prompt(category, key, **kwargs)
        print(f"⚠️  {text}")
    
    @classmethod
    def show_info(cls, category: str, key: str, **kwargs):
        """显示信息提示（总是显示）"""
        text = cls.get_prompt(category, key, **kwargs)
        print(f"ℹ️  {text}")

# ============================================================================
# 全局实例和便捷函数
# ============================================================================

# 全局提示管理器实例
prompt_manager = PromptManager()

# 便捷函数
def get_prompt(category: str, key: str, **kwargs) -> str:
    """便捷函数：获取提示"""
    return prompt_manager.get_prompt(category, key, **kwargs)

def show_prompt(category: str, key: str, level: int = PromptLevel.NORMAL, **kwargs):
    """便捷函数：显示提示"""
    prompt_manager.show(category, key, level, **kwargs)

def show_critical(category: str, key: str, **kwargs):
    """便捷函数：显示关键提示"""
    prompt_manager.show_critical(category, key, **kwargs)

def show_important(category: str, key: str, **kwargs):
    """便捷函数：显示重要提示"""
    prompt_manager.show_important(category, key, **kwargs)

def show_normal(category: str, key: str, **kwargs):
    """便捷函数：显示普通提示"""
    prompt_manager.show_normal(category, key, **kwargs)

def show_debug(category: str, key: str, **kwargs):
    """便捷函数：显示调试提示"""
    prompt_manager.show_debug(category, key, **kwargs)

def show_verbose(category: str, key: str, **kwargs):
    """便捷函数：显示详细提示"""
    prompt_manager.show_verbose(category, key, **kwargs)

def show_error(category: str, key: str, **kwargs):
    """便捷函数：显示错误提示"""
    prompt_manager.show_error(category, key, **kwargs)

def show_warning(category: str, key: str, **kwargs):
    """便捷函数：显示警告提示"""
    prompt_manager.show_warning(category, key, **kwargs)

def show_info(category: str, key: str, **kwargs):
    """便捷函数：显示信息提示"""
    prompt_manager.show_info(category, key, **kwargs)

def show_talent_lore(talent_key: str, level: int = PromptLevel.NORMAL):
    """便捷函数：显示天赋叙事文案"""
    prompt_manager.show_talent_lore(talent_key, level)

def show_formatted_lore(category: str, content: str, level: int = PromptLevel.NORMAL):
    """便捷函数：显示格式化的叙事内容"""
    prompt_manager.show_formatted_lore(category, content, level)