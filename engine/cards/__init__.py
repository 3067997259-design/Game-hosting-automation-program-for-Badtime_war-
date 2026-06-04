"""物料牌注册表。
新增牌在此注册，然后添加 play() 实现。

注意：当前 CARD_REGISTRY 用于 action_turn.py:_resolve_card_play() 分派。
material_deck.py 中的 _CARD_DEFS 仍维护一份独立的牌元数据字典（供 build_deck/get_card_info/is_playable）。
过渡期两套数据并存，后续应统一为从 CARD_REGISTRY 读取（每张 BaseCard 子类自带 name/count/voice/desc）。
"""
from engine.cards.front_row import FrontRowTicket
from engine.cards.card_exchange import CardExchange
from engine.cards.blank_stub import BlankStub
from engine.cards.earplug import Earplug
from engine.cards.glow_stick import GlowStick
from engine.cards.spotlight_photo import SpotlightPhoto
from engine.cards.support_cheer import SupportCheer
from engine.cards.backstage_pass import BackstagePass
from engine.cards.tear_ticket import TearTicket
from engine.cards.boo import Boo
from engine.cards.bouquet import Bouquet
from engine.cards.dog_tag import DogTag
from engine.cards.reflect_board import ReflectBoard
from engine.cards.ear_monitor import EarMonitor
from engine.cards.mediation import Mediation
from engine.cards.program_tidy import ProgramTidy
from engine.cards.chord_sheet import ChordSheet

CARD_REGISTRY = [
    FrontRowTicket, CardExchange, BlankStub, Earplug, GlowStick,
    SpotlightPhoto, SupportCheer, BackstagePass, TearTicket, Boo,
    Bouquet, Mediation, ProgramTidy, DogTag,
    ReflectBoard, EarMonitor, ChordSheet,
]
