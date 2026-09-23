"""全局配置：路径、配色、默认设置。

★ 想换界面颜色，只需要改本文件的 LIGHT / DARK 两个字典，其它文件不用动。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ----------------------------------------------------------------------------
# 一、路径
# ----------------------------------------------------------------------------

APP_NAME = "备忘录提醒"
APP_VERSION = "1.0.0"
ORG_NAME = "MemoLocal"

# 数据目录：打包成 exe 之后也不会变，所以升级版本不会丢数据。
# （之前确认过：数据放 E:\Memo\data\）
# 支持用环境变量覆盖，方便测试脚本用临时目录、不碰正式数据。
if sys.platform == "win32":
    DATA_DIR = Path(os.environ.get("MEMO_DATA_DIR") or r"E:\Memo\data")
else:  # 万一以后在别的系统上跑，给个退路
    DATA_DIR = Path(os.environ.get("MEMO_DATA_DIR") or (Path.home() / ".memo_app"))

DB_PATH = Path(os.environ.get("MEMO_DB_PATH") or (DATA_DIR / "memo.db"))
BACKUP_DIR = DATA_DIR / "backup"
LOG_PATH = DATA_DIR / "memo.log"
SETTINGS_PATH = Path(os.environ.get("MEMO_SETTINGS_PATH") or (DATA_DIR / "ui_settings.json"))

# 资源目录：兼容 PyInstaller 打包后的临时解包目录
def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


ASSETS_DIR = resource_path("assets")
ICON_PATH = ASSETS_DIR / "app_icon.ico"

# ----------------------------------------------------------------------------
# 二、配色（青色 / 天蓝）
# ----------------------------------------------------------------------------
# 命名规则：bg 背景 / card 卡片 / border 边框 / text 文字 / accent 主色
LIGHT: dict[str, str] = {
    "name": "浅色",
    # 渐变背景：从左上(#F4FAFD) 到右下(#E8F6FB)
    "bg_grad_a": "#F4FAFD",
    "bg_grad_b": "#E5F4FB",
    "sidebar": "rgba(255, 255, 255, 0.62)",
    "card": "#FFFFFF",
    "card_hover": "#F7FCFE",
    "card_done": "#F2F8FA",
    "border": "#E3F0F7",          # 柔和边框：只比背景深一点点
    "border_strong": "#CDE7F1",
    "text": "#1F3B45",            # 深青灰，不用纯黑
    "text_sub": "#7E9AA6",
    "text_mute": "#A9BFC8",
    "accent": "#12B5C9",          # 主青色
    "accent2": "#4FC3E8",         # 亮天蓝
    "accent_soft": "#E4F6FA",     # 主色的淡底（选中背景）
    "accent_press": "#0E9AAB",
    "p_high": "#FF7A85",          # 高优先级：柔红
    "p_mid": "#FFB65C",           # 中优先级：暖橙
    "p_low": "#7ED0A8",           # 低优先级：薄荷绿
    "shadow": "rgba(56, 178, 214, 0.18)",
    "danger": "#FF7A85",
    "scroll_bg": "transparent",
    "scroll_handle": "#CDE7F1",
    "scroll_handle_hover": "#A9DCEB",
    "input_bg": "#FFFFFF",
    "input_border": "#D8EBF3",
    "input_focus": "#12B5C9",
    "divider": "#EAF5FA",
    "today_ring_bg": "#E4F1F6",
    "grid_other_month": "#C3D6DE",
    "holiday": "#FF7A85",
    "weekend": "#7E9AA6",
    "tray_badge": "#FF7A85",
}

DARK: dict[str, str] = {
    "name": "深色",
    "bg_grad_a": "#0F2129",
    "bg_grad_b": "#16323D",
    "sidebar": "rgba(23, 50, 60, 0.72)",
    "card": "#17323C",
    "card_hover": "#1C3B47",
    "card_done": "#152B34",
    "border": "#224450",
    "border_strong": "#2C5566",
    "text": "#E6F4F8",
    "text_sub": "#8FB2BE",
    "text_mute": "#6B8B96",
    "accent": "#22C7DB",
    "accent2": "#5AD2F0",
    "accent_soft": "#1B3E4A",
    "accent_press": "#19A8BA",
    "p_high": "#FF8F98",
    "p_mid": "#FFC078",
    "p_low": "#8CDCB6",
    "shadow": "rgba(0, 0, 0, 0.45)",
    "danger": "#FF8F98",
    "scroll_bg": "transparent",
    "scroll_handle": "#2C5566",
    "scroll_handle_hover": "#3B6B80",
    "input_bg": "#122A33",
    "input_border": "#2C5566",
    "input_focus": "#22C7DB",
    "divider": "#1E3D48",
    "today_ring_bg": "#1E3D48",
    "grid_other_month": "#4A6771",
    "holiday": "#FF8F98",
    "weekend": "#8FB2BE",
    "tray_badge": "#FF8F98",
}

THEMES = {"light": LIGHT, "dark": DARK}

# 统一圆角与动效时长（"柔和边框 + 流畅交互"的数值都集中在这里）
RADIUS_CARD = 14
RADIUS_BUTTON = 10
RADIUS_INPUT = 10
RADIUS_SMALL = 8
ANIM_FAST = 160      # 毫秒：按钮/卡片 hover
ANIM_NORMAL = 240    # 毫秒：页面切换
ANIM_SLOW = 320      # 毫秒：弹窗滑入滑出
CARD_SHADOW_BLUR = 18
CARD_SHADOW_OFFSET = 2

# ----------------------------------------------------------------------------
# 三、业务默认值
# ----------------------------------------------------------------------------

PRIORITIES = ["高", "中", "低"]
PRIORITY_COLOR_KEY = {"高": "p_high", "中": "p_mid", "低": "p_low"}

DEFAULT_CATEGORIES = [
    {"name": "工作", "color": "#12B5C9"},
    {"name": "生活", "color": "#4FC3E8"},
    {"name": "学习", "color": "#7ED0A8"},
    {"name": "健康", "color": "#FFB65C"},
]

# 重复规则类型（值 = 数据库里存的字符串，改动要同步 recurrence.py）
RECUR_NONE = "none"            # 一次性
RECUR_DAILY = "daily"          # 每天
RECUR_WEEKLY = "weekly"        # 每周几（可多选）
RECUR_MONTHLY = "monthly"      # 每月几号（可多选）
RECUR_WORKDAY = "workday"      # 工作日（自动跳过周末/法定假，调休上班日照常）
RECUR_BIWEEK = "biweek"        # 每隔一周的周几（单双周）
RECUR_MONTH_END = "month_end"  # 每月最后一天
RECUR_MONTH_END_N = "month_end_n"   # 每月倒数第 N 天
RECUR_MONTH_NTH = "month_nth"  # 每月第几个周几
RECUR_QUARTER = "quarter"      # 每季度（季度首日 / 末日 / 第 N 天）

RECUR_LABELS = {
    RECUR_NONE: "只提醒一次",
    RECUR_DAILY: "每天",
    RECUR_WEEKLY: "每周",
    RECUR_MONTHLY: "每月几号",
    RECUR_WORKDAY: "每个工作日",
    RECUR_BIWEEK: "每隔一周的周几（单双周）",
    RECUR_MONTH_END: "每月最后一天",
    RECUR_MONTH_END_N: "每月倒数第 N 天",
    RECUR_MONTH_NTH: "每月第几个周几",
    RECUR_QUARTER: "每季度",
}

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
WEEKDAY_SHORT = ["一", "二", "三", "四", "五", "六", "日"]
# ISO 周几（1=周一 ... 7=周日）对应的中文
WEEKDAY_CN = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}

# 一天可以设多个提醒时间，第 1 个是"主时间"，其余的用提前提醒实现
DEFAULT_LEAD_MINUTES = 0
LEAD_CHOICES = [0, 5, 10, 15, 30, 60]

SNOOZE_CHOICES = [5, 10, 30]     # 稍后提醒档位（分钟）

# 提醒过了多久还没确认就算"错过"（用于第二天启动时的补提醒）
MISSED_GRACE_MINUTES = 10

# 刚开机/重启时，错过超过这个时长的提醒不再弹窗（避免开机被积压的过期提醒刷屏），
# 只在界面里显示为「未完成」。
#
# 取 1 小时的理由：这个阈值要同时满足两边 ——
#   * 关机一晚（十几小时）→ 远大于 1 小时，静默，不刷屏 ✓
#   * 程序崩溃重开、临时关机十几分钟 → 小于 1 小时，仍然补提醒 ✓（不然刚错过的就没了）
# 注意：这条只作用于"启动后的第一次扫描"；电脑一直开着时到点照常弹，
# 休眠几分钟后唤醒也照常补提醒。
STARTUP_SILENT_MINUTES = 60

# 节假日躲开的默认策略
# 注意：这个上限必须大于「最长的连续假期」。
# 2025/2026 的国庆+中秋连休是 8 天，春节连休是 9 天，
# 所以取 14 天，保证假期里的任务一定能顺延到节后第一个工作日。
DEFER_MAX_DAYS = 14       # 最多往后顺延几天，超过就不再顺延（防止无限跳）
HOLIDAY_POLICY_SKIP = "skip"      # 遇到节假日就顺延到下一个可用日
HOLIDAY_POLICY_IGNORE = "ignore"  # 照常提醒

# ----------------------------------------------------------------------------
# 四、界面设置（存在 ui_settings.json，用户改的偏好）
# ----------------------------------------------------------------------------

DEFAULT_UI_SETTINGS: dict = {
    "theme": "auto",              # auto / light / dark
    "autostart": True,            # 开机自启（用户已确认要）
    "minimize_to_tray": True,     # 关窗口时最小化到托盘而不是退出
    "sound_enabled": True,        # 提示音
    "popup_enabled": True,        # 右下角自定义弹窗
    "system_notify_enabled": True,  # Windows 原生通知
    "popup_auto_close_sec": 20,   # 弹窗自动淡出秒数（0=不自动关）
    "popup_max_stack": 3,         # 同屏最多叠几张提醒卡片
    "snooze_minutes": 10,         # 上次用的稍后档位
    "default_lead_minutes": 0,    # 默认提前提醒
    "default_priority": "中",
    "default_category": "工作",
    "hide_completed_in_list": False,
    "defer_on_holiday": True,     # 节假日顺延总开关
    "default_snooze_cap_minutes": 120,  # 单次提醒点"拒绝"最多累计顺延多久
    "week_start_monday": True,    # 日历从周一还是周日开始
    "window_geometry": None,      # 上次窗口大小位置（bytes base64）
    "last_page": "list",
    "tag_filter": [],             # 上次选中的标签
    "ref_biweek_date": "2026-01-05",  # 单双周基准（2026-01-05 是 ISO 第 2 周）
}

AUTOSTART_REG_NAME = "MemoReminder"          # HKCU\...\Run 里的键名
AUTOSTART_ARGS = "--autostart"               # 开机静默启动参数


# ----------------------------------------------------------------------------
# 五、读写用户设置
# ----------------------------------------------------------------------------

def log_problem(where: str, exc: BaseException | None = None,
                detail: str = "") -> None:
    """记录一处"被吞掉但值得知道"的问题，追加到 memo.log。

    为什么需要它：程序里有些地方出错后要继续运行（比如某个界面回调失败，
    不该让整个主题切换跟着崩），以前是 `except Exception: pass` —— 结果
    一旦功能悄悄失效，用户只会觉得"这功能没反应"，日志里也什么都查不到。
    现在统一往 memo.log 里留一条记录，方便排查。

    这个函数自己绝不抛异常（日志写不进去也不能影响主流程）。
    """
    try:
        import traceback
        from datetime import datetime
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        lines = [f"===== {datetime.now():%Y-%m-%d %H:%M:%S} [非致命] {where} ====="]
        if detail:
            lines.append(detail)
        if exc is not None:
            lines.append("".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__)))
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


class Settings:
    """简单的 JSON 键值设置。改了就存盘，程序里到处都用 settings.get(...)。"""

    def __init__(self, path: Path = SETTINGS_PATH):
        self.path = path
        self._data = dict(DEFAULT_UI_SETTINGS)
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    # 只认识默认表里有的键，避免旧版本脏数据搞崩程序
                    for k in DEFAULT_UI_SETTINGS:
                        if k in raw:
                            self._data[k] = raw[k]
        except Exception:
            pass  # 设置文件坏了就用默认值，不打扰用户

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def get(self, key: str, default=None):
        return self._data.get(key, DEFAULT_UI_SETTINGS.get(key, default))

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.save()

    def update(self, **kw) -> None:
        self._data.update(kw)
        self.save()

    def as_dict(self) -> dict:
        return dict(self._data)
