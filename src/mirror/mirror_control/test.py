import asyncio
from mirror.amplifier.domestic_amplifier import Amplifier as amp
from contextlib import AsyncExitStack
import numpy as np
from importlib import resources

amp_file = resources.files("mirror.mirror_control").joinpath("settings/Actuator_Mapping.csv") 
data = np.loadtxt(amp_file, delimiter=',', dtype=str, skiprows=1, comments='#')
# dev_data = np.zeros(len(data), dtype=str)
# print(f"data = {data}-{data.shape}, dev_data = {dev_data}-{dev_data.shape}")
data = np.char.strip(data[:,:]).astype(int)
actuator_id, controller_id, axis_id, amplifier_id, channel_id = data.T
print(f"actuator_id = {actuator_id}, controller_id = {controller_id}, axis_id = {axis_id}, amplifier_id = {amplifier_id}, channel_id = {channel_id}")

kp_file = resources.files("mirror.mirror_control").joinpath("settings/Coefficient_K_p.csv")
K_p = np.loadtxt(kp_file, delimiter=',', skiprows=1)    # 增益
print(f"K_p = {K_p}, K_p.shape = {K_p.shape}")

target_file = resources.files("mirror.mirror_control").joinpath("settings/Initial_Target.csv") 
target_data = np.loadtxt(target_file, delimiter=',', dtype=str, skiprows=1, comments='#')
initial_target_force = np.char.strip(target_data[:, 1]).astype(float)
Target = initial_target_force.reshape(6, 25)
print(f"Target = {Target}, Target.shape = {Target.shape}")


a = np.ones((6, 25))
b = np.zeros((6, 25))

steps_stack = np.stack([a, b])
print(f"steps_stack = {steps_stack}, steps_stack.shape = {steps_stack.shape}")
alwayes_nonzero_mask = np.all(steps_stack != 0)  # 检查每个位置在过去60秒内是否一直有非零步数(6x25: true表示一直有非零步数，false表示有过零步数)
print(f"alwayes_nonzero_mask = {alwayes_nonzero_mask}, alwayes_nonzero_mask.shape = {alwayes_nonzero_mask.shape}")
nonzero_positions = np.argwhere(alwayes_nonzero_mask)    # 获取一直有非零步数的位置索引
print(f"nonzero_positions = {nonzero_positions}, nonzero_positions.shape = {nonzero_positions.shape}")



data = np.array([[ [ 1,  2,  3],
                   [ 4, -5,  6],
                   [ 7,  8,  9],
                   [1,  2, -3],
                 ],
                 [ [ 1,  2,  3],
                   [ 4, -5,  6],
                   [ -7,  8,  9],
                   [1,  2, -3],
                 ]
                ])

print(f"data = {data}\n, data.shape = {data.shape}")
data1 = np.any(data > 0)  
print(f"data1 = {data1}, data1.shape = {data1.shape}")

data2 = np.any(data > 0, axis=0)
print(f"data2 = \n{data2}\ndata2.shape = {data2.shape}")  

data3 = np.any(data > 0, axis=1)
print(f"data3 = \n{data3}\ndata3.shape = {data3.shape}")


cols = np.arange(25).reshape(1, -1)
multiplier = np.where((cols % 2 == 0) & (cols != 0), 100.0, 20.0)   # 内圈100(偶数列)，外圈20(奇数列)，第0列为20(奇数列)
Force_limit_pos = np.ones((6, 25)) * multiplier         # 力值上限
Force_limit_neg = -np.ones((6, 25))* multiplier         # 力值下限
print(f"Force_limit_pos = {Force_limit_pos}, Force_limit_pos.shape = {Force_limit_pos.shape}")
print(f"Force_limit_neg = {Force_limit_neg}, Force_limit_neg.shape = {Force_limit_neg.shape}")