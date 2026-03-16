#!/usr/bin/env python3
"""
警察系统AI博弈模拟脚本

模拟三个随机天赋的AI围绕警察局展开博弈，测试警察系统AI交互机制
包括：
1. 警察攻击与反击
2. 队长指挥系统
3. 警察拆分与调度
4. 警察装备管理
5. 犯罪与威信系统

运行方式：python police_ai_simulation.py
"""

import sys
import random
import time
from typing import List, Dict, Any

# 禁用真实输入，使用脚本输入
_script_lines = []
_script_index = 0
_original_input = __builtins__.input if hasattr(__builtins__, 'input') else None

import getpass as _getpass
_original_getpass = _getpass.getpass


def fake_input(prompt=""):
    """模拟输入，从脚本读取"""
    global _script_index
    if _script_index < len(_script_lines):
        line = _script_lines[_script_index]
        _script_index += 1
        print(f"{prompt}{line}  [自动]")
        return line
    else:
        print(f"{prompt}[脚本结束]")
        raise SystemExit("模拟脚本执行完毕")


def fake_getpass(prompt=""):
    return fake_input(prompt)


class PoliceAISimulation:
    """警察系统AI模拟器"""
    
    def __init__(self):
        self.player_count = 3
        # 定义三个不同的AI人格
        self.player_names = ["警长猎手", "政治大师", "暴力破解者"]
        # 定义天赋列表
        self.talent_pool = [
            # 原初天赋
            "一刀缭断", "你给路打油", "天星", "六爻", 
            "不良少年", "朝阳好市民", "死者苏生",
            # 神代天赋
            "火萤Ⅳ型-完全燃烧", "请一直，注视着我", 
            "遗世独立的幻想乡", "愿负世，照拂黎明"
        ]
        self.selected_talents = []
        self.fixed_dice = []
        self.action_script = []
        
    def setup(self):
        """设置模拟环境"""
        print("=" * 70)
        print("🎮 警察系统AI博弈模拟")
        print("=" * 70)
        print()
        
        # 随机选择三个天赋（不重复）
        available_talents = self.talent_pool.copy()
        random.shuffle(available_talents)
        self.selected_talents = available_talents[:3]
        
        print(f"🎯 随机选择的天赋：")
        for i, (name, talent) in enumerate(zip(self.player_names, self.selected_talents)):
            print(f"  玩家{i+1}: {name} → {talent}")
        print()
        
        # 生成初始脚本
        init_script = self._generate_init_script()
        # 生成行动脚本（模拟8轮游戏）
        action_script = self._generate_action_script()
        # 生成固定的骰子序列（确保模拟可重现）
        self.fixed_dice = self._generate_fixed_dice()
        
        # 组合完整脚本
        self.action_script = init_script + action_script
        
        print(f"📊 模拟设置完成：")
        print(f"  • 玩家数量: {self.player_count}")
        print(f"  • 游戏轮数: 8轮")
        print(f"  • 骰子序列: {len(self.fixed_dice)}个固定值")
        print()
        
    def _generate_init_script(self) -> List[str]:
        """生成初始设置脚本"""
        script = []
        
        # 玩家数量
        script.append(str(self.player_count))
        # 玩家名称
        script.extend(self.player_names)
        # 天赋选择
        script.append("1")  # 启用天赋系统
        for talent in self.selected_talents:
            script.append(talent)
        # 游戏模式
        script.append("2")  # 全AI模式
        # AI人格分配
        script.extend(["political", "aggressive", "balanced"])  # 不同人格
        # 初始地点（为了聚焦警察局，全部在家起床）
        script.append("")  # 空行结束设置
        
        return script
    
    def _generate_fixed_dice(self) -> List[int]:
        """生成固定的骰子序列，确保模拟可重现"""
        # 为8轮游戏生成骰子序列
        # 每轮3个玩家投D4，可能还有冲突判定D6
        dice_sequence = []
        random.seed(42)  # 固定随机种子确保可重现
        
        for round_num in range(8):
            # 每轮3个D4（基础行动权判定）
            for _ in range(3):
                dice_sequence.append(random.randint(1, 4))
            # 50%概率有冲突判定（D6）
            if random.random() < 0.5:
                for _ in range(2):  # 两个玩家冲突
                    dice_sequence.append(random.randint(1, 6))
        
        print(f"🎲 生成的骰子序列（前10个）: {dice_sequence[:10]}...")
        return dice_sequence
    
    def _generate_action_script(self) -> List[str]:
        """生成行动脚本（AI会自动决策，这里只放基础指令）"""
        script = []
        
        # 添加一些基础指令确保游戏正常进行
        # AI会自动决策，所以这里主要放一些辅助指令
        for _ in range(3):  # 前几轮可能需要额外输入
            script.append("forfeit")  # AI会自动覆盖
        
        # 添加一些强制指令来测试特定功能
        # 这些指令会被AI覆盖，但可以作为备用
        script.extend([
            "status",  # 查看状态
            "police",  # 查看警察状态
            "forfeit", # 放弃
            "allstatus", # 查看所有状态
        ])
        
        return script
    
    def run(self):
        """运行模拟"""
        global _script_lines, _script_index
        
        # 准备脚本
        _script_lines = self.action_script
        _script_index = 0
        
        # 重定向输入
        import builtins
        builtins.input = fake_input
        _getpass.getpass = fake_getpass
        
        # 设置固定骰子
        original_d4 = None
        original_d6 = None
        
        if self.fixed_dice:
            import utils.dice as dice_module
            dice_idx = [0]
            original_d4 = dice_module.roll_d4
            original_d6 = dice_module.roll_d6

            def fixed_d4():
                if dice_idx[0] < len(self.fixed_dice):
                    val = self.fixed_dice[dice_idx[0]]
                    dice_idx[0] += 1
                    return min(max(val, 1), 4)
                return original_d4()

            def fixed_d6():
                if dice_idx[0] < len(self.fixed_dice):
                    val = self.fixed_dice[dice_idx[0]]
                    dice_idx[0] += 1
                    return min(max(val, 1), 6)
                return original_d6()

            dice_module.roll_d4 = fixed_d4
            dice_module.roll_d6 = fixed_d6
        
        try:
            # 清除模块缓存
            mods_to_clear = [k for k in sys.modules if k.startswith(('engine.', 'models.', 'actions.', 'locations.', 'combat.', 'cli.', 'controllers.', 'talents.'))]
            for m in mods_to_clear:
                del sys.modules[m]
            
            # 导入游戏引擎
            from engine.game_setup import setup_game
            from engine.round_manager import RoundManager
            
            print("🚀 开始模拟...")
            print()
            
            # 设置游戏
            game_state = setup_game()
            # 获取AI控制器以便监控
            self._monitor_ai_behavior(game_state)
            
            # 运行游戏（限制轮数）
            round_mgr = RoundManager(game_state)
            
            # 修改round_mgr以限制游戏轮数
            original_run = round_mgr.run_game_loop
            max_rounds = 8
            rounds_completed = [0]
            
            def limited_run():
                while rounds_completed[0] < max_rounds:
                    try:
                        original_run()
                        rounds_completed[0] += 1
                        print(f"\n📈 完成第 {rounds_completed[0]}/{max_rounds} 轮")
                        print("-" * 50)
                        
                        # 每轮结束后显示警察状态
                        self._show_police_status(game_state)
                        
                        if rounds_completed[0] >= max_rounds:
                            print(f"\n🎯 模拟完成 {max_rounds} 轮！")
                            break
                            
                    except Exception as e:
                        print(f"⚠️ 第 {rounds_completed[0]+1} 轮出现错误: {e}")
                        break
            
            round_mgr.run_game_loop = limited_run
            round_mgr.run_game_loop()
            
            # 模拟结束，显示总结
            self._show_simulation_summary(game_state)
            
        except SystemExit as e:
            print(f"\n✅ 模拟正常结束：{e}")
        except Exception as e:
            print(f"\n❌ 模拟崩溃！")
            print(f"  错误类型：{type(e).__name__}")
            print(f"  错误信息：{e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            # 恢复原始函数
            import builtins
            if _original_input:
                builtins.input = _original_input
            _getpass.getpass = _original_getpass
            if self.fixed_dice and original_d4:
                import utils.dice as dice_module
                dice_module.roll_d4 = original_d4
                dice_module.roll_d6 = original_d6
        
        print(f"\n🎉 警察系统AI博弈模拟完成！")
        return True
    
    def _monitor_ai_behavior(self, game_state):
        """监控AI行为，特别是警察相关"""
        # 保存原始方法以便包装
        from controllers.ai_basic import BasicAIController
        
        if hasattr(BasicAIController, '_original_get_command'):
            return  # 已经包装过了
        
        # 保存原始方法
        BasicAIController._original_get_command = BasicAIController.get_command
        
        # 定义包装方法
        def monitored_get_command(self, available_actions):
            """监控AI决策"""
            command = self._original_get_command(available_actions)
            
            # 记录警察相关的决策
            player = self._player
            if player:
                player_name = player.name
                
                # 检查命令是否与警察相关
                police_related = False
                police_keywords = [
                    'police', 'attack police', 'report', 'assemble',
                    'recruit', 'election', 'captain', '威信', '犯罪'
                ]
                
                cmd_lower = command.lower()
                for keyword in police_keywords:
                    if keyword in cmd_lower:
                        police_related = True
                        break
                
                if police_related:
                    print(f"  👮 {player_name} 做出警察相关决策: {command}")
                    
                    # 特别重要的命令
                    if 'attack police' in cmd_lower:
                        print(f"    ⚔️  {player_name} 决定攻击警察！")
                    elif 'report' in cmd_lower:
                        print(f"    📢  {player_name} 进行举报！")
                    elif 'recruit' in cmd_lower:
                        print(f"    🚓  {player_name} 加入警察！")
                    elif 'captain' in cmd_lower or 'election' in cmd_lower:
                        print(f"    👑  {player_name} 竞选队长！")
                    elif 'police move' in cmd_lower or 'police attack' in cmd_lower or 'police equip' in cmd_lower:
                        print(f"    🎮  {player_name} 指挥警察: {command}")
                
                # 记录攻击警察的命令
                if cmd_lower.startswith('attack police'):
                    self._log_police_attack(player_name, command)
            
            return command
        
        # 应用包装
        BasicAIController.get_command = monitored_get_command
        
        print("👀 AI行为监控已启用")
        
    def _log_police_attack(self, player_name: str, command: str):
        """记录攻击警察的行为"""
        log_entry = f"{time.strftime('%H:%M:%S')} {player_name}: {command}"
        
        # 保存到文件
        with open('police_attack_log.txt', 'a', encoding='utf-8') as f:
            f.write(log_entry + '\n')
        
        print(f"    📝 记录攻击警察行为到文件")
    
    def _show_police_status(self, game_state):
        """[Issue 13] 显示警察状态（使用当前PoliceData模型）"""
        if not hasattr(game_state, 'police') or not game_state.police:
            print("    当前无警察系统数据")
            return

        police = game_state.police
        print("    👮 警察系统状态：")

        # 队长信息
        if police.captain_id:
            captain = None
            for player in game_state.players:
                if player.player_id == police.captain_id:
                    captain = player
                    break
            if captain:
                print(f"      队长: {captain.name} (威信: {police.authority})")
        else:
            print("      队长: 无")

        # 警察单位
        total_police = len(police.units)
        alive_police = len(police.alive_units())
        print(f"      总计警察: {total_police}, 存活: {alive_police}")

        for unit in police.units:
            if unit.is_alive():
                location = unit.location if unit.location else "不在地图上"
                weapon = unit.weapon_name
                status = "可行动" if unit.is_active() else "debuff中"
                print(f"      {unit.unit_id} - 位置: {location} - 武器: {weapon} - {status}")

        # 举报状态
        if police.report_phase != 'idle':
            print(f"      举报状态: {police.report_phase} - 目标: {police.reported_target_id}")
    
    def _show_simulation_summary(self, game_state):
        """[Issue 13] 显示模拟总结（使用当前PoliceData模型）"""
        print("\n" + "=" * 70)
        print("📊 警察系统AI博弈模拟总结")
        print("=" * 70)

        # 玩家状态
        print("\n👥 玩家状态:")
        for i, player in enumerate(game_state.players):
            status = "存活" if player.is_alive() else "死亡"
            location = getattr(player, 'location', '未知')
            hp = getattr(player, 'hp', 0)

            # 犯罪记录（从police.crime_records获取）
            crime_count = 0
            if hasattr(game_state, 'police') and game_state.police:
                crimes_set = game_state.police.crime_records.get(player.player_id, set())
                crime_count = len(crimes_set)
            crimes = f"犯罪{crime_count}次" if crime_count > 0 else "无犯罪"

            # 击杀数
            kills = getattr(player, 'kill_count', 0)

            print(f"  {i+1}. {player.name} ({status}) - HP: {hp} - 位置: {location}")
            print(f"     击杀: {kills} - {crimes}")

        # 警察系统总结
        print("\n👮 警察系统总结:")
        if hasattr(game_state, 'police') and game_state.police:
            police = game_state.police

            # 警察存活情况
            total_police = len(police.units)
            alive_police = len(police.alive_units())

            print(f"  警察总数: {total_police}")
            print(f"  存活警察: {alive_police}")
            print(f"  被击杀警察: {total_police - alive_police}")

            # 队长信息
            if police.captain_id:
                captain_name = "未知"
                for player in game_state.players:
                    if player.player_id == police.captain_id:
                        captain_name = player.name
                        break
                print(f"  当前队长: {captain_name} (威信: {police.authority})")
            else:
                print(f"  当前队长: 无")

            # 犯罪统计（crime_records是dict: player_id -> set）
            total_crime_players = sum(1 for pid, crimes in police.crime_records.items() if crimes)
            attack_police_count = sum(
                1 for pid, crimes in police.crime_records.items()
                if "攻击警察" in crimes
            )
            print(f"  有犯罪记录的玩家数: {total_crime_players}")
            print(f"  攻击警察记录: {attack_police_count}")
        else:
            print("  警察系统未启用")

        # 天赋使用情况
        print("\n🎯 天赋使用情况:")
        for i, (name, talent) in enumerate(zip(self.player_names, self.selected_talents)):
            used = "未知"  # 实际中需要从游戏状态获取
            print(f"  {name}: {talent} ({used})")

        print("\n📈 关键指标:")
        # 从日志文件读取攻击警察的次数
        try:
            with open('police_attack_log.txt', 'r', encoding='utf-8') as f:
                attacks = f.readlines()
            print(f"  攻击警察次数: {len(attacks)}")
            if attacks:
                print(f"  最后一次攻击: {attacks[-1].strip()}")
        except FileNotFoundError:
            print("  攻击警察次数: 0")

        print("\n✅ 模拟完成！")


def main():
    """主函数"""
    simulator = PoliceAISimulation()
    
    # 设置模拟
    simulator.setup()
    
    # 确认开始
    print("是否开始模拟？ (Y/n): ", end="")
    if _original_input:
        response = _original_input().strip().lower()
    else:
        response = "y"
    
    if response not in ['', 'y', 'yes']:
        print("模拟取消")
        return
    
    # 运行模拟
    print()
    success = simulator.run()
    
    if success:
        print("\n🎊 模拟成功完成！")
        print("检查 police_attack_log.txt 查看详细记录")
    else:
        print("\n⚠️ 模拟过程中出现问题")
    
    return 0 if success else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n模拟被用户中断")
        sys.exit(1)
