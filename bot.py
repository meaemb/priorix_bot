import os
import re
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery
)
from dotenv import load_dotenv

from db import (
    init_db, add_task, list_tasks, update_status, delete_task,
    get_user_name, set_user_name,
    get_user_prefs, set_user_timezone, set_user_reminder_mode
)
from scheduler import setup_scheduler

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить задачу"), KeyboardButton(text="📋 Мои задачи")],
        [KeyboardButton(text="✅ Статус"), KeyboardButton(text="🗑️ Удалить")],
        [KeyboardButton(text="🔽 Сортировка"), KeyboardButton(text="📅 Сегодня")],
        [KeyboardButton(text="⚙️ Настройки")],
    ],
    resize_keyboard=True
)

STATUS_MAP = {
    "todo": "К выполнению",
    "doing": "В процессе",
    "done": "Выполнено"
}

def stars(n: int) -> str:
    try:
        n = int(n)
    except:
        n = 0
    return "⭐" * max(0, min(5, n))

def parse_deadline_utc(s: str) -> str:
    if not DATE_RE.match(s):
        raise ValueError("bad_format")
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=timezone.utc).isoformat()

def format_time_left(deadline_iso: str) -> str:
    now = datetime.now(timezone.utc)
    dl = datetime.fromisoformat(deadline_iso).replace(tzinfo=timezone.utc)
    total_minutes = int((dl - now).total_seconds() // 60)

    if total_minutes <= 0:
        return "Осталось: срок истёк"

    days = total_minutes // (60 * 24)
    hours = (total_minutes % (60 * 24)) // 60
    minutes = total_minutes % 60

    parts = []
    if days > 0:
        parts.append(f"{days} д.")
    if hours > 0 or days > 0:
        parts.append(f"{hours} ч.")
    parts.append(f"{minutes} мин.")
    return "Осталось: " + " ".join(parts)

def is_today_utc(deadline_iso: str) -> bool:
    dl = datetime.fromisoformat(deadline_iso).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return dl.date() == now.date()

def render_tasks(rows, title="📋Ваши задачи:\n") -> str:
    if not rows:
        return "У вас пока нет задач."

    lines = [title]
    for (tid, task_title, deadline, pr, imp, diff, st) in rows:
        dl_short = deadline[:16].replace("T", " ")
        time_left = format_time_left(deadline)

        lines.append(
            f"🔹 {task_title}\n"
            f"Срок: {dl_short}\n"
            f"{time_left}\n"
            f"Срочность: {stars(pr)}\n"
            f"Важность: {stars(imp)}\n"
            f"Сложность: {stars(diff)}\n"
            f"Статус: {STATUS_MAP.get(st, st)}\n"
            f"──────────────"
        )
    return "\n".join(lines)

dp = Dispatcher()
user_states = {}

# -------------------- SETTINGS UI --------------------

def settings_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить имя", callback_data="set:name")],
        [InlineKeyboardButton(text="🕒 Часовой пояс", callback_data="set:tz")],
        [InlineKeyboardButton(text="🔔 Напоминания", callback_data="set:rem")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="set:back")],
    ])

def tz_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="UTC (0)", callback_data="tz:0")],
        [InlineKeyboardButton(text="UTC+3", callback_data="tz:180")],
        [InlineKeyboardButton(text="UTC+5", callback_data="tz:300")],
        [InlineKeyboardButton(text="UTC+6", callback_data="tz:360")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="set:settings")],
    ])

def rem_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мягкий режим", callback_data="rem:gentle")],
        [InlineKeyboardButton(text="Стандартный", callback_data="rem:normal")],
        [InlineKeyboardButton(text="Строгий", callback_data="rem:strict")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="set:settings")],
    ])

# -------------------- START --------------------

@dp.message(Command("start"))
async def start(m: Message):
    uid = m.from_user.id
    name = await get_user_name(uid)

    if not name:
        user_states[uid] = {"step": "ask_name"}
        await m.answer(
            "Добро пожаловать в Priorix.\n\n"
            "Пожалуйста, напишите ваше имя."
        )
        return

    await m.answer(f"С возвращением, {name}!", reply_markup=menu_kb)

# -------------------- SETTINGS --------------------

@dp.message(Command("settings"))
@dp.message(F.text == "⚙️ Настройки")
async def settings_cmd(m: Message):
    uid = m.from_user.id
    name = await get_user_name(uid) or "друг"
    tz_offset_min, mode = await get_user_prefs(uid)

    mode_human = {"gentle": "Мягкий", "normal": "Стандартный", "strict": "Строгий"}.get(mode, "Стандартный")
    tz_h = f"UTC{'' if tz_offset_min == 0 else ('+' if tz_offset_min > 0 else '-')}{abs(tz_offset_min)//60}"

    await m.answer(
        f"⚙️ Настройки\n\n"
        f"Имя: {name}\n"
        f"Часовой пояс: {tz_h}\n"
        f"Напоминания: {mode_human}\n\n"
        f"Выберите, что изменить:",
        reply_markup=settings_kb()
    )

@dp.callback_query(F.data == "set:settings")
async def back_to_settings(cb: CallbackQuery):
    await settings_cmd(cb.message)
    await cb.answer()

@dp.callback_query(F.data == "set:back")
async def settings_back(cb: CallbackQuery):
    await cb.message.edit_text("Готово. Вы можете продолжить работу через меню.")
    await cb.answer()

@dp.callback_query(F.data == "set:name")
async def settings_name(cb: CallbackQuery):
    uid = cb.from_user.id
    user_states[uid] = {"step": "change_name"}
    await cb.message.edit_text("Введите новое имя (2–30 символов).")
    await cb.answer()

@dp.callback_query(F.data == "set:tz")
async def settings_tz(cb: CallbackQuery):
    await cb.message.edit_text("Выберите часовой пояс:", reply_markup=tz_kb())
    await cb.answer()

@dp.callback_query(F.data.startswith("tz:"))
async def tz_set(cb: CallbackQuery):
    uid = cb.from_user.id
    tz_offset_min = int(cb.data.split(":")[1])
    await set_user_timezone(uid, tz_offset_min)
    await cb.message.edit_text("Часовой пояс сохранён ✅", reply_markup=settings_kb())
    await cb.answer()

@dp.callback_query(F.data == "set:rem")
async def settings_rem(cb: CallbackQuery):
    await cb.message.edit_text("Выберите режим напоминаний:", reply_markup=rem_kb())
    await cb.answer()

@dp.callback_query(F.data.startswith("rem:"))
async def rem_set(cb: CallbackQuery):
    uid = cb.from_user.id
    mode = cb.data.split(":")[1]
    await set_user_reminder_mode(uid, mode)
    await cb.message.edit_text("Режим напоминаний сохранён ✅", reply_markup=settings_kb())
    await cb.answer()

# -------------------- ADD TASK --------------------

@dp.message(Command("add"))
async def add_cmd(m: Message):
    uid = m.from_user.id
    name = await get_user_name(uid) or "друг"
    user_states[uid] = {"step": "title"}
    await m.answer(
        f"{name}, укажите название задачи.\n"
        "Пример: Подготовить презентацию",
        reply_markup=menu_kb
    )

@dp.message(F.text == "➕ Добавить задачу")
async def add_btn(m: Message):
    await add_cmd(m)

# -------------------- TASK LIST --------------------

@dp.message(Command("tasks"))
async def tasks_cmd(m: Message):
    uid = m.from_user.id
    rows = await list_tasks(uid, order_by="deadline")
    await m.answer(render_tasks(rows), reply_markup=menu_kb)

@dp.message(F.text == "📋 Мои задачи")
async def tasks_btn(m: Message):
    await tasks_cmd(m)

# -------------------- TODAY --------------------

@dp.message(Command("today"))
async def today_cmd(m: Message):
    uid = m.from_user.id
    rows = await list_tasks(uid, order_by="deadline")
    today_rows = [r for r in rows if is_today_utc(r[2])]
    await m.answer(render_tasks(today_rows, title="📅Задачи на сегодня:\n"), reply_markup=menu_kb)

@dp.message(F.text == "📅 Сегодня")
async def today_btn(m: Message):
    await today_cmd(m)

# -------------------- STATUS (INLINE) --------------------

@dp.message(F.text == "✅ Статус")
@dp.message(Command("status"))
async def choose_task_for_status(m: Message):
    uid = m.from_user.id
    rows = await list_tasks(uid, order_by="deadline")
    if not rows:
        await m.answer("У вас пока нет задач.", reply_markup=menu_kb)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{title} ({STATUS_MAP.get(st, st)})",
            callback_data=f"status_task:{tid}"
        )]
        for (tid, title, deadline, pr, imp, diff, st) in rows
    ])

    await m.answer("Выберите задачу:", reply_markup=kb)

@dp.callback_query(F.data.startswith("status_task:"))
async def choose_new_status(cb: CallbackQuery):
    task_id = int(cb.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="К выполнению", callback_data=f"set_status:{task_id}:todo")],
        [InlineKeyboardButton(text="В процессе", callback_data=f"set_status:{task_id}:doing")],
        [InlineKeyboardButton(text="Выполнено", callback_data=f"set_status:{task_id}:done")],
    ])
    await cb.message.edit_text("Выберите новый статус:", reply_markup=kb)
    await cb.answer()

@dp.callback_query(F.data.startswith("set_status:"))
async def set_status_callback(cb: CallbackQuery):
    uid = cb.from_user.id
    _, task_id_s, new_status = cb.data.split(":")
    task_id = int(task_id_s)

    await update_status(uid, task_id, new_status)
    name = await get_user_name(uid) or "друг"
    await cb.message.edit_text(
        f"{name}, статус обновлён.\n"
        f"Теперь: {STATUS_MAP.get(new_status, new_status)}"
    )
    await cb.answer()

# -------------------- DELETE (INLINE) --------------------

@dp.message(F.text == "🗑️ Удалить")
@dp.message(Command("delete"))
async def delete_choose_task(m: Message):
    uid = m.from_user.id
    rows = await list_tasks(uid, order_by="deadline")
    if not rows:
        await m.answer("У вас пока нет задач.", reply_markup=menu_kb)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{title}", callback_data=f"del_task:{tid}")]
        for (tid, title, deadline, pr, imp, diff, st) in rows
    ])
    await m.answer("Выберите задачу для удаления:", reply_markup=kb)

@dp.callback_query(F.data.startswith("del_task:"))
async def delete_task_confirm(cb: CallbackQuery):
    task_id = int(cb.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"del_yes:{task_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="del_cancel")],
    ])
    await cb.message.edit_text("Удалить задачу?", reply_markup=kb)
    await cb.answer()

@dp.callback_query(F.data == "del_cancel")
async def delete_cancel(cb: CallbackQuery):
    await cb.message.edit_text("Удаление отменено.")
    await cb.answer()

@dp.callback_query(F.data.startswith("del_yes:"))
async def delete_yes(cb: CallbackQuery):
    uid = cb.from_user.id
    task_id = int(cb.data.split(":")[1])
    await delete_task(uid, task_id)

    name = await get_user_name(uid) or "друг"
    await cb.message.edit_text(f"{name}, задача удалена.")
    await cb.answer()

# -------------------- FLOW --------------------

@dp.message(F.text)
async def flow(m: Message):
    uid = m.from_user.id
    txt = (m.text or "").strip()

    if txt.startswith("/"):
        await m.answer("Команда не распознана. Используйте кнопки меню или /start.", reply_markup=menu_kb)
        return

    st = user_states.get(uid)

    if not st:
        await m.answer("Чтобы добавить задачу, нажмите «➕ Добавить задачу» или отправьте /add.", reply_markup=menu_kb)
        return

    # onboarding name
    if st.get("step") == "ask_name":
        name = txt.strip()
        if len(name) < 2 or len(name) > 30:
            await m.answer("Пожалуйста, укажите имя от 2 до 30 символов.", reply_markup=menu_kb)
            return
        await set_user_name(uid, name)
        user_states.pop(uid, None)
        await m.answer(f"Очень приятно, {name}!", reply_markup=menu_kb)
        return

    # settings: change name
    if st.get("step") == "change_name":
        new_name = txt.strip()
        if len(new_name) < 2 or len(new_name) > 30:
            await m.answer("Имя должно быть от 2 до 30 символов. Попробуйте ещё раз.")
            return
        await set_user_name(uid, new_name)
        user_states.pop(uid, None)
        await m.answer("Имя обновлено ✅", reply_markup=menu_kb)
        return

    name = await get_user_name(uid) or "друг"

    # add task flow
    if st.get("step") == "title":
        st["title"] = txt
        st["step"] = "deadline"
        await m.answer(
            f"{name}, укажите дату и время завершения задачи.\n\n"
            "Формат: ГГГГ-ММ-ДД ЧЧ:ММ\n"
            "Пример: 2026-02-28 18:30",
            reply_markup=menu_kb
        )
        return

    if st.get("step") == "deadline":
        try:
            st["deadline"] = parse_deadline_utc(txt)
        except:
            await m.answer(
                "Неверный формат даты.\n"
                "Пример: 2026-02-28 18:30\n"
                "Формат: ГГГГ-ММ-ДД ЧЧ:ММ",
                reply_markup=menu_kb
            )
            return

        st["step"] = "priority"
        await m.answer("Оцените срочность (1–5).", reply_markup=menu_kb)
        return

    if st.get("step") in ("priority", "importance", "difficulty"):
        if not txt.isdigit() or not (1 <= int(txt) <= 5):
            await m.answer("Введите число от 1 до 5.", reply_markup=menu_kb)
            return

        st[st["step"]] = int(txt)

        if st["step"] == "priority":
            st["step"] = "importance"
            await m.answer("Оцените важность (1–5).", reply_markup=menu_kb)
        elif st["step"] == "importance":
            st["step"] = "difficulty"
            await m.answer("Оцените сложность (1–5).", reply_markup=menu_kb)
        else:
            await add_task(uid, st["title"], st["deadline"], st["priority"], st["importance"], st["difficulty"])
            user_states.pop(uid, None)
            await m.answer(f"{name}, задача добавлена.", reply_markup=menu_kb)
        return

# -------------------- MAIN --------------------

async def main():
    await init_db()
    bot = Bot(BOT_TOKEN)
    setup_scheduler(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())