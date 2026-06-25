from .common import *
from .audio_core import *
from .mix_analysis import *

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


