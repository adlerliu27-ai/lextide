"""CLI: local preview, per-run consent, synthesis, and offline verification."""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .config import Settings
from .errors import StudioError
from .text import PreparedText, prepare_text


def _source_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", help="直接输入英文短文本（可能记录在 shell history）")
    group.add_argument("--input", type=Path, help="UTF-8 TXT 文件")


def _media_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ffmpeg", help="ffmpeg 可执行文件路径；默认自动发现")
    parser.add_argument("--ffprobe", help="ffprobe 可执行文件路径；默认自动发现")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lextide", description="Lextide — A Text-to-Speech and Listening Studio."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    gui = sub.add_parser("gui", help="启动原生 macOS 桌面界面（需 desktop 可选依赖）")
    gui.add_argument(
        "--data-dir", type=Path, help="桌面设置目录，默认用户 Application Support/Lextide/desktop"
    )
    preview = sub.add_parser("preview", help="本地预览，不联网")
    _source_arguments(preview)
    preview.add_argument("--json", action="store_true", help="输出预览 JSON（含文本，请妥善保管）")
    synth = sub.add_parser("synthesize", help="确认后合成并验证保存，支持在线或已安装的离线引擎")
    _source_arguments(synth)
    synth.add_argument(
        "--out-dir", type=Path, required=True, help="本地输出父目录，每次创建唯一 job 目录"
    )
    synth.add_argument("--engine", choices=("edge-tts", "kokoro"), default="edge-tts")
    synth.add_argument("--voice", help="edge 默认 en-US-GuyNeural；Kokoro 默认 af_heart")
    synth.add_argument("--rate", default="-4%", help="默认 -4%%；负值写作 --rate=-4%%")
    synth.add_argument("--timeout", type=float, default=90, help="引擎合成阶段上限秒数，1–300")
    synth.add_argument(
        "--allow-online", action="store_true", help="明确授权把预览后的文本发往微软在线服务"
    )
    synth.add_argument(
        "--confirm-text", metavar="SHA256", help="已审核 preview 的文本指纹；否则交互确认"
    )
    _media_arguments(synth)
    synth.add_argument("--model-dir", type=Path, help="Kokoro 独立模型存储目录")
    synth.add_argument("--offline-python", help="已安装 Kokoro runtime 的 Python 3.13 路径")
    synth.add_argument(
        "--kokoro-audio-filter",
        choices=("on", "off"),
        default="on",
        help=(
            "Kokoro PCM 滤波，默认 on（v3：9600Hz/2800Hz 与 4800Hz/60Hz 带阻，10000Hz 低通）；"
            "Edge 忽略此选项"
        ),
    )
    verify = sub.add_parser("verify", help="依据成功 manifest 在本地重新验证，无需联网")
    verify.add_argument("job_dir", type=Path)
    _media_arguments(verify)
    engines = sub.add_parser("engines", help="本地查询引擎能力；不联网、不加载模型")
    engines.add_argument("--model-dir", type=Path)
    engines.add_argument("--offline-python")
    models = sub.add_parser("models", help="显式管理可选离线模型")
    actions = models.add_subparsers(dest="model_action", required=True)
    for action in ("status", "install", "verify", "uninstall"):
        child = actions.add_parser(action)
        child.add_argument(
            "--model-dir", type=Path, help="独立模型目录；也可设置 LEXTIDE_MODEL_DIR"
        )
        if action == "install":
            child.add_argument(
                "--allow-download", action="store_true", help="明确授权下载目录所列资源"
            )
            child.add_argument("--source-dir", type=Path, help="仅从已有资源目录复制并校验，不联网")
            child.add_argument("--retries", type=int, default=2, help="有限下载重试次数，0–5")
        if action == "uninstall":
            child.add_argument("--yes", action="store_true", help="确认删除此受管模型及其下载残片")
    from .longform.cli import register_commands

    register_commands(sub)
    return parser


def _models_command(args: argparse.Namespace) -> None:
    from .models import default_model_dir, install_model, model_status, uninstall_model

    root = args.model_dir if args.model_dir is not None else default_model_dir()
    if args.model_action == "install":
        # Show trusted catalog origin/size and current state before any possible download.
        print(json.dumps(model_status(root), ensure_ascii=False, indent=2), flush=True)
        report = install_model(
            root,
            allow_download=args.allow_download,
            source_dir=args.source_dir,
            retries=args.retries,
        )
    elif args.model_action == "uninstall":
        report = uninstall_model(root, confirmed=args.yes)
    else:
        report = model_status(root, verify=args.model_action == "verify")
        if args.model_action == "verify" and not report.get("ready", False):
            print(json.dumps(report, ensure_ascii=False, indent=2))
            raise StudioError("MODEL_NOT_READY", "模型未安装或完整性校验失败。", 7)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _show_preview(prepared: PreparedText, *, as_json: bool = False) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "tts_text": prepared.tts_text,
                    "subtitle_text": prepared.tts_text,
                    "text_sha256": prepared.sha256,
                    "changes": prepared.changes,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print("--- 待合成文本预览（仅本地）---")
    print(prepared.tts_text)
    print("--- 预览结束 ---")
    print(f"清理记录：{len(prepared.changes)} 项；文字指纹：{prepared.sha256}")
    for change in prepared.changes:
        print(
            f"  {change['original_span']} → {change['new_span']}: "
            f"{change['before']!r} → {change['after']!r}"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "gui":
            from .desktop.app import launch

            return launch(data_dir=args.data_dir)
        if args.command in {"long", "tasks"}:
            from .longform.cli import handle_command

            return handle_command(args)
        if args.command == "models":
            _models_command(args)
            return 0
        if args.command == "engines":
            from .engines.edge import EdgeEngine
            from .engines.kokoro import KokoroEngine

            print(
                json.dumps(
                    [
                        asdict(EdgeEngine().capabilities()),
                        asdict(
                            KokoroEngine(
                                model_dir=args.model_dir, offline_python=args.offline_python
                            ).capabilities()
                        ),
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "verify":
            from .jobs import verify_job

            print(
                json.dumps(
                    verify_job(args.job_dir, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        prepared = prepare_text(text=args.text, input_path=args.input)
        _show_preview(prepared, as_json=getattr(args, "json", False))
        if args.command == "preview":
            return 0
        voice = args.voice or ("en-US-GuyNeural" if args.engine == "edge-tts" else "af_heart")
        settings = Settings(voice, args.rate, args.timeout, args.engine)
        settings.validate()
        if args.engine == "edge-tts" and not args.allow_online:
            raise StudioError("ONLINE_NOT_AUTHORIZED", "缺少 --allow-online，未发送请求。", 3)
        if args.engine == "edge-tts":
            print("授权范围：本次待合成文本和 voice/rate 将发送给微软在线服务。")
        else:
            print("离线模式：文本仅在本地处理；不会联网下载或转交在线引擎。")
        confirmed = args.confirm_text
        if confirmed is None:
            if not sys.stdin.isatty():
                raise StudioError(
                    "TEXT_NOT_CONFIRMED", "非交互调用必须提供已审核的 --confirm-text 指纹。", 3
                )
            prompt = (
                "确认以上文本并授权本次发送？"
                if args.engine == "edge-tts"
                else "确认以上文本用于本地离线合成？"
            )
            if input(prompt + "输入 YES 继续：").strip() != "YES":
                raise StudioError("TEXT_NOT_CONFIRMED", "未确认文本，未发送请求。", 3)
            confirmed = prepared.sha256
        if confirmed != prepared.sha256:
            raise StudioError("TEXT_NOT_CONFIRMED", "文本指纹不匹配；请重新 preview 并审核。", 3)
        from .jobs import synthesize_job

        if args.engine == "edge-tts":
            from .engines.edge import EdgeEngine

            engine = EdgeEngine()
        else:
            from .engines.kokoro import KokoroEngine

            engine = KokoroEngine(
                model_dir=args.model_dir,
                offline_python=args.offline_python,
                ffmpeg=args.ffmpeg,
                ffprobe=args.ffprobe,
                audio_filter=args.kokoro_audio_filter,
            )

        output = asyncio.run(
            synthesize_job(
                prepared,
                settings,
                args.out_dir,
                engine,
                allow_online=args.allow_online if engine.online else False,
                confirmed_sha256=confirmed,
                ffmpeg=args.ffmpeg,
                ffprobe=args.ffprobe,
            )
        )
        print(f"已验证并保存：{output}")
        return 0
    except StudioError as exc:
        print(f"ERROR [{exc.code}]: {exc}", file=sys.stderr)
        return exc.exit_code
    except (KeyboardInterrupt, EOFError):
        print("ERROR [CANCELLED]: 已取消；没有发布新的成功产物。", file=sys.stderr)
        return 130
    except OSError:
        print("ERROR [LOCAL_IO_FAILED]: 本地文件或终端读写失败。", file=sys.stderr)
        return 6
    except Exception:
        # Never dump upstream URLs, cookies, source text or private file paths on failure.
        print(
            "ERROR [INTERNAL_ERROR]: 未预期错误；请以自撰最短文本复现并反馈命令和版本。",
            file=sys.stderr,
        )
        return 1
