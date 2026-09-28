"""中国法定节假日 + 调休补班表（内置 2025 / 2026 / 2027）。

数据来源：国务院办公厅《关于XXXX年部分节假日安排的通知》。
★ 2027 年安排通常在 2026 年 11 月公布，公布后把下面 2027 的空列表填上即可；
  也可以不改代码，直接在程序「设置 -> 节假日」里手动补充。

用法：
    from app.holidays import is_holiday, is_makeup_workday, is_workday
    is_workday(date(2026, 2, 17))   # -> False（春节假期）
    is_workday(date(2026, 2, 14))   # -> True （补班日，虽然是周六）
"""

from __future__ import annotations

import json
from datetime import date

# ---------------------------------------------------------------------------
# 内置数据：只写「放假的日期」和「周末补班的日期」
# ---------------------------------------------------------------------------
# 2025 年（国办发明电〔2024〕7 号）
HOLIDAYS_2025 = [
    # 元旦
    "2025-01-01",
    # 春节
    "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-02-01", "2025-02-02", "2025-02-03", "2025-02-04",
    # 清明
    "2025-04-04", "2025-04-05", "2025-04-06",
    # 劳动节
    "2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04", "2025-05-05",
    # 端午
    "2025-05-31", "2025-06-01", "2025-06-02",
    # 国庆 + 中秋
    "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-04",
    "2025-10-05", "2025-10-06", "2025-10-07", "2025-10-08",
]
MAKEUP_2025 = ["2025-01-26", "2025-02-08", "2025-04-27", "2025-09-28", "2025-10-11"]

# 2026 年（国办发明电〔2025〕7 号，2025-11-04 发布
#          https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm）
#   元旦   1/1(四)~1/3(六) 放假；1/4(日) 上班
#   春节   2/15(日)~2/23(一) 放假 9 天；2/14(六)、2/28(六) 上班
#   清明   4/4(六)~4/6(一) 放假（无需调休）
#   劳动节 5/1(五)~5/5(二) 放假 5 天；5/9(六) 上班
#   端午   6/19(五)~6/21(日) 放假（无需调休）
#   中秋   9/25(五)~9/27(日) 放假（无需调休）
#   国庆   10/1(四)~10/7(三) 放假 7 天；9/20(日)、10/10(六) 上班
HOLIDAYS_2026 = [
    # 元旦
    "2026-01-01", "2026-01-02", "2026-01-03",
    # 春节（农历正月初一 = 2/17）
    "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18",
    "2026-02-19", "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    # 清明
    "2026-04-04", "2026-04-05", "2026-04-06",
    # 劳动节
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    # 端午（农历五月初五 = 6/19）
    "2026-06-19", "2026-06-20", "2026-06-21",
    # 中秋（农历八月十五 = 9/25）
    "2026-09-25", "2026-09-26", "2026-09-27",
    # 国庆
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
    "2026-10-05", "2026-10-06", "2026-10-07",
]
MAKEUP_2026 = ["2026-01-04", "2026-02-14", "2026-02-28",
               "2026-05-09", "2026-09-20", "2026-10-10"]

# 2027 年：国务院通知一般 2026 年 11 月才发布，先留空。
# 结构留着，公布后照着上面格式往里填即可（或在程序设置里手动补）。
HOLIDAYS_2027: list[str] = []
MAKEUP_2027: list[str] = []

BUILTIN: dict[int, tuple[list[str], list[str]]] = {
    2025: (HOLIDAYS_2025, MAKEUP_2025),
    2026: (HOLIDAYS_2026, MAKEUP_2026),
    2027: (HOLIDAYS_2027, MAKEUP_2027),
}

# 年份里带「待补录」标记的，设置页会提示用户
INCOMPLETE_YEARS = {2027}

# 每个假期「有名字的那一天」，用来给整个假期区间命名
# （下面 _build_holiday_names() 会把名字铺满到整个连续区间）
HOLIDAY_NAMES: dict[str, str] = {
    "2025-01-01": "元旦",
    "2025-01-28": "除夕", "2025-01-29": "春节",
    "2025-04-04": "清明",
    "2025-05-01": "劳动节",
    "2025-05-31": "端午",
    "2025-10-01": "国庆", "2025-10-06": "中秋",
    "2026-01-01": "元旦",
    "2026-02-16": "除夕", "2026-02-17": "春节",
    "2026-04-05": "清明",
    "2026-05-01": "劳动节",
    "2026-06-19": "端午",
    "2026-09-25": "中秋",
    "2026-10-01": "国庆",
}


def _build_holiday_names(holiday_dates: list[str]) -> dict[str, str]:
    """给假期的每一天都取个名字（原来只标了第一天）。

    为什么需要：9/25 有名字叫「中秋」，9/26、9/27 是同一个假期的后续天，
    以前会退化成笼统的「法定假日」。用户看到"法定假日"会疑惑是哪个节，
    所以这里把连续区间的所有天都铺上同一个名字。

    相邻但不同节日（如 2025 国庆 10/1-10/5 与中秋 10/6-10/8）会正确分开：
    连续块内部的成员是"连续日期"，而不同节日之间必然有名字冲突点，
    用"从有名字的那天开始，名字不停往后延续，直到出现下一个有名字的天"来切块。
    """
    named = {s: nm for s, nm in HOLIDAY_NAMES.items() if s in set(holiday_dates)}
    ordered = sorted(holiday_dates)
    out: dict[str, str] = {}
    current: str | None = None
    prev: date | None = None
    for s in ordered:
        d = _d(s)
        if s in named:
            current = named[s]           # 遇到新的名字，换一个节日
        elif prev is None or (d - prev).days > 1:
            current = None               # 断开了且没有名字 -> 不硬编
        if current:
            out[s] = current
        prev = d
    # 没被命名的（比如用户自定义假日）保留原样，由调用方兜底
    return out


HOLIDAY_NAMES_FULL: dict[str, str] = {}

# 补班日的说明（鼠标悬停/日志里可以用）
MAKEUP_NOTES: dict[str, str] = {
    "2026-01-04": "元旦调休上班",
    "2026-02-14": "春节调休上班",
    "2026-02-28": "春节调休上班",
    "2026-05-09": "劳动节调休上班",
    "2026-09-20": "国庆调休上班",
    "2026-10-10": "国庆调休上班",
}

HOLIDAY_ALIASES = {"元旦", "春节", "除夕", "清明", "劳动节", "五一",
                   "端午", "中秋", "国庆", "国庆节"}


# ---------------------------------------------------------------------------
# 运行时表（内置 + 用户手动补充）
# ---------------------------------------------------------------------------

def _d(s: str) -> date:
    y, m, dd = (int(x) for x in s.split("-"))
    return date(y, m, dd)


class HolidayCalendar:
    """节假日日历。用户可以在设置里加自己的日期，覆盖内置表。"""

    def __init__(self, extra_holidays: list[str] | None = None,
                 extra_makeup: list[str] | None = None,
                 work_weekend: bool = False):
        self._holidays: dict[date, str] = {}
        self._makeup: set[date] = set()
        self._user_holidays: list[str] = []
        self._user_makeup: list[str] = []
        # 给整个假期区间的每一天都取好名字（不只是第一天），
        # 这样 9/26、9/27 会显示"中秋"而不是笼统的"法定假日"
        all_holiday_dates = [s for hols, _ in BUILTIN.values() for s in hols]
        names_full = _build_holiday_names(all_holiday_dates)
        for year, (hols, makes) in BUILTIN.items():
            for s in hols:
                self._holidays[_d(s)] = (names_full.get(s)
                                        or HOLIDAY_NAMES.get(s)
                                        or "法定假日")
            for s in makes:
                self._makeup.add(_d(s))
        for s in extra_holidays or []:
            try:
                self._holidays[_d(s)] = "自定义假日"
                self._user_holidays.append(s)
            except Exception:
                pass
        for s in extra_makeup or []:
            try:
                self._makeup.add(_d(s))
                self._user_makeup.append(s)
            except Exception:
                pass
        # work_weekend=True 表示周末也要上班（单休/值班的人），此时周末算工作日
        self.work_weekend = work_weekend

    # -- 查询 ---------------------------------------------------------------
    def is_holiday(self, day: date) -> bool:
        """法定节假日（不含普通周末）。"""
        return day in self._holidays

    def holiday_name(self, day: date) -> str:
        return self._holidays.get(day, "")

    def is_makeup_workday(self, day: date) -> bool:
        """调休补班日：本来是周末但要上班。"""
        return day in self._makeup

    def is_weekend(self, day: date) -> bool:
        return day.weekday() >= 5

    def is_workday(self, day: date) -> bool:
        """真正的上班日：法定假日不算；补班日即使周末也算。"""
        if self.is_makeup_workday(day):
            return True
        if self.is_holiday(day):
            return False
        if self.is_weekend(day):
            return self.work_weekend
        return True

    def is_rest_day(self, day: date) -> bool:
        """休息日 = 非上班日。顺延逻辑用它判断。"""
        return not self.is_workday(day)

    def label(self, day: date) -> str:
        """给日历格子用的小角标：休 / 班 / 节日名。"""
        if self.is_holiday(day):
            return self.holiday_name(day) or "休"
        if self.is_makeup_workday(day):
            return "班"
        return ""

    def dump_extra(self) -> dict:
        """导出用户自己加的日期（供设置界面保存）。"""
        return {
            "holidays": sorted(set(self._user_holidays)),
            "makeup": sorted(set(self._user_makeup)),
        }

    def holidays_of_month(self, year: int, month: int) -> dict[date, str]:
        """给日历视图显示节日名用。"""
        return {d: n for d, n in self._holidays.items()
                if d.year == year and d.month == month}


# 全局单例，程序里直接 from app.holidays import CALENDAR 用
CALENDAR = HolidayCalendar()


def reload_calendar(extra_holidays: list[str] | None = None,
                    extra_makeup: list[str] | None = None,
                    work_weekend: bool = False) -> HolidayCalendar:
    """设置页改了节假日之后调一次，替换全局单例。"""
    global CALENDAR
    CALENDAR = HolidayCalendar(extra_holidays, extra_makeup, work_weekend)
    return CALENDAR


def next_workday(day: date, forward: bool = True, max_days: int = 60) -> date:
    """往后（或往前）找最近的一个工作日。"""
    from datetime import timedelta
    step = 1 if forward else -1
    cur = day
    for _ in range(max_days):
        cur = cur + timedelta(days=step)
        if CALENDAR.is_workday(cur):
            return cur
    return day


def year_status(year: int) -> str:
    """给设置页显示：某年的数据是全的还是要用户自己补。"""
    if year in INCOMPLETE_YEARS:
        return "待官方公布"
    if year in BUILTIN:
        return "已内置"
    return "无数据"


if __name__ == "__main__":  # 自测：python -m app.holidays
    for s in ["2025-01-28", "2025-01-26", "2026-02-14", "2026-02-17", "2026-03-05"]:
        dd = _d(s)
        print(s, "workday=", CALENDAR.is_workday(dd), "label=", CALENDAR.label(dd))
