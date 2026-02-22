from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db import get_all_open_tasks, set_last_reminded, get_user_name, get_user_prefs


def minutes_until(deadline_iso: str) -> int:
    dl = datetime.fromisoformat(deadline_iso).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return int((dl - now).total_seconds() // 60)


def format_time_left(deadline_iso: str) -> str:
    now = datetime.now(timezone.utc)
    dl = datetime.fromisoformat(deadline_iso).replace(tzinfo=timezone.utc)
    total_minutes = int((dl - now).total_seconds() // 60)

    if total_minutes <= 0:
        return "срок истёк"

    days = total_minutes // (60 * 24)
    hours = (total_minutes % (60 * 24)) // 60
    minutes = total_minutes % 60

    parts = []
    if days > 0:
        parts.append(f"{days} д.")
    if hours > 0 or days > 0:
        parts.append(f"{hours} ч.")
    parts.append(f"{minutes} мин.")
    return " ".join(parts)


def remind_interval_by_mode(minutes_left: int, mode: str) -> int:
    """
    Возвращает минимальный интервал (в минутах) между напоминаниями
    в зависимости от оставшегося времени и режима.
    """
    # базовые интервалы
    if minutes_left > 24 * 60:
        base = 24 * 60
    elif minutes_left > 6 * 60:
        base = 180
    elif minutes_left > 60:
        base = 60
    else:
        base = 15

    if mode == "gentle":
        return int(base * 2)          # реже
    if mode == "strict":
        return max(5, int(base * 0.5))  # чаще, но не меньше 5 мин
    return base


def should_remind(minutes_left: int, last_reminded_iso: str | None, mode: str) -> bool:
    if minutes_left <= 0:
        return False

    min_interval = remind_interval_by_mode(minutes_left, mode)

    if last_reminded_iso is None:
        return True

    last = datetime.fromisoformat(last_reminded_iso).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    passed = int((now - last).total_seconds() // 60)
    return passed >= min_interval


def motivation(minutes_left: int, title: str) -> str:
    if minutes_left <= 60:
        return "Остался примерно час. Начните с маленького шага на 10 минут."
    if minutes_left <= 6 * 60:
        return f"Сделайте один небольшой шаг по задаче «{title}» — это уже прогресс."
    return "Небольшой прогресс сегодня — большой результат завтра."


async def reminder_job(bot):
    tasks = await get_all_open_tasks()
    now_iso = datetime.now(timezone.utc).isoformat()

    for (task_id, user_id, title, deadline, pr, imp, diff, st, last_reminded_at) in tasks:
        name = await get_user_name(user_id) or "друг"
        tz_offset_min, mode = await get_user_prefs(user_id)

        m_left = minutes_until(deadline)
        if not should_remind(m_left, last_reminded_at, mode):
            continue

        # красиво покажем “Осталось…”
        left_str = format_time_left(deadline)

        text = (
            f"⏰ {name}, напоминание о дедлайне\n\n"
            f"Задача: {title}\n"
            f"Осталось: {left_str}\n\n"
            f"✨ {motivation(m_left, title)}"
        )

        await bot.send_message(user_id, text)
        await set_last_reminded(task_id, now_iso)


def setup_scheduler(bot):
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(reminder_job, "interval", minutes=1, args=[bot])
    scheduler.start()
    return scheduler