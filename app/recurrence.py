"""★ 重复规则引擎 —— 整个程序最核心的一块。

它只回答两个问题：
    1. occurs_on(task, day)      -> 这个任务在某一天该不该出现？
    2. next_occurrence(task, after) -> 从某天之后，下一次该是哪天？

顺延（躲节假日）也在这一层做：算出来的那天如果是休息日，
就往后找最近的工作日，最多顺延 cfg.DEFER_MAX_DAYS 天，并在
next_occurrence 的返回里带上 original（原计划日）供界面显示「因假期顺延」。

想加新规则：在 cfg 里加常量 + 在 _MATCHERS 里加一个函数即可。
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Callable

from . import config as cfg
from .holidays import CALENDAR, HolidayCalendar
from .models import Task

MAX_LOOKAHEAD_DAYS = 400   # 一年 + 余量：足够覆盖「每年一次」之外的任何周期
DEFAULT_DEFER_LIMIT = cfg.DEFER_MAX_DAYS


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date | None:
    """某月第 n 个周几。n=-1 表示最后一个。

    weekday: 1=周一 ... 7=周日（和 ISO 一致）
    """
    last = days_in_month(year, month)
    if n == -1:
        d = date(year, month, last)
        offset = (d.isoweekday() - weekday) % 7
        return d - timedelta(days=offset)
    first = date(year, month, 1)
    offset = (weekday - first.isoweekday()) % 7
    d = first + timedelta(days=offset + (n - 1) * 7)
    return d if d.month == month else None


def countdown_from_month_end(year: int, month: int, n: int) -> date:
    """每月倒数第 n 天：n=1 就是最后一天。"""
    last = days_in_month(year, month)
    return date(year, month, max(1, last - max(0, n - 1)))


def iso_week_index(day: date) -> int:
    """ISO 周序号（用于单双周判断）。"""
    return day.isocalendar()[1]


def is_odd_week(day: date, ref: date | None = None) -> bool:
    """相对基准日算「单周 / 双周」。

    做法：算目标日与基准日相差了几周（按周一对齐），偶数=和基准同奇偶。
    """
    if ref is None:
        try:
            ref = date.fromisoformat(cfg.DEFAULT_UI_SETTINGS["ref_biweek_date"])
        except Exception:
            ref = date(2026, 1, 5)
    a = day - timedelta(days=day.weekday())
    b = ref - timedelta(days=ref.weekday())
    weeks = abs((a - b).days) // 7
    return weeks % 2 == 0     # True = 与基准同一个「单/双」


# ---------------------------------------------------------------------------
# 单个规则的匹配器：fn(task, day) -> bool
# ---------------------------------------------------------------------------

def _m_none(task: Task, day: date) -> bool:
    return bool(task.once_date) and date.fromisoformat(task.once_date) == day


def _m_daily(task: Task, day: date) -> bool:
    return True


def _m_weekly(task: Task, day: date) -> bool:
    weekdays = task.recur_params.get("weekdays") or [day.isoweekday()]
    return day.isoweekday() in weekdays


def _m_monthly(task: Task, day: date) -> bool:
    days = task.recur_params.get("monthdays") or [day.day]
    if day.day in days:
        return True
    # 支持 "32" 这种表示当月最后一天的约定？—— 不用，交给 RECUR_MONTH_END
    return False


def _m_workday(task: Task, day: date) -> bool:
    cal = _cal(task)
    return cal.is_workday(day)


def _m_biweek(task: Task, day: date) -> bool:
    weekdays = task.recur_params.get("weekdays") or [day.isoweekday()]
    if day.isoweekday() not in weekdays:
        return False
    ref = task.recur_params.get("ref_date")
    ref_d = date.fromisoformat(ref) if ref else None
    same = is_odd_week(day, ref_d)
    return same if task.recur_params.get("parity", "odd") == "odd" else (not same)


def _m_month_end(task: Task, day: date) -> bool:
    return day.day == days_in_month(day.year, day.month)


def _m_month_end_n(task: Task, day: date) -> bool:
    n = int(task.recur_params.get("n", 1))
    return day.day == countdown_from_month_end(day.year, day.month, n).day


def _m_month_nth(task: Task, day: date) -> bool:
    weekday = int(task.recur_params.get("weekday", 1))
    occ = task.recur_params.get("occurrences") or [1]
    for n in occ:
        d = nth_weekday_of_month(day.year, day.month, weekday, int(n))
        if d == day:
            return True
    return False


def _m_quarter(task: Task, day: date) -> bool:
    mode = task.recur_params.get("mode", "last_day")
    q = (day.month - 1) // 3 + 1
    start_m = (q - 1) * 3 + 1
    end_m = start_m + 2
    if mode == "first_day":
        return day.month == start_m and day.day == 1
    if mode == "last_day":
        return day.month == end_m and day.day == days_in_month(day.year, end_m)
    if mode == "nth_day":
        n = int(task.recur_params.get("n", 1))
        target = date(day.year, start_m, 1) + timedelta(days=max(0, n - 1))
        return day == target
    if mode == "nth_workday":     # 季度第 n 个工作日
        n = int(task.recur_params.get("n", 1))
        cur = date(day.year, start_m, 1) - timedelta(days=1)
        cnt = 0
        cal = _cal(task)
        while cnt < n:
            cur += timedelta(days=1)
            if cur.month > end_m and cur.year == day.year:
                return False
            if cal.is_workday(cur):
                cnt += 1
        return day == cur
    return False


_MATCHERS: dict[str, Callable[[Task, date], bool]] = {
    cfg.RECUR_NONE: _m_none,
    cfg.RECUR_DAILY: _m_daily,
    cfg.RECUR_WEEKLY: _m_weekly,
    cfg.RECUR_MONTHLY: _m_monthly,
    cfg.RECUR_WORKDAY: _m_workday,
    cfg.RECUR_BIWEEK: _m_biweek,
    cfg.RECUR_MONTH_END: _m_month_end,
    cfg.RECUR_MONTH_END_N: _m_month_end_n,
    cfg.RECUR_MONTH_NTH: _m_month_nth,
    cfg.RECUR_QUARTER: _m_quarter,
}


def _cal(task: Task, calendar_obj: HolidayCalendar | None = None) -> HolidayCalendar:
    return calendar_obj or CALENDAR


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def occurs_on(task: Task, day: date, calendar_obj: HolidayCalendar | None = None) -> bool:
    """任务在 day 这天「本来」该不该出现（不含节假日顺延）。"""
    if not task.enabled:
        return False
    if day.isoformat() in (task.skip_dates or []):
        return False
    matcher = _MATCHERS.get(task.recur, _m_daily)
    if not matcher(task, day):
        return False
    return True


def holds_on(task: Task, day: date, calendar_obj: HolidayCalendar | None = None) -> bool:
    """考虑了节假日顺延之后：day 这天到底要不要提醒。

    顺延规则：从原计划日开始，如果是休息日就往后挪，最多挪
    cfg.DEFER_MAX_DAYS 天；挪到的第一个工作日就是实际提醒日，
    之后再往后找下一天时不会再重复顺延（每次只看「原计划日」）。
    """
    cal = _cal(task, calendar_obj)
    if occurs_on(task, day, cal):
        return True
    # day 本身不是计划日，那它只会是「别人顺延过来的」——检查前面几天
    for back in range(1, DEFAULT_DEFER_LIMIT + 1):
        prev = day - timedelta(days=back)
        if not occurs_on(task, prev, cal):
            continue
        target = defer_target(task, prev, cal)
        return target == day
    return False


def defer_target(task: Task, planned: date, calendar_obj: HolidayCalendar | None = None) -> date:
    """把一个计划日按「躲节假日」折算成实际提醒日。"""
    cal = _cal(task, calendar_obj)
    if not task.recur_params.get("defer_on_holiday", cfg.DEFAULT_UI_SETTINGS["defer_on_holiday"]):
        return planned
    if task.recur == cfg.RECUR_WORKDAY:
        return planned          # 工作日规则本来就不会落在休息日
    cur = planned
    for _ in range(DEFAULT_DEFER_LIMIT):
        if not cal.is_rest_day(cur):
            return cur
        cur += timedelta(days=1)
    return planned              # 超过上限就放弃顺延，照原计划提醒（避免任务消失）


def next_occurrence(task: Task, after: date | None = None,
                    calendar_obj: HolidayCalendar | None = None,
                    include_today: bool = True) -> tuple[date, date | None] | None:
    """下一次该提醒的日期。

    返回 (实际提醒日, 原计划日)；没顺延则第二项为 None。
    after 默认今天。include_today=False 表示"今天之后的第一次"
    （提醒弹过之后重排下一次时用，避免又算回今天）。
    """
    cal = _cal(task, calendar_obj)
    start = after or date.today()
    if not include_today:
        start = start + timedelta(days=1)

    for i in range(MAX_LOOKAHEAD_DAYS):
        planned = start + timedelta(days=i)
        if not occurs_on(task, planned, cal):
            continue
        actual = defer_target(task, planned, cal)
        # 顺延后可能落到 already-passed 的日子；只要不小于 start 就算数
        if actual < start:
            # 顺延后仍早于搜索起点（罕见），跳过继续找
            continue
        return actual, (planned if actual != planned else None)

    # 400 天内都没命中：一次性任务过期 / 或者条件太苛刻
    return None


def occurrences_between(task: Task, start: date, end: date,
                        calendar_obj: HolidayCalendar | None = None) -> list[date]:
    """某段日期内所有「实际提醒日」（日历视图打点、统计都用它）。"""
    cal = _cal(task, calendar_obj)
    out: list[date] = []
    if end < start:
        return out
    cur = start
    seen: set[date] = set()
    while cur <= end:
        if holds_on(task, cur, cal) and cur not in seen:
            out.append(cur)
            seen.add(cur)
        cur += timedelta(days=1)
    return out


def compute_next_at(task: Task, now: datetime | None = None) -> str | None:
    """把「下一次日期 + 主时间」拼成 next_at 字符串，供调度器使用。

    注意：当天已经过了主时间、但任务还没完成时，仍然返回今天的时间，
    这样调度器会发现「已经过期」从而触发补提醒。
    """
    now = now or datetime.now()
    res = next_occurrence(task, after=now.date())
    if not res:
        return None
    actual, _ = res
    t = task.first_time
    return f"{actual.isoformat()} {t.hour:02d}:{t.minute:02d}"


# ---------------------------------------------------------------------------
# 给界面显示的中文描述
# ---------------------------------------------------------------------------

def describe(task: Task) -> str:
    """一行人类可读的重复说明，例：『每周一、周三』『每月最后一天』"""
    r = task.recur
    p = task.recur_params or {}
    if r == cfg.RECUR_NONE:
        try:
            d = date.fromisoformat(task.once_date) if task.once_date else None
        except Exception:
            d = None
        return f"仅 {d.month}月{d.day}日" if d else "只提醒一次"
    if r == cfg.RECUR_DAILY:
        return "每天"
    if r == cfg.RECUR_WORKDAY:
        return "每个工作日"
    if r == cfg.RECUR_WEEKLY:
        wd = sorted(p.get("weekdays") or [])
        return "每周" + "、".join(cfg.WEEKDAY_CN.get(w, "?")[1:] for w in wd)
    if r == cfg.RECUR_BIWEEK:
        wd = sorted(p.get("weekdays") or [])
        names = "、".join(cfg.WEEKDAY_CN.get(w, "?")[1:] for w in wd)
        parity = "单周" if p.get("parity", "odd") == "odd" else "双周"
        return f"每{parity}周{names}"
    if r == cfg.RECUR_MONTHLY:
        ds = sorted(p.get("monthdays") or [])
        return "每月 " + "、".join(f"{d}号" for d in ds)
    if r == cfg.RECUR_MONTH_END:
        return "每月最后一天"
    if r == cfg.RECUR_MONTH_END_N:
        return f"每月倒数第{int(p.get('n', 1))}天"
    if r == cfg.RECUR_MONTH_NTH:
        names = ["", "第一个", "第二个", "第三个", "第四个", "第五个"]
        wd = cfg.WEEKDAY_CN.get(int(p.get("weekday", 1)), "周一")[1:]
        occ = p.get("occurrences") or [1]
        parts = []
        for n in occ:
            n = int(n)
            parts.append("最后一个" if n == -1 else names[n] if 0 < n < len(names) else f"第{n}个")
        return f"每月{'、'.join(p + wd for p in parts)}"
    if r == cfg.RECUR_QUARTER:
        mode = p.get("mode", "last_day")
        return {
            "first_day": "每季度第一天",
            "last_day": "每季度最后一天",
            "nth_day": f"每季度第{int(p.get('n', 1))}天",
            "nth_workday": f"每季度第{int(p.get('n', 1))}个工作日",
        }.get(mode, "每季度")
    return cfg.RECUR_LABELS.get(r, r)


def summarize_time(task: Task) -> str:
    """给卡片显示的时间部分，例：『08:30』或『08:30 / 21:00』。"""
    return " / ".join(t.strftime("%H:%M") for t in task.times_as_time())
