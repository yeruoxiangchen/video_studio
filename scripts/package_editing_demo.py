"""Package a real editing browser run and its downloaded outputs for the demo page."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(os.environ.get("CAMCAT_STUDIO_DATA", "/data/zjr/camcat_studio"))
    ffmpeg = str(root / "tools/ffmpeg/ffmpeg")
    ffprobe = str(root / "tools/ffmpeg/ffprobe")
    font = root / "tools/fonts/NotoSansCJKsc-Regular.otf"
    env = {**os.environ, "FONTCONFIG_FILE": str(root / "config/fonts.conf")}
    receipt_path = next(args.results.glob("*/acceptance.json"))
    run_dir = receipt_path.parent
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("state") != "passed":
        raise ValueError("The browser journey must pass before packaging")
    out = args.output
    parts = out / "parts"
    parts.mkdir(parents=True, exist_ok=True)

    def run(arguments: list[str]) -> None:
        result = subprocess.run(
            [ffmpeg, "-nostdin", "-y", "-v", "error", *arguments],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-4000:])

    def probe(path: Path) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(
            subprocess.check_output(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration,size:stream=codec_type,codec_name,width,height,nb_frames",
                    "-of",
                    "json",
                    str(path),
                ],
                env=env,
                text=True,
            )
        )
        return result

    def header(name: str, text: str, width: int, height: int) -> str:
        path = parts / (name + ".ass")
        path.write_text(
            "[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {width}\nPlayResY: {height}\n\n"
            "[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
            "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
            "Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: Header,Noto Sans CJK SC,28,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
            "0,0,0,0,100,100,0,0,1,0,0,7,32,32,19,1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
            "Dialogue: 0,0:00:00.00,0:59:59.00,Header,,0,0,0,," + text + "\n"
        )
        return f"ass=filename={path}:fontsdir={font.parent}"

    def join(paths: list[Path], target: Path) -> None:
        listing = parts / (target.stem + "-concat.txt")
        listing.write_text("".join(f"file '{path}'\n" for path in paths))
        run(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(target),
            ]
        )

    media_checks = {}
    for name, expected in [("first-cut", 17.0), ("revised-cut", 9.0)]:
        path = run_dir / (name + ".mp4")
        media = probe(path)
        actual = float(media["format"]["duration"])
        if abs(actual - expected) > 0.05:
            raise ValueError(f"{name} duration differs from the requested cut: {actual}")
        if not any(stream["codec_type"] == "audio" for stream in media["streams"]):
            raise ValueError(f"{name} is missing its requested source audio")
        run(["-xerror", "-i", str(path), "-threads", "2", "-f", "null", "-"])
        media_checks[name] = {
            "duration_seconds": actual,
            "audio_present": True,
            "full_decode": "passed",
        }

    markers = {m["name"]: m["seconds"] for m in receipt["markers"]}
    raw = run_dir / "video.webm"
    end = float(probe(raw)["format"]["duration"])
    analysis_seconds = round(markers["analysis_done"] - markers["upload_start"], 1)
    first_seconds = round(markers["first_edit_done"] - markers["first_edit_start"], 1)
    revision_seconds = round(markers["second_edit_done"] - markers["second_edit_start"], 1)
    first_render_seconds = round(
        markers["first-cut_render_done"] - markers["first-cut_render_start"], 1
    )
    revised_render_seconds = round(
        markers["revised-cut_render_done"] - markers["revised-cut_render_start"], 1
    )
    segments = [
        (0.0, markers["upload_start"] + 3, "01  新建项目、上传 30 秒中文原片"),
        (
            markers["analysis_done"] - 2,
            markers["first_edit_start"] + 3,
            f"02  查看原片分析，提出 17 秒剪辑要求  |  分析实际 {analysis_seconds} 秒，等待已剪短",
        ),
        (
            markers["first_edit_done"] - 2,
            markers["first-cut_render_start"] + 2,
            f"03  Agent 写入剪辑与字幕时间线  |  规划实际 {first_seconds} 秒，等待已剪短",
        ),
        (
            markers["first-cut_render_done"] - 4,
            markers["second_edit_start"] + 3,
            "04  下载第一版，继续提出精简要求  |  "
            f"导出与预览实际 {first_render_seconds} 秒，等待已剪短",
        ),
        (
            markers["second_edit_done"] - 2,
            markers["revised-cut_render_start"] + 2,
            f"05  多轮修改写入新版本  |  修改实际 {revision_seconds} 秒，等待已剪短",
        ),
        (
            markers["revised-cut_render_done"] - 4,
            end,
            f"06  下载修改版并回滚  |  导出与预览实际 {revised_render_seconds} 秒，等待已剪短",
        ),
    ]
    clips = []
    for i, (start, stop, label) in enumerate(segments):
        start, stop = max(0, start), min(end, stop)
        if stop <= start:
            raise ValueError("Invalid recording markers")
        clip = parts / f"workflow-{i}.mp4"
        vf = "scale=1920:1080,pad=1920:1152:0:72:color=0x0b1015,"
        vf += header(f"workflow-{i}", label, 1920, 1152)
        vf += ",tpad=stop_mode=clone:stop_duration=1"
        run(
            [
                "-ss",
                str(start),
                "-t",
                str(stop - start),
                "-i",
                str(raw),
                "-an",
                "-vf",
                vf,
                "-r",
                "20",
                "-c:v",
                "libx264",
                "-threads",
                "4",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                str(clip),
            ]
        )
        clips.append(clip)
    join(clips, out / "04-editing-workflow.mp4")

    comparisons = [
        (Path(receipt["input"]), 8.0, "原片：30 秒  |  此处播放前 8 秒，含原始中文旁白"),
        (run_dir / "first-cut.mp4", None, "第一版：17 秒  |  截取原片 3–20 秒，生成中文字幕"),
        (run_dir / "revised-cut.mp4", None, "修改版：9 秒  |  截取原片 3–12 秒，精简字幕措辞"),
    ]
    clips = []
    for i, (path, limit, label) in enumerate(comparisons):
        clip = parts / f"comparison-{i}.mp4"
        vf = (
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:792:(ow-iw)/2:72:color=0x0b1015,"
        )
        vf += header(f"comparison-{i}", label, 1280, 792)
        options = ["-i", str(path)]
        if limit is not None:
            options += ["-t", str(limit)]
        run(
            [
                *options,
                "-vf",
                vf,
                "-r",
                "30",
                "-c:v",
                "libx264",
                "-threads",
                "4",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(clip),
            ]
        )
        clips.append(clip)
    join(clips, out / "05-editing-comparison.mp4")
    shutil.copy2(run_dir / "revised-cut.mp4", out / "06-edited-chinese.mp4")
    shutil.copy2(run_dir / "first-timeline.png", out / "editing-workspace.png")
    files = {}
    for path in [
        out / "04-editing-workflow.mp4",
        out / "05-editing-comparison.mp4",
        out / "06-edited-chinese.mp4",
    ]:
        run(["-xerror", "-i", str(path), "-threads", "2", "-f", "null", "-"])
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        files[path.name] = {
            "sha256": digest,
            "probe": probe(path),
            "full_decode": "passed",
        }
    image = out / "editing-workspace.png"
    files[image.name] = {
        "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "bytes": image.stat().st_size,
    }
    manifest = {
        "product": "剪辑工作台",
        "kind": "Real natural-language editing browser demonstration",
        "files": files,
        "prompts": receipt["prompts"],
        "first": receipt["first"],
        "revised": receipt["revised"],
        "rollback": receipt["rollback"],
        "poster_stage": "first_cut",
        "media_checks": media_checks,
        "observed_run_seconds": {
            "analysis": analysis_seconds,
            "first_planning": first_seconds,
            "revision_planning": revision_seconds,
            "first_render_and_preview": first_render_seconds,
            "revised_render_and_preview": revised_render_seconds,
        },
        "recording": {
            "actual_browser": True,
            "waiting_shortened": True,
            "raw_recording_ranges_seconds": [[round(a, 3), round(b, 3)] for a, b, _ in segments],
            "workflow_audio": "silent screen recording",
            "comparison_audio": "actual source/output audio; no replacement narration",
            "original_excerpt_seconds": 8,
            "downloaded_final_output_unchanged": True,
        },
        "provenance": {
            "visuals": "NASA — Seeing Earth as Only NASA Can",
            "source_url": "https://svs.gsfc.nasa.gov/14831/",
            "credit": "NASA",
            "source_music": "Original Universal Production Music removed before narration",
            "source_narration": "Locally synthesized Chinese narration; authored demo script",
        },
    }
    (out / "editing-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps({"output": str(out), "files": list(files)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
