"""Offline checks using a real, previously synthesized short Tencent sample."""
import copy
import json
import tempfile
import sys
import wave
from base64 import b64encode
from pathlib import Path
from unittest.mock import patch

import director as d
import httpx
import router as r
import tencent
import visual as v
from speech import ProviderCallError, SpeechAlignmentError, SpeechSynthesisRequest


def rejects(action):
    try:
        action()
    except (d.DirectorError, r.RouterError, v.VisualError, ProviderCallError, SpeechAlignmentError):
        return
    raise AssertionError("Expected explicit refusal")


def main():
    fixture = json.loads((d.ROOT / "assets/test-voice.json").read_text())
    voice = fixture["profile"]
    cfg = {**voice, "TTS_SECRET_ID": "test-id", "TTS_SECRET_KEY": "test-key"}
    assert d.credential(cfg) == "test-id:test-key"
    assert not any(name == "app" or name.startswith("app.") for name in sys.modules)
    story = {"id": "smoke", "title": "试听", "focus": {"question": "本次对照是否能消除交界停顿", "viewer_takeaway": "相同音色下只比较交界空格", "visual_subject": "同一句口播"}, "sources": [{"id": "sample", "excerpt": "这是一句用于已完成 TTS 对照试验的测试口播，不声称产品能力。"}], "beats": [{"id": "line", "text": "让 AI 替你做事", "visual": "讲到 AI 时才展开主体", "source_refs": ["sample"]}], "anchors": {"ai_word": "AI"}}
    assert d.normalize_speech("用 AI Agent 帮你") == "用AI Agent帮你"
    assert d.normalize_speech("Let AI do things\n中文 换行") == "Let AI do things\n中文 换行"
    assert d.normalize_speech(d.normalize_speech("让 AI 替你做事")) == "让AI替你做事"
    assert d.validate_story(story) == fixture["text"]
    assert d.validate_story(json.loads((d.ROOT / "assets/storyboard.example.json").read_text()))
    for field, value in [("sources", []), ("beats", story["beats"] * 2), ("anchors", {"line": "AI"})]:
        bad = {**story, field: value}
        rejects(lambda: d.validate_story(bad))
    bad = copy.deepcopy(story)
    bad["beats"][0]["source_refs"] = ["missing"]
    rejects(lambda: d.validate_story(bad))
    check_visual_gate()
    check_world_gate()
    check_router()
    with tempfile.TemporaryDirectory(prefix="video-director-check-") as temp:
        temp = Path(temp)
        env = temp / ".env"
        env.write_text("\n".join(f'{key}="{value}"' for key, value in cfg.items() if key.startswith("TTS_") or key == "PLAYBACK_RATE"))
        env.chmod(0o600)
        loaded = d.load_config(env)
        assert d.profile(loaded) == d.profile(cfg)
        env.chmod(0o644)
        rejects(lambda: d.load_config(env))
        env.chmod(0o600)
        sample = temp / "narration.mp3"
        sample.write_bytes((d.ROOT / "assets/test-voice.mp3").read_bytes())
        assert d.file_hash(sample) == fixture["audio_sha256"]
        approval = temp / ".local/voice-approval.json"
        voice_default = temp / ".local/voice-default.json"
        assert not d.voice_approved(cfg, approval)
        rejects(lambda: d.approve_voice(cfg, sample, "Missing explicit default label", approval, remember_default=True, preference_path=voice_default))
        rejects(lambda: d.approve_voice(cfg, sample, "Label without default scope", approval, preference_label="测试音色", preference_path=voice_default))
        d.approve_voice(cfg, sample, "Synthetic test of approval handling, not a user decision", approval)
        assert d.voice_approved(cfg, approval)
        assert not d.voice_is_default(cfg, voice_default)
        d.approve_voice(cfg, sample, "Synthetic test of an explicit default choice", approval, remember_default=True, preference_label="测试音色", preference_path=voice_default)
        assert d.voice_is_default(cfg, voice_default)
        assert not d.voice_approved({**cfg, "TTS_VOICE_ID": "1002"}, approval)
        assert not d.voice_is_default({**cfg, "TTS_VOICE_ID": "1002"}, voice_default)
        assert not d.voice_approved({**cfg, "PLAYBACK_RATE": 1.0}, approval)
        assert not d.voice_approved({**cfg, "TTS_SPEED": 1.0}, approval)
        assert "test-key" not in approval.read_text()
        assert "test-key" not in voice_default.read_text()
        metadata = {"fingerprint": d.digest({"text": fixture["text"], "profile": d.profile(cfg)}), "profile": d.profile(cfg), "audio_sha256": fixture["audio_sha256"], "alignment": fixture["alignment"]}
        d.write_json(temp / "alignment.json", metadata)
        # A cache hit must neither load credentials nor call any speech provider.
        with patch.object(d, "credential", side_effect=AssertionError("No credentials in an offline check")), patch.object(d.TencentSpeechGateway, "synthesize", side_effect=AssertionError("No paid call in an offline check")):
            alignment, duration = d.synthesize(cfg, fixture["text"], temp)
            props = d.make_props(story, alignment, duration, cfg["PLAYBACK_RATE"])
            assert props["captions"][0]["text"] == "让 AI 替你做事"
            assert props["cues"]["ai_word"] == 495 / .92 + 600
            assert props["captions"][0]["endMs"] == 1562 / .92 + 600
            assert props["durationInFrames"] == 124
            rejects(lambda: d.synthesize(cfg, "不同的一句", temp))
            sample.write_bytes(b"corrupted")
            rejects(lambda: d.synthesize(cfg, fixture["text"], temp))
        assert "test-key" not in (temp / "alignment.json").read_text()
    check_provider(fixture)
    print("PASS: profile/question/shot/holdout routers, visual-state/semantic/relation/state/process/motion/text/sound/evidence/release gates, credentials/config, approval invalidation, native alignment, caption/anchor timing, cache integrity, Tencent request/signing/response boundaries, long narration; no provider calls")


def check_router():
    request = json.loads((d.ROOT / "assets/route-request.example.json").read_text())
    with tempfile.TemporaryDirectory(prefix="video-director-router-") as temp:
        default_path = Path(temp) / "voice-default.json"
        result = r.route(request, default_path)
        assert [segment["shotMode"] for segment in result["segments"]] == ["establish", "camera-move", "handoff"]
        assert result["profileMaturity"] == "tuning" and result["questions"][0]["id"] == "voice"
        assert result["resolvedChoices"][0]["resolvedBy"] == "preference"

        selected = copy.deepcopy(request)
        selected["choicePoints"][1].update({"unresolved": False, "options": [], "recommendation": "自然聊天男声"})
        selected_result = r.route(selected, default_path)
        assert selected_result["questions"] == []
        assert selected_result["resolvedChoices"][1]["resolvedBy"] == "user"

        preferred = copy.deepcopy(request)
        preferred["choicePoints"][1].update({"unresolved": False, "preferenceKnown": True, "options": [], "recommendation": "沉稳解说男声"})
        d.write_json(default_path, {"version": 1, "scope": "default", "label": "沉稳解说男声", "profile_hash": request["voiceProfileHash"]})
        preferred_result = r.route(preferred, default_path)
        assert preferred_result["questions"] == []
        assert preferred_result["resolvedChoices"][1]["resolvedBy"] == "preference"
        preferred["voiceProfileHash"] = "1" * 64
        rejects(lambda: r.route(preferred, default_path))

        missing_voice = copy.deepcopy(request)
        missing_voice["choicePoints"] = missing_voice["choicePoints"][:1]
        rejects(lambda: r.route(missing_voice, default_path))
        bad_kind = copy.deepcopy(request)
        bad_kind["choicePoints"][1]["kind"] = "shot-mode"
        rejects(lambda: r.route(bad_kind, default_path))

    structures = sorted(r.STRUCTURES)
    cases = []
    for index, structure in enumerate(structures):
        for split in ("tuning", "holdout"):
            cases.append({
                "id": f"{split}-{index}", "split": split, "domain": f"{split}-{structure}",
                "sourceHash": f"hash-{split}-{index}", "structures": [structure],
                "manualReactEdits": 0, "autoRepairRounds": 2 if split == "holdout" else 0,
                "hardGatesPassed": True, "userAccepted": True, "newFailureClass": None,
            })
    benchmark = {"version": 1, "profile": r.PROFILE_ID, "frozenRevision": "rules-v1", "cases": cases}
    assert r.benchmark_gate(benchmark)["status"] == "graduated"
    manual = copy.deepcopy(benchmark)
    manual["cases"][1]["manualReactEdits"] = 1
    rejects(lambda: r.benchmark_gate(manual))
    reused_domain = copy.deepcopy(benchmark)
    reused_domain["cases"][1]["domain"] = reused_domain["cases"][0]["domain"]
    rejects(lambda: r.benchmark_gate(reused_domain))


def check_visual_gate():
    table = json.loads((d.ROOT / "assets/visual-state-table.example.json").read_text())
    assert d.validate_visual_state_table(table) == {"objects": 1, "beats": 2, "clear_end": True}

    missing_learning = copy.deepcopy(table)
    missing_learning["appliedLearnings"].pop()
    rejects(lambda: d.validate_visual_state_table(missing_learning))

    mismatched_label = copy.deepcopy(table)
    mismatched_label["objects"][0]["onScreenLabel"] = "项目"
    rejects(lambda: d.validate_visual_state_table(mismatched_label))

    result_without_transition = copy.deepcopy(table)
    result_without_transition["beats"][0]["semantic"]["kind"] = "transform"
    result_without_transition["beats"][0]["stateChanges"] = []
    rejects(lambda: d.validate_visual_state_table(result_without_transition))

    missing_process = copy.deepcopy(table)
    missing_process["beats"][0]["transition"] = None
    rejects(lambda: d.validate_visual_state_table(missing_process))

    fake_process = copy.deepcopy(table)
    fake_process["beats"][0]["transition"]["processKind"] = "fade"
    rejects(lambda: d.validate_visual_state_table(fake_process))

    text_collision = copy.deepcopy(table)
    text_collision["objects"][0]["onScreenLabel"] = "AI"
    text_collision["objects"][0]["textSlots"] = [{"id": "label", "meaning": "AI", "firstBeat": "ai", "box": [.1, .35, .8, .3], "align": "center", "placement": "inside", "avoidZones": [[.2, .4, .6, .2]]}]
    rejects(lambda: d.validate_visual_state_table(text_collision))

    unsettled = copy.deepcopy(table)
    unsettled["beats"][0]["settleSec"] = 1.1
    rejects(lambda: d.validate_visual_state_table(unsettled))

    overlap = copy.deepcopy(table)
    overlap["objects"].append({"id": "task", "meaning": "当前任务", "form": "任务单", "role": "environment", "persistence": "temporary", "firstBeat": "ai", "internalReveals": [], "receptors": [], "textSlots": []})
    overlap["beats"][0]["appears"].append("task")
    overlap["beats"][0]["attention"]["support"].append("task")
    overlap["beats"][0]["layout"]["task"] = [450, 420, 160, 120]
    overlap["beats"][1]["exits"].append("task")
    overlap["beats"][1]["attention"]["support"].append("task")
    overlap["beats"][1]["exitModes"]["task"] = "collapse"
    rejects(lambda: d.validate_visual_state_table(overlap))

    lingering = copy.deepcopy(table)
    lingering["beats"].pop()
    rejects(lambda: d.validate_visual_state_table(lingering))

    distracted = copy.deepcopy(table)
    distracted["objects"].append({"id": "project", "meaning": "旧场景", "form": "仍占据右侧的项目图", "role": "environment", "persistence": "scene", "firstBeat": "ai", "internalReveals": [], "receptors": [], "textSlots": []})
    distracted["beats"][0]["appears"].append("project")
    distracted["beats"][1]["exits"].append("project")
    distracted["beats"][1]["exitModes"]["project"] = "collapse"
    rejects(lambda: d.validate_visual_state_table(distracted))

    early_reveal = copy.deepcopy(table)
    early_reveal["objects"][0]["internalReveals"] = [{"id": "late-copy", "meaning": "后一句才成立的信息", "firstBeat": "clear"}]
    rejects(lambda: d.validate_visual_state_table(early_reveal))

    batch_reveal = copy.deepcopy(table)
    batch_reveal["objects"][0]["internalReveals"] = [
        {"id": "found", "meaning": "被找到", "firstBeat": "ai"},
        {"id": "checked", "meaning": "被核对", "firstBeat": "ai"}
    ]
    rejects(lambda: d.validate_visual_state_table(batch_reveal))

    contained = copy.deepcopy(table)
    contained["objects"].append({"id": "room", "meaning": "承载机器人当前状态的空间", "spokenTerm": "房间", "onScreenLabel": None, "states": ["visible"], "form": "有边界的容器", "role": "environment", "persistence": "temporary", "firstBeat": "ai", "internalReveals": [], "receptors": [], "textSlots": []})
    for beat in contained["beats"]:
        beat["attention"]["support"].append("room")
        beat["relations"].append({"from": "room", "type": "contains", "to": "agent", "evidence": "房间边界完整包围机器人"})
    contained["beats"][0]["appears"].append("room")
    contained["beats"][0]["contains"] = [{"container": "room", "child": "agent", "paddingPx": 32}]
    contained["beats"][0]["layout"]["room"] = [350, 350, 300, 350]
    contained["beats"][1]["exits"].append("room")
    contained["beats"][1]["exitModes"]["room"] = "collapse"
    assert d.validate_visual_state_table(contained)["clear_end"] is True
    contained["beats"][0]["layout"]["room"] = [400, 400, 200, 250]
    rejects(lambda: d.validate_visual_state_table(contained))

    moved_without_path = copy.deepcopy(table)
    moved_without_path["segment"]["endSec"] = 3
    moved_without_path["beats"][1]["at"] = 2.5
    moved_without_path["beats"].insert(1, {
        "id": "move", "at": 1.4, "settleSec": .4, "anchorType": "speech", "spoken": "移动",
        "hero": "agent", "continuity": "continue", "attention": {"mode": "isolated", "support": []},
        "semantic": {"kind": "observe", "subject": "agent", "verb": "移动", "object": None, "result": "机器人移动到新位置"},
        "stateChanges": [], "relations": [],
        "action": "机器人移动到新位置", "appears": [], "exits": [], "exitModes": {}, "contains": [],
        "layout": {"agent": [720, 420, 160, 230]}, "textLayout": {}, "motions": []
    })
    moved_without_path["beats"][2]["motions"][0]["path"][0] = [720, 420, 160, 230]
    rejects(lambda: d.validate_visual_state_table(moved_without_path))
    moved_without_path["beats"][1]["motions"] = [{
        "object": "agent", "kind": "move", "destination": "viewport", "result": "机器人在新位置站稳",
        "path": [[420, 420, 160, 230], [570, 420, 160, 230], [720, 420, 160, 230]], "landingZone": None
    }]
    assert d.validate_visual_state_table(moved_without_path)["beats"] == 3

    stored = copy.deepcopy(table)
    stored["segment"]["endSec"] = 3
    stored["objects"].append({"id": "task-log", "meaning": "任务记录", "spokenTerm": "任务记录", "onScreenLabel": None, "states": ["empty"], "form": "有边界的记录托盘", "role": "environment", "persistence": "temporary", "firstBeat": "ai", "internalReveals": [], "receptors": ["store"], "textSlots": []})
    stored["beats"][0]["appears"].append("task-log")
    stored["beats"][0]["attention"]["support"].append("task-log")
    stored["beats"][0]["relations"].append({"from": "agent", "type": "points-to", "to": "task-log", "evidence": "机器人面向任务记录托盘"})
    stored["beats"][0]["layout"]["task-log"] = [900, 300, 500, 500]
    stored["beats"][1] = {
        "id": "store", "at": 1.5, "settleSec": .4, "anchorType": "speech", "spoken": "留在任务记录",
        "hero": "agent", "continuity": "handoff", "attention": {"mode": "guided", "support": ["task-log"]},
        "semantic": {"kind": "transfer", "subject": "agent", "verb": "存入", "object": "task-log", "result": "记录进入任务记录"},
        "stateChanges": [], "relations": [{"from": "agent", "type": "stores-in", "to": "task-log", "evidence": "记录卡沿路径完整进入托盘"}],
        "transition": {"trigger": "口播说留在任务记录", "processKind": "move", "processEvidence": "同一张记录卡沿路径进入托盘", "settled": "记录卡完整落入托盘并停止"},
        "action": "同一张记录卡进入任务记录托盘", "appears": [], "exits": ["agent"], "exitModes": {"agent": "transform"}, "contains": [],
        "layout": {"task-log": [900, 300, 500, 500]}, "textLayout": {},
        "motions": [{"object": "agent", "kind": "store", "destination": "task-log", "result": "记录卡完整落入任务记录托盘", "path": [[420, 420, 160, 230], [700, 420, 160, 230], [1000, 400, 160, 230]], "landingZone": [950, 350, 300, 350]}]
    }
    stored["beats"].append({
        "id": "clear", "at": 2.5, "settleSec": .4, "anchorType": "sentence-end", "spoken": "句末",
        "hero": "task-log", "continuity": "continue", "attention": {"mode": "isolated", "support": []},
        "semantic": {"kind": "clear", "subject": "task-log", "verb": "退出", "object": None, "result": "任务记录完整离屏"},
        "stateChanges": [], "relations": [],
        "transition": None,
        "action": "任务记录托盘完整退场", "appears": [], "exits": ["task-log"], "exitModes": {"task-log": "offscreen"}, "contains": [],
        "layout": {}, "textLayout": {},
        "motions": [{"object": "task-log", "kind": "discard", "destination": "offscreen", "result": "托盘完全离开画面", "path": [[900, 300, 500, 500], [1400, 300, 500, 500], [1940, 300, 500, 500]], "landingZone": None}]
    })
    assert d.validate_visual_state_table(stored)["clear_end"] is True
    unrelated_support = copy.deepcopy(stored)
    unrelated_support["beats"][0]["relations"] = []
    rejects(lambda: d.validate_visual_state_table(unrelated_support))
    missing_receptor = copy.deepcopy(stored)
    missing_receptor["objects"][1]["receptors"] = []
    rejects(lambda: d.validate_visual_state_table(missing_receptor))

    off_center = copy.deepcopy(table)
    off_center["objects"][0]["onScreenLabel"] = "AI"
    off_center["objects"][0]["textSlots"] = [{"id": "label", "meaning": "AI", "firstBeat": "ai", "box": [.1, .35, .8, .3], "align": "center", "placement": "inside", "avoidZones": []}]
    off_center["beats"][0]["textLayout"] = {"agent.label": [460, 540, 80, 24]}
    rejects(lambda: d.validate_visual_state_table(off_center))

    with tempfile.TemporaryDirectory(prefix="video-director-qa-") as temp:
        temp = Path(temp)
        for name in ("ai-cue.png", "ai-mid.png", "ai-settled.png", "clear-cue.png", "clear-mid.png", "clear-settled.png"):
            (temp / name).write_bytes(b"frame")
        frames = [
            {"beat": beat, "phase": phase, "image": f"{beat}-{phase}.png", "reviewed": True, "note": "已检查遮挡、越界与语义终点"}
            for beat in ("ai", "clear") for phase in ("cue", "mid", "settled")
        ]
        learned_checks = [
            {"id": "context-neutrality", "passed": True, "evidence": ["source-review"], "note": "只使用材料明确提供的背景"},
            {"id": "transition-variety-with-purpose", "passed": True, "evidence": ["full-playback"], "note": "连续观看后没有机械重复转场"},
            {"id": "direct-metaphor", "passed": True, "evidence": ["ai/cue"], "note": "机器人对象可直接识别为 AI"},
            {"id": "causal-transition-process", "passed": True, "evidence": ["ai/mid"], "note": "中间帧显示由静止到执行的过程"},
        ]
        qa = {"version": 2, "frames": frames, "learnedGateChecks": learned_checks}
        assert d.validate_rendered_evidence(table, qa, temp)["coverage"] == "complete"
        rejects(lambda: d.validate_rendered_evidence(table, {**qa, "frames": [frame for frame in frames if not (frame["beat"] == "ai" and frame["phase"] == "mid")]}, temp))
        rejects(lambda: d.validate_rendered_evidence(table, {**qa, "frames": frames[:-1]}, temp))
        rejects(lambda: d.validate_rendered_evidence(table, {**qa, "learnedGateChecks": learned_checks[:-1]}, temp))

        sound_timeline = json.loads((d.ROOT / "assets/state-timeline.example.json").read_text())
        sound_timeline["sounds"] = {"cache-confirm": {"event": "cacheHit", "kind": "confirm", "meaning": "确认缓存命中", "src": "sfx/soft-confirm.wav", "gainDb": -16, "durationSec": .3}}
        sound_qa = {**qa, "soundReview": {"reviewed": True, "voiceClear": True, "semanticMatch": True, "noClipping": True, "evidence": ["full-playback"], "note": "完整播放后口播清晰，命中音效与落位动作同步"}}
        assert d.validate_rendered_evidence(table, sound_qa, temp, sound_timeline)["soundEffects"] == 1
        rejects(lambda: d.validate_rendered_evidence(table, qa, temp, sound_timeline))

        state_path, qa_path = temp / "visual-state-table.json", temp / "rendered-qa.json"
        candidate, final = temp / "candidate.mp4", temp / "released.mp4"
        d.write_json(state_path, table)
        d.write_json(qa_path, qa)
        candidate.write_bytes(b"candidate-video")
        rejects(lambda: d.release_candidate(state_path, d.ROOT / "assets/world-model.example.json", d.ROOT / "assets/state-timeline.example.json", d.ROOT / "assets/world-props.example.json", candidate, qa_path, temp / "qa-release", final))
        assert not final.exists(), "Declaration-only QA must never release a video"

        blocked_final = temp / "blocked.mp4"
        d.write_json(qa_path, {**qa, "frames": [frame for frame in frames if not (frame["beat"] == "ai" and frame["phase"] == "mid")]})
        with patch.object(d, "run_frame_gate", return_value={"status": "pass", "checks": []}):
            rejects(lambda: d.release_candidate(
                state_path,
                d.ROOT / "assets/world-model.example.json",
                d.ROOT / "assets/state-timeline.example.json",
                d.ROOT / "assets/world-props.example.json",
                candidate,
                qa_path,
                temp / "qa-release",
                blocked_final,
            ))
        assert not blocked_final.exists()


def check_world_gate():
    world = json.loads((d.ROOT / "assets/world-model.example.json").read_text())
    timeline = json.loads((d.ROOT / "assets/state-timeline.example.json").read_text())
    props = json.loads((d.ROOT / "assets/world-props.example.json").read_text())
    assert v.validate_world_timeline(world, timeline, props) == {"nodes": 3, "motions": 8, "events": 19, "cameraKeys": 10}
    assert round(v.ease(.5), 3) == .675

    collision = copy.deepcopy(world)
    collision["nodes"]["cache"]["labelBox"] = [890, 598, 120, 44]
    rejects(lambda: v.validate_world_timeline(collision, timeline, props))

    hidden_actor = copy.deepcopy(world)
    hidden_actor["actor"].pop("fillColors")
    rejects(lambda: v.validate_world_timeline(hidden_actor, timeline, props))

    missing_scale_end = copy.deepcopy(timeline)
    missing_scale_end["motions"]["firstEnter"]["startScale"] = .25
    rejects(lambda: v.validate_world_timeline(world, missing_scale_end, props))

    wrong_arrival = copy.deepcopy(timeline)
    wrong_arrival["motions"]["firstEnter"]["to"] = "outsideApp"
    rejects(lambda: v.validate_world_timeline(world, wrong_arrival, props))

    sloped_lane = copy.deepcopy(world)
    sloped_lane["actor"]["poses"]["outsideApp"][1] -= 30
    rejects(lambda: v.validate_world_timeline(sloped_lane, timeline, props))

    raised_node = copy.deepcopy(world)
    raised_node["nodes"]["app"]["artworkBox"][1] -= 40
    rejects(lambda: v.validate_world_timeline(raised_node, timeline, props))

    uneven_label = copy.deepcopy(world)
    uneven_label["nodes"]["app"]["labelBox"][1] -= 8
    rejects(lambda: v.validate_world_timeline(uneven_label, timeline, props))

    with_sound = copy.deepcopy(timeline)
    with_sound["sounds"] = {"cache-confirm": {"event": "cacheHit", "kind": "confirm", "meaning": "确认缓存命中", "src": "sfx/soft-confirm.wav", "gainDb": -16, "durationSec": .3}}
    assert v.validate_world_timeline(world, with_sound, props)["events"] == 19
    too_loud = copy.deepcopy(with_sound)
    too_loud["sounds"]["cache-confirm"]["gainDb"] = -3
    rejects(lambda: v.validate_world_timeline(world, too_loud, props))
    unknown_event = copy.deepcopy(with_sound)
    unknown_event["sounds"]["cache-confirm"]["event"] = "unknown"
    rejects(lambda: v.validate_world_timeline(world, unknown_event, props))
    with tempfile.TemporaryDirectory(prefix="video-director-sfx-") as temp:
        base = Path(temp)
        (base / "sfx").mkdir()
        with wave.open(str(base / "sfx/soft-confirm.wav"), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 6400)
        assert v.validate_sound_assets(with_sound, base)["soundAssets"] == 1
        (base / "sfx/soft-confirm.wav").unlink()
        rejects(lambda: v.validate_sound_assets(with_sound, base))


def check_provider(fixture):
    gateway = d.TencentSpeechGateway()
    audio = (d.ROOT / "assets/test-voice.mp3").read_bytes()
    raw = [{"Text": s["text"], "BeginTime": s["start_ms"], "EndTime": s["end_ms"]} for s in fixture["alignment"]["spans"]]
    calls = []
    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert request.headers["authorization"].startswith("TC3-HMAC-SHA256 Credential=test-id/")
        assert "test-key" not in str(request.headers) and b"test-key" not in request.content
        assert payload["EnableSubtitle"] is True and payload["Speed"] == .5
        return httpx.Response(200, json={"Response": {"Audio": b64encode(audio).decode(), "Subtitles": raw, "RequestId": "test-request"}})
    client_class = httpx.Client
    def fake_client(**kwargs):
        return client_class(transport=httpx.MockTransport(respond), trust_env=False, **kwargs)
    request = SpeechSynthesisRequest("tencent_speech", "https://tts.tencentcloudapi.com", "test-id:test-key", "1", "1001", fixture["text"], 1.1)
    with patch.object(tencent.httpx, "Client", side_effect=fake_client), patch.object(gateway, "_validate_dns"):
        result = gateway.synthesize(request)
    assert result.audio == audio and result.alignment.spans[-1].end_ms == 1562
    assert len(calls) == 1
    for endpoint in ["http://tts.tencentcloudapi.com", "https://example.com", "https://tts.tencentcloudapi.com.evil.test", "https://user:secret@tts.tencentcloudapi.com", "https://tts.tencentcloudapi.com/path", "https://tts.tencentcloudapi.com:444", "https://tts.tencentcloudapi.com?key=x"]:
        rejects(lambda: gateway._validate_tencent_endpoint(endpoint))
    with patch.object(tencent.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
        rejects(lambda: gateway._validate_dns(request.base_url))
    rejects(lambda: gateway._tencent_alignment(raw_subtitles=None, narration_text=request.text))
    rejects(lambda: gateway._tencent_alignment(raw_subtitles=raw, narration_text="另一句话"))
    overlap = [{"Text":"了解", "BeginTime":0, "EndTime":11240}, {"Text":" ", "BeginTime":11240, "EndTime":11190}, {"Text":"NFT", "BeginTime":11190, "EndTime":11690}]
    assert gateway._tencent_alignment(raw_subtitles=overlap, narration_text="了解 NFT").spans[1].start_ms == 11240
    text = "学" * 140 + "。" + "学" * 11
    chunks = gateway._split_tencent_text(text)
    assert "".join(chunks) == text and all(len(c) <= 140 for c in chunks)
    # Real MP3 concatenation must shift the second chunk's native timestamps.
    long_request = SpeechSynthesisRequest("tencent_speech", request.base_url, request.api_key, "1", "1001", request.text * 2, 1.1)
    with patch.object(gateway, "_split_tencent_text", return_value=(request.text, request.text)), patch.object(gateway, "_synthesize_tencent_chunk", return_value=result):
        merged = gateway.synthesize(long_request)
    assert len(merged.alignment.spans) == 12
    assert merged.alignment.spans[6].start_ms == round(d.probe_audio_duration(audio) * 1000) + 250
    assert merged.duration_seconds > d.probe_audio_duration(audio) * 1.9


if __name__ == "__main__":
    main()
