from .common import *

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

