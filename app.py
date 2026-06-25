#!/usr/bin/env python3
import concurrent.futures
import array
import json
import math
import mimetypes
import os
import re
import shlex
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen


HOST = os.environ.get("LOUDNESS_API_HOST", "0.0.0.0")
PORT = int(os.environ.get("LOUDNESS_API_PORT", "8080"))
TIMEOUT_SECONDS = int(os.environ.get("LOUDNESS_API_TIMEOUT_SECONDS", "60"))
PROCESSING_TIMEOUT_SECONDS = int(
    os.environ.get("LOUDNESS_API_PROCESSING_TIMEOUT_SECONDS", "120")
)
DOWNLOAD_TIMEOUT_SECONDS = int(
    os.environ.get("LOUDNESS_API_DOWNLOAD_TIMEOUT_SECONDS", "60")
)
WEBHOOK_TIMEOUT_SECONDS = int(os.environ.get("LOUDNESS_API_WEBHOOK_TIMEOUT_SECONDS", "30"))
API_KEY = os.environ.get("LOUDNESS_API_KEY", "")
MAX_BODY_BYTES = int(os.environ.get("LOUDNESS_API_MAX_BODY_BYTES", "65536"))
DOC_FILE = Path(os.environ.get("LOUDNESS_API_DOC_FILE", "/app/measure-loudness.md"))
DATA_DIR = Path(os.environ.get("LOUDNESS_API_DATA_DIR", "/data"))
LOG_FILE = Path(os.environ.get("LOUDNESS_API_LOG_FILE", str(DATA_DIR / "ffmpeg-api.log")))
OUTPUT_ROOT = DATA_DIR / "outputs"
OUTPUT_TTL_SECONDS = int(os.environ.get("LOUDNESS_API_OUTPUT_TTL_SECONDS", "7200"))
PUBLIC_BASE_URL = os.environ.get(
    "LOUDNESS_API_PUBLIC_BASE_URL", "https://sound-mixer-api.jamrockdev.com"
).rstrip("/")
DEFAULT_MUX_LOGO_URL = os.environ.get(
    "LOUDNESS_API_MUX_LOGO_URL", "https://thesingingleague.com/TSL_Logo.png"
)
DEFAULT_ALLOWED_WEBHOOK_URLS = (
    "https://fvaaxuhjofhbngkalqcv.supabase.co/functions/v1/*,"
    "https://sxlwnmfsiahrqxfpkqmz.supabase.co/functions/v1/receive-vocals"
)
ALLOWED_WEBHOOK_PATTERNS = {
    url.strip()
    for url in os.environ.get(
        "LOUDNESS_API_ALLOWED_WEBHOOK_URLS",
        os.environ.get("LOUDNESS_API_ALLOWED_WEBHOOK_URL", DEFAULT_ALLOWED_WEBHOOK_URLS),
    ).split(",")
    if url.strip()
}
ALLOWED_WEBHOOK_URLS = {url for url in ALLOWED_WEBHOOK_PATTERNS if not url.endswith("*")}
ALLOWED_WEBHOOK_PREFIXES = tuple(
    url[:-1] for url in ALLOWED_WEBHOOK_PATTERNS if url.endswith("*")
)
MAX_WORKERS = int(os.environ.get("LOUDNESS_API_MAX_WORKERS", "2"))
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_API_URL = os.environ.get("RESEND_API_URL", "https://api.resend.com/emails")
NOTIFY_EMAIL_TO = os.environ.get("LOUDNESS_API_NOTIFY_EMAIL_TO", "brian@jamrockdev.com")
NOTIFY_EMAIL_FROM = os.environ.get(
    "LOUDNESS_API_NOTIFY_EMAIL_FROM", "no-reply-server@mail.zalkinteractive.com"
)
EMAIL_TIMEOUT_SECONDS = int(os.environ.get("LOUDNESS_API_EMAIL_TIMEOUT_SECONDS", "15"))
CORS_ALLOWED_ORIGINS = {
    origin.strip()
    for origin in os.environ.get("SOUND_MIXER_ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
}
CORS_ALLOWED_METHODS = "GET, POST, HEAD, OPTIONS"
CORS_ALLOWED_HEADERS = "Content-Type, X-API-KEY, X-Filename"

LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json"
LOUDNORM_JSON_RE = re.compile(r'\{[^{}]*"input_i"\s*:[^{}]*\}', re.DOTALL)
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
INPUT_LABEL_RE = re.compile(r"^in_[1-9][0-9]*$")
OUTPUT_LABEL_RE = re.compile(r"^out_[1-9][0-9]*$")
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,180}$")
ASTATS_CHANNEL_RE = re.compile(r"\[Parsed_astats_[^\]]+\] Channel: (\d+)")
ASTATS_VALUE_RE = re.compile(r"\[Parsed_astats_[^\]]+\] ([A-Za-z][A-Za-z ]+): (.+)")
SILENCE_START_RE = re.compile(r"silence_start: ([0-9.]+)")
SILENCE_END_RE = re.compile(r"silence_end: ([0-9.]+)")
FLOAT_FIELDS = (
    "input_i",
    "input_tp",
    "input_lra",
    "input_thresh",
    "output_i",
    "output_tp",
    "output_lra",
    "output_thresh",
    "target_offset",
)

OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
job_executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)
email_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
jobs = {}
jobs_lock = threading.Lock()
log_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def utc_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def truncate(value, max_chars=4000):
    if value is None:
        return None
    text = str(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"


def json_safe(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    return value


def log_event(event, **fields):
    entry = json_safe({"ts": utc_timestamp(), "event": event, **fields})
    line = json.dumps(entry, sort_keys=True, default=str, allow_nan=False)
    with log_lock:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
        except OSError as exc:
            print(f"Failed to write log file: {exc}", flush=True)
    print(line, flush=True)


def send_notification(subject, details):
    if not RESEND_API_KEY:
        log_event("email_skipped", reason="missing_resend_api_key", subject=subject)
        return

    text = "\n".join(f"{key}: {value}" for key, value in details.items())
    payload = {
        "from": NOTIFY_EMAIL_FROM,
        "to": [NOTIFY_EMAIL_TO],
        "subject": subject,
        "text": text,
    }
    request = Request(
        RESEND_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ffmpeg-sound-mixer-api/0.1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=EMAIL_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
            log_event(
                "email_sent",
                subject=subject,
                status=response.status,
                response=truncate(body, 1000),
            )
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        log_event(
            "email_failed",
            subject=subject,
            status=exc.code,
            response=truncate(body, 1000),
        )
    except Exception as exc:
        log_event("email_failed", subject=subject, error=truncate(exc, 1000))


def notify_measurement_success(request_id, audio_url, result):
    email_executor.submit(
        send_notification,
        "[FFmpeg API] Loudness measurement completed",
        {
            "event": "measure-loudness",
            "status": "COMPLETED",
            "request_id": request_id,
            "audio_url": audio_url,
            "input_i": result.get("input_i"),
            "input_tp": result.get("input_tp"),
            "input_lra": result.get("input_lra"),
        },
    )


def notify_measurement_failure(request_id, status_code, error, audio_url=None):
    email_executor.submit(
        send_notification,
        "[FFmpeg API] Loudness measurement failed",
        {
            "event": "measure-loudness",
            "status": "FAILED",
            "status_code": status_code,
            "request_id": request_id,
            "audio_url": audio_url or "",
            "error": truncate(error, 2000),
        },
    )


def notify_job_success(command_id, request_payload, output_urls):
    email_executor.submit(
        send_notification,
        "[FFmpeg API] FFmpeg job completed",
        {
            "event": "run-ffmpeg-command",
            "status": "COMPLETED",
            "command_id": command_id,
            "output_files": json.dumps(request_payload.get("output_files", {}), sort_keys=True),
            "storage_urls": json.dumps(output_urls, sort_keys=True),
            "webhook_metadata": json.dumps(
                request_payload.get("webhook_metadata", {}), sort_keys=True
            ),
        },
    )


def notify_job_failure(command_id, request_payload, error, status_code=None):
    email_executor.submit(
        send_notification,
        "[FFmpeg API] FFmpeg job failed",
        {
            "event": "run-ffmpeg-command",
            "status": "FAILED",
            "status_code": status_code or "",
            "command_id": command_id or "",
            "output_files": json.dumps(request_payload.get("output_files", {}), sort_keys=True)
            if isinstance(request_payload, dict)
            else "",
            "webhook_metadata": json.dumps(
                request_payload.get("webhook_metadata", {}), sort_keys=True
            )
            if isinstance(request_payload, dict)
            else "",
            "error": truncate(error, 2000),
        },
    )


def describe_vocal_shift(shift_ms):
    try:
        shift_ms = int(shift_ms or 0)
    except (TypeError, ValueError):
        shift_ms = 0

    if shift_ms > 0:
        return f"vocal shifted later/backward by {shift_ms} ms ({shift_ms / 1000:.3f} s)"
    if shift_ms < 0:
        return (
            f"vocal shifted earlier/forward by {abs(shift_ms)} ms "
            f"({abs(shift_ms) / 1000:.3f} s)"
        )
    return "vocal not shifted"


def timing_email_details(analysis, decisions):
    timing_alignment = analysis.get("timing_alignment", {}) if isinstance(analysis, dict) else {}
    applied_shift_ms = decisions.get(
        "applied_vocal_shift_ms",
        timing_alignment.get("applied_vocal_shift_ms", 0),
    )
    try:
        applied_shift_ms = int(applied_shift_ms or 0)
    except (TypeError, ValueError):
        applied_shift_ms = 0
    return {
        "timing_alignment_applied": decisions.get(
            "timing_alignment_applied", timing_alignment.get("applied", False)
        ),
        "detected_vocal_delay_ms": decisions.get(
            "detected_vocal_delay_ms",
            timing_alignment.get("detected_vocal_delay_ms", 0),
        ),
        "applied_vocal_shift_ms": applied_shift_ms,
        "applied_vocal_shift_seconds": round((applied_shift_ms or 0) / 1000, 3),
        "applied_vocal_shift_direction": describe_vocal_shift(applied_shift_ms),
        "timing_alignment_method": timing_alignment.get("method", ""),
        "timing_alignment_confidence": decisions.get(
            "timing_alignment_confidence", timing_alignment.get("confidence", 0)
        ),
        "timing_alignment_best_score": timing_alignment.get("best_score", ""),
        "timing_alignment_comparison_score": timing_alignment.get("comparison_score", ""),
        "timing_alignment_adjacent_score": timing_alignment.get("adjacent_score", ""),
        "timing_alignment_reason": timing_alignment.get("reason", ""),
    }


def notify_mix_success(
    command_id,
    request_payload,
    output_urls,
    analysis,
    decisions,
    debug_output_urls=None,
    debug_error="",
):
    webhook_result = request_payload.get("_webhook_result") or {}
    details = {
        "event": "analyze-and-mix",
        "status": "COMPLETED"
        if webhook_result.get("ok", True)
        else "COMPLETED_WITH_WEBHOOK_ERROR",
        "command_id": command_id,
        "output_files": json.dumps(request_payload.get("output_files", {}), sort_keys=True),
        "storage_urls": json.dumps(output_urls, sort_keys=True),
        "debug_storage_urls": json.dumps(debug_output_urls or {}, sort_keys=True),
        "debug_error": truncate(debug_error, 1000),
        "backing_lufs": analysis.get("backing", {}).get("loudnorm", {}).get("input_i"),
        "vocal_lufs": analysis.get("vocal", {}).get("loudnorm", {}).get("input_i"),
        "vocal_channel_mode": decisions.get("vocal_channel_mode"),
        "webhook_status": webhook_result.get("status", ""),
        "webhook_error": truncate(webhook_result.get("error", ""), 1000),
        "webhook_metadata": json.dumps(
            request_payload.get("webhook_metadata", {}), sort_keys=True
        ),
    }
    details.update(timing_email_details(analysis, decisions))
    email_executor.submit(
        send_notification,
        "[FFmpeg API] Analyze and mix completed"
        if webhook_result.get("ok", True)
        else "[FFmpeg API] Analyze and mix completed, webhook failed",
        details,
    )


def notify_mix_failure(command_id, request_payload, error, status_code=None):
    email_executor.submit(
        send_notification,
        "[FFmpeg API] Analyze and mix failed",
        {
            "event": "analyze-and-mix",
            "status": "FAILED",
            "status_code": status_code or "",
            "command_id": command_id or "",
            "output_files": json.dumps(request_payload.get("output_files", {}), sort_keys=True)
            if isinstance(request_payload, dict)
            else "",
            "webhook_metadata": json.dumps(
                request_payload.get("webhook_metadata", {}), sort_keys=True
            )
            if isinstance(request_payload, dict)
            else "",
            "error": truncate(error, 2000),
        },
    )


def json_response(handler, status, payload, include_body=True):
    body = json.dumps(json_safe(payload), indent=2, allow_nan=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    add_cors_headers(handler)
    handler.end_headers()
    if include_body:
        handler.wfile.write(body)


def markdown_response(handler, status, body, include_body=True):
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/markdown; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    add_cors_headers(handler)
    handler.end_headers()
    if include_body:
        handler.wfile.write(encoded)


def file_response(handler, path, include_body=True):
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    size = path.stat().st_size
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(size))
    add_cors_headers(handler)
    handler.end_headers()
    if include_body:
        with path.open("rb") as file:
            shutil.copyfileobj(file, handler.wfile)


def add_cors_headers(handler):
    origin = handler.headers.get("Origin", "")
    if "*" in CORS_ALLOWED_ORIGINS:
        allowed_origin = "*"
    elif origin in CORS_ALLOWED_ORIGINS:
        allowed_origin = origin
        handler.send_header("Vary", "Origin")
    else:
        allowed_origin = ""

    if allowed_origin:
        handler.send_header("Access-Control-Allow-Origin", allowed_origin)
        handler.send_header("Access-Control-Allow-Methods", CORS_ALLOWED_METHODS)
        handler.send_header("Access-Control-Allow-Headers", CORS_ALLOWED_HEADERS)
        handler.send_header("Access-Control-Max-Age", "86400")


def read_json_body(handler):
    if API_KEY and handler.headers.get("X-API-KEY") != API_KEY:
        raise ApiError(401, "Invalid API key")

    content_type = handler.headers.get("Content-Type", "")
    if "application/json" not in content_type.lower():
        raise ApiError(415, "Content-Type must be application/json")

    try:
        content_length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise ApiError(400, "Invalid Content-Length") from exc

    if content_length <= 0:
        raise ApiError(400, "Missing request body")
    if content_length > MAX_BODY_BYTES:
        raise ApiError(413, "Request body too large")

    raw_body = handler.rfile.read(content_length)
    try:
        return json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(400, "Invalid JSON body") from exc


def validate_http_url(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, f"Missing {field_name}")

    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ApiError(400, f"{field_name} must be a valid http or https URL")

    return url


def is_allowed_webhook_url(webhook_url):
    if not ALLOWED_WEBHOOK_PATTERNS:
        return True
    return webhook_url in ALLOWED_WEBHOOK_URLS or webhook_url.startswith(ALLOWED_WEBHOOK_PREFIXES)


def validate_audio_url(audio_url):
    try:
        return validate_http_url(audio_url, "audio_url")
    except ApiError as exc:
        if exc.message == "Missing audio_url":
            raise
        raise ApiError(400, "audio_url must be a valid http or https URL") from exc


def validate_label_map(value, label_re, field_name):
    if not isinstance(value, dict) or not value:
        raise ApiError(400, f"{field_name} must be a non-empty object")

    validated = {}
    for label, raw_value in value.items():
        if not isinstance(label, str) or not label_re.match(label):
            raise ApiError(400, f"Invalid {field_name} label {label}")
        if field_name == "input_files":
            validated[label] = validate_http_url(raw_value, f"input_files.{label}")
        else:
            if not isinstance(raw_value, str) or not raw_value.strip():
                raise ApiError(400, f"Missing output_files.{label}")
            filename = Path(raw_value.strip()).name
            if filename != raw_value.strip() or not SAFE_FILENAME_RE.match(filename):
                raise ApiError(400, f"Invalid output filename for {label}")
            validated[label] = filename

    return validated


def validate_ffmpeg_job_request(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Request body must be a JSON object")

    input_files = validate_label_map(
        payload.get("input_files"), INPUT_LABEL_RE, "input_files"
    )
    output_files = validate_label_map(
        payload.get("output_files"), OUTPUT_LABEL_RE, "output_files"
    )

    ffmpeg_command = payload.get("ffmpeg_command")
    if not isinstance(ffmpeg_command, str) or not ffmpeg_command.strip():
        raise ApiError(400, "Missing ffmpeg_command")
    ffmpeg_command = ffmpeg_command.strip()

    webhook_url = validate_http_url(payload.get("webhook_url"), "webhook_url")
    if not is_allowed_webhook_url(webhook_url):
        raise ApiError(400, "webhook_url is not allowed")

    webhook_metadata = payload.get("webhook_metadata")
    if not isinstance(webhook_metadata, dict):
        raise ApiError(400, "webhook_metadata must be an object")

    placeholders = set(PLACEHOLDER_RE.findall(ffmpeg_command))
    known_labels = set(input_files) | set(output_files)
    unknown = sorted(placeholders - known_labels)
    if unknown:
        raise ApiError(400, f"Unknown ffmpeg_command placeholder {unknown[0]}")

    if not set(output_files).issubset(placeholders):
        raise ApiError(400, "ffmpeg_command must include every output placeholder")

    return {
        "input_files": input_files,
        "output_files": output_files,
        "ffmpeg_command": ffmpeg_command,
        "webhook_url": webhook_url,
        "webhook_metadata": webhook_metadata,
    }


def validate_optional_webhook(payload):
    webhook_url = payload.get("webhook_url", "")
    if webhook_url in (None, ""):
        return ""

    webhook_url = validate_http_url(webhook_url, "webhook_url")
    if not is_allowed_webhook_url(webhook_url):
        raise ApiError(400, "webhook_url is not allowed")
    return webhook_url


def validate_analyze_mix_request(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Request body must be a JSON object")

    backing_url = validate_http_url(payload.get("backing_url"), "backing_url")
    vocal_url = validate_http_url(payload.get("vocal_url"), "vocal_url")
    reference_vocal_url = payload.get("reference_vocal_url", "")
    if reference_vocal_url in (None, ""):
        reference_vocal_url = payload.get("guide_vocal_url", "")
    if reference_vocal_url not in (None, ""):
        reference_vocal_url = validate_http_url(reference_vocal_url, "reference_vocal_url")
    else:
        reference_vocal_url = ""

    output_files = payload.get("output_files")
    if output_files is None and payload.get("output_filename"):
        output_files = {"out_1": payload.get("output_filename")}
    output_files = validate_label_map(output_files, OUTPUT_LABEL_RE, "output_files")
    if "out_1" not in output_files:
        raise ApiError(400, "output_files.out_1 is required")

    output_name = output_files["out_1"].lower()
    if not output_name.endswith((".mp3", ".m4a", ".aac", ".wav")):
        raise ApiError(400, "output_files.out_1 must be an audio filename")

    webhook_metadata = payload.get("webhook_metadata", {})
    if not isinstance(webhook_metadata, dict):
        raise ApiError(400, "webhook_metadata must be an object")

    return {
        "backing_url": backing_url,
        "vocal_url": vocal_url,
        "reference_vocal_url": reference_vocal_url,
        "output_files": output_files,
        "webhook_url": validate_optional_webhook(payload),
        "webhook_metadata": webhook_metadata,
        "mix_options": payload.get("mix_options", {})
        if isinstance(payload.get("mix_options", {}), dict)
        else {},
    }


def default_mux_output_filename(video_url, recording_id=""):
    suffix = Path(urlparse(video_url).path).suffix.lower()
    if suffix == ".webm":
        output_suffix = ".webm"
    else:
        output_suffix = ".mp4"

    safe_recording_id = ""
    if isinstance(recording_id, str) and recording_id.strip():
        candidate = recording_id.strip()
        if SAFE_FILENAME_RE.match(f"FINAL_MUX_{candidate}{output_suffix}"):
            safe_recording_id = candidate

    if not safe_recording_id:
        safe_recording_id = uuid.uuid4().hex
    return f"FINAL_MUX_{safe_recording_id}{output_suffix}"


def build_mux_ffmpeg_command(output_filename, logo_height_ratio=0.2, logo_margin_ratio=0.03):
    suffix = Path(output_filename).suffix.lower()
    video_args = "-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p"
    audio_args = "-c:a aac -b:a 192k -movflags +faststart"
    if suffix == ".webm":
        video_args = (
            "-c:v libvpx -deadline realtime -cpu-used 8 "
            "-b:v 2500k -maxrate 3500k -bufsize 7000k -pix_fmt yuv420p"
        )
        audio_args = "-c:a libopus -b:a 160k"
    return (
        "-i {{in_1}} -i {{in_2}} -loop 1 -i {{in_3}} "
        f'-filter_complex "[0:v]setpts=PTS-STARTPTS,setsar=1[base0];'
        f'[2:v]format=rgba[logo0];'
        f'[logo0][base0]scale2ref=w=ref_h*{logo_height_ratio}:'
        f'h=ref_h*{logo_height_ratio}[logo][base];'
        f'[logo]setsar=1[logo1];'
        f'[base][logo1]overlay=x=W-w-W*{logo_margin_ratio}:'
        f'y=H-h-H*{logo_margin_ratio}:format=auto,setpts=PTS-STARTPTS[outv];'
        f'[1:a]asetpts=PTS-STARTPTS[aout]" '
        "-map [outv] -map [aout] -fps_mode passthrough "
        f"{video_args} {audio_args} -shortest {{{{out_1}}}}"
    )


def mux_audio_filter(audio_delay_seconds):
    if audio_delay_seconds > 0.005:
        delay_ms = max(0, int(round(audio_delay_seconds * 1000)))
        return f"[1:a]adelay={delay_ms}:all=1[aout]"
    if audio_delay_seconds < -0.005:
        trim_seconds = abs(audio_delay_seconds)
        return f"[1:a]atrim=start={trim_seconds:.3f},asetpts=PTS-STARTPTS[aout]"
    return ""


def mux_stage_one_args(video_path, audio_path, synced_path, audio_delay_seconds=0.0):
    suffix = synced_path.suffix.lower()
    if suffix == ".webm":
        audio_args = ["-c:a", "libopus", "-b:a", "160k"]
    else:
        audio_args = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    audio_filter = mux_audio_filter(audio_delay_seconds)
    filter_args = []
    audio_map = "1:a:0"
    if audio_filter:
        filter_args = ["-filter_complex", audio_filter]
        audio_map = "[aout]"
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        *filter_args,
        "-map",
        "0:v:0",
        "-map",
        audio_map,
        "-c:v",
        "copy",
        *audio_args,
        "-shortest",
        str(synced_path),
    ]


def mux_stage_two_args(
    synced_path,
    logo_path,
    output_path,
    logo_height_ratio=0.2,
    logo_margin_ratio=0.03,
):
    suffix = output_path.suffix.lower()
    if suffix == ".webm":
        video_args = [
            "-c:v",
            "libvpx",
            "-deadline",
            "realtime",
            "-cpu-used",
            "8",
            "-b:v",
            "2500k",
            "-maxrate",
            "3500k",
            "-bufsize",
            "7000k",
            "-pix_fmt",
            "yuv420p",
        ]
        audio_args = ["-c:a", "libopus", "-b:a", "160k"]
    else:
        video_args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
        if synced_path.suffix.lower() == ".webm":
            audio_args = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
        else:
            audio_args = ["-c:a", "copy", "-movflags", "+faststart"]

    filter_complex = (
        f"[0:v]setsar=1[base0];"
        f"[1:v]format=rgba[logo0];"
        f"[logo0][base0]scale2ref=w=ref_h*{logo_height_ratio}:"
        f"h=ref_h*{logo_height_ratio}[logo][base];"
        f"[logo]setsar=1[logo1];"
        f"[base][logo1]overlay=x=W-w-W*{logo_margin_ratio}:"
        f"y=H-h-H*{logo_margin_ratio}:format=auto,fps=30[outv]"
    )
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(synced_path),
        "-loop",
        "1",
        "-i",
        str(logo_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "0:a:0",
        *video_args,
        *audio_args,
        "-shortest",
        str(output_path),
    ]


def extract_audio_preview(command_id, source_path, output_path):
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "8000",
        "-f",
        "s16le",
        str(output_path),
    ]
    run_logged_ffmpeg(command_id, "audio_sync_preview", args)


def load_s16le(path):
    samples = array.array("h")
    samples.frombytes(path.read_bytes())
    if struct.pack("=h", 1) != struct.pack("<h", 1):
        samples.byteswap()
    return samples


def rms_envelope(samples, window_size=400):
    if len(samples) < window_size:
        return []
    envelope = []
    for index in range(0, len(samples) - window_size + 1, window_size):
        total = 0
        for sample in samples[index : index + window_size]:
            total += sample * sample
        envelope.append(math.sqrt(total / window_size))
    if not envelope:
        return []
    mean = sum(envelope) / len(envelope)
    centered = [value - mean for value in envelope]
    norm = math.sqrt(sum(value * value for value in centered))
    if norm <= 0:
        return []
    return [value / norm for value in centered]


def estimate_mux_audio_delay(command_id, video_path, audio_path, work_dir):
    video_preview = work_dir / "mux_video_audio.s16"
    mix_preview = work_dir / "mux_final_mix_audio.s16"
    try:
        extract_audio_preview(command_id, video_path, video_preview)
        extract_audio_preview(command_id, audio_path, mix_preview)
        video_envelope = rms_envelope(load_s16le(video_preview))
        mix_envelope = rms_envelope(load_s16le(mix_preview))
    except Exception as exc:
        log_event(
            "mux_audio_sync_estimate_failed",
            command_id=command_id,
            error=truncate(exc, 1000),
        )
        return 0.0

    if not video_envelope or not mix_envelope:
        return 0.0

    best_score = None
    best_offset_steps = 0
    max_offset_steps = 100
    for offset_steps in range(-max_offset_steps, max_offset_steps + 1):
        if offset_steps >= 0:
            count = min(len(video_envelope), len(mix_envelope) - offset_steps)
            if count <= 10:
                continue
            score = sum(
                video_envelope[index] * mix_envelope[index + offset_steps]
                for index in range(count)
            )
        else:
            count = min(len(video_envelope) + offset_steps, len(mix_envelope))
            if count <= 10:
                continue
            score = sum(
                video_envelope[index - offset_steps] * mix_envelope[index]
                for index in range(count)
            )
        if best_score is None or score > best_score:
            best_score = score
            best_offset_steps = offset_steps

    offset_seconds = best_offset_steps * 0.05
    audio_delay_seconds = -offset_seconds
    if best_score is None or best_score < 0.2 or abs(audio_delay_seconds) > 3:
        audio_delay_seconds = 0.0

    log_event(
        "mux_audio_sync_estimated",
        command_id=command_id,
        correlation_score=round(best_score or 0, 6),
        offset_seconds=round(offset_seconds, 3),
        audio_delay_seconds=round(audio_delay_seconds, 3),
    )
    return audio_delay_seconds


def validate_mux_video_request(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Request body must be a JSON object")

    video_url = validate_http_url(payload.get("video_url"), "video_url")
    audio_url = validate_http_url(payload.get("audio_url"), "audio_url")
    burn_logo = payload.get("burn_logo", True)
    if not isinstance(burn_logo, bool):
        raise ApiError(400, "burn_logo must be a boolean")
    auto_audio_sync = payload.get("auto_audio_sync", True)
    if not isinstance(auto_audio_sync, bool):
        raise ApiError(400, "auto_audio_sync must be a boolean")
    logo_url = ""
    if burn_logo:
        logo_url = validate_http_url(payload.get("logo_url", DEFAULT_MUX_LOGO_URL), "logo_url")
    try:
        logo_height_ratio = float(payload.get("logo_height_ratio", 0.2))
        logo_margin_ratio = float(payload.get("logo_margin_ratio", 0.03))
        audio_delay_seconds = float(payload.get("audio_delay_seconds", 0.0))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "logo ratios and audio_delay_seconds must be numeric") from exc
    if logo_height_ratio <= 0 or logo_height_ratio > 0.5:
        raise ApiError(400, "logo_height_ratio must be greater than 0 and at most 0.5")
    if logo_margin_ratio < 0 or logo_margin_ratio > 0.25:
        raise ApiError(400, "logo_margin_ratio must be between 0 and 0.25")
    if abs(audio_delay_seconds) > 5:
        raise ApiError(400, "audio_delay_seconds must be between -5 and 5")

    webhook_url = validate_http_url(payload.get("webhook_url"), "webhook_url")
    if not is_allowed_webhook_url(webhook_url):
        raise ApiError(400, "webhook_url is not allowed")

    webhook_metadata = payload.get("webhook_metadata", {})
    if not isinstance(webhook_metadata, dict):
        raise ApiError(400, "webhook_metadata must be an object")

    output_files = payload.get("output_files")
    if output_files is None:
        output_files = {
            "out_1": payload.get("output_filename")
            or default_mux_output_filename(video_url, webhook_metadata.get("recording_id", ""))
        }
    output_files = validate_label_map(output_files, OUTPUT_LABEL_RE, "output_files")
    if "out_1" not in output_files:
        raise ApiError(400, "output_files.out_1 is required")

    output_name = output_files["out_1"].lower()
    if not output_name.endswith((".mp4", ".m4v", ".mov", ".webm")):
        raise ApiError(400, "output_files.out_1 must be a video filename")

    input_files = {"in_1": video_url, "in_2": audio_url}
    if burn_logo:
        input_files["in_3"] = logo_url

    return {
        "input_files": input_files,
        "output_files": output_files,
        "ffmpeg_command": build_mux_ffmpeg_command(
            output_files["out_1"],
            logo_height_ratio=logo_height_ratio,
            logo_margin_ratio=logo_margin_ratio,
        ),
        "webhook_url": webhook_url,
        "webhook_metadata": webhook_metadata,
        "job_type": "mux-video",
        "logo_url": logo_url,
        "burn_logo": burn_logo,
        "auto_audio_sync": auto_audio_sync,
        "audio_delay_seconds": audio_delay_seconds,
        "logo_height_ratio": logo_height_ratio,
        "logo_margin_ratio": logo_margin_ratio,
    }


def parse_loudnorm_json(stderr):
    matches = LOUDNORM_JSON_RE.findall(stderr)
    if not matches:
        raise ApiError(500, "Could not parse loudnorm output")

    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        raise ApiError(500, f"Failed to parse JSON: {exc}") from exc

    response = {"success": True}
    for field in FLOAT_FIELDS:
        try:
            response[field] = float(parsed[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError(500, f"Invalid loudnorm field {field}") from exc

    response["normalization_type"] = parsed.get("normalization_type", "")
    response["target_offset"] = response.pop("target_offset")
    return response


def classify_ffmpeg_failure(stderr):
    lower = stderr.lower()
    if (
        "server returned 4" in lower
        or "server returned 5" in lower
        or "http error" in lower
        or "not found" in lower
        or "connection refused" in lower
        or "connection timed out" in lower
        or "failed to resolve" in lower
        or "i/o error" in lower
        or "error opening input" in lower
    ):
        return 502, "URL unreachable or download failed"

    if (
        "invalid data found when processing input" in lower
        or "could not find codec parameters" in lower
        or "error while decoding" in lower
        or "unsupported codec" in lower
    ):
        return 422, "FFmpeg failed to decode the file"

    return 422, "FFmpeg failed to process the file"


def measure_loudness(audio_url):
    args = [
        "ffmpeg",
        "-i",
        audio_url,
        "-af",
        LOUDNORM_FILTER,
        "-f",
        "null",
        "-",
    ]

    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ApiError(504, f"Server timeout after {TIMEOUT_SECONDS} seconds") from exc
    except FileNotFoundError as exc:
        raise ApiError(500, "FFmpeg is not installed") from exc

    stderr = completed.stderr or ""
    if completed.returncode != 0:
        status, message = classify_ffmpeg_failure(stderr)
        raise ApiError(status, message)

    result = parse_loudnorm_json(stderr)
    return result


def run_command(args, timeout, error_message):
    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{error_message}: timeout after {timeout} seconds") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(f"{error_message}: executable not found") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(f"{error_message}: {stderr[-2000:]}")

    return completed


def parse_optional_float(value):
    try:
        if value in {"-inf", "inf", "+inf"}:
            return float(value)
        return float(value)
    except (TypeError, ValueError):
        return None


def ffprobe_audio(path):
    completed = run_command(
        [
            "ffprobe",
            "-hide_banner",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate,size:stream=index,codec_name,codec_type,sample_rate,channels,channel_layout,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        30,
        "ffprobe failed",
    )
    parsed = json.loads(completed.stdout)
    audio_stream = next(
        (
            stream
            for stream in parsed.get("streams", [])
            if stream.get("codec_type") == "audio"
        ),
        {},
    )
    fmt = parsed.get("format", {})
    return {
        "duration": parse_optional_float(fmt.get("duration")),
        "size": int(fmt.get("size", 0) or 0),
        "bit_rate": int(fmt.get("bit_rate", 0) or 0),
        "codec_name": audio_stream.get("codec_name", ""),
        "sample_rate": int(audio_stream.get("sample_rate", 0) or 0),
        "channels": int(audio_stream.get("channels", 0) or 0),
        "channel_layout": audio_stream.get("channel_layout", ""),
        "stream_bit_rate": int(audio_stream.get("bit_rate", 0) or 0),
    }


def loudnorm_file(path, target_i=-16):
    completed = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"loudnorm=I={target_i}:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        TIMEOUT_SECONDS,
        "loudnorm analysis failed",
    )
    return parse_loudnorm_json(completed.stderr)


def volumedetect_file(path):
    completed = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        TIMEOUT_SECONDS,
        "volume analysis failed",
    )
    metrics = {}
    for line in completed.stderr.splitlines():
        if "mean_volume:" in line:
            metrics["mean_volume"] = parse_optional_float(line.rsplit(":", 1)[1].strip().split()[0])
        elif "max_volume:" in line:
            metrics["max_volume"] = parse_optional_float(line.rsplit(":", 1)[1].strip().split()[0])
    return metrics


def clamp(value, low, high):
    return max(low, min(high, value))


def ffmpeg_number(value):
    return f"{float(value):.6g}"


def parse_vocal_eq_bands(mix_options, key, polarity):
    raw_bands = mix_options.get(key, [])
    if raw_bands in (None, ""):
        return []
    if not isinstance(raw_bands, list):
        return []

    parsed = []
    for band in raw_bands:
        if not isinstance(band, dict):
            continue
        try:
            frequency = float(
                band.get("frequency_hz", band.get("frequency", band.get("f")))
            )
            width_q = float(band.get("width_q", band.get("q", band.get("width", 1))))
            gain_db = float(band.get("gain_db", band.get("gain", band.get("g"))))
        except (TypeError, ValueError):
            continue

        if frequency <= 0 or width_q <= 0 or gain_db == 0:
            continue
        if polarity == "negative" and gain_db >= 0:
            continue
        if polarity == "positive" and gain_db <= 0:
            continue

        parsed.append(
            {
                "frequency_hz": frequency,
                "width_q": width_q,
                "gain_db": gain_db,
            }
        )
    return parsed


def equalizer_filter_from_band(band):
    return (
        f"equalizer=f={ffmpeg_number(band['frequency_hz'])}:"
        f"t=q:w={ffmpeg_number(band['width_q'])}:"
        f"g={ffmpeg_number(band['gain_db'])}"
    )


def parse_int_option(options, key, default, low, high):
    try:
        value = int(options.get(key, default))
    except (TypeError, ValueError):
        value = default
    return int(clamp(value, low, high))


def samples_rms_db(samples):
    if not samples:
        return None
    square_sum = sum(sample * sample for sample in samples)
    rms = math.sqrt(square_sum / len(samples))
    if rms <= 0:
        return -120.0
    return 20 * math.log10(rms / 32768)


def percentile(values, fraction):
    if not values:
        return None
    sorted_values = sorted(values)
    index = int(round((len(sorted_values) - 1) * fraction))
    return sorted_values[index]


def active_rms_file(path, audio_filter, threshold_db=-50, window_seconds=1):
    filter_chain = f"{audio_filter},aresample=48000"
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            filter_chain,
            "-ac",
            "1",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"active RMS analysis failed: {stderr[-2000:]}")

    sample_count = len(completed.stdout) // 2
    if sample_count == 0:
        return {
            "window_seconds": window_seconds,
            "threshold_db": threshold_db,
            "active_window_count": 0,
            "total_window_count": 0,
            "active_rms_median_db": None,
            "active_rms_p75_db": None,
            "active_rms_max_db": None,
        }

    samples = struct.unpack(f"<{sample_count}h", completed.stdout)
    window_size = int(48000 * window_seconds)
    window_levels = []
    for start in range(0, len(samples), window_size):
        window = samples[start : start + window_size]
        if len(window) < window_size // 2:
            continue
        level = samples_rms_db(window)
        if level is not None:
            window_levels.append(level)

    active_levels = [level for level in window_levels if level >= threshold_db]
    if not active_levels:
        active_levels = window_levels

    return {
        "window_seconds": window_seconds,
        "threshold_db": threshold_db,
        "active_window_count": len(active_levels),
        "total_window_count": len(window_levels),
        "active_rms_median_db": round(percentile(active_levels, 0.5), 3)
        if active_levels
        else None,
        "active_rms_p75_db": round(percentile(active_levels, 0.75), 3)
        if active_levels
        else None,
        "active_rms_max_db": round(max(active_levels), 3) if active_levels else None,
    }


def audio_envelope(path, audio_filter=None, sample_rate=8000, frame_ms=40):
    filters = []
    if audio_filter:
        filters.append(audio_filter)
    filters.extend(["highpass=f=80", "lowpass=f=4000", f"aresample={sample_rate}"])
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            ",".join(filters),
            "-ac",
            "1",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"timing envelope analysis failed: {stderr[-2000:]}")

    sample_count = len(completed.stdout) // 2
    if sample_count == 0:
        return [], {"sample_rate": sample_rate, "frame_ms": frame_ms, "frame_count": 0}

    samples = struct.unpack(f"<{sample_count}h", completed.stdout)
    frame_size = max(1, int(sample_rate * frame_ms / 1000))
    envelope = []
    for start in range(0, len(samples), frame_size):
        frame = samples[start : start + frame_size]
        if len(frame) < frame_size // 2:
            continue
        rms = math.sqrt(sum(sample * sample for sample in frame) / len(frame))
        envelope.append(math.log1p(rms))

    return envelope, {
        "sample_rate": sample_rate,
        "frame_ms": frame_ms,
        "frame_count": len(envelope),
        "duration_seconds": round(len(samples) / sample_rate, 3),
    }


def normalize_series(values):
    if not values:
        return []
    mean = sum(values) / len(values)
    centered = [value - mean for value in values]
    variance = sum(value * value for value in centered) / len(centered)
    stddev = math.sqrt(variance)
    if stddev <= 1e-9:
        return [0 for _ in centered]
    return [value / stddev for value in centered]


def smooth_series(values, radius=2):
    if not values:
        return []
    smoothed = []
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        smoothed.append(sum(values[start:end]) / (end - start))
    return smoothed


def activity_series(values):
    if not values:
        return []

    floor = percentile(values, 0.2)
    ceiling = percentile(values, 0.9)
    if floor is None or ceiling is None or ceiling - floor <= 1e-6:
        return [0.0 for _ in values]

    threshold = floor + ((ceiling - floor) * 0.35)
    raw_activity = [1.0 if value >= threshold else 0.0 for value in values]
    return smooth_series(raw_activity, radius=2)


def onset_series(values):
    if len(values) < 3:
        return [0.0 for _ in values]

    activity = activity_series(values)
    differences = [
        max(0.0, values[index] - values[index - 1])
        if activity[index] >= 0.35
        else 0.0
        for index in range(1, len(values))
    ]
    positive = [value for value in differences if value > 0]
    if not positive:
        return [0.0 for _ in values]

    threshold = percentile(positive, 0.75)
    onsets = [0.0 for _ in values]
    for index, value in enumerate(differences, start=1):
        if value >= threshold:
            for spread_index in range(max(0, index - 1), min(len(onsets), index + 2)):
                onsets[spread_index] = max(onsets[spread_index], value)
    return smooth_series(onsets, radius=1)


def correlation_at_lag(reference, recorded, lag):
    if lag >= 0:
        length = min(len(reference), len(recorded) - lag)
        ref_start = 0
        rec_start = lag
    else:
        length = min(len(reference) + lag, len(recorded))
        ref_start = -lag
        rec_start = 0

    if length <= 10:
        return None

    total = 0.0
    reference_energy = 0.0
    recorded_energy = 0.0
    for offset in range(length):
        reference_value = reference[ref_start + offset]
        recorded_value = recorded[rec_start + offset]
        total += reference_value * recorded_value
        reference_energy += reference_value * reference_value
        recorded_energy += recorded_value * recorded_value
    denominator = math.sqrt(reference_energy * recorded_energy)
    if denominator <= 1e-9:
        return None
    return total / denominator


def score_alignment_lags(reference, recorded, max_lag):
    scores = []
    for lag in range(-max_lag, max_lag + 1):
        score = correlation_at_lag(reference, recorded, lag)
        if score is not None:
            scores.append((score, lag))
    return scores


def summarize_alignment_scores(method, scores, frame_ms, peak_exclusion_ms):
    if not scores:
        return None

    scores.sort(reverse=True)
    best_score, best_lag = scores[0]
    exclusion_frames = max(1, int(round(peak_exclusion_ms / frame_ms)))
    comparison_scores = [
        score for score, lag in scores if abs(lag - best_lag) > exclusion_frames
    ]
    if not comparison_scores:
        comparison_scores = [score for score, lag in scores[1:]]

    comparison_score = percentile(comparison_scores, 0.95) if comparison_scores else 0
    adjacent_score = scores[1][0] if len(scores) > 1 else 0
    confidence = max(0, best_score - (comparison_score or 0))
    detected_delay_ms = int(round(best_lag * frame_ms))
    return {
        "method": method,
        "lag": best_lag,
        "detected_vocal_delay_ms": detected_delay_ms,
        "confidence": round(confidence, 6),
        "best_score": round(best_score, 6),
        "comparison_score": round(comparison_score or 0, 6),
        "adjacent_score": round(adjacent_score, 6),
    }


def estimate_vocal_timing(reference_path, vocal_path, vocal_needs_centering, options=None):
    options = options or {}
    frame_ms = int(options.get("alignment_frame_ms", 40))
    max_shift_seconds = float(options.get("max_alignment_shift_seconds", 15))
    minimum_confidence = float(options.get("minimum_alignment_confidence", 0.08))
    minimum_best_score = float(options.get("minimum_alignment_best_score", 0.08))
    peak_exclusion_ms = int(options.get("alignment_peak_exclusion_ms", 1000))
    vocal_filter = (
        "pan=mono|c0=c0+c1" if vocal_needs_centering else "pan=mono|c0=0.5*c0+0.5*c1"
    )
    reference_envelope, reference_meta = audio_envelope(
        reference_path, "pan=mono|c0=0.5*c0+0.5*c1", frame_ms=frame_ms
    )
    recorded_envelope, recorded_meta = audio_envelope(vocal_path, vocal_filter, frame_ms=frame_ms)
    max_lag = max(1, int(max_shift_seconds * 1000 / frame_ms))

    feature_pairs = [
        (
            "envelope",
            normalize_series(reference_envelope),
            normalize_series(recorded_envelope),
        ),
        (
            "activity",
            normalize_series(activity_series(reference_envelope)),
            normalize_series(activity_series(recorded_envelope)),
        ),
        (
            "onset",
            normalize_series(onset_series(reference_envelope)),
            normalize_series(onset_series(recorded_envelope)),
        ),
    ]

    candidates = []
    for method, reference_feature, recorded_feature in feature_pairs:
        result = summarize_alignment_scores(
            method,
            score_alignment_lags(reference_feature, recorded_feature, max_lag),
            frame_ms,
            peak_exclusion_ms,
        )
        if result is not None:
            candidates.append(result)

    if not candidates:
        return {
            "enabled": True,
            "applied": False,
            "reason": "not enough audio to estimate timing",
            "detected_vocal_delay_ms": 0,
            "applied_vocal_shift_ms": 0,
            "confidence": 0,
            "reference": reference_meta,
            "recorded": recorded_meta,
        }

    candidates.sort(key=lambda item: (item["confidence"], item["best_score"]), reverse=True)
    best = candidates[0]
    detected_delay_ms = best["detected_vocal_delay_ms"]
    apply_shift = (
        best["confidence"] >= minimum_confidence
        and best["best_score"] >= minimum_best_score
        and detected_delay_ms != 0
    )
    if apply_shift:
        reason = "ok"
    elif detected_delay_ms == 0:
        reason = "no offset detected"
    else:
        reason = "confidence too low or no offset detected"
    return {
        "enabled": True,
        "applied": apply_shift,
        "reason": reason,
        "method": best["method"],
        "detected_vocal_delay_ms": detected_delay_ms,
        "applied_vocal_shift_ms": -detected_delay_ms if apply_shift else 0,
        "confidence": best["confidence"],
        "best_score": best["best_score"],
        "comparison_score": best["comparison_score"],
        "adjacent_score": best["adjacent_score"],
        "candidates": candidates[:5],
        "frame_ms": frame_ms,
        "max_shift_seconds": max_shift_seconds,
        "minimum_confidence": minimum_confidence,
        "minimum_best_score": minimum_best_score,
        "peak_exclusion_ms": peak_exclusion_ms,
        "reference": reference_meta,
        "recorded": recorded_meta,
    }


def timing_shift_filter(shift_ms):
    if shift_ms > 0:
        return f"adelay={shift_ms}:all=1,"
    if shift_ms < 0:
        seconds = abs(shift_ms) / 1000
        return f"atrim=start={seconds:.3f},asetpts=PTS-STARTPTS,"
    return ""


def astats_file(path):
    completed = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "astats=metadata=0:reset=0",
            "-f",
            "null",
            "-",
        ],
        TIMEOUT_SECONDS,
        "channel analysis failed",
    )

    channels = {}
    overall = {}
    current = None
    in_overall = False
    for line in completed.stderr.splitlines():
        channel_match = ASTATS_CHANNEL_RE.search(line)
        if channel_match:
            current = channel_match.group(1)
            channels.setdefault(current, {})
            in_overall = False
            continue
        if " Overall" in line:
            current = None
            in_overall = True
            continue

        value_match = ASTATS_VALUE_RE.search(line)
        if not value_match:
            continue
        key = value_match.group(1).strip().lower().replace(" ", "_")
        value = parse_optional_float(value_match.group(2).strip())
        if in_overall:
            overall[key] = value
        elif current is not None:
            channels.setdefault(current, {})[key] = value

    return {"channels": channels, "overall": overall}


def silence_file(path, threshold="-45dB"):
    completed = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"silencedetect=n={threshold}:d=0.5",
            "-f",
            "null",
            "-",
        ],
        TIMEOUT_SECONDS,
        "silence analysis failed",
    )

    ranges = []
    current_start = None
    for line in completed.stderr.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match:
            current_start = float(start_match.group(1))
            continue
        end_match = SILENCE_END_RE.search(line)
        if end_match and current_start is not None:
            end = float(end_match.group(1))
            ranges.append(
                {
                    "start": current_start,
                    "end": end,
                    "duration": round(end - current_start, 6),
                }
            )
            current_start = None
    return ranges


def detect_channel_mode(probe, astats):
    channels = probe.get("channels", 0)
    channel_stats = astats.get("channels", {})
    if channels < 2 or len(channel_stats) < 2:
        return {
            "mode": "mono",
            "needs_centering": False,
            "rms_spread_db": 0,
            "reason": "input is mono",
        }

    rms_values = [
        stats.get("rms_level_db")
        for _, stats in sorted(channel_stats.items())
        if stats.get("rms_level_db") is not None
    ]
    peak_values = [
        stats.get("peak_level_db")
        for _, stats in sorted(channel_stats.items())
        if stats.get("peak_level_db") is not None
    ]
    finite_rms = [value for value in rms_values if value != float("-inf")]
    if len(finite_rms) < 2:
        return {
            "mode": "one_sided",
            "needs_centering": True,
            "rms_spread_db": None,
            "reason": "one channel is silent or nearly silent",
        }

    rms_spread = max(finite_rms) - min(finite_rms)
    peak_spread = max(peak_values) - min(peak_values) if len(peak_values) >= 2 else 0
    needs_centering = rms_spread >= 12 or peak_spread >= 12
    return {
        "mode": "imbalanced_stereo" if needs_centering else "balanced_stereo",
        "needs_centering": needs_centering,
        "rms_spread_db": round(rms_spread, 3),
        "peak_spread_db": round(peak_spread, 3),
        "reason": "channel spread exceeds 12 dB" if needs_centering else "channels are balanced",
    }


def analyze_audio_pair(backing_path, vocal_path):
    backing_probe = ffprobe_audio(backing_path)
    vocal_probe = ffprobe_audio(vocal_path)
    backing_loudnorm = loudnorm_file(backing_path)
    vocal_loudnorm = loudnorm_file(vocal_path)
    backing_volume = volumedetect_file(backing_path)
    vocal_volume = volumedetect_file(vocal_path)
    backing_astats = astats_file(backing_path)
    vocal_astats = astats_file(vocal_path)
    vocal_silence = silence_file(vocal_path)
    channel_detection = detect_channel_mode(vocal_probe, vocal_astats)
    vocal_active_filter = (
        "pan=mono|c0=c0+c1"
        if channel_detection["needs_centering"]
        else "pan=mono|c0=0.5*c0+0.5*c1"
    )
    backing_active = active_rms_file(backing_path, "pan=mono|c0=0.5*c0+0.5*c1")
    vocal_active = active_rms_file(vocal_path, vocal_active_filter)

    return {
        "backing": {
            "probe": backing_probe,
            "loudnorm": backing_loudnorm,
            "volume": backing_volume,
            "astats": backing_astats,
            "active_rms": backing_active,
        },
        "vocal": {
            "probe": vocal_probe,
            "loudnorm": vocal_loudnorm,
            "volume": vocal_volume,
            "astats": vocal_astats,
            "active_rms": vocal_active,
            "silence_ranges": vocal_silence[:80],
            "channel_detection": channel_detection,
        },
    }


def choose_mix_decisions(analysis, mix_options):
    backing_i = analysis["backing"]["loudnorm"]["input_i"]
    vocal_i = analysis["vocal"]["loudnorm"]["input_i"]
    channel_detection = analysis["vocal"]["channel_detection"]
    timing_alignment = analysis.get("timing_alignment", {"applied_vocal_shift_ms": 0})
    backing_active = analysis["backing"].get("active_rms", {}).get("active_rms_p75_db")
    vocal_active = analysis["vocal"].get("active_rms", {}).get("active_rms_p75_db")

    backing_target_i = float(mix_options.get("backing_target_i", -30))
    vocal_target_i = float(mix_options.get("vocal_target_i", -12))
    final_target_i = float(mix_options.get("final_target_i", -6))
    final_true_peak = float(mix_options.get("final_true_peak", -1))
    backing_bed_volume_db = float(mix_options.get("backing_bed_volume_db", -6))
    vocal_compressor_makeup_db = float(mix_options.get("vocal_compressor_makeup_db", 6))
    vocal_polish_enabled = mix_options.get("vocal_polish_enabled", True) is not False
    vocal_gate_enabled = mix_options.get("vocal_gate_enabled", True) is not False
    vocal_gate_threshold = float(mix_options.get("vocal_gate_threshold", 0.005))
    vocal_gate_ratio = float(mix_options.get("vocal_gate_ratio", 2))
    vocal_gate_attack_ms = float(mix_options.get("vocal_gate_attack_ms", 5))
    vocal_gate_release_ms = float(mix_options.get("vocal_gate_release_ms", 120))
    vocal_denoise_enabled = mix_options.get("vocal_denoise_enabled", True) is not False
    vocal_denoise_noise_floor_db = float(
        mix_options.get("vocal_denoise_noise_floor_db", -25)
    )
    vocal_highpass_hz = float(mix_options.get("vocal_highpass_hz", 100))
    vocal_deesser_intensity = float(mix_options.get("vocal_deesser_intensity", 0.35))
    vocal_deesser_max = float(mix_options.get("vocal_deesser_max", 0.5))
    vocal_deesser_frequency = float(mix_options.get("vocal_deesser_frequency", 0.55))
    vocal_reductive_eq_bands = parse_vocal_eq_bands(
        mix_options,
        "vocal_reductive_eq_bands",
        "negative",
    )
    vocal_additive_eq_bands = parse_vocal_eq_bands(
        mix_options,
        "vocal_additive_eq_bands",
        "positive",
    )
    vocal_plate_reverb_enabled = (
        mix_options.get("vocal_plate_reverb_enabled", True) is not False
    )
    vocal_short_delay_enabled = (
        mix_options.get("vocal_short_delay_enabled", True) is not False
    )
    debug_stage_artifacts_enabled = (
        mix_options.get("debug_stage_artifacts_enabled", True) is not False
    )
    debug_waveform_points = parse_int_option(
        mix_options,
        "debug_waveform_points",
        512,
        64,
        2048,
    )
    target_vocal_over_backing_db = float(
        mix_options.get("target_vocal_over_backing_db", 3)
    )
    max_auto_vocal_boost_db = float(mix_options.get("max_auto_vocal_boost_db", 3))
    configured_vocal_boost_db = float(mix_options.get("vocal_boost_db", 0))
    vocal_second_pass_enabled = mix_options.get("vocal_second_pass_enabled", True) is not False
    vocal_second_pass_target_over_backing_db = float(
        mix_options.get("vocal_second_pass_target_over_backing_db", 0)
    )
    vocal_second_pass_max_boost_db = float(
        mix_options.get("vocal_second_pass_max_boost_db", 9)
    )
    vocal_second_pass_max_cut_db = float(
        mix_options.get("vocal_second_pass_max_cut_db", 12)
    )
    auto_vocal_boost_db = 0
    if backing_active is not None and vocal_active is not None:
        auto_vocal_boost_db = clamp(
            (backing_active + target_vocal_over_backing_db) - vocal_active,
            0,
            max_auto_vocal_boost_db,
        )
    vocal_boost_db = max(configured_vocal_boost_db, auto_vocal_boost_db)
    backing_weight = float(mix_options.get("backing_weight", 1.5))
    vocal_weight = float(mix_options.get("vocal_weight", 1.8))
    ducking_threshold = float(mix_options.get("ducking_threshold", 0.02))
    ducking_ratio = float(mix_options.get("ducking_ratio", 12))

    return {
        "backing_input_i": backing_i,
        "vocal_input_i": vocal_i,
        "backing_active_rms_p75_db": backing_active,
        "vocal_active_rms_p75_db": vocal_active,
        "backing_target_i": backing_target_i,
        "vocal_target_i": vocal_target_i,
        "final_target_i": final_target_i,
        "final_true_peak": final_true_peak,
        "backing_bed_volume_db": backing_bed_volume_db,
        "vocal_compressor_makeup_db": vocal_compressor_makeup_db,
        "vocal_polish_enabled": vocal_polish_enabled,
        "vocal_processing_order": [
            "gate",
            "cleanup_denoise",
            "normalization",
            "reductive_eq",
            "dynamics",
            "additive_eq",
            "space",
        ],
        "vocal_gate_enabled": vocal_gate_enabled,
        "vocal_gate_threshold": vocal_gate_threshold,
        "vocal_gate_ratio": vocal_gate_ratio,
        "vocal_gate_attack_ms": vocal_gate_attack_ms,
        "vocal_gate_release_ms": vocal_gate_release_ms,
        "vocal_denoise_enabled": vocal_denoise_enabled,
        "vocal_denoise_noise_floor_db": vocal_denoise_noise_floor_db,
        "vocal_highpass_hz": vocal_highpass_hz,
        "vocal_deesser_intensity": vocal_deesser_intensity,
        "vocal_deesser_max": vocal_deesser_max,
        "vocal_deesser_frequency": vocal_deesser_frequency,
        "vocal_reductive_eq_bands": vocal_reductive_eq_bands,
        "vocal_additive_eq_bands": vocal_additive_eq_bands,
        "vocal_plate_reverb_enabled": vocal_plate_reverb_enabled,
        "vocal_short_delay_enabled": vocal_short_delay_enabled,
        "debug_stage_artifacts_enabled": debug_stage_artifacts_enabled,
        "debug_waveform_points": debug_waveform_points,
        "target_vocal_over_backing_db": target_vocal_over_backing_db,
        "auto_vocal_boost_db": round(auto_vocal_boost_db, 3),
        "vocal_boost_db": vocal_boost_db,
        "vocal_second_pass_enabled": vocal_second_pass_enabled,
        "vocal_second_pass_target_over_backing_db": vocal_second_pass_target_over_backing_db,
        "vocal_second_pass_max_boost_db": vocal_second_pass_max_boost_db,
        "vocal_second_pass_max_cut_db": vocal_second_pass_max_cut_db,
        "vocal_second_pass_gain_db": 0,
        "vocal_second_pass_backing_active_rms_p75_db": None,
        "vocal_second_pass_vocal_active_rms_p75_db": None,
        "backing_weight": backing_weight,
        "vocal_weight": vocal_weight,
        "ducking_enabled": True,
        "ducking_threshold": ducking_threshold,
        "ducking_ratio": ducking_ratio,
        "timing_alignment_applied": bool(timing_alignment.get("applied")),
        "detected_vocal_delay_ms": timing_alignment.get("detected_vocal_delay_ms", 0),
        "applied_vocal_shift_ms": timing_alignment.get("applied_vocal_shift_ms", 0),
        "timing_alignment_confidence": timing_alignment.get("confidence", 0),
        "vocal_channel_mode": channel_detection["mode"],
        "vocal_needs_centering": channel_detection["needs_centering"],
        "vocal_channel_reason": channel_detection["reason"],
        "preserve_timing": True,
        "trim_vocal": False,
        "strategy": (
            "center vocal, clean/compress vocal, keep backing as an audible bed, "
            "then limit the final mix"
        ),
    }


def build_backing_filter(decisions, output_label="backing"):
    return (
        f"[0:a]volume={decisions['backing_bed_volume_db']}dB,"
        f"aformat=sample_rates=48000:channel_layouts=stereo[{output_label}]"
    )


def vocal_stage_filter_parts(decisions, include_second_pass_gain=True):
    pre_filters = []
    if decisions["vocal_needs_centering"]:
        pre_filters.append("pan=mono|c0=c0+c1")
        vocal_stereo = "pan=stereo|c0=c0|c1=c0"
    else:
        vocal_stereo = (
            "pan=stereo|c0=c0|c1=c0"
            if decisions.get("vocal_channel_mode") == "mono"
            else ""
        )

    vocal_timing = timing_shift_filter(
        int(decisions.get("applied_vocal_shift_ms", 0))
    ).rstrip(",")
    if vocal_timing:
        pre_filters.append(vocal_timing)

    polish_enabled = decisions.get("vocal_polish_enabled", True)

    gate_filters = []
    if polish_enabled and decisions.get("vocal_gate_enabled", True):
        gate_filters.append(
            f"agate=threshold={ffmpeg_number(decisions['vocal_gate_threshold'])}:"
            f"ratio={ffmpeg_number(decisions['vocal_gate_ratio'])}:"
            f"attack={ffmpeg_number(decisions['vocal_gate_attack_ms'])}:"
            f"release={ffmpeg_number(decisions['vocal_gate_release_ms'])}"
        )

    cleanup_filters = []
    if polish_enabled and decisions.get("vocal_denoise_enabled", True):
        cleanup_filters.append(
            f"afftdn=nf={ffmpeg_number(decisions['vocal_denoise_noise_floor_db'])}"
        )
    if polish_enabled:
        cleanup_filters.append(
            f"deesser=i={decisions['vocal_deesser_intensity']}:"
            f"m={decisions['vocal_deesser_max']}:"
            f"f={decisions['vocal_deesser_frequency']}"
        )

    normalization_filters = [
        f"loudnorm=I={decisions['vocal_target_i']}:TP=-1:LRA=6",
        f"volume={decisions['vocal_boost_db']}dB",
    ]
    if include_second_pass_gain and decisions.get("vocal_second_pass_enabled", True):
        normalization_filters.append(f"volume={decisions['vocal_second_pass_gain_db']}dB")

    reductive_eq_filters = [f"highpass=f={decisions['vocal_highpass_hz']}"]
    reductive_eq_filters.extend(
        equalizer_filter_from_band(band)
        for band in decisions.get("vocal_reductive_eq_bands", [])
    )

    dynamics_filters = [
        "acompressor=threshold=-32dB:ratio=5:attack=3:release=180:"
        f"makeup={decisions['vocal_compressor_makeup_db']}",
        "alimiter=limit=0.95",
    ]

    additive_eq_filters = list(
        equalizer_filter_from_band(band)
        for band in decisions.get("vocal_additive_eq_bands", [])
    )

    space_filters = []
    if vocal_stereo:
        space_filters.append(vocal_stereo)
    if polish_enabled and decisions.get("vocal_plate_reverb_enabled", True):
        space_filters.append(
            "aecho=in_gain=0.96:out_gain=0.92:"
            "delays=24|48|72:decays=0.025|0.018|0.012"
        )
    if polish_enabled and decisions.get("vocal_short_delay_enabled", True):
        space_filters.append("aecho=in_gain=0.98:out_gain=0.95:delays=120:decays=0.04")

    stages = [
        {"key": "gate", "label": "Gate", "filters": gate_filters},
        {"key": "cleanup", "label": "Cleanup / Denoise", "filters": cleanup_filters},
        {"key": "normalization", "label": "Normalization", "filters": normalization_filters},
        {"key": "reductive_eq", "label": "Reductive EQ", "filters": reductive_eq_filters},
        {"key": "dynamics", "label": "Dynamics", "filters": dynamics_filters},
        {"key": "additive_eq", "label": "Additive EQ", "filters": additive_eq_filters},
        {"key": "space", "label": "Space", "filters": space_filters},
    ]
    return pre_filters, stages


def vocal_filters_through_stage(decisions, stage_key=None, include_second_pass_gain=True):
    filters, stages = vocal_stage_filter_parts(
        decisions,
        include_second_pass_gain=include_second_pass_gain,
    )
    for stage in stages:
        filters.extend(stage["filters"])
        if stage_key == stage["key"]:
            break
    return filters, stages


def build_vocal_stage_filter(
    decisions,
    stage_key,
    output_label="vocal",
    include_second_pass_gain=True,
):
    filters, _ = vocal_filters_through_stage(
        decisions,
        stage_key=stage_key,
        include_second_pass_gain=include_second_pass_gain,
    )
    filters.append("aformat=sample_rates=48000:channel_layouts=stereo")
    return f"[1:a]{','.join(filters)}[{output_label}]"


def build_vocal_filter(decisions, output_label="vocal", include_second_pass_gain=True):
    filters, _ = vocal_filters_through_stage(
        decisions,
        stage_key=None,
        include_second_pass_gain=include_second_pass_gain,
    )
    filters.append("aformat=sample_rates=48000:channel_layouts=stereo")
    return f"[1:a]{','.join(filters)}[{output_label}]"


def build_mix_filter(decisions):
    backing = build_backing_filter(decisions)
    vocal = build_vocal_filter(decisions)
    mix = (
        f"[backing][vocal]amix=inputs=2:duration=longest:dropout_transition=0:"
        f"weights='{decisions['backing_weight']} {decisions['vocal_weight']}':"
        f"normalize=0,"
        "alimiter=limit=0.95[out]"
    )
    return ";".join([backing, vocal, mix])


def build_debug_stage_mix_filter(decisions, stage_key):
    backing = build_backing_filter(decisions, "debug_backing")
    vocal = build_vocal_stage_filter(decisions, stage_key, "debug_vocal")
    mix = (
        f"[debug_backing][debug_vocal]amix=inputs=2:duration=longest:"
        f"dropout_transition=0:weights='{decisions['backing_weight']} {decisions['vocal_weight']}':"
        f"normalize=0,alimiter=limit=0.95[debug_mix]"
    )
    return ";".join([backing, vocal, mix])


def waveform_json_for_audio(command_id, source_path, output_path, work_dir, label, points=512):
    pcm_path = work_dir / f"{output_path.stem}.s16le"
    args = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "8000",
        "-f",
        "s16le",
        str(pcm_path),
    ]
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=PROCESSING_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(stderr[-2000:] or "Waveform extraction failed")

    raw = pcm_path.read_bytes()
    samples = array.array("h")
    samples.frombytes(raw)
    if not samples:
        waveform = {
            "label": label,
            "sample_rate": 8000,
            "duration_seconds": 0,
            "points": [],
            "summary": {
                "peak": 0,
                "rms_db_p50": None,
                "rms_db_p75": None,
            },
        }
    else:
        point_count = min(max(1, points), len(samples))
        window_size = max(1, math.ceil(len(samples) / point_count))
        waveform_points = []
        rms_values = []
        peak_values = []
        for start in range(0, len(samples), window_size):
            window = samples[start : start + window_size]
            if not window:
                continue
            peak = max(abs(sample) for sample in window) / 32768
            rms_db = samples_rms_db(window)
            if rms_db is not None:
                rms_values.append(rms_db)
            peak_values.append(peak)
            waveform_points.append(
                {
                    "time_seconds": round(start / 8000, 3),
                    "peak": round(peak, 5),
                    "rms_db": round(rms_db, 3) if rms_db is not None else None,
                }
            )
        waveform = {
            "label": label,
            "sample_rate": 8000,
            "duration_seconds": round(len(samples) / 8000, 3),
            "points": waveform_points,
            "summary": {
                "peak": round(max(peak_values) if peak_values else 0, 5),
                "rms_db_p50": round(percentile(rms_values, 0.50), 3)
                if rms_values
                else None,
                "rms_db_p75": round(percentile(rms_values, 0.75), 3)
                if rms_values
                else None,
            },
        }

    output_path.write_text(json.dumps(waveform, separators=(",", ":")), encoding="utf-8")
    return waveform["summary"]


def create_stage_debug_artifacts(command_id, backing_path, vocal_path, output_dir, work_dir, decisions):
    if not decisions.get("debug_stage_artifacts_enabled", True):
        return {"enabled": False, "stages": []}

    short_id = command_id.replace("mix-", "").replace("job-", "")[:12]
    points = int(decisions.get("debug_waveform_points", 512))
    debug_artifacts = {
        "enabled": True,
        "kind": "vocal_stage_feedback",
        "public_base_url": f"{PUBLIC_BASE_URL}/tmp/{command_id}",
        "base": {},
        "stages": [],
    }

    original_waveform_path = output_dir / f"00-original-vocal-waveform-{short_id}.json"
    original_summary = waveform_json_for_audio(
        command_id,
        vocal_path,
        original_waveform_path,
        work_dir,
        "Original vocal input",
        points=points,
    )
    debug_artifacts["base"]["original_vocal_waveform"] = {
        "storage_url": public_output_url(command_id, original_waveform_path),
        "summary": original_summary,
    }
    set_job_status(command_id, debug_artifacts=debug_artifacts)

    _, stages = vocal_stage_filter_parts(decisions)
    for order, stage in enumerate(stages, start=1):
        stage_key = stage["key"]
        mix_path = output_dir / f"{order:02d}-{stage_key}-mix-{short_id}.wav"
        waveform_path = output_dir / f"{order:02d}-{stage_key}-waveform-{short_id}.json"
        args = [
            "ffmpeg",
            "-y",
            "-i",
            str(backing_path),
            "-i",
            str(vocal_path),
            "-filter_complex",
            build_debug_stage_mix_filter(decisions, stage_key),
            "-map",
            "[debug_mix]",
            "-c:a",
            "pcm_s16le",
            str(mix_path),
        ]
        log_event(
            "mix_stage_debug_command_started",
            command_id=command_id,
            stage=stage_key,
            args=[truncate(arg, 800) for arg in args],
        )
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=PROCESSING_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            raise RuntimeError(stderr[-2000:] or f"Debug stage render failed: {stage_key}")
        if not mix_path.exists() or mix_path.stat().st_size == 0:
            raise RuntimeError(f"Expected debug stage mix was not created: {stage_key}")

        summary = waveform_json_for_audio(
            command_id,
            mix_path,
            waveform_path,
            work_dir,
            f"{stage['label']} mix",
            points=points,
        )
        stage_artifact = {
            "order": order,
            "key": stage_key,
            "label": stage["label"],
            "mix": {"storage_url": public_output_url(command_id, mix_path)},
            "waveform": {"storage_url": public_output_url(command_id, waveform_path)},
            "compare_to": "original_vocal_waveform",
            "summary": summary,
            "stage_filters": stage["filters"],
        }
        debug_artifacts["stages"].append(stage_artifact)
        set_job_status(command_id, debug_artifacts=debug_artifacts)
        log_event(
            "mix_stage_debug_completed",
            command_id=command_id,
            stage=stage_key,
            mix_url=stage_artifact["mix"]["storage_url"],
            waveform_url=stage_artifact["waveform"]["storage_url"],
            summary=summary,
        )

    return debug_artifacts


def apply_vocal_second_pass_leveling(command_id, backing_path, vocal_path, work_dir, decisions):
    if not decisions.get("vocal_second_pass_enabled", True):
        return decisions

    backing_stem_path = work_dir / "second-pass-backing.wav"
    vocal_stem_path = work_dir / "second-pass-vocal-first-pass.wav"
    filter_complex = ";".join(
        [
            build_backing_filter(decisions, "second_pass_backing"),
            build_vocal_filter(
                decisions,
                "second_pass_vocal",
                include_second_pass_gain=False,
            ),
        ]
    )
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(backing_path),
        "-i",
        str(vocal_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[second_pass_backing]",
        "-c:a",
        "pcm_s16le",
        str(backing_stem_path),
        "-map",
        "[second_pass_vocal]",
        "-c:a",
        "pcm_s16le",
        str(vocal_stem_path),
    ]
    log_event(
        "mix_second_pass_measurement_started",
        command_id=command_id,
        args=[truncate(arg, 800) for arg in args],
    )
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=PROCESSING_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        log_event(
            "mix_second_pass_measurement_failed",
            command_id=command_id,
            returncode=completed.returncode,
            stderr=truncate(stderr, 4000),
        )
        raise RuntimeError(stderr[-2000:] or "Second-pass stem measurement failed")

    stem_filter = "pan=mono|c0=0.5*c0+0.5*c1"
    backing_active = active_rms_file(backing_stem_path, stem_filter)
    vocal_active = active_rms_file(vocal_stem_path, stem_filter)
    backing_level = backing_active.get("active_rms_p75_db")
    vocal_level = vocal_active.get("active_rms_p75_db")
    gain_db = 0
    if backing_level is not None and vocal_level is not None:
        target_level = backing_level + decisions["vocal_second_pass_target_over_backing_db"]
        gain_db = clamp(
            target_level - vocal_level,
            -decisions["vocal_second_pass_max_cut_db"],
            decisions["vocal_second_pass_max_boost_db"],
        )

    decisions["vocal_second_pass_gain_db"] = round(gain_db, 3)
    decisions["vocal_second_pass_backing_active_rms_p75_db"] = backing_level
    decisions["vocal_second_pass_vocal_active_rms_p75_db"] = vocal_level
    log_event(
        "mix_second_pass_measurement_completed",
        command_id=command_id,
        backing_active_rms_p75_db=backing_level,
        vocal_active_rms_p75_db=vocal_level,
        target_over_backing_db=decisions["vocal_second_pass_target_over_backing_db"],
        applied_vocal_gain_db=decisions["vocal_second_pass_gain_db"],
        max_boost_db=decisions["vocal_second_pass_max_boost_db"],
        max_cut_db=decisions["vocal_second_pass_max_cut_db"],
    )
    return decisions


def build_reference_mix_filter():
    backing = "[0:a]aformat=sample_rates=48000:channel_layouts=stereo[backing]"
    reference_vocal = (
        "[1:a]pan=mono|c0=c0+c1,pan=stereo|c0=c0|c1=c0,"
        "aformat=sample_rates=48000:channel_layouts=stereo[reference_vocal]"
    )
    mix = (
        "[backing][reference_vocal]amix=inputs=2:duration=longest:"
        "dropout_transition=0:weights='1 1':normalize=0,"
        "alimiter=limit=0.98[out]"
    )
    return ";".join([backing, reference_vocal, mix])


def encode_args_for_output(output_path):
    suffix = output_path.suffix.lower()
    if suffix == ".wav":
        return ["-c:a", "pcm_s16le"]
    if suffix in {".m4a", ".aac"}:
        return ["-c:a", "aac", "-b:a", "192k"]
    return ["-c:a", "libmp3lame", "-q:a", "2"]


def public_output_url(command_id, path):
    return f"{PUBLIC_BASE_URL}/tmp/{command_id}/{quote(path.name)}"


def create_reference_debug_mix(command_id, backing_path, reference_vocal_path, output_dir):
    output_path = output_dir / "debug_original_reference_mix.mp3"
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(backing_path),
        "-i",
        str(reference_vocal_path),
        "-filter_complex",
        build_reference_mix_filter(),
        "-map",
        "[out]",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(output_path),
    ]
    log_event(
        "reference_debug_mix_command_started",
        command_id=command_id,
        args=[truncate(arg, 800) for arg in args],
    )
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=PROCESSING_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        log_event(
            "reference_debug_mix_command_failed",
            command_id=command_id,
            returncode=completed.returncode,
            stderr=truncate(stderr, 4000),
        )
        raise RuntimeError(stderr[-2000:] or "Reference debug mix exited with an error")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("Expected reference debug mix file was not created")

    debug_urls = {
        "original_reference_mix": {"storage_url": public_output_url(command_id, output_path)}
    }
    log_event(
        "reference_debug_mix_completed",
        command_id=command_id,
        output_files=debug_urls,
    )
    return debug_urls


def cleanup_old_outputs():
    cutoff = time.time() - OUTPUT_TTL_SECONDS
    for path in OUTPUT_ROOT.iterdir():
        try:
            if path.stat().st_mtime < cutoff:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        except OSError as exc:
            log_event("output_cleanup_failed", path=str(path), error=truncate(exc, 1000))


def extension_from_url(url):
    suffix = Path(urlparse(url).path).suffix
    if suffix and SAFE_FILENAME_RE.match(f"file{suffix}"):
        return suffix[:16]
    return ".bin"


def download_file(url, destination):
    request = Request(url, headers={"User-Agent": "ffmpeg-sound-mixer-api/0.1"})
    try:
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            with destination.open("wb") as file:
                shutil.copyfileobj(response, file)
    except HTTPError as exc:
        raise RuntimeError(f"Download failed with HTTP {exc.code}: {url}") from exc
    except (OSError, URLError) as exc:
        raise RuntimeError(f"Download failed: {url}") from exc


VOICE_GATE_SAMPLE_RATE = 16000
VOICE_GATE_MASK_SAMPLE_RATE = 1000
VOICE_GATE_FRAME_MS = 32
VOICE_GATE_HOP_MS = 10
VOICE_GATE_MODES = {
    "conservative": {
        "noise_offset_db": 5.5,
        "min_score": 2.35,
        "min_vocal_ratio": 0.36,
        "max_high_ratio": 1.35,
        "max_low_ratio": 0.45,
        "default_padding_ms": 190,
    },
    "balanced": {
        "noise_offset_db": 8.0,
        "min_score": 2.75,
        "min_vocal_ratio": 0.42,
        "max_high_ratio": 1.05,
        "max_low_ratio": 0.38,
        "default_padding_ms": 150,
    },
    "aggressive": {
        "noise_offset_db": 11.0,
        "min_score": 3.05,
        "min_vocal_ratio": 0.48,
        "max_high_ratio": 0.85,
        "max_low_ratio": 0.32,
        "default_padding_ms": 110,
    },
}


def pcm_array_from_file(path, audio_filter=None, channels=1, sample_rate=16000, duration=None):
    filters = []
    if audio_filter:
        filters.append(audio_filter)
    filters.append(f"aresample={sample_rate}")
    args = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-i",
        str(path),
    ]
    if duration:
        args.extend(["-t", str(duration)])
    args.extend(
        [
            "-af",
            ",".join(filters),
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-",
        ]
    )
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=PROCESSING_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"PCM analysis failed: {stderr[-2000:]}")
    samples = array.array("h")
    samples.frombytes(completed.stdout)
    return samples


def percentile(values, percent):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * clamp(percent, 0, 100) / 100
    lower = int(math.floor(index))
    upper = int(math.ceil(index))
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def mean_square_to_db(mean_square):
    if mean_square <= 0:
        return -120.0
    return 10 * math.log10(mean_square / float(32768 * 32768))


def sliding_mean_squares(samples, frame_size, hop_size):
    if len(samples) < frame_size or frame_size <= 0 or hop_size <= 0:
        return []
    current = sum(sample * sample for sample in samples[:frame_size])
    values = [current / frame_size]
    start = 0
    last_start = len(samples) - frame_size
    while start + hop_size <= last_start:
        next_start = start + hop_size
        for index in range(start, next_start):
            current -= samples[index] * samples[index]
        for index in range(start + frame_size, next_start + frame_size):
            current += samples[index] * samples[index]
        values.append(current / frame_size)
        start = next_start
    return values


def voice_signal_quality(band_energy):
    epsilon = 1e-9
    total_full = sum(band_energy.get("full", []))
    total_vocal = sum(band_energy.get("vocal", []))
    total_presence = sum(band_energy.get("presence", []))
    total_high = sum(band_energy.get("high", []))
    total_low = sum(band_energy.get("low", []))
    vocal_ratio = total_vocal / max(total_full, epsilon)
    presence_ratio = total_presence / max(total_vocal, epsilon)
    high_ratio = total_high / max(total_vocal, epsilon)
    low_ratio = total_low / max(total_full, epsilon)
    non_vocal_noise = bool(
        (low_ratio >= 0.55 and vocal_ratio <= 0.45)
        or vocal_ratio <= 0.22
        or (high_ratio >= 0.22 and vocal_ratio <= 0.55)
    )
    reason = ""
    if non_vocal_noise:
        if low_ratio >= 0.55 and vocal_ratio <= 0.45:
            reason = "low-frequency noise dominates vocal band"
        elif vocal_ratio <= 0.22:
            reason = "insufficient vocal-band energy"
        else:
            reason = "high-frequency noise dominates vocal band"
    return {
        "vocal_ratio": round(vocal_ratio, 4),
        "presence_ratio": round(presence_ratio, 4),
        "high_ratio": round(high_ratio, 4),
        "low_ratio": round(low_ratio, 4),
        "non_vocal_noise_detected": non_vocal_noise,
        "reason": reason,
    }


def bool_segments(flags):
    segments = []
    start = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            segments.append((start, index - 1))
            start = None
    if start is not None:
        segments.append((start, len(flags) - 1))
    return segments


def smooth_voice_flags(flags, min_voice_frames, min_gap_frames):
    if not flags:
        return []
    segments = bool_segments(flags)
    if not segments:
        return [False] * len(flags)

    merged = []
    for start, end in segments:
        if end - start + 1 < min_voice_frames:
            continue
        if merged and start - merged[-1][1] - 1 <= min_gap_frames:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))

    smoothed = [False] * len(flags)
    for start, end in merged:
        for index in range(start, end + 1):
            smoothed[index] = True
    return smoothed


def padded_time_segments(frame_segments, duration, frame_seconds, hop_seconds, padding_seconds):
    segments = []
    for first_frame, last_frame in frame_segments:
        start = max(0.0, first_frame * hop_seconds - padding_seconds)
        end = min(duration, last_frame * hop_seconds + frame_seconds + padding_seconds)
        if end <= start:
            continue
        if segments and start <= segments[-1]["end"]:
            segments[-1]["end"] = max(segments[-1]["end"], end)
        else:
            segments.append({"start": round(start, 4), "end": round(end, 4)})
    return segments


def suppress_leading_artifact_segments(segments, duration, options):
    if not options.get("suppress_leading_artifacts", True):
        return segments, None
    if len(segments) < 2 or duration < 15:
        return segments, None

    first = segments[0]
    max_start = options.get("leading_artifact_max_start_seconds", 8.0)
    max_end = options.get("leading_artifact_max_end_seconds", 15.0)
    max_next_start = options.get("leading_artifact_max_next_start_seconds", 22.0)
    min_gap = options.get("leading_artifact_min_gap_seconds", 0.55)
    min_remaining_ratio = options.get("leading_artifact_min_remaining_ratio", 1.5)

    if first["start"] > max_start or first["end"] > max_end:
        return segments, None

    cutoff_index = 0
    for index, segment in enumerate(segments):
        if segment["start"] <= max_start + 4 and segment["end"] <= max_end:
            cutoff_index = index + 1
            continue
        break

    if cutoff_index <= 0 or cutoff_index >= len(segments):
        return segments, None

    leading_end = segments[cutoff_index - 1]["end"]
    main_start = segments[cutoff_index]["start"]
    if main_start > max_next_start or main_start - leading_end < min_gap:
        return segments, None

    leading_seconds = sum(item["end"] - item["start"] for item in segments[:cutoff_index])
    remaining_seconds = sum(item["end"] - item["start"] for item in segments[cutoff_index:])
    if remaining_seconds < max(12.0, leading_seconds * min_remaining_ratio):
        return segments, None

    return segments[cutoff_index:], {
        "suppressed_segment_count": cutoff_index,
        "suppressed_until_seconds": round(leading_end, 4),
        "main_start_seconds": round(main_start, 4),
        "leading_seconds": round(leading_seconds, 3),
        "remaining_seconds": round(remaining_seconds, 3),
        "reason": "early isolated active block before sustained vocal body",
    }


def analyze_voice_activity(path, options):
    mode_name = options["mode"]
    mode = VOICE_GATE_MODES[mode_name]
    probe = ffprobe_audio(path)
    duration = probe.get("duration") or 0
    if duration <= 0:
        raise RuntimeError("Could not determine audio duration")

    frame_size = max(1, round(VOICE_GATE_SAMPLE_RATE * VOICE_GATE_FRAME_MS / 1000))
    hop_size = max(1, round(VOICE_GATE_SAMPLE_RATE * VOICE_GATE_HOP_MS / 1000))
    full = pcm_array_from_file(path, channels=1, sample_rate=VOICE_GATE_SAMPLE_RATE)
    if len(full) < frame_size:
        raise RuntimeError("Audio is too short to process")

    bands = {
        "full": full,
        "low": pcm_array_from_file(
            path,
            audio_filter="lowpass=f=120",
            channels=1,
            sample_rate=VOICE_GATE_SAMPLE_RATE,
        ),
        "vocal": pcm_array_from_file(
            path,
            audio_filter="highpass=f=120,lowpass=f=4200",
            channels=1,
            sample_rate=VOICE_GATE_SAMPLE_RATE,
        ),
        "presence": pcm_array_from_file(
            path,
            audio_filter="highpass=f=650,lowpass=f=3600",
            channels=1,
            sample_rate=VOICE_GATE_SAMPLE_RATE,
        ),
        "high": pcm_array_from_file(
            path,
            audio_filter="highpass=f=6000",
            channels=1,
            sample_rate=VOICE_GATE_SAMPLE_RATE,
        ),
    }
    min_len = min(len(samples) for samples in bands.values())
    if min_len < frame_size:
        raise RuntimeError("Audio is too short to process")
    band_energy = {
        name: sliding_mean_squares(samples[:min_len], frame_size, hop_size)
        for name, samples in bands.items()
    }
    frame_count = min(len(values) for values in band_energy.values())
    for name in list(band_energy):
        band_energy[name] = band_energy[name][:frame_count]

    signal_quality = voice_signal_quality(band_energy)
    full_db = [mean_square_to_db(value) for value in band_energy["full"]]
    vocal_db = [mean_square_to_db(value) for value in band_energy["vocal"]]
    presence_db = [mean_square_to_db(value) for value in band_energy["presence"]]
    noise_floor_db = percentile(full_db, 20)
    vocal_floor_db = percentile(vocal_db, 20)
    presence_floor_db = percentile(presence_db, 20)
    active_threshold_db = max(
        noise_floor_db + mode["noise_offset_db"],
        percentile(full_db, 90) - 34,
        -62,
    )
    vocal_threshold_db = max(vocal_floor_db + mode["noise_offset_db"] - 2.5, -65)
    presence_threshold_db = max(presence_floor_db + mode["noise_offset_db"] - 5, -68)

    flags = []
    frame_scores = []
    epsilon = 1e-9
    if signal_quality["non_vocal_noise_detected"]:
        flags = [False] * frame_count
        frame_scores = [-3.0] * frame_count
    else:
        for index in range(frame_count):
            full_energy = band_energy["full"][index]
            vocal_energy = band_energy["vocal"][index]
            presence_energy = band_energy["presence"][index]
            high_energy = band_energy["high"][index]
            low_energy = band_energy["low"][index]
            vocal_ratio = vocal_energy / max(full_energy, epsilon)
            presence_ratio = presence_energy / max(vocal_energy, epsilon)
            high_ratio = high_energy / max(vocal_energy, epsilon)
            low_ratio = low_energy / max(full_energy, epsilon)

            score = 0.0
            if full_db[index] >= active_threshold_db:
                score += 1.2
            if vocal_db[index] >= vocal_threshold_db:
                score += 0.9
            if presence_db[index] >= presence_threshold_db:
                score += 0.55
            if vocal_ratio >= mode["min_vocal_ratio"]:
                score += 0.55
            if 0.05 <= presence_ratio <= 1.45:
                score += 0.35
            if high_ratio <= mode["max_high_ratio"]:
                score += 0.35
            if low_ratio <= mode["max_low_ratio"]:
                score += 0.25
            if full_db[index] < active_threshold_db - 5:
                score -= 1.0
            if vocal_ratio < mode["min_vocal_ratio"]:
                score -= clamp((mode["min_vocal_ratio"] - vocal_ratio) * 3.0, 0.2, 1.0)
            if low_ratio > mode["max_low_ratio"]:
                score -= clamp((low_ratio - mode["max_low_ratio"]) * 3.0, 0.25, 1.25)
            if high_ratio > mode["max_high_ratio"] * 1.8 and full_db[index] < percentile(full_db, 75):
                score -= 0.75

            is_voice = score >= mode["min_score"]
            flags.append(is_voice)
            frame_scores.append(round(score, 3))

    min_voice_frames = max(1, round(options["min_voice_ms"] / VOICE_GATE_HOP_MS))
    min_gap_frames = max(1, round(options["min_gap_ms"] / VOICE_GATE_HOP_MS))
    smoothed = smooth_voice_flags(flags, min_voice_frames, min_gap_frames)
    raw_segments = bool_segments(flags)
    voice_segments = padded_time_segments(
        bool_segments(smoothed),
        duration,
        VOICE_GATE_FRAME_MS / 1000,
        VOICE_GATE_HOP_MS / 1000,
        options["padding_ms"] / 1000,
    )
    voice_segments, leading_artifact = suppress_leading_artifact_segments(
        voice_segments,
        duration,
        options,
    )
    voice_seconds = round(sum(item["end"] - item["start"] for item in voice_segments), 3)
    muted_seconds = round(max(0.0, duration - voice_seconds), 3)
    return {
        "duration_seconds": round(duration, 3),
        "mode": mode_name,
        "sample_rate": VOICE_GATE_SAMPLE_RATE,
        "frame_ms": VOICE_GATE_FRAME_MS,
        "hop_ms": VOICE_GATE_HOP_MS,
        "thresholds": {
            "noise_floor_db": round(noise_floor_db, 3),
            "active_threshold_db": round(active_threshold_db, 3),
            "vocal_threshold_db": round(vocal_threshold_db, 3),
            "presence_threshold_db": round(presence_threshold_db, 3),
        },
        "frame_count": frame_count,
        "raw_voice_frame_count": sum(1 for flag in flags if flag),
        "smoothed_voice_frame_count": sum(1 for flag in smoothed if flag),
        "raw_segment_count": len(raw_segments),
        "voice_segment_count": len(voice_segments),
        "voice_seconds": voice_seconds,
        "muted_seconds": muted_seconds,
        "voice_ratio": round(voice_seconds / duration, 4) if duration else 0,
        "segments": voice_segments,
        "leading_artifact": leading_artifact,
        "signal_quality": signal_quality,
        "score_summary": {
            "p10": round(percentile(frame_scores, 10), 3),
            "p50": round(percentile(frame_scores, 50), 3),
            "p90": round(percentile(frame_scores, 90), 3),
        },
    }


def gain_for_time(timestamp, segments, floor_gain, attack_seconds, release_seconds):
    for segment in segments:
        start = segment["start"]
        end = segment["end"]
        if timestamp < start:
            return floor_gain
        if start <= timestamp <= end:
            fade_in = 1.0
            fade_out = 1.0
            if attack_seconds > 0:
                fade_in = clamp((timestamp - start) / attack_seconds, 0, 1)
            if release_seconds > 0:
                fade_out = clamp((end - timestamp) / release_seconds, 0, 1)
            envelope = min(fade_in, fade_out)
            return floor_gain + (1.0 - floor_gain) * envelope
    return floor_gain


def write_voice_gate_mask(mask_path, duration, segments, attenuation_db, attack_ms, release_ms):
    sample_rate = VOICE_GATE_MASK_SAMPLE_RATE
    total_samples = max(1, int(math.ceil(duration * sample_rate)))
    floor_gain = db_to_linear(attenuation_db)
    attack_seconds = attack_ms / 1000
    release_seconds = release_ms / 1000
    with wave.open(str(mask_path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        chunk = array.array("h")
        for index in range(total_samples):
            timestamp = index / sample_rate
            gain = gain_for_time(timestamp, segments, floor_gain, attack_seconds, release_seconds)
            value = int(clamp(gain, 0, 1) * 32767)
            chunk.append(value)
            chunk.append(value)
            if len(chunk) >= sample_rate * 2:
                wav.writeframes(chunk.tobytes())
                chunk = array.array("h")
        if chunk:
            wav.writeframes(chunk.tobytes())


def render_voice_gate(command_id, stage, input_path, mask_path, output_path):
    filtergraph = (
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo[main];"
        "[1:a]aformat=sample_rates=48000:channel_layouts=stereo[mask];"
        "[main][mask]amultiply[out]"
    )
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-i",
        str(mask_path),
        "-filter_complex",
        filtergraph,
        "-map",
        "[out]",
        *encode_args_for_output(output_path),
        str(output_path),
    ]
    run_logged_ffmpeg(command_id, stage, args)
    return filtergraph


def apply_voice_gate_to_file(command_id, stage, input_path, output_path, options):
    work_dir = output_path.parent / f".{output_path.stem}-{uuid.uuid4().hex}-voicegate"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        mask_path = work_dir / "voice_mask.wav"
        rendered_path = work_dir / output_path.name
        activity = analyze_voice_activity(input_path, options)
        if (
            activity["voice_segment_count"] == 0
            and options.get("fail_on_no_voice", True)
            and not activity.get("signal_quality", {}).get("non_vocal_noise_detected")
        ):
            raise RuntimeError("No confident singing voice segments detected")
        write_voice_gate_mask(
            mask_path,
            activity["duration_seconds"],
            activity["segments"],
            options["attenuation_db"],
            options["attack_ms"],
            options["release_ms"],
        )
        filtergraph = render_voice_gate(command_id, stage, input_path, mask_path, rendered_path)
        if not rendered_path.exists() or rendered_path.stat().st_size == 0:
            raise RuntimeError("Expected voice-gated output file was not created")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(rendered_path), str(output_path))
        return {
            "enabled": True,
            "stage": stage,
            "options": options,
            "analysis": activity,
            "filtergraph": filtergraph,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def replace_placeholders(command, paths):
    def replace(match):
        label = match.group(1)
        try:
            return str(paths[label])
        except KeyError as exc:
            raise RuntimeError(f"Unknown placeholder {label}") from exc

    return PLACEHOLDER_RE.sub(replace, command)


def is_allowed_local_path(arg, allowed_roots):
    if not arg.startswith("/"):
        return True

    try:
        resolved = Path(arg).resolve()
    except OSError:
        return False

    return any(resolved == root or root in resolved.parents for root in allowed_roots)


def build_ffmpeg_args(command, input_paths, output_paths):
    paths = {**input_paths, **output_paths}
    command = command.replace(":LR=", ":LRA=")
    substituted = replace_placeholders(command, paths)
    try:
        tokens = shlex.split(substituted)
    except ValueError as exc:
        raise RuntimeError(f"Invalid ffmpeg_command: {exc}") from exc

    if tokens and tokens[0] == "ffmpeg":
        tokens = tokens[1:]

    if not tokens:
        raise RuntimeError("ffmpeg_command produced no arguments")

    allowed_roots = {path.parent.resolve() for path in paths.values()}
    for token in tokens:
        if "://" in token:
            raise RuntimeError("ffmpeg_command must use input placeholders, not URLs")
        if not is_allowed_local_path(token, allowed_roots):
            raise RuntimeError(f"ffmpeg_command references disallowed path: {token}")

    return ["ffmpeg", "-y", *tokens]


def post_webhook(url, payload):
    body_text = json.dumps(json_safe(payload), allow_nan=False)
    body = body_text.encode("utf-8")
    log_event(
        "webhook_post_started",
        webhook_url=url,
        payload=truncate(body_text, 5000),
    )
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
        body = response.read().decode("utf-8", errors="replace")
        log_event(
            "webhook_posted",
            webhook_url=url,
            status=response.status,
            response=truncate(body, 1000),
        )
        return {
            "ok": True,
            "status": response.status,
            "response": truncate(body, 1000),
        }


def post_webhook_safely(url, payload, event_prefix="webhook"):
    try:
        return post_webhook(url, payload)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        result = {
            "ok": False,
            "status": exc.code,
            "error": str(exc),
            "response": truncate(body, 2000),
        }
        log_event(
            f"{event_prefix}_post_failed",
            webhook_url=url,
            status=exc.code,
            error=truncate(exc, 1000),
            response=truncate(body, 2000),
        )
        return result
    except Exception as exc:
        result = {
            "ok": False,
            "status": None,
            "error": str(exc),
            "response": "",
        }
        log_event(
            f"{event_prefix}_post_failed",
            webhook_url=url,
            error=truncate(exc, 1000),
        )
        return result


def webhook_payload(status, request_payload, output_urls=None, error_message=None):
    data = {
        "status": status,
        "output_files": output_urls or {},
        "original_request": {"output_files": request_payload["output_files"]},
        "webhook_metadata": request_payload["webhook_metadata"],
    }
    if status == "FAILED":
        data["error_message"] = error_message or "FFmpeg processing failed"
        data["error_status"] = "ffmpeg_error"
    return {"data": data}


def mix_webhook_payload(
    status,
    request_payload,
    output_urls=None,
    analysis=None,
    decisions=None,
    debug_artifacts=None,
    debug_error="",
    error_message=None,
):
    data = {
        "status": status,
        "output_files": output_urls or {},
        "original_request": {"output_files": request_payload["output_files"]},
        "webhook_metadata": request_payload.get("webhook_metadata", {}),
    }
    if analysis is not None:
        data["analysis"] = analysis
    if decisions is not None:
        data["mix_decisions"] = decisions
    if debug_artifacts is not None:
        data["debug_artifacts"] = debug_artifacts
    if debug_error:
        data["debug_error"] = debug_error
    if status == "FAILED":
        data["error_message"] = error_message or "Analyze and mix failed"
        data["error_status"] = "mix_error"
    return {"data": data}


def set_job_status(command_id, **updates):
    with jobs_lock:
        jobs.setdefault(command_id, {}).update(updates)


def append_job_log(command_id, event, **fields):
    entry = json_safe({"ts": utc_timestamp(), "event": event, **fields})
    with jobs_lock:
        job = jobs.setdefault(command_id, {})
        job.setdefault("processing_log", []).append(entry)
    log_event(event, command_id=command_id, **fields)


def run_logged_ffmpeg(command_id, stage, args):
    log_event(
        "ffmpeg_command_started",
        command_id=command_id,
        stage=stage,
        args=[truncate(arg, 500) for arg in args],
    )
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=PROCESSING_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        log_event(
            "ffmpeg_command_failed",
            command_id=command_id,
            stage=stage,
            returncode=completed.returncode,
            stderr=truncate(stderr, 4000),
        )
        raise RuntimeError(stderr[-2000:] or "FFmpeg exited with an error")


def run_ffmpeg_job(command_id, request_payload):
    set_job_status(command_id, status="RUNNING")
    started = time.monotonic()
    log_event(
        "ffmpeg_job_started",
        command_id=command_id,
        input_files=request_payload["input_files"],
        output_files=request_payload["output_files"],
        webhook_metadata=request_payload["webhook_metadata"],
    )
    output_dir = OUTPUT_ROOT / command_id
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"{command_id}-") as work_dir_name:
        work_dir = Path(work_dir_name)
        try:
            cleanup_old_outputs()

            input_paths = {}
            for label, url in request_payload["input_files"].items():
                destination = work_dir / f"{label}{extension_from_url(url)}"
                log_event("ffmpeg_input_download_started", command_id=command_id, label=label, url=url)
                download_file(url, destination)
                log_event(
                    "ffmpeg_input_downloaded",
                    command_id=command_id,
                    label=label,
                    bytes=destination.stat().st_size,
                )
                input_paths[label] = destination

            output_paths = {
                label: output_dir / filename
                for label, filename in request_payload["output_files"].items()
            }
            args = build_ffmpeg_args(
                request_payload["ffmpeg_command"], input_paths, output_paths
            )
            log_event(
                "ffmpeg_command_started",
                command_id=command_id,
                args=[truncate(arg, 500) for arg in args],
            )

            completed = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=PROCESSING_TIMEOUT_SECONDS,
                check=False,
            )
            if completed.returncode != 0:
                stderr = (completed.stderr or "").strip()
                log_event(
                    "ffmpeg_command_failed",
                    command_id=command_id,
                    returncode=completed.returncode,
                    stderr=truncate(stderr, 4000),
                )
                raise RuntimeError(stderr[-2000:] or "FFmpeg exited with an error")

            output_urls = {}
            for label, path in output_paths.items():
                if not path.exists() or path.stat().st_size == 0:
                    raise RuntimeError(f"Expected output file was not created: {label}")
                filename = quote(path.name)
                output_urls[label] = {
                    "storage_url": f"{PUBLIC_BASE_URL}/tmp/{command_id}/{filename}"
                }

            webhook_status = (
                "SUCCESS"
                if request_payload.get("job_type") == "mux-video"
                else "COMPLETED"
            )
            payload = webhook_payload(webhook_status, request_payload, output_urls)
            post_webhook(request_payload["webhook_url"], payload)
            set_job_status(command_id, status="COMPLETED", output_files=output_urls)
            duration = round(time.monotonic() - started, 3)
            log_event(
                "ffmpeg_job_completed",
                command_id=command_id,
                duration_seconds=duration,
                output_files=output_urls,
            )
            notify_job_success(command_id, request_payload, output_urls)
        except Exception as exc:
            message = str(exc) or "FFmpeg processing failed"
            duration = round(time.monotonic() - started, 3)
            log_event(
                "ffmpeg_job_failed",
                command_id=command_id,
                duration_seconds=duration,
                error=truncate(message, 4000),
            )
            payload = webhook_payload(
                "FAILED", request_payload, output_urls={}, error_message=message
            )
            try:
                post_webhook(request_payload["webhook_url"], payload)
            except Exception as webhook_exc:
                log_event(
                    "webhook_failure_post_failed",
                    command_id=command_id,
                    error=truncate(webhook_exc, 2000),
                )
            set_job_status(command_id, status="FAILED", error_message=message)
            notify_job_failure(command_id, request_payload, message)


def run_mux_video_job(command_id, request_payload):
    set_job_status(command_id, status="RUNNING")
    started = time.monotonic()
    log_event(
        "ffmpeg_job_started",
        command_id=command_id,
        input_files=request_payload["input_files"],
        output_files=request_payload["output_files"],
        webhook_metadata=request_payload["webhook_metadata"],
    )
    output_dir = OUTPUT_ROOT / command_id
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"{command_id}-") as work_dir_name:
        work_dir = Path(work_dir_name)
        try:
            cleanup_old_outputs()

            input_paths = {}
            for label, url in request_payload["input_files"].items():
                destination = work_dir / f"{label}{extension_from_url(url)}"
                log_event("ffmpeg_input_download_started", command_id=command_id, label=label, url=url)
                download_file(url, destination)
                log_event(
                    "ffmpeg_input_downloaded",
                    command_id=command_id,
                    label=label,
                    bytes=destination.stat().st_size,
                )
                input_paths[label] = destination

            output_paths = {
                label: output_dir / filename
                for label, filename in request_payload["output_files"].items()
            }
            output_path = output_paths["out_1"]
            burn_logo = request_payload.get("burn_logo", True)
            video_suffix = input_paths["in_1"].suffix.lower()
            if burn_logo:
                synced_suffix = ".webm" if video_suffix == ".webm" else ".mp4"
                synced_path = work_dir / f"{command_id}-synced{synced_suffix}"
            else:
                synced_path = output_path

            audio_delay_seconds = request_payload.get("audio_delay_seconds", 0.0)
            if request_payload.get("auto_audio_sync", True):
                audio_delay_seconds += estimate_mux_audio_delay(
                    command_id,
                    input_paths["in_1"],
                    input_paths["in_2"],
                    work_dir,
                )
            log_event(
                "mux_audio_sync_applied",
                command_id=command_id,
                audio_delay_seconds=round(audio_delay_seconds, 3),
            )

            run_logged_ffmpeg(
                command_id,
                "mux_sync",
                mux_stage_one_args(
                    input_paths["in_1"],
                    input_paths["in_2"],
                    synced_path,
                    audio_delay_seconds=audio_delay_seconds,
                ),
            )
            if not synced_path.exists() or synced_path.stat().st_size == 0:
                raise RuntimeError("Expected synced mux file was not created")

            if burn_logo:
                run_logged_ffmpeg(
                    command_id,
                    "logo_burn",
                    mux_stage_two_args(
                        synced_path,
                        input_paths["in_3"],
                        output_path,
                        logo_height_ratio=request_payload.get("logo_height_ratio", 0.2),
                        logo_margin_ratio=request_payload.get("logo_margin_ratio", 0.03),
                    ),
                )

            output_urls = {}
            for label, path in output_paths.items():
                if not path.exists() or path.stat().st_size == 0:
                    raise RuntimeError(f"Expected output file was not created: {label}")
                output_urls[label] = {"storage_url": public_output_url(command_id, path)}

            payload = webhook_payload("SUCCESS", request_payload, output_urls)
            post_webhook(request_payload["webhook_url"], payload)
            set_job_status(command_id, status="COMPLETED", output_files=output_urls)
            duration = round(time.monotonic() - started, 3)
            log_event(
                "ffmpeg_job_completed",
                command_id=command_id,
                duration_seconds=duration,
                output_files=output_urls,
            )
            notify_job_success(command_id, request_payload, output_urls)
        except Exception as exc:
            message = str(exc) or "FFmpeg processing failed"
            duration = round(time.monotonic() - started, 3)
            log_event(
                "ffmpeg_job_failed",
                command_id=command_id,
                duration_seconds=duration,
                error=truncate(message, 4000),
            )
            payload = webhook_payload(
                "FAILED", request_payload, output_urls={}, error_message=message
            )
            try:
                post_webhook(request_payload["webhook_url"], payload)
            except Exception as webhook_exc:
                log_event(
                    "webhook_failure_post_failed",
                    command_id=command_id,
                    error=truncate(webhook_exc, 2000),
                )
            set_job_status(command_id, status="FAILED", error_message=message)
            notify_job_failure(command_id, request_payload, message)


def run_analyze_mix_job(command_id, request_payload):
    set_job_status(command_id, status="RUNNING")
    started = time.monotonic()
    log_event(
        "mix_job_started",
        command_id=command_id,
        backing_url=request_payload["backing_url"],
        vocal_url=request_payload["vocal_url"],
        reference_vocal_url=request_payload.get("reference_vocal_url", ""),
        output_files=request_payload["output_files"],
        webhook_metadata=request_payload["webhook_metadata"],
    )
    output_dir = OUTPUT_ROOT / command_id
    output_dir.mkdir(parents=True, exist_ok=True)

    analysis = None
    decisions = None
    debug_output_urls = {}
    debug_artifacts = {}
    debug_error = ""
    with tempfile.TemporaryDirectory(prefix=f"{command_id}-") as work_dir_name:
        work_dir = Path(work_dir_name)
        try:
            cleanup_old_outputs()
            backing_path = work_dir / f"backing{extension_from_url(request_payload['backing_url'])}"
            vocal_path = work_dir / f"vocal{extension_from_url(request_payload['vocal_url'])}"
            reference_vocal_path = None
            if request_payload.get("reference_vocal_url"):
                reference_vocal_path = work_dir / (
                    f"reference_vocal{extension_from_url(request_payload['reference_vocal_url'])}"
                )

            log_event("mix_input_download_started", command_id=command_id, label="backing")
            download_file(request_payload["backing_url"], backing_path)
            log_event(
                "mix_input_downloaded",
                command_id=command_id,
                label="backing",
                bytes=backing_path.stat().st_size,
            )
            log_event("mix_input_download_started", command_id=command_id, label="vocal")
            download_file(request_payload["vocal_url"], vocal_path)
            log_event(
                "mix_input_downloaded",
                command_id=command_id,
                label="vocal",
                bytes=vocal_path.stat().st_size,
            )
            if reference_vocal_path is not None:
                log_event(
                    "mix_input_download_started",
                    command_id=command_id,
                    label="reference_vocal",
                )
                download_file(request_payload["reference_vocal_url"], reference_vocal_path)
                log_event(
                    "mix_input_downloaded",
                    command_id=command_id,
                    label="reference_vocal",
                    bytes=reference_vocal_path.stat().st_size,
                )

            analysis = analyze_audio_pair(backing_path, vocal_path)
            if reference_vocal_path is not None:
                timing_alignment = estimate_vocal_timing(
                    reference_vocal_path,
                    vocal_path,
                    analysis["vocal"]["channel_detection"]["needs_centering"],
                    request_payload.get("mix_options", {}),
                )
                analysis["timing_alignment"] = timing_alignment
                log_event(
                    "mix_timing_alignment_completed",
                    command_id=command_id,
                    detected_vocal_delay_ms=timing_alignment.get("detected_vocal_delay_ms"),
                    applied_vocal_shift_ms=timing_alignment.get("applied_vocal_shift_ms"),
                    confidence=timing_alignment.get("confidence"),
                    method=timing_alignment.get("method"),
                    best_score=timing_alignment.get("best_score"),
                    comparison_score=timing_alignment.get("comparison_score"),
                    adjacent_score=timing_alignment.get("adjacent_score"),
                    applied=timing_alignment.get("applied"),
                    reason=timing_alignment.get("reason"),
                )
            else:
                analysis["timing_alignment"] = {
                    "enabled": False,
                    "applied": False,
                    "detected_vocal_delay_ms": 0,
                    "applied_vocal_shift_ms": 0,
                    "confidence": 0,
                    "reason": "reference_vocal_url not provided",
                }
            decisions = choose_mix_decisions(analysis, request_payload.get("mix_options", {}))
            decisions = apply_vocal_second_pass_leveling(
                command_id,
                backing_path,
                vocal_path,
                work_dir,
                decisions,
            )
            log_event(
                "mix_analysis_completed",
                command_id=command_id,
                backing_lufs=analysis["backing"]["loudnorm"]["input_i"],
                vocal_lufs=analysis["vocal"]["loudnorm"]["input_i"],
                vocal_channel_mode=decisions["vocal_channel_mode"],
                vocal_needs_centering=decisions["vocal_needs_centering"],
                timing_alignment_applied=decisions["timing_alignment_applied"],
                detected_vocal_delay_ms=decisions["detected_vocal_delay_ms"],
                applied_vocal_shift_ms=decisions["applied_vocal_shift_ms"],
                vocal_second_pass_enabled=decisions["vocal_second_pass_enabled"],
                vocal_second_pass_target_over_backing_db=decisions[
                    "vocal_second_pass_target_over_backing_db"
                ],
                vocal_second_pass_gain_db=decisions["vocal_second_pass_gain_db"],
                vocal_second_pass_backing_active_rms_p75_db=decisions[
                    "vocal_second_pass_backing_active_rms_p75_db"
                ],
                vocal_second_pass_vocal_active_rms_p75_db=decisions[
                    "vocal_second_pass_vocal_active_rms_p75_db"
                ],
                vocal_polish_enabled=decisions["vocal_polish_enabled"],
                vocal_highpass_hz=decisions["vocal_highpass_hz"],
                vocal_deesser_intensity=decisions["vocal_deesser_intensity"],
                vocal_plate_reverb_enabled=decisions["vocal_plate_reverb_enabled"],
                vocal_short_delay_enabled=decisions["vocal_short_delay_enabled"],
                applied_vocal_shift_direction=describe_vocal_shift(
                    decisions["applied_vocal_shift_ms"]
                ),
            )

            try:
                debug_artifacts = create_stage_debug_artifacts(
                    command_id,
                    backing_path,
                    vocal_path,
                    output_dir,
                    work_dir,
                    decisions,
                )
            except Exception as debug_exc:
                debug_error = str(debug_exc) or "Stage debug artifact render failed"
                debug_artifacts = {
                    "enabled": decisions.get("debug_stage_artifacts_enabled", True),
                    "kind": "vocal_stage_feedback",
                    "error": debug_error,
                    "stages": [],
                }
                set_job_status(command_id, debug_artifacts=debug_artifacts, debug_error=debug_error)
                log_event(
                    "mix_stage_debug_failed",
                    command_id=command_id,
                    error=truncate(debug_error, 4000),
                )

            output_path = output_dir / request_payload["output_files"]["out_1"]
            filter_complex = build_mix_filter(decisions)
            args = [
                "ffmpeg",
                "-y",
                "-i",
                str(backing_path),
                "-i",
                str(vocal_path),
                "-filter_complex",
                filter_complex,
                "-map",
                "[out]",
                *encode_args_for_output(output_path),
                str(output_path),
            ]
            log_event(
                "mix_command_started",
                command_id=command_id,
                args=[truncate(arg, 800) for arg in args],
            )
            completed = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=PROCESSING_TIMEOUT_SECONDS,
                check=False,
            )
            if completed.returncode != 0:
                stderr = (completed.stderr or "").strip()
                log_event(
                    "mix_command_failed",
                    command_id=command_id,
                    returncode=completed.returncode,
                    stderr=truncate(stderr, 4000),
                )
                raise RuntimeError(stderr[-2000:] or "FFmpeg mix exited with an error")

            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError("Expected mixed output file was not created")

            output_urls = {
                "out_1": {
                    "storage_url": public_output_url(command_id, output_path)
                }
            }
            if reference_vocal_path is not None:
                try:
                    debug_output_urls = create_reference_debug_mix(
                        command_id,
                        backing_path,
                        reference_vocal_path,
                        output_dir,
                    )
                    if debug_artifacts:
                        debug_artifacts["reference"] = debug_output_urls
                    else:
                        debug_artifacts = {"reference": debug_output_urls}
                except Exception as debug_exc:
                    reference_debug_error = str(debug_exc) or "Reference debug mix failed"
                    debug_error = (
                        f"{debug_error}; {reference_debug_error}"
                        if debug_error
                        else reference_debug_error
                    )
                    log_event(
                        "reference_debug_mix_failed",
                        command_id=command_id,
                        error=truncate(reference_debug_error, 4000),
                    )

            webhook_result = None
            if request_payload.get("webhook_url"):
                webhook_result = post_webhook_safely(
                    request_payload["webhook_url"],
                    mix_webhook_payload(
                        "COMPLETED",
                        request_payload,
                        output_urls=output_urls,
                        analysis=analysis,
                        decisions=decisions,
                        debug_artifacts=debug_artifacts,
                        debug_error=debug_error,
                    ),
                    event_prefix="mix_webhook",
                )
                request_payload["_webhook_result"] = webhook_result

            duration = round(time.monotonic() - started, 3)
            set_job_status(
                command_id,
                status="COMPLETED",
                output_files=output_urls,
                analysis=analysis,
                mix_decisions=decisions,
                debug_artifacts=debug_artifacts,
                debug_error=debug_error,
                webhook_result=webhook_result,
            )
            log_event(
                "mix_job_completed",
                command_id=command_id,
                duration_seconds=duration,
                output_files=output_urls,
                debug_output_files=debug_output_urls,
                debug_artifacts=debug_artifacts,
                debug_error=debug_error,
                webhook_result=webhook_result,
            )
            notify_mix_success(
                command_id,
                request_payload,
                output_urls,
                analysis,
                decisions,
                debug_artifacts,
                debug_error,
            )
        except Exception as exc:
            message = str(exc) or "Analyze and mix failed"
            duration = round(time.monotonic() - started, 3)
            log_event(
                "mix_job_failed",
                command_id=command_id,
                duration_seconds=duration,
                error=truncate(message, 4000),
            )
            if request_payload.get("webhook_url"):
                post_webhook_safely(
                    request_payload["webhook_url"],
                    mix_webhook_payload(
                        "FAILED",
                        request_payload,
                        output_urls={},
                        analysis=analysis,
                        decisions=decisions,
                        debug_artifacts=debug_artifacts,
                        debug_error=debug_error,
                        error_message=message,
                    ),
                    event_prefix="mix_webhook_failure",
                )
            set_job_status(
                command_id,
                status="FAILED",
                error_message=message,
                debug_artifacts=debug_artifacts,
                debug_error=debug_error,
            )
            notify_mix_failure(command_id, request_payload, message)


def enqueue_ffmpeg_job(request_payload):
    cleanup_old_outputs()
    command_id = f"job-{uuid.uuid4().hex}"
    with jobs_lock:
        jobs[command_id] = {"status": "QUEUED", "created_at": time.time()}
    log_event(
        "ffmpeg_job_queued",
        command_id=command_id,
        input_files=request_payload["input_files"],
        output_files=request_payload["output_files"],
        webhook_metadata=request_payload["webhook_metadata"],
    )
    if request_payload.get("job_type") == "mux-video":
        job_executor.submit(run_mux_video_job, command_id, request_payload)
    else:
        job_executor.submit(run_ffmpeg_job, command_id, request_payload)
    return command_id


def enqueue_mix_job(request_payload):
    cleanup_old_outputs()
    command_id = f"mix-{uuid.uuid4().hex}"
    with jobs_lock:
        jobs[command_id] = {"status": "QUEUED", "created_at": time.time()}
    log_event(
        "mix_job_queued",
        command_id=command_id,
        backing_url=request_payload["backing_url"],
        vocal_url=request_payload["vocal_url"],
        reference_vocal_url=request_payload.get("reference_vocal_url", ""),
        output_files=request_payload["output_files"],
        webhook_metadata=request_payload["webhook_metadata"],
    )
    job_executor.submit(run_analyze_mix_job, command_id, request_payload)
    return command_id


MASTERING_STYLES = ("warm", "balanced", "open")
MASTERING_LOUDNESS_LEVELS = ("conservative", "standard", "loud")
MASTERING_ANALYSIS_SECONDS = int(os.environ.get("MASTERING_ANALYSIS_SECONDS", "120"))
MASTERING_DEFAULT_PREVIEW_SECONDS = int(
    os.environ.get("MASTERING_DEFAULT_PREVIEW_SECONDS", "75")
)
MASTERING_PREFERENCES_FILE = DATA_DIR / "mastering-preferences.jsonl"
MASTERING_AI_PROVIDER = os.environ.get("MASTERING_AI_PROVIDER", "auto").strip().lower()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_API_URL = os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1/responses")
MASTERING_OPENAI_MODEL = os.environ.get("MASTERING_OPENAI_MODEL", "gpt-5.5")
MASTERING_OPENAI_TIMEOUT_SECONDS = int(
    os.environ.get("MASTERING_OPENAI_TIMEOUT_SECONDS", "60")
)
MASTERING_UPLOAD_MAX_BYTES = int(os.environ.get("MASTERING_UPLOAD_MAX_BYTES", "524288000"))
MASTERING_EQ_MAX_GAIN_DB = float(os.environ.get("MASTERING_EQ_MAX_GAIN_DB", "4.5"))
MASTERING_STEREO_GOAL_RE = re.compile(
    r"\b(mono\s*(to|2)\s*stereo|make\s+.*stereo|stereo\s+.*mono|wider|widen|stereoize)\b",
    re.IGNORECASE,
)
MASTERING_CLEANUP_GOAL_RE = re.compile(
    r"\b(noise|noisy|fuzz|fuzzy|fan|hiss|hum|buzz|clean\s*up|cleanup|quiet\s+except|voice|vocal)\b",
    re.IGNORECASE,
)
MASTERING_BANDS = (
    ("sub_20_60", 20, 60),
    ("bass_60_120", 60, 120),
    ("low_mid_120_300", 120, 300),
    ("mid_300_1000", 300, 1000),
    ("presence_1000_4000", 1000, 4000),
    ("harsh_4000_8000", 4000, 8000),
    ("air_8000_16000", 8000, 16000),
)
MASTERING_BAND_CENTERS = {
    "bass_60_120": 90,
    "low_mid_120_300": 220,
    "mid_300_1000": 650,
    "presence_1000_4000": 2800,
    "harsh_4000_8000": 6200,
    "air_8000_16000": 10500,
}
MASTERING_TARGET_PROFILES = {
    "modern_pop_streaming": {
        "profile": "modern_pop_streaming",
        "target_lufs": -10.5,
        "target_true_peak": -1.0,
        "target_lra": 5.5,
        "min_lra": 4.0,
        "spectral_curve": {
            "sub_20_60": -17,
            "bass_60_120": -13,
            "low_mid_120_300": -15,
            "mid_300_1000": -16,
            "presence_1000_4000": -14,
            "harsh_4000_8000": -16,
            "air_8000_16000": -18,
        },
        "stereo": {
            "target_width": 0.78,
            "max_low_end_width": 0.18,
            "min_mono_correlation": 0.25,
        },
    },
    "hiphop_loud": {
        "profile": "hiphop_loud",
        "target_lufs": -9.5,
        "target_true_peak": -1.0,
        "target_lra": 4.8,
        "min_lra": 3.5,
        "spectral_curve": {
            "sub_20_60": -13,
            "bass_60_120": -12,
            "low_mid_120_300": -15,
            "mid_300_1000": -17,
            "presence_1000_4000": -15,
            "harsh_4000_8000": -17,
            "air_8000_16000": -19,
        },
        "stereo": {
            "target_width": 0.72,
            "max_low_end_width": 0.16,
            "min_mono_correlation": 0.25,
        },
    },
    "acoustic_natural": {
        "profile": "acoustic_natural",
        "target_lufs": -14.0,
        "target_true_peak": -1.2,
        "target_lra": 9.0,
        "min_lra": 7.0,
        "spectral_curve": {
            "sub_20_60": -22,
            "bass_60_120": -17,
            "low_mid_120_300": -15,
            "mid_300_1000": -15,
            "presence_1000_4000": -15,
            "harsh_4000_8000": -18,
            "air_8000_16000": -18,
        },
        "stereo": {
            "target_width": 0.62,
            "max_low_end_width": 0.20,
            "min_mono_correlation": 0.30,
        },
    },
    "rock_punchy": {
        "profile": "rock_punchy",
        "target_lufs": -10.8,
        "target_true_peak": -1.0,
        "target_lra": 6.2,
        "min_lra": 4.5,
        "spectral_curve": {
            "sub_20_60": -19,
            "bass_60_120": -14,
            "low_mid_120_300": -14,
            "mid_300_1000": -14,
            "presence_1000_4000": -13,
            "harsh_4000_8000": -16,
            "air_8000_16000": -19,
        },
        "stereo": {
            "target_width": 0.70,
            "max_low_end_width": 0.18,
            "min_mono_correlation": 0.25,
        },
    },
    "edm_loud_clean": {
        "profile": "edm_loud_clean",
        "target_lufs": -8.8,
        "target_true_peak": -1.0,
        "target_lra": 4.2,
        "min_lra": 3.0,
        "spectral_curve": {
            "sub_20_60": -12,
            "bass_60_120": -12,
            "low_mid_120_300": -16,
            "mid_300_1000": -17,
            "presence_1000_4000": -15,
            "harsh_4000_8000": -16,
            "air_8000_16000": -17,
        },
        "stereo": {
            "target_width": 0.82,
            "max_low_end_width": 0.14,
            "min_mono_correlation": 0.22,
        },
    },
    "country_radio": {
        "profile": "country_radio",
        "target_lufs": -11.5,
        "target_true_peak": -1.0,
        "target_lra": 6.0,
        "min_lra": 4.5,
        "spectral_curve": {
            "sub_20_60": -20,
            "bass_60_120": -15,
            "low_mid_120_300": -15,
            "mid_300_1000": -15,
            "presence_1000_4000": -13,
            "harsh_4000_8000": -16,
            "air_8000_16000": -18,
        },
        "stereo": {
            "target_width": 0.68,
            "max_low_end_width": 0.18,
            "min_mono_correlation": 0.28,
        },
    },
    "podcast_voice": {
        "profile": "podcast_voice",
        "target_lufs": -16.0,
        "target_true_peak": -1.5,
        "target_lra": 5.0,
        "min_lra": 3.0,
        "spectral_curve": {
            "sub_20_60": -30,
            "bass_60_120": -22,
            "low_mid_120_300": -17,
            "mid_300_1000": -14,
            "presence_1000_4000": -12,
            "harsh_4000_8000": -18,
            "air_8000_16000": -22,
        },
        "stereo": {
            "target_width": 0.18,
            "max_low_end_width": 0.05,
            "min_mono_correlation": 0.70,
        },
    },
    "warm_vintage": {
        "profile": "warm_vintage",
        "target_lufs": -12.5,
        "target_true_peak": -1.2,
        "target_lra": 7.0,
        "min_lra": 5.0,
        "spectral_curve": {
            "sub_20_60": -20,
            "bass_60_120": -14,
            "low_mid_120_300": -14,
            "mid_300_1000": -15,
            "presence_1000_4000": -16,
            "harsh_4000_8000": -19,
            "air_8000_16000": -22,
        },
        "stereo": {
            "target_width": 0.58,
            "max_low_end_width": 0.16,
            "min_mono_correlation": 0.32,
        },
    },
    "open_bright": {
        "profile": "open_bright",
        "target_lufs": -11.8,
        "target_true_peak": -1.0,
        "target_lra": 6.0,
        "min_lra": 4.5,
        "spectral_curve": {
            "sub_20_60": -21,
            "bass_60_120": -15,
            "low_mid_120_300": -16,
            "mid_300_1000": -16,
            "presence_1000_4000": -13,
            "harsh_4000_8000": -16,
            "air_8000_16000": -15,
        },
        "stereo": {
            "target_width": 0.78,
            "max_low_end_width": 0.18,
            "min_mono_correlation": 0.25,
        },
    },
}

MASTERING_PLANNER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "selected_profile",
        "classification",
        "mastering_plan",
        "candidate_strategy",
    ],
    "properties": {
        "selected_profile": {
            "type": "string",
            "enum": list(MASTERING_TARGET_PROFILES.keys()),
        },
        "classification": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "genre_guess",
                "vocal_prominence",
                "mix_quality",
                "mastering_goal",
                "mix_problem_tags",
                "already_mastered",
            ],
            "properties": {
                "genre_guess": {"type": "string"},
                "vocal_prominence": {
                    "type": "string",
                    "enum": ["low", "balanced", "forward", "instrumental", "unknown"],
                },
                "mix_quality": {"type": "string"},
                "mastering_goal": {"type": "string"},
                "mix_problem_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 12,
                },
                "already_mastered": {"type": "boolean"},
            },
        },
        "mastering_plan": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "input_gain_db",
                "target_lufs",
                "target_true_peak",
                "target_lra",
                "eq",
                "compression",
                "stereo",
                "saturation",
                "limiter",
                "rationale",
            ],
            "properties": {
                "input_gain_db": {"type": "number"},
                "target_lufs": {"type": "number"},
                "target_true_peak": {"type": "number"},
                "target_lra": {"type": "number"},
                "eq": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "type",
                            "frequency",
                            "q",
                            "gain_db",
                            "slope",
                            "reason",
                        ],
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["highpass", "bell", "shelf"],
                            },
                            "frequency": {"type": "number"},
                            "q": {"type": "number"},
                            "gain_db": {"type": "number"},
                            "slope": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "compression": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "type",
                        "threshold_db",
                        "ratio",
                        "attack_ms",
                        "release_ms",
                        "reason",
                    ],
                    "properties": {
                        "type": {"type": "string", "enum": ["broadband"]},
                        "threshold_db": {"type": "number"},
                        "ratio": {"type": "number"},
                        "attack_ms": {"type": "number"},
                        "release_ms": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                },
                "stereo": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "widen_amount",
                        "mono_bass_below_hz",
                        "reason",
                    ],
                    "properties": {
                        "widen_amount": {"type": "number"},
                        "mono_bass_below_hz": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                },
                "saturation": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["amount", "type", "reason"],
                    "properties": {
                        "amount": {"type": "number"},
                        "type": {"type": "string", "enum": ["soft"]},
                        "reason": {"type": "string"},
                    },
                },
                "limiter": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["ceiling_db", "aggressiveness", "reason"],
                    "properties": {
                        "ceiling_db": {"type": "number"},
                        "aggressiveness": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                        "reason": {"type": "string"},
                    },
                },
                "rationale": {"type": "string"},
            },
        },
        "candidate_strategy": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "preferred_styles",
                "preferred_loudness_levels",
                "notes",
            ],
            "properties": {
                "preferred_styles": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(MASTERING_STYLES)},
                    "maxItems": 3,
                },
                "preferred_loudness_levels": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": list(MASTERING_LOUDNESS_LEVELS),
                    },
                    "maxItems": 3,
                },
                "notes": {"type": "string"},
            },
        },
    },
}


def db_to_linear(db):
    return 10 ** (float(db) / 20)


def pcm_samples_from_file(path, audio_filter=None, channels=1, sample_rate=16000, duration=None):
    filters = []
    if audio_filter:
        filters.append(audio_filter)
    filters.append(f"aresample={sample_rate}")
    args = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-i",
        str(path),
    ]
    if duration:
        args.extend(["-t", str(duration)])
    args.extend(
        [
            "-af",
            ",".join(filters),
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-",
        ]
    )
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=PROCESSING_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"PCM analysis failed: {stderr[-2000:]}")
    sample_count = len(completed.stdout) // 2
    if sample_count <= 0:
        return ()
    return struct.unpack(f"<{sample_count}h", completed.stdout)


def rms_db_from_samples(samples):
    if not samples:
        return -120.0
    total = sum(sample * sample for sample in samples)
    rms = math.sqrt(total / len(samples))
    if rms <= 0:
        return -120.0
    return round(20 * math.log10(rms / 32768), 3)


def peak_db_from_samples(samples):
    if not samples:
        return -120.0
    peak = max(abs(sample) for sample in samples)
    if peak <= 0:
        return -120.0
    return round(20 * math.log10(peak / 32768), 3)


def band_filter(low_hz, high_hz):
    if low_hz <= 20:
        return f"lowpass=f={high_hz}"
    if high_hz >= 16000:
        return f"highpass=f={low_hz}"
    return f"highpass=f={low_hz},lowpass=f={high_hz}"


def spectral_balance_file(path):
    spectrum = {}
    for name, low_hz, high_hz in MASTERING_BANDS:
        samples = pcm_samples_from_file(
            path,
            audio_filter=band_filter(low_hz, high_hz),
            channels=1,
            sample_rate=16000,
            duration=MASTERING_ANALYSIS_SECONDS,
        )
        spectrum[name] = rms_db_from_samples(samples)
    return spectrum


def stereo_metrics_file(path):
    samples = pcm_samples_from_file(
        path,
        channels=2,
        sample_rate=16000,
        duration=MASTERING_ANALYSIS_SECONDS,
    )
    if len(samples) < 4:
        return {
            "width_score": 0.0,
            "mono_correlation": 1.0,
            "low_end_width": 0.0,
            "channel_balance_db": 0.0,
            "left_rms_db": -120.0,
            "right_rms_db": -120.0,
            "mid_rms_db": -120.0,
            "side_rms_db": -120.0,
            "effective_mono": True,
            "stereo_assessment": "too_short_to_measure",
        }

    left = samples[0::2]
    right = samples[1::2]
    pair_count = min(len(left), len(right))
    left = left[:pair_count]
    right = right[:pair_count]
    left_energy = sum(value * value for value in left)
    right_energy = sum(value * value for value in right)
    cross = sum(l_value * r_value for l_value, r_value in zip(left, right))
    denominator = math.sqrt(left_energy * right_energy)
    correlation = cross / denominator if denominator > 0 else 1.0
    mid = [(l_value + r_value) / 2 for l_value, r_value in zip(left, right)]
    side = [(l_value - r_value) / 2 for l_value, r_value in zip(left, right)]
    left_rms = math.sqrt(left_energy / len(left)) if left else 0
    right_rms = math.sqrt(right_energy / len(right)) if right else 0
    mid_rms = math.sqrt(sum(value * value for value in mid) / len(mid)) if mid else 0
    side_rms = math.sqrt(sum(value * value for value in side) / len(side)) if side else 0
    width_score = clamp((side_rms / mid_rms) * 1.4 if mid_rms > 0 else 0, 0, 1)
    quieter_channel = min(left_rms, right_rms)
    louder_channel = max(left_rms, right_rms)
    one_sided = bool(louder_channel > 0 and quieter_channel / louder_channel < 0.08)
    channel_balance_db = 0.0
    if left_rms > 0 and right_rms > 0:
        channel_balance_db = 20 * math.log10(left_rms / right_rms)
    elif left_rms > 0:
        channel_balance_db = 60.0
    elif right_rms > 0:
        channel_balance_db = -60.0
    dual_mono = bool(abs(correlation) >= 0.98 and width_score <= 0.08 and not one_sided)
    effective_mono = bool(dual_mono or one_sided)
    if one_sided:
        stereo_assessment = "one_sided_mono"
    elif dual_mono:
        stereo_assessment = "dual_mono"
    elif correlation < 0.2:
        stereo_assessment = "phase_risk"
    elif width_score >= 0.85 and correlation >= 0.95:
        stereo_assessment = "imbalanced_wide"
    else:
        stereo_assessment = "stereo"

    low_samples = pcm_samples_from_file(
        path,
        audio_filter="lowpass=f=120",
        channels=2,
        sample_rate=8000,
        duration=MASTERING_ANALYSIS_SECONDS,
    )
    low_width = 0.0
    if len(low_samples) >= 4:
        low_left = low_samples[0::2]
        low_right = low_samples[1::2]
        low_mid = [(l_value + r_value) / 2 for l_value, r_value in zip(low_left, low_right)]
        low_side = [(l_value - r_value) / 2 for l_value, r_value in zip(low_left, low_right)]
        low_mid_rms = (
            math.sqrt(sum(value * value for value in low_mid) / len(low_mid)) if low_mid else 0
        )
        low_side_rms = (
            math.sqrt(sum(value * value for value in low_side) / len(low_side))
            if low_side
            else 0
        )
        low_width = clamp((low_side_rms / low_mid_rms) * 1.4 if low_mid_rms > 0 else 0, 0, 1)

    return {
        "width_score": round(width_score, 3),
        "mono_correlation": round(correlation, 3),
        "low_end_width": round(low_width, 3),
        "channel_balance_db": round(channel_balance_db, 3),
        "left_rms_db": rms_db_from_samples(left),
        "right_rms_db": rms_db_from_samples(right),
        "mid_rms_db": round(20 * math.log10(mid_rms / 32768), 3) if mid_rms > 0 else -120.0,
        "side_rms_db": round(20 * math.log10(side_rms / 32768), 3) if side_rms > 0 else -120.0,
        "effective_mono": effective_mono,
        "stereo_assessment": stereo_assessment,
    }


def dynamic_metrics_file(path, loudness):
    samples = pcm_samples_from_file(
        path,
        channels=1,
        sample_rate=16000,
        duration=MASTERING_ANALYSIS_SECONDS,
    )
    peak_db = peak_db_from_samples(samples)
    rms_db = rms_db_from_samples(samples)
    crest_factor = round(peak_db - rms_db, 3)
    clipping_detected = any(abs(sample) >= 32760 for sample in samples)
    frame_size = 1600
    frame_levels = []
    for index in range(0, len(samples), frame_size):
        frame = samples[index : index + frame_size]
        if len(frame) >= frame_size // 2:
            frame_levels.append(samples_rms_db(frame))
    changes = [
        abs(frame_levels[index] - frame_levels[index - 1])
        for index in range(1, len(frame_levels))
        if frame_levels[index] is not None and frame_levels[index - 1] is not None
    ]
    transient_density = 0.0
    if changes:
        transient_density = sum(1 for change in changes if change >= 4.0) / len(changes)

    lra = loudness.get("lra")
    if crest_factor <= 8 or (lra is not None and lra <= 4):
        compression_guess = "over_compressed"
    elif crest_factor >= 14 or (lra is not None and lra >= 9):
        compression_guess = "under_compressed"
    else:
        compression_guess = "moderate"

    return {
        "transient_density": round(transient_density, 3),
        "compression_guess": compression_guess,
        "clipping_detected": bool(clipping_detected),
        "sample_peak_db": peak_db,
        "sample_rms_db": rms_db,
    }, crest_factor


def noise_metrics_file(path):
    leading_samples = pcm_samples_from_file(
        path,
        channels=1,
        sample_rate=16000,
        duration=4,
    )
    if not leading_samples:
        return {
            "leading_rms_db": -120.0,
            "leading_peak_db": -120.0,
            "cleanup_recommended": False,
        }
    leading_rms = rms_db_from_samples(leading_samples)
    leading_peak = peak_db_from_samples(leading_samples)
    cleanup_recommended = bool(-62 <= leading_rms <= -22)
    return {
        "leading_rms_db": leading_rms,
        "leading_peak_db": leading_peak,
        "cleanup_recommended": cleanup_recommended,
    }


def classify_mastering_analysis(loudness, spectrum, stereo, dynamics):
    tags = []
    genre_guess = "pop"
    integrated_lufs = loudness.get("integrated_lufs")
    lra = loudness.get("lra")

    low_mid = spectrum.get("low_mid_120_300", -120)
    presence = spectrum.get("presence_1000_4000", -120)
    harsh = spectrum.get("harsh_4000_8000", -120)
    air = spectrum.get("air_8000_16000", -120)
    bass = spectrum.get("bass_60_120", -120)

    if bass - presence > 5:
        genre_guess = "hiphop"
    if lra is not None and lra >= 9 and integrated_lufs is not None and integrated_lufs <= -15:
        genre_guess = "acoustic"
    if harsh - presence > 3:
        tags.append("harsh")
    if low_mid - presence > 3:
        tags.append("low_mid_muddy")
    if air < presence - 5:
        tags.append("dark")
    if presence < low_mid - 2:
        tags.append("vocal_buried")
        vocal_prominence = "low"
    elif presence > low_mid + 4:
        vocal_prominence = "forward"
    else:
        vocal_prominence = "balanced"
    if stereo.get("stereo_assessment") in {"dual_mono", "one_sided_mono"} or stereo.get(
        "effective_mono"
    ):
        tags.append(stereo.get("stereo_assessment") or "mono_source")
    if stereo.get("mono_correlation", 1) < 0.2:
        tags.append("phase_risk")
    if stereo.get("low_end_width", 0) > 0.35:
        tags.append("wide_low_end")
    if dynamics.get("clipping_detected"):
        tags.append("clipping_risk")

    if dynamics.get("compression_guess") == "over_compressed":
        mix_quality = "already_loud_or_crushed"
    elif "dark" in tags and "low_mid_muddy" in tags:
        mix_quality = "decent_but_dark_muddy"
    elif tags:
        mix_quality = "needs_targeted_mastering"
    else:
        mix_quality = "balanced"

    return {
        "genre_guess": genre_guess,
        "vocal_prominence": vocal_prominence,
        "mix_quality": mix_quality,
        "mastering_goal": "streaming_loud_modern",
        "mix_problem_tags": tags,
        "already_mastered": bool(
            integrated_lufs is not None
            and integrated_lufs > -11
            and lra is not None
            and lra < 5
        ),
    }


def analyze_mastering_file(path):
    loudnorm = loudnorm_file(path)
    loudness = {
        "integrated_lufs": loudnorm.get("input_i"),
        "short_term_lufs_max": None,
        "lra": loudnorm.get("input_lra"),
        "true_peak_db": loudnorm.get("input_tp"),
        "crest_factor": None,
    }
    spectrum = spectral_balance_file(path)
    stereo = stereo_metrics_file(path)
    dynamics, crest_factor = dynamic_metrics_file(path, loudness)
    noise = noise_metrics_file(path)
    dynamics.update(noise)
    loudness["crest_factor"] = crest_factor
    classification = classify_mastering_analysis(loudness, spectrum, stereo, dynamics)
    return {
        "loudness": loudness,
        "spectrum": spectrum,
        "stereo": stereo,
        "dynamics": dynamics,
        "classification": classification,
    }


def compact_mastering_analysis(analysis):
    if not isinstance(analysis, dict):
        return {}
    loudness = analysis.get("loudness", {})
    stereo = analysis.get("stereo", {})
    dynamics = analysis.get("dynamics", {})
    classification = analysis.get("classification", {})
    return {
        "integrated_lufs": loudness.get("integrated_lufs"),
        "true_peak_db": loudness.get("true_peak_db"),
        "lra": loudness.get("lra"),
        "crest_factor": loudness.get("crest_factor"),
        "width_score": stereo.get("width_score"),
        "mono_correlation": stereo.get("mono_correlation"),
        "low_end_width": stereo.get("low_end_width"),
        "channel_balance_db": stereo.get("channel_balance_db"),
        "effective_mono": stereo.get("effective_mono"),
        "stereo_assessment": stereo.get("stereo_assessment"),
        "compression_guess": dynamics.get("compression_guess"),
        "clipping_detected": dynamics.get("clipping_detected"),
        "leading_rms_db": dynamics.get("leading_rms_db"),
        "leading_peak_db": dynamics.get("leading_peak_db"),
        "cleanup_recommended": dynamics.get("cleanup_recommended"),
        "genre_guess": classification.get("genre_guess"),
        "mix_quality": classification.get("mix_quality"),
        "mix_problem_tags": classification.get("mix_problem_tags", []),
    }


def compact_mastering_plan(plan):
    if not isinstance(plan, dict):
        return {}
    stereo = plan.get("stereo", {})
    compression = plan.get("compression", {})
    limiter = plan.get("limiter", {})
    return {
        "candidate_id": plan.get("candidate_id"),
        "style": plan.get("style"),
        "loudness": plan.get("loudness"),
        "input_gain_db": plan.get("input_gain_db"),
        "output_gain_db": plan.get("output_gain_db", 0),
        "target_lufs": plan.get("target_lufs"),
        "target_true_peak": plan.get("target_true_peak"),
        "target_lra": plan.get("target_lra"),
        "eq": [
            {
                "type": move.get("type"),
                "frequency": move.get("frequency"),
                "q": move.get("q"),
                "gain_db": move.get("gain_db"),
                "reason": move.get("reason", ""),
            }
            for move in plan.get("eq", [])
            if isinstance(move, dict)
        ],
        "compression": {
            "threshold_db": compression.get("threshold_db"),
            "ratio": compression.get("ratio"),
            "attack_ms": compression.get("attack_ms"),
            "release_ms": compression.get("release_ms"),
        },
        "stereo": {
            "widen_amount": stereo.get("widen_amount"),
            "mono_to_stereo": stereo.get("mono_to_stereo", False),
            "mono_to_stereo_strength": stereo.get("mono_to_stereo_strength", 0),
            "mono_bass_below_hz": stereo.get("mono_bass_below_hz"),
        },
        "cleanup": plan.get("cleanup", {}),
        "ambience": plan.get("ambience", {}),
        "adjustments": plan.get("adjustments", {}),
        "saturation_amount": plan.get("saturation", {}).get("amount"),
        "limiter_ceiling_db": limiter.get("ceiling_db"),
        "limiter_aggressiveness": limiter.get("aggressiveness"),
    }


def request_wants_stereo_image(request_payload):
    user_goal = request_payload.get("user_goal", "")
    return isinstance(user_goal, str) and bool(MASTERING_STEREO_GOAL_RE.search(user_goal))


def request_wants_cleanup(request_payload):
    user_goal = request_payload.get("user_goal", "")
    return isinstance(user_goal, str) and bool(MASTERING_CLEANUP_GOAL_RE.search(user_goal))


def apply_mastering_render_intent(plan, source_analysis, target_profile, request_payload):
    plan = json.loads(json.dumps(plan))
    stereo = plan.setdefault("stereo", {})
    cleanup = plan.setdefault("cleanup", {})
    source_stereo = source_analysis.get("stereo", {})
    source_dynamics = source_analysis.get("dynamics", {})
    target_stereo = target_profile.get("stereo", {})
    wants_stereo = request_wants_stereo_image(request_payload)
    wants_cleanup = request_wants_cleanup(request_payload)
    source_effective_mono = bool(source_stereo.get("effective_mono"))
    target_width = float(target_stereo.get("target_width", 0.7))
    should_stereoize = bool(
        (wants_stereo or source_effective_mono)
        and target_width >= 0.35
        and target_profile.get("profile") != "podcast_voice"
    )
    if should_stereoize:
        style = plan.get("style", "balanced")
        base_strength = 0.16 if wants_stereo else 0.12
        if style == "open":
            base_strength += 0.02
        elif style == "warm":
            base_strength -= 0.02
        stereo["mono_to_stereo"] = True
        stereo["mono_to_stereo_strength"] = round(clamp(base_strength, 0.08, 0.18), 3)
        stereo["widen_amount"] = round(
            max(float(stereo.get("widen_amount", 0)), stereo["mono_to_stereo_strength"]),
            3,
        )
        stereo["reason"] = (
            "explicit stereo image requested"
            if wants_stereo
            else f"source assessed as {source_stereo.get('stereo_assessment', 'mono')}"
        )
    should_cleanup = bool(
        wants_cleanup
        or (
            source_stereo.get("stereo_assessment") in {"one_sided_mono", "dual_mono"}
            and source_dynamics.get("cleanup_recommended")
        )
    )
    if should_cleanup:
        style = plan.get("style", "balanced")
        loudness = plan.get("loudness", "standard")
        leading_rms = float(source_dynamics.get("leading_rms_db") or -48)
        gate_threshold = clamp(leading_rms + 7, -52, -34)
        noise_reduction = 14.0
        gate_range = 0.01
        if style == "open":
            noise_reduction += 4
            gate_range = 0.006
        elif style == "warm":
            noise_reduction -= 2
            gate_range = 0.018
        if loudness == "loud":
            noise_reduction += 2
        elif loudness == "conservative":
            noise_reduction -= 1
        cleanup.update(
            {
                "enabled": True,
                "noise_reduction_db": round(clamp(noise_reduction, 8, 24), 3),
                "noise_floor_db": round(clamp(leading_rms - 4, -70, -32), 3),
                "gate_threshold_db": round(gate_threshold, 3),
                "gate_range": round(clamp(gate_range, 0.003, 0.12), 3),
                "reason": (
                    "explicit noise/voice cleanup requested"
                    if wants_cleanup
                    else "one-sided mono source with measurable leading noise"
                ),
            }
        )
    return validate_mastering_plan(plan)


def copy_target_profile(profile_name):
    raw = MASTERING_TARGET_PROFILES.get(profile_name, MASTERING_TARGET_PROFILES["modern_pop_streaming"])
    return json.loads(json.dumps(raw))


def select_mastering_target_profile(analysis, requested_profile=""):
    if requested_profile:
        if requested_profile not in MASTERING_TARGET_PROFILES:
            raise ApiError(400, "Unknown mastering profile")
        return copy_target_profile(requested_profile)

    classification = analysis.get("classification", {})
    genre_guess = classification.get("genre_guess")
    if genre_guess == "hiphop":
        return copy_target_profile("hiphop_loud")
    if genre_guess == "acoustic":
        return copy_target_profile("acoustic_natural")
    return copy_target_profile("modern_pop_streaming")


def reference_target_profile(reference_analysis, base_profile):
    target = json.loads(json.dumps(base_profile))
    target["profile"] = f"reference_match_{base_profile['profile']}"
    loudness = reference_analysis.get("loudness", {})
    spectrum = reference_analysis.get("spectrum", {})
    stereo = reference_analysis.get("stereo", {})
    if loudness.get("integrated_lufs") is not None:
        target["target_lufs"] = clamp(loudness["integrated_lufs"], -16, -8)
    if loudness.get("true_peak_db") is not None:
        target["target_true_peak"] = min(-1.0, loudness["true_peak_db"])
    if loudness.get("lra") is not None:
        target["target_lra"] = clamp(loudness["lra"], 3, 12)
        target["min_lra"] = clamp(loudness["lra"] - 1.5, 3, 10)
    if spectrum:
        target["spectral_curve"] = {
            key: spectrum.get(key, value)
            for key, value in target.get("spectral_curve", {}).items()
        }
    if stereo:
        target["stereo"]["target_width"] = clamp(stereo.get("width_score", 0.7), 0.1, 0.85)
        target["stereo"]["min_mono_correlation"] = clamp(
            stereo.get("mono_correlation", 0.25) - 0.05, 0.2, 0.8
        )
    return target


def ai_provider_for_request(request_payload):
    requested = request_payload.get("planner", "auto")
    provider = requested if requested != "auto" else MASTERING_AI_PROVIDER
    if provider == "auto":
        return "openai" if OPENAI_API_KEY else "rule"
    return provider


def openai_response_text(response_data):
    if isinstance(response_data.get("output_text"), str):
        return response_data["output_text"]
    for item in response_data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and isinstance(
                content.get("text"), str
            ):
                return content["text"]
            if content.get("type") == "refusal":
                raise RuntimeError(f"OpenAI planner refused: {content.get('refusal', '')}")
    raise RuntimeError("OpenAI planner returned no text output")


def call_openai_mastering_planner(source_analysis, reference_analysis, target_profile, request_payload):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    user_goal = request_payload.get("user_goal", "")
    payload = {
        "model": MASTERING_OPENAI_MODEL,
        "reasoning": {"effort": "low"},
        "instructions": (
            "You are an AI mastering planner. Decide mastering moves from audio "
            "analysis and target data. Return only the requested structured JSON. "
            "Never write FFmpeg commands. Stay within mastering-safe ranges: "
            "input_gain_db -6..6, target_lufs -16..-8, true peak no higher than -1.0, "
            "EQ mostly -4.5..+4.5 dB, highpass 20..60 Hz, ratio 1.1..3.0, "
            "widen_amount 0..0.18. Prefer purposeful but still mastering-safe moves. "
            "If the user asks for mono-to-stereo or wider stereo, use the maximum safe "
            "widen_amount when mono compatibility remains acceptable."
        ),
        "input": [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "Create a constrained mastering plan for this track.",
                        "source_analysis": source_analysis,
                        "reference_analysis": reference_analysis,
                        "initial_target_profile": target_profile,
                        "available_profiles": list(MASTERING_TARGET_PROFILES.keys()),
                        "candidate_grid": {
                            "styles": list(MASTERING_STYLES),
                            "loudness_levels": list(MASTERING_LOUDNESS_LEVELS),
                        },
                        "requested_profile": request_payload.get("profile", ""),
                        "user_goal": user_goal,
                    },
                    sort_keys=True,
                ),
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "mastering_planner_response",
                "strict": True,
                "schema": MASTERING_PLANNER_SCHEMA,
            }
        },
        "max_output_tokens": 3500,
    }
    request = Request(
        OPENAI_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ffmpeg-sound-mixer-api/0.1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=MASTERING_OPENAI_TIMEOUT_SECONDS) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"OpenAI planner failed with HTTP {exc.code}: {truncate(body, 2000)}"
        ) from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"OpenAI planner request failed: {exc}") from exc

    if response_data.get("status") == "incomplete":
        reason = response_data.get("incomplete_details", {}).get("reason", "unknown")
        raise RuntimeError(f"OpenAI planner response incomplete: {reason}")

    output_text = openai_response_text(response_data)
    try:
        planner_response = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenAI planner returned invalid JSON") from exc
    return planner_response


def plan_mastering_with_ai(source_analysis, reference_analysis, target_profile, request_payload):
    provider = ai_provider_for_request(request_payload)
    if provider in {"rule", "rules", "disabled", "none"}:
        return {
            "provider": "rule",
            "status": "disabled",
            "target_profile": target_profile,
            "base_plan": None,
            "classification": source_analysis.get("classification", {}),
            "candidate_strategy": {},
        }
    if provider != "openai":
        raise ApiError(400, "Unsupported mastering planner")

    planner_response = call_openai_mastering_planner(
        source_analysis,
        reference_analysis,
        target_profile,
        request_payload,
    )
    selected_profile = planner_response.get("selected_profile")
    if request_payload.get("profile") or target_profile.get("profile", "").startswith("reference_match_"):
        selected_target = target_profile
    else:
        selected_target = copy_target_profile(selected_profile)

    plan = dict(planner_response.get("mastering_plan", {}))
    plan["candidate_id"] = "ai_base"
    plan["style"] = "balanced"
    plan["loudness"] = "standard"
    base_plan = validate_mastering_plan(plan)
    classification = planner_response.get("classification", {})
    if isinstance(classification, dict):
        source_analysis["classification"].update(
            {
                "genre_guess": classification.get(
                    "genre_guess", source_analysis["classification"].get("genre_guess")
                ),
                "vocal_prominence": classification.get(
                    "vocal_prominence",
                    source_analysis["classification"].get("vocal_prominence"),
                ),
                "mix_quality": classification.get(
                    "mix_quality", source_analysis["classification"].get("mix_quality")
                ),
                "mastering_goal": classification.get(
                    "mastering_goal",
                    source_analysis["classification"].get("mastering_goal"),
                ),
                "mix_problem_tags": classification.get(
                    "mix_problem_tags",
                    source_analysis["classification"].get("mix_problem_tags", []),
                ),
                "already_mastered": classification.get(
                    "already_mastered",
                    source_analysis["classification"].get("already_mastered", False),
                ),
            }
        )

    return {
        "provider": "openai",
        "model": MASTERING_OPENAI_MODEL,
        "status": "completed",
        "target_profile": selected_target,
        "base_plan": base_plan,
        "classification": source_analysis.get("classification", {}),
        "candidate_strategy": planner_response.get("candidate_strategy", {}),
        "raw_planner_response": planner_response,
    }


def mastering_plan_with_ai_fallback(source_analysis, reference_analysis, target_profile, request_payload):
    provider = ai_provider_for_request(request_payload)
    if provider in {"rule", "rules", "disabled", "none"}:
        return plan_mastering_with_ai(source_analysis, reference_analysis, target_profile, request_payload)
    try:
        return plan_mastering_with_ai(source_analysis, reference_analysis, target_profile, request_payload)
    except ApiError:
        raise
    except Exception as exc:
        if request_payload.get("planner") == "openai":
            raise
        log_event(
            "mastering_ai_planner_fallback",
            provider=provider,
            error=truncate(exc, 2000),
        )
        return {
            "provider": "rule",
            "status": "fallback",
            "fallback_reason": truncate(exc, 2000),
            "target_profile": target_profile,
            "base_plan": None,
            "classification": source_analysis.get("classification", {}),
            "candidate_strategy": {},
        }


def loudness_target_for_variant(target_profile, loudness_level):
    base = float(target_profile["target_lufs"])
    if loudness_level == "conservative":
        return clamp(base - 2.0, -16, -8)
    if loudness_level == "loud":
        return clamp(base + 1.2, -16, -8)
    return clamp(base, -16, -8)


def add_or_merge_eq(eq_moves, eq_move):
    if eq_move.get("type") != "bell":
        eq_moves.append(eq_move)
        return
    for existing in eq_moves:
        if existing.get("type") == "bell" and abs(existing.get("frequency", 0) - eq_move["frequency"]) <= 120:
            existing["gain_db"] = round(
                clamp(
                    existing.get("gain_db", 0) + eq_move["gain_db"],
                    -MASTERING_EQ_MAX_GAIN_DB,
                    MASTERING_EQ_MAX_GAIN_DB,
                ),
                3,
            )
            existing["reason"] = f"{existing.get('reason', '')}; {eq_move.get('reason', '')}".strip("; ")
            return
    eq_moves.append(eq_move)


def build_mastering_plan(source_analysis, target_profile, style, loudness_level):
    source_lufs = source_analysis["loudness"].get("integrated_lufs") or -18
    target_lufs = loudness_target_for_variant(target_profile, loudness_level)
    input_gain_db = clamp((target_lufs - source_lufs) * 0.18, -3, 3)
    eq_moves = [{"type": "highpass", "frequency": 28, "slope": "gentle", "reason": "remove subsonic rumble"}]

    source_spectrum = source_analysis.get("spectrum", {})
    target_spectrum = target_profile.get("spectral_curve", {})
    for band, center_hz in MASTERING_BAND_CENTERS.items():
        source_value = source_spectrum.get(band)
        target_value = target_spectrum.get(band)
        if source_value is None or target_value is None:
            continue
        delta = target_value - source_value
        if abs(delta) < 2.0:
            continue
        gain = clamp(delta * 0.42, -3.4, 3.4)
        reason = "move tonal balance toward target"
        if band == "low_mid_120_300" and gain < 0:
            reason = "reduce low-mid mud"
        elif band == "presence_1000_4000" and gain > 0:
            reason = "bring vocal/presence forward"
        elif band == "harsh_4000_8000" and gain < 0:
            reason = "reduce harshness"
        elif band == "air_8000_16000" and gain > 0:
            reason = "add air"
        add_or_merge_eq(
            eq_moves,
            {
                "type": "bell",
                "frequency": center_hz,
                "q": 0.8 if center_hz >= 1000 else 0.9,
                "gain_db": round(gain, 3),
                "reason": reason,
            },
        )

    if style == "warm":
        add_or_merge_eq(eq_moves, {"type": "bell", "frequency": 115, "q": 0.75, "gain_db": 1.25, "reason": "warm style bass support"})
        add_or_merge_eq(eq_moves, {"type": "bell", "frequency": 850, "q": 0.9, "gain_db": 0.65, "reason": "warm style body"})
        add_or_merge_eq(eq_moves, {"type": "bell", "frequency": 9200, "q": 0.65, "gain_db": -1.0, "reason": "warm style top-end restraint"})
    elif style == "open":
        add_or_merge_eq(eq_moves, {"type": "bell", "frequency": 3000, "q": 0.75, "gain_db": 1.45, "reason": "open style presence"})
        add_or_merge_eq(eq_moves, {"type": "bell", "frequency": 11200, "q": 0.65, "gain_db": 1.9, "reason": "open style air"})
        add_or_merge_eq(eq_moves, {"type": "bell", "frequency": 240, "q": 0.95, "gain_db": -1.1, "reason": "open style low-mid cleanup"})

    dynamics = source_analysis.get("dynamics", {})
    crest_factor = source_analysis.get("loudness", {}).get("crest_factor") or 12
    if dynamics.get("compression_guess") == "over_compressed":
        ratio = 1.12
        threshold_db = -12
    elif loudness_level == "loud":
        ratio = 1.8
        threshold_db = -21
    elif crest_factor > 14:
        ratio = 1.55
        threshold_db = -22
    else:
        ratio = 1.35
        threshold_db = -20

    stereo_target = target_profile.get("stereo", {})
    current_width = source_analysis.get("stereo", {}).get("width_score", 0.5)
    current_correlation = source_analysis.get("stereo", {}).get("mono_correlation", 1)
    widen = clamp((stereo_target.get("target_width", 0.7) - current_width) * 0.22, 0, 0.18)
    if style == "open":
        widen = clamp(widen + 0.04, 0, 0.18)
    elif style == "warm":
        widen = clamp(widen - 0.02, 0, 0.12)
    if current_correlation < stereo_target.get("min_mono_correlation", 0.25):
        widen = 0

    saturation_amount = 0.02
    if style == "warm":
        saturation_amount = 0.045
    elif loudness_level == "loud":
        saturation_amount = 0.04
    elif style == "open":
        saturation_amount = 0.015

    return validate_mastering_plan(
        {
            "candidate_id": f"{style}_{loudness_level}",
            "style": style,
            "loudness": loudness_level,
            "input_gain_db": round(input_gain_db, 3),
            "target_lufs": target_lufs,
            "target_true_peak": min(-1.0, float(target_profile["target_true_peak"])),
            "target_lra": clamp(float(target_profile["target_lra"]), 3, 12),
            "eq": eq_moves,
            "compression": {
                "type": "broadband",
                "threshold_db": threshold_db,
                "ratio": ratio,
                "attack_ms": 24 if loudness_level != "loud" else 12,
                "release_ms": 160 if style == "warm" else 110,
            },
            "stereo": {
                "widen_amount": round(widen, 3),
                "mono_bass_below_hz": 120,
            },
            "saturation": {
                "amount": saturation_amount,
                "type": "soft",
            },
            "limiter": {
                "ceiling_db": min(-1.0, float(target_profile["target_true_peak"])),
                "aggressiveness": "medium" if loudness_level != "loud" else "high",
            },
        }
    )


def style_variant_eq_moves(style):
    if style == "warm":
        return [
            {
                "type": "bell",
                "frequency": 115,
                "q": 0.75,
                "gain_db": 1.25,
                "reason": "warm style bass support",
            },
            {
                "type": "bell",
                "frequency": 850,
                "q": 0.9,
                "gain_db": 0.65,
                "reason": "warm style body",
            },
            {
                "type": "bell",
                "frequency": 9200,
                "q": 0.65,
                "gain_db": -1.0,
                "reason": "warm style top-end restraint",
            },
        ]
    if style == "open":
        return [
            {
                "type": "bell",
                "frequency": 3000,
                "q": 0.75,
                "gain_db": 1.45,
                "reason": "open style presence",
            },
            {
                "type": "bell",
                "frequency": 11200,
                "q": 0.65,
                "gain_db": 1.9,
                "reason": "open style air",
            },
            {
                "type": "bell",
                "frequency": 240,
                "q": 0.95,
                "gain_db": -1.1,
                "reason": "open style low-mid cleanup",
            },
        ]
    return []


def variant_mastering_plan(base_plan, target_profile, style, loudness_level):
    plan = json.loads(json.dumps(base_plan))
    plan["candidate_id"] = f"{style}_{loudness_level}"
    plan["style"] = style
    plan["loudness"] = loudness_level
    plan["target_lufs"] = loudness_target_for_variant(target_profile, loudness_level)
    plan["target_true_peak"] = min(-1.0, float(target_profile["target_true_peak"]))
    plan["target_lra"] = clamp(float(target_profile["target_lra"]), 3, 12)

    for move in style_variant_eq_moves(style):
        add_or_merge_eq(plan.setdefault("eq", []), move)

    compression = plan.setdefault("compression", {})
    if loudness_level == "conservative":
        compression["ratio"] = clamp(float(compression.get("ratio", 1.35)) - 0.15, 1.1, 3.0)
        compression["threshold_db"] = clamp(
            float(compression.get("threshold_db", -20)) + 2,
            -30,
            -10,
        )
        plan["saturation"]["amount"] = clamp(
            float(plan.get("saturation", {}).get("amount", 0.02)) - 0.01,
            0,
            0.08,
        )
    elif loudness_level == "loud":
        compression["ratio"] = clamp(float(compression.get("ratio", 1.35)) + 0.25, 1.1, 3.0)
        compression["threshold_db"] = clamp(
            float(compression.get("threshold_db", -20)) - 2,
            -30,
            -10,
        )
        plan["saturation"]["amount"] = clamp(
            float(plan.get("saturation", {}).get("amount", 0.02)) + 0.015,
            0,
            0.08,
        )

    stereo = plan.setdefault("stereo", {})
    widen = float(stereo.get("widen_amount", 0))
    if style == "open":
        widen += 0.035
    elif style == "warm":
        widen -= 0.02
    stereo["widen_amount"] = clamp(widen, 0, 0.18)
    return validate_mastering_plan(plan)


def validate_mastering_plan(plan):
    validated = {
        "candidate_id": str(plan.get("candidate_id", "candidate"))[:80],
        "style": plan.get("style", "balanced") if plan.get("style") in MASTERING_STYLES else "balanced",
        "loudness": plan.get("loudness", "standard")
        if plan.get("loudness") in MASTERING_LOUDNESS_LEVELS
        else "standard",
        "input_gain_db": round(clamp(float(plan.get("input_gain_db", 0)), -6, 6), 3),
        "output_gain_db": round(clamp(float(plan.get("output_gain_db", 0)), -12, 12), 3),
        "target_lufs": round(clamp(float(plan.get("target_lufs", -12)), -16, -8), 3),
        "target_true_peak": round(min(-1.0, clamp(float(plan.get("target_true_peak", -1)), -3, -1)), 3),
        "target_lra": round(clamp(float(plan.get("target_lra", 6)), 3, 12), 3),
        "eq": [],
        "compression": {},
        "stereo": {},
        "cleanup": {},
        "ambience": {},
        "saturation": {},
        "limiter": {},
        "deesser": {},
        "dynamic_eq": {},
    }
    for move in plan.get("eq", []):
        if not isinstance(move, dict):
            continue
        move_type = move.get("type")
        if move_type == "highpass":
            validated["eq"].append(
                {
                    "type": "highpass",
                    "frequency": round(clamp(float(move.get("frequency", 28)), 20, 60), 3),
                    "slope": "gentle",
                    "reason": str(move.get("reason", ""))[:200],
                }
            )
        elif move_type in {"bell", "shelf"}:
            validated["eq"].append(
                {
                    "type": move_type,
                    "frequency": round(clamp(float(move.get("frequency", 1000)), 60, 16000), 3),
                    "q": round(clamp(float(move.get("q", 0.8)), 0.3, 3.0), 3),
                    "gain_db": round(
                        clamp(
                            float(move.get("gain_db", 0)),
                            -MASTERING_EQ_MAX_GAIN_DB,
                            MASTERING_EQ_MAX_GAIN_DB,
                        ),
                        3,
                    ),
                    "reason": str(move.get("reason", ""))[:200],
                }
            )
    compression = plan.get("compression", {}) if isinstance(plan.get("compression"), dict) else {}
    validated["compression"] = {
        "type": "broadband",
        "threshold_db": round(clamp(float(compression.get("threshold_db", -20)), -30, -10), 3),
        "ratio": round(clamp(float(compression.get("ratio", 1.4)), 1.1, 3.0), 3),
        "attack_ms": round(clamp(float(compression.get("attack_ms", 20)), 5, 80), 3),
        "release_ms": round(clamp(float(compression.get("release_ms", 120)), 50, 300), 3),
        "mix": round(clamp(float(compression.get("mix", 0.85)), 0.35, 1.0), 3),
    }
    stereo = plan.get("stereo", {}) if isinstance(plan.get("stereo"), dict) else {}
    validated["stereo"] = {
        "widen_amount": round(clamp(float(stereo.get("widen_amount", 0)), 0, 0.18), 3),
        "mono_to_stereo": bool(stereo.get("mono_to_stereo", False)),
        "mono_to_stereo_strength": round(
            clamp(float(stereo.get("mono_to_stereo_strength", 0)), 0, 0.18),
            3,
        ),
        "mono_bass_below_hz": round(clamp(float(stereo.get("mono_bass_below_hz", 120)), 80, 180), 3),
        "mono_bass_amount": round(clamp(float(stereo.get("mono_bass_amount", 0)), 0, 1), 3),
        "reason": str(stereo.get("reason", ""))[:200],
    }
    cleanup = plan.get("cleanup", {}) if isinstance(plan.get("cleanup"), dict) else {}
    validated["cleanup"] = {
        "enabled": bool(cleanup.get("enabled", False)),
        "noise_reduction_db": round(clamp(float(cleanup.get("noise_reduction_db", 0)), 0, 24), 3),
        "noise_floor_db": round(clamp(float(cleanup.get("noise_floor_db", -50)), -75, -28), 3),
        "gate_threshold_db": round(clamp(float(cleanup.get("gate_threshold_db", -45)), -60, -28), 3),
        "gate_range": round(clamp(float(cleanup.get("gate_range", 0.06)), 0.003, 0.2), 3),
        "reason": str(cleanup.get("reason", ""))[:200],
    }
    ambience = plan.get("ambience", {}) if isinstance(plan.get("ambience"), dict) else {}
    validated["ambience"] = {
        "amount": round(clamp(float(ambience.get("amount", 0)), 0, 0.35), 3),
        "delay_ms": round(clamp(float(ambience.get("delay_ms", 65)), 35, 140), 3),
        "reason": str(ambience.get("reason", ""))[:200],
    }
    adjustments = plan.get("adjustments", {}) if isinstance(plan.get("adjustments"), dict) else {}
    validated["adjustments"] = {
        key: round(clamp(float(value), *CONTROL_SETTING_RANGES[key]), 3)
        for key, value in adjustments.items()
        if key in CONTROL_SETTING_RANGES
    }
    saturation = plan.get("saturation", {}) if isinstance(plan.get("saturation"), dict) else {}
    validated["saturation"] = {
        "amount": round(clamp(float(saturation.get("amount", 0)), 0, 0.08), 3),
        "type": "soft",
    }
    deesser = plan.get("deesser", {}) if isinstance(plan.get("deesser"), dict) else {}
    validated["deesser"] = {
        "amount": round(clamp(float(deesser.get("amount", 0)), 0, 1), 3),
        "frequency": round(clamp(float(deesser.get("frequency", 0.55)), 0.35, 0.8), 3),
        "max_reduction": round(clamp(float(deesser.get("max_reduction", 0.55)), 0.2, 0.9), 3),
    }
    dynamic_eq = plan.get("dynamic_eq", {}) if isinstance(plan.get("dynamic_eq"), dict) else {}
    validated["dynamic_eq"] = {
        "amount": round(clamp(float(dynamic_eq.get("amount", 0)), 0, 1), 3),
        "harsh_frequency": round(clamp(float(dynamic_eq.get("harsh_frequency", 4600)), 3000, 6500), 3),
        "harsh_range_db": round(clamp(float(dynamic_eq.get("harsh_range_db", 2.5)), 1, 8), 3),
    }
    limiter = plan.get("limiter", {}) if isinstance(plan.get("limiter"), dict) else {}
    ceiling_db = round(min(-1.5, clamp(float(limiter.get("ceiling_db", -1.5)), -3, -1.5)), 3)
    validated["limiter"] = {
        "ceiling_db": ceiling_db,
        "aggressiveness": limiter.get("aggressiveness", "medium")
        if limiter.get("aggressiveness") in {"low", "medium", "high"}
        else "medium",
    }
    validated["target_true_peak"] = min(validated["target_true_peak"], ceiling_db)
    return validated


def compile_mastering_filter_stages(plan):
    stages = []
    format_filters = ["aformat=sample_rates=48000:channel_layouts=stereo"]
    stereo = plan.get("stereo", {})
    mono_to_stereo = bool(stereo.get("mono_to_stereo"))
    if mono_to_stereo:
        strength = clamp(
            float(stereo.get("mono_to_stereo_strength") or stereo.get("widen_amount") or 0.12),
            0.08,
            0.18,
        )
        right_delay = clamp(5.5 + strength * 24, 6.0, 9.8)
        side_gain = clamp(1.0 + strength * 2.4, 1.15, 1.45)
        format_filters.extend(
            [
                "pan=mono|c0=0.5*c0+0.5*c1",
                "pan=stereo|c0=c0|c1=c0",
                "haas="
                "left_delay=1.6:"
                f"right_delay={right_delay:.3f}:"
                f"side_gain={side_gain:.3f}:"
                "middle_source=mid:"
                "level_out=0.95:"
                "right_phase=false",
            ]
        )
    stages.append(
        {
            "key": "format_stereo",
            "label": "Format / Stereo Prep",
            "filters": format_filters,
        }
    )

    cleanup = plan.get("cleanup", {})
    cleanup_filters = []
    if cleanup.get("enabled") and cleanup.get("noise_reduction_db", 0) >= 0.5:
        cleanup_filters.append(
            "afftdn="
            f"nr={cleanup['noise_reduction_db']}:"
            f"nf={cleanup['noise_floor_db']}:"
            "tn=true:"
            "ad=0.65:"
            "gs=8"
        )
        cleanup_filters.append(
            "agate="
            f"threshold={db_to_linear(cleanup['gate_threshold_db']):.6f}:"
            f"range={cleanup['gate_range']}:"
            "ratio=8:"
            "attack=8:"
            "release=180:"
            "knee=3:"
            "detection=rms:"
            "link=average"
        )
    stages.append({"key": "cleanup", "label": "Cleanup", "filters": cleanup_filters})

    eq_filters = []
    if abs(plan["input_gain_db"]) >= 0.05:
        eq_filters.append(f"volume={plan['input_gain_db']}dB")
    for move in plan.get("eq", []):
        if move["type"] == "highpass":
            eq_filters.append(f"highpass=f={move['frequency']}")
        elif move["type"] == "bell" and abs(move["gain_db"]) >= 0.05:
            eq_filters.append(
                f"equalizer=f={move['frequency']}:t=q:w={move['q']}:g={move['gain_db']}"
            )
        elif move["type"] == "shelf" and abs(move["gain_db"]) >= 0.05:
            # FFmpeg's stock equalizer is used as a broad shelf approximation.
            eq_filters.append(
                f"equalizer=f={move['frequency']}:t=q:w={max(move['q'], 0.5)}:g={move['gain_db']}"
            )
    stages.append({"key": "eq", "label": "EQ", "filters": eq_filters})

    sibilance_filters = []
    deesser = plan.get("deesser", {})
    if deesser.get("amount", 0) >= 0.01:
        sibilance_filters.append(
            "deesser="
            f"i={clamp(float(deesser.get('amount', 0)), 0, 1):.3f}:"
            f"m={clamp(float(deesser.get('max_reduction', 0.55)), 0.2, 0.9):.3f}:"
            f"f={clamp(float(deesser.get('frequency', 0.55)), 0.35, 0.8):.3f}"
        )

    dynamic_eq = plan.get("dynamic_eq", {})
    dynamic_amount = clamp(float(dynamic_eq.get("amount", 0)), 0, 1)
    if dynamic_amount >= 0.01:
        sibilance_filters.append(
            "adynamicequalizer="
            "auto=adaptive:"
            f"dfrequency={dynamic_eq.get('harsh_frequency', 4600)}:"
            "dqfactor=1.4:"
            f"tfrequency={dynamic_eq.get('harsh_frequency', 4600)}:"
            "tqfactor=1.0:"
            "attack=8:"
            "release=100:"
            f"ratio={1.2 + dynamic_amount * 3.0:.3f}:"
            f"range={dynamic_eq.get('harsh_range_db', 2.5)}:"
            "mode=cutabove:"
            "dftype=bandpass:"
            "tftype=bell"
        )
    stages.append(
        {
            "key": "sibilance_dynamic_eq",
            "label": "De-ess / Dynamic EQ",
            "filters": sibilance_filters,
        }
    )

    compression = plan.get("compression", {})
    threshold = db_to_linear(compression.get("threshold_db", -20))
    stages.append(
        {
            "key": "compression",
            "label": "Compression",
            "filters": [
                "acompressor="
                f"threshold={threshold:.6f}:"
                f"ratio={compression.get('ratio', 1.4)}:"
                f"attack={compression.get('attack_ms', 20)}:"
                f"release={compression.get('release_ms', 120)}:"
                f"makeup=1:knee=2.5:link=average:detection=rms:mix={compression.get('mix', 0.85)}"
            ],
        }
    )

    color_space_filters = []
    saturation = plan.get("saturation", {})
    if saturation.get("amount", 0) >= 0.01:
        threshold = clamp(1.0 - saturation["amount"], 0.90, 1.0)
        color_space_filters.append(f"asoftclip=type=tanh:threshold={threshold:.3f}:oversample=2")

    ambience = plan.get("ambience", {})
    if ambience.get("amount", 0) >= 0.01:
        amount = clamp(float(ambience.get("amount", 0)), 0, 0.35)
        first_delay = clamp(float(ambience.get("delay_ms", 65)), 35, 140)
        second_delay = clamp(first_delay * 1.72, 60, 220)
        wet_gain = clamp(0.06 + amount * 0.32, 0.06, 0.18)
        first_decay = clamp(0.08 + amount * 0.34, 0.08, 0.20)
        second_decay = clamp(0.04 + amount * 0.22, 0.04, 0.13)
        color_space_filters.append(
            "aecho="
            f"0.8:{wet_gain:.3f}:"
            f"{first_delay:.1f}|{second_delay:.1f}:"
            f"{first_decay:.3f}|{second_decay:.3f}"
        )

    widen_amount = stereo.get("widen_amount", 0)
    if widen_amount >= 0.01 and not mono_to_stereo:
        color_space_filters.append(
            "stereowiden="
            f"delay={clamp(6 + (widen_amount * 60), 4, 16):.3f}:"
            f"feedback={widen_amount:.3f}:crossfeed=0.15:drymix=0.95"
        )
    mono_bass_amount = clamp(float(stereo.get("mono_bass_amount", 0)), 0, 1)
    if mono_bass_amount >= 0.01:
        color_space_filters.append(
            "stereotools="
            f"slev={clamp(1.0 - mono_bass_amount * 0.55, 0.45, 1.0):.3f}:"
            "mlev=1.0:"
            f"base={-0.12 * mono_bass_amount:.3f}"
        )
    stages.append(
        {
            "key": "tone_space",
            "label": "Tone / Space",
            "filters": color_space_filters,
        }
    )

    level_filters = [
        f"loudnorm=I={plan['target_lufs']}:LRA={plan['target_lra']}:TP={plan['target_true_peak']}"
    ]
    if abs(plan.get("output_gain_db", 0)) >= 0.05:
        level_filters.append(f"volume={plan['output_gain_db']}dB")
    level_filters.append(
        f"alimiter=limit={db_to_linear(plan['limiter']['ceiling_db']):.6f}:level=false:latency=true"
    )
    stages.append(
        {
            "key": "level_limiter",
            "label": "Level / Limiter",
            "filters": level_filters,
        }
    )
    return stages


def flatten_mastering_filter_stages(stages, through_key=None):
    filters = []
    for stage in stages:
        filters.extend(stage.get("filters") or [])
        if through_key and stage.get("key") == through_key:
            break
    return filters or ["anull"]


def compile_mastering_filtergraph(plan):
    filters = flatten_mastering_filter_stages(compile_mastering_filter_stages(plan))
    return ",".join(filters)


def encode_master_output_args(output_path):
    suffix = output_path.suffix.lower()
    if suffix == ".wav":
        return ["-c:a", "pcm_s24le"]
    if suffix in {".m4a", ".aac"}:
        return ["-c:a", "aac", "-b:a", "256k"]
    if suffix == ".flac":
        return ["-c:a", "flac"]
    return ["-c:a", "libmp3lame", "-q:a", "2"]


def render_mastering_output(command_id, source_path, output_path, plan, preview_seconds=None):
    filtergraph = compile_mastering_filtergraph(plan)
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
    ]
    if preview_seconds:
        args.extend(["-t", str(preview_seconds)])
    args.extend(
        [
            "-vn",
            "-af",
            filtergraph,
            *encode_master_output_args(output_path),
            str(output_path),
        ]
    )
    run_logged_ffmpeg(command_id, f"mastering_{plan['candidate_id']}", args)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Expected mastering output was not created: {output_path.name}")
    return filtergraph


def preset_score_breakdown():
    return {
        "score": 0,
        "spectral_distance_from_target": 0,
        "harshness_score": 0,
        "overcompression_risk": "not_scored",
    }


def build_mastering_preset_candidates(source_analysis, target_profile, command_id, source_path):
    presets = [
        ("open", "standard", "Open / Bright"),
        ("warm", "standard", "Warm"),
        ("balanced", "standard", "Balanced"),
    ]
    source_file = candidate_output_url(command_id, source_path)
    candidates = []
    for order, (style, loudness_level, label) in enumerate(presets, start=1):
        plan = build_mastering_plan(source_analysis, target_profile, style, loudness_level)
        candidate = {
            "candidate_id": plan["candidate_id"],
            "style": style,
            "loudness": loudness_level,
            "label": label,
            "base_plan": plan,
            "plan": plan,
            "control_settings": control_settings_from_plan(plan),
            "ffmpeg_filtergraph": "",
            "preview_file": source_file,
            "post_analysis": source_analysis,
            "score": 4 - order,
            "score_breakdown": preset_score_breakdown(),
            "scoring_status": "skipped",
            "render_status": "not_rendered",
            "preview_status": "source_audio",
        }
        candidates.append(candidate)
    return candidates


def safe_debug_token(value, fallback="debug"):
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._-")
    return (token or fallback)[:80]


def create_mastering_debug_artifacts(
    command_id,
    source_path,
    output_dir,
    work_dir,
    plan,
    preview_seconds,
    debug_options=None,
):
    debug_options = debug_options or {}
    if debug_options.get("debug_stage_artifacts_enabled", True) is False:
        return {"enabled": False, "stages": []}

    short_id = command_id.replace("master-", "")[:12]
    candidate_id = safe_debug_token(plan.get("candidate_id", "candidate"), "candidate")
    points = int(clamp(int(debug_options.get("debug_waveform_points", 512)), 64, 2048))
    filter_stages = compile_mastering_filter_stages(plan)
    debug_artifacts = {
        "enabled": True,
        "kind": "mastering_stage_feedback",
        "public_base_url": f"{PUBLIC_BASE_URL}/tmp/{command_id}",
        "candidate_id": plan.get("candidate_id", ""),
        "base": {},
        "stages": [],
    }

    original_waveform_path = output_dir / f"00-original-source-waveform-{short_id}.json"
    original_summary = waveform_json_for_audio(
        command_id,
        source_path,
        original_waveform_path,
        work_dir,
        "Original source input",
        points=points,
    )
    original_waveform = {
        "storage_url": public_output_url(command_id, original_waveform_path),
        "summary": original_summary,
    }
    debug_artifacts["base"]["original_source_waveform"] = original_waveform
    # Keep the existing UI field populated until all deployed clients use the
    # generic source waveform name.
    debug_artifacts["base"]["original_vocal_waveform"] = original_waveform
    set_job_status(command_id, debug_artifacts=debug_artifacts)

    accumulated_filters = []
    for order, stage in enumerate(filter_stages, start=1):
        stage_key = stage["key"]
        accumulated_filters.extend(stage.get("filters") or [])
        filtergraph = ",".join(accumulated_filters or ["anull"])
        audio_path = output_dir / f"{order:02d}-{stage_key}-master-{candidate_id}-{short_id}.wav"
        waveform_path = output_dir / f"{order:02d}-{stage_key}-waveform-{candidate_id}-{short_id}.json"
        args = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
        ]
        if preview_seconds:
            args.extend(["-t", str(preview_seconds)])
        args.extend(
            [
                "-vn",
                "-af",
                filtergraph,
                "-c:a",
                "pcm_s16le",
                str(audio_path),
            ]
        )
        log_event(
            "mastering_stage_debug_command_started",
            command_id=command_id,
            candidate_id=plan.get("candidate_id", ""),
            stage=stage_key,
            args=[truncate(arg, 800) for arg in args],
        )
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=PROCESSING_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            raise RuntimeError(stderr[-2000:] or f"Mastering debug stage render failed: {stage_key}")
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            raise RuntimeError(f"Expected mastering debug stage file was not created: {stage_key}")

        summary = waveform_json_for_audio(
            command_id,
            audio_path,
            waveform_path,
            work_dir,
            f"{stage['label']} master",
            points=points,
        )
        stage_artifact = {
            "order": order,
            "key": stage_key,
            "label": stage["label"],
            "mix": {"storage_url": public_output_url(command_id, audio_path)},
            "waveform": {"storage_url": public_output_url(command_id, waveform_path)},
            "compare_to": "original_source_waveform",
            "summary": summary,
            "stage_filters": stage.get("filters") or [],
        }
        debug_artifacts["stages"].append(stage_artifact)
        set_job_status(command_id, debug_artifacts=debug_artifacts)
        log_event(
            "mastering_stage_debug_completed",
            command_id=command_id,
            candidate_id=plan.get("candidate_id", ""),
            stage=stage_key,
            audio_url=stage_artifact["mix"]["storage_url"],
            waveform_url=stage_artifact["waveform"]["storage_url"],
            summary=summary,
        )

    return debug_artifacts


def spectral_distance(analysis, target_profile):
    spectrum = analysis.get("spectrum", {})
    target = target_profile.get("spectral_curve", {})
    distances = [
        abs(spectrum[key] - target[key])
        for key in target
        if key in spectrum and spectrum[key] is not None and target[key] is not None
    ]
    if not distances:
        return 1.0
    return round(sum(distances) / (len(distances) * 12), 4)


def harshness_score(analysis, target_profile):
    harsh = analysis.get("spectrum", {}).get("harsh_4000_8000")
    target = target_profile.get("spectral_curve", {}).get("harsh_4000_8000")
    if harsh is None or target is None:
        return 0
    return round(clamp((harsh - target) / 8, 0, 1), 4)


def leading_noise_score(analysis):
    leading_rms = analysis.get("dynamics", {}).get("leading_rms_db")
    if leading_rms is None:
        return 0
    return round(clamp((leading_rms + 58) / 28, 0, 1), 4)


CONTROL_SETTING_RANGES = {
    "brightness": (-MASTERING_EQ_MAX_GAIN_DB, MASTERING_EQ_MAX_GAIN_DB),
    "warmth": (-MASTERING_EQ_MAX_GAIN_DB, MASTERING_EQ_MAX_GAIN_DB),
    "presence": (-MASTERING_EQ_MAX_GAIN_DB, MASTERING_EQ_MAX_GAIN_DB),
    "bass": (-MASTERING_EQ_MAX_GAIN_DB, MASTERING_EQ_MAX_GAIN_DB),
    "low_mid": (-MASTERING_EQ_MAX_GAIN_DB, MASTERING_EQ_MAX_GAIN_DB),
    "air": (-MASTERING_EQ_MAX_GAIN_DB, MASTERING_EQ_MAX_GAIN_DB),
    "de_ess": (0, 1),
    "harshness": (-MASTERING_EQ_MAX_GAIN_DB, MASTERING_EQ_MAX_GAIN_DB),
    "boxiness": (-MASTERING_EQ_MAX_GAIN_DB, MASTERING_EQ_MAX_GAIN_DB),
    "body": (-MASTERING_EQ_MAX_GAIN_DB, MASTERING_EQ_MAX_GAIN_DB),
    "mono_bass": (0, 1),
    "dynamic_eq": (0, 1),
    "stereo_width": (0, 0.18),
    "cleanup": (0.003, 0.2),
    "cleanup_gate": (-60, -28),
    "cleanup_noise": (0, 24),
    "loudness": (-16, -8),
    "volume_db": (-12, 12),
    "input_gain_db": (-6, 6),
    "compression": (1.1, 3.0),
    "compression_threshold": (-30, -10),
    "compression_attack": (5, 80),
    "compression_release": (50, 300),
    "compression_mix": (0.35, 1.0),
    "saturation": (0, 0.08),
    "limiter": (-3, -1.5),
    "ambience": (0, 0.35),
}


CONTROL_EQ_POINTS = {
    "brightness": (11200, 1200),
    "warmth": (145, 80),
    "presence": (2850, 600),
    "bass": (95, 80),
    "low_mid": (260, 100),
    "air": (14000, 2200),
    "harshness": (4600, 350),
    "boxiness": (420, 45),
    "body": (185, 25),
}


def eq_gain_near(plan, center_hz, tolerance_hz):
    gain = 0.0
    for move in plan.get("eq", []):
        if not isinstance(move, dict):
            continue
        if move.get("type") not in {"bell", "shelf"}:
            continue
        try:
            frequency = float(move.get("frequency", 0))
            move_gain = float(move.get("gain_db", 0))
        except (TypeError, ValueError):
            continue
        if abs(frequency - center_hz) <= tolerance_hz:
            gain += move_gain
    return round(clamp(gain, -MASTERING_EQ_MAX_GAIN_DB, MASTERING_EQ_MAX_GAIN_DB), 3)


def control_settings_from_plan(plan):
    compression = plan.get("compression", {})
    stereo = plan.get("stereo", {})
    cleanup = plan.get("cleanup", {})
    ambience = plan.get("ambience", {})
    deesser = plan.get("deesser", {})
    dynamic_eq = plan.get("dynamic_eq", {})
    settings = {
        "brightness": eq_gain_near(plan, *CONTROL_EQ_POINTS["brightness"]),
        "warmth": eq_gain_near(plan, *CONTROL_EQ_POINTS["warmth"]),
        "presence": eq_gain_near(plan, *CONTROL_EQ_POINTS["presence"]),
        "bass": eq_gain_near(plan, *CONTROL_EQ_POINTS["bass"]),
        "low_mid": eq_gain_near(plan, *CONTROL_EQ_POINTS["low_mid"]),
        "air": eq_gain_near(plan, *CONTROL_EQ_POINTS["air"]),
        "de_ess": deesser.get("amount", 0),
        "harshness": eq_gain_near(plan, *CONTROL_EQ_POINTS["harshness"]),
        "boxiness": eq_gain_near(plan, *CONTROL_EQ_POINTS["boxiness"]),
        "body": eq_gain_near(plan, *CONTROL_EQ_POINTS["body"]),
        "mono_bass": stereo.get("mono_bass_amount", 0),
        "dynamic_eq": dynamic_eq.get("amount", 0),
        "stereo_width": stereo.get("mono_to_stereo_strength")
        if stereo.get("mono_to_stereo")
        else stereo.get("widen_amount", 0),
        "cleanup": cleanup.get("gate_range", 0.06),
        "cleanup_gate": cleanup.get("gate_threshold_db", -45),
        "cleanup_noise": cleanup.get("noise_reduction_db", 0),
        "loudness": plan.get("target_lufs", -10.5),
        "volume_db": plan.get("output_gain_db", 0),
        "input_gain_db": plan.get("input_gain_db", 0),
        "compression": compression.get("ratio", 1.4),
        "compression_threshold": compression.get("threshold_db", -20),
        "compression_attack": compression.get("attack_ms", 20),
        "compression_release": compression.get("release_ms", 120),
        "compression_mix": compression.get("mix", 0.85),
        "saturation": plan.get("saturation", {}).get("amount", 0),
        "limiter": plan.get("limiter", {}).get("ceiling_db", -1.5),
        "ambience": ambience.get("amount", 0),
    }
    return {
        key: round(clamp(float(value), *CONTROL_SETTING_RANGES[key]), 3)
        for key, value in settings.items()
    }


def score_mastering_candidate(analysis, target_profile):
    loudness = analysis.get("loudness", {})
    stereo = analysis.get("stereo", {})
    dynamics = analysis.get("dynamics", {})
    lufs = loudness.get("integrated_lufs") or target_profile["target_lufs"]
    true_peak = loudness.get("true_peak_db") or target_profile["target_true_peak"]
    lra = loudness.get("lra") or target_profile["target_lra"]
    distance = spectral_distance(analysis, target_profile)
    harshness = harshness_score(analysis, target_profile)
    noise = leading_noise_score(analysis)
    score = 100.0
    score -= abs(lufs - target_profile["target_lufs"]) * 4
    score -= abs(true_peak - target_profile["target_true_peak"]) * 8
    score -= distance * 25
    if lra < target_profile.get("min_lra", 3):
        score -= 10
    if stereo.get("mono_correlation", 1) < target_profile.get("stereo", {}).get("min_mono_correlation", 0.25):
        score -= 20
    if dynamics.get("clipping_detected"):
        score -= 50
    if harshness > 0.8:
        score -= 15
    if noise > 0.45:
        score -= noise * 12
    if dynamics.get("compression_guess") == "over_compressed" and lra < target_profile.get("min_lra", 3.5):
        score -= 15
    return {
        "score": round(clamp(score, 0, 100), 3),
        "spectral_distance_from_target": distance,
        "harshness_score": harshness,
        "leading_noise_score": noise,
        "overcompression_risk": "high"
        if dynamics.get("compression_guess") == "over_compressed" and lra < target_profile.get("min_lra", 3.5)
        else "low",
    }


def select_recommended_mastering_candidates(candidate_results, limit=3):
    if not candidate_results:
        return []
    sorted_candidates = sorted(candidate_results, key=lambda item: item["score"], reverse=True)
    selected = [sorted_candidates[0]]
    best_score = sorted_candidates[0]["score"]

    def add_best(predicate, max_score_drop=35):
        if len(selected) >= limit:
            return False
        selected_ids = {item["candidate_id"] for item in selected}
        score_floor = max(best_score - max_score_drop, best_score * 0.55, 15)
        for item in sorted_candidates:
            if item["candidate_id"] in selected_ids:
                continue
            if item["score"] < score_floor:
                continue
            if predicate(item):
                selected.append(item)
                return True
        return False

    first = selected[0]
    add_best(
        lambda item: item.get("style") != first.get("style")
        and item.get("loudness") != first.get("loudness"),
        max_score_drop=45,
    )
    used_styles = {item.get("style") for item in selected}
    used_loudness = {item.get("loudness") for item in selected}
    add_best(
        lambda item: item.get("style") not in used_styles
        and item.get("loudness") not in used_loudness,
        max_score_drop=45,
    )
    used_styles = {item.get("style") for item in selected}
    used_loudness = {item.get("loudness") for item in selected}
    add_best(
        lambda item: item.get("style") not in used_styles
        or item.get("loudness") not in used_loudness,
        max_score_drop=45,
    )

    for item in sorted_candidates:
        if len(selected) >= limit:
            break
        if item["candidate_id"] not in {selected_item["candidate_id"] for selected_item in selected}:
            selected.append(item)
    return selected[:limit]


def validate_mastering_adjustments(raw_adjustments):
    if raw_adjustments is None:
        raw_adjustments = {}
    if not isinstance(raw_adjustments, dict):
        raise ApiError(400, "adjustments must be an object")
    adjustments = {}
    for key, value in raw_adjustments.items():
        if key not in CONTROL_SETTING_RANGES:
            continue
        minimum, maximum = CONTROL_SETTING_RANGES[key]
        try:
            adjustments[key] = round(clamp(float(value), minimum, maximum), 3)
        except (TypeError, ValueError) as exc:
            raise ApiError(400, f"adjustments.{key} must be numeric") from exc
    return adjustments


def complete_mastering_control_settings(base_plan, partial_settings):
    settings = control_settings_from_plan(base_plan)
    for key, value in partial_settings.items():
        if key in CONTROL_SETTING_RANGES:
            settings[key] = round(clamp(float(value), *CONTROL_SETTING_RANGES[key]), 3)
    return settings


def apply_mastering_adjustments(base_plan, settings):
    plan = json.loads(json.dumps(base_plan))
    plan["candidate_id"] = str(plan.get("candidate_id", "candidate"))
    plan["adjustments"] = settings
    base_settings = control_settings_from_plan(base_plan)

    brightness = settings["brightness"] - base_settings["brightness"]
    warmth = settings["warmth"] - base_settings["warmth"]
    presence = settings["presence"] - base_settings["presence"]
    bass = settings["bass"] - base_settings["bass"]
    low_mid = settings["low_mid"] - base_settings["low_mid"]
    air = settings["air"] - base_settings["air"]
    harshness = settings["harshness"] - base_settings["harshness"]
    boxiness = settings["boxiness"] - base_settings["boxiness"]
    body = settings["body"] - base_settings["body"]

    plan["input_gain_db"] = settings["input_gain_db"]
    plan["output_gain_db"] = settings["volume_db"]
    plan["target_lufs"] = settings["loudness"]

    if abs(brightness) >= 0.01:
        add_or_merge_eq(
            plan.setdefault("eq", []),
            {
                "type": "bell",
                "frequency": 11200,
                "q": 0.65,
                "gain_db": round(brightness * 2.2, 3),
                "reason": "manual brightness adjustment",
            },
        )
    if abs(air) >= 0.01:
        add_or_merge_eq(
            plan.setdefault("eq", []),
            {
                "type": "bell",
                "frequency": 14000,
                "q": 0.7,
                "gain_db": round(air * 2.4, 3),
                "reason": "manual air adjustment",
            },
        )
    if abs(bass) >= 0.01:
        add_or_merge_eq(
            plan.setdefault("eq", []),
            {
                "type": "bell",
                "frequency": 95,
                "q": 0.8,
                "gain_db": round(bass * 2.4, 3),
                "reason": "manual bass adjustment",
            },
        )
    if abs(low_mid) >= 0.01:
        add_or_merge_eq(
            plan.setdefault("eq", []),
            {
                "type": "bell",
                "frequency": 260,
                "q": 0.95,
                "gain_db": round(low_mid * 2.2, 3),
                "reason": "manual low-mid adjustment",
            },
        )
    if abs(warmth) >= 0.01:
        add_or_merge_eq(
            plan.setdefault("eq", []),
            {
                "type": "bell",
                "frequency": 145,
                "q": 0.8,
                "gain_db": round(warmth * 1.8, 3),
                "reason": "manual warmth adjustment",
            },
        )
        add_or_merge_eq(
            plan.setdefault("eq", []),
            {
                "type": "bell",
                "frequency": 360,
                "q": 1.0,
                "gain_db": round(max(-warmth, 0) * -1.0, 3),
                "reason": "manual low-mid cleanup for less warmth",
            },
        )
    if abs(presence) >= 0.01:
        add_or_merge_eq(
            plan.setdefault("eq", []),
            {
                "type": "bell",
                "frequency": 2850,
                "q": 0.85,
                "gain_db": round(presence * 2.0, 3),
                "reason": "manual presence adjustment",
            },
        )
    if abs(harshness) >= 0.01:
        plan.setdefault("eq", []).append(
            {
                "type": "bell",
                "frequency": 4600,
                "q": 1.05,
                "gain_db": round(harshness, 3),
                "reason": "manual harshness adjustment around 3k-6k",
            },
        )
    if abs(boxiness) >= 0.01:
        plan.setdefault("eq", []).append(
            {
                "type": "bell",
                "frequency": 420,
                "q": 1.0,
                "gain_db": round(boxiness, 3),
                "reason": "manual boxiness adjustment around 300-500 Hz",
            },
        )
    if abs(body) >= 0.01:
        plan.setdefault("eq", []).append(
            {
                "type": "bell",
                "frequency": 185,
                "q": 0.85,
                "gain_db": round(body, 3),
                "reason": "manual body adjustment around 120-250 Hz",
            },
        )

    compression = plan.setdefault("compression", {})
    compression["ratio"] = settings["compression"]
    compression["threshold_db"] = settings["compression_threshold"]
    compression["attack_ms"] = settings["compression_attack"]
    compression["release_ms"] = settings["compression_release"]
    compression["mix"] = settings["compression_mix"]

    stereo = plan.setdefault("stereo", {})
    stereo["widen_amount"] = settings["stereo_width"]
    stereo["mono_bass_amount"] = settings["mono_bass"]
    if stereo.get("mono_to_stereo"):
        stereo["mono_to_stereo_strength"] = settings["stereo_width"]
    stereo["reason"] = "manual stereo width and mono bass settings"

    cleanup = plan.setdefault("cleanup", {})
    cleanup["enabled"] = settings["cleanup_noise"] >= 0.5
    cleanup["noise_reduction_db"] = settings["cleanup_noise"]
    cleanup["gate_threshold_db"] = settings["cleanup_gate"]
    cleanup["gate_range"] = settings["cleanup"]
    cleanup["reason"] = "manual cleanup settings"

    saturation = plan.setdefault("saturation", {})
    saturation["amount"] = settings["saturation"]

    plan["deesser"] = {
        "amount": settings["de_ess"],
        "frequency": round(clamp(0.5 + settings["de_ess"] * 0.18, 0.35, 0.8), 3),
        "max_reduction": round(clamp(0.35 + settings["de_ess"] * 0.45, 0.2, 0.9), 3),
    }

    plan["dynamic_eq"] = {
        "amount": settings["dynamic_eq"],
        "harsh_frequency": 4600,
        "harsh_range_db": round(clamp(1.5 + settings["dynamic_eq"] * 4 + max(-settings["harshness"], 0) * 0.35, 1, 8), 3),
    }

    limiter = plan.setdefault("limiter", {})
    limiter["ceiling_db"] = settings["limiter"]

    plan["ambience"] = {
        "amount": settings["ambience"],
        "delay_ms": round(55 + (settings["ambience"] / 0.35) * 65, 3) if settings["ambience"] > 0 else 65,
        "reason": "manual ambience settings" if settings["ambience"] >= 0.01 else "",
    }
    return validate_mastering_plan(plan)


def candidate_output_url(command_id, path):
    return {"storage_url": public_output_url(command_id, path)}


def validate_mastering_request(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Request body must be a JSON object")
    audio_url = validate_http_url(payload.get("audio_url"), "audio_url")
    reference_url = payload.get("reference_url", "")
    if reference_url not in (None, ""):
        reference_url = validate_http_url(reference_url, "reference_url")
    else:
        reference_url = ""

    output_filename = payload.get("output_filename", "")
    if output_filename in (None, ""):
        output_filename = f"MASTER_{uuid.uuid4().hex}.wav"
    output_filename = Path(str(output_filename).strip()).name
    if not SAFE_FILENAME_RE.match(output_filename):
        raise ApiError(400, "Invalid output_filename")
    if not output_filename.lower().endswith((".wav", ".mp3", ".m4a", ".aac", ".flac")):
        raise ApiError(400, "output_filename must be an audio filename")

    profile = payload.get("profile", "")
    if profile not in (None, "") and profile not in MASTERING_TARGET_PROFILES:
        raise ApiError(400, "Unknown mastering profile")

    planner = payload.get("planner", "auto")
    if planner not in {"auto", "openai", "rule"}:
        raise ApiError(400, "planner must be auto, openai, or rule")

    user_goal = payload.get("user_goal", "")
    if user_goal in (None, ""):
        user_goal = ""
    if not isinstance(user_goal, str):
        raise ApiError(400, "user_goal must be a string")

    try:
        preview_seconds = int(payload.get("preview_seconds", MASTERING_DEFAULT_PREVIEW_SECONDS))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "preview_seconds must be numeric") from exc
    preview_seconds = int(clamp(preview_seconds, 20, 180))

    debug_stage_artifacts_enabled = payload.get(
        "debug_stage_artifacts_enabled",
        payload.get("debug_artifacts_enabled", True),
    )
    if not isinstance(debug_stage_artifacts_enabled, bool):
        raise ApiError(400, "debug_stage_artifacts_enabled must be boolean")
    try:
        debug_waveform_points = int(payload.get("debug_waveform_points", 512))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "debug_waveform_points must be numeric") from exc
    debug_waveform_points = int(clamp(debug_waveform_points, 64, 2048))

    webhook_metadata = payload.get("webhook_metadata", {})
    if not isinstance(webhook_metadata, dict):
        raise ApiError(400, "webhook_metadata must be an object")

    return {
        "audio_url": audio_url,
        "reference_url": reference_url,
        "output_filename": output_filename,
        "profile": profile or "",
        "planner": planner,
        "user_goal": user_goal[:1200],
        "preview_seconds": preview_seconds,
        "debug_stage_artifacts_enabled": debug_stage_artifacts_enabled,
        "debug_waveform_points": debug_waveform_points,
        "webhook_url": validate_optional_webhook(payload),
        "webhook_metadata": webhook_metadata,
    }


def validate_mastering_analyze_request(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Request body must be a JSON object")
    return {"audio_url": validate_http_url(payload.get("audio_url"), "audio_url")}


def safe_upload_filename(raw_filename, fallback="upload.wav"):
    filename = Path(str(raw_filename or fallback).strip()).name
    if not filename or not SAFE_FILENAME_RE.match(filename):
        filename = fallback
    if not filename.lower().endswith((".wav", ".aiff", ".aif", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".webm")):
        filename = f"{filename}.bin" if SAFE_FILENAME_RE.match(f"{filename}.bin") else fallback
    return filename


def read_mastering_upload(handler):
    if API_KEY and handler.headers.get("X-API-KEY") != API_KEY:
        raise ApiError(401, "Invalid API key")

    try:
        content_length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise ApiError(400, "Invalid Content-Length") from exc

    if content_length <= 0:
        raise ApiError(400, "Missing upload body")
    if content_length > MASTERING_UPLOAD_MAX_BYTES:
        raise ApiError(413, "Upload too large")

    filename = safe_upload_filename(
        handler.headers.get("X-Filename")
        or unquote(urlparse(handler.path).query.split("filename=", 1)[-1])
        if "filename=" in urlparse(handler.path).query
        else handler.headers.get("X-Filename")
    )
    upload_id = f"upload-{uuid.uuid4().hex}"
    output_dir = OUTPUT_ROOT / upload_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    remaining = content_length
    with output_path.open("wb") as file:
        while remaining > 0:
            chunk = handler.rfile.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            file.write(chunk)
            remaining -= len(chunk)

    if remaining != 0:
        try:
            output_path.unlink()
        except OSError:
            pass
        raise ApiError(400, "Upload body ended early")

    return {
        "success": True,
        "upload_id": upload_id,
        "filename": filename,
        "bytes": output_path.stat().st_size,
        "audio_url": public_output_url(upload_id, output_path),
        "expires_in_seconds": OUTPUT_TTL_SECONDS,
    }


def validate_mastering_finalize_request(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Request body must be a JSON object")
    command_id = payload.get("command_id")
    candidate_id = payload.get("candidate_id")
    if not isinstance(command_id, str) or not command_id.startswith("master-"):
        raise ApiError(400, "command_id must be a mastering job id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ApiError(400, "Missing candidate_id")
    output_filename = payload.get("output_filename", "")
    if output_filename in (None, ""):
        output_filename = f"FINAL_MASTER_{uuid.uuid4().hex}.wav"
    output_filename = Path(str(output_filename).strip()).name
    if not SAFE_FILENAME_RE.match(output_filename):
        raise ApiError(400, "Invalid output_filename")
    if not output_filename.lower().endswith((".wav", ".mp3", ".m4a", ".aac", ".flac")):
        raise ApiError(400, "output_filename must be an audio filename")
    return {
        "source_command_id": command_id,
        "candidate_id": candidate_id,
        "output_filename": output_filename,
    }


def validate_mastering_preference_request(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Request body must be a JSON object")
    command_id = payload.get("command_id")
    winner = payload.get("winner_candidate_id")
    if not isinstance(command_id, str) or not command_id.startswith("master-"):
        raise ApiError(400, "command_id must be a mastering job id")
    if not isinstance(winner, str) or not winner:
        raise ApiError(400, "Missing winner_candidate_id")
    compared = payload.get("compared_candidate_ids", [])
    if compared is None:
        compared = []
    if not isinstance(compared, list) or not all(isinstance(item, str) for item in compared):
        raise ApiError(400, "compared_candidate_ids must be an array of strings")
    metadata = payload.get("user_metadata", {})
    if not isinstance(metadata, dict):
        raise ApiError(400, "user_metadata must be an object")
    return {
        "command_id": command_id,
        "winner_candidate_id": winner,
        "compared_candidate_ids": compared,
        "user_metadata": metadata,
    }


def validate_mastering_reprocess_request(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Request body must be a JSON object")
    command_id = payload.get("command_id")
    candidate_id = payload.get("candidate_id")
    if not isinstance(command_id, str) or not command_id.startswith("master-"):
        raise ApiError(400, "command_id must be a mastering job id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ApiError(400, "Missing candidate_id")
    try:
        preview_seconds = int(payload.get("preview_seconds", MASTERING_DEFAULT_PREVIEW_SECONDS))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "preview_seconds must be numeric") from exc
    clean_audio = payload.get("clean_audio", False)
    if not isinstance(clean_audio, bool):
        raise ApiError(400, "clean_audio must be boolean")
    audio_url = payload.get("audio_url", "")
    if audio_url in (None, ""):
        audio_url = ""
    else:
        audio_url = validate_audio_url(audio_url)
    return {
        "command_id": command_id,
        "candidate_id": candidate_id[:80],
        "audio_url": audio_url,
        "adjustments": validate_mastering_adjustments(payload.get("adjustments", {})),
        "preview_seconds": int(clamp(preview_seconds, 20, 180)),
        "clean_audio": clean_audio,
        "voice_gate_options": normalize_voice_gate_options(
            payload.get("voice_gate_options", payload.get("clean_audio_options", {})),
            default_mode="conservative",
        ),
    }


def normalize_voice_gate_options(raw_options, default_mode="conservative"):
    if raw_options is None:
        raw_options = {}
    if not isinstance(raw_options, dict):
        raise ApiError(400, "voice_gate_options must be an object")

    mode = str(raw_options.get("mode", default_mode)).strip().lower()
    if mode not in VOICE_GATE_MODES:
        raise ApiError(400, "voice_gate_options.mode must be conservative, balanced, or aggressive")
    defaults = VOICE_GATE_MODES[mode]

    def number_option(name, default, low, high):
        try:
            return round(clamp(float(raw_options.get(name, default)), low, high), 3)
        except (TypeError, ValueError) as exc:
            raise ApiError(400, f"voice_gate_options.{name} must be numeric") from exc

    fail_on_no_voice = raw_options.get("fail_on_no_voice", True)
    if not isinstance(fail_on_no_voice, bool):
        raise ApiError(400, "voice_gate_options.fail_on_no_voice must be boolean")
    suppress_leading_artifacts = raw_options.get("suppress_leading_artifacts", True)
    if not isinstance(suppress_leading_artifacts, bool):
        raise ApiError(400, "voice_gate_options.suppress_leading_artifacts must be boolean")

    return {
        "mode": mode,
        "attenuation_db": number_option("attenuation_db", -80, -96, -12),
        "padding_ms": number_option("padding_ms", defaults["default_padding_ms"], 60, 600),
        "attack_ms": number_option("attack_ms", 25, 3, 120),
        "release_ms": number_option("release_ms", 140, 20, 500),
        "min_voice_ms": number_option("min_voice_ms", 140, 40, 800),
        "min_gap_ms": number_option("min_gap_ms", 180, 40, 900),
        "suppress_leading_artifacts": suppress_leading_artifacts,
        "fail_on_no_voice": fail_on_no_voice,
    }


def validate_voice_gate_request(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Request body must be a JSON object")
    audio_url = validate_audio_url(payload.get("audio_url"))
    output_filename = payload.get("output_filename", "")
    if output_filename in (None, ""):
        output_filename = f"VOICE_CLEANED_{uuid.uuid4().hex}.wav"
    output_filename = Path(str(output_filename).strip()).name
    if not SAFE_FILENAME_RE.match(output_filename):
        raise ApiError(400, "Invalid output_filename")
    if not output_filename.lower().endswith((".wav", ".mp3", ".m4a", ".aac", ".flac")):
        raise ApiError(400, "output_filename must be an audio filename")

    webhook_metadata = payload.get("webhook_metadata", {})
    if not isinstance(webhook_metadata, dict):
        raise ApiError(400, "webhook_metadata must be an object")

    return {
        "audio_url": audio_url,
        "output_filename": output_filename,
        "webhook_url": validate_optional_webhook(payload),
        "webhook_metadata": webhook_metadata,
        "voice_gate_options": normalize_voice_gate_options(
            payload.get("voice_gate_options", payload.get("options", {})),
            default_mode=str(payload.get("mode", "conservative")),
        ),
    }


def voice_gate_webhook_payload(status, request_payload, job_data=None, error_message=None):
    data = {
        "status": status,
        "webhook_metadata": request_payload.get("webhook_metadata", {}),
    }
    if job_data:
        data.update(job_data)
    if status == "FAILED":
        data["error_message"] = error_message or "Voice gate failed"
        data["error_status"] = "voice_gate_error"
    return {"data": data}


def enqueue_voice_gate_job(request_payload):
    cleanup_old_outputs()
    command_id = f"voicegate-{uuid.uuid4().hex}"
    with jobs_lock:
        jobs[command_id] = {"status": "QUEUED", "created_at": time.time()}
    log_event(
        "voice_gate_job_queued",
        command_id=command_id,
        audio_url=request_payload["audio_url"],
        output_filename=request_payload["output_filename"],
        options=request_payload["voice_gate_options"],
    )
    job_executor.submit(run_voice_gate_job, command_id, request_payload)
    return command_id


def run_voice_gate_job(command_id, request_payload):
    set_job_status(command_id, status="RUNNING")
    started = time.monotonic()
    output_dir = OUTPUT_ROOT / command_id
    output_dir.mkdir(parents=True, exist_ok=True)
    options = request_payload["voice_gate_options"]
    append_job_log(
        command_id,
        "voice_gate_started",
        audio_url=request_payload["audio_url"],
        output_filename=request_payload["output_filename"],
        options=options,
    )
    try:
        cleanup_old_outputs()
        with tempfile.TemporaryDirectory(prefix=f"{command_id}-") as work_dir_name:
            work_dir = Path(work_dir_name)
            input_path = work_dir / f"source{extension_from_url(request_payload['audio_url'])}"
            mask_path = work_dir / "voice_mask.wav"
            output_path = output_dir / request_payload["output_filename"]
            append_job_log(command_id, "voice_gate_download_started", audio_url=request_payload["audio_url"])
            download_file(request_payload["audio_url"], input_path)
            append_job_log(command_id, "voice_gate_downloaded", bytes=input_path.stat().st_size)

            activity = analyze_voice_activity(input_path, options)
            append_job_log(
                command_id,
                "voice_gate_analysis_completed",
                duration_seconds=activity["duration_seconds"],
                voice_segment_count=activity["voice_segment_count"],
                voice_ratio=activity["voice_ratio"],
                thresholds=activity["thresholds"],
                score_summary=activity["score_summary"],
                leading_artifact=activity.get("leading_artifact"),
                signal_quality=activity.get("signal_quality"),
            )
            if (
                activity["voice_segment_count"] == 0
                and options["fail_on_no_voice"]
                and not activity.get("signal_quality", {}).get("non_vocal_noise_detected")
            ):
                raise RuntimeError("No confident singing voice segments detected")

            write_voice_gate_mask(
                mask_path,
                activity["duration_seconds"],
                activity["segments"],
                options["attenuation_db"],
                options["attack_ms"],
                options["release_ms"],
            )
            append_job_log(
                command_id,
                "voice_gate_mask_created",
                mask_sample_rate=VOICE_GATE_MASK_SAMPLE_RATE,
                attenuation_db=options["attenuation_db"],
                attack_ms=options["attack_ms"],
                release_ms=options["release_ms"],
                bytes=mask_path.stat().st_size,
            )
            filtergraph = render_voice_gate(command_id, "voice_gate_render", input_path, mask_path, output_path)
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError("Expected voice-gated output file was not created")

            output_file = {"storage_url": public_output_url(command_id, output_path)}
            job_data = {
                "output_file": output_file,
                "output_files": {"out_1": output_file},
                "analysis": activity,
                "voice_gate_options": options,
                "filtergraph": filtergraph,
            }
            if request_payload.get("webhook_url"):
                post_webhook_safely(
                    request_payload["webhook_url"],
                    voice_gate_webhook_payload("COMPLETED", request_payload, job_data),
                    "voice_gate_webhook",
                )
            set_job_status(command_id, status="COMPLETED", **job_data)
            append_job_log(
                command_id,
                "voice_gate_completed",
                duration_seconds=round(time.monotonic() - started, 3),
                output_file=output_file,
                voice_segment_count=activity["voice_segment_count"],
                muted_seconds=activity["muted_seconds"],
            )
    except Exception as exc:
        message = str(exc) or "Voice gate failed"
        if request_payload.get("webhook_url"):
            post_webhook_safely(
                request_payload["webhook_url"],
                voice_gate_webhook_payload("FAILED", request_payload, error_message=message),
                "voice_gate_webhook",
            )
        set_job_status(command_id, status="FAILED", error_message=message)
        append_job_log(
            command_id,
            "voice_gate_failed",
            duration_seconds=round(time.monotonic() - started, 3),
            error=truncate(message, 4000),
        )


def mastering_webhook_payload(status, request_payload, job_data=None, error_message=None):
    data = {
        "status": status,
        "webhook_metadata": request_payload.get("webhook_metadata", {}),
    }
    if job_data:
        data.update(job_data)
    if status == "FAILED":
        data["error_message"] = error_message or "Mastering failed"
        data["error_status"] = "mastering_error"
    return {"data": data}


def enqueue_mastering_job(request_payload):
    cleanup_old_outputs()
    command_id = f"master-{uuid.uuid4().hex}"
    with jobs_lock:
        jobs[command_id] = {"status": "QUEUED", "created_at": time.time()}
    log_event(
        "mastering_job_queued",
        command_id=command_id,
        audio_url=request_payload["audio_url"],
        reference_url=request_payload.get("reference_url", ""),
        profile=request_payload.get("profile", ""),
    )
    job_executor.submit(run_mastering_job, command_id, request_payload)
    return command_id


def enqueue_mastering_final_job(request_payload):
    cleanup_old_outputs()
    command_id = f"final-{uuid.uuid4().hex}"
    with jobs_lock:
        jobs[command_id] = {"status": "QUEUED", "created_at": time.time()}
    log_event(
        "mastering_final_job_queued",
        command_id=command_id,
        source_command_id=request_payload["source_command_id"],
        candidate_id=request_payload["candidate_id"],
    )
    job_executor.submit(run_mastering_final_job, command_id, request_payload)
    return command_id


def find_mastering_source_path(command_id):
    output_dir = OUTPUT_ROOT / command_id
    if not output_dir.is_dir():
        return None
    try:
        resolved_output_dir = output_dir.resolve()
        resolved_root = OUTPUT_ROOT.resolve()
        if resolved_root not in resolved_output_dir.parents and resolved_output_dir != resolved_root:
            return None
    except OSError:
        return None
    for path in output_dir.iterdir():
        if (
            path.is_file()
            and path.stem == "source"
            and path.suffix.lower() in {".wav", ".aiff", ".aif", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".webm"}
        ):
            return path
    return None


def restore_mastering_job_from_outputs(command_id):
    if not isinstance(command_id, str) or not command_id.startswith("master-"):
        return None
    with jobs_lock:
        existing = jobs.get(command_id)
        if existing and existing.get("status") == "COMPLETED":
            return existing

    source_path = find_mastering_source_path(command_id)
    if source_path is None:
        return None

    with jobs_lock:
        jobs.setdefault(command_id, {"status": "RESTORING", "created_at": time.time()})

    log_event(
        "mastering_job_restore_started",
        command_id=command_id,
        source_path=str(source_path),
    )
    source_analysis = analyze_mastering_file(source_path)
    target_profile = select_mastering_target_profile(source_analysis, "")
    recommended = build_mastering_preset_candidates(
        source_analysis,
        target_profile,
        command_id,
        source_path,
    )
    job_data = {
        "source_url": public_output_url(command_id, source_path),
        "source_path": str(source_path),
        "source_analysis": source_analysis,
        "reference_analysis": None,
        "target_profile": target_profile,
        "planner_result": {
            "provider": "preset",
            "model": "",
            "status": "restored",
            "target_profile": target_profile,
            "base_plan": None,
            "fallback_reason": "restored from existing output files after process restart",
        },
        "candidate_scores": recommended,
        "recommended_candidates": recommended,
        "output_filename": "FINAL_MASTER.wav",
        "debug_artifacts": {"enabled": False, "kind": "mastering_stage_feedback", "stages": []},
        "debug_error": "",
        "restored_from_outputs": True,
    }
    set_job_status(command_id, status="COMPLETED", **job_data)
    append_job_log(
        command_id,
        "mastering_job_restored_from_outputs",
        source_path=str(source_path),
        candidates=[item.get("candidate_id") for item in recommended],
    )
    return {"status": "COMPLETED", **job_data}


def run_mastering_job(command_id, request_payload):
    set_job_status(command_id, status="RUNNING")
    started = time.monotonic()
    output_dir = OUTPUT_ROOT / command_id
    output_dir.mkdir(parents=True, exist_ok=True)
    log_event(
        "mastering_job_started",
        command_id=command_id,
        audio_url=request_payload["audio_url"],
        reference_url=request_payload.get("reference_url", ""),
        preview_seconds=request_payload.get("preview_seconds"),
    )
    append_job_log(
        command_id,
        "mastering_processing_started",
        preview_seconds=request_payload.get("preview_seconds"),
        profile=request_payload.get("profile", ""),
        planner=request_payload.get("planner", "auto"),
        has_reference=bool(request_payload.get("reference_url")),
        user_goal=truncate(request_payload.get("user_goal", ""), 500),
    )
    try:
        cleanup_old_outputs()
        source_path = output_dir / f"source{extension_from_url(request_payload['audio_url'])}"
        append_job_log(command_id, "mastering_source_download_started")
        download_file(request_payload["audio_url"], source_path)
        append_job_log(
            command_id,
            "mastering_source_download_completed",
            bytes=source_path.stat().st_size,
            filename=source_path.name,
        )
        append_job_log(command_id, "mastering_source_analysis_started")
        source_analysis = analyze_mastering_file(source_path)
        append_job_log(
            command_id,
            "mastering_source_analysis_completed",
            analysis=compact_mastering_analysis(source_analysis),
        )
        target_profile = select_mastering_target_profile(
            source_analysis,
            request_payload.get("profile", ""),
        )
        append_job_log(
            command_id,
            "mastering_target_profile_selected",
            profile=target_profile.get("profile"),
            target_lufs=target_profile.get("target_lufs"),
            target_true_peak=target_profile.get("target_true_peak"),
            target_lra=target_profile.get("target_lra"),
        )
        reference_analysis = None
        if request_payload.get("reference_url"):
            append_job_log(
                command_id,
                "mastering_reference_analysis_skipped",
                reason="fast preset load does not analyze reference tracks",
            )

        planner_result = {
            "provider": "preset",
            "model": "",
            "status": "skipped",
            "target_profile": target_profile,
            "base_plan": None,
            "fallback_reason": "fast preset load skips AI planning, rendering, and scoring",
        }
        append_job_log(
            command_id,
            "mastering_planner_skipped",
            reason=planner_result["fallback_reason"],
        )

        recommended = build_mastering_preset_candidates(
            source_analysis,
            target_profile,
            command_id,
            source_path,
        )
        candidate_results = recommended
        append_job_log(
            command_id,
            "mastering_preset_candidates_ready",
            candidates=[
                {
                    "candidate_id": item["candidate_id"],
                    "style": item["style"],
                    "label": item.get("label"),
                    "preview_status": item.get("preview_status"),
                    "scoring_status": item.get("scoring_status"),
                }
                for item in recommended
            ],
        )
        debug_artifacts = {"enabled": False, "kind": "mastering_stage_feedback", "stages": []}
        debug_error = ""
        job_data = {
            "source_url": request_payload["audio_url"],
            "source_path": str(source_path),
            "source_analysis": source_analysis,
            "reference_analysis": reference_analysis,
            "target_profile": target_profile,
            "planner_result": planner_result,
            "candidate_scores": candidate_results,
            "recommended_candidates": recommended,
            "output_filename": request_payload["output_filename"],
            "debug_artifacts": debug_artifacts,
            "debug_error": debug_error,
        }
        set_job_status(command_id, status="COMPLETED", **job_data)
        duration = round(time.monotonic() - started, 3)
        log_event(
            "mastering_job_completed",
            command_id=command_id,
            duration_seconds=duration,
            planner_provider=planner_result.get("provider"),
            planner_status=planner_result.get("status"),
            recommended_candidates=[item["candidate_id"] for item in recommended],
        )
        if request_payload.get("webhook_url"):
            post_webhook_safely(
                request_payload["webhook_url"],
                mastering_webhook_payload("COMPLETED", request_payload, job_data),
                event_prefix="mastering_webhook",
            )
    except Exception as exc:
        message = str(exc) or "Mastering failed"
        duration = round(time.monotonic() - started, 3)
        append_job_log(
            command_id,
            "mastering_processing_failed",
            duration_seconds=duration,
            error=truncate(message, 4000),
        )
        log_event(
            "mastering_job_failed",
            command_id=command_id,
            duration_seconds=duration,
            error=truncate(message, 4000),
        )
        set_job_status(command_id, status="FAILED", error_message=message)
        if request_payload.get("webhook_url"):
            post_webhook_safely(
                request_payload["webhook_url"],
                mastering_webhook_payload("FAILED", request_payload, error_message=message),
                event_prefix="mastering_webhook_failure",
            )


def run_mastering_final_job(command_id, request_payload):
    set_job_status(command_id, status="RUNNING")
    started = time.monotonic()
    output_dir = OUTPUT_ROOT / command_id
    output_dir.mkdir(parents=True, exist_ok=True)
    append_job_log(
        command_id,
        "mastering_final_processing_started",
        source_command_id=request_payload["source_command_id"],
        candidate_id=request_payload["candidate_id"],
        output_filename=request_payload["output_filename"],
    )
    try:
        with jobs_lock:
            source_job = jobs.get(request_payload["source_command_id"])
        if not source_job or source_job.get("status") != "COMPLETED":
            source_job = restore_mastering_job_from_outputs(request_payload["source_command_id"])
        if not source_job or source_job.get("status") != "COMPLETED":
            raise RuntimeError("Source mastering job is not completed")
        source_path = Path(source_job.get("source_path", ""))
        if not source_path.is_file():
            raise RuntimeError("Source audio is no longer available")
        candidates = source_job.get("candidate_scores", [])
        candidate = next(
            (
                item
                for item in candidates
                if item.get("candidate_id") == request_payload["candidate_id"]
            ),
            None,
        )
        if candidate is None:
            raise RuntimeError("Candidate not found")
        output_path = output_dir / request_payload["output_filename"]
        append_job_log(
            command_id,
            "mastering_final_render_started",
            source_command_id=request_payload["source_command_id"],
            candidate_id=request_payload["candidate_id"],
            plan=compact_mastering_plan(candidate.get("plan", {})),
        )
        mastering_filtergraph = render_mastering_output(
            command_id,
            source_path,
            output_path,
            candidate["plan"],
            preview_seconds=None,
        )
        filtergraph = mastering_filtergraph
        voice_cleaning = candidate.get("voice_cleaning", {})
        if isinstance(voice_cleaning, dict) and voice_cleaning.get("enabled"):
            final_voice_cleaning = apply_voice_gate_to_file(
                command_id,
                "mastering_final_clean_audio",
                output_path,
                output_path,
                voice_cleaning.get("options") or normalize_voice_gate_options({}),
            )
            filtergraph = {
                "mastering": mastering_filtergraph,
                "voice_gate": final_voice_cleaning.get("filtergraph"),
            }
            append_job_log(
                command_id,
                "mastering_final_clean_audio_completed",
                candidate_id=request_payload["candidate_id"],
                voice_segment_count=final_voice_cleaning["analysis"]["voice_segment_count"],
                muted_seconds=final_voice_cleaning["analysis"]["muted_seconds"],
                leading_artifact=final_voice_cleaning["analysis"].get("leading_artifact"),
                signal_quality=final_voice_cleaning["analysis"].get("signal_quality"),
            )
            voice_cleaning = final_voice_cleaning
        final_analysis = analyze_mastering_file(output_path)
        append_job_log(
            command_id,
            "mastering_final_render_completed",
            candidate_id=request_payload["candidate_id"],
            bytes=output_path.stat().st_size,
            filtergraph=filtergraph,
            analysis=compact_mastering_analysis(final_analysis),
        )
        final_output = {"out_1": candidate_output_url(command_id, output_path)}
        set_job_status(
            command_id,
            status="COMPLETED",
            source_command_id=request_payload["source_command_id"],
            selected_candidate=request_payload["candidate_id"],
            final_outputs=final_output,
            final_analysis=final_analysis,
            ffmpeg_filtergraph=filtergraph,
            voice_cleaning=voice_cleaning,
        )
        log_event(
            "mastering_final_job_completed",
            command_id=command_id,
            duration_seconds=round(time.monotonic() - started, 3),
            source_command_id=request_payload["source_command_id"],
            candidate_id=request_payload["candidate_id"],
            final_outputs=final_output,
        )
    except Exception as exc:
        message = str(exc) or "Final master failed"
        append_job_log(
            command_id,
            "mastering_final_processing_failed",
            duration_seconds=round(time.monotonic() - started, 3),
            error=truncate(message, 4000),
        )
        set_job_status(command_id, status="FAILED", error_message=message)
        log_event(
            "mastering_final_job_failed",
            command_id=command_id,
            duration_seconds=round(time.monotonic() - started, 3),
            error=truncate(message, 4000),
        )


def reprocess_mastering_candidate(request_payload):
    command_id = request_payload["command_id"]
    candidate_id = request_payload["candidate_id"]
    partial_settings = request_payload["adjustments"]
    started = time.monotonic()
    with jobs_lock:
        source_job = jobs.get(command_id)
    if not source_job or source_job.get("status") != "COMPLETED":
        source_job = restore_mastering_job_from_outputs(command_id)
    with jobs_lock:
        source_job = jobs.get(command_id)
        if not source_job or source_job.get("status") != "COMPLETED":
            raise ApiError(404, "Mastering job is not completed or no longer available")
        source_path = Path(source_job.get("source_path", ""))
        target_profile = json.loads(json.dumps(source_job.get("target_profile", {})))
        candidates = list(source_job.get("candidate_scores", []))
        candidate = next(
            (item for item in candidates if item.get("candidate_id") == candidate_id),
            None,
        )
        edit_versions = dict(source_job.get("candidate_edit_versions", {}))
        edit_version = int(edit_versions.get(candidate_id, 0)) + 1

    if candidate is None:
        raise ApiError(404, "Candidate not found")
    if not source_path.is_file():
        raise ApiError(410, "Source audio is no longer available")
    if not target_profile:
        raise ApiError(410, "Target profile is no longer available")

    output_dir = OUTPUT_ROOT / command_id
    output_dir.mkdir(parents=True, exist_ok=True)
    append_job_log(
        command_id,
        "mastering_candidate_reprocess_started",
        candidate_id=candidate_id,
        edit_version=edit_version,
        adjustments=partial_settings,
        clean_audio=request_payload.get("clean_audio", False),
        has_audio_override=bool(request_payload.get("audio_url")),
    )
    temp_dir = None
    try:
        render_source_path = source_path
        if request_payload.get("audio_url"):
            temp_dir = tempfile.TemporaryDirectory(prefix=f"{command_id}-{candidate_id}-reprocess-source-")
            render_source_path = Path(temp_dir.name) / f"source{extension_from_url(request_payload['audio_url'])}"
            append_job_log(
                command_id,
                "mastering_candidate_reprocess_source_download_started",
                candidate_id=candidate_id,
                audio_url=request_payload["audio_url"],
            )
            download_file(request_payload["audio_url"], render_source_path)
            append_job_log(
                command_id,
                "mastering_candidate_reprocess_source_download_completed",
                candidate_id=candidate_id,
                bytes=render_source_path.stat().st_size,
            )
        base_plan = candidate.get("base_plan") or candidate.get("plan", {})
        settings = complete_mastering_control_settings(base_plan, partial_settings)
        plan = apply_mastering_adjustments(base_plan, settings)
        preview_path = output_dir / f"{candidate_id}_edit_{edit_version}.mp3"
        mastering_filtergraph = render_mastering_output(
            command_id,
            render_source_path,
            preview_path,
            plan,
            preview_seconds=request_payload["preview_seconds"],
        )
        filtergraph = mastering_filtergraph
        voice_cleaning = {"enabled": False}
        if request_payload.get("clean_audio"):
            cleaned_preview_path = output_dir / f"{candidate_id}_edit_{edit_version}_clean.mp3"
            voice_cleaning = apply_voice_gate_to_file(
                command_id,
                f"mastering_{candidate_id}_clean_audio",
                preview_path,
                cleaned_preview_path,
                request_payload["voice_gate_options"],
            )
            preview_path = cleaned_preview_path
            filtergraph = {
                "mastering": mastering_filtergraph,
                "voice_gate": voice_cleaning.get("filtergraph"),
            }
            append_job_log(
                command_id,
                "mastering_candidate_clean_audio_completed",
                candidate_id=candidate_id,
                edit_version=edit_version,
                voice_segment_count=voice_cleaning["analysis"]["voice_segment_count"],
                muted_seconds=voice_cleaning["analysis"]["muted_seconds"],
                leading_artifact=voice_cleaning["analysis"].get("leading_artifact"),
                signal_quality=voice_cleaning["analysis"].get("signal_quality"),
            )
        analysis = analyze_mastering_file(preview_path)
        score = score_mastering_candidate(analysis, target_profile)
        debug_artifacts = {}
        debug_error = ""
        try:
            debug_source_path = preview_path if request_payload.get("clean_audio") else render_source_path
            with tempfile.TemporaryDirectory(prefix=f"{command_id}-{candidate_id}-debug-") as work_dir_name:
                debug_artifacts = create_mastering_debug_artifacts(
                    command_id,
                    debug_source_path,
                    output_dir,
                    Path(work_dir_name),
                    plan,
                    request_payload["preview_seconds"],
                    request_payload,
                )
            append_job_log(
                command_id,
                "mastering_candidate_debug_artifacts_completed",
                candidate_id=candidate_id,
                edit_version=edit_version,
                stage_count=len(debug_artifacts.get("stages") or []),
            )
        except Exception as debug_exc:
            debug_error = str(debug_exc) or "Candidate debug artifact render failed"
            debug_artifacts = {
                "enabled": request_payload.get("debug_stage_artifacts_enabled", True),
                "kind": "mastering_stage_feedback",
                "candidate_id": candidate_id,
                "error": debug_error,
                "stages": [],
            }
            append_job_log(
                command_id,
                "mastering_candidate_debug_artifacts_failed",
                candidate_id=candidate_id,
                edit_version=edit_version,
                error=truncate(debug_error, 4000),
            )
        updated_candidate = {
            **candidate,
            "candidate_id": candidate_id,
            "style": candidate.get("style", plan.get("style")),
            "loudness": candidate.get("loudness", plan.get("loudness")),
            "base_plan": base_plan,
            "plan": plan,
            "control_settings": control_settings_from_plan(plan),
            "ffmpeg_filtergraph": filtergraph,
            "preview_file": candidate_output_url(command_id, preview_path),
            "post_analysis": analysis,
            "score": score["score"],
            "score_breakdown": score,
            "edit_version": edit_version,
            "last_adjustments": control_settings_from_plan(plan),
            "voice_cleaning": voice_cleaning,
            "scoring_status": "scored",
            "render_status": "rendered",
            "preview_status": "processed_preview",
        }
        with jobs_lock:
            source_job = jobs.get(command_id)
            candidate_scores = list(source_job.get("candidate_scores", []))
            recommended = list(source_job.get("recommended_candidates", []))
            candidate_scores = [
                updated_candidate if item.get("candidate_id") == candidate_id else item
                for item in candidate_scores
            ]
            recommended = [
                updated_candidate if item.get("candidate_id") == candidate_id else item
                for item in recommended
            ]
            edit_versions = dict(source_job.get("candidate_edit_versions", {}))
            edit_versions[candidate_id] = edit_version
            source_job.update(
                {
                    "candidate_scores": candidate_scores,
                    "recommended_candidates": recommended,
                    "candidate_edit_versions": edit_versions,
                    "debug_artifacts": debug_artifacts,
                    "debug_error": debug_error,
                }
            )
            processing_log = list(source_job.get("processing_log", []))
        append_job_log(
            command_id,
            "mastering_candidate_reprocess_completed",
            candidate_id=candidate_id,
            edit_version=edit_version,
            duration_seconds=round(time.monotonic() - started, 3),
            plan=compact_mastering_plan(plan),
            analysis=compact_mastering_analysis(analysis),
            score_breakdown=score,
            filtergraph=filtergraph,
        )
        with jobs_lock:
            processing_log = list(jobs.get(command_id, {}).get("processing_log", processing_log))
        return {
            "success": True,
            "candidate": updated_candidate,
            "debug_artifacts": debug_artifacts,
            "debug_error": debug_error,
            "processing_log": processing_log,
        }
    except ApiError:
        raise
    except Exception as exc:
        message = str(exc) or "Candidate reprocess failed"
        append_job_log(
            command_id,
            "mastering_candidate_reprocess_failed",
            candidate_id=candidate_id,
            edit_version=edit_version,
            duration_seconds=round(time.monotonic() - started, 3),
            error=truncate(message, 4000),
        )
        raise RuntimeError(message) from exc
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def save_mastering_preference(preference):
    with jobs_lock:
        source_job = jobs.get(preference["command_id"])
    if not source_job:
        raise ApiError(404, "Mastering job not found")
    candidates = source_job.get("candidate_scores", [])
    candidate_ids = {item.get("candidate_id") for item in candidates}
    if preference["winner_candidate_id"] not in candidate_ids:
        raise ApiError(400, "winner_candidate_id was not part of the mastering job")
    entry = {
        "ts": utc_timestamp(),
        "command_id": preference["command_id"],
        "input_analysis": source_job.get("source_analysis"),
        "target_profile": source_job.get("target_profile"),
        "candidates": [
            {
                "candidate_id": item.get("candidate_id"),
                "plan": item.get("plan"),
                "post_analysis": item.get("post_analysis"),
                "score": item.get("score"),
            }
            for item in candidates
            if not preference["compared_candidate_ids"]
            or item.get("candidate_id") in set(preference["compared_candidate_ids"])
            or item.get("candidate_id") == preference["winner_candidate_id"]
        ],
        "winner": preference["winner_candidate_id"],
        "user_metadata": preference["user_metadata"],
    }
    MASTERING_PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with MASTERING_PREFERENCES_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(json_safe(entry), sort_keys=True, allow_nan=False) + "\n")
    log_event(
        "mastering_preference_saved",
        command_id=preference["command_id"],
        winner=preference["winner_candidate_id"],
    )
    return {"success": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "ffmpeg-sound-mixer-api/0.1"

    def log_message(self, fmt, *args):
        log_event(
            "http_access",
            client_ip=self.client_address[0],
            message=fmt % args,
        )

    def serve_read(self, include_body=True):
        request_path = urlparse(self.path).path

        if request_path == "/health":
            json_response(self, 200, {"status": "ok"}, include_body=include_body)
            return

        if request_path == "/mastering/profiles":
            json_response(
                self,
                200,
                {
                    "profiles": MASTERING_TARGET_PROFILES,
                    "candidate_grid": {
                        "styles": list(MASTERING_STYLES),
                        "loudness_levels": list(MASTERING_LOUDNESS_LEVELS),
                    },
                    "ai_planner": {
                        "provider": MASTERING_AI_PROVIDER,
                        "openai_configured": bool(OPENAI_API_KEY),
                        "openai_model": MASTERING_OPENAI_MODEL,
                    },
                },
                include_body=include_body,
            )
            return

        if request_path in {"/", "/measure-loudness.md", "/ffmpeg-api.md"}:
            try:
                markdown_response(
                    self,
                    200,
                    DOC_FILE.read_text(encoding="utf-8"),
                    include_body=include_body,
                )
            except OSError:
                json_response(
                    self,
                    404,
                    {"success": False, "error": "Document not found"},
                    include_body=include_body,
                )
            return

        if request_path == "/mastering-ui-guide.md":
            guide_file = Path("/app/mastering-ui-guide.md")
            try:
                markdown_response(
                    self,
                    200,
                    guide_file.read_text(encoding="utf-8"),
                    include_body=include_body,
                )
            except OSError:
                json_response(
                    self,
                    404,
                    {"success": False, "error": "Guide not found"},
                    include_body=include_body,
                )
            return

        if request_path == "/voice-gate-guide.md":
            guide_file = Path("/app/voice-gate-guide.md")
            try:
                markdown_response(
                    self,
                    200,
                    guide_file.read_text(encoding="utf-8"),
                    include_body=include_body,
                )
            except OSError:
                json_response(
                    self,
                    404,
                    {"success": False, "error": "Guide not found"},
                    include_body=include_body,
                )
            return

        if request_path.startswith("/tmp/"):
            parts = [unquote(part) for part in request_path.split("/") if part]
            if len(parts) == 3:
                _, command_id, filename = parts
                output_path = OUTPUT_ROOT / command_id / filename
                try:
                    resolved = output_path.resolve()
                    root = OUTPUT_ROOT.resolve()
                    if root in resolved.parents and resolved.is_file():
                        file_response(self, resolved, include_body=include_body)
                        return
                except OSError:
                    pass

            json_response(
                self,
                404,
                {"success": False, "error": "Output file not found"},
                include_body=include_body,
            )
            return

        if request_path.startswith("/jobs/"):
            parts = [part for part in request_path.split("/") if part]
            if len(parts) == 2:
                command_id = parts[1]
                with jobs_lock:
                    job = jobs.get(command_id)
                if job is None and command_id.startswith("master-"):
                    job = restore_mastering_job_from_outputs(command_id)
                if job is not None:
                    json_response(
                        self,
                        200,
                        {"command_id": command_id, **job},
                        include_body=include_body,
                    )
                    return

            json_response(
                self,
                404,
                {"success": False, "error": "Job not found"},
                include_body=include_body,
            )
            return

        json_response(
            self,
            404,
            {"success": False, "error": "Not found"},
            include_body=include_body,
        )

    def do_HEAD(self):
        self.serve_read(include_body=False)

    def do_GET(self):
        self.serve_read()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        add_cors_headers(self)
        self.end_headers()

    def do_POST(self):
        request_path = urlparse(self.path).path
        if request_path not in {
            "/measure-loudness",
            "/run-ffmpeg-command",
            "/analyze-and-mix",
            "/sound-mixer",
            "/mux-video",
            "/mastering/analyze",
            "/mastering/master",
            "/mastering/finalize",
            "/mastering/preference",
            "/mastering/reprocess",
            "/mastering/upload",
            "/noise-reduction/voice-gate",
            "/noise-reduction/mute-non-vocal",
        }:
            json_response(self, 404, {"success": False, "error": "Not found"})
            return

        request_id = f"req-{uuid.uuid4().hex}"
        started = time.monotonic()
        payload = {}
        audio_url = None
        try:
            if request_path == "/mastering/upload":
                result = read_mastering_upload(self)
                log_event(
                    "mastering_upload_completed",
                    request_id=request_id,
                    upload_id=result["upload_id"],
                    filename=result["filename"],
                    bytes=result["bytes"],
                )
                json_response(self, 200, result)
                return

            payload = read_json_body(self)
            log_event(
                "request_received",
                request_id=request_id,
                path=request_path,
                client_ip=self.client_address[0],
            )

            if request_path == "/measure-loudness":
                audio_url = validate_audio_url(payload.get("audio_url"))
                result = measure_loudness(audio_url)
                duration = round(time.monotonic() - started, 3)
                log_event(
                    "measure_loudness_completed",
                    request_id=request_id,
                    audio_url=audio_url,
                    duration_seconds=duration,
                    input_i=result.get("input_i"),
                    input_tp=result.get("input_tp"),
                    input_lra=result.get("input_lra"),
                )
                notify_measurement_success(request_id, audio_url, result)
                json_response(self, 200, result)
                return

            if request_path == "/mastering/analyze":
                request_payload = validate_mastering_analyze_request(payload)
                with tempfile.TemporaryDirectory(prefix=f"{request_id}-mastering-analysis-") as work_dir_name:
                    work_dir = Path(work_dir_name)
                    source_path = work_dir / f"source{extension_from_url(request_payload['audio_url'])}"
                    download_file(request_payload["audio_url"], source_path)
                    analysis = analyze_mastering_file(source_path)
                log_event(
                    "mastering_analysis_completed",
                    request_id=request_id,
                    duration_seconds=round(time.monotonic() - started, 3),
                    audio_url=request_payload["audio_url"],
                )
                json_response(self, 200, {"success": True, "analysis": analysis})
                return

            if request_path == "/mastering/master":
                request_payload = validate_mastering_request(payload)
                command_id = enqueue_mastering_job(request_payload)
                log_event(
                    "mastering_job_accepted",
                    request_id=request_id,
                    command_id=command_id,
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                json_response(self, 200, {"command_id": command_id})
                return

            if request_path == "/mastering/finalize":
                request_payload = validate_mastering_finalize_request(payload)
                command_id = enqueue_mastering_final_job(request_payload)
                log_event(
                    "mastering_final_job_accepted",
                    request_id=request_id,
                    command_id=command_id,
                    source_command_id=request_payload["source_command_id"],
                    candidate_id=request_payload["candidate_id"],
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                json_response(self, 200, {"command_id": command_id})
                return

            if request_path == "/mastering/preference":
                request_payload = validate_mastering_preference_request(payload)
                result = save_mastering_preference(request_payload)
                json_response(self, 200, result)
                return

            if request_path == "/mastering/reprocess":
                request_payload = validate_mastering_reprocess_request(payload)
                result = reprocess_mastering_candidate(request_payload)
                log_event(
                    "mastering_candidate_reprocess_accepted",
                    request_id=request_id,
                    command_id=request_payload["command_id"],
                    candidate_id=request_payload["candidate_id"],
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                json_response(self, 200, result)
                return

            if request_path in {"/noise-reduction/voice-gate", "/noise-reduction/mute-non-vocal"}:
                request_payload = validate_voice_gate_request(payload)
                command_id = enqueue_voice_gate_job(request_payload)
                log_event(
                    "voice_gate_job_accepted",
                    request_id=request_id,
                    command_id=command_id,
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                json_response(self, 200, {"command_id": command_id})
                return

            if request_path == "/run-ffmpeg-command":
                request_payload = validate_ffmpeg_job_request(payload)
                command_id = enqueue_ffmpeg_job(request_payload)
                log_event(
                    "ffmpeg_job_accepted",
                    request_id=request_id,
                    command_id=command_id,
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                json_response(self, 200, {"command_id": command_id})
                return

            if request_path == "/mux-video":
                request_payload = validate_mux_video_request(payload)
                command_id = enqueue_ffmpeg_job(request_payload)
                log_event(
                    "mux_video_job_accepted",
                    request_id=request_id,
                    command_id=command_id,
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                json_response(self, 200, {"command_id": command_id})
                return

            request_payload = validate_analyze_mix_request(payload)
            command_id = enqueue_mix_job(request_payload)
            log_event(
                "mix_job_accepted",
                request_id=request_id,
                command_id=command_id,
                duration_seconds=round(time.monotonic() - started, 3),
            )
            json_response(self, 200, {"command_id": command_id})
        except ApiError as exc:
            log_event(
                "request_failed",
                request_id=request_id,
                path=request_path,
                status=exc.status,
                error=truncate(exc.message, 2000),
            )
            if request_path == "/measure-loudness":
                if isinstance(payload, dict) and not audio_url:
                    raw_audio_url = payload.get("audio_url")
                    audio_url = raw_audio_url if isinstance(raw_audio_url, str) else None
                notify_measurement_failure(request_id, exc.status, exc.message, audio_url)
            elif request_path == "/run-ffmpeg-command":
                notify_job_failure("", payload if isinstance(payload, dict) else {}, exc.message, exc.status)
            elif request_path == "/mux-video":
                notify_job_failure("", payload if isinstance(payload, dict) else {}, exc.message, exc.status)
            elif request_path in {"/analyze-and-mix", "/sound-mixer"}:
                notify_mix_failure("", payload if isinstance(payload, dict) else {}, exc.message, exc.status)
            json_response(self, exc.status, {"success": False, "error": exc.message})
        except Exception as exc:
            log_event(
                "request_failed",
                request_id=request_id,
                path=request_path,
                status=500,
                error=truncate(exc, 2000),
            )
            if request_path == "/measure-loudness":
                notify_measurement_failure(request_id, 500, "Internal server error", audio_url)
            elif request_path == "/run-ffmpeg-command":
                notify_job_failure("", payload if isinstance(payload, dict) else {}, "Internal server error", 500)
            elif request_path == "/mux-video":
                notify_job_failure("", payload if isinstance(payload, dict) else {}, "Internal server error", 500)
            elif request_path in {"/analyze-and-mix", "/sound-mixer"}:
                notify_mix_failure("", payload if isinstance(payload, dict) else {}, "Internal server error", 500)
            json_response(self, 500, {"success": False, "error": "Internal server error"})


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log_event(
        "server_started",
        host=HOST,
        port=PORT,
        log_file=str(LOG_FILE),
        notify_email_to=NOTIFY_EMAIL_TO,
        notify_email_from=NOTIFY_EMAIL_FROM,
        resend_configured=bool(RESEND_API_KEY),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
