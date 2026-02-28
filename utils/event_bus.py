"""
事件总线：发布-订阅模式。
天赋通过订阅事件来在各阶段插入自己的逻辑。
"""


class EventBus:
    def __init__(self):
        self._listeners = {}  # event_name -> [(priority, callback), ...]

    def subscribe(self, event_name, callback, priority=50):
        """
        订阅事件。
        priority: 数字越小越先执行（默认50）。
        callback: callable，签名取决于事件类型。
        """
        if event_name not in self._listeners:
            self._listeners[event_name] = []
        self._listeners[event_name].append((priority, callback))
        self._listeners[event_name].sort(key=lambda x: x[0])

    def unsubscribe(self, event_name, callback):
        """取消订阅"""
        if event_name in self._listeners:
            self._listeners[event_name] = [
                (p, cb) for p, cb in self._listeners[event_name]
                if cb != callback
            ]

    def emit(self, event_name, **kwargs):
        """
        发布事件。返回所有回调的返回值列表。
        回调如果返回 dict 且包含 "cancel": True，则中断后续回调。
        """
        results = []
        for priority, callback in self._listeners.get(event_name, []):
            try:
                result = callback(**kwargs)
                results.append(result)
                if isinstance(result, dict) and result.get("cancel"):
                    break
            except Exception as e:
                print(f"  [EventBus] 事件 {event_name} 回调异常：{e}")
                results.append(None)
        return results

    def emit_collect(self, event_name, **kwargs):
        """
        发布事件并收集所有非None返回值。
        用于收集修改器（如伤害加成倍率）。
        """
        values = []
        for priority, callback in self._listeners.get(event_name, []):
            try:
                result = callback(**kwargs)
                if result is not None:
                    values.append(result)
            except Exception as e:
                print(f"  [EventBus] 事件 {event_name} 回调异常：{e}")
        return values

    def clear(self):
        """清除所有订阅"""
        self._listeners.clear()
