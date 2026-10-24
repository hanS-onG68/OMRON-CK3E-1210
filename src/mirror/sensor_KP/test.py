import numpy as np

data_list = list(range(0, -300000, -5000))

print(f"Original data_list: {data_list}")

data_list.reverse()  # 反转列表，使其从大到小排列
print(f"Reversed data_list: {data_list}")