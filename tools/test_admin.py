"""老师端管理页自测：真起服务端 -> 真用 AdminApi / AdminView 操作。

覆盖三件事：
  * AdminApi 拿管理员密钥能发/撤回/拉列表，拿接入密钥会被拒
  * AdminView 真的构造出来、真的能通过界面发通告和任务（发完服务端要收到）
  * 连不上服务端时只显示一行提示，不崩、不弹框

这里刻意**真构造 Qt 控件**而不是只测逻辑：班级页当初就是栽在"纯逻辑测试全绿、
但一构造控件就 AttributeError"上（AnnouncementCard 的 hint 写反了顺序）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import server as srv                                    # noqa: E402

OUT = Path(__file__).resolve().parent / "_admin_test.txt"
L: list[str] = []
PASS, FAIL = [], []


def say(m: str = "") -> None:
    L.append(str(m))
    try:
        print(m)
    except UnicodeEncodeError:
        pass


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    say(("  [通过] " if cond else "  [失败] ") + name + (f"   {extra}" if extra else ""))


class _Settings:
    """够用的假 settings（和 test_class_sync 里那个一样）。"""

    def __init__(self, **kw):
        self.d = dict(kw)

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v

    def update(self, **kw):
        self.d.update(kw)

    def as_dict(self):
        return dict(self.d)

    def save(self):
        pass


def main() -> int:
    from PySide6.QtWidgets import QApplication, QLabel
    app = QApplication.instance() or QApplication(sys.argv)

    from app import config as cfg
    from app.admin_api import AdminApi
    from app.theme import ThemeManager
    from app.ui.admin_view import TASK_RECUR_CHOICES, AdminView

    def _pick_recur(view, key):
        view.cmb_recur.setCurrentIndex(
            [k for k, _ in TASK_RECUR_CHOICES].index(key))
        view._on_recur_changed()

    tmp = Path(tempfile.mkdtemp(prefix="memo_admin_"))

    # ---------- 起服务端 ----------
    store = srv.Store(tmp / "server" / "class.db")
    admin_key, join_key = srv.load_or_create_keys(store, "ADMIN-ADM", "MEMO-ADM")
    srv.Handler.api = srv.Api(store, admin_key, join_key)
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)
    url = f"http://127.0.0.1:{port}"
    say(f"服务端: {url}   管理员密钥={admin_key}")
    say("")

    st = _Settings(class_server_url=url, class_admin_key=admin_key,
                   class_join_key=join_key, theme="light")
    theme = ThemeManager(app, st)

    # ---------- 1. AdminApi 基本操作 ----------
    say("=== 1. AdminApi：管理员密钥能干活 ===")
    api = AdminApi(st)
    check("configured 认定配置好了", api.configured is True)
    res = api.test_connection()
    check("测试连接成功", res.ok, res.error)

    res = api.publish_announcement("明天交作业", "第 3 章习题")
    check("能发通告", res.ok and res.data.get("id"), res.error)
    ann_id = res.data.get("id")

    res = api.publish_task("交读书笔记", note="写 500 字",
                           times=["19:00"], recur=cfg.RECUR_NONE,
                           once_date="2026-12-31")
    check("能发任务", res.ok and res.data.get("id"), res.error)
    task_id = res.data.get("id")

    res = api.fetch_all()
    check("能拉全量列表", res.ok, res.error)
    check("列表里有刚发的通告",
          any(a["title"] == "明天交作业" for a in res.data["announcements"]))
    check("列表里有刚发的任务",
          any(t["title"] == "交读书笔记" for t in res.data["tasks"]))
    check("列表里有统计", isinstance(res.data.get("stats"), dict))

    res = api.withdraw_announcement(ann_id)
    check("能撤回通告", res.ok, res.error)
    res = api.withdraw_task(task_id)
    check("能撤回任务", res.ok, res.error)

    # ---------- 2. 空输入 / 错密钥 ----------
    say("")
    say("=== 2. 空输入和错误密钥都要给明确提示 ===")
    check("空标题不发出请求", api.publish_announcement("   ").ok is False)
    check("空标题的原因说得清",
          "标题" in api.publish_announcement("   ").error)
    check("任务没时间点被拦", api.publish_task("x", times=[]).ok is False)
    check("撤回不带 id 被拦", api.withdraw_announcement(0).ok is False)

    bad = _Settings(class_server_url=url, class_admin_key="WRONG-KEY")
    res = AdminApi(bad).fetch_all()
    check("错的管理员密钥连不上", res.ok is False)
    check("错误信息指明密钥问题", "密钥" in res.error, res.error)

    # 用接入密钥冒充管理员：必须被拒（同学拿到管理页也不能发东西）
    fake = _Settings(class_server_url=url, class_admin_key=join_key)
    res = AdminApi(fake).publish_announcement("学生偷发", "x")
    check("拿接入密钥发通告被拒", res.ok is False, res.error)
    check("被拒原因指明权限", "密钥" in res.error, res.error)

    res = AdminApi(_Settings()).fetch_all()
    check("啥都没填时不崩、有提示", res.ok is False and "还没填" in res.error,
          res.error)

    # ---------- 3. AdminView 构造 ----------
    say("")
    say("=== 3. 管理页构造 ===")
    view = AdminView(st, theme)
    check("AdminView 能构造", view is not None)
    check("三个标签页都在", view.tabs.count() == 3, str(view.tabs.count()))
    check("四个输入框都在",
          all(x is not None for x in (view.edit_ann_title, view.edit_ann_body,
                                      view.edit_task_title, view.edit_task_note)))

    # ---------- 4. 日期/时间框宽度 ----------
    say("")
    say("=== 4. 日期/时间框要装得下自己的文字 ===")
    # 这条是真机截图逼出来的：全局 QSS 的默认宽度按英文排，
    # "2026-09-28" 在界面上被截成 "2026-09-"，时间被截成 "19:0"。
    #
    # 断言要量**实际渲染出来能放文字的区域**，不能只跟 sizeHint() 比：
    # QSS 用的是内容盒模型（theme.py 里 `padding: 6px 10px`），
    # sizeHint() 不含这段内边距，拿它当基准会算出"够宽"的假结论 ——
    # 我就这么放过一次，结果截图里还是截断的。
    from PySide6.QtGui import QFontMetrics
    view.resize(1000, 680)
    view.show()
    app.processEvents()
    for name, w in (("日期", view.date_task), ("时间", view.time_task)):
        fm = QFontMetrics(w.font())
        need = fm.horizontalAdvance(w.text())
        # 尾部还有下拉箭头(日期)/上下箭头(时间)，它们也占宽度
        arrow = 36 if name == "日期" else 24
        avail = w.width() - arrow
        check(f"{name}框装得下 {w.text()!r}", avail >= need,
              f"控件宽 {w.width()}，扣掉箭头和内边距后可用 {avail}，"
              f"文字宽 {need}")

    say("")
    say("=== 5. 界面上点「发给全班」服务端要真收到 ===")
    view.edit_ann_title.setText("界面发的通告")
    view.edit_ann_body.setPlainText("这条是从管理页点出去的")
    view._send_announcement()
    check("发送后有成功提示", "已发给全班" in view.lbl_ann_msg.text(),
          view.lbl_ann_msg.text())
    check("服务端真的收到了",
          any(a["title"] == "界面发的通告"
              for a in store.announcements(0)))
    check("发完清空了输入框",
          view.edit_ann_title.text() == "" and view.edit_ann_body.toPlainText() == "")

    # 空标题：界面要给出提示，而不是静默失败
    view.edit_ann_title.setText("")
    view._send_announcement()
    check("空标题在界面上有提示", "失败" in view.lbl_ann_msg.text(),
          view.lbl_ann_msg.text())

    # ---------- 5. 通过界面发任务（含重复规则翻译）----------
    say("")
    say("=== 5. 界面发任务：重复规则要翻译对 ===")
    view.edit_task_title.setText("每周五交周报")
    view.edit_task_note.setText("写在群里")
    _pick_recur(view, cfg.RECUR_WEEKLY)
    check("选「每周」时星期选择出现", view.week_box.isVisibleTo(view) is True)
    for i, c in enumerate(view.chk_days):
        c.setChecked(i == 4)                       # 只勾周五
    view._send_task()
    check("发任务有成功提示", "已发布" in view.lbl_task_msg.text(),
          view.lbl_task_msg.text())

    sent = [t for t in store.tasks(0) if t["title"] == "每周五交周报"]
    check("服务端收到了这个任务", len(sent) == 1, str(len(sent)))
    if sent:
        check("重复方式传对了", sent[0]["recur"] == "weekly", sent[0]["recur"])
        check("星期几传对了", sent[0]["recur_params"].get("weekdays") == [5],
              str(sent[0]["recur_params"]))
        check("重复任务不带 once_date", not sent[0].get("once_date"),
              str(sent[0].get("once_date")))

    # 每周但一天都没勾 -> 必须拦下来，不能发一个永远不会提醒的任务
    view.edit_task_title.setText("没勾星期的任务")
    for c in view.chk_days:
        c.setChecked(False)
    view._send_task()
    check("每周没勾星期会被拦", "至少" in view.lbl_task_msg.text() or
          "勾" in view.lbl_task_msg.text(), view.lbl_task_msg.text())
    check("被拦的任务没发到服务端",
          not any(t["title"] == "没勾星期的任务" for t in store.tasks(0)))

    # 一次性任务要带 once_date
    _pick_recur(view, cfg.RECUR_NONE)
    check("选「只提醒一次」时星期选择收起", view.week_box.isVisibleTo(view) is False)
    view.edit_task_title.setText("一次性任务")
    view._send_task()
    once = [t for t in store.tasks(0) if t["title"] == "一次性任务"]
    check("一次性任务带上了日期", bool(once) and bool(once[0].get("once_date")),
          str(once[0].get("once_date")) if once else "没发出去")

    # ---------- 6. 列表与撤回 ----------
    say("")
    say("=== 7. 已发布列表与撤回 ===")
    view.refresh()
    check("状态栏显示连上了", "已连接" in view.lbl_status.text(),
          view.lbl_status.text())
    check("通告列表有内容", view.ann_box.count() > 0, str(view.ann_box.count()))
    check("任务列表有内容", view.task_box.count() > 0, str(view.task_box.count()))

    def _row_labels(box) -> list[str]:
        """把列表容器里每一行的文字抠出来，用来断言界面上真的显示了什么。"""
        out = []
        for i in range(box.count()):
            w = box.itemAt(i).widget()
            if w is None:
                continue
            out.extend(lb.text() for lb in w.findChildren(QLabel))
        return out

    target = [t for t in store.announcements(0) if t["title"] == "界面发的通告"][0]
    view._withdraw_ann(target["id"])
    # 注意：announcements() 默认不返回已撤回的，得显式 include_withdrawn=True，
    # 否则这里会查不到、误判成"撤回没生效"（第一版就这么写的，白失败一次）。
    gone = [a for a in store.announcements(0, include_withdrawn=True)
            if a["id"] == target["id"]]
    check("撤回后服务端标记为已撤回",
          bool(gone) and bool(gone[0].get("withdrawn")), str(gone))

    # 撤回后管理页仍要列出来并标明"已撤回"，否则老师不知道撤成功没有
    view.refresh()
    labels = _row_labels(view.ann_box)
    check("撤回后仍列在管理页", any("界面发的通告" in t for t in labels),
          str(labels)[:160])
    check("列表上标了「已撤回」", any("已撤回" in t for t in labels),
          str(labels)[:160])

    # ---------- 7. 成员名单 ----------
    say("")
    say("=== 8. 接入名单 ===")
    store.touch_member("dev-adm-1", "李四", "1.0")
    view.refresh()
    check("名单列表有内容", view.member_box.count() > 0, str(view.member_box.count()))

    # ---------- 8. 服务端关掉 ----------
    say("")
    say("=== 9. 服务端关掉 -> 只提示、不崩 ===")
    httpd.shutdown()
    time.sleep(0.3)
    try:
        view.refresh()
        check("刷新不抛异常", True)
    except Exception as e:
        check("刷新不抛异常", False, f"{type(e).__name__}: {e}")
    check("状态栏提示连不上", "连不上" in view.lbl_status.text(),
          view.lbl_status.text())
    try:
        view.edit_ann_title.setText("关服后发的")
        view._send_announcement()
        check("关服后发送不抛异常", True)
        check("关服后给出失败提示", "失败" in view.lbl_ann_msg.text(),
              view.lbl_ann_msg.text())
    except Exception as e:
        check("关服后发送不抛异常", False, f"{type(e).__name__}: {e}")

    # 没配置管理的机器上打开这页，也要有可读提示
    blank = AdminView(_Settings(), theme)
    blank.refresh()
    check("没配密钥时提示去设置", "设置" in blank.lbl_status.text(),
          blank.lbl_status.text())

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    say("")
    say("=" * 60)
    say(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        say("失败项：")
        for f in FAIL:
            say("  - " + f)
    say("=" * 60)
    OUT.write_text("\n".join(L), encoding="utf-8")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
