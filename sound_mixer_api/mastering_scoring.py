from .common import *
from .audio_mix import *
from .mastering_config import *
from .mastering_planner import *

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


