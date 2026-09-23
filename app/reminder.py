"""★ 提醒调度器。

职责：
  1. 定时（每 10 秒）扫描数据库，找出「到点了还没提醒」的任务，发出信号。
  2. 处理「稍后提醒」。
  3. 处理「一天多个时间点」：第一个时间点提醒完，自动把下次时间改成当天后面的时间点。
  4. 处理「休眠/关机错过的提醒」：启动时和每次唤醒后补提醒（标 补 字）。
  5. 写 fire_log，方便排查"为什么没提醒我"。

它不直接画界面 —— 只管发信号，界面（MainWindow）接住去弹窗/发系统通知。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QObject, QTimer, Signal

from . import config as cfg
from .database import Database
from .holidays import CALENDAR
from .models import Task, combine
from .recurrence import compute_next_at, next_occurrence, occurs_on

TICK_MS = 10_000            # 10 秒扫一次，足够准且几乎不耗电
CATCHUP_HOURS = 24          # 错过多久以内还补提醒
SLEEP_GAP_SECONDS = 90      # 两次 tick 间隔超过这么久，认为刚从休眠中醒来


class SnoozeState:
    """稍后提醒的临时记录（不写库，重启后就没了，这是合理的）。"""

    __slots__ = ("task_id", "until", "planned_at", "extra_index")

    def __init__(self, task_id: int, until: datetime, planned_at: str | None,
                 extra_index: int = -1):
        self.task_id = task_id
        self.until = until
        self.planned_at = planned_at
        self.extra_index = extra_index


class ReminderScheduler(QObject):
    """发信号给界面；界面决定怎么弹。"""

    # 该提醒了：task, 触发时间(datetime), 是不是补提醒
    due = Signal(object, object, bool)
    # 需要界面刷新（任务时间被调度器改了、或跨天了）
    refreshed = Signal()
    # 日期变了（跨天），界面要重算"今天"
    day_changed = Signal(object)

    def __init__(self, db: Database, settings, parent=None):
        super().__init__(parent)
        self.db = db
        self.settings = settings
        self._snoozes: dict[int, SnoozeState] = {}
        self._pending: set[int] = set()      # 已经弹过、等着用户处理的，别重复弹
        self._last_tick: datetime | None = None
        self._last_day: date = date.today()
        self._enabled = True

        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self.tick)

    # ------------------------------------------------------------------ 生命周期
    def start(self, catch_up: bool = True) -> None:
        # 先修历史数据：老版本有过一个 bug —— 重复任务标记完成后没有复位
        # done 标志，导致任务永远卡在「已完成」、第二天再也不提醒。
        # 这里在启动时把这类任务捞回来（详见 repair_stuck_completed）。
        self.repair_stuck_completed()
        self.timer.start()
        if catch_up:
            # 启动时先补一次错过的
            QTimer.singleShot(1200, lambda: self.tick(force_catchup=True))

    def repair_stuck_completed(self) -> int:
        """把「卡住的重复任务」修回可提醒状态。

        什么是卡住：下次提醒时间停在过去，任务既不会响、也不会自己恢复。
        典型来源是更早版本的两个 bug：
          * 完成后没有把 next_at 推到下一轮（弹过就一直停在过期时间）；
          * 完成后把 done 抹成 0 与 done_date 打架（已完成筛选看不到）。

        判定条件（满足其一）：
          A. 下次提醒时间早于上次弹窗时间 —— 说明完成时的重排没生效
          B. 完成日期在以前、且下次时间也在过去 —— 老数据遗留

        修法：把 next_at 排到未来。
        **不再动 done / done_date** —— 它们是完成事实的记录，
        调度器用「done=1 且完成日期是过去」自然恢复（见 Database.active_tasks）。
        返回修好的条数。
        """
        today = date.today().isoformat()
        rows = self.db.conn.execute(
            "SELECT * FROM tasks WHERE done=1 AND enabled=1 AND recur != ? "
            "AND ("
            # A：下次时间早于上次弹窗 -> 重排失败（含今天刚点完成的）
            "  (last_fired_at IS NOT NULL AND next_at IS NOT NULL "
            "   AND next_at < substr(last_fired_at,1,16))"
            "  OR "
            # B：老数据，完成日期在以前且下次时间也在过去
            "  (next_at IS NOT NULL AND substr(next_at,1,10) < ? "
            "   AND (done_date IS NULL OR done_date < ?))"
            ")",
            (cfg.RECUR_NONE, today, today)).fetchall()
        fixed = 0
        for row in rows:
            t = Task.from_row(row)
            try:
                res = next_occurrence(t, after=date.today(), include_today=True)
            except Exception as e:
                # 规则异常读不出来就跳过这条，但留日志 —— 不然用户会觉得
                # "这条任务莫名不提醒了"，却没有线索。
                cfg.log_problem("repair_stuck_completed 算下次时间失败", e,
                                f"task_id={t.id} recur={t.recur} "
                                f"params={t.recur_params}")
                continue
            nxt = None
            if res:
                actual, _ = res
                tt = t.first_time
                nxt = f"{actual.isoformat()} {tt.hour:02d}:{tt.minute:02d}"
            self.db.update_fields(t.id, next_at=nxt,
                                  snooze_count=0, snooze_total_min=0)
            self.db.log_fire(t.id, t.next_at, "repair", f"stuck -> {nxt}")
            fixed += 1
        if fixed:
            self.db.conn.commit()
        return fixed

    def stop(self) -> None:
        self.timer.stop()

    def set_enabled(self, on: bool) -> None:
        self._enabled = on
        if on:
            self.timer.start()
        else:
            self.timer.stop()

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _now() -> datetime:
        return datetime.now().replace(microsecond=0)

    def _refresh_task_schedule(self, t: Task, now: datetime | None = None) -> None:
        """重算任务的 next_at 并写库。"""
        now = now or self._now()
        nxt = compute_next_at(t, now)
        if t.next_at != nxt:
            t.next_at = nxt
            self.db.update_fields(t.id, next_at=nxt)

    def reschedule_all(self) -> None:
        """设置变了（比如节假日策略、单双周基准）以后，全量重算。"""
        for t in self.db.active_tasks():
            self._refresh_task_schedule(t)
        self.refreshed.emit()

    def planned_date_of(self, t: Task) -> date | None:
        """这个任务下一次的「原计划日」（用于统计和显示"因假期顺延"）。"""
        res = next_occurrence(t, after=date.today())
        if not res:
            return None
        _actual, planned = res
        return planned or _actual

    # ------------------------------------------------------------------ 主循环
    def tick(self, force_catchup: bool = False) -> None:
        if not self._enabled:
            return
        now = self._now()

        # 1) 跨天检测
        if now.date() != self._last_day:
            self._last_day = now.date()
            self._on_new_day(now)
            self.day_changed.emit(now.date())

        # 2) 刚睡醒？（tick 间隔异常大 -> 说明系统休眠过）
        woke_up = False
        if self._last_tick is not None:
            gap = (now - self._last_tick).total_seconds()
            if gap > SLEEP_GAP_SECONDS:
                woke_up = True
        self._last_tick = now

        catchup = force_catchup or woke_up

        # 3) 稍后提醒到期
        for tid in list(self._snoozes):
            st = self._snoozes[tid]
            if now >= st.until:
                t = self.db.get_task(tid)
                del self._snoozes[tid]
                if t and not t.done and t.enabled:
                    self._pending.discard(tid)
                    self.due.emit(t, now, False)

        # 4) 正常到点
        today_iso = now.date().isoformat()
        # 今天已经为哪些任务弹过（从 fire_log 查，作为"同一天不重复打扰"的兜底）。
        # 为什么需要：_pending 只在内存里，程序重启或用户取消完成等操作会让它清空，
        # 之后如果 next_at 又落到过去，调度器就会把同一条任务再弹一次。
        fired_today: set[int] = set()
        try:
            for r in self.db.conn.execute(
                    "SELECT DISTINCT task_id FROM fire_log "
                    "WHERE kind IN ('popup','catchup') AND substr(fired_at,1,10)=? "
                    "AND task_id IS NOT NULL", (today_iso,)):
                fired_today.add(r["task_id"])
        except Exception as e:
            cfg.log_problem("查询今日已提醒记录失败", e)

        for t in self.db.tasks_with_next_at():
            if t.id in self._pending or t.id in self._snoozes:
                continue
            # 今天已经做过的就别再提醒了。
            # 注：active/ tasks_with_next_at 会把"完成日期在过去"的任务也返回
            # （那是为了新的一天能恢复提醒），所以这里要显式挡掉今天已完成的。
            if t.done and t.done_date == today_iso:
                continue
            # 同一个任务今天已经提醒过 -> 不再重复弹。
            # _pending 只活在内存里，程序重启、用户取消完成等操作会清掉它；
            # 那时若 next_at 又落到过去，这条任务就会被再弹一次。
            # 用 fire_log 里的当日记录兜底，保证"一天最多打扰一次"。
            if t.id in fired_today:
                continue
            ndt = t.next_dt
            if ndt is None:
                continue
            lead = timedelta(minutes=max(0, int(t.lead_minutes or 0)))
            fire_at = ndt - lead
            if now < fire_at:
                continue

            overdue = now - fire_at
            if overdue > timedelta(hours=CATCHUP_HOURS):
                # 太久远了（超过一天），直接跳到下一个周期，避免开机弹一堆
                self._advance(t, now)
                continue

            # ★ 刚开机/重启时，不为"已经过去很久"的提醒补弹窗。
            #   场景：晚上 23:00 关机，有条当天没点完成的提醒；第二天开机时
            #   它已经过去十几个小时，再弹窗已时过境迁，而且开机常会积压好几条，
            #   会连续刷屏。处理方式：把下次提醒排到未来（界面里仍显示「未完成」，
            #   到点照常提醒），只是不为它单独弹一次。
            #   阈值取 1 小时（cfg.STARTUP_SILENT_MINUTES）：这样关机一晚会被静默，
            #   但程序崩溃重开、临时关机十几分钟的情况仍会补提醒。
            #   注意只在"启动/重启后"这样做：电脑一直开着时到点照弹，
            #   休眠几分钟后唤醒也照弹（那种 overdue 很小，到不了阈值）。
            if force_catchup and overdue > timedelta(
                    minutes=cfg.STARTUP_SILENT_MINUTES):
                cfg.log_problem(
                    "启动时跳过过期提醒（不弹窗，保持未完成）",
                    None,
                    f"task_id={t.id} 标题={t.title!r} 原定={t.next_at} "
                    f"已过去={overdue}")
                self.db.log_fire(t.id, t.next_at, "advance", "启动时跳过过期提醒")
                self._advance(t, now)
                continue

            if overdue > timedelta(minutes=cfg.MISSED_GRACE_MINUTES):
                # 错过了一段时间：补提醒
                self._fire(t, now, catchup=True)
            else:
                self._fire(t, now, catchup=False)

        # 5) 顺手修掉 next_at 是空的任务（比如一次性任务过期了）
        for t in self.db.active_tasks():
            if not t.next_at:
                nxt = compute_next_at(t, now)
                if nxt:
                    self.db.update_fields(t.id, next_at=nxt)

        if woke_up or catchup:
            self.refreshed.emit()

    # ------------------------------------------------------------------ 内部
    def _on_new_day(self, now: datetime) -> None:
        """跨天：清空"已弹过"标记，把停在昨天的任务重排到今天。"""
        self._pending.clear()
        self._snoozes.clear()
        today = now.date().isoformat()

        # 注意：**不要**在这里把 done 抹成 0。
        # done=1 + done_date=昨天 已经能表达"上一轮做完了"，而
        # Database.active_tasks() 会把这种任务视为可提醒（新的一天恢复了）。
        # 抹成 0 反而会让 done 和 done_date 打架，导致
        # 「已完成」筛选与「已完成」分组给出相反结论。

        for t in self.db.active_tasks():
            nxt = t.next_at or ""
            # 已经是今天或更晚的，不动；停在昨天的（昨天没做完的）重排到今天
            if nxt[:10] >= today:
                continue
            self.db.update_fields(t.id, deferred_from=None)
            t.deferred_from = None
            if t.recur == cfg.RECUR_NONE:
                # 一次性的，昨天错过就算了，别再冒出来
                self.db.update_fields(t.id, next_at=None)
                continue
            res = next_occurrence(t, after=now.date(), include_today=True)
            if res:
                actual, _ = res
                tt = t.first_time
                val = f"{actual.isoformat()} {tt.hour:02d}:{tt.minute:02d}"
                self.db.update_fields(t.id, next_at=val)
                t.next_at = val
        self.refreshed.emit()

    def _fire(self, t: Task, now: datetime, catchup: bool) -> None:
        self._pending.add(t.id)
        self.db.update_fields(t.id, last_fired_at=now.strftime("%Y-%m-%d %H:%M:%S"))
        self.db.log_fire(t.id, t.next_at, "catchup" if catchup else "popup", "shown")
        # ★ 关键：弹过之后必须把 next_at 推到下一次，否则这条任务会一直停在
        #   "已过期" 状态，同一天里被反复提醒（之前就是这个 bug）。
        #   注意：如果用户没点完成就忽略弹窗，任务会留在"今日逾期"里，
        #   当天不再打扰，明天由 _on_new_day 重新排到当天时间。
        self._advance(t, now)
        self.due.emit(t, now, catchup)

    def _advance(self, t: Task, now: datetime) -> None:
        """把任务的下次提醒时间推到未来并写库（允许排到今天剩下的时间点）。

        用于两类场景：提醒弹过之后重排、启动时跳过过期提醒。
        两者都必须"允许今天"—— 否则「每天 21:17」这种任务在早上触发时
        会被直接推到明天，当天就再也不提醒了（这正是之前的 bug）。
        """
        if t.done:
            # 已完成的任务不该再排回今天，交给 _reschedule_after_done 处理
            self._reschedule_after_done(t, now, allow_today=False)
            self.refreshed.emit()
            return

        self._reschedule_after_done(t, now, allow_today=True)
        self.refreshed.emit()

    # ------------------------------------------------------------------ 外部动作
    def complete(self, t: Task, planned: date | None = None) -> None:
        """用户点了「完成」。"""
        self._pending.discard(t.id)
        self._snoozes.pop(t.id, None)
        today = date.today()
        self.db.mark_done(t, day=today, planned=planned or today,
                          was_deferred=self._is_deferred_now(t))
        self.db.log_fire(t.id, t.next_at, "popup", "done")

        if t.recur == cfg.RECUR_NONE:
            # 一次性的：完成即停用
            self.db.update_fields(t.id, enabled=0, next_at=None)
        else:
            # ★ 关键：重复任务完成后必须做两件事，少一件第二天就不会再提醒：
            #   1) 把 next_at 推到「下一轮」—— 不能用 compute_next_at()，
            #      它为了支持"错过补提醒"会把今天没到点的时间算回来，
            #      于是任务会重新落到今天早上，等于没排下次；
            #   2) **不要**把 done 抹成 0。done 是"本轮做完了"的真实记录，
            #      调度器靠「done=1 且完成日期是过去」判断"新的一天该恢复了"
            #      （见 Database.active_tasks）。抹成 0 会让 done 与 done_date
            #      打架：任务显示在「已完成」组里，点「已完成」筛选却是空的。
            #   allow_today=False：今天已经做完了，不该再排回今天。
            self._reschedule_after_done(t, now=self._now(), allow_today=False)
        self.refreshed.emit()

    def unmark_done_reschedule(self, t: Task) -> None:
        """用户取消了「完成」：任务留在今天显示为未完成，但**不要**因此补弹提醒。

        两个要求同时满足：
          1. 它今天该做，所以必须还出现在「今天」列表里 —— 不能把 next_at
             推到明天（那样用户会以为任务消失了）。所以这里刻意保留原定的
             时间点，即使它已经过去（列表会把它显示为逾期）。
          2. 取消勾选是"状态修正"操作，不该被当成"错过了提醒"而补弹。
             靠两点保证：标记 _pending，以及 tick 里"同一天最多打扰一次"
             （fire_log 有当日记录就不再弹）。

        注意：本方法**自己负责清库**（done / done_date / completed_at 和
        completion_log 记录），不要依赖调用方先调 unmark_done —— 依赖调用顺序
        是隐患，测试里就因此暴露过一次不一致。
        """
        self.db.unmark_done(t)
        self.db.update_fields(t.id, enabled=1)
        # 保留"今天该做"的事实：把下次提醒放回今天（时分沿用原设定）
        planned = t.next_at
        if not planned or planned[:10] != date.today().isoformat():
            tt = t.first_time
            planned = f"{date.today().isoformat()} {tt.hour:02d}:{tt.minute:02d}"
        self.db.update_fields(t.id, next_at=planned)
        t.next_at = planned
        t.done = False
        t.done_date = None
        t.completed_at = None
        self._pending.add(t.id)      # 别让这次重排被当成新到点
        self.refreshed.emit()

    def _reschedule_after_done(self, t: Task, now: datetime | None = None,
                               allow_today: bool = False) -> None:
        """确定任务的「下一次提醒时间」并写库。

        allow_today=True：允许排到今天（只要今天该提醒、且今天的某个时间点还没到）。
        allow_today=False：严格排到明天及以后。

        两种调用场景不一样，这个区分很重要：
          * 用户点「完成」→ allow_today=False。今天已经做完了，不该再排回今天。
          * 启动/跳过期提醒时重排 → allow_today=True。因为启动时可能只是
            "错过了早上的那次"，而当天晚上的时间点还没到 —— 不能把今天
            整个跳过。之前一律用"从明天开始找"，导致像「每天 21:17」这种
            任务在早上开机时被直接推到明天，当天就再也不提醒了。
        """
        now = now or self._now()
        today = now.date()
        nxt = None

        if allow_today:
            # 先看今天是不是该提醒、以及今天还有没有没到的时间点
            try:
                if occurs_on(t, today):
                    remaining = [tt for tt in t.times_as_time()
                                 if datetime.combine(today, tt) > now]
                    if remaining:
                        nxt = datetime.combine(today, remaining[0]).strftime(
                            "%Y-%m-%d %H:%M")
            except Exception as e:
                cfg.log_problem("_reschedule_after_done 判断今天是否可排失败", e,
                                f"task_id={t.id} recur={t.recur}")

        if nxt is None:
            try:
                res = next_occurrence(t, after=today, include_today=False)
            except Exception as e:
                cfg.log_problem("_reschedule_after_done 算下次时间失败", e,
                                f"task_id={t.id} recur={t.recur}")
                res = None
            if res:
                actual, _ = res
                tt = t.first_time
                nxt = f"{actual.isoformat()} {tt.hour:02d}:{tt.minute:02d}"

        # 只改下次时间和稍后计数；done / done_date 保持不动（它们是完成事实的记录）
        self.db.update_fields(t.id, next_at=nxt,
                              snooze_count=0, snooze_total_min=0)
        t.next_at = nxt
        t.snooze_count = 0
        t.snooze_total_min = 0

    def snooze(self, t: Task, minutes: int | None = None) -> None:
        """稍后提醒。"""
        minutes = int(minutes or self.settings.get("snooze_minutes", 10))
        now = self._now()
        until = now + timedelta(minutes=minutes)
        self._pending.discard(t.id)
        self._snoozes[t.id] = SnoozeState(t.id, until, t.next_at)
        self.db.update_fields(
            t.id,
            snooze_count=(t.snooze_count or 0) + 1,
            snooze_total_min=(t.snooze_total_min or 0) + minutes)
        self.db.log_fire(t.id, t.next_at, "snooze", f"+{minutes}min")
        self.refreshed.emit()

    def skip_once(self, t: Task) -> None:
        """跳过这一次（只跳过今天，规则不变）。"""
        self._pending.discard(t.id)
        self._snoozes.pop(t.id, None)
        today = date.today().isoformat()
        skips = list(t.skip_dates or [])
        if today not in skips:
            skips.append(today)
        self.db.update_fields(t.id, skip_dates=Task.dumps(skips))
        self.db.log_fire(t.id, t.next_at, "popup", "skipped")
        t.skip_dates = skips
        self._refresh_task_schedule(t)
        self.refreshed.emit()

    def dismiss(self, t: Task, result: str = "auto_closed") -> None:
        """弹窗自动关掉/用户关掉，但任务没完成——下次 tick 会再提醒（因为 next_at 还在过去）。"""
        self._pending.discard(t.id)
        self.db.log_fire(t.id, t.next_at, "popup", result)

    def mark_pending_done_externally(self, task_id: int) -> None:
        """别处（比如主界面勾选）完成了任务，调度器同步一下内部状态。"""
        self._pending.discard(task_id)
        self._snoozes.pop(task_id, None)

    def _is_deferred_now(self, t: Task) -> bool:
        try:
            res = next_occurrence(t, after=date.today() - timedelta(days=cfg.DEFER_MAX_DAYS))
            if not res:
                return False
            actual, planned = res
            return planned is not None and actual != planned
        except Exception:
            return False

    # ------------------------------------------------------------------ 一天多提醒
    def extra_times_after(self, t: Task, moment: datetime) -> list[time]:
        """当天在 moment 之后还有哪些提醒时间点。"""
        out = []
        for tt in t.times_as_time()[1:]:       # 第 0 个是主时间
            if datetime.combine(moment.date(), tt) > moment:
                out.append(tt)
        return out

    def next_extra_slot(self, t: Task, moment: datetime) -> datetime | None:
        ex = self.extra_times_after(t, moment)
        return datetime.combine(moment.date(), ex[0]) if ex else None

    def dump_state(self) -> dict:
        return {
            "pending": sorted(self._pending),
            "snoozes": {k: v.until.strftime("%H:%M") for k, v in self._snoozes.items()},
            "last_tick": self._last_tick.isoformat() if self._last_tick else None,
        }


# ---------------------------------------------------------------------------
# 柔和提示音：用标准库合成一段 WAV 写到临时目录，再用 Qt 播放，不依赖素材
# ---------------------------------------------------------------------------

def ensure_chime(path=None) -> str:
    """合成一个"叮——咚"两声的柔和提示音（正弦 + 指数衰减），返回 wav 路径。"""
    import base64
    import io
    import struct
    import wave
    from pathlib import Path

    path = Path(path or (cfg.DATA_DIR / "chime.wav"))
    if path.exists():
        return str(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rate = 44100

        def tone(freq: float, dur: float, amp: float) -> list[int]:
            n = int(rate * dur)
            out = []
            for i in range(n):
                t = i / rate
                env = pow(2.718281828, -6.0 * t / dur)      # 指数衰减，柔和
                # 加一点泛音，听起来更像风铃而不是电子音
                v = (0.75 * _sin(2 * 3.141592653589793 * freq * t)
                     + 0.25 * _sin(2 * 3.141592653589793 * freq * 2.01 * t))
                out.append(int(max(-1.0, min(1.0, v * env * amp)) * 32767))
            return out

        samples = [0] * int(rate * 0.05)
        samples += tone(880.0, 0.55, 0.55)          # 第一声 高一点
        samples += [0] * int(rate * 0.10)
        samples += tone(659.25, 0.75, 0.42)         # 第二声 低一点，更柔
        samples += [0] * int(rate * 0.15)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(struct.pack("<h", s) for s in samples))
        path.write_bytes(buf.getvalue())
        return str(path)
    except Exception:
        return ""


def _sin(x: float) -> float:
    """不 import math 也行，但用 math 更准（这里是为了函数内联清晰）。"""
    import math
    return math.sin(x)


# ---------------------------------------------------------------------------
# 播放：Windows 的 winsound 是标准库，异步播放不卡界面，也不需要 QtMultimedia
# ---------------------------------------------------------------------------

def play_chime(volume_note: str = "") -> bool:
    """播放合成好的提示音。"""
    path = ensure_chime()
    if not path:
        return False
    try:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC |
                           winsound.SND_NODEFAULT)
        return True
    except Exception:
        pass
    # 非 Windows 或 winsound 不可用时，退回 Qt 的 beep
    try:
        from PySide6.QtWidgets import QApplication
        QApplication.beep()
        return True
    except Exception:
        return False


def play_system_alert() -> None:
    """系统提示音（最保底的方式，一定响）。"""
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass
