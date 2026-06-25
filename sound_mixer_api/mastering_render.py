from .common import *
from .audio_mix import *
from .mastering_config import *
from .mastering_planner import *
from .mastering_scoring import candidate_output_url, control_settings_from_plan

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


