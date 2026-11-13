import os
import matplotlib
# 强制校验后端，避免有人误改顺序导致GUI阻塞
if matplotlib.get_backend().lower() != 'agg':
    matplotlib.use('Agg')
matplotlib.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei']
matplotlib.rcParams['axes.unicode_minus'] = False
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from scipy.stats import pearsonr, spearmanr, linregress
from mirror.logger import setup_logger
from typing import Optional, List
import re

# 模块级日志配置，禁止向上传播，避免日志重复
logger = setup_logger()
logger.propagate = False

# -------------------------- 可配置参数，直接改这里即可 --------------------------
CONFIG = {
    "corr_p_threshold": 0.05,       # 相关性P值阈值，低于则认为显著
    "corr_r_threshold": 0.6,        # 相关系数绝对值阈值，高于则认为强相关
    "fig_dpi": 300,                 # 图片分辨率
    "fig_format": "png",            # 图片格式：png/svg/pdf
    "show_residual_plot": True,     # 是否生成残差图
    "auto_create_dir": True,        # 自动创建图片保存目录
    "enable_chinese_support": True, # 自动适配中文显示，避免列名中文乱码
}
# -----------------------------------------------------------------------------


kp = np.full(150, np.nan)

class DataAnalyzer:
    """数据分析类，包含相关性分析和趋势图绘制
    适配工控电机测试场景，所有异常都内部捕获，不会向上抛出导致主程序崩溃
    """
    def __init__(self, csv_file: str):
        self.csv_file = os.path.abspath(csv_file)
        self.file_dir = os.path.dirname(self.csv_file)
        self.file_basename = os.path.splitext(os.path.basename(self.csv_file))[0]
        self.df = None
        # 统计结果缓存
        self.pearson_r = None
        self.pearson_p = None
        self.spearman_r = None
        self.spearman_p = None
        self.slope = None
        self.intercept = None
        self.r2 = None

        # 初始化自动读取CSV+数据校验
        self._load_and_validate_data()

        # 配置中文显示
        if CONFIG["enable_chinese_support"]:
            plt.rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'SimHei', 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False

    def _load_and_validate_data(self) -> bool:
        """内部方法：加载CSV并校验数据合法性，异常内部捕获"""
        try:
            if not os.path.exists(self.csv_file):
                logger.error(f"❌ CSV文件不存在: {self.csv_file}")
                return False
            self.df = pd.read_csv(self.csv_file)
            # 校验数据量：至少2行才能做相关性分析
            if len(self.df) < 2:
                logger.warning(f"⚠️ CSV数据量不足（仅{len(self.df)}行），无法进行相关性分析")
                return False
            # 自动剔除全空列/行
            self.df = self.df.dropna(axis=1, how='all').dropna(axis=0, how='all')
            return True
        except Exception as e:
            logger.error(f"❌ 加载CSV失败: {str(e)}")
            self.df = None
            return False

    def _validate_columns(self, x_col: str, y_col: str) -> bool:
        """内部方法：校验列名是否存在+数据是否合法"""
        if self.df is None:
            return False
        if x_col not in self.df.columns or y_col not in self.df.columns:
            logger.error(f"❌ 列名不存在，可用列: {list(self.df.columns)}")
            return False
        # 校验目标列是否有有效数值
        if self.df[x_col].isna().all() or self.df[y_col].isna().all():
            logger.error(f"❌ 目标列{x_col}/{y_col}全为空值")
            return False
        # 自动剔除单条空值
        self.df = self.df.dropna(subset=[x_col, y_col])
        return len(self.df) >= 2

    def is_linear_relationship(self, x_col: str, y_col: str) -> bool:
        """判断是否为线性关系，仅打日志不阻塞后续绘图"""
        if not self._validate_columns(x_col, y_col):
            return False
        try:
            self.pearson_r, self.pearson_p = pearsonr(self.df[x_col], self.df[y_col])
            self.spearman_r, self.spearman_p = spearmanr(self.df[x_col], self.df[y_col])
            
            logger.info(f"皮尔逊相关系数: {self.pearson_r:.4f} (p={self.pearson_p:.4f})")
            logger.info(f"斯皮尔曼相关系数: {self.spearman_r:.4f} (p={self.spearman_p:.4f})")

            # 相关性判断
            p_ok = self.pearson_p < CONFIG["corr_p_threshold"] and self.spearman_p < CONFIG["corr_p_threshold"]
            r_ok = abs(self.pearson_r) > CONFIG["corr_r_threshold"] and abs(self.spearman_r) > CONFIG["corr_r_threshold"]
            
            if p_ok and r_ok:
                logger.info("✅ 相关性显著且强线性相关")
                return True
            elif p_ok:
                logger.warning(f"⚠️ 相关性显著但线性程度一般（r={abs(self.pearson_r):.4f} < 阈值{CONFIG['corr_r_threshold']}）")
            else:
                logger.warning("⚠️ 相关性不显著，结果仅作趋势参考")
            return False
        except Exception as e:
            logger.error(f"❌ 相关性计算失败: {str(e)}")
            return False

    def plot_linear_relationship(self, x_col: str, y_col: str) -> tuple:
        """绘制线性回归拟合图+残差图，返回(斜率, 截距, 残差, 生成的图片路径)"""
        if not self._validate_columns(x_col, y_col):
            return None, None, None, None
        try:
            x = self.df[x_col]
            y = self.df[y_col]
            slope, intercept, r_value, p_value, std_err = linregress(x, y)  # 斜率，截距，皮尔逊系数的r值，皮尔逊系数的P值，截距的标准误差
            self.slope = slope           # 斜率
            self.intercept = intercept   # 截距
            self.r2 = r_value ** 2       # 拟合优度

            logger.info("🔍 线性关系分析结果:")
            logger.info("=" * 40)
            logger.info(f"回归方程: y = {slope:.6f}x + {intercept:.6f}")
            logger.info(f"相关系数 : r = {r_value:.6f}, p = {p_value:.6f}")
            logger.info(f"拟合优度 R²: {self.r2:.6f}")
            logger.info(f"斜率标准差: {std_err:.6f}")
            
            y_pred = slope * x + intercept
            residuals = y - y_pred
            logger.info(f"残差标准差: {residuals.std():.6f}")

            # 绘图逻辑
            plot_num = 2 if CONFIG["show_residual_plot"] else 1
            fig, axs = plt.subplots(1, plot_num, figsize=(6*plot_num, 6), constrained_layout=True)
            axs = np.atleast_1d(axs) # 兼容plot_num=1的情况

            # 拟合图
            # 统计信息注释
            stats_text = (
                f"Pearson:  r={self.pearson_r:.4f}, p={self.pearson_p:.4f}\n"
                f"Spearman: r={self.spearman_r:.4f}, p={self.spearman_p:.4f}"
            )
            ax = axs[0]
            ax.scatter(x, y, alpha=0.6, label='实测数据')
            ax.plot(x, y_pred, color='red', linewidth=2, label=f'拟合线: y={slope:.4f}x+{intercept:.4f}\n拟合优度: R²={self.r2:.4f}\n{stats_text}')
            ax.set_xlabel(x_col, fontsize=12)
            ax.set_ylabel(y_col, fontsize=12)
            ax.legend(fontsize=8)
            ax.set_title('线性回归拟合', fontweight='bold')
            ax.grid(True, alpha=0.3)

            # 残差图（可选）
            fit_img_path = ""
            if CONFIG["show_residual_plot"]:
                ax = axs[1]
                # ax.subplot(1, 2, 2)
                ax.scatter(y_pred, residuals, alpha=0.6)
                ax.axhline(y=0, color='red', linestyle='--')
                ax.set_xlabel('预测值', fontsize=12)
                ax.set_ylabel('残差', fontsize=12)
                ax.set_title('残差分布', fontweight='bold')
                ax.grid(True, alpha=0.3)
            
            # 自动创建目录
            fit_img_path = os.path.join(self.file_dir, f"{self.file_basename}_拟合图.{CONFIG['fig_format']}")
            if CONFIG["auto_create_dir"]:
                os.makedirs(os.path.dirname(fit_img_path), exist_ok=True)
            plt.savefig(fit_img_path, dpi=CONFIG["fig_dpi"], bbox_inches='tight')
            plt.close()
            return slope, intercept, residuals, fit_img_path
        except Exception as e:
            logger.error(f"❌ 绘制拟合图失败: {str(e)}")
            plt.close()
            return None, None, None, None

    def plot_data_trend(self, x_col: str, y_col: str) -> str:
        """绘制数据趋势图，返回生成的图片路径"""
        if not self._validate_columns(x_col, y_col):
            return ""
        try:
            logger.info(f"📊 正在绘制趋势图，有效数据量: {len(self.df)}行")
            plt.figure(figsize=(24, 16), constrained_layout=True)

            plt.plot(self.df[x_col], self.df[y_col], 
                    marker='o', linestyle='-', linewidth=1, markersize=3,
                    color='#2E86AB', label='实测值')

            # 统计信息注释
            # stats_text = (
            #     f"皮尔逊相关系数: r={self.pearson_r:.4f}, p={self.pearson_p:.4f}\n"
            #     f"斯皮尔曼相关系数: r={self.spearman_r:.4f}, p={self.spearman_p:.4f}\n"
            # )
            # if self.r2 is not None:
            #     stats_text += f"线性拟合R²: {self.r2:.4f}"
            # plt.annotate(stats_text, 
            #         xy=(0.02, 0.98), xycoords='axes fraction',
            #         bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", alpha=0.8),
            #         fontsize=10, ha='left', va='top')

            plt.title(f"{x_col}-{y_col} 趋势图", fontsize=14, fontweight='bold', pad=20)
            plt.xlabel(x_col, fontsize=12)
            plt.ylabel(y_col, fontsize=12)
            plt.grid(True, alpha=0.3, linestyle='--')
            plt.legend(fontsize=8)
            import matplotlib.ticker as ticker
            # 获取当前的坐标轴实例
            ax = plt.gca()
            # 设置横坐标的主刻度间隔为 10（可根据实际需求修改数字）
            ax.xaxis.set_major_locator(ticker.MultipleLocator(5)) 
            # 设置纵坐标的主刻度间隔为 10（可根据实际需求修改数字）
            ax.yaxis.set_major_locator(ticker.MultipleLocator(20000)) 
            # plt.tight_layout()

            trend_img_path = os.path.join(self.file_dir, f"{self.file_basename}_趋势图.{CONFIG['fig_format']}")
            if CONFIG["auto_create_dir"]:
                os.makedirs(os.path.dirname(trend_img_path), exist_ok=True)
            plt.savefig(trend_img_path, dpi=CONFIG["fig_dpi"], bbox_inches='tight')
            plt.close()
            return trend_img_path
        except Exception as e:
            logger.error(f"❌ 绘制趋势图失败: {str(e)}")
            plt.close()
            return ""

    def plot(self, x_col: str, y_col: str, one_actuator_info: Optional[dict] = None, is_linearity_test: Optional[bool] = True) -> dict:
        """对外主接口：绘制所有图表，返回所有生成的图片路径和统计结果，不会向上抛异常"""
        result = {
            "success": False,
            "trend_img": "",
            "fit_img": "",
            "pearson_r": self.pearson_r,
            "pearson_p": self.pearson_p,
            "slope": self.slope,
            "intercept": self.intercept,
            "r2": self.r2
        }
        try:
            if self.df is None:
                logger.error("❌ 数据未加载成功，无法绘图")
                return result

            if not is_linearity_test:
                result["trend_img"] = self.plot_data_trend(x_col, y_col)   # 绘制趋势图
            else:
                if not self.is_linear_relationship(x_col, y_col):          # 先做相关性分析
                    logger.warning("❌ 数据线性不相关，不再继续绘图")
                    return

                self.slope, self.intercept, _, result["fit_img"] = self.plot_linear_relationship(x_col, y_col)   # 绘制拟合图
                one_actuator_info["拟合图名称"] = f"{self.file_basename}_拟合图"
                print(f"{self.file_basename}_拟合图")
                one_actuator_info["拟合图"] = result["fit_img"]
                test_time = re.search(r"data_(.*?)_拟合图", one_actuator_info["拟合图名称"])
                if test_time:
                    one_actuator_info["测试时间"] = test_time.group(1)
                one_actuator_info["线性方程"] = f"y = {self.slope:.4f}x + {self.intercept:.4f}"
                one_actuator_info["线性度"] = f"{self.slope:.4f}"


            # 回写统计结果
            result.update({
                "success": True,
                "pearson_r": self.pearson_r,
                "pearson_p": self.pearson_p,
                "slope": self.slope,
                "intercept": self.intercept,
                "r2": self.r2
            })
            logger.info(f"✅ 绘图完成, 拟合图: {result['fit_img']}")
            kp[int(one_actuator_info["sensor_index"])] = self.slope
            logger.info(f"记录sensor_index = {one_actuator_info['sensor_index']}时, 矩阵kp = {kp}")
            return result, 
        except Exception as e:
            logger.error(f"❌ 绘图整体失败: {str(e)}")
            plt.close('all')
            return result


if __name__ == "__main__":
    # 测试示例
    analyzer = DataAnalyzer("data/4_data_20260517_165503.csv")
    res = analyzer.plot(y_col='Steps', x_col='Force_Value', one_actuator_info={"sensor_index": 0, "mirror_id": 1}, is_linearity_test=True)
    print("绘图结果：", res)
