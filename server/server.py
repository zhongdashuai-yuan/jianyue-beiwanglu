"""班级通告服务端（只依赖 Python 标准库）。

用途：老师电脑上跑这个，同班同学的程序定时来同步通告和班级任务。
设计原则：
  * **只用标准库**（http.server + sqlite3），老师电脑不需要额外装任何包
  * **两个密钥分开**：管理员密钥能发/撤回；接入密钥只能读。防止同学冒充老师
  * **不是公网服务**：默认只监听局域网（0.0.0.0，方便同网段同学连），
    没有做 HTTPS，所以只在校园网/家里这种可信内网用

启动：
    python server.py                 # 默认 0.0.0.0:8765
    python server.py --port 9000     # 换端口
    python server.py --admin-key 我的口令 --join-key 给同学的码

首次启动会自动生成两个密钥并打印出来，同时写进服务端的数据目录。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

APP_NAME = "备忘录提醒 · 班级通告服务"
VERSION = "1.0.0"
DEFAULT_PORT = 8765
DATA_DIR = Path(__file__).resolve().parent / "server_data"
DB_PATH = DATA_DIR / "class.db"

# 同一个客户端最短多久能同步一次（秒）。防止有人写脚本狂刷。
MIN_SYNC_INTERVAL = 3
# 单个 IP 每分钟最多请求数
RATE_LIMIT_PER_MIN = 120

SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    body        TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    author      TEXT DEFAULT '老师',
    withdrawn   INTEGER DEFAULT 0     -- 撤回后置 1，客户端同步时就不再显示
);

CREATE TABLE IF NOT EXISTS class_tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    note        TEXT DEFAULT '',
    category    TEXT DEFAULT '班级',
    priority    TEXT DEFAULT '中',
    times       TEXT DEFAULT '["08:00"]',   -- JSON 数组，HH:MM
    recur       TEXT DEFAULT 'none',
    recur_params TEXT DEFAULT '{}',
    once_date   TEXT,
    created_at  TEXT NOT NULL,
    withdrawn   INTEGER DEFAULT 0
);

-- 谁接入了（档位1只需要看到名单，不收集完成情况）
CREATE TABLE IF NOT EXISTS members (
    device_id   TEXT PRIMARY KEY,
    name        TEXT DEFAULT '',
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    last_ver    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 数据层
# ---------------------------------------------------------------------------

class Store:
    """服务端数据库。所有写操作都要先通过密钥校验（见 Api）。"""

    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- 密钥 ----
    def get_meta(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                              (key, value))
            self.conn.commit()

    # ---- 通告 ----
    def add_announcement(self, title: str, body: str, author: str = "老师") -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO announcements(title,body,created_at,author) VALUES(?,?,?,?)",
                (title, body, _now(), author))
            self.conn.commit()
            return cur.lastrowid

    def withdraw_announcement(self, aid: int) -> bool:
        with self.lock:
            cur = self.conn.execute(
                "UPDATE announcements SET withdrawn=1 WHERE id=?", (aid,))
            self.conn.commit()
            return cur.rowcount > 0

    def announcements(self, since_id: int = 0, include_withdrawn: bool = False) -> list[dict]:
        sql = "SELECT * FROM announcements WHERE id > ?"
        if not include_withdrawn:
            sql += " AND withdrawn=0"
        sql += " ORDER BY id"
        return [dict(r) for r in self.conn.execute(sql, (since_id,))]

    # ---- 班级任务 ----
    def add_task(self, data: dict) -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO class_tasks(title,note,category,priority,times,recur,"
                "recur_params,once_date,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (data.get("title", ""), data.get("note", ""),
                 data.get("category", "班级"), data.get("priority", "中"),
                 json.dumps(data.get("times") or ["08:00"], ensure_ascii=False),
                 data.get("recur", "none"),
                 json.dumps(data.get("recur_params") or {}, ensure_ascii=False),
                 data.get("once_date"), _now()))
            self.conn.commit()
            return cur.lastrowid

    def withdraw_task(self, tid: int) -> bool:
        with self.lock:
            cur = self.conn.execute(
                "UPDATE class_tasks SET withdrawn=1 WHERE id=?", (tid,))
            self.conn.commit()
            return cur.rowcount > 0

    def tasks(self, since_id: int = 0, include_withdrawn: bool = False) -> list[dict]:
        sql = "SELECT * FROM class_tasks WHERE id > ?"
        if not include_withdrawn:
            sql += " AND withdrawn=0"
        sql += " ORDER BY id"
        out = []
        for r in self.conn.execute(sql, (since_id,)):
            d = dict(r)
            d["times"] = json.loads(d.get("times") or "[]")
            d["recur_params"] = json.loads(d.get("recur_params") or "{}")
            out.append(d)
        return out

    # ---- 成员 ----
    def touch_member(self, device_id: str, name: str, ver: str) -> None:
        with self.lock:
            row = self.conn.execute("SELECT device_id FROM members WHERE device_id=?",
                                    (device_id,)).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE members SET last_seen=?, name=COALESCE(NULLIF(?,''), name),"
                    " last_ver=? WHERE device_id=?",
                    (_now(), name, ver, device_id))
            else:
                self.conn.execute(
                    "INSERT INTO members(device_id,name,first_seen,last_seen,last_ver)"
                    " VALUES(?,?,?,?,?)", (device_id, name, _now(), _now(), ver))
            self.conn.commit()

    def members(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM members ORDER BY last_seen DESC")]

    # ---- 统计 ----
    def stats(self) -> dict:
        g = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "announcements": g("SELECT COUNT(*) FROM announcements WHERE withdrawn=0"),
            "tasks": g("SELECT COUNT(*) FROM class_tasks WHERE withdrawn=0"),
            "members": g("SELECT COUNT(*) FROM members"),
        }


# ---------------------------------------------------------------------------
# HTTP 接口
# ---------------------------------------------------------------------------

class Api:
    """把请求分发到具体处理函数。刻意保持简单，方便老师自己看懂。"""

    def __init__(self, store: Store, admin_key: str, join_key: str):
        self.store = store
        self.admin_key = admin_key
        self.join_key = join_key
        self._hits: dict[str, list[float]] = {}

    # ---- 限流 ----
    def allow(self, ip: str) -> bool:
        now = time.time()
        bucket = [t for t in self._hits.get(ip, []) if now - t < 60]
        if len(bucket) >= RATE_LIMIT_PER_MIN:
            self._hits[ip] = bucket
            return False
        bucket.append(now)
        self._hits[ip] = bucket
        return True

    # ---- 鉴权 ----
    def check(self, headers: dict, need_admin: bool) -> tuple[bool, str]:
        key = headers.get("x-memo-key", "")
        if not key:
            return False, "缺少密钥"
        if need_admin:
            if key != self.admin_key:
                return False, "管理员密钥不对"
        else:
            if key not in (self.join_key, self.admin_key):
                return False, "接入密钥不对"
        return True, ""

    # ---- 处理 ----
    def handle(self, method: str, path: str, headers: dict, body: dict,
               ip: str) -> tuple[int, dict]:
        if not self.allow(ip):
            return 429, {"ok": False, "error": "请求太频繁，请稍后再试"}

        if path == "/api/ping":
            return 200, {"ok": True, "app": APP_NAME, "version": VERSION,
                         "time": _now()}

        if path == "/api/sync":
            ok, err = self.check(headers, need_admin=False)
            if not ok:
                return 401, {"ok": False, "error": err}
            since_a = int(body.get("since_announcement_id") or 0)
            since_t = int(body.get("since_task_id") or 0)
            device = str(body.get("device_id") or "").strip()
            if device:
                self.store.touch_member(device, str(body.get("name") or ""),
                                        str(body.get("client_version") or ""))
            return 200, {
                "ok": True,
                "server_time": _now(),
                "announcements": self.store.announcements(since_a),
                "tasks": self.store.tasks(since_t),
                # 撤回列表：客户端据此把本地对应条目删掉
                "withdrawn_announcements": [r["id"] for r in
                                            self.store.announcements(0, True)
                                            if r["withdrawn"]],
                "withdrawn_tasks": [r["id"] for r in self.store.tasks(0, True)
                                    if r["withdrawn"]],
            }

        # 只读列表：管理员密钥能看到已撤回的，接入密钥只能看到还在的。
        # 单列这个接口是为了让"查一下服务端上有什么"这种需求不用走 /api/sync
        # （sync 会顺带登记设备，只查内容不该留下访问记录）。
        if path == "/api/announcements":
            ok, err = self.check(headers, need_admin=False)
            if not ok:
                return 401, {"ok": False, "error": err}
            is_admin = headers.get("x-memo-key", "") == self.admin_key
            items = self.store.announcements(0, include_withdrawn=is_admin)
            if not is_admin:
                items = [a for a in items if not a.get("withdrawn")]
            return 200, {"ok": True, "announcements": items,
                         "stats": self.store.stats()}

        if path == "/api/tasks":
            ok, err = self.check(headers, need_admin=False)
            if not ok:
                return 401, {"ok": False, "error": err}
            is_admin = headers.get("x-memo-key", "") == self.admin_key
            return 200, {"ok": True,
                         "tasks": self.store.tasks(0, include_withdrawn=is_admin)}

        # 管理页用：一次把通告、任务、名单、统计都拿回来。
        # 管理页要做"撤回"，所以这里必须能看见已经撤回的条目（灰掉显示）。
        if path == "/api/admin/list":
            ok, err = self.check(headers, need_admin=True)
            if not ok:
                return 403, {"ok": False, "error": err}
            return 200, {"ok": True,
                         "announcements": self.store.announcements(0, True),
                         "tasks": self.store.tasks(0, True),
                         "members": self.store.members(),
                         "stats": self.store.stats()}

        # 以下都需要管理员密钥
        if path == "/api/announcement" and method == "POST":
            ok, err = self.check(headers, need_admin=True)
            if not ok:
                return 403, {"ok": False, "error": err}
            title = str(body.get("title") or "").strip()
            if not title:
                return 400, {"ok": False, "error": "通告标题不能为空"}
            aid = self.store.add_announcement(title, str(body.get("body") or ""),
                                              str(body.get("author") or "老师"))
            return 200, {"ok": True, "id": aid}

        if path == "/api/announcement/withdraw" and method == "POST":
            ok, err = self.check(headers, need_admin=True)
            if not ok:
                return 403, {"ok": False, "error": err}
            return 200, {"ok": self.store.withdraw_announcement(
                int(body.get("id") or 0))}

        if path == "/api/task" and method == "POST":
            ok, err = self.check(headers, need_admin=True)
            if not ok:
                return 403, {"ok": False, "error": err}
            if not str(body.get("title") or "").strip():
                return 400, {"ok": False, "error": "任务标题不能为空"}
            return 200, {"ok": True, "id": self.store.add_task(body)}

        if path == "/api/task/withdraw" and method == "POST":
            ok, err = self.check(headers, need_admin=True)
            if not ok:
                return 403, {"ok": False, "error": err}
            return 200, {"ok": self.store.withdraw_task(int(body.get("id") or 0))}

        if path == "/api/members":
            ok, err = self.check(headers, need_admin=True)
            if not ok:
                return 403, {"ok": False, "error": err}
            return 200, {"ok": True, "members": self.store.members(),
                         "stats": self.store.stats()}

        return 404, {"ok": False, "error": f"没有这个接口: {path}"}


class Handler(BaseHTTPRequestHandler):
    api: Api = None          # 由 main() 注入
    server_version = "MemoClassServer/" + VERSION

    def log_message(self, fmt, *args):     # 精简日志，只留时间+路径
        sys.stderr.write(f"[{_now()}] {self.address_string()} {fmt % args}\n")

    def _read_body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            raw = self.rfile.read(n)
            return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return {}

    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _dispatch(self, method: str):
        headers = {k.lower(): v for k, v in self.headers.items()}
        body = self._read_body() if method == "POST" else {}
        path = self.path.split("?")[0]
        try:
            code, payload = self.api.handle(method, path, headers, body,
                                            self.client_address[0])
        except Exception as e:
            code, payload = 500, {"ok": False, "error": f"服务器内部错误: {e}"}
        self._send(code, payload)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def load_or_create_keys(store: Store, admin_key: str | None,
                        join_key: str | None) -> tuple[str, str]:
    import secrets
    ak = admin_key or store.get_meta("admin_key")
    jk = join_key or store.get_meta("join_key")
    if not ak:
        ak = "ADMIN-" + secrets.token_hex(4).upper()
        store.set_meta("admin_key", ak)
    if not jk:
        jk = "MEMO-" + secrets.token_hex(3).upper()
        store.set_meta("join_key", jk)
    if admin_key:
        store.set_meta("admin_key", admin_key)
    if join_key:
        store.set_meta("join_key", join_key)
    return ak, jk


def local_ips() -> list[str]:
    """列出本机在局域网里的地址，方便老师告诉同学填哪个。"""
    import socket
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # 不会真的发包，只为拿到出口 IP
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127.") and ":" not in ip:
                ips.append(ip)
    except Exception:
        pass
    return ips or ["127.0.0.1"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="0.0.0.0",
                    help="监听地址；0.0.0.0 表示同网段都能连")
    ap.add_argument("--admin-key", default=None, help="管理员密钥（不填则自动生成）")
    ap.add_argument("--join-key", default=None, help="发给同学的接入密钥")
    ap.add_argument("--data-dir", default=None, help="数据目录（默认脚本同级 server_data）")
    args = ap.parse_args(argv)

    global DATA_DIR, DB_PATH
    if args.data_dir:
        DATA_DIR = Path(args.data_dir)
        DB_PATH = DATA_DIR / "class.db"

    store = Store(DB_PATH)
    admin_key, join_key = load_or_create_keys(store, args.admin_key, args.join_key)

    print("=" * 66)
    print(f"  {APP_NAME}  v{VERSION}")
    print("=" * 66)
    print(f"  数据文件 : {DB_PATH}")
    print(f"  监听端口 : {args.port}")
    print()
    print("  ★ 管理员密钥（只有你自己知道，能发通告/任务）:")
    print(f"      {admin_key}")
    print()
    print("  ★ 接入密钥（发给同学，只能收）:")
    print(f"      {join_key}")
    print()
    print("  同学要填的服务器地址（把能连上的那个发给他们）:")
    for ip in local_ips():
        print(f"      {ip}:{args.port}")
    print()
    print("  提示：这是局域网服务，同学必须在同一网络下（校园网/同一 WiFi）。")
    print("        如果同学连不上，先检查 Windows 防火墙是否放行了这个端口。")
    print("=" * 66)
    print("  按 Ctrl+C 停止服务")
    print()

    Handler.api = Api(store, admin_key, join_key)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止服务。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
