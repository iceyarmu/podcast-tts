#!/usr/bin/env python3
"""
Generate podcast-style audio via the Volcengine Seed Audio API.

Requires: httpx (pip install httpx)

Usage:
    python3 src/generate_podcast.py \
        --prompt "$(<test/podcast3.md)" \
        --filename test/podcast3.mp3 \
        --reference_audio docs/调皮男孩.mp3 docs/藕霸小童.mp3 \
        --subtitle-filename test/podcast3.subtitle.json
"""

import argparse
import base64
import binascii
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, NoReturn

API_KEY_ENV = "VOLCENGINE_APP_KEY"
API_URL_ENV = "VOLCENGINE_PODCAST_API_URL"
MODEL_ENV = "VOLCENGINE_PODCAST_MODEL"

DEFAULT_API_URL = "https://openspeech.bytedance.com/api/v3/tts/create"
DEFAULT_MODEL = "seed-audio-1.0"

REQUEST_TIMEOUT_SECONDS = 600
DOWNLOAD_TIMEOUT_SECONDS = 120
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 10

MAX_PROMPT_CHARS = 3000
MAX_REFERENCES = 3
MAX_REFERENCE_BYTES = 10 * 1024 * 1024

REFERENCE_AUDIO_SUFFIXES = {".wav", ".mp3", ".pcm", ".ogg", ".opus"}
FORMAT_BY_SUFFIX = {
    ".mp3": "mp3",
    ".wav": "wav",
    ".pcm": "pcm",
    ".ogg": "ogg_opus",
    ".opus": "ogg_opus",
}
SUPPORTED_SAMPLE_RATES = {
    "mp3": {8000, 16000, 24000, 32000, 44100, 48000},
    "wav": {8000, 16000, 24000, 32000, 40000, 44100, 48000},
    "pcm": {8000, 16000, 24000, 32000, 40000, 44100, 48000},
    "ogg_opus": {48000},
}
DEFAULT_SAMPLE_RATES = {
    "mp3": 24000,
    "wav": 24000,
    "pcm": 24000,
    "ogg_opus": 48000,
}

ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def fail(message: str) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def format_bytes(byte_count: int) -> str:
    units = ("B", "KB", "MB", "GB")
    size = float(byte_count)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{byte_count} {unit}"
            return f"{size:.2f} {unit} ({byte_count:,} bytes)"
        size /= 1024
    return f"{byte_count} B"


def can_retry(attempt: int) -> bool:
    return attempt < MAX_RETRIES


def wait_before_retry(reason: str, attempt: int) -> None:
    retry_number = attempt + 1
    print(f"{reason}; retrying in {RETRY_DELAY_SECONDS}s ({retry_number}/{MAX_RETRIES}) ...")
    time.sleep(RETRY_DELAY_SECONDS)


def raise_for_status_with_body(response: Any) -> None:
    try:
        response.raise_for_status()
    except Exception:
        try:
            response.read()
        except Exception:
            pass
        raise


def read_response_text(response: Any, max_chars: int = 2000) -> str:
    try:
        text = response.text
    except Exception:
        try:
            body = response.read()
        except Exception as exc:
            return f"<unable to read response body: {exc}>"
        if isinstance(body, bytes):
            encoding = getattr(response, "encoding", None) or "utf-8"
            text = body.decode(encoding, errors="replace")
        else:
            text = str(body)

    if len(text) > max_chars:
        return f"{text[:max_chars]}... <truncated>"
    return text


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        fail(f"{name} is not set")
    return value


def parse_env_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: Path, required: bool) -> bool:
    if not path.exists():
        if required:
            fail(f"Environment file not found: {path}")
        return False
    if not path.is_file():
        fail(f"Environment path is not a file: {path}")

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        fail(f"Unable to read environment file {path}: {exc}")

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or not ENV_NAME_PATTERN.fullmatch(name):
            fail(f"Invalid environment assignment in {path}:{line_number}")
        os.environ.setdefault(name, parse_env_value(raw_value))

    print(f"Loaded environment: {path.resolve()}")
    return True


def load_configured_environment(env_file: str | None) -> None:
    if env_file:
        load_env_file(Path(env_file).expanduser(), required=True)
        return

    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"]
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if load_env_file(resolved, required=False):
            return


def require_httpx() -> None:
    try:
        import httpx  # noqa: F401
    except ImportError:
        fail("httpx is required; install it with: pip install httpx")


def validate_prompt(prompt: str) -> str:
    if not prompt.strip():
        fail("Prompt must not be empty")
    if len(prompt) > MAX_PROMPT_CHARS:
        fail(f"Prompt is {len(prompt)} characters; the API limit is {MAX_PROMPT_CHARS}")
    return prompt


def looks_like_file_path(value: str, path: Path) -> bool:
    return (
        value.startswith((".", "~"))
        or "/" in value
        or "\\" in value
        or bool(path.suffix)
    )


def build_references(reference_values: list[str], prompt: str) -> list[dict[str, str]]:
    if len(reference_values) > MAX_REFERENCES:
        fail(
            f"At most {MAX_REFERENCES} references are supported; "
            f"received {len(reference_values)}"
        )

    references: list[dict[str, str]] = []
    for index, raw_value in enumerate(reference_values, start=1):
        value = raw_value.strip()
        if not value:
            fail("Reference audio values must not be empty")

        path = Path(value).expanduser()
        if not path.exists():
            if looks_like_file_path(value, path):
                fail(f"Reference audio file not found: {path}")
            references.append({"speaker": value})
            print(f"Reference {index}: voice {value}")
            continue

        if not path.is_file():
            fail(f"Reference audio is not a file: {path}")
        if path.suffix.lower() not in REFERENCE_AUDIO_SUFFIXES:
            supported = ", ".join(sorted(REFERENCE_AUDIO_SUFFIXES))
            fail(f"Unsupported reference audio format for {path}; expected one of: {supported}")
        try:
            size = path.stat().st_size
        except OSError as exc:
            fail(f"Unable to inspect reference audio {path}: {exc}")
        if size == 0:
            fail(f"Reference audio is empty: {path}")
        if size > MAX_REFERENCE_BYTES:
            fail(
                f"Reference audio exceeds the 10 MB API limit: {path} "
                f"({format_bytes(size)})"
            )

        try:
            audio_data = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            fail(f"Unable to read reference audio {path}: {exc}")
        references.append({"audio_data": audio_data})
        print(f"Reference {index}: audio file {path}")

        marker = f"@音频{index}"
        if marker not in prompt:
            print(f"Warning: prompt does not contain {marker} for reference audio {path}")

    return references


def resolve_audio_format(output_path: Path, requested_format: str | None) -> str:
    inferred_format = FORMAT_BY_SUFFIX.get(output_path.suffix.lower())
    if requested_format:
        if inferred_format and inferred_format != requested_format:
            fail(
                f"Output suffix {output_path.suffix} does not match "
                f"--format {requested_format}"
            )
        return requested_format
    if inferred_format:
        return inferred_format
    fail("Unable to infer output format from filename; pass --format explicitly")


def resolve_sample_rate(audio_format: str, requested_rate: int | None) -> int:
    sample_rate = requested_rate or DEFAULT_SAMPLE_RATES[audio_format]
    if sample_rate not in SUPPORTED_SAMPLE_RATES[audio_format]:
        supported = ", ".join(str(value) for value in sorted(SUPPORTED_SAMPLE_RATES[audio_format]))
        fail(f"Unsupported sample rate for {audio_format}: {sample_rate}; expected one of: {supported}")
    return sample_rate


def validate_rate(name: str, value: int, minimum: int, maximum: int) -> None:
    if value < minimum or value > maximum:
        fail(f"{name} must be between {minimum} and {maximum}")


def create_generation(
    prompt: str,
    references: list[dict[str, str]],
    audio_config: dict[str, Any],
    api_key: str,
    api_url: str,
    model: str,
) -> tuple[dict[str, Any], str, str | None]:
    import httpx

    request_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "model": model,
        "text_prompt": prompt,
        "audio_config": audio_config,
    }
    if references:
        payload["references"] = references

    headers = {
        "X-Api-Key": api_key,
        "X-Api-Request-Id": request_id,
        "Content-Type": "application/json",
    }

    print(f"Submitting generation to {api_url} ...")
    print(
        f"Model: {model}, format: {audio_config['format']}, "
        f"sample rate: {audio_config['sample_rate']} Hz, references: {len(references)}"
    )

    for attempt in range(MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = client.post(api_url, json=payload, headers=headers)
                raise_for_status_with_body(response)
                try:
                    response_json = response.json()
                except json.JSONDecodeError:
                    fail(f"API returned invalid JSON: {read_response_text(response)}")
                if not isinstance(response_json, dict):
                    fail("API response must be a JSON object")
                log_id = response.headers.get("X-Tt-Logid")
                return response_json, request_id, log_id
        except httpx.TimeoutException as exc:
            if can_retry(attempt):
                wait_before_retry("Request timed out", attempt)
                continue
            fail(f"Request timeout after {MAX_RETRIES} retries: {exc}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 502 and can_retry(attempt):
                wait_before_retry("API returned 502", attempt)
                continue
            fail(f"API error {exc.response.status_code}: {read_response_text(exc.response)}")
        except httpx.RequestError as exc:
            fail(f"Request error: {exc}")
        except Exception as exc:
            fail(f"Request error: {exc}")

    fail("Request failed without a response")


def extract_audio_value(response_json: dict[str, Any]) -> str:
    code = response_json.get("code")
    if code not in (None, 0):
        message = response_json.get("message") or "Unknown API error"
        fail(f"API error {code}: {message}")

    for key in ("audio", "url"):
        value = response_json.get(key)
        if isinstance(value, str) and value:
            return value

    keys = ", ".join(sorted(str(key) for key in response_json.keys()))
    fail(f"No audio payload in response; response fields: {keys or '<none>'}")


def decode_inline_audio_bytes(audio_value: str) -> bytes | None:
    if audio_value.startswith("data:"):
        header, separator, encoded_data = audio_value.partition(",")
        if not separator or ";base64" not in header or not encoded_data:
            return None
        audio_value = encoded_data

    if audio_value.startswith("http://") or audio_value.startswith("https://"):
        return None

    return base64.b64decode(audio_value, validate=True)


def download_audio_bytes(audio_url: str) -> bytes:
    import httpx

    for attempt in range(MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
                response = client.get(audio_url)
                raise_for_status_with_body(response)
                return response.content
        except httpx.TimeoutException as exc:
            if can_retry(attempt):
                wait_before_retry("Download timed out", attempt)
                continue
            fail(f"Download timeout after {MAX_RETRIES} retries: {exc}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 502 and can_retry(attempt):
                wait_before_retry("Download returned 502", attempt)
                continue
            fail(f"Download error {exc.response.status_code}: {read_response_text(exc.response)}")
        except httpx.RequestError as exc:
            fail(f"Download error: {exc}")
        except Exception as exc:
            fail(f"Download error: {exc}")

    fail("Download failed without a response")


def write_bytes_atomically(data: bytes, output_path: Path) -> None:
    if not data:
        fail("API returned an empty audio payload")

    temp_path = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_bytes(data)
        os.replace(temp_path, output_path)
    except OSError as exc:
        fail(f"Unable to save {output_path}: {exc}")
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def write_audio_value(audio_value: str, output_path: Path) -> None:
    if audio_value.startswith("http://") or audio_value.startswith("https://"):
        audio_bytes = download_audio_bytes(audio_value)
    else:
        try:
            decoded_audio = decode_inline_audio_bytes(audio_value)
        except (binascii.Error, ValueError) as exc:
            fail(f"Unable to decode audio payload: {exc}")
        if decoded_audio is None:
            header, _, _ = audio_value.partition(",")
            fail(f"Unsupported audio payload: {header}")
        audio_bytes = decoded_audio

    write_bytes_atomically(audio_bytes, output_path)


def write_json_atomically(value: Any, output_path: Path) -> None:
    try:
        data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as exc:
        fail(f"Unable to encode JSON for {output_path}: {exc}")
    write_bytes_atomically(data, output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate podcast-style audio via the Volcengine Seed Audio API"
    )
    parser.add_argument("--prompt", "-p", required=True, help="Audio director prompt")
    parser.add_argument("--filename", "-f", required=True, help="Output audio filename")
    parser.add_argument(
        "--reference_audio",
        nargs="+",
        default=[],
        help=(
            "Reference audio file path(s) or voice name(s), in "
            "@音频1/@音频2/@音频3 order"
        ),
    )
    parser.add_argument(
        "--format",
        choices=["mp3", "wav", "pcm", "ogg_opus"],
        help="Output format (default: inferred from filename)",
    )
    parser.add_argument("--sample-rate", type=int, help="Output sample rate in Hz (default: 24000)")
    parser.add_argument(
        "--speech-rate",
        type=int,
        default=0,
        help="Speech rate adjustment from -50 to 100 (default: 0)",
    )
    parser.add_argument(
        "--loudness-rate",
        type=int,
        default=0,
        help="Loudness adjustment from -50 to 100 (default: 0)",
    )
    parser.add_argument(
        "--pitch-rate",
        type=int,
        default=0,
        help="Pitch adjustment from -12 to 12 (default: 0)",
    )
    parser.add_argument(
        "--enable-subtitle",
        action="store_true",
        help="Request subtitles and print the returned subtitle text",
    )
    parser.add_argument(
        "--subtitle-filename",
        help="Write returned subtitle metadata as JSON; also enables subtitles",
    )
    parser.add_argument(
        "--env-file",
        help="Environment file to load (default: .env in the current directory or repository)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_configured_environment(args.env_file)
    require_httpx()

    api_key = required_env(API_KEY_ENV)
    api_url = os.environ.get(API_URL_ENV) or DEFAULT_API_URL
    model = os.environ.get(MODEL_ENV) or DEFAULT_MODEL

    prompt = validate_prompt(args.prompt)
    output_path = Path(args.filename).expanduser()
    if output_path.exists() and output_path.is_dir():
        fail(f"Output path is a directory: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    subtitle_path: Path | None = None
    if args.subtitle_filename:
        subtitle_path = Path(args.subtitle_filename).expanduser()
        if subtitle_path.exists() and subtitle_path.is_dir():
            fail(f"Subtitle output path is a directory: {subtitle_path}")
        subtitle_path.parent.mkdir(parents=True, exist_ok=True)

    references = build_references(args.reference_audio, prompt)

    audio_format = resolve_audio_format(output_path, args.format)
    sample_rate = resolve_sample_rate(audio_format, args.sample_rate)
    validate_rate("--speech-rate", args.speech_rate, -50, 100)
    validate_rate("--loudness-rate", args.loudness_rate, -50, 100)
    validate_rate("--pitch-rate", args.pitch_rate, -12, 12)

    enable_subtitle = args.enable_subtitle or bool(args.subtitle_filename)
    audio_config = {
        "format": audio_format,
        "sample_rate": sample_rate,
        "speech_rate": args.speech_rate,
        "loudness_rate": args.loudness_rate,
        "pitch_rate": args.pitch_rate,
        "enable_subtitle": enable_subtitle,
    }
    start_time = time.perf_counter()
    response_json, request_id, log_id = create_generation(
        prompt,
        references,
        audio_config,
        api_key,
        api_url,
        model,
    )
    audio_value = extract_audio_value(response_json)
    subtitle = response_json.get("subtitle")
    if subtitle_path and not isinstance(subtitle, dict):
        fail("Subtitles were requested but the API response did not contain subtitle metadata")

    write_audio_value(audio_value, output_path)

    if subtitle_path:
        if not isinstance(subtitle, dict):
            fail("Subtitles were requested but the API response did not contain subtitle metadata")
        write_json_atomically(subtitle, subtitle_path)
        print(f"Subtitle metadata saved: {subtitle_path.resolve()}")
    elif args.enable_subtitle and isinstance(subtitle, dict):
        subtitle_text = subtitle.get("text")
        if isinstance(subtitle_text, str) and subtitle_text:
            print(f"Subtitle: {subtitle_text}")

    elapsed_seconds = time.perf_counter() - start_time
    file_size = output_path.stat().st_size
    duration = response_json.get("duration")
    original_duration = response_json.get("original_duration")

    print(f"\nAudio saved: {output_path.resolve()}")
    print(f"File size: {format_bytes(file_size)}")
    if isinstance(duration, (int, float)):
        print(f"Duration: {duration:.3f} seconds")
    if isinstance(original_duration, (int, float)):
        print(f"Original duration: {original_duration:.3f} seconds")
    print(f"Request ID: {request_id}")
    if log_id:
        print(f"Server log ID: {log_id}")
    print(f"Generation time: {elapsed_seconds:.2f} seconds")


if __name__ == "__main__":
    main()
