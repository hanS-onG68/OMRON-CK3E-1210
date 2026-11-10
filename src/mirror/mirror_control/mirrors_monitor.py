import sys, atexit, asyncio, traceback,signal
import numpy as np
from PySide2.QtWidgets import *
from PySide2.QtCore import *
from PySide2.QtGui import *
import pyqtgraph as pg
from typing import List, Tuple
import random  # 用于模拟传感器数据
from mirror.mirror_control.mirror_controller import MirrorsController
from mirror.mirror_control.config import Actuator2Pon_Map

# 设置pyqtgraph选项
pg.setConfigOptions(antialias=True, useOpenGL=True)  # 启用OpenGL加速

class HexagonSensorVisualizer(QMainWindow):
    """六边形传感器可视化主窗口"""
    def __init__(self):
        super().__init__()
        self.actuators_per_mirror = 25  #（中心镜不部署传感器,每个边缘子镜的传感器数量）
        self.mirror_count = 6           # 6个边缘子镜
        self.mirror = MirrorsController(is_domestic=True)       # 国产放大器
        self.sensor_data = self.mirror.Force          # 传感器数据
        self.motor_status = self.mirror.motors_status # 电机的状态：true表示正常，false表示故障
        self.change_val = self.mirror.change_va
        self.close_future = None  # 用于在窗口关闭时触发的Future对象
        
        # 几何参数：对边距离2米
        self.flat_to_flat = 1                          # 对边距离
        self.side_length = self.flat_to_flat / np.sqrt(3)   # 边长 ≈ 1.1547米
        
        self.hexagon_centers = self.calculate_hexagon_centers()            # 各个子镜的中心位置（x,y) - 蜂巢紧密排列
        self.sensor_positions = np.array(self.calculate_hexagon_layout())  # 各个传感器的位置坐标（x,y): 依次从物理标定的边缘子镜1~边缘子镜6，子镜1的逻辑0（即self.sensor_positions[0,0]）对应mirror.Force[0][0]; 形状：6x25x2
        
        # 保存初始视图范围
        self.initial_view_range = None
        
        # 初始化属性
        self.min_label = None
        self.max_label = None
        self.avg_label = None
        self.std_label = None
        self.cmap_combo = None
        self.filter_check = None
        self.filter_enabled = False
        self.threshold_slider = None
        self.threshold = 80
        self.threshold_label = None
        
        # 性能优化：预创建对象池
        self.connection_lines = []
        self.hexagon_items = []
        
        # 分子镜统计标签
        self.mirror_stats_labels = []
        self.mir_enabled = np.full(shape=self.mirror_count, fill_value=False)   # 用于存储每个边缘子镜的启用状态
        self.mir_check = np.empty(shape=self.mirror_count, dtype=object)     # 用于存储每个边缘子镜的复选框
        self.visible_mask = np.full(shape=(self.mirror_count, self.actuators_per_mirror), fill_value=False)   # 用于存储每个边缘子镜的可见性状态,默认都不可见
    

        # 当前颜色映射
        self.current_colormap = pg.colormap.get('viridis')
        
        self.setup_ui()
        
        # 设置定时器模拟实时数据更新
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_simulation_data)
        
        # 初始化
        self.initialize_scatter_textItem()

        # 保存初始视图范围
        self.save_initial_view_range()

    def set_close_future(self, close_future):
        """设置关闭窗口时的Future对象"""
        self.close_future = close_future
    def closeEvent(self, event):
        """窗口关闭事件处理"""
        if self.close_future and not self.close_future.done():
            self.close_future.set_result(None)  # 设置Future对象的结果，通知外部事件循环
        super().closeEvent(event)  # 接受关闭事件

    async def monitor_task(self):
        # 后台开启传感器数据处理流程
        try:
            await asyncio.gather(
                self.mirror.run(),
                self.update_scatter()
            )
        except Exception as e:
            print(f"出现异常：{str(e)}")
            traceback.print_exc()  

    def calculate_hexagon_centers(self):
        """计算7个六边形的中心位置 - 真正蜂巢紧密排列"""
        # 中心六边形
        centers = [(0, 0)]   # 中心子镜的中心位置
        
        # 中心间距 = 对边距离 = 2米
        center_distance = self.flat_to_flat  # 2.0米
        
        # 六个方向的角度（从水平向右开始，逆时针60°递增）
        angles = [90, 30, -30, -90, -150, -210]  # 边缘子镜的中心位置
        
        for angle in angles:
            # 将角度转换为弧度
            rad = np.radians(angle)
            x = center_distance * np.cos(rad)
            y = center_distance * np.sin(rad)
            centers.append((x, y)) 
        return centers

    def calculate_hexagon_layout(self) -> List[Tuple[float, float]]:
        """计算蜂窝状传感器布局坐标 - 蜂巢紧密排列"""
        positions = [[] for _ in range(6)]  # 6个空列表，分别对应6个周边子镜的传感器点位
        
        # 7个六边形的中心位置
        centers = self.hexagon_centers
        
        # 每个六边形内12个传感器的角度分布
        angles = np.linspace(0, 2*np.pi, 13)[:-1]  # 12个等分角度=2π/12=30°：0、30、60、90、120、150、180、210、240、270、300、330
        
        # 传感器到六边形中心的距离（内圈和外圈）
        inner_radius = self.side_length * 0.46  # 内圈半径
        outer_radius = self.side_length * 0.75  # 外圈半径 0.75
        
        # 只计算周围6个子镜的传感器（中心镜不部署传感器）
        for center_idx, (cx, cy) in enumerate(centers):
            if center_idx == 0:  # 跳过中心镜（镜1）
                continue
            positions[center_idx-1].append((cx, cy))
            for i, angle in enumerate(angles):
                x1 = cx + inner_radius * np.cos(angle)
                y1 = cy + inner_radius * np.sin(angle)
                x2 = cx + outer_radius * np.cos(angle)
                y2 = cy + outer_radius * np.sin(angle)
                positions[center_idx-1].append((x2, y2))   # 外圈：奇数下标
                positions[center_idx-1].append((x1, y1))   # 内圈：偶数下标
        return positions  
        # 一个子镜的25个传感器：
                #self.sensor_positions:    0     1     2    3     4     5    6     7    8     9    10    11    12   13    14   15    16    17   18    19    20   21    22   23    24 
                                        # 中心  外圈  内圈  外圈  内圈  外圈  内圈  外圈  内圈  外圈  内圈  外圈  内圈  外圈  内圈  外圈  内圈  外圈  内圈  外圈  内圈  外圈  内圈  外圈  内圈
                                        #        0°   0°   30°   30°  60°   60°   90°   90°  120°  120°  150° 150°  180° 180°  210°  210° 240°  240°  270° 270°  300°  300° 330°  330°

    def get_available_colormaps(self):
        """获取可用的颜色映射列表 - 只保留三种"""
        # 只保留viridis、plasma、inferno三种颜色映射
        return ['viridis', 'plasma', 'inferno']
    
    def setup_ui(self):
        """设置用户界面"""
        self.setWindowTitle("传感器实时监控系统")
        
        # 获取屏幕尺寸，设置窗口大小
        screen = QApplication.primaryScreen().availableGeometry()
        self.setGeometry(100, 100, int(screen.width() * 0.5), int(screen.height() * 0.9))
        self.move(screen.left() + 50, screen.top() + 20)
        
        # 中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(5, 5, 5, 5)
        
        # 创建绘图区域
        plot_widget = self.create_plot_widget()
        
        # 创建控制面板
        control_panel = self.create_control_panel()
        
        # 添加到布局
        main_layout.addWidget(plot_widget, 5)
        main_layout.addWidget(control_panel, 1)
        
        # 设置控制面板固定宽度
        control_panel.setMinimumWidth(200)   # 保证不被压得太窄
        control_panel.setMaximumWidth(300)   # 可选：防止过宽
        
    def create_plot_widget(self) -> pg.GraphicsLayoutWidget:
        """创建pyqtgraph绘图部件"""
        plot_widget = pg.GraphicsLayoutWidget()
        plot_widget.setBackground('#0f0f0f')
        
        # 创建主绘图区域
        self.main_plot = plot_widget.addPlot(row=0, col=0)
        self.main_plot.setAspectLocked(True)
        # self.main_plot.setLabel('left', 'Y坐标 (米)')
        # self.main_plot.setLabel('bottom', 'X坐标 (米)')
        
        # 设置视图范围
        # self.main_plot.setXRange(-3.0, 3.0)
        # self.main_plot.setYRange(-3.0, 3.0)
        self.main_plot.hideAxis('left')    # 隐藏左侧Y轴标尺
        self.main_plot.hideAxis('bottom')  # 隐藏下方X轴标尺
        
        # 绘制六边形网格
        self.draw_hexagon_grid()
        
        # 创建散点图项（传感器点） # 性能优化：预创建散点图项对象池
        self.scatter_plot = pg.ScatterPlotItem(
            pos=self.sensor_positions.reshape(-1, 2),  # 将二维数组展平为N x 2的形状
            size=16,
            pen=pg.mkPen('#ffffff', width=1.5),
            # pen=pg.mkPen(None),
            brush=pg.mkBrush(255, 255, 255, 180),
            # symbol='s',  # 方形
            pxMode=True # False：size 的单位是图表数据坐标。当你放大图表时，点会跟着变得巨大；缩小时点会变小。
        )
        # 创建散点图项（电机是否故障） # 性能优化：预创建散点图项对象池
        self.scatter_plot_motor_status = pg.ScatterPlotItem(
            pos=self.sensor_positions.reshape(-1, 2),  # 将二维数组展平为N x 2的形状
            size=12,
            # pen=pg.mkPen(None),  # 无边框
            pen=pg.mkPen('#333333', width=.5),
            brush=pg.mkBrush(0, 255, 0),  # 填充绿色
            # offset=(20000, 0),  # ★ 黑科技：整个散点图向右平移 18 个屏幕像素
            # symbol='s'
            pxMode=True # true:size 的单位是屏幕像素。不管你怎么放大缩小图表，这个绿点的大小在屏幕上永远不变。
        )
        # transform = QTransform()
        # transform.translate(0.05, 0) 
        # self.scatter_plot_motor_status.setTransform(transform)
        self.main_plot.addItem(self.scatter_plot)
        self.main_plot.addItem(self.scatter_plot_motor_status)
        
        # 颜色条
        self.colorbar = self.create_colorbar()
        plot_widget.addItem(self.colorbar, row=0, col=1)
        
        return plot_widget
    
    def save_initial_view_range(self):
        """保存初始视图范围"""
        view_range = self.main_plot.viewRange()
        if view_range:
            self.initial_view_range = {
                'x_range': (view_range[0][0], view_range[0][1]),
                'y_range': (view_range[1][0], view_range[1][1])
            }
    
    def restore_initial_view(self):
        """恢复初始视图范围"""
        if self.initial_view_range:
            self.main_plot.setXRange(*self.initial_view_range['x_range'])
            self.main_plot.setYRange(*self.initial_view_range['y_range'])
    
    def create_colorbar(self) -> pg.ColorBarItem:
        """创建颜色条"""
        colorbar = pg.ColorBarItem(
            colorMap=self.current_colormap,
            orientation='vertical',
            label='传感器数值',
            limits=(0, 100)
        )
        return colorbar
    
    def update_colorbar(self):
        """更新颜色条的颜色映射"""
        if hasattr(self, 'colorbar'):
            self.colorbar.setColorMap(self.current_colormap)
    
    def draw_hexagon_grid(self):
        """绘制六边形网格背景和中心圆形"""
        def _get_hexagon_vertices(center_x, center_y):
            vertices = []
            for i in range(6):
                angle = 2 * np.pi/6 * i
                x = center_x + self.side_length * np.cos(angle)
                y = center_y + self.side_length * np.sin(angle)
                vertices.append((x, y))
            return vertices

        self.hexagon_items = []
        
        for idx, (cx, cy) in enumerate(self.hexagon_centers):
            # 如果是中心镜（镜0），绘制半径为1米的圆形
            if idx == 0:
                # 绘制圆形
                angles = np.linspace(0, 2*np.pi, 100)
                circle_x = cx + 0.3 * np.cos(angles)
                circle_y = cy + 0.3 * np.sin(angles)
                
                circle = pg.PlotDataItem(
                    circle_x, circle_y,
                    pen=pg.mkPen('#cccccc', width=2, style=Qt.SolidLine),
                    connect='all'
                )
                self.main_plot.addItem(circle)
                self.hexagon_items.append(circle)
                
                # 添加圆形中心标签
                circle_text = pg.TextItem(f"子镜{idx}", color='#ffaa00', anchor=(0.5, 0.5))
                circle_text.setPos(cx, cy)
                circle_text.setFont(QFont('Arial', 12))
                self.main_plot.addItem(circle_text)
                self.hexagon_items.append(circle_text)
                continue  # 跳过中心镜的六边形绘制
            vertices = _get_hexagon_vertices(cx, cy)   # 获取六边形顶点         
            vertices.append(vertices[0])               # 闭合六边形: 添加第一个顶点到末尾
            vertices_array = np.array(vertices)        # 转换为numpy数组
            # 创建六边形边界
            hex_line = pg.PlotDataItem(
                vertices_array[:, 0], 
                vertices_array[:, 1],
                pen=pg.mkPen('#4CAF50', width=2, style=Qt.SolidLine),
                connect='all'
            )
            self.main_plot.addItem(hex_line)
            self.hexagon_items.append(hex_line)
            
            # 添加六边形中心标签
            text = pg.TextItem(f"子镜{idx}", color='#4CAF50', anchor=(0.5, 1.5))
            text.setPos(cx, cy+0.01)
            text.setFont(QFont('Arial', 12))
            self.main_plot.addItem(text)
            self.hexagon_items.append(text)
    
    def create_control_panel(self) -> QWidget:
        """创建右侧控制面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(5)
        layout.setContentsMargins(5, 5, 5, 5)

        inner_widget = QWidget()
        inner_layout = QVBoxLayout(inner_widget)
        inner_layout.setSpacing(5)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        
        # 标题
        title = QLabel("传感器监视系统")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #4CAF50;")
        title.setAlignment(Qt.AlignCenter)
        inner_layout.addWidget(title)
        
        # 分隔线
        inner_layout.addWidget(self.create_h_line())

        # 视图控制
        view_group = QGroupBox("视图控制")
        view_layout = QVBoxLayout()
        
        restore_btn = QPushButton("还原视图 (恢复初始缩放)")
        restore_btn.clicked.connect(self.restore_initial_view)
        restore_btn.setStyleSheet("background-color: #4CAF50; color: white; padding: 5px;")
        view_layout.addWidget(restore_btn)
        
        view_info = QLabel("视图控制说明:")
        view_info.setStyleSheet("font-weight: bold; color: #cccccc;")
        view_layout.addWidget(view_info)
        
        view_instructions = QLabel(
            "• 鼠标滚轮: 缩放视图\n"
            "• 鼠标拖动: 平移视图\n"
            "• 右键拖动: 框选放大\n"
            "• 双击: 自动缩放到适合\n"
            "• 点击上方按钮还原初始视图"
        )
        view_instructions.setStyleSheet("color: #999999; font-size: 10pt;")
        view_layout.addWidget(view_instructions)
        
        view_group.setLayout(view_layout)
        inner_layout.addWidget(view_group)
        
        # 数据更新频率
        freq_group = QGroupBox("更新设置")
        freq_layout = QVBoxLayout()
        
        freq_sub_layout = QHBoxLayout()
        freq_sub_layout.addWidget(QLabel("更新频率:"))
        self.freq_combo = QComboBox()
        self.freq_combo.addItems(["1 Hz", "5 Hz", "10 Hz", "20 Hz", "50 Hz"])
        self.freq_combo.setCurrentText("5 Hz")
        self.freq_combo.currentTextChanged.connect(self.update_timer_interval)
        freq_sub_layout.addWidget(self.freq_combo)
        freq_layout.addLayout(freq_sub_layout)
        
        freq_group.setLayout(freq_layout)
        inner_layout.addWidget(freq_group)
        
        # 颜色映射选择
        cmap_group = QGroupBox("显示设置")
        cmap_layout = QVBoxLayout()
        
        cmap_sub_layout = QHBoxLayout()
        cmap_sub_layout.addWidget(QLabel("颜色方案:"))
        self.cmap_combo = QComboBox()
        available_colormaps = self.get_available_colormaps()
        self.cmap_combo.addItems(available_colormaps)
        self.cmap_combo.setCurrentText("viridis")
        self.cmap_combo.currentTextChanged.connect(self.on_colormap_changed)
        cmap_sub_layout.addWidget(self.cmap_combo)
        cmap_layout.addLayout(cmap_sub_layout)
        
        cmap_group.setLayout(cmap_layout)
        inner_layout.addWidget(cmap_group)
        
        # 传感器筛选
        filter_group = QGroupBox("筛选设置")
        filter_layout = QVBoxLayout()
        
        self.filter_check = QCheckBox("仅显示异常传感器")
        self.filter_check.stateChanged.connect(self.update_visualization)
        filter_layout.addWidget(self.filter_check)
        
        threshold_layout = QHBoxLayout()
        threshold_layout.addWidget(QLabel("阈值:"))
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(0, 100)
        self.threshold_slider.setValue(80)
        self.threshold_slider.valueChanged.connect(self.update_visualization)
        threshold_layout.addWidget(self.threshold_slider)
        self.threshold_label = QLabel("80")
        threshold_layout.addWidget(self.threshold_label)
        filter_layout.addLayout(threshold_layout)

        # 调测使用
        mir_sub_layout = QGridLayout()
        mir_sub_layout.addWidget(QLabel("要显示的子镜:"), 0, 0, 1, 3)
        for mir_idx in range(self.mirror_count):
            self.mir_check[mir_idx] = QCheckBox(f"子镜{mir_idx+1}")
            self.mir_check[mir_idx].stateChanged.connect(self.update_visualization)
            mir_sub_layout.addWidget(self.mir_check[mir_idx], mir_idx // 3 + 1, mir_idx % 3)
        filter_layout.addLayout(mir_sub_layout)

    
        filter_group.setLayout(filter_layout)
        inner_layout.addWidget(filter_group)
        
        # 全局数据统计
        global_stats_group = QGroupBox("全局数据统计")
        global_stats_layout = QVBoxLayout()
        self.min_label = QLabel("最小值: --")
        self.max_label = QLabel("最大值: --")
        self.avg_label = QLabel("平均值: --")
        self.std_label = QLabel("标准差: --")
        
        global_stats_layout.addWidget(self.min_label)
        global_stats_layout.addWidget(self.max_label)
        global_stats_layout.addWidget(self.avg_label)
        global_stats_layout.addWidget(self.std_label)
        
        global_stats_group.setLayout(global_stats_layout)
        inner_layout.addWidget(global_stats_group)
        
        # 分子镜数据统计（现在只有6个子镜）
        mirror_stats_group = QGroupBox("分子镜数据统计")
        mirror_stats_scroll = QScrollArea()
        mirror_stats_scroll.setWidgetResizable(True)
        mirror_stats_scroll.setMaximumHeight(250)
        
        mirror_stats_widget = QWidget()
        mirror_stats_layout = QVBoxLayout(mirror_stats_widget)
        
        self.mirror_stats_labels = []
        for i in range(self.mirror_count):  # 只有6个边缘子镜有传感器
            mirror_group = QGroupBox(f"边缘子镜{i+1}")  # 从镜1开始
            mirror_group_layout = QVBoxLayout()
            
            min_label = QLabel("最小值: --")
            max_label = QLabel("最大值: --")
            avg_label = QLabel("平均值: --")
            std_label = QLabel("标准差: --")
            
            mirror_group_layout.addWidget(min_label)
            mirror_group_layout.addWidget(max_label)
            mirror_group_layout.addWidget(avg_label)
            mirror_group_layout.addWidget(std_label)
            
            mirror_group.setLayout(mirror_group_layout)
            mirror_stats_layout.addWidget(mirror_group)
            
            self.mirror_stats_labels.append({
                'min': min_label,
                'max': max_label,
                'avg': avg_label,
                'std': std_label
            })
        
        mirror_stats_layout.addStretch()
        mirror_stats_widget.setLayout(mirror_stats_layout)
        mirror_stats_scroll.setWidget(mirror_stats_widget)
        mirror_stats_group_layout = QVBoxLayout()
        mirror_stats_group_layout.addWidget(mirror_stats_scroll)
        mirror_stats_group.setLayout(mirror_stats_group_layout)
        inner_layout.addWidget(mirror_stats_group)
        
        # 控制按钮
        btn_group = QGroupBox("控制")
        btn_layout = QVBoxLayout()
        
        self.test_matrix_btn = QPushButton("系统响应矩阵测试")
        self.test_matrix_btn.clicked.connect(self.test_matrix)
        btn_layout.addWidget(self.test_matrix_btn)
        
        self.start_btn = QPushButton("开始模拟")
        self.start_btn.clicked.connect(self.start_simulation)
        btn_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("暂停模拟")
        self.stop_btn.clicked.connect(self.stop_simulation)
        self.stop_btn.setEnabled(False)
        btn_layout.addWidget(self.stop_btn)
        
        self.reset_btn = QPushButton("重置数据")
        self.reset_btn.clicked.connect(self.reset_data)
        btn_layout.addWidget(self.reset_btn)
        
        self.export_btn = QPushButton("导出数据")
        self.export_btn.clicked.connect(self.export_data)
        btn_layout.addWidget(self.export_btn)        
        
        btn_group.setLayout(btn_layout)
        inner_layout.addWidget(btn_group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)      # 关键：让内部 widget 宽度跟随
        scroll.setWidget(inner_widget)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # 不需要横向滚动条
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(scroll)
        
        # layout.addStretch()
        return panel
    
    def create_h_line(self):
        """创建水平分隔线"""
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("background-color: #666;")
        return line
    
    def on_colormap_changed(self):
        """颜色映射改变时的处理"""
        if not self.cmap_combo:
            return
            
        cmap_name = self.cmap_combo.currentText()
        
        if cmap_name in ['viridis', 'plasma', 'inferno']:
            self.current_colormap = pg.colormap.get(cmap_name)
        else:
            self.current_colormap = pg.colormap.get('viridis')
            self.cmap_combo.setCurrentText('viridis')
        
        self.update_colorbar()
        self.update_visualization()
    
    def update_timer_interval(self):
        """更新定时器间隔"""
        freq_text = self.freq_combo.currentText()
        freq = int(freq_text.split()[0])
        
        if freq > 0:
            interval = int(1000 / freq)       # 间隔时间: 1000 / freq(ms)
            self.timer.setInterval(interval)  # 设置定时器触发间隔
    
    def update_simulation_data(self):
        """更新模拟数据"""
        for i in range(25):
            change = np.random.normal(0, 1.5)
            for (i, j), val in np.ndenumerate(self.sensor_data):
                self.sensor_data[i][j] = val + change

    def update_visualization(self):
        """更新可视化"""
        if self.cmap_combo is None:
            return
        
        self.threshold = 80
        if self.threshold_slider:
            self.threshold = self.threshold_slider.value()
            if self.threshold_label:
                self.threshold_label.setText(str(self.threshold))
        
        self.filter_enabled = False
        if self.filter_check:  # 异常传感器的复选框是否创建成功
            self.filter_enabled = self.filter_check.isChecked()

        for mir_idx in range(self.mirror_count):
            if self.mir_check[mir_idx]:
                self.mir_enabled[mir_idx] = self.mir_check[mir_idx].isChecked()
                self.visible_mask[mir_idx] = self.mir_enabled[mir_idx]

    def initialize_scatter_textItem(self):
        """初始化传感器数值文本项"""
        self.current_text_items = np.empty((self.mirror_count, self.actuators_per_mirror), dtype=object)  # 用于显示传感器数值的文本项
        self.current_change_val_text_items = np.empty((self.mirror_count, self.actuators_per_mirror), dtype=object)  # 用于显示差值的文本项
        self.current_motor_id_text_items = np.empty((self.mirror_count, self.actuators_per_mirror), dtype=object)  # 用于显示电机编号的文本项
        sensor_positions = self.sensor_positions.reshape(-1, 2)  # 展平为150 x 2的形状
        for idx, (x, y) in enumerate(sensor_positions):
            i, j = idx // self.actuators_per_mirror, idx % self.actuators_per_mirror   # i表示镜子id, j表示传感器相对该子镜的编号, i*25+j就是传感器的物理位置，对应文件setting/Actuator_Mapping.csv中的actuator_id
            point = self.scatter_plot.points()[idx]
            motor_status_point_data = self.scatter_plot_motor_status.data
            motor_status_point_data['x'][idx] = x + 0.05
            # if idx%25%2:  # 外圈点用方形，内圈点用圆形
            #     point.setSymbol('s')
            value = self.sensor_data[i, j]  # 获取对应的传感器数值
            text = pg.TextItem(f"{value:.1f}", anchor=(0.5, 0))
            text.setPos(x, y-0.02)
            text.setColor('#ffffff')
            text.setFont(QFont('Arial', 8, QFont.Bold))

            motor_id = self.mirror._axis_idx[i, j]  # 获取对应的电机编号
            text_motor_id = pg.TextItem(f"{motor_id}", anchor=(0.5, 0.5))
            text_motor_id.setPos(x+0.05, y)
            text_motor_id.setColor('#000000')
            text_motor_id.setFont(QFont('Arial', 7, QFont.Bold))

            change_val = self.change_val[i,j]  # 获取对应的差值
            text_change_val = pg.TextItem(f"△={change_val:.1f}", anchor=(0.5, 1))
            text_change_val.setPos(x, y+0.02)
            text_change_val.setColor('#ffffff')
            text_change_val.setFont(QFont('Arial', 7.5, QFont.Bold))

            phy_id = Actuator2Pon_Map[i+1][j]  # 促动器编号(从1开始显示)
            pos_flag = pg.TextItem(f"{phy_id}", anchor=(0.5, 0.5))
            pos_flag.setPos(x, y)
            pos_flag.setColor('#ffff00')
            pos_flag.setFont(QFont('Arial', 7, QFont.Bold))

            self.main_plot.addItem(pos_flag)
            self.main_plot.addItem(text)
            self.main_plot.addItem(text_change_val)
            self.main_plot.addItem(text_motor_id)

            self.current_text_items[i, j] = text
            self.current_change_val_text_items[i, j] = text_change_val
            self.current_motor_id_text_items[i, j] = text_motor_id
        self.scatter_plot.update()
        self.scatter_plot_motor_status.update()
    
    async def update_scatter(self):
        while True:
            self.update_global_statistics()
            self.update_mirror_statistics()
            self.scatter_plot.setPointsVisible(self.visible_mask.ravel().tolist())
            self.scatter_plot_motor_status.setPointsVisible(self.visible_mask.ravel().tolist())
            self.sensor_data = self.mirror.Force
            self.motor_status = self.mirror.motors_status
            self.change_val = self.mirror.change_va
            for (i, j), text_item in np.ndenumerate(self.current_text_items):
                if not self.visible_mask[i, 0]:   # 子镜不可见
                    [item.setVisible(False) for item in self.current_text_items[i, :]]  # 隐藏该子镜的所有传感器文本项
                    [item.setVisible(False) for item in self.current_change_val_text_items[i, :]]  # 隐藏该子镜的所有传感器文本项
                    continue
                value = self.sensor_data[i, j]
                text_item.setText(f"{value:.2f}")
                change_val = self.change_val[i, j]
                change_val_text = self.current_change_val_text_items[i, j]
                change_val_text.setText(f"△={change_val:.1f}")
                motor_id_text = self.current_motor_id_text_items[i, j]
                motor_id_text.setColor('#000000' if self.motor_status[i,j] else '#ffffff')  # 电机状态：绿色正常，红色故障

                if np.isnan(value):
                    color = QColor(128, 128, 128)  # 灰色
                else:
                    def normalize(value, data_min=-100, data_range=200):
                        normalized = (value - data_min) / data_range
                        return normalized
                    normalized = normalize(value)
                    color = self.current_colormap.mapToQColor(normalized)
                point = self.scatter_plot.points()[i * self.actuators_per_mirror + j]
                point.setBrush(color)
                motor_status_point = self.scatter_plot_motor_status.points()[i * self.actuators_per_mirror + j]
                motor_color = QColor(0, 255, 0) if self.motor_status[i,j]  else QColor(255, 0, 0) # 红色(255,0,0): 故障， 绿色：正常
                motor_status_point.setBrush(motor_color)
                # motor_status_point.setData(offset=(18, 0))
                
                visible = not (self.filter_enabled and value < self.threshold)
                point.setVisible(visible)  # visible 为 True 时，该数据点会在界面上正常显示
                text_item.setVisible(visible)
                change_val_text.setVisible(visible)
                motor_status_point.setVisible(visible)
            self.scatter_plot.update()
            self.scatter_plot_motor_status.update()
            await asyncio.sleep(0.1)  # 0.2秒更新一次
    
    def update_global_statistics(self):
        """更新全局统计数据"""
        if len(self.sensor_data) > 0 and self.min_label:
            self.min_label.setText(f"最小值: {self.sensor_data.min():.2f}")
            self.max_label.setText(f"最大值: {self.sensor_data.max():.2f}")
            self.avg_label.setText(f"平均值: {self.sensor_data.mean():.2f}")
            self.std_label.setText(f"标准差: {self.sensor_data.std():.2f}")
    
    def update_mirror_statistics(self):
        """更新分子镜统计数据"""
        if not self.mirror_stats_labels:
            return
        
        for mirror_idx in range(self.mirror_count):  # 只有6个边缘子镜
            mirror_data = self.sensor_data[mirror_idx, :]
            
            if len(mirror_data) > 0:
                min_val = mirror_data.min()
                max_val = mirror_data.max()
                avg_val = mirror_data.mean()
                std_val = mirror_data.std()
                
                labels = self.mirror_stats_labels[mirror_idx]
                labels['min'].setText(f"最小值: {min_val:.2f}")
                labels['max'].setText(f"最大值: {max_val:.2f}")
                labels['avg'].setText(f"平均值: {avg_val:.2f}")
                labels['std'].setText(f"标准差: {std_val:.2f}")
    
    def start_simulation(self):
        """开始模拟"""
        self.timer.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
    
    def stop_simulation(self):
        """停止模拟"""
        self.timer.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
    
    def reset_data(self):
        """重置数据"""
        self.sensor_data = np.zeros(shape=(self.mirror_count, self.actuators_per_mirror))
        self.update_visualization()
    
    def export_data(self):
        """导出数据"""
        filename, _ = QFileDialog.getSaveFileName(
            self, "导出数据", "sensor_data.csv", "CSV Files (*.csv);;All Files (*)"
        )
        
        if filename:
            try:
                import csv
                with open(filename, 'w', newline='') as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow(['传感器ID', '子镜编号', 'X坐标(米)', 'Y坐标(米)', '数值', '状态'])
                    
                    sensor_positions = self.sensor_positions.reshape(-1, 2)  # 展平为150 x 2的形状
                    for idx, (x, y) in enumerate(sensor_positions):
                        i, j = idx // self.actuators_per_mirror, idx % self.actuators_per_mirror   # i表示镜子id,j表示传感器相对该子镜的编号， i*25+j就是传感器的物理位置，对应文件setting/Actuator_Mapping.csv中的actuator_id（i = actuator_id）
                        value = self.sensor_data[i, j]
                        threshold = self.threshold_slider.value() if self.threshold_slider else 80
                        status = "正常" if value <= threshold else "异常"
                        writer.writerow([idx, 0, f"{x:.3f}", f"{y:.3f}", f"{value:.3f}", status])
                
                QMessageBox.information(self, "成功", f"72个传感器数据已导出到:\n{filename}")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"导出失败: {str(e)}")

    def test_matrix(self):
        """系统响应矩阵测试"""
        dialog = QDialog(self)
        dialog.setWindowTitle("系统响应矩阵测试 - 促动器选择")
        dialog.setModal(False)           # 非模态
        dialog.setMinimumWidth(1000)
        dialog.setMinimumHeight(600)

        main_layout = QVBoxLayout(dialog)

        # 说明标签
        info_label = QLabel("请选择要激活的促动器，并输入测试力值（N）")
        info_label.setStyleSheet("font-weight: bold; margin: 5px;")
        main_layout.addWidget(info_label)

        # 创建一个滚动区域，用于放置子镜分组
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        main_layout.addWidget(scroll)

        # 滚动区域内的容器
        container = QWidget()
        container_layout = QVBoxLayout(container)
        scroll.setWidget(container)

        # 存储子镜分组控件
        self.test_dialog_groups = []
        
        # 为每个子镜创建分组框
        for mirror_idx in range(self.mirror_count):  # 只有6个边缘子镜有促动器
            start_idx = mirror_idx * self.actuators_per_mirror
            end_idx = start_idx + self.actuators_per_mirror
            actuator_indices = list(range(start_idx, end_idx))  # 物理位置的id=settings/Actuator_Mapping.csv文件中的actuator_id
            group_box = QGroupBox(f"子镜{mirror_idx+1} （促动器 {start_idx} ~ {end_idx}）")
            group_layout = QVBoxLayout(group_box)

            # 子镜全选复选框
            select_all_cb = QCheckBox("全选此子镜所有促动器")
            select_all_cb.setStyleSheet("font-weight: bold; color: #4CAF50;")
            group_layout.addWidget(select_all_cb)

            # 促动器列表区域（网格布局）
            actuator_grid = QWidget()
            grid_layout = QGridLayout(actuator_grid)
            # 每行放5个复选框（25个促动器，5x5）
            self.checkboxes = []
            for i, act_idx in enumerate(actuator_indices):
                act_idx = act_idx % 25
                phy_id = Actuator2Pon_Map[mirror_idx+1][act_idx]  # 促动器编号从1开始显示
                act_name = f"促动器-{act_idx}({phy_id})"
                cb = QCheckBox(f"{act_name}")
                row = i // 8
                col = i % 8
                grid_layout.addWidget(cb, row, col)
                self.checkboxes.append(cb)

            # 联动：对应位置的两个径向促动器需要联动(内圈：200N，外圈：100N)
            for i in range(2, 14, 2):  # 内圈
                self.checkboxes[i].toggled.connect(lambda checked, cb=self.checkboxes[i+12]: self.sync_cb(checked, cb))
                self.checkboxes[i+12].toggled.connect(lambda checked, cb=self.checkboxes[i]: self.sync_cb(checked, cb))

            group_layout.addWidget(actuator_grid)
            group_box.setLayout(group_layout)
            container_layout.addWidget(group_box)

            # 存储分组信息
            self.test_dialog_groups.append({
                'group_box': group_box,
                'checkboxes': self.checkboxes,
                'select_all': select_all_cb,
                'mirror_idx': mirror_idx,
                'start_idx': start_idx
            })

            # 连接子镜全选信号
            select_all_cb.stateChanged.connect(
                lambda state, cbs=self.checkboxes: self._on_select_all_mirror(state, cbs)  # state是stateChanged信号自带的参数，所有绑定这个信号的槽函数，都可以使用它
            )

        container_layout.addStretch()

        # 下方控制区
        self.control_layout = QGridLayout()
        main_layout.addLayout(self.control_layout)
        def create_control_layout(label_name, grid_row):
            self.control_layout.addWidget(QLabel(label_name +"(N):"), grid_row, 0)
            force_edit = QLineEdit()
            force_edit.setValidator(QDoubleValidator())   # 只允许数字
            self.control_layout.addWidget(force_edit, grid_row, 1)

            execute_btn = QPushButton("执行")
            execute_btn.setStyleSheet("background-color: #4CAF50; color: white; padding: 5px;")
            self.control_layout.addWidget(execute_btn, grid_row, 2)

            close_btn = QPushButton("关闭")
            self.control_layout.addWidget(close_btn, grid_row, 3)

            # 连接按钮事件
            execute_btn.clicked.connect(lambda: self._test_matrix_execute(dialog, grid_row))
            close_btn.clicked.connect(dialog.close)

        create_control_layout("目标力", 0)
        create_control_layout("增加力", 1)
        # 显示对话框（非模态）
        dialog.show()
        # 保存对话框引用，防止被垃圾回收
        self.test_matrix_dialog = dialog

    def sync_cb(self, checked, cb):
        cb.blockSignals(True)  # 暂停信号发射
        cb.setChecked(checked) # 修改状态
        cb.blockSignals(False) # 恢复信号发射

    def _on_select_all_mirror(self, state, checkboxes):
        """子镜全选复选框的状态改变时，同步该组所有促动器复选框"""
        for cb in checkboxes:
            cb.setChecked(state == Qt.Checked)

    def _test_matrix_execute(self, dialog, grid_row):
        """执行测试：收集选中的促动器编号和目标力，并输出（可扩展为实际发送指令）"""
        try:
            if grid_row == 0:
                target_force = self.control_layout.itemAtPosition(0, 1).widget().text()     # (0,1)对应目标值的输入框
            else:
                increment_force = self.control_layout.itemAtPosition(1, 1).widget().text()  # (1,1)对应增量值的输入框
        except ValueError:
            QMessageBox.warning(dialog, "输入错误", "目标力必须为数字")
            return

        # 收集所有选中的促动器编号（全局索引，从1开始）
        selected_actuators = []
        for group in self.test_dialog_groups:
            for i, cb in enumerate(group['checkboxes']):
                if cb.isChecked():
                    act_global_index = group['start_idx'] + i  # 0-based
                    selected_actuators.append(act_global_index)
                    MirrorsController.Target[group["mirror_idx"]][i] = float(target_force) if grid_row == 0 else (MirrorsController.Target[group["mirror_idx"]][i] + float(increment_force))
                    print(f"子镜{group['mirror_idx']+1} 促动器{act_global_index} 设置目标力: {MirrorsController.Target[group['mirror_idx']][i]} N")
        if not selected_actuators:
            QMessageBox.information(dialog, "提示", "未选择任何促动器")
            return


def palette_set():
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.WindowText, Qt.white)
    palette.setColor(QPalette.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ToolTipBase, Qt.white)
    palette.setColor(QPalette.ToolTipText, Qt.white)
    palette.setColor(QPalette.Text, Qt.white)
    palette.setColor(QPalette.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ButtonText, Qt.white)
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, Qt.white)
    app.setPalette(palette)

if __name__ == "__main__":
    try:
        import os
        os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"  #  告诉 Qt 启用自动缩放（必须在创建 QApplication 之前设置）
        from qasync import QEventLoop, asyncSlot
        # ★★★ 在创建 QApplication 之前设置 ★★★
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)      #关键：关窗不自动停 loop
        with QEventLoop(app) as loop:
            asyncio.set_event_loop(loop)
            palette_set()
            window = HexagonSensorVisualizer()
            window.show()
            async def main():
                close_future = asyncio.get_running_loop().create_future()
                def on_sigint():
                    if not close_future.done():
                        close_future.set_result(None)
                try:
                    loop.add_signal_handler(signal.SIGINT, on_sigint)
                except (NotImplementedError, RuntimeError):
                    pass    # Windows 等平台不支持，退回到 KeyboardInterrupt
                window.set_close_future(close_future)
                monitor_task = asyncio.create_task(window.monitor_task())
                try:
                    await close_future     # 关窗时唤醒
                except Exception as e:
                    traceback.print_exc()  # 打印完整堆栈
                    print(f"出现异常, e = {str(e)}")
                finally:
                    monitor_task.cancel()
                    await asyncio.gather(monitor_task, return_exceptions=True)
                    print(f"monitor_task is cancelled?: {monitor_task.cancelled()}")
                    try:
                        await window.mirror.close()
                    except Exception as e:
                        traceback.print_exc()  # 打印完整堆栈
                        print(f"出现异常, e = {str(e)}")
            try:
                loop.run_until_complete(main())
            except KeyboardInterrupt:
                print(f"用户中断程序")
    except Exception:
        traceback.print_exc()
    finally:
        pass
        