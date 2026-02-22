import aiosqlite
from datetime import datetime

DB_PATH = "priorix.sqlite"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # users: имя + часовой пояс + режим напоминаний
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            tz_offset_min INTEGER NOT NULL DEFAULT 0,
            reminder_mode TEXT NOT NULL DEFAULT 'normal',
            created_at TEXT NOT NULL
        );
        """)

        # tasks
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            deadline TEXT NOT NULL,
            priority INTEGER NOT NULL,
            importance INTEGER NOT NULL,
            difficulty INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'todo',
            created_at TEXT NOT NULL,
            last_reminded_at TEXT
        );
        """)

        # если таблица users была старой — добавим колонки (мягкая миграция)
        # (SQLite не умеет IF NOT EXISTS для колонок, поэтому try/except)
        try:
            await db.execute("ALTER TABLE users ADD COLUMN tz_offset_min INTEGER NOT NULL DEFAULT 0;")
        except:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN reminder_mode TEXT NOT NULL DEFAULT 'normal';")
        except:
            pass

        await db.commit()


async def get_user_name(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else None


async def set_user_name(user_id: int, name: str):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO users(user_id, name, created_at)
        VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET name=excluded.name
        """, (user_id, name, now))
        await db.commit()


async def get_user_prefs(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
        SELECT tz_offset_min, reminder_mode
        FROM users
        WHERE user_id=?
        """, (user_id,))
        row = await cur.fetchone()
        if not row:
            return (0, "normal")
        return (row[0] or 0, row[1] or "normal")


async def set_user_timezone(user_id: int, tz_offset_min: int):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        # гарантируем запись пользователя
        await db.execute("""
        INSERT INTO users(user_id, name, created_at, tz_offset_min)
        VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET tz_offset_min=excluded.tz_offset_min
        """, (user_id, "User", now, tz_offset_min))
        await db.commit()


async def set_user_reminder_mode(user_id: int, mode: str):
    if mode not in ("gentle", "normal", "strict"):
        mode = "normal"
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO users(user_id, name, created_at, reminder_mode)
        VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET reminder_mode=excluded.reminder_mode
        """, (user_id, "User", now, mode))
        await db.commit()


async def add_task(user_id: int, title: str, deadline_iso: str, priority: int, importance: int, difficulty: int):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO tasks(user_id, title, deadline, priority, importance, difficulty, status, created_at)
        VALUES(?,?,?,?,?,?, 'todo', ?)
        """, (user_id, title, deadline_iso, priority, importance, difficulty, now))
        await db.commit()


async def list_tasks(user_id: int, order_by: str = "deadline"):
    allowed = {"deadline", "priority", "importance", "difficulty", "status", "id"}
    if order_by not in allowed:
        order_by = "deadline"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(f"""
        SELECT id, title, deadline, priority, importance, difficulty, status
        FROM tasks
        WHERE user_id=?
        ORDER BY {order_by} ASC
        """, (user_id,))
        return await cur.fetchall()


async def update_status(user_id: int, task_id: int, status: str):
    if status not in ("todo", "doing", "done"):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE tasks SET status=?
        WHERE user_id=? AND id=?
        """, (status, user_id, task_id))
        await db.commit()


async def delete_task(user_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE user_id=? AND id=?", (user_id, task_id))
        await db.commit()


async def get_all_open_tasks():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
        SELECT id, user_id, title, deadline, priority, importance, difficulty, status, last_reminded_at
        FROM tasks
        WHERE status != 'done'
        """)
        return await cur.fetchall()


async def set_last_reminded(task_id: int, ts_iso: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tasks SET last_reminded_at=? WHERE id=?", (ts_iso, task_id))
        await db.commit()