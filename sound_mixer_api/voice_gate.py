from .common import *
from .audio_mix import *
from .ffmpeg_jobs import post_webhook_safely

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


