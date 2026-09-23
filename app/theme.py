"""主题：把 config 里的配色字典渲染成 Qt 样式表(QSS)。

★ 想改颜色改 app/config.py；想改控件的圆角/间距改这里。
所有控件都用 objectName 或 class 选择器挂样式，界面代码里只 setObjectName。
"""

from __future__ import annotations

from . import config as cfg


def gradient_qss(c: dict, widget: str = "QWidget#RootBackground") -> str:
    """主窗口的斜向渐变背景。Qt 的 qlineargradient 支持斜角，正好能用。"""
    return f"""
{widget} {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {c['bg_grad_a']}, stop:1 {c['bg_grad_b']});
}}
"""


def build_qss(c: dict) -> str:
    r_card = cfg.RADIUS_CARD
    r_btn = cfg.RADIUS_BUTTON
    r_in = cfg.RADIUS_INPUT
    r_sm = cfg.RADIUS_SMALL

    return f"""
/* ============================ 全局 ============================ */
* {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif;
    outline: none;
}}
QWidget {{
    color: {c['text']};
    font-size: 13px;
}}
QToolTip {{
    background: {c['card']};
    color: {c['text']};
    border: 1px solid {c['border_strong']};
    border-radius: {r_sm}px;
    padding: 6px 10px;
}}

/* 主窗口根节点：青->天蓝 斜向渐变 */
QWidget#RootBackground {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {c['bg_grad_a']}, stop:1 {c['bg_grad_b']});
}}

/* ============================ 卡片 ============================ */
/* 柔和边框的关键：1px 浅色边框 + 大圆角 + 极淡阴影 */
QFrame#Card {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: {r_card}px;
}}
QFrame#Card:hover {{
    background: {c['card_hover']};
    border: 1px solid {c['border_strong']};
}}
QFrame#CardDone {{
    background: {c['card_done']};
    border: 1px solid {c['border']};
    border-radius: {r_card}px;
}}
QFrame#Panel {{
    background: {c['sidebar']};
    border: 1px solid {c['border']};
    border-radius: {r_card}px;
}}
QFrame#Divider {{
    background: {c['divider']};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

/* 左侧边栏 */
QWidget#Sidebar {{
    background: {c['sidebar']};
    border-right: 1px solid {c['border']};
}}

/* ============================ 文字 ============================ */
QLabel#H1 {{ font-size: 22px; font-weight: 600; color: {c['text']}; }}
QLabel#H2 {{ font-size: 16px; font-weight: 600; color: {c['text']}; }}
QLabel#Sub {{ font-size: 12px; color: {c['text_sub']}; }}
QLabel#Muted {{ font-size: 12px; color: {c['text_mute']}; }}
QLabel#CardTitle {{ font-size: 14px; font-weight: 500; color: {c['text']}; }}
QLabel#CardTitleDone {{
    font-size: 14px; color: {c['text_mute']};
    text-decoration: line-through;
}}
QLabel#BigNumber {{ font-size: 30px; font-weight: 600; color: {c['accent']}; }}
QLabel#GroupHeader {{ font-size: 13px; font-weight: 600; color: {c['text_sub']}; }}
QLabel#HolidayText {{ color: {c['holiday']}; }}
QLabel#EmptyIcon {{ font-size: 40px; }}
QLabel#EmptyText {{ font-size: 14px; color: {c['text_sub']}; }}

/* ============================ 按钮 ============================ */
QPushButton {{
    background: {c['card']};
    color: {c['text']};
    border: 1px solid {c['border_strong']};
    border-radius: {r_btn}px;
    padding: 7px 14px;
    font-size: 13px;
}}
QPushButton:hover {{
    background: {c['accent_soft']};
    border: 1px solid {c['accent']};
    color: {c['accent_press']};
}}
QPushButton:pressed {{
    background: {c['accent']};
    color: #FFFFFF;
    border: 1px solid {c['accent']};
}}
QPushButton:disabled {{
    color: {c['text_mute']};
    border: 1px solid {c['border']};
    background: {c['card_done']};
}}

/* 主按钮：青色渐变 */
QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {c['accent']}, stop:1 {c['accent2']});
    color: #FFFFFF;
    border: none;
    border-radius: {r_btn}px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {c['accent_press']}, stop:1 {c['accent']});
}}
QPushButton#Primary:pressed {{ padding-top: 9px; padding-bottom: 7px; }}

/* 幽灵按钮（次要动作） */
QPushButton#Ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {c['text_sub']};
    padding: 6px 10px;
    border-radius: {r_sm}px;
}}
QPushButton#Ghost:hover {{
    background: {c['accent_soft']};
    color: {c['accent_press']};
}}

/* 危险按钮（删除） */
QPushButton#Danger {{
    background: transparent;
    border: 1px solid {c['danger']};
    color: {c['danger']};
    border-radius: {r_btn}px;
    padding: 7px 14px;
}}
QPushButton#Danger:hover {{ background: {c['danger']}; color: #FFFFFF; }}

/* 侧边栏导航项 */
QPushButton#NavItem {{
    background: transparent;
    border: none;
    border-radius: {r_btn}px;
    padding: 10px 14px;
    text-align: left;
    font-size: 14px;
    color: {c['text_sub']};
}}
QPushButton#NavItem:hover {{ background: {c['accent_soft']}; color: {c['accent_press']}; }}
QPushButton#NavItem:checked {{
    background: {c['accent_soft']};
    color: {c['accent_press']};
    font-weight: 600;
    border-left: 3px solid {c['accent']};
    padding-left: 11px;
}}

/* 标签筛选小胶囊 */
QPushButton#Chip {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 12px;
    padding: 3px 10px;
    font-size: 12px;
    color: {c['text_sub']};
}}
QPushButton#Chip:checked {{
    background: {c['accent_soft']};
    border: 1px solid {c['accent']};
    color: {c['accent_press']};
    font-weight: 600;
}}

/* ============================ 输入控件 ============================ */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QTimeEdit, QDateEdit, QComboBox {{
    background: {c['input_bg']};
    border: 1px solid {c['input_border']};
    border-radius: {r_in}px;
    padding: 6px 10px;
    selection-background-color: {c['accent']};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QTimeEdit:focus, QDateEdit:focus, QComboBox:focus {{
    border: 1px solid {c['input_focus']};
}}
QLineEdit#SearchBox {{
    border-radius: 16px;
    padding: 7px 14px;
    background: {c['card']};
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {c['card']};
    border: 1px solid {c['border_strong']};
    border-radius: {r_sm}px;
    padding: 4px;
    selection-background-color: {c['accent_soft']};
    selection-color: {c['accent_press']};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button,
QTimeEdit::up-button, QTimeEdit::down-button {{ width: 14px; border: none; }}

/* 勾选框：自己画成圆形，更像"完成打卡" */
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 20px; height: 20px;
    border-radius: 10px;
    border: 2px solid {c['border_strong']};
    background: transparent;
}}
QCheckBox::indicator:hover {{ border: 2px solid {c['accent']}; }}
QCheckBox::indicator:checked {{
    background: {c['accent']};
    border: 2px solid {c['accent']};
}}
QCheckBox#PrioRadio::indicator {{ border-radius: 8px; width: 16px; height: 16px; }}

QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{
    width: 16px; height: 16px; border-radius: 8px;
    border: 2px solid {c['border_strong']}; background: transparent;
}}
QRadioButton::indicator:checked {{ background: {c['accent']}; border: 2px solid {c['accent']}; }}

/* ============================ 列表 ============================ */
QListWidget, QListView, QTreeWidget {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget::item {{ border-radius: {r_sm}px; padding: 2px; }}
QListWidget::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
QListWidget::item:hover {{ background: {c['card_hover']}; }}

/* ============================ 滚动条：细、圆、柔和 ============================ */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: {c['scroll_bg']};
    width: 9px;
    margin: 2px 2px 2px 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {c['scroll_handle']};
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['scroll_handle_hover']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{
    background: {c['scroll_bg']}; height: 9px; margin: 0 2px 2px 2px; border: none;
}}
QScrollBar::handle:horizontal {{
    background: {c['scroll_handle']}; border-radius: 4px; min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{ background: {c['scroll_handle_hover']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; border: none; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* ============================ 日历 ============================ */
QWidget#CalendarGrid {{ background: transparent; }}
QPushButton#CalDay {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    font-size: 13px;
    color: {c['text']};
    text-align: center;
    padding: 0;
}}
QPushButton#CalDay:hover {{ border: 1px solid {c['accent']}; background: {c['accent_soft']}; }}
QPushButton#CalDayOther {{ color: {c['grid_other_month']}; background: transparent; border: 1px solid transparent; }}
QPushButton#CalDayToday {{
    border: 2px solid {c['accent']};
    font-weight: 600;
    color: {c['accent_press']};
    background: {c['accent_soft']};
}}
QPushButton#CalDaySelected {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {c['accent']}, stop:1 {c['accent2']});
    color: #FFFFFF;
    border: none;
    font-weight: 600;
}}
QPushButton#CalNav {{
    background: transparent; border: none; font-size: 16px;
    color: {c['text_sub']}; padding: 4px 10px; border-radius: {r_sm}px;
}}
QPushButton#CalNav:hover {{ background: {c['accent_soft']}; color: {c['accent_press']}; }}

/* ============================ 提醒弹窗 ============================ */
QFrame#Popup {{
    background: {c['card']};
    border: 1px solid {c['border_strong']};
    border-radius: {r_card}px;
}}
QLabel#PopupTitle {{ font-size: 15px; font-weight: 600; color: {c['text']}; }}
QLabel#PopupTime {{ font-size: 13px; font-weight: 600; color: {c['accent']}; }}

/* ============================ 菜单 ============================ */
QMenu {{
    background: {c['card']};
    border: 1px solid {c['border_strong']};
    border-radius: {r_in}px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 22px 7px 14px;
    border-radius: {r_sm}px;
    color: {c['text']};
}}
QMenu::item:selected {{ background: {c['accent_soft']}; color: {c['accent_press']}; }}
QMenu::separator {{ height: 1px; background: {c['divider']}; margin: 5px 8px; }}

/* ============================ 对话框 ============================ */
QDialog {{ background: {c['bg_grad_a']}; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: {r_in}px; background: {c['card']}; }}
QTabBar::tab {{
    background: transparent; color: {c['text_sub']};
    padding: 8px 16px; border-radius: {r_sm}px; margin-right: 4px;
}}
QTabBar::tab:selected {{ background: {c['accent_soft']}; color: {c['accent_press']}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {c['accent_press']}; }}

/* ============================ 其他 ============================ */
QProgressBar {{
    background: {c['today_ring_bg']};
    border: none; border-radius: 4px; height: 8px; text-align: center;
}}
QProgressBar::chunk {{
    border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {c['accent']}, stop:1 {c['accent2']});
}}
QSlider::groove:horizontal {{ height: 4px; background: {c['today_ring_bg']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {c['accent']}; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px;
}}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {c['accent']}, stop:1 {c['accent2']});
    border-radius: 2px;
}}
QGroupBox {{
    border: 1px solid {c['border']}; border-radius: {r_in}px;
    margin-top: 12px; padding-top: 10px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {c['accent_press']}; }}
QStatusBar {{ background: transparent; color: {c['text_sub']}; }}
QHeaderView::section {{
    background: {c['accent_soft']}; color: {c['text_sub']};
    border: none; padding: 6px; border-radius: 0;
}}
"""


class ThemeManager:
    """管当前主题 + 通知所有窗口刷新样式。"""

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings
        self.mode = "light"          # 当前实际生效的
        self._listeners: list = []

    def resolve(self, mode: str | None = None) -> str:
        mode = mode or self.settings.get("theme", "auto")
        if mode == "auto":
            return "dark" if _system_is_dark() else "light"
        return mode if mode in cfg.THEMES else "light"

    def colors(self, mode: str | None = None) -> dict:
        return cfg.THEMES[self.resolve(mode)]

    def apply(self, mode: str | None = None) -> str:
        self.mode = self.resolve(mode)
        c = cfg.THEMES[self.mode]
        self.app.setStyleSheet(build_qss(c))
        for cb in self._listeners:
            try:
                cb(c)
            except Exception as e:
                # 一个回调失败不该让整个换肤中断，但必须留下记录，
                # 否则界面某块没跟着换色、用户只会觉得"怪怪的"却查不到原因。
                cfg.log_problem("theme.apply 的回调", e,
                                f"mode={self.mode} 回调={getattr(cb, '__qualname__', cb)}")
        return self.mode

    def on_change(self, callback) -> None:
        self._listeners.append(callback)

    @property
    def c(self) -> dict:
        """当前配色，界面里直接 theme.c['accent'] 用。"""
        return cfg.THEMES[self.mode]


def _system_is_dark() -> bool:
    """读注册表判断 Windows 是不是深色模式。读不到就当浅色。"""
    try:
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        return v == 0
    except Exception:
        return False


def make_icon(size: int = 64, c: dict | None = None):
    """用代码画一个青色渐变圆角图标（不用准备素材文件）。"""
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QIcon, QPixmap, QPainter, QLinearGradient, QColor, QPen, QBrush

    c = c or cfg.LIGHT
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0.0, QColor(c["accent"]))
    g.setColorAt(1.0, QColor(c["accent2"]))
    p.setBrush(QBrush(g))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size * 0.26, size * 0.26)

    # 一个"打勾"符号
    pen = QPen(QColor("#FFFFFF"))
    pen.setWidthF(size * 0.085)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    from PySide6.QtCore import QPointF
    p.drawPolyline([
        QPointF(size * 0.28, size * 0.52),
        QPointF(size * 0.44, size * 0.68),
        QPointF(size * 0.73, size * 0.34),
    ])
    p.end()
    return QIcon(pm)


def save_ico(path, sizes=(16, 24, 32, 48, 64, 128, 256)) -> bool:
    """把代码画的图标存成一个真正的多尺寸 .ico（窗口/托盘/PyInstaller 都能用）。

    ICO 格式其实就是一个目录 + 若干张 PNG：Windows Vista 以后支持 PNG 压缩的图标，
    所以不需要额外的图像库。
    """
    import struct
    from pathlib import Path
    from PySide6.QtCore import QBuffer, QByteArray, QSize

    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # 没有 QApplication 时 QPixmap 无法创建（比如命令行里直接调）
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:
            return False

        images: list[tuple[int, bytes]] = []
        for s in sizes:
            pm = make_icon(s, cfg.LIGHT).pixmap(QSize(s, s))
            # 注意：QByteArray 和 QBuffer 都必须是长期存在的 Python 变量，
            # 否则会被 GC 掉导致崩溃（C++ 侧还在引用）。
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QBuffer.WriteOnly)
            pm.save(buf, "PNG")
            buf.close()
            images.append((s, bytes(ba)))

        count = len(images)
        header = struct.pack("<HHH", 0, 1, count)      # reserved, type=icon, count
        offset = 6 + count * 16
        entries = b""
        payload = b""
        for s, data in images:
            entries += struct.pack("<BBBBHHII",
                                   s if s < 256 else 0,     # 256 用 0 表示
                                   s if s < 256 else 0,
                                   0, 0, 1, 32, len(data), offset)
            offset += len(data)
            payload += data
        path.write_bytes(header + entries + payload)
        return True
    except Exception:
        return False
