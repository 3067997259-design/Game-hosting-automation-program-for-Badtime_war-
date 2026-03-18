import sys, numpy as np  
from rl.env import BadtimeWarEnv  
  
def log(msg):  
    sys.stderr.write(f"[DEBUG] {msg}\n"); sys.stderr.flush()  
  
env = BadtimeWarEnv(num_opponents=1, max_rounds=50)  
for ep in range(20):  
    obs, info = env.reset()  
    done, steps = False, 0  
    while not done:  
        action = np.random.choice(info["action_masks"].nonzero()[0])  
        obs, reward, terminated, truncated, info = env.step(action)  
        done = terminated or truncated  
        steps += 1  
    log(f"ep {ep}: {steps} steps, winner={env._state.winner}")  
env.close()  
log("stability test passed")