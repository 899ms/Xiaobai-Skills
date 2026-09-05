"""Deterministic policy router for director profiles, user questions, shots, and graduation."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_DEFAULT = ROOT / ".local" / "voice-default.json"
PROFILE_ID = "knowledge-explainer"
STRUCTURES = {"mechanism-flow", "abstract-concept", "cause-tradeoff", "data-trend", "case-narrative"}
USER_DECISIONS = {"focus", "story-angle", "voice", "visual-language", "capability-approval"}
AI_DECISIONS = ["explanation-structure", "semantic-segmentation", "shot-mode", "object-design", "layout", "timing", "easing", "render", "qa"]


class RouterError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise RouterError(message)


def load_profile(profile_id):
    path = ROOT / "profiles" / profile_id / "profile.json"
    require(path.is_file(), f"未知导演 Profile：{profile_id}")
    return json.loads(path.read_text())


def load_voice_default(path=VOICE_DEFAULT):
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise RouterError("默认音色记录损坏；不能跳过音色选择。") from None
    require(
        isinstance(value, dict)
        and value.get("version") == 1
        and value.get("scope") == "default"
        and isinstance(value.get("label"), str)
        and value["label"].strip()
        and isinstance(value.get("profile_hash"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["profile_hash"]),
        "默认音色记录格式无效；不能跳过音色选择。",
    )
    return value


def shot_mode(segment, first):
    if first:
        return "establish"
    if segment["worldChanged"] or segment["timeChanged"]:
        return "cut"
    if segment["subjectHandoff"]:
        return "handoff"
    if segment["sameWorld"] and segment["sameSubject"] and segment["focusChanged"]:
        return "camera-move"
    if segment["sameWorld"]:
        return "local-transform"
    return "cut"


def route(request, voice_default_path=VOICE_DEFAULT):
    require(isinstance(request, dict) and request.get("version") == 2, "route-request.version 必须是 2。")
    profile_id = request.get("profile")
    require(profile_id == PROFILE_ID, f"当前只实现 {PROFILE_ID} Profile。")
    profile = load_profile(profile_id)
    spine = request.get("narrativeSpine")
    require(isinstance(spine, str) and spine.strip(), "缺少全片 narrativeSpine。")
    voice_profile_hash = request.get("voiceProfileHash")
    require(isinstance(voice_profile_hash, str) and re.fullmatch(r"[0-9a-f]{64}", voice_profile_hash), "缺少 preflight 返回的 voiceProfileHash。")

    questions = []
    resolved = []
    choice_points = request.get("choicePoints")
    require(isinstance(choice_points, list), "choicePoints 必须是数组。")
    seen = set()
    for choice in choice_points:
        required = {"id", "kind", "materiallyDifferent", "unresolved", "preferenceKnown", "options", "recommendation", "reason"}
        require(isinstance(choice, dict) and set(choice) == required, "choicePoints 项格式无效。")
        require(isinstance(choice["id"], str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", choice["id"]) and choice["id"] not in seen, "choicePoints.id 无效或重复。")
        seen.add(choice["id"])
        require(choice["kind"] in USER_DECISIONS, f"{choice['kind']} 属于专业执行，不应询问用户。")
        require(all(isinstance(choice[key], bool) for key in ("materiallyDifferent", "unresolved", "preferenceKnown")), f"choice {choice['id']} 的触发条件无效。")
        require(isinstance(choice["recommendation"], str) and choice["recommendation"].strip() and isinstance(choice["reason"], str) and choice["reason"].strip(), f"choice {choice['id']} 缺少推荐与理由。")
        if choice["kind"] == "voice":
            require(choice["materiallyDifferent"] is True, "音色会显著改变成片，voice.materiallyDifferent 必须为 true。")
            if choice["preferenceKnown"]:
                preferred = load_voice_default(voice_default_path)
                require(preferred is not None, "只有声音试听记录，没有长期默认音色；必须询问用户。")
                require(preferred["profile_hash"] == voice_profile_hash, "长期默认音色与当前 TTS 配置不一致；必须重新试听并询问用户。")
                require(preferred["label"] == choice["recommendation"], "voice.recommendation 必须与长期默认音色名称一致。")
        ask = choice["materiallyDifferent"] and choice["unresolved"] and not choice["preferenceKnown"]
        if ask:
            options = choice["options"]
            require(isinstance(options, list) and 2 <= len(options) <= 3 and all(isinstance(option, str) and option.strip() for option in options) and len(options) == len(set(options)) and choice["recommendation"] in options, f"choice {choice['id']} 需要 2–3 个非空选项，推荐项必须在其中。")
            questions.append({key: choice[key] for key in ("id", "kind", "options", "recommendation", "reason")})
        else:
            resolved_by = "preference" if choice["preferenceKnown"] else "ai" if choice["unresolved"] else "user"
            resolved.append({"id": choice["id"], "kind": choice["kind"], "resolvedBy": resolved_by, "choice": choice["recommendation"]})

    require(sum(choice["kind"] == "voice" for choice in choice_points) == 1, "每条新视频必须且只能包含一个 voice 决策点。")

    segments = request.get("segments")
    require(isinstance(segments, list) and segments, "segments 必须包含至少一个语义段。")
    routed = []
    segment_ids = set()
    for index, segment in enumerate(segments):
        required = {"id", "structure", "sameWorld", "sameSubject", "focusChanged", "worldChanged", "timeChanged", "subjectHandoff", "evidence"}
        require(isinstance(segment, dict) and set(segment) == required, "segments 项格式无效。")
        require(isinstance(segment["id"], str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", segment["id"]) and segment["id"] not in segment_ids, "segment.id 无效或重复。")
        segment_ids.add(segment["id"])
        require(segment["structure"] in STRUCTURES, f"segment {segment['id']} 的解释结构无效。")
        require(all(isinstance(segment[key], bool) for key in ("sameWorld", "sameSubject", "focusChanged", "worldChanged", "timeChanged", "subjectHandoff")), f"segment {segment['id']} 的镜头事实无效。")
        require(isinstance(segment["evidence"], str) and segment["evidence"].strip(), f"segment {segment['id']} 缺少路由证据。")
        require(not segment["worldChanged"] or not segment["sameWorld"], f"segment {segment['id']} 不能同时声明 sameWorld 与 worldChanged。")
        require(not segment["subjectHandoff"] or not segment["sameSubject"], f"segment {segment['id']} 主体交接时不能声明 sameSubject。")
        routed.append({"id": segment["id"], "structure": segment["structure"], "shotMode": shot_mode(segment, index == 0), "evidence": segment["evidence"]})

    return {
        "version": 1,
        "profile": profile["id"],
        "profileMaturity": profile.get("maturity", "tuning"),
        "narrativeSpine": spine,
        "questions": questions,
        "resolvedChoices": resolved,
        "segments": routed,
        "delegatedToAI": AI_DECISIONS,
    }


def benchmark_gate(manifest):
    require(isinstance(manifest, dict) and manifest.get("version") == 1 and manifest.get("profile") == PROFILE_ID, "benchmark manifest 版本或 Profile 无效。")
    require(isinstance(manifest.get("frozenRevision"), str) and manifest["frozenRevision"].strip(), "benchmark 必须记录冻结版本。")
    cases = manifest.get("cases")
    require(isinstance(cases, list) and cases, "benchmark 缺少 cases。")
    ids, hashes = set(), set()
    by_split = {"tuning": {key: set() for key in STRUCTURES}, "holdout": {key: set() for key in STRUCTURES}}
    for case in cases:
        required = {"id", "split", "domain", "sourceHash", "structures", "manualReactEdits", "autoRepairRounds", "hardGatesPassed", "userAccepted", "newFailureClass"}
        require(isinstance(case, dict) and set(case) == required, "benchmark case 格式无效。")
        require(isinstance(case["id"], str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", case["id"]) and case["id"] not in ids and isinstance(case["sourceHash"], str) and case["sourceHash"].strip() and case["sourceHash"] not in hashes, "benchmark case id/sourceHash 无效或重复。")
        ids.add(case["id"])
        hashes.add(case["sourceHash"])
        require(case["split"] in by_split and isinstance(case["domain"], str) and case["domain"].strip(), f"case {case['id']} 的 split/domain 无效。")
        require(isinstance(case["structures"], list) and case["structures"] and all(isinstance(structure, str) for structure in case["structures"]) and len(case["structures"]) == len(set(case["structures"])) and set(case["structures"]).issubset(STRUCTURES), f"case {case['id']} 的 structures 无效。")
        require(isinstance(case["manualReactEdits"], int) and case["manualReactEdits"] >= 0 and isinstance(case["autoRepairRounds"], int) and case["autoRepairRounds"] >= 0, f"case {case['id']} 的修复计数无效。")
        require(isinstance(case["hardGatesPassed"], bool) and isinstance(case["userAccepted"], bool), f"case {case['id']} 的验收结果无效。")
        require(case["newFailureClass"] is None or isinstance(case["newFailureClass"], str) and case["newFailureClass"].strip(), f"case {case['id']} 的新失败类型无效。")
        for structure in case["structures"]:
            by_split[case["split"]][structure].add(case["domain"])
        if case["split"] == "holdout":
            require(case["manualReactEdits"] == 0, f"留出案例 {case['id']} 发生人工 React 修改。")
            require(case["autoRepairRounds"] <= 2, f"留出案例 {case['id']} 超过两轮自动修复。")
            require(case["hardGatesPassed"] and case["userAccepted"] and case["newFailureClass"] is None, f"留出案例 {case['id']} 尚未达到毕业标准。")
    for structure in STRUCTURES:
        require(by_split["tuning"][structure], f"解释结构 {structure} 缺少调优案例。")
        require(by_split["holdout"][structure], f"解释结构 {structure} 缺少留出案例。")
        require(by_split["holdout"][structure].isdisjoint(by_split["tuning"][structure]), f"解释结构 {structure} 的留出案例没有跨到新领域。")
    return {"profile": PROFILE_ID, "status": "graduated", "holdoutCases": sum(case["split"] == "holdout" for case in cases), "structures": sorted(STRUCTURES)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    route_parser = sub.add_parser("route")
    route_parser.add_argument("--request", type=Path, required=True)
    route_parser.add_argument("--out", type=Path)
    benchmark = sub.add_parser("benchmark-gate")
    benchmark.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    source = args.request if args.command == "route" else args.manifest
    require(source.is_file(), f"缺少输入文件：{source}")
    result = route(json.loads(source.read_text())) if args.command == "route" else benchmark_gate(json.loads(source.read_text()))
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.command == "route" and args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    try:
        main()
    except RouterError as exc:
        raise SystemExit(str(exc)) from None
