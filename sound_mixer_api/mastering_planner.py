from .common import *
from .audio_mix import *
from .mastering_config import *
from .mastering_analysis import *

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


