from .common import *
from .audio_mix import *
from .voice_gate import *
from .ffmpeg_jobs import *
from .mastering_config import *
from .mastering_analysis import *
from .mastering_planner import *
from .mastering_render import *
from .mastering_scoring import *
from .mastering_requests import *

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


