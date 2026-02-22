import json
import re
from typing import Any, Dict, Optional

from google import genai

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

SYSTEM_PROMPT = """
Ты — ИИ-помощник планировщика задач Priorix.
Извлеки из текста пользователя задачу и верни СТРОГО JSON без дополнительного текста.

Формат JSON:
{
  "title": "короткое название",
  "deadline": "YYYY-MM-DD HH:MM" | null,
  "priority": 1-5,
  "importance": 1-5,
  "difficulty": 1-5
}

Правила:
- deadline строго в формате YYYY-MM-DD HH:MM. Если пользователь сказал "завтра/сегодня" или без точного времени — deadline = null.
- priority: срочность (1..5), importance: важность (1..5), difficulty: сложность (1..5).
- Если оценок нет в тексте — оцени по смыслу.
- Никакого текста кроме JSON.
""".strip()

def _safe_int(x: Any, default: int = 3) -> int:
    try:
        x = int(x)
    except:
        return default
    return max(1, min(5, x))

def _valid_deadline(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    return s if DATE_RE.match(s) else None

async def gemini_task_json(api_key: str, user_text: str, model: str = "gemini-2.5-flash") -> Dict[str, Any]:
    """
    Возвращает распарсенный JSON задачи.
    """
    client = genai.Client(api_key=api_key)

    prompt = f"{SYSTEM_PROMPT}\n\nТекст пользователя:\n{user_text.strip()}\n"

    # Важно: модель должна вернуть JSON. Мы дополнительно “вырежем” JSON из ответа.
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
    )

    content = (getattr(resp, "text", None) or "").strip()
    if not content:
        raise ValueError("EMPTY_AI_RESPONSE")

    # вырезаем JSON даже если модель добавила лишнее
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"AI_BAD_OUTPUT: {content[:200]}")

    obj = json.loads(content[start:end + 1])

    title = (obj.get("title") or "").strip() or "Без названия"
    deadline = _valid_deadline(obj.get("deadline"))
    pr = _safe_int(obj.get("priority"), 3)
    imp = _safe_int(obj.get("importance"), 3)
    diff = _safe_int(obj.get("difficulty"), 3)

    return {
        "title": title,
        "deadline": deadline,
        "priority": pr,
        "importance": imp,
        "difficulty": diff,
    }