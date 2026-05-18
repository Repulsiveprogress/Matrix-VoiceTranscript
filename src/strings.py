from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Strings:
    transcribing: str
    transcription_result: str
    transcription_failed: str
    audio_too_large: str
    no_audio_url: str
    unsupported_format: str


def make_strings(locale: str) -> Strings:
    if locale == "ru":
        return Strings(
            transcribing="Распознаю речь…",
            transcription_result="Транскрипция:\n{text}",
            transcription_failed="Не удалось распознать речь.",
            audio_too_large="Аудиофайл слишком большой для распознавания (максимум {max_mb} МБ).",
            no_audio_url="Не удалось скачать аудио.",
            unsupported_format="Формат аудио не поддерживается или повреждён.",
        )
    return Strings(
        transcribing="Transcribing audio…",
        transcription_result="Transcript:\n{text}",
        transcription_failed="Failed to transcribe the audio.",
        audio_too_large="Audio file is too large to transcribe (max {max_mb} MB).",
        no_audio_url="Could not download the audio attachment.",
        unsupported_format="Audio format could not be converted.",
    )
