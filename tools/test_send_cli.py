"""测试老师用的命令行工具 send.py（真的起服务端 + 真的执行命令）。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

import server as srv                                    # noqa: E402

OUT = Path(__file__).resolve().parent / "_send_test.txt"
L: list[str] = []
PASS, FAIL = [], []
PORT = 8799


def say(m: str = "") -> None:
    L.append(str(m))
    try:
        print(m)
    except UnicodeEncodeError:
        pass


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    say(("  [通过] " if cond else "  [失败] ") + name + (f"   {extra}" if extra else ""))


def run_send(*args: str) -> tuple[int, str]:
    """执行 send.py，返回 (退出码, 输出)。"""
    cmd = [sys.executable, str(ROOT / "server" / "send.py"),
           "--url", f"http://127.0.0.1:{PORT}", "--db", str(DB_PATH), *args]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


DB_PATH = Path()


def main() -> int:
    global DB_PATH
    tmp = Path(tempfile.mkdtemp(prefix="memo_send_"))
    DB_PATH = tmp / "class.db"

    store = srv.Store(DB_PATH)
    admin_key, join_key = srv.load_or_create_keys(store, "ADMIN-CLI", "MEMO-CLI")
    srv.Handler.api = srv.Api(store, admin_key, join_key)
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", PORT), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)
    say(f"服务端已在 127.0.0.1:{PORT} 启动（密钥从数据文件读）")
    say("")

    say("=== 1. ping ===")
    rc, out = run_send("ping")
    check("ping 成功", rc == 0 and "✓" in out, out.strip()[:120])

    say("")
    say("=== 2. 发通告 ===")
    rc, out = run_send("announcement", "明天交作业", "--body", "第 3 章习题")
    check("发送成功", rc == 0 and "已发出通告" in out, out.strip()[:120])
    check("服务端真的存了", len(store.announcements()) == 1,
          str([a["title"] for a in store.announcements()]))

    say("")
    say("=== 3. 发一次性任务 ===")
    rc, out = run_send("task", "周四交实验报告", "--date", "2026-12-31",
                       "--time", "08:00", "--priority", "高")
    check("发送成功", rc == 0 and "已发出班级任务" in out, out.strip()[:120])
    tasks = store.tasks()
    check("服务端存了任务", len(tasks) == 1, str([t["title"] for t in tasks]))
    check("日期/时间/优先级正确",
          tasks and tasks[0]["once_date"] == "2026-12-31"
          and tasks[0]["times"] == ["08:00"] and tasks[0]["priority"] == "高",
          str(tasks[0] if tasks else None)[:120])

    say("")
    say("=== 4. 发每天重复的任务 ===")
    rc, out = run_send("task", "每天背单词", "--time", "21:00")
    check("不填日期 = 每天重复", rc == 0 and "每天" in out, out.strip()[:120])
    t2 = [t for t in store.tasks() if t["title"] == "每天背单词"]
    check("recur 为 daily", t2 and t2[0]["recur"] == "daily",
          str(t2[0]["recur"] if t2 else None))

    say("")
    say("=== 5. list ===")
    rc, out = run_send("list")
    check("列出通告", "明天交作业" in out, out.strip()[:150])
    check("列出任务", "周四交实验报告" in out and "每天背单词" in out,
          out.strip()[:150])

    say("")
    say("=== 6. members ===")
    rc, out = run_send("members")
    check("能查接入名单", rc == 0 and "已接入" in out, out.strip()[:120])

    say("")
    say("=== 7. 撤回 ===")
    aid = store.announcements()[0]["id"]
    rc, out = run_send("withdraw-ann", str(aid))
    check("撤回通告成功", rc == 0 and "已撤回" in out, out.strip()[:120])
    check("服务端标记为撤回", store.announcements() == [],
          str(store.announcements()))

    tid = store.tasks()[0]["id"]
    rc, out = run_send("withdraw-task", str(tid))
    check("撤回任务成功", rc == 0 and "已撤回" in out, out.strip()[:120])
    check("服务端任务被撤回",
          all(t["id"] != tid for t in store.tasks()), str(store.tasks())[:120])

    say("")
    say("=== 8. 服务端没启动时的报错是否友好 ===")
    httpd.shutdown()
    time.sleep(0.3)
    rc, out = run_send("ping")
    check("连不上时给出人话提示", rc == 1 and "连不上" in out, out.strip()[:150])
    check("提示里告诉老师要检查什么",
          "启动" in out or "地址" in out, out.strip()[:150])

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
