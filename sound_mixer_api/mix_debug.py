from .common import *
from .audio_core import *
from .mix_filters import *

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


