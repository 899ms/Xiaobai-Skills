"""Standalone director preparation. Run with this skill's own uv project."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import score as executable_score
import unicodedata
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from speech import (SpeechAlignment, SpeechAlignmentSpan, SpeechSynthesisRequest,
                    materialize_phrase_beats, validate_speech_alignment, probe_audio_duration)
from tencent import TencentSpeechGateway
from visual import VisualError, read_json, run_frame_gate, validate_sound_assets, validate_world_timeline

ROOT = Path(__file__).resolve().parents[1]
APPROVAL = ROOT / ".local" / "voice-approval.json"
VOICE_DEFAULT = ROOT / ".local" / "voice-default.json"
SAMPLE = "让AI替你做事，要先把预算与权限说清楚。假设总预算十美元，这次花了三美元，还剩七美元。每笔支出都留下记录，方便之后核对。"
LEARNING_ENFORCERS = {
    "gate": {
        "verb-visible-evidence", "progressive-reveal", "complete-exit", "label-artwork-separation",
        "semantic-motion-destination", "continuous-state-world", "container-and-spacing",
        "single-meaning-object", "exact-spoken-label-binding", "typed-object-relations",
        "state-derived-result", "attention-isolation", "causal-transition-process",
    },
    "world-gate": {"executable-world-model", "semantic-motion-endpoints", "horizontal-lane-consistency"},
    "frame-gate": {"executable-world-model", "actor-layer-visibility", "horizontal-lane-consistency"},
    "qa-gate": {"context-neutrality", "transition-variety-with-purpose", "direct-metaphor", "causal-transition-process"},
}


class DirectorError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise DirectorError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_learning_rules(profile_id):
    manifest_path = ROOT / "assets/learned-gates.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("version") == 1:
        rules = manifest.get("rules")
    else:
        require(manifest.get("version") == 2 and isinstance(manifest.get("sources"), list), "经验账本清单格式无效。")
        rules = []
        for source in manifest["sources"]:
            require(isinstance(source, dict) and source.get("scope") in {"core", "profile"} and isinstance(source.get("path"), str), "经验账本来源格式无效。")
            if source["scope"] == "profile" and source.get("profile") != profile_id:
                continue
            path = (manifest_path.parent / source["path"]).resolve()
            require(path == ROOT or ROOT in path.parents, "经验账本来源不能离开 Skill 目录。")
            require(path.is_file(), f"缺少经验账本：{path}")
            payload = json.loads(path.read_text())
            require(payload.get("version") == 1 and payload.get("scope") == source["scope"] and isinstance(payload.get("rules"), list), f"经验账本格式无效：{path}")
            if source["scope"] == "profile":
                require(payload.get("profile") == profile_id, f"经验账本 Profile 不匹配：{path}")
            rules.extend(payload["rules"])
    require(isinstance(rules, list) and all(isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] for item in rules), "经验账本规则格式无效。")
    require(len({item["id"] for item in rules}) == len(rules), "经验账本规则 id 不能重复。")
    for item in rules:
        enforcement = item.get("enforcement")
        require(isinstance(enforcement, str), f"经验 {item['id']} 缺少 enforcement。")
        components = enforcement.split("+")
        require(len(components) == len(set(components)) and "release" in components and set(components).issubset({*LEARNING_ENFORCERS, "release"}), f"经验 {item['id']} 必须接入已知检查器并由 release 阻断。")
        for component in set(components) - {"release"}:
            require(item["id"] in LEARNING_ENFORCERS[component], f"经验 {item['id']} 声明由 {component} 执行，但没有注册对应检查。")
    return rules


def normalize_speech(text):
    return re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[A-Za-z])|(?<=[A-Za-z]) +(?=[\u4e00-\u9fff])", "", text)


def content(text):
    return "".join(c.casefold() for c in unicodedata.normalize("NFKC", text) if c.isalnum())


def load_config(path):
    require(path.is_file(), "缺少 .env，请从 .env.example 创建本机配置。")
    cfg = {k: v or "" for k, v in dotenv_values(path, interpolate=False).items()}
    required = ("TTS_PROTOCOL", "TTS_BASE_URL", "TTS_MODEL", "TTS_VOICE_ID", "TTS_SPEED", "PLAYBACK_RATE", "TTS_LANGUAGE")
    require(all(cfg.get(k) for k in required), "配置缺项：" + ", ".join(k for k in required if not cfg.get(k)))
    require(cfg.get("TTS_CREDENTIAL_SOURCE", "env") == "env", "独立版不读取工坊数据库；请在本机 .env 填写腾讯云凭证。")
    require(cfg["TTS_PROTOCOL"] == "tencent_speech", "v1 仅验证了 tencent_speech；其他服务需另做适配和对齐验证。")
    require(cfg["TTS_LANGUAGE"] == "zh-CN", "v1 支持中文及中英混读，暂不支持切换主语言。")
    parsed = urlsplit(cfg["TTS_BASE_URL"])
    require(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.path in {"", "/"}, "TTS_BASE_URL 不得包含凭证、查询参数或额外路径。")
    require(cfg["TTS_MODEL"].isdigit() and cfg["TTS_VOICE_ID"].isdigit(), "模型和音色 ID 必须为整数。")
    require(cfg["TTS_MODEL"] == "1", "v1 仅验证了 ModelType=1。")
    try:
        cfg["TTS_SPEED"] = float(cfg["TTS_SPEED"])
        cfg["PLAYBACK_RATE"] = float(cfg["PLAYBACK_RATE"])
    except ValueError:
        raise DirectorError("语速必须是数值。") from None
    require(.5 <= cfg["TTS_SPEED"] <= 2 and .5 <= cfg["PLAYBACK_RATE"] <= 2, "语速倍数必须在 0.5–2 之间。")
    if cfg.get("TTS_SECRET_ID") or cfg.get("TTS_SECRET_KEY"):
        require(path.stat().st_mode & 0o077 == 0, ".env 含凭证时权限应为 600；请先 chmod 600。")
    TencentSpeechGateway._validate_tencent_endpoint(cfg["TTS_BASE_URL"])
    return cfg


def profile(cfg):
    # Credentials and credential source do not belong in artifacts or voice approval.
    return {**{k: cfg[k] for k in ("TTS_PROTOCOL", "TTS_BASE_URL", "TTS_MODEL", "TTS_VOICE_ID", "TTS_SPEED", "PLAYBACK_RATE", "TTS_LANGUAGE")}, "preprocessing": "cjk-latin-padding-v1", "sample_rate": 16000, "codec": "mp3"}


def voice_approved(cfg, path=APPROVAL):
    if not path.is_file():
        return False
    return json.loads(path.read_text()).get("profile_hash") == digest(profile(cfg))


def load_voice_default(path=VOICE_DEFAULT):
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise DirectorError("默认音色记录损坏；不要猜测偏好，请重新询问用户。") from None
    require(
        isinstance(value, dict)
        and value.get("version") == 1
        and value.get("scope") == "default"
        and isinstance(value.get("label"), str)
        and value["label"].strip()
        and isinstance(value.get("profile_hash"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["profile_hash"]),
        "默认音色记录格式无效；不要猜测偏好，请重新询问用户。",
    )
    return value


def voice_is_default(cfg, path=VOICE_DEFAULT):
    value = load_voice_default(path)
    return bool(value and value["profile_hash"] == digest(profile(cfg)))


def approve_voice(cfg, evidence, note, path=APPROVAL, *, remember_default=False, preference_label=None, preference_path=VOICE_DEFAULT):
    require(evidence.is_file() and bool(note.strip()), "声音确认需要已试听文件和用户确认依据。")
    require(not remember_default or isinstance(preference_label, str) and preference_label.strip(), "设为默认音色时必须记录用户看到的音色名称。")
    require(remember_default or preference_label is None, "只有明确设为默认音色时才能写入音色名称。")
    # Only invoke after a real user decision; this command does not infer acceptance.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    approved_at = datetime.now(timezone.utc).isoformat()
    profile_hash = digest(profile(cfg))
    evidence_sha256 = file_hash(evidence)
    write_json(path, {"profile_hash": profile_hash, "profile": profile(cfg), "evidence": str(evidence.resolve()), "evidence_sha256": evidence_sha256, "user_confirmation": note, "approved_at": approved_at})
    path.chmod(0o600)
    if remember_default:
        preference_path.parent.mkdir(parents=True, exist_ok=True)
        preference_path.parent.chmod(0o700)
        write_json(preference_path, {"version": 1, "scope": "default", "label": preference_label.strip(), "profile_hash": profile_hash, "approval_evidence_sha256": evidence_sha256, "user_confirmation": note, "chosen_at": approved_at})
        preference_path.chmod(0o600)


def credential(cfg):
    require(bool(cfg.get("TTS_SECRET_ID") and cfg.get("TTS_SECRET_KEY")), "请在本机 .env 填写 TTS_SECRET_ID 和 TTS_SECRET_KEY。")
    return cfg["TTS_SECRET_ID"] + ":" + cfg["TTS_SECRET_KEY"]


def synthesize(cfg, narration, out):
    out.mkdir(parents=True, exist_ok=True)
    audio_path, metadata_path = out / "narration.mp3", out / "alignment.json"
    fingerprint = digest({"text": narration, "profile": profile(cfg)})
    if audio_path.exists() or metadata_path.exists():
        require(audio_path.is_file() and metadata_path.is_file(), "已有不完整配音产物，保留它们并换一个输出目录。")
        metadata = json.loads(metadata_path.read_text())
        require(metadata.get("fingerprint") == fingerprint, "输出目录已有另一版配音，请使用新目录以保留旧版本。")
        require(metadata.get("audio_sha256") == file_hash(audio_path), "配音缓存哈希不匹配，不能沿用其时间轴。")
        raw = metadata["alignment"]
        audio = audio_path.read_bytes()
        alignment = SpeechAlignment(raw["source"], raw["granularity"], tuple(SpeechAlignmentSpan(**s) for s in raw["spans"]))
    else:
        response = TencentSpeechGateway().synthesize(SpeechSynthesisRequest(protocol=cfg["TTS_PROTOCOL"], base_url=cfg["TTS_BASE_URL"], api_key=credential(cfg), model=cfg["TTS_MODEL"], voice_id=cfg["TTS_VOICE_ID"], text=narration, speed=cfg["TTS_SPEED"]))
        require(response.alignment is not None, "语音服务未返回原生时间戳；不可退回估算时间。")
        audio, alignment = response.audio, response.alignment
        validate_speech_alignment(alignment, narration_text=narration, audio_duration_seconds=probe_audio_duration(audio))
        audio_path.write_bytes(audio)
        write_json(metadata_path, {"fingerprint": fingerprint, "profile": profile(cfg), "audio_sha256": file_hash(audio_path), "alignment": asdict(alignment)})
    duration = probe_audio_duration(audio)
    validate_speech_alignment(alignment, narration_text=narration, audio_duration_seconds=duration)
    (out / "narration.txt").write_text(narration + "\n")
    return alignment, duration


def validate_story(story):
    require(isinstance(story, dict), "storyboard 必须是 JSON 对象。")
    require(isinstance(story.get("id"), str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", story["id"]), "storyboard.id 必须是稳定的英文标识。")
    require(isinstance(story.get("title"), str) and story["title"].strip(), "缺少标题。")
    require(isinstance(story.get("focus"), dict) and all(isinstance(story["focus"].get(k), str) and story["focus"][k].strip() for k in ("question", "viewer_takeaway", "visual_subject")), "focus 需要 question、viewer_takeaway 和 visual_subject。")
    require(isinstance(story.get("sources"), list) and story["sources"], "需要至少一项材料依据，不能无材料生成事实讲解。")
    sources = story["sources"]
    require(all(isinstance(s, dict) and isinstance(s.get("id"), str) and s["id"] and any(s.get(k) for k in ("path", "url", "excerpt")) for s in sources), "来源需要 id 和 path/url/excerpt。")
    require(len({s['id'] for s in sources}) == len(sources), "来源 id 不能重复。")
    beats = story.get("beats")
    require(isinstance(beats, list) and bool(beats), "缺少 beats。")
    require(all(isinstance(b, dict) and isinstance(b.get("id"), str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", b["id"]) and isinstance(b.get("text"), str) and content(b["text"]) and isinstance(b.get("visual"), str) and b["visual"].strip() for b in beats), "每个 beat 需要稳定 id、非空 text 和具体 visual。")
    require(len({b['id'] for b in beats}) == len(beats), "beat id 不能重复。")
    source_ids = {s["id"] for s in sources}
    require(all(isinstance(b.get("source_refs", []), list) and all(isinstance(ref, str) and ref in source_ids for ref in b.get("source_refs", [])) for b in beats), "source_refs 必须引用已有材料 id。")
    anchors = story.get("anchors", {})
    require(isinstance(anchors, dict) and all(isinstance(k, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", k) and isinstance(v, str) and content(v) for k, v in anchors.items()), "anchors 必须把稳定名称映射到非空口播原文。")
    require(not set(anchors).intersection(b["id"] for b in beats), "anchor 名称不能覆盖 beat id。")
    narration = normalize_speech("".join(b["text"] for b in beats))
    require("<" not in narration and ">" not in narration, "v1 不接受 SSML/标记式口播；先建立显示文本映射再扩展。")
    require(all(content(narration).count(content(v)) == 1 for v in anchors.values()), "动作词 anchor 必须在全文唯一出现。")
    return narration


def validate_visual_state_table(table):
    """Reject animation plans that are visually ambiguous before React work starts."""
    require(isinstance(table, dict) and table.get("version") == 4, "visual-state-table.version 必须是 4。")
    segment = table.get("segment")
    require(isinstance(segment, dict), "visual-state-table 缺少 segment。")
    require(all(isinstance(segment.get(k), (int, float)) for k in ("startSec", "endSec", "fps")), "segment 需要 startSec、endSec 和 fps。")
    require(segment["startSec"] >= 0 and segment["endSec"] > segment["startSec"] and segment["fps"] > 0, "segment 时间范围或 fps 无效。")

    rules = table.get("rules")
    require(isinstance(rules, dict), "visual-state-table 缺少 rules。")
    max_foreground = rules.get("maxForegroundObjects")
    min_gap = rules.get("minGapPx")
    require(isinstance(max_foreground, int) and 1 <= max_foreground <= 4, "maxForegroundObjects 必须在 1–4。")
    require(isinstance(min_gap, (int, float)) and min_gap >= 0, "minGapPx 必须是非负数。")
    require(rules.get("requireClearEnd") is True, "样片必须启用 requireClearEnd，避免对象跨段残留。")
    require(rules.get("requireAttentionContract") is True, "样片必须启用 requireAttentionContract，避免旧场景与当前主角竞争注意力。")
    require(rules.get("requireInternalRevealContract") is True, "样片必须启用 requireInternalRevealContract，避免对象内部信息抢先出现。")
    require(rules.get("requireContainmentContract") is True, "样片必须启用 requireContainmentContract，避免对象被硬塞进过小容器。")
    require(rules.get("requireContinuityContract") is True, "样片必须启用 requireContinuityContract，避免无意义地频繁换场。")
    require(rules.get("requireMotionContract") is True, "样片必须启用 requireMotionContract，避免位移没有明确目的地。")
    require(rules.get("requireTextSlotContract") is True, "样片必须启用 requireTextSlotContract，避免文字偏位或被遮挡。")
    require(rules.get("requireProgressiveRevealContract") is True, "样片必须启用 requireProgressiveRevealContract，避免一帧堆入多个新对象。")
    require(rules.get("requireEvidenceCoverage") is True, "样片必须启用 requireEvidenceCoverage，逐 beat 检查真实渲染帧。")
    require(rules.get("requireSemanticBindingContract") is True, "样片必须启用 requireSemanticBindingContract，避免口播名词与画面标签错配。")
    require(rules.get("requireStateTransitionContract") is True, "样片必须启用 requireStateTransitionContract，避免结果凭空出现。")
    require(rules.get("requireRelationContract") is True, "样片必须启用 requireRelationContract，避免对象只有并排没有关系。")
    require(rules.get("requireArtworkTextCollisionContract") is True, "样片必须启用 requireArtworkTextCollisionContract，避免文字压住图形。")
    require(rules.get("requireFeedbackRegressionContract") is True, "样片必须启用 requireFeedbackRegressionContract，避免重复踩已记录的坑。")
    require(rules.get("requireCausalTransitionContract") is True, "样片必须启用 requireCausalTransitionContract，避免只切换前后完成态。")
    profile_id = table.get("profile", "knowledge-explainer")
    require((ROOT / "profiles" / profile_id / "profile.json").is_file(), f"未知导演 Profile：{profile_id}")
    learned_ids = {item["id"] for item in load_learning_rules(profile_id)}
    applied = table.get("appliedLearnings")
    require(isinstance(applied, list) and len(applied) == len(set(applied)) and set(applied) == learned_ids, "appliedLearnings 必须精确覆盖生效经验账本。")

    objects = table.get("objects")
    require(isinstance(objects, list) and objects, "visual-state-table 缺少 objects。")
    object_ids = [obj.get("id") for obj in objects if isinstance(obj, dict)]
    require(len(object_ids) == len(objects) and all(isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value) for value in object_ids), "每个对象需要稳定英文 id。")
    require(len(set(object_ids)) == len(object_ids), "对象 id 不能重复。")
    meanings = [obj.get("meaning") for obj in objects]
    require(all(isinstance(value, str) and value.strip() for value in meanings), "每个对象只写一个非空 meaning。")
    require(len(set(meanings)) == len(meanings), "一个视觉对象只能承担一个稳定含义；meaning 不能重复。")
    spoken_terms = [obj.get("spokenTerm") for obj in objects]
    require(all(isinstance(value, str) and content(value) for value in spoken_terms), "每个对象需要一个非空 spokenTerm，绑定口播中的单一名词。")
    require(len(set(content(value) for value in spoken_terms)) == len(spoken_terms), "spokenTerm 必须一词一物，不能由多个对象重复表示。")
    valid_roles = {"actor", "prop", "result", "environment"}
    valid_persistence = {"temporary", "scene", "logical"}
    require(all(obj.get("role") in valid_roles and obj.get("persistence") in valid_persistence and isinstance(obj.get("form"), str) and obj["form"].strip() and isinstance(obj.get("firstBeat"), str) for obj in objects), "对象需要 role、persistence、form 和 firstBeat。")
    valid_motion_kinds = {"move", "store", "merge", "handoff", "discard", "transform"}
    for obj in objects:
        label = obj.get("onScreenLabel")
        require(label is None or isinstance(label, str) and content(label), f"对象 {obj['id']} 的 onScreenLabel 必须是非空文字或 null。")
        require(label is None or content(label) in content(obj["spokenTerm"]) or content(obj["spokenTerm"]) in content(label), f"对象 {obj['id']} 的可见标签必须直接来自 spokenTerm，不能把 {obj['spokenTerm']} 改写成 {label}。")
        states = obj.get("states")
        require(isinstance(states, list) and states and len(states) == len(set(states)) and all(isinstance(state, str) and state.strip() for state in states), f"对象 {obj['id']} 需要唯一、非空的 states。")
        require(isinstance(obj.get("receptors"), list) and len(set(obj["receptors"])) == len(obj["receptors"]) and all(kind in valid_motion_kinds for kind in obj["receptors"]), f"对象 {obj['id']} 需要 receptors；不能承接动作时使用空数组。")
        slots = obj.get("textSlots")
        require(isinstance(slots, list), f"对象 {obj['id']} 需要 textSlots；没有文字时使用空数组。")
        require(len({slot.get('id') for slot in slots if isinstance(slot, dict)}) == len(slots), f"对象 {obj['id']} 的文字槽位 id 不能重复。")
        for slot in slots:
            require(isinstance(slot, dict) and set(slot) == {"id", "meaning", "firstBeat", "box", "align", "placement", "avoidZones"}, f"对象 {obj['id']} 的 textSlots 项格式无效。")
            require(isinstance(slot["id"], str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slot["id"]), f"对象 {obj['id']} 的文字槽位需要稳定英文 id。")
            require(isinstance(slot["meaning"], str) and slot["meaning"].strip() and slot["align"] in {"center", "start"} and slot["placement"] in {"inside", "above", "below"}, f"对象 {obj['id']} 的文字槽位需要 meaning、align 和 placement。")
            box = slot["box"]
            require(isinstance(box, list) and len(box) == 4 and all(isinstance(value, (int, float)) for value in box), f"对象 {obj['id']} 的文字槽位边界无效。")
            x, y, width, height = box
            require(width > 0 and height > 0 and x >= 0 and y >= 0 and x + width <= 1 and y + height <= 1, f"对象 {obj['id']} 的文字槽位必须使用 0–1 的对象内相对坐标。")
            require(slot["placement"] != "above" or y + height <= .5, f"对象 {obj['id']} 的上方文字槽必须位于对象上半区。")
            require(slot["placement"] != "below" or y >= .5, f"对象 {obj['id']} 的下方文字槽必须位于对象下半区。")
            avoid = slot["avoidZones"]
            require(isinstance(avoid, list), f"对象 {obj['id']} 的文字槽位需要 avoidZones。")
            for zone in avoid:
                require(isinstance(zone, list) and len(zone) == 4 and all(isinstance(value, (int, float)) for value in zone), f"对象 {obj['id']} 的文字避让区无效。")
                zx, zy, zw, zh = zone
                require(zw > 0 and zh > 0 and zx >= 0 and zy >= 0 and zx + zw <= 1 and zy + zh <= 1, f"对象 {obj['id']} 的文字避让区必须位于对象内。")
                require(x + width <= zx or zx + zw <= x or y + height <= zy or zy + zh <= y, f"对象 {obj['id']} 的文字槽位与图形避让区相交。")
        if label is not None:
            label_slots = [slot for slot in slots if slot["id"] == "label"]
            require(len(label_slots) == 1 and (content(label_slots[0]["meaning"]) in content(label) or content(label) in content(label_slots[0]["meaning"])), f"对象 {obj['id']} 的可见主标签必须使用唯一 label 槽。")
    by_id = {obj["id"]: obj for obj in objects}

    beats = table.get("beats")
    require(isinstance(beats, list) and beats, "visual-state-table 缺少 beats。")
    beat_ids = [beat.get("id") for beat in beats if isinstance(beat, dict)]
    require(len(beat_ids) == len(beats) and all(isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value) for value in beat_ids), "每个视觉 beat 需要稳定英文 id。")
    require(len(set(beat_ids)) == len(beat_ids), "视觉 beat id 不能重复。")
    beat_id_set = set(beat_ids)
    beat_index = {beat_id: index for index, beat_id in enumerate(beat_ids)}
    require(all(obj["firstBeat"] in beat_id_set for obj in objects), "对象 firstBeat 必须引用已有视觉 beat。")
    reveals_by_beat = {}
    reveal_ids = []
    for obj in objects:
        reveals = obj.get("internalReveals")
        require(isinstance(reveals, list), f"对象 {obj['id']} 需要 internalReveals；没有延迟出现的信息时使用空数组。")
        for reveal in reveals:
            require(isinstance(reveal, dict) and isinstance(reveal.get("id"), str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", reveal["id"]), f"对象 {obj['id']} 的内部信息需要稳定英文 id。")
            require(isinstance(reveal.get("meaning"), str) and reveal["meaning"].strip(), f"对象 {obj['id']} 的内部信息需要唯一含义。")
            require(reveal.get("firstBeat") in beat_id_set, f"对象 {obj['id']} 的内部信息 {reveal['id']} 引用了未知 firstBeat。")
            reveal_ids.append(reveal["id"])
            reveals_by_beat.setdefault(reveal["firstBeat"], []).append(obj["id"])
        require(len({reveal["firstBeat"] for reveal in reveals}) == len(reveals), f"对象 {obj['id']} 的多项内部信息必须逐 beat 揭示，不能同时出现。")
        require(all(slot["firstBeat"] in beat_id_set and beat_index[slot["firstBeat"]] >= beat_index[obj["firstBeat"]] for slot in obj["textSlots"]), f"对象 {obj['id']} 的文字槽位 firstBeat 无效或早于对象出现。")
    require(len(set(reveal_ids)) == len(reveal_ids), "内部信息 id 不能重复。")

    active = set()
    current_states = {}
    first_appearance = {}
    previous_at = segment["startSec"] - 1e-9
    previous_boxes = {}
    for index, beat in enumerate(beats):
        at = beat.get("at")
        require(isinstance(at, (int, float)) and previous_at < at < segment["endSec"], f"视觉 beat {beat.get('id')} 的 at 必须严格递增并落在片段内。")
        settle = beat.get("settleSec")
        require(isinstance(settle, (int, float)) and 0 <= settle <= 2, f"视觉 beat {beat['id']} 需要 0–2 秒 settleSec。")
        next_at = beats[index + 1].get("at") if index + 1 < len(beats) else segment["endSec"]
        require(isinstance(next_at, (int, float)) and at + settle <= next_at + 1e-6, f"视觉 beat {beat['id']} 尚未完成，下一个 beat 已开始。")
        require(beat.get("anchorType", "speech") in {"speech", "sentence-end"} and isinstance(beat.get("spoken"), str) and beat["spoken"].strip(), f"视觉 beat {beat['id']} 缺少可核对的语音或句末锚点。")
        require(isinstance(beat.get("action"), str) and beat["action"].strip(), f"视觉 beat {beat['id']} 缺少具体动作。")
        continuity = beat.get("continuity")
        require(continuity in {"continue", "handoff", "reset"}, f"视觉 beat {beat['id']} 需要声明 continue、handoff 或 reset。")
        if index and continuity == "reset":
            require(beat.get("anchorType") == "sentence-end", f"视觉 beat {beat['id']} 只能在句末重置画面。")
        hero = beat.get("hero")
        require(hero in by_id, f"视觉 beat {beat['id']} 的 hero 不存在：{hero}")
        appears, exits = beat.get("appears"), beat.get("exits")
        require(isinstance(appears, list) and isinstance(exits, list), f"视觉 beat {beat['id']} 需要 appears 和 exits。")
        require(len(set(appears)) == len(appears) and len(set(exits)) == len(exits) and not set(appears).intersection(exits), f"视觉 beat {beat['id']} 的 appears/exits 冲突或重复。")
        require(all(obj_id in by_id for obj_id in appears + exits), f"视觉 beat {beat['id']} 引用了未知对象。")
        require(sum(by_id[obj_id]["role"] != "environment" for obj_id in appears) <= 1, f"视觉 beat {beat['id']} 同时引入多个前景对象；请按口播逐项出现。")
        require(all(obj_id not in active for obj_id in appears), f"视觉 beat {beat['id']} 重复挂载仍在画面的对象。")
        require(all(obj_id in active for obj_id in exits), f"视觉 beat {beat['id']} 退出了尚未出现的对象。")
        before = set(active)
        active.difference_update(exits)
        active.update(appears)
        for obj_id in appears:
            current_states[obj_id] = by_id[obj_id]["states"][0]
        require(hero in before or hero in active, f"视觉 beat {beat['id']} 的主角没有出现在动作前后。")
        require(all(obj_id in active for obj_id in reveals_by_beat.get(beat["id"], [])), f"视觉 beat {beat['id']} 揭示了尚未在画面的对象内部信息。")
        for obj_id in appears:
            first_appearance.setdefault(obj_id, beat["id"])

        semantic = beat.get("semantic")
        require(isinstance(semantic, dict) and set(semantic) == {"kind", "subject", "verb", "object", "result"}, f"视觉 beat {beat['id']} 需要完整 semantic。")
        require(semantic["kind"] in {"introduce", "observe", "transform", "transfer", "hold", "clear"}, f"视觉 beat {beat['id']} 的 semantic.kind 无效。")
        require(semantic["subject"] == hero and isinstance(semantic["verb"], str) and semantic["verb"].strip() and isinstance(semantic["result"], str) and semantic["result"].strip(), f"视觉 beat {beat['id']} 的语义主语必须等于 hero，并写清动词和结果。")
        require(semantic["object"] is None or semantic["object"] in before or semantic["object"] in active, f"视觉 beat {beat['id']} 的语义宾语不在动作前后画面中。")
        state_changes = beat.get("stateChanges")
        require(isinstance(state_changes, list), f"视觉 beat {beat['id']} 需要 stateChanges；没有状态改变时使用空数组。")
        changed_objects = set()
        for change in state_changes:
            require(isinstance(change, dict) and set(change) == {"object", "from", "to", "evidence"}, f"视觉 beat {beat['id']} 的 stateChanges 项格式无效。")
            obj_id = change["object"]
            require(obj_id in before or obj_id in active, f"视觉 beat {beat['id']} 改变了不在画面中的对象。")
            require(obj_id not in changed_objects and current_states.get(obj_id) == change["from"] and change["to"] in by_id[obj_id]["states"] and change["to"] != change["from"], f"视觉 beat {beat['id']} 的 {obj_id} 状态前后无效。")
            require(isinstance(change["evidence"], str) and change["evidence"].strip(), f"视觉 beat {beat['id']} 的状态变化需要可见证据。")
            current_states[obj_id] = change["to"]
            changed_objects.add(obj_id)
        transition = beat.get("transition")
        require(transition is None or isinstance(transition, dict) and set(transition) == {"trigger", "processKind", "processEvidence", "settled"}, f"视觉 beat {beat['id']} 的 transition 必须为 null 或完整的触发—过程—落定证据。")
        needs_transition = semantic["kind"] in {"transform", "transfer"} or bool(state_changes)
        require(not needs_transition or transition is not None, f"视觉 beat {beat['id']} 发生状态或对象流转，却没有声明触发—过程—落定。")
        transition_kind = None
        if transition is not None:
            transition_kind = transition["processKind"]
            valid_process_kinds = {"move", "morph", "accumulate", "deplete", "open-close", "split-merge", "fill-drain", "block-release", "propagate", "progressive-reveal"}
            require(all(isinstance(transition.get(key), str) and transition[key].strip() for key in ("trigger", "processKind", "processEvidence", "settled")), f"视觉 beat {beat['id']} 的 transition 四项都必须是非空文字。")
            require(transition_kind in valid_process_kinds, f"视觉 beat {beat['id']} 的过程必须是可见机制动作；淡入淡出、换标签或瞬间替换不能算状态变化。")
            require(settle >= .25, f"视觉 beat {beat['id']} 的状态过程少于 0.25 秒，无法形成可辨认的中间态。")
        if semantic["kind"] == "introduce":
            require(bool(appears or reveals_by_beat.get(beat["id"])), f"视觉 beat {beat['id']} 声称引入对象，却没有出现或内部揭示。")
        if semantic["kind"] == "transform":
            require(bool(state_changes) and semantic["subject"] in changed_objects, f"视觉 beat {beat['id']} 的 transform 必须改变主角自身状态，不能凭空新增结果对象。")
        if semantic["kind"] == "clear":
            require(bool(exits), f"视觉 beat {beat['id']} 的 clear 必须退出对象。")

        foreground = {obj_id for obj_id in active if by_id[obj_id]["role"] != "environment"}
        require(len(foreground) <= max_foreground, f"视觉 beat {beat['id']} 同时出现 {len(foreground)} 个前景对象，超过上限 {max_foreground}。")
        attention = beat.get("attention")
        require(isinstance(attention, dict) and attention.get("mode") in {"guided", "isolated"}, f"视觉 beat {beat['id']} 需要 guided 或 isolated 注意力模式。")
        support = attention.get("support")
        require(isinstance(support, list) and len(support) <= 3 and len(set(support)) == len(support) and hero not in support and all(obj_id in by_id for obj_id in support), f"视觉 beat {beat['id']} 的注意力 support 无效。")
        visible_for_focus = active if hero in active else before
        focus_objects = {hero, *support}
        require(focus_objects.issubset(visible_for_focus), f"视觉 beat {beat['id']} 的主角或辅助对象不在画面中。")
        if attention["mode"] == "isolated":
            require(visible_for_focus == focus_objects, f"视觉 beat {beat['id']} 要求焦点隔离，但仍有旧对象竞争注意力：" + ", ".join(sorted(visible_for_focus - focus_objects)))
        contains = beat.get("contains")
        require(isinstance(contains, list), f"视觉 beat {beat['id']} 需要 contains；没有容器关系时使用空数组。")
        containment_pairs = set()
        for relation in contains:
            require(isinstance(relation, dict) and set(relation) == {"container", "child", "paddingPx"}, f"视觉 beat {beat['id']} 的 contains 项格式无效。")
            container, child, padding = relation["container"], relation["child"], relation["paddingPx"]
            require(container in active and child in active and container != child, f"视觉 beat {beat['id']} 的容器或子对象不在画面中。")
            require(by_id[container]["role"] == "environment", f"视觉 beat {beat['id']} 的容器 {container} 必须是 environment。")
            require(isinstance(padding, (int, float)) and padding >= 0, f"视觉 beat {beat['id']} 的容器留白必须是非负数。")
            require((container, child) not in containment_pairs, f"视觉 beat {beat['id']} 的容器关系重复。")
            containment_pairs.add((container, child))
        relations = beat.get("relations")
        require(isinstance(relations, list), f"视觉 beat {beat['id']} 需要 relations；单对象画面使用空数组。")
        valid_relation_types = {"contains", "connected-to", "points-to", "attached-to", "inside", "modifies", "stores-in", "contrasts-with", "receives", "persists-beside", "assigned-to"}
        relation_pairs = set(containment_pairs)
        for relation in relations:
            require(isinstance(relation, dict) and set(relation) == {"from", "type", "to", "evidence"}, f"视觉 beat {beat['id']} 的 relations 项格式无效。")
            left, right = relation["from"], relation["to"]
            require((left in before or left in active) and (right in before or right in active), f"视觉 beat {beat['id']} 的关系对象不在动作前后画面中。")
            require(left != right and relation["type"] in valid_relation_types and isinstance(relation["evidence"], str) and relation["evidence"].strip(), f"视觉 beat {beat['id']} 的关系需要不同对象、有效类型和可见证据。")
            relation_pairs.add((left, right))
        for support_id in support:
            require((hero, support_id) in relation_pairs or (support_id, hero) in relation_pairs, f"视觉 beat {beat['id']} 的辅助对象 {support_id} 没有与主角建立可见关系。")
        layout = beat.get("layout")
        require(isinstance(layout, dict) and set(layout) == active, f"视觉 beat {beat['id']} 的 layout 必须精确覆盖当前所有可见对象。")
        boxes = {}
        for obj_id, box in layout.items():
            require(isinstance(box, list) and len(box) == 4 and all(isinstance(value, (int, float)) for value in box), f"视觉 beat {beat['id']} 的 {obj_id} 边界框无效。")
            x, y, width, height = box
            require(width > 0 and height > 0 and x >= 0 and y >= 205 and x + width <= 1920 and y + height <= 880, f"视觉 beat {beat['id']} 的 {obj_id} 超出主画面安全区。")
            boxes[obj_id] = box
        for relation in contains:
            cx, cy, cw, ch = boxes[relation["container"]]
            x, y, width, height = boxes[relation["child"]]
            padding = relation["paddingPx"]
            require(x >= cx + padding and y >= cy + padding and x + width <= cx + cw - padding and y + height <= cy + ch - padding, f"视觉 beat {beat['id']} 的 {relation['child']} 没有完整放入 {relation['container']}，或容器留白不足 {padding}px。")
        ids = sorted(boxes)
        for left_index, left_id in enumerate(ids):
            lx, ly, lw, lh = boxes[left_id]
            for right_id in ids[left_index + 1:]:
                if (left_id, right_id) in containment_pairs or (right_id, left_id) in containment_pairs:
                    continue
                rx, ry, rw, rh = boxes[right_id]
                separated = lx + lw + min_gap <= rx or rx + rw + min_gap <= lx or ly + lh + min_gap <= ry or ry + rh + min_gap <= ly
                require(separated, f"视觉 beat {beat['id']} 的 {left_id} 与 {right_id} 重叠或间距不足 {min_gap}px。")

        expected_text = {}
        for obj_id in active:
            parent = boxes[obj_id]
            px, py, pw, ph = parent
            for slot in by_id[obj_id]["textSlots"]:
                if beat_index[slot["firstBeat"]] <= index:
                    sx, sy, sw, sh = slot["box"]
                    expected_text[f"{obj_id}.{slot['id']}"] = ([px + sx * pw, py + sy * ph, sw * pw, sh * ph], slot["align"], obj_id)
        text_layout = beat.get("textLayout")
        require(isinstance(text_layout, dict) and set(text_layout) == set(expected_text), f"视觉 beat {beat['id']} 的 textLayout 必须精确覆盖当前已揭示文字。")
        text_boxes = {}
        for text_id, text_box in text_layout.items():
            require(isinstance(text_box, list) and len(text_box) == 4 and all(isinstance(value, (int, float)) for value in text_box), f"视觉 beat {beat['id']} 的文字 {text_id} 边界框无效。")
            x, y, width, height = text_box
            sx, sy, sw, sh = expected_text[text_id][0]
            require(width > 0 and height > 0 and x >= sx and y >= sy and x + width <= sx + sw and y + height <= sy + sh, f"视觉 beat {beat['id']} 的文字 {text_id} 超出预留槽位。")
            if expected_text[text_id][1] == "center":
                require(abs((x + width / 2) - (sx + sw / 2)) <= 12 and abs((y + height / 2) - (sy + sh / 2)) <= 12, f"视觉 beat {beat['id']} 的文字 {text_id} 没有在槽位内居中。")
            else:
                require(abs(x - sx) <= 12 and abs((y + height / 2) - (sy + sh / 2)) <= 12, f"视觉 beat {beat['id']} 的文字 {text_id} 没有对齐槽位起点。")
            text_boxes[text_id] = text_box

        motions = beat.get("motions")
        require(isinstance(motions, list), f"视觉 beat {beat['id']} 需要 motions；没有位移时使用空数组。")
        motion_objects = [motion.get("object") for motion in motions if isinstance(motion, dict)]
        require(len(motion_objects) == len(motions) and all(isinstance(obj_id, str) for obj_id in motion_objects) and len(set(motion_objects)) == len(motion_objects), f"视觉 beat {beat['id']} 的 motion 对象无效或重复。")
        if semantic["kind"] == "transfer":
            require(any(motion.get("kind") in {"store", "merge", "handoff"} for motion in motions), f"视觉 beat {beat['id']} 的 transfer 必须有存入、合并或交接 motion。")
        if transition_kind == "move":
            require(bool(motions), f"视觉 beat {beat['id']} 声明 move 过程，却没有 motion 路径。")
        elif transition_kind == "morph":
            require(bool(state_changes), f"视觉 beat {beat['id']} 声明 morph 过程，却没有主对象状态变化。")
        elif transition_kind == "progressive-reveal":
            require(bool(reveals_by_beat.get(beat["id"])), f"视觉 beat {beat['id']} 声明 progressive-reveal，却没有内部信息逐步揭示。")
        elif transition_kind is not None:
            require(bool(state_changes or motions or reveals_by_beat.get(beat["id"])), f"视觉 beat {beat['id']} 声明了过程，却没有任何可执行变化。")
        if semantic["kind"] not in {"hold", "clear"}:
            require(bool(appears or reveals_by_beat.get(beat["id"]) or state_changes or motions), f"视觉 beat {beat['id']} 的动词没有任何可见证据。")
        moved = {obj_id for obj_id in active.intersection(before) if obj_id in previous_boxes and boxes[obj_id] != previous_boxes[obj_id]}
        require(moved.issubset(set(motion_objects)), f"视觉 beat {beat['id']} 有对象改变位置却没有 motion：" + ", ".join(sorted(moved - set(motion_objects))))
        for motion in motions:
            require(set(motion) == {"object", "kind", "destination", "result", "path", "landingZone"}, f"视觉 beat {beat['id']} 的 motion 项格式无效。")
            obj_id, kind, destination = motion["object"], motion["kind"], motion["destination"]
            require(isinstance(destination, str), f"视觉 beat {beat['id']} 的 motion destination 必须是对象 id、viewport 或 offscreen。")
            require(obj_id in before or obj_id in active, f"视觉 beat {beat['id']} 的 motion 对象不在动作前后画面中。")
            require(kind in valid_motion_kinds and isinstance(motion["result"], str) and motion["result"].strip(), f"视觉 beat {beat['id']} 的 motion 需要明确 kind 与 result。")
            path = motion["path"]
            require(isinstance(path, list) and len(path) >= 3, f"视觉 beat {beat['id']} 的 motion 至少需要起点、中点、终点三个采样框。")
            for path_index, path_box in enumerate(path):
                require(isinstance(path_box, list) and len(path_box) == 4 and all(isinstance(value, (int, float)) for value in path_box), f"视觉 beat {beat['id']} 的 motion 路径框无效。")
                x, y, width, height = path_box
                require(width > 0 and height > 0, f"视觉 beat {beat['id']} 的 motion 路径框尺寸无效。")
                if 0 < path_index < len(path) - 1:
                    require(x >= 0 and y >= 205 and x + width <= 1920 and y + height <= 880, f"视觉 beat {beat['id']} 的 motion 中途越出主画面安全区。")
            if obj_id in previous_boxes:
                require(path[0] == previous_boxes[obj_id], f"视觉 beat {beat['id']} 的 motion 起点与上一稳定帧不一致。")
            elif obj_id in appears:
                x, y, width, height = path[0]
                require(x + width <= 0 or x >= 1920 or y + height <= 205 or y >= 880, f"视觉 beat {beat['id']} 的新对象若声明进场 motion，起点必须在画外。")
            if obj_id in active:
                require(path[-1] == boxes[obj_id], f"视觉 beat {beat['id']} 的 motion 终点与本 beat 稳定帧不一致。")
            landing = motion["landingZone"]
            if destination in {"viewport", "offscreen"}:
                require(landing is None, f"视觉 beat {beat['id']} 的 viewport/offscreen motion 不应声明 landingZone。")
                if destination == "offscreen":
                    x, y, width, height = path[-1]
                    require(obj_id in exits and (x + width <= 0 or x >= 1920 or y + height <= 205 or y >= 880), f"视觉 beat {beat['id']} 的 offscreen motion 必须让退出对象完全离屏。")
            else:
                require(destination in active and destination in boxes and destination != obj_id, f"视觉 beat {beat['id']} 的 motion 目的地必须是当前可见的有界对象。")
                require(kind in by_id[destination]["receptors"], f"视觉 beat {beat['id']} 的目的地 {destination} 未声明可承接 {kind}。")
                require(kind in {"store", "merge", "handoff"} and obj_id in exits, f"视觉 beat {beat['id']} 向对象移动时必须明确 store/merge/handoff，并卸载来源对象。")
                require(isinstance(landing, list) and len(landing) == 4 and all(isinstance(value, (int, float)) for value in landing), f"视觉 beat {beat['id']} 的语义目的地需要 landingZone。")
                dx, dy, dw, dh = boxes[destination]
                lx, ly, lw, lh = landing
                require(lw > 0 and lh > 0 and lx >= dx and ly >= dy and lx + lw <= dx + dw and ly + lh <= dy + dh, f"视觉 beat {beat['id']} 的 landingZone 必须完整位于目的地内。")
                x, y, width, height = path[-1]
                require(x >= lx and y >= ly and x + width <= lx + lw and y + height <= ly + lh, f"视觉 beat {beat['id']} 的来源对象没有完整落入 landingZone。")
                for text_id, text_box in text_boxes.items():
                    if expected_text[text_id][2] == destination:
                        tx, ty, tw, th = text_box
                        require(lx + lw <= tx or tx + tw <= lx or ly + lh <= ty or ty + th <= ly, f"视觉 beat {beat['id']} 的 landingZone 遮挡了目的地文字 {text_id}。")
            obstacle_boxes = {**previous_boxes, **boxes}
            for path_box in path[1:-1]:
                x, y, width, height = path_box
                for other_id, other_box in obstacle_boxes.items():
                    if other_id in {obj_id, destination}:
                        continue
                    ox, oy, ow, oh = other_box
                    require(x + width <= ox or ox + ow <= x or y + height <= oy or oy + oh <= y, f"视觉 beat {beat['id']} 的 {obj_id} 在运动途中遮挡了 {other_id}。")

        exit_modes = beat.get("exitModes", {})
        require(isinstance(exit_modes, dict) and set(exit_modes) == set(exits), f"视觉 beat {beat['id']} 必须为每个退出对象声明 exitModes。")
        require(all(mode in {"offscreen", "collapse", "fragment", "transform"} for mode in exit_modes.values()), f"视觉 beat {beat['id']} 存在只淡不退或含义不明的退出方式。")
        require(all(obj_id in motion_objects for obj_id, mode in exit_modes.items() if mode in {"offscreen", "transform"}), f"视觉 beat {beat['id']} 的离屏或变形退出缺少可检查路径。")
        if index and continuity == "reset":
            require(before.issubset(set(exits)), f"视觉 beat {beat['id']} 声明 reset，却仍保留上一画面的对象。")
        previous_boxes = boxes
        previous_at = at
        for obj_id in exits:
            current_states.pop(obj_id, None)

    require(all(first_appearance.get(obj_id) == obj["firstBeat"] for obj_id, obj in by_id.items()), "对象 firstBeat 必须等于第一次真实出现的 beat。")
    require(not active, "片段结束前仍有视觉对象未完整退出：" + ", ".join(sorted(active)))
    return {"objects": len(objects), "beats": len(beats), "clear_end": True}


def validate_rendered_evidence(table, qa, base, timeline=None):
    """Require reviewed cue/settled frames and transition mid-frames for every beat."""
    validate_visual_state_table(table)
    require(isinstance(qa, dict) and qa.get("version") == 2 and isinstance(qa.get("frames"), list) and isinstance(qa.get("learnedGateChecks"), list), "rendered-qa.version 必须是 2，并包含 frames 与 learnedGateChecks。")
    beat_ids = {beat["id"] for beat in table["beats"]}
    required = {(beat["id"], phase) for beat in table["beats"] for phase in ("cue", "settled")}
    required.update((beat["id"], "mid") for beat in table["beats"] if beat["motions"] or beat.get("transition") is not None)
    covered = set()
    for frame in qa["frames"]:
        require(isinstance(frame, dict) and set(frame) == {"beat", "phase", "image", "reviewed", "note"}, "rendered-qa 的 frame 项格式无效。")
        key = (frame["beat"], frame["phase"])
        require(frame["beat"] in beat_ids and frame["phase"] in {"cue", "mid", "settled"} and key not in covered, "rendered-qa 存在未知或重复的 beat/phase。")
        image = (base / frame["image"]).resolve()
        require(image.is_file() and image.suffix.lower() in {".png", ".jpg", ".jpeg"}, f"rendered-qa 缺少证据图片：{frame['image']}")
        require(frame["reviewed"] is True and isinstance(frame["note"], str) and frame["note"].strip(), f"rendered-qa 的 {frame['beat']}/{frame['phase']} 尚未完成人工或 Agent 目视检查。")
        covered.add(key)
    require(required.issubset(covered), "rendered-qa 未覆盖：" + ", ".join(f"{beat}/{phase}" for beat, phase in sorted(required - covered)))
    profile_id = table.get("profile", "knowledge-explainer")
    qa_rule_ids = {item["id"] for item in load_learning_rules(profile_id) if "qa-gate" in item["enforcement"].split("+")}
    checks = qa["learnedGateChecks"]
    check_ids = [check.get("id") for check in checks if isinstance(check, dict)]
    require(len(check_ids) == len(checks) and len(check_ids) == len(set(check_ids)) and set(check_ids) == qa_rule_ids, "learnedGateChecks 必须精确覆盖所有 qa-gate 经验。")
    allowed_evidence = {f"{beat}/{phase}" for beat, phase in covered} | {"full-playback", "source-review"}
    for check in checks:
        require(set(check) == {"id", "passed", "evidence", "note"}, f"经验检查 {check.get('id')} 格式无效。")
        evidence = check["evidence"]
        require(check["passed"] is True and isinstance(evidence, list) and evidence and all(isinstance(item, str) and item in allowed_evidence for item in evidence) and isinstance(check["note"], str) and check["note"].strip(), f"经验检查 {check['id']} 未通过或缺少可追溯证据。")
    sound_count = len(timeline.get("sounds", {})) if isinstance(timeline, dict) else 0
    if sound_count:
        review = qa.get("soundReview")
        require(isinstance(review, dict) and set(review) == {"reviewed", "voiceClear", "semanticMatch", "noClipping", "evidence", "note"}, "使用音效时，rendered-qa 缺少完整 soundReview。")
        require(review["reviewed"] is True and review["voiceClear"] is True and review["semanticMatch"] is True and review["noClipping"] is True, "音效未通过口播清晰度、语义匹配或削波检查。")
        require(review["evidence"] == ["full-playback"] and isinstance(review["note"], str) and review["note"].strip(), "soundReview 必须记录完整播放证据和具体结论。")
    return {"frames": len(covered), "learnedGateChecks": len(checks), "soundEffects": sound_count, "coverage": "complete"}


def make_props(story, alignment, duration, rate):
    narration = validate_story(story)
    validate_speech_alignment(alignment, narration_text=narration, audio_duration_seconds=duration)
    beats = materialize_phrase_beats(scene_id=story["id"], narration_text=narration, phrase_beats=tuple({"id": f"{story['id']}:{b['id']}", "text": b["text"]} for b in story["beats"]), alignment=alignment)
    cues = {b["id"].removeprefix(story["id"] + ":"): b["start_ms"] / rate + 600 for b in beats}
    aligned_text = "".join(content(s.text) for s in alignment.spans)
    for name, phrase in story.get("anchors", {}).items():
        position, cursor = aligned_text.index(content(phrase)), 0
        for span in alignment.spans:
            cursor += len(content(span.text))
            if cursor > position:
                cues[name] = span.start_ms / rate + 600
                break
    captions = [{"text": b["text"], "startMs": b["start_ms"] / rate + 600, "endMs": b["end_ms"] / rate + 600, "timestampMs": None, "confidence": None} for b in beats]
    return {"title": story["title"], "audioSrc": "narration.mp3", "leadFrames": 18, "playbackRate": rate, "durationInFrames": math.ceil((duration / rate + .6 + 1.6) * 30), "cues": cues, "captions": captions}


def release_candidate(state_path, world_path, timeline_path, props_path, candidate, qa_path, frame_out, final):
    """Legacy declarations remain readable, but are no longer a release path."""
    raise DirectorError("旧版声明式 Gate 已停用交付：请使用 score.json v2 和受控 score-render；不能仅凭 reviewed=true 生成成片。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    for command in ("score-check", "score-render", "score-review", "score-release"):
        executable = sub.add_parser(command)
        executable.add_argument("--score", type=Path, required=True)
        executable.add_argument("--props", type=Path, required=True)
        if command != "score-check":
            executable.add_argument("--candidate", type=Path, required=True)
        if command == "score-release":
            executable.add_argument("--qa", type=Path, required=True)
            executable.add_argument("--final", type=Path, required=True)
    sample = sub.add_parser("sample")
    sample.add_argument("--text", default=SAMPLE)
    sample.add_argument("--out", type=Path, required=True)
    approve = sub.add_parser("approve-voice")
    approve.add_argument("--evidence", type=Path, required=True)
    approve.add_argument("--note", required=True)
    approve.add_argument("--remember-default", action="store_true")
    approve.add_argument("--label")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--story", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    gate = sub.add_parser("gate")
    gate.add_argument("--state", type=Path, required=True)
    qa_gate = sub.add_parser("qa-gate")
    qa_gate.add_argument("--state", type=Path, required=True)
    qa_gate.add_argument("--timeline", type=Path, required=True)
    qa_gate.add_argument("--qa", type=Path, required=True)
    world_gate = sub.add_parser("world-gate")
    world_gate.add_argument("--world", type=Path, required=True)
    world_gate.add_argument("--timeline", type=Path, required=True)
    world_gate.add_argument("--props", type=Path, required=True)
    frame_gate = sub.add_parser("frame-gate")
    frame_gate.add_argument("--world", type=Path, required=True)
    frame_gate.add_argument("--timeline", type=Path, required=True)
    frame_gate.add_argument("--props", type=Path, required=True)
    frame_gate.add_argument("--video", type=Path, required=True)
    frame_gate.add_argument("--out", type=Path, required=True)
    release = sub.add_parser("release")
    release.add_argument("--state", type=Path, required=True)
    release.add_argument("--world", type=Path, required=True)
    release.add_argument("--timeline", type=Path, required=True)
    release.add_argument("--props", type=Path, required=True)
    release.add_argument("--candidate", type=Path, required=True)
    release.add_argument("--qa", type=Path, required=True)
    release.add_argument("--frame-out", type=Path, required=True)
    release.add_argument("--final", type=Path, required=True)
    args = parser.parse_args()
    if args.command.startswith("score-"):
        if args.command == "score-check":
            data = executable_score.compile_file(args.score, args.props)
            result = {"gate": "pass", "frames": len(data["frames"]), "actions": len(data["actions"]), "sounds": len(data["sounds"]), "qualityStatus": "not-reviewed"}
        elif args.command == "score-render":
            result = executable_score.render(args.score, args.props, args.candidate)
        elif args.command == "score-review":
            result = executable_score.review_pack(args.score, args.props, args.candidate)
        else:
            result = executable_score.release(args.score, args.props, args.candidate, args.qa, args.final)
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.command == "release":
        print(json.dumps(release_candidate(args.state, args.world, args.timeline, args.props, args.candidate, args.qa, args.frame_out, args.final), ensure_ascii=False))
        return
    if args.command in {"world-gate", "frame-gate"}:
        world = read_json(args.world)
        timeline = read_json(args.timeline)
        props = read_json(args.props)
        result = validate_world_timeline(world, timeline, props)
        result.update(validate_sound_assets(timeline, args.timeline.parent))
        if args.command == "frame-gate":
            result.update(run_frame_gate(world, timeline, props, args.video, args.out))
        print(json.dumps({"gate": "pass", **result}, ensure_ascii=False))
        return
    if args.command in {"gate", "qa-gate"}:
        require(args.state.is_file(), "缺少 visual-state-table.json。")
        table = json.loads(args.state.read_text())
        result = validate_visual_state_table(table)
        if args.command == "qa-gate":
            require(args.qa.is_file(), "缺少 rendered-qa.json。")
            result.update(validate_rendered_evidence(table, json.loads(args.qa.read_text()), args.qa.parent, read_json(args.timeline)))
        print(json.dumps({"gate": "pass", **result}, ensure_ascii=False))
        return
    cfg = load_config(args.env_file)
    for executable in ("ffmpeg", "ffprobe"):
        require(shutil.which(executable), f"缺少 {executable}。")
    if args.command == "preflight":
        credential(cfg)  # Resolve only in memory; never print or persist credentials.
        for executable in ("node", "pnpm"):
            require(shutil.which(executable), f"缺少 {executable}。")
        require((ROOT / "node_modules/.bin/remotion").is_file(), "缺少独立 Remotion 依赖，请在 Skill 目录运行 pnpm install --frozen-lockfile。")
        print(json.dumps({"ready": True, "standalone": True, "profile": profile(cfg), "voice_profile_hash": digest(profile(cfg)), "voice_approved": voice_approved(cfg), "voice_default": voice_is_default(cfg)}, ensure_ascii=False))
    elif args.command == "approve-voice":
        approve_voice(cfg, args.evidence, args.note, remember_default=args.remember_default, preference_label=args.label)
        print(json.dumps({"voice_approved": True, "voice_default": voice_is_default(cfg), "profile_hash": digest(profile(cfg))}))
    elif args.command == "sample":
        text = normalize_speech(args.text)
        require(0 < len(text) <= 140 and content(text) and "<" not in text and ">" not in text, "试听文本应为 1–140 字的纯文本。")
        _, duration = synthesize(cfg, text, args.out)
        audition = args.out / "audition.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(args.out / "narration.mp3"), "-af", f"atempo={cfg['PLAYBACK_RATE']}", "-c:a", "pcm_s16le", "-y", str(audition)], check=True)
        print(json.dumps({"audition": str(audition.resolve()), "seconds": round(duration / cfg["PLAYBACK_RATE"], 2), "voice_approved": voice_approved(cfg), "voice_default": voice_is_default(cfg)}, ensure_ascii=False))
    else:
        story = json.loads(args.story.read_text())
        narration = validate_story(story)
        require(voice_approved(cfg), "当前声音配置尚未确认。先 sample 并给用户试听，收到确认后再 approve-voice。")
        alignment, duration = synthesize(cfg, narration, args.out)
        props = make_props(story, alignment, duration, cfg["PLAYBACK_RATE"])
        write_json(args.out / "storyboard.json", story)
        write_json(args.out / "props.json", props)
        print(json.dumps({"props": str((args.out / 'props.json').resolve()), "video_seconds": props["durationInFrames"] / 30, "captions": len(props["captions"])}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (DirectorError, VisualError, executable_score.ScoreError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
    except Exception as error:
        # Provider/DB exceptions may include connection details; expose only safe fields.
        print(getattr(error, "safe_message", f"执行失败（{type(error).__name__}），检查本机配置或输入格式；不要打印凭证。"), file=sys.stderr)
        sys.exit(1)
