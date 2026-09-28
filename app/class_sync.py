"""班级通告同步（客户端）。

职责：把老师服务端上的通告和班级任务拉到本地，并保证：
  * **服务器连不上时静默降级**：不弹错误、不影响本来的提醒功能，
    只是同步状态显示"离线"，已收到的内容照常可看
  * **通告是本地缓存**：服务端才是权威，撤回后本地也删掉
  * **老师发的任务标出来源**（source='class'），同学不能删（防止误删作业）
  * **增量同步**：只拉自上次以来的新内容，不重复下载

只依赖标准库（urllib）+ 项目已有的 sqlite 封装。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime

from . import config as cfg
from .database import Database
from .models import Task, now_str
from .recurrence import compute_next_at

# 同步间隔（毫秒）。课前发通告的场景，3 分钟足够，也不会给老师电脑压力。
SYNC_INTERVAL_MS = 3 * 60 * 1000
HTTP_TIMEOUT = 8          # 秒。局域网内足够，连不上也不会卡住界面


class SyncResult:
    """一次同步的结果，给界面显示用。"""

    __slots__ = ("ok", "error", "announcements", "tasks", "removed", "at")

    def __init__(self, ok: bool, error: str = "", announcements: int = 0,
                 tasks: int = 0, removed: int = 0):
        self.ok = ok
        self.error = error
        self.announcements = announcements
        self.tasks = tasks
        self.removed = removed
        self.at = datetime.now().strftime("%H:%M:%S")

    def summary(self) -> str:
        if not self.ok:
            return f"离线（{self.error}）"
        if not (self.announcements or self.tasks or self.removed):
            return f"已同步（{self.at}），没有新内容"
        bits = []
        if self.announcements:
            bits.append(f"{self.announcements} 条通告")
        if self.tasks:
            bits.append(f"{self.tasks} 个班级任务")
        if self.removed:
            bits.append(f"移除 {self.removed} 条已撤回内容")
        return f"已同步（{self.at}）：收到 " + "、".join(bits)


class ClassSync:
    """一个实例管一个服务器。界面通过这些方法取状态、触发同步。"""

    def __init__(self, db: Database, settings):
        self.db = db
        self.settings = settings
        self.last: SyncResult | None = None
        self._lock = threading.Lock()
        self._timer = None
        self._on_done = None

    # ------------------------------------------------------------------ 配置
    @property
    def server_url(self) -> str:
        return (self.settings.get("class_server_url", "") or "").strip().rstrip("/")

    @property
    def join_key(self) -> str:
        return (self.settings.get("class_join_key", "") or "").strip()

    @property
    def device_id(self) -> str:
        """本机标识（首次使用时生成，用于老师在服务端看到"谁接入了"）。"""
        did = self.settings.get("class_device_id", "")
        if not did:
            import uuid
            did = "dev-" + uuid.uuid4().hex[:12]
            self.settings.set("class_device_id", did)
        return did

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("class_enabled", False)
                    and self.server_url and self.join_key)

    def configure(self, url: str, key: str, name: str = "", enabled: bool = True) -> None:
        self.settings.update(class_server_url=(url or "").strip().rstrip("/"),
                             class_join_key=(key or "").strip(),
                             class_student_name=(name or "").strip(),
                             class_enabled=enabled)
        self._ensure_device_id()

    def _ensure_device_id(self) -> None:
        _ = self.device_id          # 触发生成

    # ------------------------------------------------------------------ 网络
    def _post(self, path: str, payload: dict, key: str) -> tuple[int, dict]:
        url = f"{self.server_url}{path}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        req.add_header("X-Memo-Key", key)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8"))
            except Exception:
                return e.code, {}
        # URLError（连不上）交给调用方处理

    def test_connection(self) -> SyncResult:
        """只测连通性和密钥，不写任何数据（设置界面用）。"""
        if not self.server_url or not self.join_key:
            return SyncResult(False, "还没填服务器地址或接入密钥")
        try:
            code, d = self._post("/api/sync", {
                "since_announcement_id": 0, "since_task_id": 0,
                "device_id": self.device_id,
                "name": self.settings.get("class_student_name", ""),
                "client_version": cfg.APP_VERSION, "probe": True}, self.join_key)
        except Exception as e:
            return SyncResult(False, f"连不上服务器（{type(e).__name__}）")
        if code == 200 and d.get("ok"):
            return SyncResult(True)
        return SyncResult(False, str(d.get("error") or f"HTTP {code}"))

    # ------------------------------------------------------------------ 同步
    def sync(self) -> SyncResult:
        """拉一次。出错不抛异常，返回 SyncResult 交给界面显示。"""
        if not self.enabled:
            self.last = SyncResult(False, "未启用班级同步")
            return self.last
        with self._lock:
            try:
                res = self._do_sync()
            except Exception as e:
                res = SyncResult(False, f"{type(e).__name__}: {e}")
            self.last = res
            return res

    def _do_sync(self) -> SyncResult:
        since_a = int(self._state("announcement_last_id", "0") or 0)
        since_t = int(self._state("task_last_id", "0") or 0)

        code, d = self._post("/api/sync", {
            "since_announcement_id": since_a,
            "since_task_id": since_t,
            "device_id": self.device_id,
            "name": self.settings.get("class_student_name", ""),
            "client_version": cfg.APP_VERSION,
        }, self.join_key)

        if code != 200 or not d.get("ok"):
            return SyncResult(False, str(d.get("error") or f"HTTP {code}"))

        n_ann = self._apply_announcements(d.get("announcements") or [])
        n_task = self._apply_tasks(d.get("tasks") or [])
        removed = self._apply_withdrawn(d.get("withdrawn_announcements") or [],
                                        d.get("withdrawn_tasks") or [])

        # 记录进度（取服务端返回的最大 id）
        for item in d.get("announcements") or []:
            self._set_state("announcement_last_id", str(item["id"]))
        for item in d.get("tasks") or []:
            self._set_state("task_last_id", str(item["id"]))
        # 撤回的也要推进进度，否则每次都会重复下发撤回列表
        for i in d.get("withdrawn_announcements") or []:
            self._set_state("announcement_last_id",
                            str(max(int(self._state("announcement_last_id", "0") or 0), i)))
        for i in d.get("withdrawn_tasks") or []:
            self._set_state("task_last_id",
                            str(max(int(self._state("task_last_id", "0") or 0), i)))

        return SyncResult(True, announcements=n_ann, tasks=n_task, removed=removed)

    # ------------------------------------------------------------------ 落库
    def _apply_announcements(self, items: list[dict]) -> int:
        n = 0
        for it in items:
            rid = int(it.get("id") or 0)
            if not rid:
                continue
            with self.db.tx() as c:
                c.execute(
                    "INSERT OR REPLACE INTO class_announcements"
                    "(remote_id,title,body,author,created_at,read_at)"
                    " VALUES(?,?,?,?,?,"
                    "  COALESCE((SELECT read_at FROM class_announcements"
                    "            WHERE remote_id=?), NULL))",
                    (rid, it.get("title") or "", it.get("body") or "",
                     it.get("author") or "老师", it.get("created_at") or "", rid))
            n += 1
        return n

    def _apply_tasks(self, items: list[dict]) -> int:
        """把老师的任务导入本地待办。

        用 remote_id 去重：同一条任务重复同步不会变成两条。
        已存在的只更新标题/备注（老师改了内容同学能看到），
        **不动同学的完成状态**（那是本地的事）。
        """
        n = 0
        for it in items:
            rid = int(it.get("id") or 0)
            if not rid:
                continue
            exists = self.db.conn.execute(
                "SELECT id FROM tasks WHERE source='class' AND remote_id=?",
                (rid,)).fetchone()
            if exists:
                with self.db.tx() as c:
                    c.execute("UPDATE tasks SET title=?, note=?, category=?,"
                              " priority=? WHERE id=?",
                              (it.get("title") or "", it.get("note") or "",
                               it.get("category") or "班级",
                               it.get("priority") or "中", exists["id"]))
                continue

            t = Task(title=it.get("title") or "(老师发的任务)",
                     note=it.get("note") or "",
                     category=it.get("category") or "班级",
                     priority=it.get("priority") or "中",
                     times=list(it.get("times") or ["08:00"]),
                     recur=it.get("recur") or cfg.RECUR_NONE,
                     recur_params=it.get("recur_params") or {},
                     once_date=it.get("once_date"),
                     enabled=True, done=False,
                     created_at=now_str(), source="class", remote_id=rid)
            t.next_at = compute_next_at(t)
            self.db.add_task(t)
            n += 1
        return n

    def _apply_withdrawn(self, ann_ids: list[int], task_ids: list[int]) -> int:
        n = 0
        for rid in ann_ids:
            with self.db.tx() as c:
                n += c.execute("DELETE FROM class_announcements WHERE remote_id=?",
                               (int(rid),)).rowcount
        for rid in task_ids:
            # 老师撤回任务：本地也删掉（连同它的完成记录）
            row = self.db.conn.execute(
                "SELECT id FROM tasks WHERE source='class' AND remote_id=?",
                (int(rid),)).fetchone()
            if row:
                with self.db.tx() as c:
                    c.execute("DELETE FROM completion_log WHERE task_id=?", (row["id"],))
                self.db.delete_task(row["id"])
                n += 1
        return n

    # ------------------------------------------------------------------ 状态
    def _state(self, key: str, default: str = "") -> str:
        r = self.db.conn.execute("SELECT value FROM sync_state WHERE key=?",
                                 (key,)).fetchone()
        return r["value"] if r else default

    def _set_state(self, key: str, value: str) -> None:
        with self.db.tx() as c:
            c.execute("INSERT OR REPLACE INTO sync_state(key,value) VALUES(?,?)",
                      (key, value))

    # ---- 通告的读取（界面用）----
    def announcements(self, unread_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM class_announcements"
        if unread_only:
            sql += " WHERE read_at IS NULL"
        sql += " ORDER BY created_at DESC, remote_id DESC"
        return [dict(r) for r in self.db.conn.execute(sql)]

    def unread_count(self) -> int:
        r = self.db.conn.execute(
            "SELECT COUNT(*) c FROM class_announcements WHERE read_at IS NULL"
        ).fetchone()
        return r["c"] if r else 0

    def mark_read(self, remote_id: int, read: bool = True) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE class_announcements SET read_at=? WHERE remote_id=?",
                      (now_str() if read else None, int(remote_id)))

    # ------------------------------------------------------------------ 定时
    def start_auto(self, on_done=None) -> None:
        """启动定时同步。用 QTimer 是为了跟界面同一个线程，避免线程问题。"""
        from PySide6.QtCore import QTimer
        self._on_done = on_done
        if self._timer is None:
            self._timer = QTimer()
            self._timer.setInterval(SYNC_INTERVAL_MS)
            self._timer.timeout.connect(self._auto_tick)
        if self.enabled:
            self._timer.start()
            # 启动后稍等一下再同步，别和界面初始化抢时间
            QTimer.singleShot(2500, self._auto_tick)

    def stop_auto(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    def _auto_tick(self) -> None:
        res = self.sync()
        if self._on_done:
            try:
                self._on_done(res)
            except Exception as e:
                cfg.log_problem("班级同步回调失败", e)
