"""核实 DSH 插件的可信度：npm 元数据 + GitHub 仓库活跃度。

用法：
    python tools/verify_plugin.py dshmarket dsh-better-sidebar ...
    python tools/verify_plugin.py --top      # 核实推荐清单里的默认那批
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CACHE = Path(r"E:\Memo\_npm\verify")
DEFAULT = [
    "dshmarket",
    "dsh-better-sidebar",
    "dsh-context",
    "dsh-cost-meter",
    "dsh-agent-teams",
    "billion-context",
    "@liustack/modlens",
    "@xmanrui/dsh-im",
    "@linxin666/dsh-web-all",
    "@linxin666/dsh-remote-web-ui",
    "dsh-univer-office",
]


def curl(url: str, out: Path) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["curl.exe", "-s", "-L", "--max-time", "90",
                        "-H", "User-Agent: memo-app", url, "-o", str(out)],
                       capture_output=True)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 2


def load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def check(pkg: str) -> dict:
    safe = pkg.replace("/", "__").replace("@", "at_")
    npm_file = CACHE / f"{safe}.npm.json"
    if not curl(f"https://registry.npmjs.org/{pkg}", npm_file):
        return {"pkg": pkg, "error": "npm 查询失败"}
    d = load(npm_file) or {}
    if "error" in d:
        return {"pkg": pkg, "error": d["error"]}

    latest = (d.get("dist-tags") or {}).get("latest")
    ver = (d.get("versions") or {}).get(latest, {})
    times = d.get("time") or {}
    repo = d.get("repository")
    repo_url = repo.get("url") if isinstance(repo, dict) else repo
    scripts = ver.get("scripts") or {}

    out = {
        "pkg": pkg,
        "desc": (d.get("description") or "").strip(),
        "latest": latest,
        "published": (times.get(latest) or "")[:10],
        "created": (times.get("created") or "")[:10],
        "modified": (times.get("modified") or "")[:10],
        "repo": repo_url or "",
        "license": ver.get("license") or d.get("license") or "",
        "versions": len(d.get("versions") or {}),
        # 安装脚本是重要的安全信号
        "install_scripts": {k: v for k, v in scripts.items()
                            if k in ("preinstall", "install", "postinstall", "prepare")},
        "has_install_script": any(k in scripts for k in
                                  ("preinstall", "install", "postinstall")),
        "deps": list((ver.get("dependencies") or {}).keys()),
    }

    # 下载量
    dl_file = CACHE / f"{safe}.dl.json"
    if curl(f"https://api.npmjs.org/downloads/point/last-month/{pkg}", dl_file):
        dl = load(dl_file) or {}
        # 包名里的 / 会被 URL 吃掉，用 total 字段更稳
        out["monthly"] = dl.get("downloads", dl.get("total"))
    else:
        out["monthly"] = None

    # GitHub 仓库活跃度
    if repo_url and "github.com" in repo_url:
        slug = repo_url.split("github.com/")[-1].removesuffix(".git").strip("/")
        gh_file = CACHE / f"{safe}.gh.json"
        if curl(f"https://api.github.com/repos/{slug}", gh_file):
            g = load(gh_file) or {}
            if "stargazers_count" in g:
                out["gh_slug"] = slug
                out["gh_stars"] = g.get("stargazers_count")
                out["gh_pushed"] = (g.get("pushed_at") or "")[:10]
                out["gh_license"] = ((g.get("license") or {}) or {}).get("spdx_id")
                out["gh_open_issues"] = g.get("open_issues_count")
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pkgs = args or (DEFAULT if "--top" in sys.argv or not args else [])
    if not pkgs:
        pkgs = DEFAULT

    rows = [check(p) for p in pkgs]
    lines: list[str] = []
    for r in rows:
        if r.get("error"):
            lines.append(f"{r['pkg']:<34} 查询失败: {r['error']}")
            continue
        dl = r.get("monthly")
        stars = r.get("gh_stars")
        flag = "有安装脚本!" if r.get("has_install_script") else "无安装脚本"
        lines.append(f"{r['pkg']:<34} v{r['latest']:<10} 月下载 {(f'{dl:,}' if dl else '-'):>9}  "
                     f"star {(stars if stars is not None else '-'):>6}  "
                     f"更新 {r['modified']}  许可 {r['license'] or r.get('gh_license') or '?':<12} {flag}")
        lines.append(f"     仓库: {r.get('gh_slug') or r['repo'] or '无'}   "
                     f"仓库最后推送: {r.get('gh_pushed') or '?'}")
        lines.append(f"     说明: {r['desc'][:110]}")
        if r["install_scripts"]:
            lines.append(f"     安装脚本内容: {r['install_scripts']}")
        lines.append("")

    text = "\n".join(lines)
    print(text)
    (CACHE / "_report.txt").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
