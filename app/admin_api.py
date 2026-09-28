"""老师端管理接口（客户端）。

和 `class_sync.py` 的区别：那个是**同学用的**，拿接入密钥只能读；
这个是**老师用的**，拿管理员密钥能发内容、撤回、看接入名单。

设计要点：
  * 只走 HTTP 接口，**不碰服务端数据库**。这样管理页和 `send.py` 走的是同一条路，
    不会出现"命令行发得出去、界面上发不出去"这种两套逻辑不一致的问题。
  * 所有方法都不抛异常，统一返回 `AdminResult`。老师电脑连不上服务端是常事，
    界面不该因此崩掉或者弹一堆错误框。
  * 只依赖标准库（urllib）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime

from . import config as cfg

HTTP_TIMEOUT = 8          # 秒。和 class_sync 保持一致，局域网内够用


class AdminResult:
    """一次管理操作的结果。

    ok=False 时 error 一定有值，界面直接把它显示出来就行。
    """

    __slots__ = ("ok", "error", "data", "at")

    def __init__(self, ok: bool, error: str = "", data: dict | None = None):
        self.ok = ok
        self.error = error
        self.data = data or {}
        self.at = datetime.now().strftime("%H:%M:%S")

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        return f"<AdminResult ok={self.ok} error={self.error!r}>"


def _looks_set(text: str) -> bool:
    return bool((text or "").strip())


class AdminApi:
    """用管理员密钥跟服务端打交道。一个实例管一个服务器。"""

    def __init__(self, settings, server_url: str | None = None,
                 admin_key: str | None = None):
        self.settings = settings
        self._url_override = server_url
        self._key_override = admin_key

    # ------------------------------------------------------------------ 配置
    @property
    def server_url(self) -> str:
        if self._url_override is not None:
            return self._url_override.strip().rstrip("/")
        return (self.settings.get("class_server_url", "") or "").strip().rstrip("/")

    @property
    def admin_key(self) -> str:
        if self._key_override is not None:
            return self._key_override.strip()
        return (self.settings.get("class_admin_key", "") or "").strip()

    @property
    def configured(self) -> bool:
        """填了地址和管理员密钥才算配好了。"""
        return _looks_set(self.server_url) and _looks_set(self.admin_key)

    # ------------------------------------------------------------------ 网络
    def _call(self, path: str, payload: dict | None = None,
              method: str = "POST") -> AdminResult:
        if not self.configured:
            return AdminResult(False, "还没填服务器地址或管理员密钥")
        url = f"{self.server_url}{path}"
        headers = {"X-Memo-Key": self.admin_key,
                   "Content-Type": "application/json; charset=utf-8"}
        data = None
        if method == "POST":
            data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                code = resp.status
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 403/400 这类是服务端明确拒绝，把它的原话带给老师
            code = e.code
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:
                body = {}
        except Exception as e:
            # 连不上：老师电脑关机、防火墙没放行、地址填错
            cfg.log_problem("班级管理请求失败", e)
            return AdminResult(False, f"连不上服务器（{type(e).__name__}）")

        if code == 200 and body.get("ok"):
            return AdminResult(True, "", body)
        return AdminResult(False, str(body.get("error") or f"HTTP {code}"), body)

    # ------------------------------------------------------------------ 查询
    def test_connection(self) -> AdminResult:
        """只验证地址和密钥，不写任何数据。"""
        return self._call("/api/admin/list", method="GET")

    def fetch_all(self) -> AdminResult:
        """通告 + 任务 + 接入名单 + 统计，管理页一次刷完。"""
        return self._call("/api/admin/list", method="GET")

    # ------------------------------------------------------------------ 发送
    def publish_announcement(self, title: str, body: str = "",
                             author: str = "") -> AdminResult:
        title = (title or "").strip()
        if not title:
            return AdminResult(False, "通告标题不能为空")
        return self._call("/api/announcement", {
            "title": title, "body": body or "", "author": author or "老师"})

    def withdraw_announcement(self, ann_id: int) -> AdminResult:
        if not ann_id:
            return AdminResult(False, "没指定要撤回哪条通告")
        return self._call("/api/announcement/withdraw", {"id": int(ann_id)})

    def publish_task(self, title: str, note: str = "", times: list | None = None,
                     recur: str = "none", recur_params: dict | None = None,
                     once_date: str | None = None, category: str = "班级",
                     priority: str = "中") -> AdminResult:
        title = (title or "").strip()
        if not title:
            return AdminResult(False, "任务标题不能为空")
        if not times:
            return AdminResult(False, "至少要有一个提醒时间")
        return self._call("/api/task", {
            "title": title, "note": note or "", "times": list(times),
            "recur": recur or "none", "recur_params": recur_params or {},
            "once_date": once_date, "category": category or "班级",
            "priority": priority or "中"})

    def withdraw_task(self, task_id: int) -> AdminResult:
        if not task_id:
            return AdminResult(False, "没指定要撤回哪个任务")
        return self._call("/api/task/withdraw", {"id": int(task_id)})
