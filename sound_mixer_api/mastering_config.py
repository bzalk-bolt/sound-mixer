from .common import *

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


