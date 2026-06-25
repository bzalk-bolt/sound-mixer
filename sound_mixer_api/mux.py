from .common import *

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


