from PySide2.QtWidgets import *
from PySide2.QtCore import *
from PySide2.QtGui import *


class ActuatorLinearityTest(QMainWindow):
    def __init__(self, amplifier_list):
        self.amplifier_list = amplifier_list
        self.amplifier_index = 0
        
        # 中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QHBoxLayout(central_widget)

        group_box = QGroupBox(f"放大器: {self.amplifier_list[{self.amplifier_index++}]}")
        group_layout = QVBoxLayout(group_box)
        
        pass