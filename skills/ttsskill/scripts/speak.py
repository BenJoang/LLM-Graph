#!/usr/bin/env python3
"""Generate Hu Tao-style speech through an IndexTTS OpenAI-compatible API."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_API_BASE = "http://192.168.10.71:8092"
DEFAULT_MODEL = "/models/indextts"
DEFAULT_LANGUAGE = "zhen"
MAX_REFERENCE_BYTES = 10 * 1024 * 1024

LANGUAGE_ALIASES = {
    "zh": "zh",
    "chinese": "zh",
    "en": "en",
    "english": "en",
    "zhen": "zhen",
    "mixed": "zhen",
    "zh-en": "zhen",
    "chinese-english": "zhen",
    "ja": "ja",
    "japanese": "ja",
    "yue": "yue",
    "cantonese": "yue",
}


def normalize_language(value: str) -> str:
    language = LANGUAGE_ALIASES.get(value.strip().lower())
    if language is None:
        supported = ", ".join(("zhen", "zh", "en", "ja", "yue"))
        raise argparse.ArgumentTypeError(f"不支持的语言 {value!r}；可用值：{supported}")
    return language


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clone the bundled Hu Tao reference voice with IndexTTS 2.5."
    )
    parser.add_argument("text", help="Text to synthesize.")
    parser.add_argument(
        "--api-base",
        default=os.environ.get("INDEXTTS_URL", DEFAULT_API_BASE),
        help="IndexTTS server base URL (default: INDEXTTS_URL or %(default)s).",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model ID exposed by the server.")
    parser.add_argument(
        "--language",
        type=normalize_language,
        default=DEFAULT_LANGUAGE,
        help="IndexTTS language code (default: zhen for mixed Chinese-English; zh/en/ja/yue also supported).",
    )
    parser.add_argument("--speed", type=float, default=0.9, help="Speech speed from 0.25 to 4.0.")
    parser.add_argument("--seed", type=int, default=42, help="Generation seed.")
    parser.add_argument("--output", help="Destination .wav path; relative paths use the current directory.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--save", action="store_true", help="Save the generated WAV without playing it.")
    mode_group.add_argument("--play", action="store_true", help="Save and then play the generated WAV.")
    mode_group.add_argument(
        "--play-only",
        action="store_true",
        help="Explicitly select the default: play from a temporary WAV, then delete it.",
    )
    parser.add_argument("--timeout", type=float, default=110.0, help="HTTP timeout in seconds.")
    parser.add_argument("--reference-audio", help="Override the bundled reference WAV.")
    parser.add_argument("--reference-text-file", help="Transcript file matching --reference-audio.")
    return parser


def default_paths() -> tuple[Path, Path, Path]:
    skill_dir = Path(__file__).resolve().parent.parent
    project_root = skill_dir.parent.parent
    return (
        skill_dir / "assets" / "hutao-default.wav",
        skill_dir / "assets" / "hutao-default.txt",
        project_root / "outputs" / "tts",
    )


def resolve_output(value: str | None, output_dir: Path) -> Path:
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = output_dir / f"hutao-{stamp}.wav"
    path = path.resolve()
    if path.suffix.lower() != ".wav":
        raise ValueError("输出文件必须使用 .wav 扩展名")
    return path


def make_audio_data_uri(audio_path: Path) -> str:
    if not audio_path.is_file():
        raise FileNotFoundError(f"参考音频不存在：{audio_path}")
    size = audio_path.stat().st_size
    if size > MAX_REFERENCE_BYTES:
        raise ValueError(f"参考音频超过服务端 10 MB 限制：{size} bytes")
    mime_type = mimetypes.guess_type(audio_path.name)[0] or "audio/wav"
    encoded = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def synthesize(
    *,
    api_base: str,
    model: str,
    text: str,
    language: str,
    speed: float,
    seed: int,
    reference_audio: Path,
    reference_text: str,
    timeout: float,
) -> bytes:
    payload = {
        "input": text,
        "model": model,
        "response_format": "wav",
        "speed": speed,
        "stream": False,
        "task_type": "Base",
        "extra_params": {"lang": language},
        "ref_audio": make_audio_data_uri(reference_audio),
        "ref_text": reference_text,
        "x_vector_only_mode": False,
        "seed": seed,
    }
    endpoint = api_base.rstrip("/") + "/v1/audio/speech"
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"IndexTTS 返回 HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"无法连接 IndexTTS 服务 {endpoint}: {exc.reason}") from exc

    if not data.startswith(b"RIFF"):
        preview = data[:500].decode("utf-8", errors="replace")
        raise RuntimeError(f"服务未返回 WAV（Content-Type: {content_type}）：{preview}")
    return data


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=path.stem + "-", suffix=".tmp", dir=path.parent, delete=False
        ) as temp_file:
            temp_file.write(data)
            temp_name = temp_file.name
        Path(temp_name).replace(path)
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def play_wav(path: Path) -> None:
    if sys.platform != "win32":
        raise RuntimeError("--play 当前仅支持 Windows；音频已经成功保存")
    import winsound

    winsound.PlaySound(str(path), winsound.SND_FILENAME)


def play_temporary_wav(data: bytes) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="ttsskill-play-", suffix=".wav", delete=False
        ) as temp_file:
            temp_file.write(data)
            temp_path = Path(temp_file.name)
        play_wav(temp_path)
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    text = args.text.strip()
    if not text:
        parser.error("text 不能为空")
    if not 0.25 <= args.speed <= 4.0:
        parser.error("--speed 必须在 0.25 到 4.0 之间")
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    if args.play_only and args.output:
        parser.error("--play-only 不会保存文件，不能同时使用 --output")

    default_audio, default_text_file, output_dir = default_paths()
    reference_audio = Path(args.reference_audio).expanduser().resolve() if args.reference_audio else default_audio
    reference_text_file = (
        Path(args.reference_text_file).expanduser().resolve()
        if args.reference_text_file
        else default_text_file
    )
    if not reference_text_file.is_file():
        raise FileNotFoundError(f"参考文本不存在：{reference_text_file}")
    reference_text = reference_text_file.read_text(encoding="utf-8").strip()
    if not reference_text:
        raise ValueError(f"参考文本为空：{reference_text_file}")

    audio = synthesize(
        api_base=args.api_base,
        model=args.model,
        text=text,
        language=args.language,
        speed=args.speed,
        seed=args.seed,
        reference_audio=reference_audio,
        reference_text=reference_text,
        timeout=args.timeout,
    )

    should_save = args.save or args.play or args.output is not None
    should_play = args.play or args.play_only or not should_save

    output: Path | None = None
    if should_save:
        output = resolve_output(args.output, output_dir)
        atomic_write(output, audio)
    if should_play:
        if output:
            play_wav(output)
        else:
            play_temporary_wav(audio)

    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output) if output else None,
                "bytes": len(audio),
                "played": should_play,
                "saved": output is not None,
                "reference_audio": str(reference_audio),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"TTS 生成失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
