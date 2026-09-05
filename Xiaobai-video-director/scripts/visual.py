"""World-model and rendered-frame checks shared by video-director runs."""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from PIL import Image


class VisualError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise VisualError(message)


def read_json(path: Path):
    require(path.is_file(), f"缺少文件：{path}")
    return json.loads(path.read_text())


def rect(value, name):
    require(isinstance(value, list) and len(value) == 4 and all(isinstance(v, (int, float)) for v in value), f"{name} 必须是 [x,y,w,h]。")
    x, y, width, height = value
    require(width > 0 and height > 0, f"{name} 的宽高必须大于零。")
    return [float(x), float(y), float(width), float(height)]


def point(value, name):
    require(isinstance(value, list) and len(value) == 2 and all(isinstance(v, (int, float)) for v in value), f"{name} 必须是 [x,y]。")
    return [float(value[0]), float(value[1])]


def separated(left, right, gap=0):
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    return lx + lw + gap <= rx or rx + rw + gap <= lx or ly + lh + gap <= ry or ry + rh + gap <= ly


def contains(container, child):
    cx, cy, cw, ch = container
    x, y, w, h = child
    return x >= cx and y >= cy and x + w <= cx + cw and y + h <= cy + ch


def moment(ref, props):
    require(isinstance(ref, dict), "时间点必须是对象。")
    offset = ref.get("offsetSec", 0)
    require(isinstance(offset, (int, float)), "offsetSec 必须是数值。")
    if "atSec" in ref:
        require(isinstance(ref["atSec"], (int, float)), "atSec 必须是数值。")
        return float(ref["atSec"]) + float(offset)
    cue = ref.get("cue")
    require(isinstance(cue, str) and cue in props.get("cues", {}), f"状态时间轴引用了未知 cue：{cue}")
    return float(props["cues"][cue]) / 1000 + float(offset)


def actor_box(position, size, scale=1):
    width, height = size[0] * scale, size[1] * scale
    return [position[0] - width / 2, position[1] - height / 2, width, height]


def validate_world_timeline(world, timeline, props):
    require(world.get("version") == 1 and timeline.get("version") == 1, "world-model 和 state-timeline 版本必须是 1。")
    safe = rect(world.get("safeArea"), "safeArea")
    require(isinstance(world.get("background"), str) and isinstance(world.get("actorColors"), list) and world["actorColors"], "world-model 需要 background 和 actorColors。")
    events = timeline.get("events")
    require(isinstance(events, dict) and events, "state-timeline 缺少 events。")
    event_times = {name: moment(ref, props) for name, ref in events.items()}

    sounds = timeline.get("sounds", {})
    require(isinstance(sounds, dict), "state-timeline.sounds 必须是对象；不使用音效时填空对象或省略。")
    sound_events = set()
    sound_times = []
    for sound_id, sound in sounds.items():
        require(isinstance(sound_id, str) and sound_id and isinstance(sound, dict), "sounds 的 id 与内容必须有效。")
        require(set(sound) == {"event", "kind", "meaning", "src", "gainDb", "durationSec"}, f"音效 {sound_id} 格式无效。")
        event = sound["event"]
        require(isinstance(event, str) and event in event_times and event not in sound_events, f"音效 {sound_id} 必须独占一个已存在的语义事件。")
        require(isinstance(sound["kind"], str) and sound["kind"] in {"arrive", "handoff", "store", "block", "release", "confirm", "failure"}, f"音效 {sound_id}.kind 无效。")
        require(isinstance(sound["meaning"], str) and sound["meaning"].strip(), f"音效 {sound_id} 必须说明它帮助理解的动作。")
        require(isinstance(sound["src"], str), f"音效 {sound_id}.src 必须是字符串。")
        source = Path(sound["src"])
        require(sound["src"].startswith("sfx/") and not source.is_absolute() and ".." not in source.parts and source.suffix.lower() in {".wav", ".mp3", ".m4a", ".aac"}, f"音效 {sound_id} 必须使用 run/sfx 下的本地音频文件。")
        gain = sound["gainDb"]
        duration = sound["durationSec"]
        require(isinstance(gain, (int, float)) and -30 <= gain <= -8, f"音效 {sound_id}.gainDb 必须在 -30 到 -8 dB，避免遮住口播。")
        require(isinstance(duration, (int, float)) and .08 <= duration <= 1, f"音效 {sound_id}.durationSec 必须在 0.08–1 秒。")
        at = event_times[event]
        require(0 <= at and at + duration <= props["durationInFrames"] / 30, f"音效 {sound_id} 超出成片时间。")
        sound_events.add(event)
        sound_times.append((at, sound_id))
    sound_times.sort()
    for left, right in zip(sound_times, sound_times[1:]):
        require(right[0] - left[0] >= .8, f"音效 {left[1]} 与 {right[1]} 间隔不足 0.8 秒，会与口播争夺注意力。")

    nodes = world.get("nodes")
    require(isinstance(nodes, dict) and nodes, "world-model 缺少 nodes。")
    receptor_boxes = {}
    for node_id, node in nodes.items():
        require(isinstance(node, dict) and all(isinstance(node.get(k), str) and node[k] for k in ("kind", "label", "meaning", "visibleEvent")), f"节点 {node_id} 缺少 kind、label、meaning 或 visibleEvent。")
        require(node["visibleEvent"] in events, f"节点 {node_id} 的 visibleEvent 不存在。")
        point(node.get("origin"), f"{node_id}.origin")
        artwork = rect(node.get("artworkBox"), f"{node_id}.artworkBox")
        label = rect(node.get("labelBox"), f"{node_id}.labelBox")
        require(contains(safe, artwork) and contains(safe, label), f"节点 {node_id} 的图形或文字超出安全区。")
        avoid = node.get("avoidBoxes", [])
        require(isinstance(avoid, list), f"节点 {node_id}.avoidBoxes 必须是数组。")
        for index, value in enumerate(avoid):
            blocked = rect(value, f"{node_id}.avoidBoxes[{index}]")
            require(separated(label, blocked), f"节点 {node_id} 的文字槽与图形避让区重叠。")
        receptors = node.get("receptors", {})
        require(isinstance(receptors, dict), f"节点 {node_id}.receptors 必须是对象。")
        for receptor_id, value in receptors.items():
            receptor = rect(value, f"{node_id}.receptors.{receptor_id}")
            require(contains(safe, receptor), f"节点 {node_id} 的接收区 {receptor_id} 超出安全区。")
            receptor_boxes[(node_id, receptor_id)] = receptor

    actor = world.get("actor")
    require(isinstance(actor, dict) and isinstance(actor.get("id"), str) and isinstance(actor.get("meaning"), str), "world-model 缺少 actor。")
    size = point(actor.get("size"), "actor.size")
    fill_colors = actor.get("fillColors")
    min_visible_fill = actor.get("minVisibleFillRatio")
    require(isinstance(fill_colors, list) and fill_colors and all(isinstance(value, str) for value in fill_colors), "actor.fillColors 必须声明主角的可见填充色。")
    require(isinstance(min_visible_fill, (int, float)) and 0 < min_visible_fill < 1, "actor.minVisibleFillRatio 必须在 0–1。")
    for value in fill_colors:
        color(value)
    poses = actor.get("poses")
    require(isinstance(poses, dict) and poses, "actor 缺少 poses。")
    pose_points = {name: point(value, f"actor.poses.{name}") for name, value in poses.items()}
    lane_lock = actor.get("laneLock", False)
    require(isinstance(lane_lock, bool), "actor.laneLock 必须是布尔值。")
    lane_y = actor.get("laneY")
    if lane_lock:
        require(isinstance(lane_y, (int, float)), "启用 actor.laneLock 时必须声明 laneY。")
        label_tops = []
        for node_id, node in nodes.items():
            artwork = rect(node["artworkBox"], f"{node_id}.artworkBox")
            require(abs(artwork[1] + artwork[3] / 2 - lane_y) <= artwork[3] * .1, f"节点 {node_id} 的视觉中心没有对齐 actor.laneY。")
            label_tops.append(rect(node["labelBox"], f"{node_id}.labelBox")[1])
            for receptor_id, receptor_value in node.get("receptors", {}).items():
                receptor = rect(receptor_value, f"{node_id}.receptors.{receptor_id}")
                require(abs(receptor[1] + receptor[3] / 2 - lane_y) < .5, f"节点 {node_id} 的接收区 {receptor_id} 没有对齐 actor.laneY。")
        require(max(label_tops) - min(label_tops) < .5, "水平轨道上的节点标签必须共用同一基线。")

    motions = timeline.get("motions")
    require(isinstance(motions, dict) and motions, "state-timeline 缺少 motions。")
    label_boxes = {node_id: rect(node["labelBox"], f"{node_id}.labelBox") for node_id, node in nodes.items()}
    for motion_id, motion_value in motions.items():
        require(isinstance(motion_value, dict), f"motion {motion_id} 必须是对象。")
        start_pose, end_pose = motion_value.get("from"), motion_value.get("to")
        require(start_pose in pose_points and end_pose in pose_points, f"motion {motion_id} 引用了未知 pose。")
        if lane_lock:
            require(all(abs(pose_points[pose][1] - lane_y) < .5 for pose in (start_pose, end_pose)), f"motion {motion_id} 必须沿 actor.laneY 水平移动。")
        start, end = moment(motion_value.get("start"), props), moment(motion_value.get("end"), props)
        require(0 <= start < end <= props["durationInFrames"] / 30, f"motion {motion_id} 的时间范围无效。")
        start_scale = motion_value.get("startScale", 1)
        require(isinstance(start_scale, (int, float)) and 0 < start_scale <= 1, f"motion {motion_id}.startScale 必须在 0–1。")
        if start_scale < 1:
            scale_end = moment(motion_value.get("scaleEnd"), props)
            require(start < scale_end <= end, f"motion {motion_id}.scaleEnd 必须晚于出生且不晚于运动结束。")
        source_node = motion_value.get("spawnFromNode")
        if source_node is not None:
            require(source_node in nodes and "spawnBox" in nodes[source_node], f"motion {motion_id} 的出生节点缺少 spawnBox。")
            visible_at = event_times[nodes[source_node]["visibleEvent"]]
            require(visible_at < start, f"motion {motion_id} 出生前，来源节点 {source_node} 必须已经出现。")
            require(contains(rect(nodes[source_node]["spawnBox"], f"{source_node}.spawnBox"), actor_box(pose_points[start_pose], size, start_scale)), f"motion {motion_id} 没有从 {source_node} 的可见出口内部产生。")
        semantic = motion_value.get("semantic")
        require(isinstance(semantic, dict) and semantic.get("action") in {"arrive", "transfer"}, f"motion {motion_id} 缺少 arrive/transfer 语义。")
        target_key = (semantic.get("targetNode"), semantic.get("targetReceptor"))
        require(target_key in receptor_boxes, f"motion {motion_id} 引用了未知目标接收区。")
        require(contains(receptor_boxes[target_key], actor_box(pose_points[end_pose], size)), f"motion {motion_id} 表示到达 {target_key[0]}，但终点没有进入其接收区。")
        if semantic["action"] == "transfer":
            source_key = (semantic.get("sourceNode"), semantic.get("sourceReceptor"))
            require(source_key in receptor_boxes, f"motion {motion_id} 引用了未知来源接收区。")
            require(contains(receptor_boxes[source_key], actor_box(pose_points[start_pose], size, start_scale)), f"motion {motion_id} 表示从 {source_key[0]} 发出，但起点不在其接收区。")
        for sample in range(33):
            progress = sample / 32
            position = [pose_points[start_pose][axis] + (pose_points[end_pose][axis] - pose_points[start_pose][axis]) * progress for axis in (0, 1)]
            scale = float(start_scale) + (1 - float(start_scale)) * progress
            moving = actor_box(position, size, scale)
            for node_id, label in label_boxes.items():
                require(separated(moving, label), f"motion {motion_id} 在途中遮挡了 {node_id} 的文字槽。")

    camera = timeline.get("camera")
    require(isinstance(camera, list) and len(camera) >= 2, "state-timeline 需要至少两个 camera 关键点。")
    camera_times = []
    for index, keyframe in enumerate(camera):
        require(isinstance(keyframe, dict) and isinstance(keyframe.get("x"), (int, float)) and isinstance(keyframe.get("zoom"), (int, float)) and keyframe["zoom"] > 0, f"camera[{index}] 无效。")
        camera_times.append(moment(keyframe, props))
    require(camera_times == sorted(camera_times) and len(camera_times) == len(set(camera_times)), "camera 关键点必须按时间严格递增。")
    return {"nodes": len(nodes), "motions": len(motions), "events": len(events), "cameraKeys": len(camera)}


def validate_sound_assets(timeline, base: Path):
    """Keep renders offline and fail before Remotion when a declared sound is missing."""
    base = base.resolve()
    for sound_id, sound in timeline.get("sounds", {}).items():
        source = (base / sound["src"]).resolve()
        require(base in source.parents and source.is_file(), f"音效 {sound_id} 的本地文件不存在：{sound['src']}")
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(source)], capture_output=True, text=True)
        try:
            duration = float(probe.stdout.strip())
        except ValueError:
            duration = 0
        require(probe.returncode == 0 and duration + .02 >= sound["durationSec"], f"音效 {sound_id} 无法解码或短于声明时长。")
    return {"soundAssets": len(timeline.get("sounds", {}))}


def color(hex_value):
    value = hex_value.removeprefix("#")
    require(len(value) == 6, f"颜色格式无效：{hex_value}")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def ease(progress):
    """Match Remotion's Easing.bezier(0.65, 0, 0.2, 1)."""
    def curve(t, first, second):
        return 3 * (1 - t) ** 2 * t * first + 3 * (1 - t) * t ** 2 * second + t ** 3

    low, high = 0.0, 1.0
    for _ in range(24):
        parameter = (low + high) / 2
        if curve(parameter, .65, .2) < progress:
            low = parameter
        else:
            high = parameter
    return curve((low + high) / 2, 0, 1)


def ratio(image, box_value, predicate):
    x, y, width, height = [int(round(v)) for v in box_value]
    x, y = max(0, x), max(0, y)
    width, height = min(image.width - x, width), min(image.height - y, height)
    require(width > 0 and height > 0, "像素检查区域超出画面。")
    pixels = image.crop((x, y, x + width, y + height)).convert("RGB").getdata()
    total = width * height
    return sum(1 for pixel in pixels if predicate(pixel)) / total


def camera_at(seconds, timeline, props):
    frames = [(moment(item, props), float(item["x"]), float(item["zoom"])) for item in timeline["camera"]]
    if seconds <= frames[0][0]:
        return frames[0][1:]
    if seconds >= frames[-1][0]:
        return frames[-1][1:]
    for left, right in zip(frames, frames[1:]):
        if left[0] <= seconds <= right[0]:
            progress = ease((seconds - left[0]) / (right[0] - left[0]))
            return left[1] + (right[1] - left[1]) * progress, left[2] + (right[2] - left[2]) * progress
    raise AssertionError("camera interpolation failed")


def project(box_value, seconds, timeline, props, padding=0):
    x, y, width, height = box_value
    camera_x, zoom = camera_at(seconds, timeline, props)
    return [960 + (x - camera_x) * zoom - padding, 548 + (y - 548) * zoom - padding, width * zoom + padding * 2, height * zoom + padding * 2]


def extract_frame(video, seconds, destination):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{seconds:.4f}", "-i", str(video), "-frames:v", "1", str(destination)], check=True)
    require(destination.is_file(), f"未生成真实帧：{destination}")


def run_frame_gate(world, timeline, props, video: Path, out: Path):
    validate_world_timeline(world, timeline, props)
    require(video.is_file(), f"缺少成片：{video}")
    out.mkdir(parents=True, exist_ok=True)
    background = color(world["background"])
    actor_colors = [color(value) for value in world["actorColors"]]
    actor_fills = [color(value) for value in world["actor"]["fillColors"]]
    foreground = lambda pixel: math.dist(pixel, background) > 48
    actor_pixel = lambda pixel: min(math.dist(pixel, value) for value in actor_colors) < 38
    actor_fill_pixel = lambda pixel: min(math.dist(pixel, value) for value in actor_fills) < 32

    samples = set()
    appearance = []
    for node_id, node in world["nodes"].items():
        show = moment(timeline["events"][node["visibleEvent"]], props)
        before, after = max(0, show - 0.18), show + 0.8
        samples.update((before, after))
        appearance.append((node_id, before, after))
    for motion_value in timeline["motions"].values():
        start, end = moment(motion_value["start"], props), moment(motion_value["end"], props)
        samples.update(start + (end - start) * fraction for fraction in (0, .25, .5, .75, 1))
    samples.update(moment(item, props) for item in timeline["camera"])

    images = {}
    for index, seconds in enumerate(sorted(samples)):
        destination = out / f"frame-{index:03d}-{seconds:.3f}.png"
        extract_frame(video, seconds, destination)
        images[seconds] = Image.open(destination)

    checks = []
    for node_id, before, after in appearance:
        artwork = rect(world["nodes"][node_id]["artworkBox"], f"{node_id}.artworkBox")
        label = rect(world["nodes"][node_id]["labelBox"], f"{node_id}.labelBox")
        before_ratio = max(ratio(images[before], project(box, before, timeline, props, 12), foreground) for box in (artwork, label))
        after_ratio = max(ratio(images[after], project(box, after, timeline, props, 12), foreground) for box in (artwork, label))
        require(before_ratio < .02, f"真实帧检查发现 {node_id} 在首次口播前已经出现。")
        require(after_ratio > .012, f"真实帧检查没有在首次口播后找到 {node_id}。")
        checks.append({"kind": "appearance-order", "node": node_id, "beforeRatio": round(before_ratio, 5), "afterRatio": round(after_ratio, 5)})

    motion_times = sorted({moment(value["start"], props) + (moment(value["end"], props) - moment(value["start"], props)) * fraction for value in timeline["motions"].values() for fraction in (0, .25, .5, .75, 1)})
    for seconds in motion_times:
        image = images[seconds]
        for node_id, node in world["nodes"].items():
            if seconds < moment(timeline["events"][node["visibleEvent"]], props):
                continue
            label = rect(node["labelBox"], f"{node_id}.labelBox")
            overlap_ratio = ratio(image, project(label, seconds, timeline, props, 3), actor_pixel)
            require(overlap_ratio < .002, f"真实帧检查发现运动对象遮挡 {node_id} 文字（{seconds:.2f}s）。")
    checks.append({"kind": "text-occlusion", "frames": len(motion_times), "status": "clear"})

    visible_ratios = []
    poses = world["actor"]["poses"]
    actor_size = world["actor"]["size"]
    for motion_id, motion_value in timeline["motions"].items():
        start, end = moment(motion_value["start"], props), moment(motion_value["end"], props)
        for fraction in (.25, .5, .75):
            seconds = start + (end - start) * fraction
            progress = ease(fraction)
            position = [poses[motion_value["from"]][axis] + (poses[motion_value["to"]][axis] - poses[motion_value["from"]][axis]) * progress for axis in (0, 1)]
            visible_ratio = ratio(images[seconds], project(actor_box(position, actor_size), seconds, timeline, props, -5), actor_fill_pixel)
            require(visible_ratio >= world["actor"]["minVisibleFillRatio"], f"真实帧检查发现主角被其他图层遮挡（{motion_id}，{seconds:.2f}s）。")
            visible_ratios.append(visible_ratio)
    checks.append({"kind": "actor-visibility-layering", "frames": len(visible_ratios), "minFillRatio": round(min(visible_ratios), 5), "status": "clear"})

    sx, sy, sw, sh = rect(world["safeArea"], "safeArea")
    border_boxes = ([sx, sy, sw, 3], [sx, sy + sh - 3, sw, 3], [sx, sy, 3, sh], [sx + sw - 3, sy, 3, sh])
    for seconds, image in images.items():
        edge_ratio = max(ratio(image, border, foreground) for border in border_boxes)
        require(edge_ratio < .025, f"真实帧检查发现主画面边界存在裁切内容（{seconds:.2f}s）。")
    checks.append({"kind": "safe-area-crop", "frames": len(images), "status": "clear"})

    report = {"version": 1, "video": str(video.resolve()), "frames": len(images), "checks": checks, "status": "pass"}
    (out / "rendered-frame-gate.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report
