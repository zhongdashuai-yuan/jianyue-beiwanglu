"""真机渲染：在真实显示器上开窗口、截图、退出。

用法：
    python tools/render_real.py

和 render_preview.py 的区别：这个不设置 QT_QPA_PLATFORM=offscreen，
所以用的是 Windows 真实字体（微软雅黑），能验证中文排版和 DPI 缩放。
窗口会短暂出现在屏幕上，截完图自动关闭。

生成：
    E:\\Memo\\memo_app\\preview\\real_*.png
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT = Path(__file__).resolve().parent.parent / "preview"


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    from app import config as cfg
    from app.database import Database
    from app.reminder import ReminderScheduler
    from app.theme import ThemeManager
    from app.ui.main_window import MainWindow
    from app.ui.popup import Popup

    # --demo：用隔离的 demo.db 并写入示例数据（不动你正式的数据）
    demo = "--demo" in sys.argv
    if demo:
        db_path = cfg.DATA_DIR / "demo.db"
        db_path.unlink(missing_ok=True)
        db = Database(db_path)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import seed_data
        seed_data.seed(db)
    else:
        db = Database(cfg.DB_PATH)
    print(f"数据库：{db.path}   任务数：{len(db.all_tasks())}")

    settings = cfg.Settings(cfg.SETTINGS_PATH)
    theme = ThemeManager(app, settings)
    theme.apply()
    sched = ReminderScheduler(db, settings)
    win = MainWindow(db, sched, theme, settings)
    win.resize(1180, 760)
    win.show()
    win.raise_()
    win.activateWindow()

    screens = QGuiApplication.screens()
    print("屏幕数：", len(screens),
          "| 主屏 DPI 缩放：", screens[0].devicePixelRatio() if screens else "?")

    shots = [("list", 0), ("calendar", 1), ("stats", 2)]
    state = {"i": 0}

    def step():
        i = state["i"]
        if i < len(shots):
            page, _ = shots[i]
            win.switch_page(page)
            app.processEvents()
            state["i"] += 1
            # 页面切换动画 240ms + 布局结算，等够时间再截，否则会截到布局中途的样子
            QTimer.singleShot(900, lambda p=page: capture(p))
        else:
            win.hide()
            # 弹窗单独截
            t = db.all_tasks()
            if t:
                pop = Popup(t[0], theme, catchup=True)
                pop.adjustSize()
                pop.show()
                app.processEvents()
                QTimer.singleShot(600, lambda: (capture_popup(pop)))
            else:
                QTimer.singleShot(100, finish)

    def capture(page: str):
        path = OUT / f"real_{page}.png"
        cv = win.calendar_view
        holder = cv.grid_scroll.widget()
        rep = [f"win={win.width()}x{win.height()}",
               f"stack={win.stack.width()}x{win.stack.height()}",
               f"viewport={cv.grid_scroll.viewport().width()}x{cv.grid_scroll.viewport().height()}",
               f"holder={holder.width()}x{holder.height()} (hint {holder.sizeHint().height()})",
               f"vbar={'Y' if cv.grid_scroll.verticalScrollBar().isVisible() else 'N'}"
               f"/max={cv.grid_scroll.verticalScrollBar().maximum()}"]
        # 每一行日期格子的 y 坐标 + 高度，用来确认没有行被切掉
        rows = []
        for r in range(0, 7):
            it = cv.grid.itemAtPosition(r, 0)
            if it and it.widget():
                g = it.widget().geometry()
                rows.append(f"r{r}@y{g.y()}+{g.height()}")
        print(f"[{page}] " + "  ".join(rep + rows))
        win.grab().save(str(path))
        QTimer.singleShot(150, step)

    def capture_popup(pop):
        path = OUT / "real_popup.png"
        pop.grab().save(str(path))
        print("已生成", path.name, path.stat().st_size, "字节")
        pop.hide()
        QTimer.singleShot(150, finish)

    def finish():
        db.close()
        print("完成")
        app.quit()

    QTimer.singleShot(900, step)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
