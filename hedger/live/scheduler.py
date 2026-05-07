"""Live scheduler.

Wraps APScheduler with sensible defaults: a tick every N minutes during
trading hours, plus a 22:00-CET nightly trigger for the reflection loop.
"""

from __future__ import annotations

from typing import Callable

from hedger.config import Config
from hedger.live.runner import Runner, make_runner
from hedger.util import get_logger

log = get_logger("hedger.scheduler")


def run_scheduler(
    cfg: Config | None = None,
    *,
    runner: Runner | None = None,
    on_reflect: Callable[[], None] | None = None,
) -> None:
    """Block forever, ticking on cadence, calling `on_reflect` at the cron time."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    cfg = cfg or Config()
    runner = runner or make_runner(cfg)

    sched = BlockingScheduler(timezone=cfg.reflection.timezone)
    interval = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 60 * 24}.get(cfg.timeframe, 60)
    sched.add_job(
        runner.tick,
        IntervalTrigger(minutes=interval),
        name="tick",
        coalesce=True,
        max_instances=1,
    )
    if cfg.reflection.enabled and on_reflect:
        sched.add_job(
            on_reflect,
            CronTrigger.from_crontab(cfg.reflection.cron),
            name="reflect",
            max_instances=1,
        )
    log.info("scheduler_starting", cadence_minutes=interval, reflect_cron=cfg.reflection.cron)
    sched.start()


__all__ = ["run_scheduler"]
