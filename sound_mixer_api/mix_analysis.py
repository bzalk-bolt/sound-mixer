from .common import *
from .audio_core import *
from .mix_timing import *

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


