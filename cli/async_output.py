"""
线程安全的异步输出工具
═══════════════════════
在 input() 阻塞期间，其他线程调用 print() 会导致输出追加到提示符后面。
async_print 使用 ANSI 转义序列清除当前行、输出消息、然后重绘提示符。
"""
import sys
import threading

_lock = threading.RLock()
_current_prompt: str = ""  # 当前显示的提示符文本


def set_current_prompt(prompt: str):
    """设置当前正在显示的 input() 提示符（由 input 调用前设置，input 返回后清除）。"""
    global _current_prompt
    _current_prompt = prompt


def clear_current_prompt():
    """清除当前提示符记录。"""
    global _current_prompt
    _current_prompt = ""


def async_print(text: str):
    """
    在 input() 阻塞期间安全地输出一行文本。
    - 清除当前行（回到行首 + 清除到行尾）
    - 输出消息
    - 重绘提示符
    """
    with _lock:
        if _current_prompt:
            # \r 回到行首，\033[K 清除当前行到末尾
            sys.stdout.write(f"\r\033[K{text}\n{_current_prompt}")
            sys.stdout.flush()
        else:
            print(text)
