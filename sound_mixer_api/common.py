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


def db_to_linear(db):
    return 10 ** (float(db) / 20)


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

