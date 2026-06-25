from .common import *
from .audio_core import *

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


