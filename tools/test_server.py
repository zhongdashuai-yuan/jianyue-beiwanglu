"""服务端自测：把 server.py 跑起来，用真实 HTTP 请求验证每个接口。

不依赖任何第三方库，只用标准库 urllib。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import server as srv                                    # noqa: E402

OUT = Path(__file__).resolve().parent / "_server_test.txt"
L: list[str] = []
PASS, FAIL = [], []


def say(m: str = "") -> None:
    L.append(str(m))
    try:
        print(m)
    except UnicodeEncodeError:
        pass


def req(base: str, path: str, key: str | None = None, body: dict | None = None,
        method: str | None = None):
    url = base + path
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method or
                               ("POST" if data is not None else "GET"))
    r.add_header("Content-Type", "application/json")
    if key:
        r.add_header("X-Memo-Key", key)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    say(("  [通过] " if cond else "  [失败] ") + name + (f"   {extra}" if extra else ""))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="memo_srv_"))
    store = srv.Store(tmp / "class.db")
    admin_key, join_key = srv.load_or_create_keys(store, None, None)
    srv.Handler.api = srv.Api(store, admin_key, join_key)

    # 在随机可用端口上起服务
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    say(f"服务端已启动: {base}")
    say(f"管理员密钥={admin_key}  接入密钥={join_key}")
    say("")

    # ---- 1. 连通性 ----
    say("=== 1. 连通性与鉴权 ===")
    code, d = req(base, "/api/ping")
    check("无需密钥就能 ping", code == 200 and d.get("ok"), str(d)[:60])

    code, d = req(base, "/api/sync", key="WRONG-KEY")
    check("错误密钥被拒绝", code == 401, f"HTTP {code}")

    code, d = req(base, "/api/sync", key=None)
    check("不传密钥被拒绝", code == 401, f"HTTP {code}")

    code, d = req(base, "/api/sync", key=join_key, body={"device_id": "dev-1",
                                                         "name": "张三",
                                                         "client_version": "1.0.0"})
    check("接入密钥能同步", code == 200 and d.get("ok"), str(d)[:80])
    check("新库同步返回空列表",
          d.get("announcements") == [] and d.get("tasks") == [], str(d)[:80])

    # ---- 2. 权限隔离 ----
    say("")
    say("=== 2. 权限：接入密钥不能发东西 ===")
    code, d = req(base, "/api/announcement", key=join_key,
                  body={"title": "我想冒充老师", "body": "x"})
    check("接入密钥发通告被拒（403）", code == 403, f"HTTP {code} {d.get('error')}")

    code, d = req(base, "/api/task", key=join_key, body={"title": "冒充"})
    check("接入密钥发任务被拒（403）", code == 403, f"HTTP {code} {d.get('error')}")

    code, d = req(base, "/api/members", key=join_key)
    check("接入密钥看名单被拒（403）", code == 403, f"HTTP {code}")

    # ---- 3. 老师发通告 ----
    say("")
    say("=== 3. 老师发通告 / 撤回 ===")
    code, d = req(base, "/api/announcement", key=admin_key,
                  body={"title": "明天交作业", "body": "第 3 章习题，拍照上传"})
    aid = d.get("id")
    check("管理员能发通告", code == 200 and aid, str(d))

    code, d = req(base, "/api/announcement", key=admin_key, body={"title": "  "})
    check("空标题被拒绝（400）", code == 400, f"HTTP {code}")

    code, d = req(base, "/api/sync", key=join_key, body={"device_id": "dev-1"})
    anns = d.get("announcements") or []
    check("同学能收到通告", len(anns) == 1 and anns[0]["title"] == "明天交作业",
          str(anns)[:90])

    code, d = req(base, "/api/sync", key=join_key,
                  body={"device_id": "dev-1", "since_announcement_id": aid})
    check("按 since_id 增量同步（不再重复下发）",
          (d.get("announcements") or []) == [], str(d.get("announcements")))

    code, d = req(base, "/api/announcement/withdraw", key=admin_key, body={"id": aid})
    check("管理员能撤回", code == 200 and d.get("ok"))
    code, d = req(base, "/api/sync", key=join_key, body={"device_id": "dev-1"})
    check("撤回后客户端拿到撤回列表", aid in (d.get("withdrawn_announcements") or []),
          str(d.get("withdrawn_announcements")))

    # ---- 4. 班级任务 ----
    say("")
    say("=== 4. 老师发班级任务 ===")
    code, d = req(base, "/api/task", key=admin_key,
                  body={"title": "交作业", "note": "拍照上传", "times": ["08:00"],
                        "recur": "none", "once_date": "2026-09-25"})
    ctid = d.get("id")
    check("管理员能发任务", code == 200 and ctid, str(d))

    code, d = req(base, "/api/sync", key=join_key, body={"device_id": "dev-2"})
    tasks = d.get("tasks") or []
    check("同学能收到任务", len(tasks) == 1 and tasks[0]["title"] == "交作业",
          str(tasks)[:90])
    check("任务的 times 是数组（JSON 已解析）",
          isinstance(tasks[0].get("times"), list), str(tasks[0].get("times")))

    # ---- 5. 名单 ----
    say("")
    say("=== 5. 接入名单 ===")
    code, d = req(base, "/api/members", key=admin_key)
    names = [m.get("name") for m in (d.get("members") or [])]
    check("管理员能看到接入名单", code == 200 and "张三" in names, str(names))
    check("名单里有设备数统计", (d.get("stats") or {}).get("members") == 2,
          str(d.get("stats")))

    # ---- 6. 限流 ----
    say("")
    say("=== 6. 频率限制 ===")
    hit429 = False
    for _ in range(srv.RATE_LIMIT_PER_MIN + 15):
        code, _d = req(base, "/api/ping")
        if code == 429:
            hit429 = True
            break
    check("狂刷会被限制（429）", hit429)

    # ---- 7. 未知接口 ----
    # 注意：上一步把本机 IP 的限流额度刷满了，这里换个 IP 直接调 Api，
    # 否则会被 429 拦掉、测不到 404（第一版就踩了这个坑）。
    say("")
    say("=== 7. 未知接口 ===")
    code, d = srv.Handler.api.handle("GET", "/api/nothing-here",
                                     {"x-memo-key": admin_key}, {}, "10.9.9.9")
    check("未知接口返回 404", code == 404, f"HTTP {code}")
    code, d = srv.Handler.api.handle("POST", "/api/announcement",
                                     {"x-memo-key": ""}, {"title": "x"}, "10.9.9.10")
    check("缺密钥返回 403", code == 403, f"HTTP {code}")

    httpd.shutdown()

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
