from .common import *
from .audio_mix import *
from .mastering_config import *

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
    from .mastering_planner import validate_mastering_plan

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
