"""把界面离屏渲染成 PNG，用来检查配色/圆角/排版是否正常。

用法：
    python tools/render_preview.py

生成到 E:\\Memo\\memo_app\\preview\\ ：
    light_list.png / dark_list.png / light_calendar.png / dark_calendar.png
    light_stats.png / dark_stats.png / popup.png / edit_dialog.png
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = Path(__file__).resolve().parent.parent / "preview"


def seed(db):
    """复用 tools/seed_data.py 里的示例数据，保证两个脚本看到的一样。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import seed_data
    seed_data.seed(db)


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    from app import config as cfg
    from app.database import Database
    from app.reminder import ReminderScheduler
    from app.theme import ThemeManager
    from app.ui.dialogs import TaskEditDialog
    from app.ui.main_window import MainWindow
    from app.ui.popup import Popup

    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="memo_preview_"))
    db = Database(tmp / "preview.db")
    seed(db)

    settings = cfg.Settings(tmp / "ui.json")
    theme = ThemeManager(app, settings)
    sched = ReminderScheduler(db, settings)
    win = MainWindow(db, sched, theme, settings)
    win.resize(1120, 720)
    win.show()
    app.processEvents()

    for mode in ("light", "dark"):
        theme.apply(mode)
        win.apply_theme(theme.c)
        app.processEvents()
        for page in ("list", "calendar", "stats"):
            win.switch_page(page)
            for _ in range(6):
                app.processEvents()
            path = OUT / f"{mode}_{page}.png"
            win.grab().save(str(path))
            print("已生成", path.name)

        # 提醒弹窗
        t = db.all_tasks()[1]
        pop = Popup(t, theme, catchup=True)
        pop.adjustSize()
        pop.show()
        for _ in range(8):
            app.processEvents()
        pop.grab().save(str(OUT / f"popup_{mode}.png"))
        print("已生成", f"popup_{mode}.png")
        pop.hide()

        # 编辑对话框（检查表单布局）
        dlg = TaskEditDialog(theme, t, db, None)
        dlg.resize(600, 700)
        dlg.show()
        for _ in range(8):
            app.processEvents()
        dlg.grab().save(str(OUT / f"edit_{mode}.png"))
        print("已生成", f"edit_{mode}.png")
        dlg.hide()

    db.close()
    print("\n全部生成到：", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
