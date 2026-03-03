"""
提示管理器 - 统一管理所有游戏文本输出
支持分级提示、动态变量替换、外部配置文件
"""

import json
import os
import re
from enum import IntEnum
from typing import Dict, Any, List, Optional, Union
from datetime import datetime


class PromptLevel(IntEnum):
    """提示级别枚举"""
    CRITICAL = 0      # 死亡、胜利、致命错误（始终显示）
    IMPORTANT = 1     # 攻击结果、状态变更
    NORMAL = 2        # 移动、交互（默认级别）
    DEBUG = 3         # AI决策、详细过程
    VERBOSE = 4       # 所有细节


class PromptManager:
    """提示管理器单例类"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PromptManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.prompts: Dict[str, Any] = {}
        self.config: Dict[str, Any] = {}
        self.current_level = PromptLevel.NORMAL
        self.debug_level = PromptLevel.DEBUG
        
        # 默认配置
        self.default_config = {
            "show_timestamps": False,
            "use_colors": True,
            "max_line_length": 80,
            "default_level": "NORMAL",
            "debug_level": "DEBUG",
            "show_talent_lore": True,
            "talent_lore_level": "IMPORTANT"
        }
        
        self._load_config()
        self._load_prompts()
        self._initialized = True
    
    def _load_config(self):
        """加载配置文件"""
        config_paths = [
            "config/prompt_config.json",
            "prompt_config.json"
        ]
        
        for path in config_paths:
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        self.config = {**self.default_config, **json.load(f)}
                    
                    # 转换级别字符串为枚举
                    level_map = {
                        "CRITICAL": PromptLevel.CRITICAL,
                        "IMPORTANT": PromptLevel.IMPORTANT,
                        "NORMAL": PromptLevel.NORMAL,
                        "DEBUG": PromptLevel.DEBUG,
                        "VERBOSE": PromptLevel.VERBOSE
                    }
                    
                    self.current_level = level_map.get(
                        self.config.get("default_level", "NORMAL"),
                        PromptLevel.NORMAL
                    )
                    self.debug_level = level_map.get(
                        self.config.get("debug_level", "DEBUG"),
                        PromptLevel.DEBUG
                    )
                    
                    print(f"[提示系统] 加载配置: {path}")
                    return
                except Exception as e:
                    print(f"[提示系统] 配置加载失败 {path}: {e}")
        
        # 使用默认配置
        self.config = self.default_config
        print("[提示系统] 使用默认配置")
    
    def _load_prompts(self):
        """加载提示文本，自动修复常见JSON错误"""
        prompt_paths = [
            "data/prompts.json",
            "prompts.json"
        ]
        
        for path in prompt_paths:
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # 尝试解析JSON
                    try:
                        self.prompts = json.loads(content)
                        print(f"[提示系统] 加载提示: {path} ({len(self.prompts)} categories)")
                        return
                    except json.JSONDecodeError as e:
                        print(f"[提示系统] JSON解析错误 {path}: {e}")
                        print(f"[提示系统] 尝试修复JSON...")
                        
                        # 尝试修复常见的JSON错误
                        fixed_content = self._fix_json_errors(content, e)
                        try:
                            self.prompts = json.loads(fixed_content)
                            print(f"[提示系统] JSON修复成功，使用修复后的版本")
                            
                            # 保存修复后的版本（备份原文件）
                            backup_path = path + ".backup"
                            with open(backup_path, 'w', encoding='utf-8') as backup:
                                backup.write(content)
                            print(f"[提示系统] 原文件已备份到: {backup_path}")
                            
                            # 可选：保存修复版本
                            # with open(path, 'w', encoding='utf-8') as f:
                            #     f.write(fixed_content)
                            # print(f"[提示系统] 修复版本已保存")
                            
                            return
                        except json.JSONDecodeError as e2:
                            print(f"[提示系统] JSON修复失败: {e2}")
                            print(f"[提示系统] 使用空提示结构")
                
                except Exception as e:
                    print(f"[提示系统] 提示加载失败 {path}: {e}")
        
        # 创建空结构
        self.prompts = {}
        print("[提示系统] 未找到提示文件，创建空结构")
    
    def _fix_json_errors(self, content: str, error: json.JSONDecodeError) -> str:
        """
        尝试修复常见的JSON错误
        
        常见错误：
        1. 缺少逗号：在字段之间
        2. 未转义的双引号
        3. 尾随逗号
        """
        lines = content.split('\n')
        line_no = error.lineno - 1  # 转换为0-based索引
        col_no = error.colno - 1    # 转换为0-based索引
        
        print(f"[提示系统] 错误位置: 第{error.lineno}行, 第{error.colno}列, 字符{error.pos}")
        print(f"[提示系统] 错误消息: {error.msg}")
        
        # 获取错误行
        if line_no < len(lines):
            error_line = lines[line_no]
            print(f"[提示系统] 错误行: {error_line}")
            print(f"[提示系统] 错误位置标记: {error_line[:col_no]}^")
        
        # 根据错误类型尝试修复
        if "Expecting ',' delimiter" in error.msg:
            # 在错误位置插入逗号
            pos = error.pos
            if pos < len(content):
                # 简单修复：在位置插入逗号
                fixed = content[:pos] + ',' + content[pos:]
                print(f"[提示系统] 在位置{pos}插入逗号")
                return fixed
        
        elif "Expecting ':' delimiter" in error.msg:
            # 缺少冒号
            pos = error.pos
            if pos < len(content):
                fixed = content[:pos] + ':' + content[pos:]
                print(f"[提示系统] 在位置{pos}插入冒号")
                return fixed
        
        elif "Unterminated string" in error.msg:
            # 字符串未终止，尝试在行末添加引号
            if line_no < len(lines):
                error_line = lines[line_no]
                # 在行末添加引号
                lines[line_no] = error_line + '"'
                fixed = '\n'.join(lines)
                print(f"[提示系统] 在第{error.lineno}行末添加引号")
                return fixed
        
        # 通用修复：尝试修复常见的缺少逗号情况
        # 查找模式: "key": "value" "next_key"
        pattern = r'("\s*"[^",}\]]\s*")'
        fixed = re.sub(pattern, r'\1,', content)
        
        # 尝试修复未转义的双引号
        # 查找字符串内部未转义的双引号
        # 模式: "(.*[^\\])"(.*)"
        # 但需要小心处理
        
        return fixed
    
    # 公共方法，用于向后兼容
    def load_prompts(self):
        """公共方法：加载提示（向后兼容）"""
        self._load_prompts()
    
    def load_config(self):
        """公共方法：加载配置（向后兼容）"""
        self._load_config()
    
    def reload(self):
        """重新加载配置和提示"""
        self._load_config()
        self._load_prompts()
    
    def get_prompt(self, category: str, key: str, **kwargs) -> Any:
        """
        获取提示文本，支持嵌套键和变量替换
        
        Args:
            category: 类别，如 "ui", "combat", "talent"
            key: 键路径，如 "attack.hit" 或 "g1mythfire.lore"
            **kwargs: 替换变量，如 player_name="张三"
        
        Returns:
            替换变量后的文本（字符串）或原始值
        """
        try:
            # 分割嵌套键
            parts = key.split('.')
            value = self.prompts.get(category, {})
            
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part, {})
                else:
                    return f"[Missing: {category}.{key}]"
            
            # 如果最终是字符串，进行变量替换
            if isinstance(value, str) and kwargs:
                try:
                    return value.format(**kwargs)
                except KeyError as e:
                    return f"[Format Error: {category}.{key} missing {e}]"
            
            return value
            
        except Exception as e:
            return f"[Error: {category}.{key} - {e}]"
    
    def show(self, category: str, key: str, level: PromptLevel = None, **kwargs):
        """
        显示提示（如果级别足够）
        
        Args:
            category: 提示类别
            key: 提示键
            level: 提示级别（默认使用当前级别）
            **kwargs: 替换变量
        """
        if level is None:
            level = self.current_level
        
        # 检查是否应该显示
        if level > self.current_level and level != PromptLevel.CRITICAL:
            return
        
        text = self.get_prompt(category, key, **kwargs)
        if text and not text.startswith("[") and not text.startswith("{"):
            self._output(text, level)
    
    def show_formatted(self, title: str, content: Union[str, List[str]], 
                      level: PromptLevel = None, border_char: str = "═"):
        """
        显示带格式的提示
        
        Args:
            title: 标题
            content: 内容（字符串或字符串列表）
            level: 提示级别
            border_char: 边框字符
        """
        if level is None:
            level = self.current_level
        
        if level > self.current_level and level != PromptLevel.CRITICAL:
            return
        
        if isinstance(content, list):
            content = "\n".join(content)
        
        # 创建边框
        border = border_char * (len(title) + 4)
        
        # 输出
        output = f"\n{border}\n  {title}\n{border}\n{content}\n{border}"
        self._output(output, level)
    
    def show_talent_lore(self, talent_key: str, level: PromptLevel = None):
        """
        显示天赋的叙事文案
        
        Args:
            talent_key: 天赋键，如 "g1mythfire"
            level: 提示级别
        """
        if not self.config.get("show_talent_lore", True):
            return
        
        if level is None:
            level_str = self.config.get("talent_lore_level", "IMPORTANT")
            level_map = {
                "CRITICAL": PromptLevel.CRITICAL,
                "IMPORTANT": PromptLevel.IMPORTANT,
                "NORMAL": PromptLevel.NORMAL,
                "DEBUG": PromptLevel.DEBUG,
                "VERBOSE": PromptLevel.VERBOSE
            }
            level = level_map.get(level_str, PromptLevel.IMPORTANT)
        
        lore = self.get_prompt("talent", f"{talent_key}.lore")
        if lore and isinstance(lore, list):
            self.show_formatted(f"天赋叙事：{talent_key}", lore, level)
    
    def show_critical(self, category: str, key: str, **kwargs):
        """显示关键提示（始终显示）"""
        self.show(category, key, PromptLevel.CRITICAL, **kwargs)
    
    def show_important(self, category: str, key: str, **kwargs):
        """显示重要提示"""
        self.show(category, key, PromptLevel.IMPORTANT, **kwargs)
    
    def show_normal(self, category: str, key: str, **kwargs):
        """显示普通提示"""
        self.show(category, key, PromptLevel.NORMAL, **kwargs)
    
    def show_debug(self, category: str, key: str, **kwargs):
        """显示调试提示"""
        self.show(category, key, PromptLevel.DEBUG, **kwargs)
    
    def show_verbose(self, category: str, key: str, **kwargs):
        """显示详细提示"""
        self.show(category, key, PromptLevel.VERBOSE, **kwargs)
    
    def _output(self, text: str, level: PromptLevel):
        """输出文本（可根据配置添加时间戳、颜色等）"""
        if self.config.get("show_timestamps", False):
            timestamp = datetime.now().strftime("%H:%M:%S")
            text = f"[{timestamp}] {text}"
        
        print(text)
    
    def set_level(self, level: Union[PromptLevel, str]):
        """设置当前提示级别"""
        if isinstance(level, str):
            level_map = {
                "CRITICAL": PromptLevel.CRITICAL,
                "IMPORTANT": PromptLevel.IMPORTANT,
                "NORMAL": PromptLevel.NORMAL,
                "DEBUG": PromptLevel.DEBUG,
                "VERBOSE": PromptLevel.VERBOSE
            }
            level = level_map.get(level.upper(), PromptLevel.NORMAL)
        
        self.current_level = level
        print(f"[提示系统] 级别设置为: {level.name}")
    
    def get_level(self) -> PromptLevel:
        """获取当前提示级别"""
        return self.current_level
    
    def update_prompt(self, category: str, key: str, value: Any):
        """更新提示（运行时修改）"""
        parts = key.split('.')
        target = self.prompts.setdefault(category, {})
        
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        
        target[parts[-1]] = value
    
    def save_prompts(self, path: str = "data/prompts.json"):
        """保存提示到文件"""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.prompts, f, ensure_ascii=False, indent=2)
            print(f"[提示系统] 提示已保存: {path}")
        except Exception as e:
            print(f"[提示系统] 提示保存失败: {e}")


# 全局单例实例
prompt_manager = PromptManager()

# 快捷函数
def show_info(category: str, key: str, **kwargs):
    """显示普通信息（兼容旧API）"""
    prompt_manager.show_normal(category, key, **kwargs)

def show_warning(category: str, key: str, **kwargs):
    """显示警告"""
    prompt_manager.show_important(category, key, **kwargs)

def show_error(category: str, key: str, **kwargs):
    """显示错误"""
    prompt_manager.show_critical(category, key, **kwargs)

def show_debug(category: str, key: str, **kwargs):
    """显示调试信息"""
    prompt_manager.show_debug(category, key, **kwargs)