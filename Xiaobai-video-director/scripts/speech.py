"""Standalone audio/alignment primitives, extracted from the validated workshop path.
No application settings, database, queue or web framework imports.
"""
from __future__ import annotations
import json
import subprocess
import tempfile
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

@dataclass(frozen=True)
class SpeechAlignmentSpan:
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class SpeechAlignment:
    source: str
    granularity: Literal["word", "sentence"]
    spans: tuple[SpeechAlignmentSpan, ...]


@dataclass(frozen=True)
class SpeechSynthesisRequest:
    protocol: Literal["tencent_speech"]
    base_url: str
    api_key: str = field(repr=False)
    model: str
    voice_id: str
    text: str
    speed: float = 1.0
    response_format: Literal["mp3"] = "mp3"


@dataclass(frozen=True)
class SpeechSynthesisResponse:
    audio: bytes
    media_type: str
    provider_request_id: str | None
    duration_seconds: float | None = None
    alignment: SpeechAlignment | None = None


class ProviderCallError(Exception):
    def __init__(
        self,
        *,
        code: str,
        safe_message: str,
        retryable: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.details = details or {}


class AudioProcessingError(RuntimeError):
    pass


def probe_audio_duration(audio: bytes, *, suffix: str = ".mp3") -> float:
    with tempfile.TemporaryDirectory(prefix="director-audio-probe-") as temp_dir:
        path = Path(temp_dir) / f"segment{suffix}"
        path.write_bytes(audio)
        try:
            completed = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            raise AudioProcessingError("无法读取语音片段时长。") from error
        if completed.returncode != 0:
            raise AudioProcessingError("语音片段不是可读取的音频。")
        try:
            duration = float(json.loads(completed.stdout)["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AudioProcessingError("语音片段没有有效时长。") from error
        if duration <= 0:
            raise AudioProcessingError("语音片段没有有效时长。")
        return round(duration, 3)


def concatenate_mp3(segments: list[bytes], *, timeout_seconds: float) -> bytes:
    if not segments:
        raise AudioProcessingError("没有可合并的语音片段。")
    if len(segments) == 1:
        return segments[0]
    with tempfile.TemporaryDirectory(prefix="director-audio-concat-") as temp_dir:
        temp = Path(temp_dir)
        segment_paths: list[Path] = []
        for index, content in enumerate(segments):
            path = temp / f"segment-{index:03d}.mp3"
            path.write_bytes(content)
            segment_paths.append(path)
        list_path = temp / "segments.txt"
        list_path.write_text(
            "\n".join(f"file '{path.as_posix()}'" for path in segment_paths) + "\n",
            encoding="utf-8",
        )
        output_path = temp / "narration.mp3"
        try:
            completed = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_path),
                    "-c:a",
                    "libmp3lame",
                    "-y",
                    str(output_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            raise AudioProcessingError("无法合并语音片段。") from error
        if completed.returncode != 0 or not output_path.is_file():
            raise AudioProcessingError("语音片段合并失败。")
        return output_path.read_bytes()


class SpeechAlignmentError(ValueError):
    pass


def validate_speech_alignment(
    alignment: SpeechAlignment,
    *,
    narration_text: str,
    audio_duration_seconds: float,
) -> SpeechAlignment:
    if alignment.source != "provider_native":
        raise SpeechAlignmentError("Speech Alignment must be provider-native")
    if not alignment.spans:
        raise SpeechAlignmentError("Speech Alignment must contain spans")
    if audio_duration_seconds <= 0:
        raise SpeechAlignmentError("Final audio duration must be positive")

    previous_end = 0
    for span in alignment.spans:
        if not span.text:
            raise SpeechAlignmentError("Speech Alignment span text cannot be empty")
        if span.start_ms < previous_end or span.end_ms <= span.start_ms:
            raise SpeechAlignmentError("Speech Alignment timestamps must be monotonic")
        previous_end = span.end_ms

    duration_ms = round(audio_duration_seconds * 1000)
    if alignment.spans[-1].end_ms > duration_ms:
        raise SpeechAlignmentError("Speech Alignment exceeds final audio duration")
    if _content_text(span.text for span in alignment.spans) != _content_text(
        (narration_text,)
    ):
        raise SpeechAlignmentError("Speech Alignment does not map to approved narration")
    return alignment


def materialize_phrase_beats(
    *,
    scene_id: str,
    narration_text: str,
    phrase_beats: tuple[dict[str, Any], ...],
    alignment: SpeechAlignment,
) -> tuple[dict[str, Any], ...]:
    if not phrase_beats:
        raise SpeechAlignmentError("Phrase beats are required")
    if _content_text(str(beat.get("text", "")) for beat in phrase_beats) != _content_text(
        (narration_text,)
    ):
        raise SpeechAlignmentError("Phrase beats do not map to approved narration")
    if _content_text(span.text for span in alignment.spans) != _content_text(
        (narration_text,)
    ):
        raise SpeechAlignmentError("Speech Alignment does not map to approved narration")

    results: list[dict[str, Any]] = []
    span_cursor = 0
    for beat_index, beat in enumerate(phrase_beats, start=1):
        beat_id = str(beat.get("id", "")).strip()
        beat_text = str(beat.get("text", "")).strip()
        if not beat_id or not beat_text or not beat_id.startswith(f"{scene_id}:"):
            raise SpeechAlignmentError("Phrase beat IDs must be stable within their scene")
        target = _content_text((beat_text,))
        matched = ""
        matched_indexes: list[int] = []
        while matched != target:
            if span_cursor >= len(alignment.spans):
                raise SpeechAlignmentError("Phrase boundary does not match provider spans")
            span_content = _content_text((alignment.spans[span_cursor].text,))
            candidate = matched + span_content
            if not target.startswith(candidate):
                raise SpeechAlignmentError("Phrase boundary falls inside a provider word")
            matched_indexes.append(span_cursor)
            matched = candidate
            span_cursor += 1

        while (
            span_cursor < len(alignment.spans)
            and not _content_text((alignment.spans[span_cursor].text,))
            and _contains_non_content(beat_text)
        ):
            matched_indexes.append(span_cursor)
            span_cursor += 1

        first = alignment.spans[matched_indexes[0]]
        last = alignment.spans[matched_indexes[-1]]
        results.append(
            {
                "id": beat_id,
                "text": beat_text,
                "start_ms": first.start_ms,
                "end_ms": last.end_ms,
                "alignment_refs": tuple(
                    f"span-{index + 1:04d}" for index in matched_indexes
                ),
            }
        )

    if span_cursor != len(alignment.spans):
        raise SpeechAlignmentError("Phrase boundary leaves provider spans unmapped")
    return tuple(results)


def _content_text(parts: Iterable[str]) -> str:
    return "".join(
        character.casefold()
        for part in parts
        for character in unicodedata.normalize("NFKC", part)
        if character.isalnum()
    )


def _contains_non_content(value: str) -> bool:
    return any(not character.isalnum() and not character.isspace() for character in value)
