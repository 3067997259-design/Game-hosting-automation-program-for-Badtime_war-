"""G2 曲目注册表。"""
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
