# stt_whisper.py
from faster_whisper import WhisperModel

# tiny/base = быстрее, small/medium = точнее
_model = WhisperModel("small", device="cpu", compute_type="int8")

def transcribe_audio(path: str) -> str:
    segments, _ = _model.transcribe(path, language="ru")  # можно убрать language, если будет микс RU/EN
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text