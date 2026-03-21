from rl.env import BadtimeWarEnv
import numpy as np

env = BadtimeWarEnv(num_opponents=1, max_rounds=10)
obs, info = env.reset()
done = False
steps = 0
total_reward = 0.0
while not done:
    mask = info["action_masks"]
    valid_actions = mask.nonzero()[0]
    action = np.random.choice(valid_actions)
    obs, reward, terminated, truncated, info = env.step(action)
    done = terminated or truncated
    total_reward += reward
    steps += 1
    if steps % 10 == 0:
        print(f"step {steps}, reward={reward:.2f}, total={total_reward:.2f}")

print(f"Game over: {steps} steps, total_reward={total_reward:.2f}")
print(f"winner: {env._state.winner}, terminated={terminated}, truncated={truncated}")
env.close()