


MIRRORS_COUNT = 6
ACTUATORS_PER_MIRROR = 25
TOTAL_ACTUATORS = ACTUATORS_PER_MIRROR * MIRRORS_COUNT  # 力促动器总数量


AMPLIFIER_COUNT = 19
SENSORS_PER_AMP = 8
CAPACITY_SENSORS = SENSORS_PER_AMP * AMPLIFIER_COUNT    # 放大器总接口数(不是实际用了多少个接口)

BYTES_PER_FLOAT = 8
AMP_DATA_BYTES = BYTES_PER_FLOAT * SENSORS_PER_AMP          # 单个放大器数据连续共享内存区大小
AMP_TIMESTAMP_BYTES = BYTES_PER_FLOAT * SENSORS_PER_AMP     # 单个放大器时间戳连续共享内存区大小
DATA_BUFF_BYTES = BYTES_PER_FLOAT * CAPACITY_SENSORS        # 全部放大器数据连续共享内存区大小，也是时间戳区的起点
TIMESTAMP_BUFF_BYTES = BYTES_PER_FLOAT * CAPACITY_SENSORS   # 全部放大器数据连续共享内存区大小，也是时间戳区的起点
TOTAL_BUFF_BYTES = DATA_BUFF_BYTES + TIMESTAMP_BUFF_BYTES

SHM_NAME = "QUEST_Mirrors_Control"


DEFAULT_CTRL_IPS = [f"192.168.0.{200+i}" for i in range(2)]
# DEFAULT_AMP_PORTS = [f"/dev/ttyr{i:02d}" for i in range(19)]      # 进口放大器
DEFAULT_AMP_PORTS = [f"192.168.0.{i}" for i in range(104, 107, 1)]  # 国产放大器



FORCE_100_UPPER = 100
FORCE_100_LOWER = -100
FORCE_200_UPPER = 200
FORCE_200_LOWER = -200
MOTOR_STEPS_LIMIT = 5000

FORCE_TIMEOUT = 2.0


outer2inner_Map = {
    "6a": "16",   "6b": "19",
    "3a": "10",   "3b": "13",
    "5a": "15",   "5b": "18",
    "2a": "9",    "2b": "12",
    "4a": "14",   "4b": "17",
    "1a": "8",    "1b": "11",
}

Actuator2Pon_Map = {
    1: {    # 1号边缘子镜：逻辑驱动器编号到物理位置编号的映射
        0:  "7",  # 中心
        1:  "1b",                 3:  "6a",                 5:  "3a",                 7:  "5a",                 9:  "2a",                  11:  "4a",                 13:  "1a",                 15:  "6b",                 17:  "3b",                 19: "5b",                  21: "2b",                  23: "4b",                    # outer
        2: outer2inner_Map["1b"], 4: outer2inner_Map["6a"], 6: outer2inner_Map["3a"], 8: outer2inner_Map["5a"], 10: outer2inner_Map["2a"], 12: outer2inner_Map["4a"], 14: outer2inner_Map["1a"], 16: outer2inner_Map["6b"], 18: outer2inner_Map["3b"], 20: outer2inner_Map["5b"], 22: outer2inner_Map["2b"], 24: outer2inner_Map["4b"]    # inner
    },
    2: {    # 2号边缘子镜：逻辑驱动器编号到物理位置编号的映射
        0:  "7",  # 中心
        1:  "3a",                 3:  "5a",                 5:  "2a",                 7:  "4a",                 9:  "1a",                  11:  "6b",                 13:  "3b",                 15:  "5b",                 17:  "2b",                 19: "4b",                  21: "1b",                  23: "6a",                    # outer
        2: outer2inner_Map["3a"], 4: outer2inner_Map["5a"], 6: outer2inner_Map["2a"], 8: outer2inner_Map["4a"], 10: outer2inner_Map["1a"], 12: outer2inner_Map["6b"], 14: outer2inner_Map["3b"], 16: outer2inner_Map["5b"], 18: outer2inner_Map["2b"], 20: outer2inner_Map["4b"], 22: outer2inner_Map["1b"], 24: outer2inner_Map["6a"]    # inner
    },
    3: {    # 3号边缘子镜：逻辑驱动器编号到物理位置编号的映射
            0:  "7",  # 中心
            1:  "2a",                 3:  "4a",                 5:  "1a",                 7:  "6b",                 9:  "3b",                  11:  "5b",                 13:  "2b",                 15:  "4b",                 17:  "1b",                 19: "6a",                  21: "3a",                  23: "5a",                    # outer
            2: outer2inner_Map["2a"], 4: outer2inner_Map["4a"], 6: outer2inner_Map["1a"], 8: outer2inner_Map["6b"], 10: outer2inner_Map["3b"], 12: outer2inner_Map["5b"], 14: outer2inner_Map["2b"], 16: outer2inner_Map["4b"], 18: outer2inner_Map["1b"], 20: outer2inner_Map["6a"], 22: outer2inner_Map["3a"], 24: outer2inner_Map["5a"]    # inner
    },
    4: {    # 4号边缘子镜：逻辑驱动器编号到物理位置编号的映射
        0:  "7", 1:  "1a", 2:  "11", 3:  "6b", 4:  "16", 5:  "3b", 6:  "10", 7:  "5b", 8:  "15", 9:  "2b",
        10: "9", 11: "4b", 12: "14", 13: "1b", 14: "8",  15: "6a", 16: "19", 17: "3a", 18: "13", 19: "5a", 20: "18", 21: "2a", 22: "12", 23: "4a", 24: "17"
    },
    5: {    # 5号边缘子镜：逻辑驱动器编号到物理位置编号的映射
        0:  "7", 1:  "3b", 2:  "11", 3:  "5b", 4:  "16", 5:  "2b", 6:  "10", 7:  "4b", 8:  "15", 9:  "1b",
        10: "9", 11: "6a", 12: "14", 13: "3a", 14: "8",  15: "5a", 16: "19", 17: "2a", 18: "13", 19: "4a", 20: "18", 21: "1a", 22: "12", 23: "6b", 24: "17"
    },
    6: {    # 6号边缘子镜：逻辑驱动器编号到物理位置编号的映射
        0:  "7", 1:  "2b", 2:  "11", 3:  "4b", 4:  "16", 5:  "1b", 6:  "10", 7:  "6a", 8:  "15", 9:  "3a",
        10: "9", 11: "5a", 12: "14", 13: "2a", 14: "8",  15: "4a", 16: "19", 17: "1a", 18: "13", 19: "6b", 20: "18", 21: "3b", 22: "12", 23: "5b", 24: "17"
    }
}


