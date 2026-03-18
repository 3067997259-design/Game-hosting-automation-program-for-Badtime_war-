# r/test_smoke.py  
from rl.env import BadtimeWarEnv  
  
env = BadtimeWarEnv(num_opponents=1, max_rounds=10)  
obs, info = env.reset()  
print(f"obs shape: {obs.shape}, mask sum: {info['action_masks'].sum()}")  
  
# 选一个合法动作  
mask = info["action_masks"]  
action = mask.nonzero()[0][0]  # 第一个合法动作  
print(f"action: {action}")  
  
obs, reward, terminated, truncated, info = env.step(action)  
print(f"reward: {reward}, terminated: {terminated}, truncated: {truncated}")  
env.close()