from .common import *
from .audio_mix import *
from .voice_gate import *
from .ffmpeg_jobs import *

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


