"""Tencent-only adapter extracted from the validated workshop implementation.
TC3 signing, chunking, native timestamps and speed mapping retain their behavior.
"""
from __future__ import annotations
import binascii
import hashlib
import hmac
import ipaddress
import json
import socket
from base64 import b64decode
from datetime import UTC, datetime
from itertools import pairwise
from time import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
import httpx
from speech import (AudioProcessingError, ProviderCallError, SpeechAlignment,
    SpeechAlignmentError, SpeechAlignmentSpan, SpeechSynthesisRequest,
    SpeechSynthesisResponse, concatenate_mp3, probe_audio_duration,
    validate_speech_alignment)

TIMEOUT_SECONDS = 1800.0
MAX_RESPONSE_BYTES = 50_000_000

class TencentSpeechGateway:
    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResponse:
        self._validate_tencent_endpoint(request.base_url)
        if request.protocol != "tencent_speech" or not request.text.strip() or request.response_format != "mp3":
            raise ProviderCallError(code="PROVIDER_CONFIGURATION_INVALID", safe_message="需要腾讯云协议、非空文本和 MP3 格式。", retryable=False)
        return self._synthesize_tencent(request)

    @staticmethod
    def _validate_tencent_endpoint(value: str) -> None:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        official = host == "tts.tencentcloudapi.com" or (host.startswith("tts.") and host.endswith(".tencentcloudapi.com"))
        if (parsed.scheme != "https" or not official or parsed.username or parsed.password
            or parsed.port not in {None, 443} or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ProviderCallError(code="PROVIDER_CONFIGURATION_INVALID", safe_message="腾讯云语音必须使用无附加路径、参数和凭证的官方 HTTPS 地址。", retryable=False)

    @staticmethod
    def _validate_dns(endpoint: str) -> None:
        try:
            addresses = socket.getaddrinfo(urlsplit(endpoint).hostname, 443)
        except socket.gaierror as error:
            raise ProviderCallError(code="PROVIDER_DNS_FAILED", safe_message="无法解析腾讯云语音地址。", retryable=True) from error
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ProviderCallError(code="PROVIDER_ADDRESS_NOT_ALLOWED", safe_message="腾讯云地址解析到了不允许访问的网络。", retryable=False)

    def _synthesize_tencent(
        self, request: SpeechSynthesisRequest
    ) -> SpeechSynthesisResponse:
        secret_id, secret_key = self._parse_tencent_credentials(request.api_key)
        try:
            model_type = int(request.model)
            voice_type = int(request.voice_id)
        except ValueError as error:
            raise ProviderCallError(
                code="PROVIDER_CONFIGURATION_INVALID",
                safe_message="腾讯云模型类型和音色 ID 必须是整数。",
                retryable=False,
            ) from error
        chunks = self._split_tencent_text(request.text)
        results = [
            self._synthesize_tencent_chunk(
                request,
                text=chunk,
                secret_id=secret_id,
                secret_key=secret_key,
                model_type=model_type,
                voice_type=voice_type,
            )
            for chunk in chunks
        ]
        if len(results) == 1:
            return results[0]

        try:
            offset_ms = 0
            merged_spans: list[SpeechAlignmentSpan] = []
            for result in results:
                if result.alignment is None:
                    raise AudioProcessingError("腾讯云分段缺少时间戳。")
                merged_spans.extend(
                    SpeechAlignmentSpan(
                        text=span.text,
                        start_ms=span.start_ms + offset_ms,
                        end_ms=span.end_ms + offset_ms,
                    )
                    for span in result.alignment.spans
                )
                offset_ms += round(probe_audio_duration(result.audio) * 1000)
            audio = concatenate_mp3(
                [result.audio for result in results],
                timeout_seconds=TIMEOUT_SECONDS,
            )
            duration_seconds = probe_audio_duration(audio)
            alignment = validate_speech_alignment(
                SpeechAlignment(
                    source="provider_native",
                    granularity="word",
                    spans=tuple(merged_spans),
                ),
                narration_text=request.text,
                audio_duration_seconds=duration_seconds,
            )
        except (AudioProcessingError, SpeechAlignmentError) as error:
            raise ProviderCallError(
                code="PROVIDER_RESPONSE_INVALID",
                safe_message="腾讯云分段语音无法合并为可靠的旁白时间轴。",
                retryable=False,
            ) from error
        if len(audio) > MAX_RESPONSE_BYTES:
            raise ProviderCallError(
                code="PROVIDER_RESPONSE_TOO_LARGE",
                safe_message="腾讯云合成后的完整旁白过大。",
                retryable=False,
            )
        return SpeechSynthesisResponse(
            audio=audio,
            media_type="audio/mpeg",
            provider_request_id=results[0].provider_request_id,
            duration_seconds=duration_seconds,
            alignment=alignment,
        )


    def _synthesize_tencent_chunk(
        self,
        request: SpeechSynthesisRequest,
        *,
        text: str,
        secret_id: str,
        secret_key: str,
        model_type: int,
        voice_type: int,
    ) -> SpeechSynthesisResponse:
        payload = {
            "Text": text,
            "SessionId": str(uuid4()),
            "Volume": 0,
            "Speed": self._tencent_speed(request.speed),
            "ProjectId": 0,
            "ModelType": model_type,
            "VoiceType": voice_type,
            "PrimaryLanguage": 1,
            "SampleRate": 16000,
            "Codec": request.response_format,
            "EnableSubtitle": True,
        }
        response = self._post_tencent(
            request.base_url,
            secret_id=secret_id,
            secret_key=secret_key,
            payload=payload,
        )
        try:
            body = response.json()
            provider_response = body["Response"]
            if not isinstance(provider_response, dict):
                raise TypeError
            provider_error = provider_response.get("Error")
            if provider_error is not None:
                if not isinstance(provider_error, dict):
                    raise TypeError
                raise ProviderCallError(
                    code="PROVIDER_REJECTED_REQUEST",
                    safe_message="腾讯云语音服务拒绝了请求。",
                    retryable=False,
                    details={
                        "provider_code": provider_error.get("Code"),
                        "provider_request_id": provider_response.get("RequestId"),
                    },
                )
            encoded_audio = provider_response["Audio"]
            if not isinstance(encoded_audio, str):
                raise TypeError
            audio = b64decode(encoded_audio, validate=True)
            request_id = provider_response.get("RequestId")
            alignment = self._tencent_alignment(
                raw_subtitles=provider_response.get("Subtitles"),
                narration_text=text,
            )
        except ProviderCallError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError, binascii.Error) as error:
            raise ProviderCallError(
                code="PROVIDER_RESPONSE_INVALID",
                safe_message="腾讯云语音服务返回了无法识别的音频或字幕。",
                retryable=False,
            ) from error
        self._validate_audio(audio)
        return SpeechSynthesisResponse(
            audio=audio,
            media_type="audio/mpeg",
            provider_request_id=request_id if isinstance(request_id, str) else None,
            alignment=alignment,
        )


    @staticmethod
    def _split_tencent_text(text: str, *, max_characters: int = 140) -> tuple[str, ...]:
        if len(text) <= max_characters:
            return (text,)
        chunks: list[str] = []
        remaining = text
        sentence_endings = "。！？!?；;\n"
        while len(remaining) > max_characters:
            minimum_split = max_characters // 2
            split_at = max(
                (remaining.rfind(mark, minimum_split, max_characters) for mark in sentence_endings),
                default=-1,
            )
            split_at = split_at + 1 if split_at >= minimum_split else max_characters
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]
        if remaining:
            chunks.append(remaining)
        return tuple(chunks)


    def _tencent_alignment(
        self,
        *,
        raw_subtitles: Any,
        narration_text: str,
    ) -> SpeechAlignment:
        if not isinstance(raw_subtitles, list) or not raw_subtitles:
            raise ProviderCallError(
                code="SPEECH_TIMESTAMPS_UNAVAILABLE",
                safe_message="所选腾讯云音色没有返回字幕时间轴，请试听并确认支持时间戳的音色。",
                retryable=False,
            )
        try:
            spans_list: list[SpeechAlignmentSpan] = []
            previous_end_ms = 0
            for item in raw_subtitles:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("Text"), str)
                    or not isinstance(item.get("BeginTime"), int)
                    or isinstance(item.get("BeginTime"), bool)
                    or not isinstance(item.get("EndTime"), int)
                    or isinstance(item.get("EndTime"), bool)
                ):
                    raise TypeError
                text = item["Text"]
                start_ms = item["BeginTime"]
                end_ms = item["EndTime"]
                if not text.strip():
                    continue
                if end_ms <= start_ms:
                    if not any(character.isalnum() for character in text):
                        continue
                    raise SpeechAlignmentError("Tencent content timestamp has no duration")
                overlap_ms = previous_end_ms - start_ms
                if overlap_ms > 250:
                    raise SpeechAlignmentError("Tencent content timestamps overlap too far")
                start_ms = max(start_ms, previous_end_ms)
                if end_ms <= start_ms:
                    raise SpeechAlignmentError("Tencent content timestamp was swallowed by overlap")
                spans_list.append(
                    SpeechAlignmentSpan(text=text, start_ms=start_ms, end_ms=end_ms)
                )
                previous_end_ms = end_ms
            spans = tuple(spans_list)
            if not spans:
                raise TypeError
            return validate_speech_alignment(
                SpeechAlignment(
                    source="provider_native",
                    granularity="word",
                    spans=spans,
                ),
                narration_text=narration_text,
                audio_duration_seconds=spans[-1].end_ms / 1000,
            )
        except (KeyError, TypeError, ValueError, SpeechAlignmentError) as error:
            raise ProviderCallError(
                code="PROVIDER_RESPONSE_INVALID",
                safe_message="腾讯云返回的字幕时间轴无法与配音可靠对齐，请重试；若仍失败再更换音色。",
                retryable=False,
            ) from error


    def _post_tencent(
        self,
        endpoint: str,
        *,
        secret_id: str,
        secret_key: str,
        payload: dict[str, Any],
    ) -> httpx.Response:
        self._validate_tencent_endpoint(endpoint)
        self._validate_dns(endpoint)
        timestamp = int(time())
        parsed = urlsplit(endpoint)
        host = parsed.netloc
        canonical_uri = parsed.path or "/"
        content_type = "application/json; charset=utf-8"
        action = "TextToVoice"
        payload_bytes = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        signed_headers = "content-type;host;x-tc-action"
        canonical_headers = (
            f"content-type:{content_type}\n"
            f"host:{host}\n"
            f"x-tc-action:{action.lower()}\n"
        )
        canonical_request = "\n".join(
            (
                "POST",
                canonical_uri,
                "",
                canonical_headers,
                signed_headers,
                hashlib.sha256(payload_bytes).hexdigest(),
            )
        )
        date = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")
        credential_scope = f"{date}/tts/tc3_request"
        string_to_sign = "\n".join(
            (
                "TC3-HMAC-SHA256",
                str(timestamp),
                credential_scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            )
        )
        secret_date = self._hmac_sha256(f"TC3{secret_key}".encode(), date)
        secret_service = self._hmac_sha256(secret_date, "tts")
        secret_signing = self._hmac_sha256(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing,
            string_to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()
        authorization = (
            "TC3-HMAC-SHA256 "
            f"Credential={secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        try:
            with httpx.Client(
                timeout=TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    endpoint,
                    headers={
                        "Authorization": authorization,
                        "Content-Type": content_type,
                        "Host": host,
                        "X-TC-Action": action,
                        "X-TC-Timestamp": str(timestamp),
                        "X-TC-Version": "2019-08-23",
                    },
                    content=payload_bytes,
                )
        except httpx.TimeoutException as error:
            raise ProviderCallError(
                code="PROVIDER_TIMEOUT",
                safe_message="腾讯云语音服务响应超时。",
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            raise ProviderCallError(
                code="PROVIDER_UNREACHABLE",
                safe_message="无法连接腾讯云语音服务。",
                retryable=True,
            ) from error
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ProviderCallError(
                code="PROVIDER_RESPONSE_TOO_LARGE",
                safe_message="腾讯云语音服务返回内容过大。",
                retryable=False,
            )
        if response.is_redirect:
            raise ProviderCallError(
                code="PROVIDER_REDIRECT_NOT_ALLOWED",
                safe_message="腾讯云语音服务返回了不允许的重定向。",
                retryable=False,
            )
        if response.status_code >= 400:
            raise ProviderCallError(
                code="PROVIDER_REJECTED_REQUEST",
                safe_message=f"腾讯云语音服务请求失败（HTTP {response.status_code}）。",
                retryable=response.status_code in {408, 409, 429}
                or response.status_code >= 500,
            )
        return response


    @staticmethod
    def _parse_tencent_credentials(value: str) -> tuple[str, str]:
        secret_id, separator, secret_key = value.partition(":")
        if not separator or not secret_id.strip() or not secret_key.strip():
            raise ProviderCallError(
                code="PROVIDER_CONFIGURATION_INVALID",
                safe_message="腾讯云凭证格式无效，请同时填写 SecretId 和 SecretKey。",
                retryable=False,
            )
        return secret_id.strip(), secret_key.strip()


    @staticmethod
    def _tencent_speed(multiplier: float) -> float:
        anchors = (
            (0.5, -2.0),
            (0.8, -1.0),
            (1.0, 0.0),
            (1.2, 1.0),
            (1.5, 2.0),
            (2.0, 4.0),
        )
        bounded = min(max(multiplier, anchors[0][0]), anchors[-1][0])
        for (left_rate, left_value), (right_rate, right_value) in pairwise(anchors):
            if bounded <= right_rate:
                ratio = (bounded - left_rate) / (right_rate - left_rate)
                return round(left_value + ratio * (right_value - left_value), 2)
        return anchors[-1][1]


    @staticmethod
    def _hmac_sha256(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode(), hashlib.sha256).digest()


    @staticmethod
    def _validate_audio(audio: bytes) -> None:
        is_mp3 = audio.startswith(b"ID3") or (
            len(audio) >= 2 and audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0
        )
        if not is_mp3:
            raise ProviderCallError(
                code="PROVIDER_RESPONSE_INVALID",
                safe_message="语音服务没有返回有效的 MP3。",
                retryable=False,
            )
