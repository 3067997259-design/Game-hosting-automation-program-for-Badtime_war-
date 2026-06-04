"""物料牌注册表。新增牌在此注册。"""
from engine.cards.reflect_board import ReflectBoard
from engine.cards.ear_monitor import EarMonitor
from engine.cards.chord_sheet import ChordSheet

# 新牌（已模块化）
CARD_REGISTRY = [ReflectBoard, EarMonitor, ChordSheet]
