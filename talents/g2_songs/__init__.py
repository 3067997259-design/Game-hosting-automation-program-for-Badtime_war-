"""G2 曲目注册表。

注意：Overture（序曲）在 pursue_light.py 中定义但在 v0.6 设定中作为
固定开幕曲通过 execute_sing() 直接调用，不走 SONG_REGISTRY 选择流程，
因此未注册到任何 registry。若后续 Overture 变为可选曲目，需添加到此文件。
"""
from talents.g2_songs.pursue_light import Soave, Sognando
from talents.g2_songs.patch_regret import Placido, Zeffiroso
from talents.g2_songs.before_light import Riposato, Dolente
from talents.g2_songs.melody import Melody1, Melody2, Melody3

# 非旋律曲目（需选听者）
SONG_REGISTRY = [Soave, Sognando, Placido, Riposato, Dolente]
# Zeffiroso 特殊处理（双人选牌）
ZEFFIROSO_CLASS = Zeffiroso
# 旋律曲目（不需选听者，走双座位）
MELODY_REGISTRY = [Melody1, Melody2, Melody3]
