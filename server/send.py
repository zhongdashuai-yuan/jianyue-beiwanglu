"""老师用的命令行发送工具。

用法（在项目根目录执行）：
    python server\\send.py ping
    python server\\send.py announcement "明天交作业" --body "第 3 章习题"
    python server\\send.py task "周四交实验报告" --date 2026-10-08 --time 08:00
    python server\\send.py list
    python server\\send.py withdraw-ann 3
    python server\\send.py withdraw-task 2
    python server\\send.py members

设计说明：
  * 密钥不用手输 —— 从服务端的 class.db 里读（所以本工具要在放服务端数据的那台机器上跑）
  * 服务端地址默认 127.0.0.1:8765；如果服务端不在本机，用 --url 指定
  * 只依赖标准库
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8765"
SERVER_DATA = Path(__file__).resolve().parent / "server_data"
DB_PATH = SERVER_DATA / "class.db"

# Windows 控制台默认是 GBK，打 ✓/✗ 这类符号会抛 UnicodeEncodeError 直接崩掉。
# 这个工具是给老师用的命令行，必须稳，所以启动时主动把输出切成 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
    except Exception:
        pass


def read_admin_key(db: Path) -> str | None:
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(str(db))
        r = conn.execute("SELECT value FROM meta WHERE key='admin_key'").fetchone()
        conn.close()
        return r[0] if r else None
    except Exception:
        return None


def call(url: str, path: str, key: str, body: dict | None = None,
         method: str = "POST") -> tuple[int, dict]:
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url.rstrip("/") + path, data=data,
                                 method=method if data is not None else "GET")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if key:
        req.add_header("X-Memo-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"ok": False, "error": f"连不上服务端（{type(e).__name__}）。"
                                        f"服务端启动了吗？地址写对了吗？"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="班级通告发送工具（老师用）")
    # 全局参数用 parse_known_args 解析：这样 `send.py ping --url ...` 和
    # `send.py --url ... ping` 两种写法都能用。
    # （argparse 默认只接受"子命令之前"的全局参数，人会很自然写在后面，
    #   所以这里特意放宽 —— 第一版就因为这个报"unrecognized arguments"。）
    ap.add_argument("--url", default=DEFAULT_URL, help=f"服务端地址（默认 {DEFAULT_URL}）")
    ap.add_argument("--db", default=str(DB_PATH), help="服务端数据文件（用来读密钥）")
    ap.add_argument("--key", default=None, help="管理员密钥（不填则从数据文件读）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("ping", help="测试服务端是否在跑")
    p = sub.add_parser("announcement", help="发一条通告")
    p.add_argument("title")
    p.add_argument("--body", default="", help="通告正文（可多行）")

    p = sub.add_parser("task", help="发一个班级任务（会进同学的待办并提醒）")
    p.add_argument("title")
    p.add_argument("--note", default="", help="备注")
    p.add_argument("--date", default=None, help="哪一天（YYYY-MM-DD），不填=每天")
    p.add_argument("--time", default="08:00", help="提醒时间 HH:MM（默认 08:00）")
    p.add_argument("--priority", default="中", choices=["高", "中", "低"])
    p.add_argument("--workday", action="store_true", help="改成每个工作日重复")

    sub.add_parser("list", help="列出已发的通告和任务")
    p = sub.add_parser("withdraw-ann", help="撤回通告")
    p.add_argument("id", type=int)
    p = sub.add_parser("withdraw-task", help="撤回班级任务")
    p.add_argument("id", type=int)
    sub.add_parser("members", help="看谁接入了、最近什么时候同步")

    args = ap.parse_known_args(argv)
    # 允许全局参数写在子命令后面：把解析剩下的参数重新喂给子命令解析一遍
    known, rest = args
    if rest:
        # 把剩下的参数拼回"子命令之后"再解析一次（只为了吃下 --url/--db/--key）
        try:
            ap2 = argparse.ArgumentParser(add_help=False)
            ap2.add_argument("--url", default=None)
            ap2.add_argument("--db", default=None)
            ap2.add_argument("--key", default=None)
            extra, unknown = ap2.parse_known_args(rest)
            if extra.url:
                known.url = extra.url
            if extra.db:
                known.db = extra.db
            if extra.key:
                known.key = extra.key
            if unknown:
                ap.error(f"无法识别的参数: {' '.join(unknown)}")
        except SystemExit:
            raise
    args = known
    if not args.cmd:
        ap.print_help()
        return 1

    key = args.key or read_admin_key(Path(args.db)) or ""
    if args.cmd == "ping":
        code, d = call(args.url, "/api/ping", "")
        if code == 200:
            print(f"✓ 服务端在跑：{d.get('app')} v{d.get('version')}  服务器时间 {d.get('time')}")
            return 0
        print(f"✗ 连不上：{d.get('error') or code}")
        return 1

    if not key:
        print("✗ 没找到管理员密钥。请先启动一次 server\\server.py，"
              "或用 --key 手动指定。")
        return 1

    if args.cmd == "announcement":
        code, d = call(args.url, "/api/announcement", key,
                       {"title": args.title, "body": args.body})
        if d.get("ok"):
            print(f"✓ 已发出通告 #{d.get('id')}：{args.title}")
            print("  （同学的程序几分钟内会收到）")
            return 0
        print(f"✗ 发送失败：{d.get('error') or code}")
        return 1

    if args.cmd == "task":
        payload = {"title": args.title, "note": args.note,
                   "times": [args.time], "priority": args.priority}
        if args.workday:
            payload["recur"] = "workday"
        elif args.date:
            payload["recur"] = "none"
            payload["once_date"] = args.date
        else:
            payload["recur"] = "daily"
        code, d = call(args.url, "/api/task", key, payload)
        if d.get("ok"):
            when = args.date or ("每个工作日" if args.workday else "每天")
            print(f"✓ 已发出班级任务 #{d.get('id')}：{args.title}")
            print(f"  {when} {args.time} 提醒，优先级 {args.priority}")
            return 0
        print(f"✗ 发送失败：{d.get('error') or code}")
        return 1

    if args.cmd == "list":
        code, d = call(args.url, "/api/members", key)
        if not d.get("ok"):
            print(f"✗ {d.get('error') or code}")
            return 1
        # 通告和任务通过 sync 接口拿（成员接口只给名单）
        code2, d2 = call(args.url, "/api/sync", key, {"since_announcement_id": 0,
                                                      "since_task_id": 0})
        anns = d2.get("announcements") or []
        tasks = d2.get("tasks") or []
        print(f"通告（{len(anns)} 条）:")
        for a in anns:
            print(f"  #{a['id']:<3} {a['created_at'][:16]}  {a['title']}")
        print(f"班级任务（{len(tasks)} 个）:")
        for t in tasks:
            print(f"  #{t['id']:<3} {t.get('once_date') or t.get('recur')} "
                  f"{','.join(t.get('times') or [])}  {t['title']}")
        return 0

    if args.cmd == "withdraw-ann":
        code, d = call(args.url, "/api/announcement/withdraw", key, {"id": args.id})
        print("✓ 已撤回，同学下次同步会消失" if d.get("ok")
              else f"✗ 撤回失败：{d.get('error') or code}")
        return 0 if d.get("ok") else 1

    if args.cmd == "withdraw-task":
        code, d = call(args.url, "/api/task/withdraw", key, {"id": args.id})
        print("✓ 已撤回，同学下次同步会删掉这条" if d.get("ok")
              else f"✗ 撤回失败：{d.get('error') or code}")
        return 0 if d.get("ok") else 1

    if args.cmd == "members":
        code, d = call(args.url, "/api/members", key)
        if not d.get("ok"):
            print(f"✗ {d.get('error') or code}")
            return 1
        ms = d.get("members") or []
        print(f"已接入 {len(ms)} 人：")
        for m in ms:
            name = m.get("name") or "（没填名字）"
            print(f"  {name:<10} 首次 {m['first_seen'][:16]}  "
                  f"最近 {m['last_seen'][:16]}  {m['device_id']}")
        st = d.get("stats") or {}
        print(f"\n当前有效内容：通告 {st.get('announcements')} 条，"
              f"班级任务 {st.get('tasks')} 个")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
