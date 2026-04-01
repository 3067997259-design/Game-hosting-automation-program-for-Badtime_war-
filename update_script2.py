import json

def update_files():
    # 4a. Update t3_star.py
    with open("talents/t3_star.py", "r", encoding="utf-8") as f:
        t3_content = f.read()
    
    t3_content = t3_content.replace(
        "for i in range(2):",
        "for i in range(getattr(self, 'ripple_bounce_count', 2)):"
    )
    
    with open("talents/t3_star.py", "w", encoding="utf-8") as f:
        f.write(t3_content)
        
    # 4c. Update g2_hologram.py
    with open("talents/g2_hologram.py", "r", encoding="utf-8") as f:
        g2_content = f.read()
        
    g2_content = g2_content.replace(
        "def enhance_by_ripple(self):\n        \"\"\"涟漪献诗：易伤+1，最大使用次数+1（ver1.9移除了持续时间-1的效果）\"\"\"\n        self.enhanced = True\n        self.max_uses += 1",
        "def enhance_by_ripple(self):\n        \"\"\"涟漪献诗：易伤+1，最大使用次数+1（ver1.9移除了持续时间-1的效果）\"\"\"\n        self.enhanced = True\n        self.max_uses += 1\n        self.ripple_extra_vulnerability = getattr(self, 'ripple_extra_vulnerability', 0) + 0.5"
    )
    
    g2_content = g2_content.replace(
        "def _get_bonus_damage(self):\n        \"\"\"影像内额外伤害\"\"\"\n        if self.enhanced:\n            return 1.0\n        return 0.5",
        "def _get_bonus_damage(self):\n        \"\"\"影像内额外伤害\"\"\"\n        base = 0.5\n        if self.enhanced:\n            base = 1.0\n        return base + getattr(self, 'ripple_extra_vulnerability', 0.0)"
    )
    
    with open("talents/g2_hologram.py", "w", encoding="utf-8") as f:
        f.write(g2_content)
        
    # 4d. Update g4_savior.py
    with open("talents/g4_savior.py", "r", encoding="utf-8") as f:
        g4_content = f.read()
        
    g4_content = g4_content.replace(
        "if self.spent:\n            return\n\n        # 效果1：立刻额外获得 2 点火种",
        "# 效果1：立刻额外获得 2 点火种"
    )
    
    g4_content = g4_content.replace(
        "self.passive_bonus_divinity = 2",
        "self.passive_bonus_divinity = getattr(self, 'passive_bonus_divinity', 0) + 1"
    )
    
    with open("talents/g4_savior.py", "w", encoding="utf-8") as f:
        f.write(g4_content)
        
    # Section 7: Update game_setup.py
    with open("engine/game_setup.py", "r", encoding="utf-8") as f:
        setup_content = f.read()
        
    setup_content = setup_content.replace(
        "(12, \"神代天赋-往世的涟漪\", Ripple,\n     \"成为涤荡记忆的那颗流星，在命运长河中激起涟漪的石子\"),",
        "(12, \"神代天赋-往世的涟漪\", Ripple,\n     \"追忆满后发动：锚定命运或献诗增强。无次数限制，爱与记忆逐次成长\"),"
    )
    
    with open("engine/game_setup.py", "w", encoding="utf-8") as f:
        f.write(setup_content)
        
    # Section 8: Update prompts.json
    with open("data/prompts.json", "r", encoding="utf-8") as f:
        prompts = json.load(f)
        
    if "poem_already_used" in prompts["talent"]["g5ripple"]:
        del prompts["talent"]["g5ripple"]["poem_already_used"]
        
    prompts["talent"]["g5ripple"]["poem_strife_completion"] = "🔥 {target_name} 完成立刻行动！\\n 获得2个「炽愿」（当前{charges}层）\\n （每层抵扣1次debuff + 0.5额外生命值）"
    prompts["talent"]["g5ripple"]["poem_stars"] = "⭐ {target_name} 的「天星」被涟漪增强！\\n 天星落下后额外{bounce}次×0.5无视属性弹射伤害\\n 石化不再因被攻击自动解除"
    prompts["talent"]["g5ripple"]["poem_shore"] = "💀✨ {target_name} 的「死者苏生」增强！\\n 复活后可获得{count}件全游戏任意物品或法术\\n （不含扩展/天赋物品，不含抽象权能）"
    prompts["talent"]["g5ripple"]["poem_light_enhanced"] = "✨{target_name} 的「请一直，注视着我」增强！\\n 易伤+{vuln} | 可用次数+1（当前{uses}次）"
    prompts["talent"]["g5ripple"]["poem_destiny_header"] = "\\n🌊 献予「爱与记忆」之诗！（第{count}次，消耗{cost}层追忆）\\n 选择{stages}个单体单位（可重复），分别承受：\\n {types} 各1点伤害"
    prompts["talent"]["g5ripple"]["poem_destiny_true_damage"] = " → {target_name}（真伤）：无视一切防御！ HP {old_hp} → {new_hp}"
    
    with open("data/prompts.json", "w", encoding="utf-8") as f:
        json.dump(prompts, f, ensure_ascii=False, indent=2)

update_files()
