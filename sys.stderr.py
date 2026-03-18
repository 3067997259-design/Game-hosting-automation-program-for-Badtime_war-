import sys  
from rl.env import BadtimeWarEnv  
import numpy as np  
  
def log(msg):  
    sys.stderr.write(f"[DEBUG] {msg}\n")  
    sys.stderr.flush()  
  
env = BadtimeWarEnv(num_opponents=1, max_rounds=50)  
log("env created")  
  
obs, info = env.reset()  
log(f"reset done, obs shape={obs.shape}, mask sum={info['action_masks'].sum()}")  
log(f"game_over={env._state.game_over}, game_over_flag={env._game_over_flag}")  
  
done = False  
steps = 0  
while not done:  
    mask = info["action_masks"]  
    valid_actions = mask.nonzero()[0]  
    log(f"step {steps}, valid actions: {valid_actions[:10]}...")  
    action = np.random.choice(valid_actions)  
    obs, reward, terminated, truncated, info = env.step(action)  
    done = terminated or truncated  
    steps += 1  
  
log(f"Game over: {steps} steps, winner={env._state.winner}")  
log(f"terminated={terminated}, truncated={truncated}")  
env.close()