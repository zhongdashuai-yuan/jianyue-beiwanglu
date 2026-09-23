"""分析 DSH 插件目录，找出 star 最多 / 下载最多的插件。

用法：
    python tools/rank_plugins.py            # 用本地缓存的目录数据
    python tools/rank_plugins.py --fetch    # 先重新下载目录数据
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CATALOG = Path(r"E:\Memo\_npm\awesome2.json")
URL = "https://awesome-dsh-plugin.com/plugins.json"


def fetch() -> None:
    import subprocess
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["curl.exe", "-s", "--max-time", "120", URL, "-o", str(CATALOG)],
                       capture_output=True)
    print("下载完成:", CATALOG.stat().st_size, "字节" if r.returncode == 0 else "失败")


def fmt_num(v) -> str:
    if v is None:
        return "-"
    return f"{v:,}"


def show(title: str, items: list[dict], limit: int = 15) -> None:
    print()
    print("=" * 104)
    print(title)
    print("=" * 104)
    print(f"{'插件':<34}{'Star':>7}{'月下载':>12}  {'分类':<20}{'npm 包'}")
    print("-" * 104)
    for p in items[:limit]:
        print(f"{p['name'][:33]:<34}{p.get('stars') or 0:>7}{fmt_num(p.get('downloads')):>12}  "
              f"{str(p.get('category'))[:19]:<20}{(p.get('npm') or '-')[:38]}")
        desc = (p.get("description") or {})
        text = desc.get("zh") or desc.get("en") or ""
        if text:
            print(f"{'':<34}{text[:88]}")


def main() -> int:
    if "--fetch" in sys.argv or not CATALOG.exists():
        fetch()
    if not CATALOG.exists():
        print("没有目录数据，先跑 --fetch")
        return 1

    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    plugins = data["plugins"]
    print(f"目录更新: {data.get('updated')}   插件总数: {data.get('count')}")

    stars = lambda p: p.get("stars") or 0          # noqa: E731
    dls = lambda p: p.get("downloads") or 0        # noqa: E731

    show("【一】按 GitHub Star 排序（最受关注）",
         sorted(plugins, key=stars, reverse=True))
    show("【二】按 npm 月下载量排序（实际用的人最多）",
         sorted(plugins, key=dls, reverse=True))

    # 同时给一份"综合评分"：star 和下载都看
    print()
    print("=" * 104)
    print("【三】综合排序（star 前 60 且下载前 200 的交集）")
    print("=" * 104)
    top_star = {p["name"] for p in sorted(plugins, key=stars, reverse=True)[:60]}
    top_dl = {p["name"] for p in sorted(plugins, key=dls, reverse=True)[:200]}
    both = [p for p in plugins if p["name"] in top_star and p["name"] in top_dl]
    both.sort(key=lambda p: (stars(p), dls(p)), reverse=True)
    print(f"{'插件':<34}{'Star':>7}{'月下载':>12}  {'分类':<20}{'npm 包'}")
    print("-" * 104)
    for p in both[:20]:
        print(f"{p['name'][:33]:<34}{stars(p):>7}{fmt_num(p.get('downloads')):>12}  "
              f"{str(p.get('category'))[:19]:<20}{(p.get('npm') or '-')[:38]}")

    print()
    print("分类统计:")
    cats: dict[str, int] = {}
    for p in plugins:
        c = str(p.get("category"))
        cats[c] = cats.get(c, 0) + 1
    for c, n in sorted(cats.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {c:<24} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
