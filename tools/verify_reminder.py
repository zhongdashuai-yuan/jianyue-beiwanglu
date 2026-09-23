"""端到端提醒验证：造一条几十秒后到点的任务，真的跑一遍程序，看它有没有提醒。

这是"提醒"这条核心链路唯一能自动验证的办法 —— 光跑单元测试证明不了
调度器真的会在到点时触发。

用法：
    python tools/verify_reminder.py

它做这些事：
  1. 用独立的临时数据库（不碰你的正式数据）
  2. 造一条 now+45 秒到点的任务，以及一条昨天错过、还没完成的任务
  3. 用子进程真的把 main.py 跑起来，等约 100 秒
  4. 检查：fire_log 里有没有 popup 记录、next_at 有没有推进、
           补提醒（catchup）有没有触发
  5. 结束子进程，输出结论

日志写到 tools/_verify_report.txt
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT = Path(__file__).resolve().parent / "_verify_report.txt"
lines: list[str] = []


def say(msg: str) -> None:
    lines.append(msg)
    try:
        print(msg)
    except UnicodeEncodeError:
        # 控制台是 GBK，遇到生僻字符就替换掉，别让脚本因为打印失败而崩
        try:
            print(msg.encode("gbk", "replace").decode("gbk", "replace"))
        except Exception:
            pass


def main() -> int:
    import tempfile

    from app import config as cfg
    from app.database import Database
    from app.models import Task

    tmp = Path(tempfile.mkdtemp(prefix="memo_verify_"))
    db_path = tmp / "verify.db"
    # 让子进程用这个临时数据库和设置文件（靠环境变量传进去）
    os.environ["MEMO_DB_PATH"] = str(db_path)
    os.environ["MEMO_SETTINGS_PATH"] = str(tmp / "ui.json")
    os.environ["MEMO_VERIFY"] = "1"

    db = Database(db_path)
    now = datetime.now()

    # 任务 A：45 秒后到点（主链路：到点弹提醒）
    fire_at = now + timedelta(seconds=45)
    a = Task(title="【自动测试】即将到点的提醒", times=[fire_at.strftime("%H:%M")],
             priority="高", category="工作", recur=cfg.RECUR_DAILY,
             next_at=fire_at.strftime("%Y-%m-%d %H:%M"), enabled=True,
             created_at=now.strftime("%Y-%m-%d %H:%M:%S"))
    a_id = db.add_task(a)

    # 任务 B：今天早上 07:30 的一条，故意把 next_at 设成很久以前且没完成
    #   -> 调度器应该在启动补提醒时触发它（kind='catchup'）
    b = Task(title="【自动测试】错过的提醒（补提醒）", times=["07:30"],
             priority="中", category="生活", recur=cfg.RECUR_DAILY,
             next_at=f"{now.date()} 07:30", enabled=True,
             created_at=now.strftime("%Y-%m-%d %H:%M:%S"))
    b_id = db.add_task(b)
    db.close()

    say("=" * 68)
    say(f"验证开始  {now:%Y-%m-%d %H:%M:%S}")
    say(f"任务 A (id={a_id}) 应在 {fire_at:%H:%M:%S} 触发（约 45 秒后）")
    say(f"任务 B (id={b_id}) 是今天 07:30 已错过、未完成的，应触发补提醒")
    say(f"临时数据库: {db_path}")
    say("-" * 68)

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"   # 不真的弹窗到你屏幕上干扰你
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "main.py")],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    # 等到任务 A 到点后再多等 25 秒，给调度器（10 秒一个 tick）留时间
    wait_sec = 45 + 25
    say(f"程序已启动（PID {proc.pid}），等待 {wait_sec} 秒观察是否提醒...")
    for i in range(wait_sec):
        time.sleep(1)
        if proc.poll() is not None:
            say(f"!! 程序提前退出了，退出码 {proc.returncode}")
            break
        if i and i % 15 == 0:
            say(f"   ...已等待 {i} 秒")

    # 读结果（程序还在跑，用只读方式再开一个连接）
    time.sleep(1)
    result_db = Database(db_path)
    fires = result_db.last_fires(50)
    say("-" * 68)
    say(f"fire_log 共 {len(fires)} 条：")
    for f in fires:
        say(f"   {f['fired_at']}  task={f['task_id']}  kind={f['kind']}  "
            f"result={f['result']}  planned={f['planned_at']}")

    ta = result_db.get_task(a_id)
    tb = result_db.get_task(b_id)
    say("-" * 68)

    ok = True

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal ok
        if not cond:
            ok = False
        say(("  [通过] " if cond else "  [失败] ") + name + (f"   {extra}" if extra else ""))

    a_kinds = {f["kind"] for f in fires if f["task_id"] == a_id}
    b_kinds = {f["kind"] for f in fires if f["task_id"] == b_id}
    a_count = len([f for f in fires if f["task_id"] == a_id])
    b_fired = any(k in ("popup", "catchup") for k in b_kinds)

    check("任务 A 触发了提醒（准点路径）", bool(a_kinds), f"kind={a_kinds}")
    check("任务 A 只提醒了一次（没有重复弹窗刷屏）", a_count == 1,
          f"共 {a_count} 次")
    check("任务 A 的 last_fired_at 已写入", bool(ta and ta.last_fired_at),
          str(ta.last_fired_at if ta else None))
    check("任务 A 的 next_at 已推到下一次（不会一直卡在过期）",
          bool(ta and ta.next_at and ta.next_at != a.next_at),
          f"{a.next_at} -> {ta.next_at if ta else None}")
    check("任务 A 的下次时间在未来",
          bool(ta and ta.next_at and ta.next_at > now.strftime("%Y-%m-%d %H:%M")),
          str(ta.next_at if ta else None))

    # 任务 B 是"今天 07:30 错过、现在已过去很久"的情况。
    # 按设计（cfg.STARTUP_SILENT_MINUTES=60 分钟）它属于"启动时静默"的过期提醒：
    # 不该弹窗刷屏，但要留下 advance 记录说明为什么没提醒，也不能被标成已完成。
    if b_fired:
        check("任务 B 触发了补提醒（错过在静默阈值内）", True, f"kind={b_kinds}")
    else:
        check("任务 B 按设计静默跳过（过期超过阈值，不弹窗）",
              "advance" in b_kinds,
              f"kind={b_kinds}（应为 advance：已跳过并重排）")
    check("任务 B 仍是未完成状态（界面上要显示它还没做）",
          bool(tb and not tb.done), f"done={tb.done if tb else None}")
    check("任务 B 的下次提醒已排到未来（不会一直卡在过去）",
          bool(tb and tb.next_at and tb.next_at > now.strftime("%Y-%m-%d %H:%M")),
          str(tb.next_at if tb else None))

    # 收尾
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    say("-" * 68)
    try:
        out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
        if out.strip():
            say("程序输出：")
            say(out[-1500:])
    except Exception:
        pass

    result_db.close()
    say("=" * 68)
    say("验证结论：" + ("全部通过" if ok else "有失败项"))
    say("=" * 68)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
