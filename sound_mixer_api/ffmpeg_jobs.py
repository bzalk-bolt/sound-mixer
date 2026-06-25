from .common import *
from .audio_mix import *
from .mux import *

def replace_placeholders(command, paths):
    def replace(match):
        label = match.group(1)
        try:
            return str(paths[label])
        except KeyError as exc:
            raise RuntimeError(f"Unknown placeholder {label}") from exc

    return PLACEHOLDER_RE.sub(replace, command)


def is_allowed_local_path(arg, allowed_roots):
    if not arg.startswith("/"):
        return True

    try:
        resolved = Path(arg).resolve()
    except OSError:
        return False

    return any(resolved == root or root in resolved.parents for root in allowed_roots)


def build_ffmpeg_args(command, input_paths, output_paths):
    paths = {**input_paths, **output_paths}
    command = command.replace(":LR=", ":LRA=")
    substituted = replace_placeholders(command, paths)
    try:
        tokens = shlex.split(substituted)
    except ValueError as exc:
        raise RuntimeError(f"Invalid ffmpeg_command: {exc}") from exc

    if tokens and tokens[0] == "ffmpeg":
        tokens = tokens[1:]

    if not tokens:
        raise RuntimeError("ffmpeg_command produced no arguments")

    allowed_roots = {path.parent.resolve() for path in paths.values()}
    for token in tokens:
        if "://" in token:
            raise RuntimeError("ffmpeg_command must use input placeholders, not URLs")
        if not is_allowed_local_path(token, allowed_roots):
            raise RuntimeError(f"ffmpeg_command references disallowed path: {token}")

    return ["ffmpeg", "-y", *tokens]


def post_webhook(url, payload):
    body_text = json.dumps(json_safe(payload), allow_nan=False)
    body = body_text.encode("utf-8")
    log_event(
        "webhook_post_started",
        webhook_url=url,
        payload=truncate(body_text, 5000),
    )
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
        body = response.read().decode("utf-8", errors="replace")
        log_event(
            "webhook_posted",
            webhook_url=url,
            status=response.status,
            response=truncate(body, 1000),
        )
        return {
            "ok": True,
            "status": response.status,
            "response": truncate(body, 1000),
        }


def post_webhook_safely(url, payload, event_prefix="webhook"):
    try:
        return post_webhook(url, payload)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        result = {
            "ok": False,
            "status": exc.code,
            "error": str(exc),
            "response": truncate(body, 2000),
        }
        log_event(
            f"{event_prefix}_post_failed",
            webhook_url=url,
            status=exc.code,
            error=truncate(exc, 1000),
            response=truncate(body, 2000),
        )
        return result
    except Exception as exc:
        result = {
            "ok": False,
            "status": None,
            "error": str(exc),
            "response": "",
        }
        log_event(
            f"{event_prefix}_post_failed",
            webhook_url=url,
            error=truncate(exc, 1000),
        )
        return result


def webhook_payload(status, request_payload, output_urls=None, error_message=None):
    data = {
        "status": status,
        "output_files": output_urls or {},
        "original_request": {"output_files": request_payload["output_files"]},
        "webhook_metadata": request_payload["webhook_metadata"],
    }
    if status == "FAILED":
        data["error_message"] = error_message or "FFmpeg processing failed"
        data["error_status"] = "ffmpeg_error"
    return {"data": data}


def mix_webhook_payload(
    status,
    request_payload,
    output_urls=None,
    analysis=None,
    decisions=None,
    debug_artifacts=None,
    debug_error="",
    error_message=None,
):
    data = {
        "status": status,
        "output_files": output_urls or {},
        "original_request": {"output_files": request_payload["output_files"]},
        "webhook_metadata": request_payload.get("webhook_metadata", {}),
    }
    if analysis is not None:
        data["analysis"] = analysis
    if decisions is not None:
        data["mix_decisions"] = decisions
    if debug_artifacts is not None:
        data["debug_artifacts"] = debug_artifacts
    if debug_error:
        data["debug_error"] = debug_error
    if status == "FAILED":
        data["error_message"] = error_message or "Analyze and mix failed"
        data["error_status"] = "mix_error"
    return {"data": data}




def run_ffmpeg_job(command_id, request_payload):
    set_job_status(command_id, status="RUNNING")
    started = time.monotonic()
    log_event(
        "ffmpeg_job_started",
        command_id=command_id,
        input_files=request_payload["input_files"],
        output_files=request_payload["output_files"],
        webhook_metadata=request_payload["webhook_metadata"],
    )
    output_dir = OUTPUT_ROOT / command_id
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"{command_id}-") as work_dir_name:
        work_dir = Path(work_dir_name)
        try:
            cleanup_old_outputs()

            input_paths = {}
            for label, url in request_payload["input_files"].items():
                destination = work_dir / f"{label}{extension_from_url(url)}"
                log_event("ffmpeg_input_download_started", command_id=command_id, label=label, url=url)
                download_file(url, destination)
                log_event(
                    "ffmpeg_input_downloaded",
                    command_id=command_id,
                    label=label,
                    bytes=destination.stat().st_size,
                )
                input_paths[label] = destination

            output_paths = {
                label: output_dir / filename
                for label, filename in request_payload["output_files"].items()
            }
            args = build_ffmpeg_args(
                request_payload["ffmpeg_command"], input_paths, output_paths
            )
            log_event(
                "ffmpeg_command_started",
                command_id=command_id,
                args=[truncate(arg, 500) for arg in args],
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
                    "ffmpeg_command_failed",
                    command_id=command_id,
                    returncode=completed.returncode,
                    stderr=truncate(stderr, 4000),
                )
                raise RuntimeError(stderr[-2000:] or "FFmpeg exited with an error")

            output_urls = {}
            for label, path in output_paths.items():
                if not path.exists() or path.stat().st_size == 0:
                    raise RuntimeError(f"Expected output file was not created: {label}")
                filename = quote(path.name)
                output_urls[label] = {
                    "storage_url": f"{PUBLIC_BASE_URL}/tmp/{command_id}/{filename}"
                }

            webhook_status = (
                "SUCCESS"
                if request_payload.get("job_type") == "mux-video"
                else "COMPLETED"
            )
            payload = webhook_payload(webhook_status, request_payload, output_urls)
            post_webhook(request_payload["webhook_url"], payload)
            set_job_status(command_id, status="COMPLETED", output_files=output_urls)
            duration = round(time.monotonic() - started, 3)
            log_event(
                "ffmpeg_job_completed",
                command_id=command_id,
                duration_seconds=duration,
                output_files=output_urls,
            )
            notify_job_success(command_id, request_payload, output_urls)
        except Exception as exc:
            message = str(exc) or "FFmpeg processing failed"
            duration = round(time.monotonic() - started, 3)
            log_event(
                "ffmpeg_job_failed",
                command_id=command_id,
                duration_seconds=duration,
                error=truncate(message, 4000),
            )
            payload = webhook_payload(
                "FAILED", request_payload, output_urls={}, error_message=message
            )
            try:
                post_webhook(request_payload["webhook_url"], payload)
            except Exception as webhook_exc:
                log_event(
                    "webhook_failure_post_failed",
                    command_id=command_id,
                    error=truncate(webhook_exc, 2000),
                )
            set_job_status(command_id, status="FAILED", error_message=message)
            notify_job_failure(command_id, request_payload, message)


def run_mux_video_job(command_id, request_payload):
    set_job_status(command_id, status="RUNNING")
    started = time.monotonic()
    log_event(
        "ffmpeg_job_started",
        command_id=command_id,
        input_files=request_payload["input_files"],
        output_files=request_payload["output_files"],
        webhook_metadata=request_payload["webhook_metadata"],
    )
    output_dir = OUTPUT_ROOT / command_id
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"{command_id}-") as work_dir_name:
        work_dir = Path(work_dir_name)
        try:
            cleanup_old_outputs()

            input_paths = {}
            for label, url in request_payload["input_files"].items():
                destination = work_dir / f"{label}{extension_from_url(url)}"
                log_event("ffmpeg_input_download_started", command_id=command_id, label=label, url=url)
                download_file(url, destination)
                log_event(
                    "ffmpeg_input_downloaded",
                    command_id=command_id,
                    label=label,
                    bytes=destination.stat().st_size,
                )
                input_paths[label] = destination

            output_paths = {
                label: output_dir / filename
                for label, filename in request_payload["output_files"].items()
            }
            output_path = output_paths["out_1"]
            burn_logo = request_payload.get("burn_logo", True)
            video_suffix = input_paths["in_1"].suffix.lower()
            if burn_logo:
                synced_suffix = ".webm" if video_suffix == ".webm" else ".mp4"
                synced_path = work_dir / f"{command_id}-synced{synced_suffix}"
            else:
                synced_path = output_path

            audio_delay_seconds = request_payload.get("audio_delay_seconds", 0.0)
            if request_payload.get("auto_audio_sync", True):
                audio_delay_seconds += estimate_mux_audio_delay(
                    command_id,
                    input_paths["in_1"],
                    input_paths["in_2"],
                    work_dir,
                )
            log_event(
                "mux_audio_sync_applied",
                command_id=command_id,
                audio_delay_seconds=round(audio_delay_seconds, 3),
            )

            run_logged_ffmpeg(
                command_id,
                "mux_sync",
                mux_stage_one_args(
                    input_paths["in_1"],
                    input_paths["in_2"],
                    synced_path,
                    audio_delay_seconds=audio_delay_seconds,
                ),
            )
            if not synced_path.exists() or synced_path.stat().st_size == 0:
                raise RuntimeError("Expected synced mux file was not created")

            if burn_logo:
                run_logged_ffmpeg(
                    command_id,
                    "logo_burn",
                    mux_stage_two_args(
                        synced_path,
                        input_paths["in_3"],
                        output_path,
                        logo_height_ratio=request_payload.get("logo_height_ratio", 0.2),
                        logo_margin_ratio=request_payload.get("logo_margin_ratio", 0.03),
                    ),
                )

            output_urls = {}
            for label, path in output_paths.items():
                if not path.exists() or path.stat().st_size == 0:
                    raise RuntimeError(f"Expected output file was not created: {label}")
                output_urls[label] = {"storage_url": public_output_url(command_id, path)}

            payload = webhook_payload("SUCCESS", request_payload, output_urls)
            post_webhook(request_payload["webhook_url"], payload)
            set_job_status(command_id, status="COMPLETED", output_files=output_urls)
            duration = round(time.monotonic() - started, 3)
            log_event(
                "ffmpeg_job_completed",
                command_id=command_id,
                duration_seconds=duration,
                output_files=output_urls,
            )
            notify_job_success(command_id, request_payload, output_urls)
        except Exception as exc:
            message = str(exc) or "FFmpeg processing failed"
            duration = round(time.monotonic() - started, 3)
            log_event(
                "ffmpeg_job_failed",
                command_id=command_id,
                duration_seconds=duration,
                error=truncate(message, 4000),
            )
            payload = webhook_payload(
                "FAILED", request_payload, output_urls={}, error_message=message
            )
            try:
                post_webhook(request_payload["webhook_url"], payload)
            except Exception as webhook_exc:
                log_event(
                    "webhook_failure_post_failed",
                    command_id=command_id,
                    error=truncate(webhook_exc, 2000),
                )
            set_job_status(command_id, status="FAILED", error_message=message)
            notify_job_failure(command_id, request_payload, message)


def run_analyze_mix_job(command_id, request_payload):
    set_job_status(command_id, status="RUNNING")
    started = time.monotonic()
    log_event(
        "mix_job_started",
        command_id=command_id,
        backing_url=request_payload["backing_url"],
        vocal_url=request_payload["vocal_url"],
        reference_vocal_url=request_payload.get("reference_vocal_url", ""),
        output_files=request_payload["output_files"],
        webhook_metadata=request_payload["webhook_metadata"],
    )
    output_dir = OUTPUT_ROOT / command_id
    output_dir.mkdir(parents=True, exist_ok=True)

    analysis = None
    decisions = None
    debug_output_urls = {}
    debug_artifacts = {}
    debug_error = ""
    with tempfile.TemporaryDirectory(prefix=f"{command_id}-") as work_dir_name:
        work_dir = Path(work_dir_name)
        try:
            cleanup_old_outputs()
            backing_path = work_dir / f"backing{extension_from_url(request_payload['backing_url'])}"
            vocal_path = work_dir / f"vocal{extension_from_url(request_payload['vocal_url'])}"
            reference_vocal_path = None
            if request_payload.get("reference_vocal_url"):
                reference_vocal_path = work_dir / (
                    f"reference_vocal{extension_from_url(request_payload['reference_vocal_url'])}"
                )

            log_event("mix_input_download_started", command_id=command_id, label="backing")
            download_file(request_payload["backing_url"], backing_path)
            log_event(
                "mix_input_downloaded",
                command_id=command_id,
                label="backing",
                bytes=backing_path.stat().st_size,
            )
            log_event("mix_input_download_started", command_id=command_id, label="vocal")
            download_file(request_payload["vocal_url"], vocal_path)
            log_event(
                "mix_input_downloaded",
                command_id=command_id,
                label="vocal",
                bytes=vocal_path.stat().st_size,
            )
            if reference_vocal_path is not None:
                log_event(
                    "mix_input_download_started",
                    command_id=command_id,
                    label="reference_vocal",
                )
                download_file(request_payload["reference_vocal_url"], reference_vocal_path)
                log_event(
                    "mix_input_downloaded",
                    command_id=command_id,
                    label="reference_vocal",
                    bytes=reference_vocal_path.stat().st_size,
                )

            analysis = analyze_audio_pair(backing_path, vocal_path)
            if reference_vocal_path is not None:
                timing_alignment = estimate_vocal_timing(
                    reference_vocal_path,
                    vocal_path,
                    analysis["vocal"]["channel_detection"]["needs_centering"],
                    request_payload.get("mix_options", {}),
                )
                analysis["timing_alignment"] = timing_alignment
                log_event(
                    "mix_timing_alignment_completed",
                    command_id=command_id,
                    detected_vocal_delay_ms=timing_alignment.get("detected_vocal_delay_ms"),
                    applied_vocal_shift_ms=timing_alignment.get("applied_vocal_shift_ms"),
                    confidence=timing_alignment.get("confidence"),
                    method=timing_alignment.get("method"),
                    best_score=timing_alignment.get("best_score"),
                    comparison_score=timing_alignment.get("comparison_score"),
                    adjacent_score=timing_alignment.get("adjacent_score"),
                    applied=timing_alignment.get("applied"),
                    reason=timing_alignment.get("reason"),
                )
            else:
                analysis["timing_alignment"] = {
                    "enabled": False,
                    "applied": False,
                    "detected_vocal_delay_ms": 0,
                    "applied_vocal_shift_ms": 0,
                    "confidence": 0,
                    "reason": "reference_vocal_url not provided",
                }
            decisions = choose_mix_decisions(analysis, request_payload.get("mix_options", {}))
            decisions = apply_vocal_second_pass_leveling(
                command_id,
                backing_path,
                vocal_path,
                work_dir,
                decisions,
            )
            log_event(
                "mix_analysis_completed",
                command_id=command_id,
                backing_lufs=analysis["backing"]["loudnorm"]["input_i"],
                vocal_lufs=analysis["vocal"]["loudnorm"]["input_i"],
                vocal_channel_mode=decisions["vocal_channel_mode"],
                vocal_needs_centering=decisions["vocal_needs_centering"],
                timing_alignment_applied=decisions["timing_alignment_applied"],
                detected_vocal_delay_ms=decisions["detected_vocal_delay_ms"],
                applied_vocal_shift_ms=decisions["applied_vocal_shift_ms"],
                vocal_second_pass_enabled=decisions["vocal_second_pass_enabled"],
                vocal_second_pass_target_over_backing_db=decisions[
                    "vocal_second_pass_target_over_backing_db"
                ],
                vocal_second_pass_gain_db=decisions["vocal_second_pass_gain_db"],
                vocal_second_pass_backing_active_rms_p75_db=decisions[
                    "vocal_second_pass_backing_active_rms_p75_db"
                ],
                vocal_second_pass_vocal_active_rms_p75_db=decisions[
                    "vocal_second_pass_vocal_active_rms_p75_db"
                ],
                vocal_polish_enabled=decisions["vocal_polish_enabled"],
                vocal_highpass_hz=decisions["vocal_highpass_hz"],
                vocal_deesser_intensity=decisions["vocal_deesser_intensity"],
                vocal_plate_reverb_enabled=decisions["vocal_plate_reverb_enabled"],
                vocal_short_delay_enabled=decisions["vocal_short_delay_enabled"],
                applied_vocal_shift_direction=describe_vocal_shift(
                    decisions["applied_vocal_shift_ms"]
                ),
            )

            try:
                debug_artifacts = create_stage_debug_artifacts(
                    command_id,
                    backing_path,
                    vocal_path,
                    output_dir,
                    work_dir,
                    decisions,
                )
            except Exception as debug_exc:
                debug_error = str(debug_exc) or "Stage debug artifact render failed"
                debug_artifacts = {
                    "enabled": decisions.get("debug_stage_artifacts_enabled", True),
                    "kind": "vocal_stage_feedback",
                    "error": debug_error,
                    "stages": [],
                }
                set_job_status(command_id, debug_artifacts=debug_artifacts, debug_error=debug_error)
                log_event(
                    "mix_stage_debug_failed",
                    command_id=command_id,
                    error=truncate(debug_error, 4000),
                )

            output_path = output_dir / request_payload["output_files"]["out_1"]
            filter_complex = build_mix_filter(decisions)
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
                "[out]",
                *encode_args_for_output(output_path),
                str(output_path),
            ]
            log_event(
                "mix_command_started",
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
                    "mix_command_failed",
                    command_id=command_id,
                    returncode=completed.returncode,
                    stderr=truncate(stderr, 4000),
                )
                raise RuntimeError(stderr[-2000:] or "FFmpeg mix exited with an error")

            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError("Expected mixed output file was not created")

            output_urls = {
                "out_1": {
                    "storage_url": public_output_url(command_id, output_path)
                }
            }
            if reference_vocal_path is not None:
                try:
                    debug_output_urls = create_reference_debug_mix(
                        command_id,
                        backing_path,
                        reference_vocal_path,
                        output_dir,
                    )
                    if debug_artifacts:
                        debug_artifacts["reference"] = debug_output_urls
                    else:
                        debug_artifacts = {"reference": debug_output_urls}
                except Exception as debug_exc:
                    reference_debug_error = str(debug_exc) or "Reference debug mix failed"
                    debug_error = (
                        f"{debug_error}; {reference_debug_error}"
                        if debug_error
                        else reference_debug_error
                    )
                    log_event(
                        "reference_debug_mix_failed",
                        command_id=command_id,
                        error=truncate(reference_debug_error, 4000),
                    )

            webhook_result = None
            if request_payload.get("webhook_url"):
                webhook_result = post_webhook_safely(
                    request_payload["webhook_url"],
                    mix_webhook_payload(
                        "COMPLETED",
                        request_payload,
                        output_urls=output_urls,
                        analysis=analysis,
                        decisions=decisions,
                        debug_artifacts=debug_artifacts,
                        debug_error=debug_error,
                    ),
                    event_prefix="mix_webhook",
                )
                request_payload["_webhook_result"] = webhook_result

            duration = round(time.monotonic() - started, 3)
            set_job_status(
                command_id,
                status="COMPLETED",
                output_files=output_urls,
                analysis=analysis,
                mix_decisions=decisions,
                debug_artifacts=debug_artifacts,
                debug_error=debug_error,
                webhook_result=webhook_result,
            )
            log_event(
                "mix_job_completed",
                command_id=command_id,
                duration_seconds=duration,
                output_files=output_urls,
                debug_output_files=debug_output_urls,
                debug_artifacts=debug_artifacts,
                debug_error=debug_error,
                webhook_result=webhook_result,
            )
            notify_mix_success(
                command_id,
                request_payload,
                output_urls,
                analysis,
                decisions,
                debug_artifacts,
                debug_error,
            )
        except Exception as exc:
            message = str(exc) or "Analyze and mix failed"
            duration = round(time.monotonic() - started, 3)
            log_event(
                "mix_job_failed",
                command_id=command_id,
                duration_seconds=duration,
                error=truncate(message, 4000),
            )
            if request_payload.get("webhook_url"):
                post_webhook_safely(
                    request_payload["webhook_url"],
                    mix_webhook_payload(
                        "FAILED",
                        request_payload,
                        output_urls={},
                        analysis=analysis,
                        decisions=decisions,
                        debug_artifacts=debug_artifacts,
                        debug_error=debug_error,
                        error_message=message,
                    ),
                    event_prefix="mix_webhook_failure",
                )
            set_job_status(
                command_id,
                status="FAILED",
                error_message=message,
                debug_artifacts=debug_artifacts,
                debug_error=debug_error,
            )
            notify_mix_failure(command_id, request_payload, message)


def enqueue_ffmpeg_job(request_payload):
    cleanup_old_outputs()
    command_id = f"job-{uuid.uuid4().hex}"
    with jobs_lock:
        jobs[command_id] = {"status": "QUEUED", "created_at": time.time()}
    log_event(
        "ffmpeg_job_queued",
        command_id=command_id,
        input_files=request_payload["input_files"],
        output_files=request_payload["output_files"],
        webhook_metadata=request_payload["webhook_metadata"],
    )
    if request_payload.get("job_type") == "mux-video":
        job_executor.submit(run_mux_video_job, command_id, request_payload)
    else:
        job_executor.submit(run_ffmpeg_job, command_id, request_payload)
    return command_id


def enqueue_mix_job(request_payload):
    cleanup_old_outputs()
    command_id = f"mix-{uuid.uuid4().hex}"
    with jobs_lock:
        jobs[command_id] = {"status": "QUEUED", "created_at": time.time()}
    log_event(
        "mix_job_queued",
        command_id=command_id,
        backing_url=request_payload["backing_url"],
        vocal_url=request_payload["vocal_url"],
        reference_vocal_url=request_payload.get("reference_vocal_url", ""),
        output_files=request_payload["output_files"],
        webhook_metadata=request_payload["webhook_metadata"],
    )
    job_executor.submit(run_analyze_mix_job, command_id, request_payload)
    return command_id


