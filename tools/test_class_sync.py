"""端到端测试：真的起一个服务端，让客户端同步，验证整条链路。

覆盖：
  * 老师发通告 -> 同学收到并缓存
  * 重复同步不会重复（增量）
  * 老师撤回 -> 同学本地也删掉
  * 老师发班级任务 -> 进同学待办列表，标为 source='class'
  * 密钥不对 / 服务器关掉 -> 静默降级，不抛异常、不影响本地数据
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

OUT = Path(__file__).resolve().parent / "_e2e_report.txt"
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


def _fake_click():
    """一个假的鼠标左键事件，用来直接驱动卡片的展开/收起，不用真的点屏幕。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(QEvent.MouseButtonRelease, QPointF(5, 5), QPointF(5, 5),
                       Qt.LeftButton, Qt.NoButton, Qt.NoModifier)


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    from app import config as cfg
    from app.class_sync import ClassSync
    from app.database import Database

    tmp = Path(tempfile.mkdtemp(prefix="memo_e2e_"))

    # ---------- 起服务端 ----------
    store = srv.Store(tmp / "server" / "class.db")
    admin_key, join_key = srv.load_or_create_keys(store, "ADMIN-TEST", "MEMO-TEST")
    srv.Handler.api = srv.Api(store, admin_key, join_key)
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)
    url = f"http://127.0.0.1:{port}"
    say(f"服务端: {url}   管理员密钥={admin_key}  接入密钥={join_key}")
    say("")

    # ---------- 客户端 ----------
    db = Database(tmp / "client.db")
    st = _Settings(class_enabled=True, class_server_url=url,
                   class_join_key=join_key, class_student_name="张三",
                   class_device_id="dev-e2e-1")
    syncer = ClassSync(db, st)

    say("=== 1. 首次同步（服务端还没有内容）===")
    r = syncer.sync()
    check("同步成功", r.ok, r.summary())
    check("本地没有通告", syncer.announcements() == [])
    check("未读数为 0", syncer.unread_count() == 0)
    check("服务端记录了接入（张三）",
          any(m.get("name") == "张三" for m in store.members()),
          str([m.get("name") for m in store.members()]))

    # ---------- 老师发通告 ----------
    say("")
    say("=== 2. 老师发通告 -> 同学收到 ===")
    aid = store.add_announcement("明天交作业", "第 3 章习题，拍照上传")
    r = syncer.sync()
    check("同步报告收到公告", r.announcements == 1, r.summary())
    anns = syncer.announcements()
    check("本地缓存了通告", len(anns) == 1 and anns[0]["title"] == "明天交作业",
          str(anns)[:80])
    check("未读数为 1", syncer.unread_count() == 1)

    say("")
    say("=== 3. 增量：再同步不会重复 ===")
    r = syncer.sync()
    check("第二次同步没有新通告", r.announcements == 0, r.summary())
    check("本地仍然只有 1 条", len(syncer.announcements()) == 1)

    say("")
    say("=== 4. 标记已读 ===")
    syncer.mark_read(aid, True)
    check("已读后未读数为 0", syncer.unread_count() == 0)
    syncer.mark_read(aid, False)
    check("取消已读后未读数为 1", syncer.unread_count() == 1)

    # ---------- 老师发班级任务 ----------
    say("")
    say("=== 5. 老师发班级任务 -> 进同学待办 ===")
    # 注意：once_date 要用"未来的日期"，否则一次性任务的 next_at 会是空的
    # （那是对的行为：过期的一次性任务不该再提醒）。第一版这里踩过坑。
    store.add_task({"title": "交作业", "note": "拍照上传", "times": ["08:00"],
                    "category": "班级", "priority": "高",
                    "recur": "none", "once_date": "2026-12-31"})
    r = syncer.sync()
    check("同步报告收到任务", r.tasks == 1, r.summary())
    class_tasks = [t for t in db.all_tasks() if t.is_class_task]
    check("待办里有这条班级任务", len(class_tasks) == 1,
          str([(t.title, t.source) for t in class_tasks]))
    ct = class_tasks[0] if class_tasks else None
    check("标了来源为 class", ct is not None and ct.source == "class")
    check("记了服务端 id", ct is not None and ct.remote_id == 1)
    check("本地已有 next_at（能进提醒调度）", bool(ct and ct.next_at),
          str(ct.next_at if ct else None))

    say("")
    say("=== 6. 重复同步不会把任务变成两条 ===")
    r = syncer.sync()
    check("第二次没有新任务", r.tasks == 0, r.summary())
    check("班级任务仍然只有 1 条",
          len([t for t in db.all_tasks() if t.is_class_task]) == 1)

    say("")
    say("=== 7. 老师改了任务标题 -> 同学这边更新 ===")
    store.conn.execute("UPDATE class_tasks SET title='交作业（改动版）' WHERE id=1")
    store.conn.commit()
    # 改标题后要让客户端重新拿到：把进度退回 1 之前
    syncer._set_state("task_last_id", "0")
    syncer.sync()
    titles = [t.title for t in db.all_tasks() if t.is_class_task]
    check("标题已更新", titles == ["交作业（改动版）"], str(titles))

    # ---------- 撤回 ----------
    say("")
    say("=== 8. 老师撤回 -> 同学本地也删掉 ===")
    store.withdraw_announcement(aid)
    store.withdraw_task(1)
    r = syncer.sync()
    check("同步报告移除了内容", r.removed >= 1, r.summary())
    check("通告被删掉", syncer.announcements() == [],
          str(syncer.announcements()))
    check("班级任务被删掉",
          len([t for t in db.all_tasks() if t.is_class_task]) == 0)

    # ---------- 班级页界面（真构造控件）----------
    # 这一节是被真机验证逼出来的：AnnouncementCard 曾经在 __init__ 里先调
    # _refresh_body()、之后才创建 self.hint，结果只要有**一条带正文的通告**，
    # 同学一点开「班级」页就 AttributeError 崩掉。纯逻辑测试全绿也发现不了，
    # 因为没人真去构造那个卡片。
    say("")
    say("=== 9. 班级页界面：各种通告形状都不许崩 ===")
    from app.theme import ThemeManager
    from app.ui.class_view import AnnouncementCard, ClassView

    theme = ThemeManager(app, st)

    shapes = [
        ("普通通告", {"remote_id": 1, "title": "交作业", "body": "第 3 章习题",
                  "author": "老师", "created_at": "2026-09-28 10:00", "read_at": None}),
        ("没有正文", {"remote_id": 2, "title": "只看标题", "body": "",
                  "author": "老师", "created_at": "2026-09-28 10:00", "read_at": None}),
        ("正文是空的 None", {"remote_id": 3, "title": "空正文", "body": None,
                        "author": "老师", "created_at": "2026-09-28 10:00",
                        "read_at": None}),
        ("多行正文", {"remote_id": 4, "title": "多行", "body": "第一行\n第二行\n第三行",
                  "author": "老师", "created_at": "2026-09-28 10:00", "read_at": None}),
        ("超长单行正文", {"remote_id": 5, "title": "长", "body": "啊" * 200,
                    "author": "老师", "created_at": "2026-09-28 10:00", "read_at": None}),
        ("字段全缺", {"remote_id": 6}),
        ("已读过", {"remote_id": 7, "title": "读过的", "body": "内容",
                 "author": "老师", "created_at": "2026-09-28 10:00",
                 "read_at": "2026-09-28 11:00"}),
    ]

    for label, ann in shapes:
        try:
            card = AnnouncementCard(ann, theme)
            check(f"卡片可构造：{label}", True)
        except Exception as e:
            check(f"卡片可构造：{label}", False, f"{type(e).__name__}: {e}")
            continue

        # 点一下展开/收起，走的是 _refresh_body 的另一条分支
        try:
            card.mouseReleaseEvent(_fake_click())
            check(f"卡片可展开：{label}", card._expanded is True)
            card.mouseReleaseEvent(_fake_click())
            check(f"卡片可收起：{label}", card._expanded is False)
        except Exception as e:
            check(f"卡片可展开：{label}", False, f"{type(e).__name__}: {e}")

        # 没正文时不该提示"点击展开"
        if not (ann.get("body") or "").strip():
            check(f"空正文不显示展开提示：{label}",
                  card.hint.text() == "" and card.hint.isVisible() is False,
                  repr(card.hint.text()))
        card.setParent(None)
        card.deleteLater()

    # 整页刷新（这是同学点侧边栏「班级」时真正走的路径）
    view = ClassView(db, syncer, theme, st)
    store.add_announcement("界面测试通告", "第一行\n第二行")
    syncer.sync()
    try:
        view.refresh()
        check("整页刷新不崩（有通告）", True)
    except Exception as e:
        check("整页刷新不崩（有通告）", False, f"{type(e).__name__}: {e}")

    # 没有通告时走的是空状态分支
    view2 = ClassView(db, ClassSync(db, _Settings()), theme, st)
    try:
        view2.refresh()
        check("整页刷新不崩（没接入班级）", True)
    except Exception as e:
        check("整页刷新不崩（没接入班级）", False, f"{type(e).__name__}: {e}")

    view.setParent(None)
    view.deleteLater()
    view2.setParent(None)
    view2.deleteLater()

    # ---------- 密钥错误（必须在关服之前测，否则报的是"连不上"）----------
    say("")
    say("=== 10. 密钥错了 -> 明确报错 ===")
    st2 = _Settings(class_enabled=True, class_server_url=url,
                    class_join_key="WRONG", class_device_id="dev-e2e-2")
    syncer2 = ClassSync(db, st2)
    r2 = syncer2.test_connection()
    check("错误密钥被识别", r2.ok is False)
    check("错误信息指明密钥问题", "密钥" in str(r2.error), str(r2.error))

    # ---------- 降级 ----------
    say("")
    say("=== 11. 服务器关掉 -> 静默降级 ===")
    store.add_announcement("关服前发的", "这条应该已经在本地")
    syncer.sync()          # 先拿到
    before = len(syncer.announcements())
    httpd.shutdown()
    time.sleep(0.3)
    r = syncer.sync()
    check("同步失败但不抛异常", r.ok is False, r.summary())
    check("错误信息友好", "连不上" in r.summary() or "离线" in r.summary(),
          r.summary())
    check("离线时本地已有通告仍然在", len(syncer.announcements()) == before,
          f"{before} -> {len(syncer.announcements())}")

    db.close()
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
