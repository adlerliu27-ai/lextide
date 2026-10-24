"""Long-task commands, separate from the accepted short-text CLI."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ..config import Settings
from ..errors import StudioError
from .document import chunk_document, prepare_document
from .media import LongEngine
from .tasks import cancel_task, create_task, default_cache_dir, run_task, task_status


def _source(parser, *, required=True):
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--text", help="英文正文；私人文字建议使用 TXT")
    group.add_argument("--input", type=Path, help="UTF-8 TXT（最多 1 MiB）")


def _runtime(parser):
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--offline-python")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument(
        "--allow-online", action="store_true", help="仅本次授权 Edge；恢复不会继承旧授权"
    )


def register_commands(subparsers):
    long = subparsers.add_parser("long", help="预览或开始可恢复的长文任务")
    _source(long)
    _runtime(long)
    long.add_argument("--preview", action="store_true", help="仅本地预览、分块；不创建任务、不联网")
    long.add_argument("--out-dir", type=Path, help="任务父目录；实际合成时必填")
    long.add_argument("--cache-dir", type=Path, help="与任务目录分开的 PCM 缓存目录")
    long.add_argument("--engine", choices=("edge-tts", "kokoro"), default="edge-tts")
    long.add_argument("--voice")
    long.add_argument("--rate", default="-4%")
    long.add_argument("--timeout", type=float, default=90, help="每块合成超时秒数，1–300")
    long.add_argument(
        "--chunk-bytes", type=int, help="单块上限；Edge 按 XML 转义 UTF-8，Kokoro 按 UTF-8"
    )
    long.add_argument(
        "--chunk-pause-ms", type=int, default=0, help="块间额外静音，0–2000ms；默认不新增"
    )
    long.add_argument(
        "--kokoro-audio-filter",
        choices=("on", "off"),
        default="on",
        help="新 Kokoro 任务默认 on（kokoro-hf-v3）；off 不加滤波链，Edge 忽略此选项",
    )
    long.add_argument(
        "--confirm-text", metavar="SHA256", help="确认已审核的全文指纹；否则交互输入 YES"
    )
    tasks = subparsers.add_parser("tasks", help="长文任务状态、恢复和取消")
    actions = tasks.add_subparsers(dest="task_action", required=True)
    for name in ("status", "resume", "cancel"):
        action = actions.add_parser(name)
        action.add_argument("task_dir", type=Path)
        if name == "resume":
            _runtime(action)
            _source(action, required=False)
            action.add_argument("--engine", choices=("edge-tts", "kokoro"))
            action.add_argument("--voice")
            action.add_argument("--rate")
            action.add_argument("--chunk-bytes", type=int)
            action.add_argument("--chunk-pause-ms", type=int)
            action.add_argument(
                "--kokoro-audio-filter",
                choices=("on", "off"),
                help="可选一致性检查，须与原任务相同；恢复保留原滤波版本，升级需新建任务",
            )


def _progress(report):
    print(
        f"进度 {report['completed_chunks']}/{report['total_chunks']}"
        f"；本块缓存命中={report['current_cache_hit']}",
        flush=True,
    )


def _execute(task, args, **kwargs):
    print(f"任务路径：{task}", flush=True)
    output = asyncio.run(
        run_task(
            task,
            allow_online=args.allow_online,
            model_dir=args.model_dir,
            offline_python=args.offline_python,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            progress=_progress,
            **kwargs,
        )
    )
    if output is None:
        print("任务已安全取消；有效块已保留，可显式 resume。")
        return 130
    print(f"已验证并保存：{output}")
    return 0


def handle_command(args: argparse.Namespace) -> int:
    if args.command == "tasks":
        if args.task_action == "status":
            print(json.dumps(task_status(args.task_dir), ensure_ascii=False, indent=2))
            return 0
        if args.task_action == "cancel":
            print(json.dumps(cancel_task(args.task_dir), ensure_ascii=False, indent=2))
            return 0
        expected = (
            prepare_document(text=args.text, input_path=args.input)
            if args.text is not None or args.input is not None
            else None
        )
        return _execute(
            args.task_dir,
            args,
            expected_input=expected,
            expected_options={
                "engine": args.engine,
                "voice": args.voice,
                "rate": args.rate,
                "chunk_bytes": args.chunk_bytes,
                "pause_ms": args.chunk_pause_ms,
                "audio_filter": args.kokoro_audio_filter,
            },
        )
    prepared = prepare_document(text=args.text, input_path=args.input)
    voice = args.voice or ("af_heart" if args.engine == "kokoro" else "en-US-GuyNeural")
    settings = Settings(voice, args.rate, args.timeout, args.engine)
    settings.validate()
    plan = chunk_document(prepared, args.engine, max_bytes=args.chunk_bytes)
    print("--- 长文全文预览（本地）---")
    print(prepared.tts_text)
    print(f"--- 预览结束：{len(plan['segments'])} segments / {len(plan['chunks'])} chunks ---")
    print(f"清理修改：{len(prepared.changes)}；全文 SHA256：{prepared.sha256}")
    if args.preview:
        return 0
    if args.out_dir is None:
        raise StudioError("INVALID_INPUT", "长文合成需要 --out-dir。", 2)
    if args.engine == "edge-tts" and not args.allow_online:
        raise StudioError("ONLINE_NOT_AUTHORIZED", "缺少本次 --allow-online；未发送请求。", 3)
    confirmed = args.confirm_text
    if confirmed is None and sys.stdin.isatty():
        notice = (
            "全文将分块发送给微软在线服务" if args.engine == "edge-tts" else "全文仅在本地离线合成"
        )
        if input(f"{notice}。确认以上全文请输入 YES：").strip() == "YES":
            confirmed = prepared.sha256
    if confirmed != prepared.sha256:
        raise StudioError(
            "TEXT_NOT_CONFIRMED", "需审核全文并确认正确 --confirm-text 指纹或输入 YES。", 3
        )
    engine = LongEngine(
        settings,
        model_dir=args.model_dir,
        offline_python=args.offline_python,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
    )
    task = create_task(
        prepared,
        settings,
        args.out_dir,
        args.cache_dir or default_cache_dir(),
        engine=engine,
        chunk_bytes=args.chunk_bytes,
        audio_filter=args.kokoro_audio_filter,
        pause_ms=args.chunk_pause_ms,
    )
    return _execute(task, args)
