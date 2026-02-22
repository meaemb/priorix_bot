import os
import re
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery
)
from dotenv import load_dotenv

from ai_gemini import gemini_task_json
from stt_whisper import transcribe_audio  # твой модуль распознавания

from db import (
    init_db, add_task, list_tasks, update_status, delete_task,
    get_user_name, set_user_name,
    get_user_prefs, set_user_timezone, set_user_reminder_mode
)
from scheduler import setup_scheduler


# -------------------- ENV --------------------

ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=ENV_PATH)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

print("CWD:", os.getcwd())
print("ENV FILE:", ENV_PATH)
print("BOT_TOKEN loaded:", bool(BOT_TOKEN))
print("GEMINI_API_KEY loaded:", bool(GEMINI_API_KEY))


# -------------------- CONSTANTS --------------------

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить задачу"), KeyboardButton(text="📋 Мои задачи")],
        [KeyboardButton(text="✅ Статус"), KeyboardButton(text="🗑️ Удалить")],
        [KeyboardButton(text="📅 Сегодня"), KeyboardButton(text="🔽 Сортировка")],
        [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="🤖 ИИ-ввод")],
    ],
    resize_keyboard=True
)

STATUS_MAP = {
    "todo": "К выполнению",
    "doing": "В процессе",
    "done": "Выполнено"
}


# -------------------- HELPERS --------------------

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


def sort_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📆 По дедлайну", callback_data="sort:deadline")],
        [InlineKeyboardButton(text="⚡ По срочности", callback_data="sort:priority")],
        [InlineKeyboardButton(text="🎯 По важности", callback_data="sort:importance")],
        [InlineKeyboardButton(text="🧩 По сложности", callback_data="sort:difficulty")],
    ])


def sort_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к сортировке", callback_data="sort:back")]
    ])


def sort_rows(rows, key: str):
    # rows: (tid, title, deadline, pr, imp, diff, st)
    if not rows:
        return rows

    if key == "deadline":
        return sorted(rows, key=lambda r: r[2])  # ISO строка сортится корректно
    if key == "priority":
        return sorted(rows, key=lambda r: int(r[3]), reverse=True)
    if key == "importance":
        return sorted(rows, key=lambda r: int(r[4]), reverse=True)
    if key == "difficulty":
        return sorted(rows, key=lambda r: int(r[5]), reverse=True)

    return rows


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
        await m.answer("Добро пожаловать в Priorix.\n\nПожалуйста, напишите ваше имя.")
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


# -------------------- SORT --------------------

@dp.message(Command("sort"))
@dp.message(F.text == "🔽 Сортировка")
async def sort_cmd(m: Message):
    await m.answer("Выберите сортировку:", reply_markup=sort_menu_kb())


@dp.callback_query(F.data == "sort:back")
async def sort_back(cb: CallbackQuery):
    await cb.message.edit_text("Выберите сортировку:", reply_markup=sort_menu_kb())
    await cb.answer()


@dp.callback_query(F.data.startswith("sort:"))
async def sort_apply(cb: CallbackQuery):
    uid = cb.from_user.id
    key = cb.data.split(":")[1]

    if key == "back":
        await sort_back(cb)
        return

    rows = await list_tasks(uid, order_by="deadline")  # базово тянем все
    rows = sort_rows(rows, key)

    title_map = {
        "deadline": "📋Отсортировано (дедлайн):\n",
        "priority": "📋Отсортировано (срочность):\n",
        "importance": "📋Отсортировано (важность):\n",
        "difficulty": "📋Отсортировано (сложность):\n",
    }
    text = render_tasks(rows, title=title_map.get(key, "📋Отсортировано:\n"))

    await cb.message.edit_text(text, reply_markup=sort_back_kb())
    await cb.answer()


# -------------------- AI INPUT --------------------

@dp.message(F.text == "🤖 ИИ-ввод")
async def ai_input_start(m: Message):
    uid = m.from_user.id
    user_states[uid] = {"step": "ai_input"}
    if not GEMINI_API_KEY:
        await m.answer("ИИ не настроен: отсутствует GEMINI_API_KEY в .env.", reply_markup=menu_kb)
        return

    await m.answer(
        "🤖 ИИ-ввод включён.\n\n"
        "Опишите задачу одним сообщением.\n"
        "Пример: «Сделать презентацию до 2026-03-01 18:30, срочно, важно, сложно»",
        reply_markup=menu_kb
    )


# -------------------- VOICE (STT + AI) --------------------

@dp.message(F.voice)
async def voice_handler(m: Message):
    uid = m.from_user.id

    bot = m.bot
    file = await bot.get_file(m.voice.file_id)
    local_path = f"voice_{uid}_{m.message_id}.ogg"

    try:
        await bot.download_file(file.file_path, destination=local_path)

        text = transcribe_audio(local_path)
        if not text or not text.strip():
            await m.answer("Я ничего не услышал(а) в голосовом 😅 Попробуйте ещё раз.")
            return

        await m.answer(f"🎤 Я услышал(а):\n{text}")

        if not GEMINI_API_KEY:
            await m.answer("ИИ не настроен: нет GEMINI_API_KEY в .env.")
            return

        task = await gemini_task_json(GEMINI_API_KEY, text)

        if not task.get("deadline"):
            user_states[uid] = {"step": "ai_deadline", "ai_task": task}
            await m.answer("Укажите дедлайн строго: YYYY-MM-DD HH:MM\nПример: 2026-02-28 18:30")
            return

        deadline_iso = parse_deadline_utc(task["deadline"])
        await add_task(uid, task["title"], deadline_iso, task["priority"], task["importance"], task["difficulty"])

        await m.answer(
            "✅ Задача добавлена (голосом)\n\n"
            f"• {task['title']}\n"
            f"• Дедлайн: {task['deadline']}\n"
            f"• Срочность: {stars(task['priority'])}\n"
            f"• Важность: {stars(task['importance'])}\n"
            f"• Сложность: {stars(task['difficulty'])}",
            reply_markup=menu_kb
        )

    except Exception:
        await m.answer("Не получилось распознать голос 😔 Попробуйте короче и громче.")
    finally:
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
        except:
            pass


# -------------------- ADD TASK (manual) --------------------

@dp.message(Command("add"))
async def add_cmd(m: Message):
    uid = m.from_user.id
    name = await get_user_name(uid) or "друг"
    user_states[uid] = {"step": "title"}
    await m.answer(f"{name}, укажите название задачи.\nПример: Подготовить презентацию", reply_markup=menu_kb)


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
    await cb.message.edit_text(f"{name}, статус обновлён.\nТеперь: {STATUS_MAP.get(new_status, new_status)}")
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


# -------------------- FLOW (TEXT) --------------------

@dp.message(F.text)
async def flow(m: Message):
    uid = m.from_user.id
    txt = (m.text or "").strip()

    # неизвестные команды
    if txt.startswith("/"):
        await m.answer("Команда не распознана. Используйте кнопки меню или /start.", reply_markup=menu_kb)
        return

    st = user_states.get(uid)

    # 1) AI input
    if st and st.get("step") == "ai_input":
        if not GEMINI_API_KEY:
            await m.answer("ИИ не настроен: отсутствует GEMINI_API_KEY в .env.", reply_markup=menu_kb)
            return
        try:
            task = await gemini_task_json(GEMINI_API_KEY, txt)
        except Exception:
            await m.answer("Не получилось распознать задачу. Попробуйте написать проще.", reply_markup=menu_kb)
            return

        if not task.get("deadline"):
            st["ai_task"] = task
            st["step"] = "ai_deadline"
            await m.answer("Укажите дедлайн строго: YYYY-MM-DD HH:MM\nПример: 2026-02-28 18:30", reply_markup=menu_kb)
            return

        deadline_iso = parse_deadline_utc(task["deadline"])
        await add_task(uid, task["title"], deadline_iso, task["priority"], task["importance"], task["difficulty"])
        user_states.pop(uid, None)

        await m.answer(
            "✅ Задача добавлена (ИИ)\n\n"
            f"• {task['title']}\n"
            f"• Дедлайн: {task['deadline']}\n"
            f"• Срочность: {stars(task['priority'])}\n"
            f"• Важность: {stars(task['importance'])}\n"
            f"• Сложность: {stars(task['difficulty'])}",
            reply_markup=menu_kb
        )
        return

    # 2) ai_deadline
    if st and st.get("step") == "ai_deadline":
        try:
            dl_iso = parse_deadline_utc(txt)
        except:
            await m.answer("Формат неверный. Пример: 2026-02-28 18:30", reply_markup=menu_kb)
            return

        task = st["ai_task"]
        await add_task(uid, task["title"], dl_iso, task["priority"], task["importance"], task["difficulty"])
        user_states.pop(uid, None)
        await m.answer("✅ Задача добавлена (ИИ).", reply_markup=menu_kb)
        return

    # default states
    if not st:
        await m.answer("Выберите действие в меню 👇", reply_markup=menu_kb)
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

    # change name
    if st.get("step") == "change_name":
        new_name = txt.strip()
        if len(new_name) < 2 or len(new_name) > 30:
            await m.answer("Имя должно быть от 2 до 30 символов. Попробуйте ещё раз.", reply_markup=menu_kb)
            return
        await set_user_name(uid, new_name)
        user_states.pop(uid, None)
        await m.answer("Имя обновлено ✅", reply_markup=menu_kb)
        return

    name = await get_user_name(uid) or "друг"

    # manual add flow
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