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
from .recurrence import compute_next_at, next_occurrence

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
        self.timer.start()
        if catch_up:
            # 启动时先补一次错过的
            QTimer.singleShot(1200, lambda: self.tick(force_catchup=True))

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
        for t in self.db.tasks_with_next_at():
            if t.id in self._pending or t.id in self._snoozes:
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
        """跨天：清空"已弹过"标记，把昨天的任务重新排到今天。"""
        self._pending.clear()
        self._snoozes.clear()
        today = now.date().isoformat()
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
        """把任务的下次提醒时间推到未来（今天之后）并写库。"""
        # 优先用当天剩下的追加时间点（一天多提醒）
        if not t.done and t.enabled:
            try:
                nxt_extra = self.next_extra_slot(t, now)
            except Exception:
                nxt_extra = None
            if nxt_extra and nxt_extra > now:
                val = nxt_extra.strftime("%Y-%m-%d %H:%M")
                self.db.update_fields(t.id, next_at=val)
                t.next_at = val
                return

        # 否则找「明天及以后」的下一次
        res = next_occurrence(t, after=now.date(), include_today=False)
        nxt = None
        if res:
            actual, _ = res
            tt = t.first_time
            nxt = f"{actual.isoformat()} {tt.hour:02d}:{tt.minute:02d}"
        self.db.update_fields(t.id, next_at=nxt)
        t.next_at = nxt
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
        fresh = self.db.get_task(t.id)
        if fresh:
            if fresh.recur == cfg.RECUR_NONE:
                self.db.update_fields(t.id, enabled=0, next_at=None)
            else:
                self._refresh_task_schedule(fresh)
        self.refreshed.emit()

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
