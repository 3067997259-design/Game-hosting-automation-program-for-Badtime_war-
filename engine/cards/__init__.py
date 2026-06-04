"""物料牌注册表。新增牌在此注册，然后添加 play() 实现。"""
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
