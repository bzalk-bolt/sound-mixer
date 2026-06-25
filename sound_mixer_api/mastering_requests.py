from .common import *
from .audio_mix import *
from .voice_gate import normalize_voice_gate_options
from .mastering_config import *
from .mastering_scoring import *

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


