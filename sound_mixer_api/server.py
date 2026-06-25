from .common import *
from .audio_mix import *
from .mux import *
from .ffmpeg_jobs import *
from .voice_gate import *
from .mastering import *

class Handler(BaseHTTPRequestHandler):
    server_version = "ffmpeg-sound-mixer-api/0.1"

    def log_message(self, fmt, *args):
        log_event(
            "http_access",
            client_ip=self.client_address[0],
            message=fmt % args,
        )

    def serve_read(self, include_body=True):
        request_path = urlparse(self.path).path

        if request_path == "/health":
            json_response(self, 200, {"status": "ok"}, include_body=include_body)
            return

        if request_path == "/mastering/profiles":
            json_response(
                self,
                200,
                {
                    "profiles": MASTERING_TARGET_PROFILES,
                    "candidate_grid": {
                        "styles": list(MASTERING_STYLES),
                        "loudness_levels": list(MASTERING_LOUDNESS_LEVELS),
                    },
                    "ai_planner": {
                        "provider": MASTERING_AI_PROVIDER,
                        "openai_configured": bool(OPENAI_API_KEY),
                        "openai_model": MASTERING_OPENAI_MODEL,
                    },
                },
                include_body=include_body,
            )
            return

        if request_path in {"/", "/measure-loudness.md", "/ffmpeg-api.md"}:
            try:
                markdown_response(
                    self,
                    200,
                    DOC_FILE.read_text(encoding="utf-8"),
                    include_body=include_body,
                )
            except OSError:
                json_response(
                    self,
                    404,
                    {"success": False, "error": "Document not found"},
                    include_body=include_body,
                )
            return

        if request_path == "/mastering-ui-guide.md":
            guide_file = Path("/app/mastering-ui-guide.md")
            try:
                markdown_response(
                    self,
                    200,
                    guide_file.read_text(encoding="utf-8"),
                    include_body=include_body,
                )
            except OSError:
                json_response(
                    self,
                    404,
                    {"success": False, "error": "Guide not found"},
                    include_body=include_body,
                )
            return

        if request_path == "/voice-gate-guide.md":
            guide_file = Path("/app/voice-gate-guide.md")
            try:
                markdown_response(
                    self,
                    200,
                    guide_file.read_text(encoding="utf-8"),
                    include_body=include_body,
                )
            except OSError:
                json_response(
                    self,
                    404,
                    {"success": False, "error": "Guide not found"},
                    include_body=include_body,
                )
            return

        if request_path.startswith("/tmp/"):
            parts = [unquote(part) for part in request_path.split("/") if part]
            if len(parts) == 3:
                _, command_id, filename = parts
                output_path = OUTPUT_ROOT / command_id / filename
                try:
                    resolved = output_path.resolve()
                    root = OUTPUT_ROOT.resolve()
                    if root in resolved.parents and resolved.is_file():
                        file_response(self, resolved, include_body=include_body)
                        return
                except OSError:
                    pass

            json_response(
                self,
                404,
                {"success": False, "error": "Output file not found"},
                include_body=include_body,
            )
            return

        if request_path.startswith("/jobs/"):
            parts = [part for part in request_path.split("/") if part]
            if len(parts) == 2:
                command_id = parts[1]
                with jobs_lock:
                    job = jobs.get(command_id)
                if job is None and command_id.startswith("master-"):
                    job = restore_mastering_job_from_outputs(command_id)
                if job is not None:
                    json_response(
                        self,
                        200,
                        {"command_id": command_id, **job},
                        include_body=include_body,
                    )
                    return

            json_response(
                self,
                404,
                {"success": False, "error": "Job not found"},
                include_body=include_body,
            )
            return

        json_response(
            self,
            404,
            {"success": False, "error": "Not found"},
            include_body=include_body,
        )

    def do_HEAD(self):
        self.serve_read(include_body=False)

    def do_GET(self):
        self.serve_read()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        add_cors_headers(self)
        self.end_headers()

    def do_POST(self):
        request_path = urlparse(self.path).path
        if request_path not in {
            "/measure-loudness",
            "/run-ffmpeg-command",
            "/analyze-and-mix",
            "/sound-mixer",
            "/mux-video",
            "/mastering/analyze",
            "/mastering/master",
            "/mastering/finalize",
            "/mastering/preference",
            "/mastering/reprocess",
            "/mastering/upload",
            "/noise-reduction/voice-gate",
            "/noise-reduction/mute-non-vocal",
        }:
            json_response(self, 404, {"success": False, "error": "Not found"})
            return

        request_id = f"req-{uuid.uuid4().hex}"
        started = time.monotonic()
        payload = {}
        audio_url = None
        try:
            if request_path == "/mastering/upload":
                result = read_mastering_upload(self)
                log_event(
                    "mastering_upload_completed",
                    request_id=request_id,
                    upload_id=result["upload_id"],
                    filename=result["filename"],
                    bytes=result["bytes"],
                )
                json_response(self, 200, result)
                return

            payload = read_json_body(self)
            log_event(
                "request_received",
                request_id=request_id,
                path=request_path,
                client_ip=self.client_address[0],
            )

            if request_path == "/measure-loudness":
                audio_url = validate_audio_url(payload.get("audio_url"))
                result = measure_loudness(audio_url)
                duration = round(time.monotonic() - started, 3)
                log_event(
                    "measure_loudness_completed",
                    request_id=request_id,
                    audio_url=audio_url,
                    duration_seconds=duration,
                    input_i=result.get("input_i"),
                    input_tp=result.get("input_tp"),
                    input_lra=result.get("input_lra"),
                )
                notify_measurement_success(request_id, audio_url, result)
                json_response(self, 200, result)
                return

            if request_path == "/mastering/analyze":
                request_payload = validate_mastering_analyze_request(payload)
                with tempfile.TemporaryDirectory(prefix=f"{request_id}-mastering-analysis-") as work_dir_name:
                    work_dir = Path(work_dir_name)
                    source_path = work_dir / f"source{extension_from_url(request_payload['audio_url'])}"
                    download_file(request_payload["audio_url"], source_path)
                    analysis = analyze_mastering_file(source_path)
                log_event(
                    "mastering_analysis_completed",
                    request_id=request_id,
                    duration_seconds=round(time.monotonic() - started, 3),
                    audio_url=request_payload["audio_url"],
                )
                json_response(self, 200, {"success": True, "analysis": analysis})
                return

            if request_path == "/mastering/master":
                request_payload = validate_mastering_request(payload)
                command_id = enqueue_mastering_job(request_payload)
                log_event(
                    "mastering_job_accepted",
                    request_id=request_id,
                    command_id=command_id,
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                json_response(self, 200, {"command_id": command_id})
                return

            if request_path == "/mastering/finalize":
                request_payload = validate_mastering_finalize_request(payload)
                command_id = enqueue_mastering_final_job(request_payload)
                log_event(
                    "mastering_final_job_accepted",
                    request_id=request_id,
                    command_id=command_id,
                    source_command_id=request_payload["source_command_id"],
                    candidate_id=request_payload["candidate_id"],
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                json_response(self, 200, {"command_id": command_id})
                return

            if request_path == "/mastering/preference":
                request_payload = validate_mastering_preference_request(payload)
                result = save_mastering_preference(request_payload)
                json_response(self, 200, result)
                return

            if request_path == "/mastering/reprocess":
                request_payload = validate_mastering_reprocess_request(payload)
                result = reprocess_mastering_candidate(request_payload)
                log_event(
                    "mastering_candidate_reprocess_accepted",
                    request_id=request_id,
                    command_id=request_payload["command_id"],
                    candidate_id=request_payload["candidate_id"],
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                json_response(self, 200, result)
                return

            if request_path in {"/noise-reduction/voice-gate", "/noise-reduction/mute-non-vocal"}:
                request_payload = validate_voice_gate_request(payload)
                command_id = enqueue_voice_gate_job(request_payload)
                log_event(
                    "voice_gate_job_accepted",
                    request_id=request_id,
                    command_id=command_id,
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                json_response(self, 200, {"command_id": command_id})
                return

            if request_path == "/run-ffmpeg-command":
                request_payload = validate_ffmpeg_job_request(payload)
                command_id = enqueue_ffmpeg_job(request_payload)
                log_event(
                    "ffmpeg_job_accepted",
                    request_id=request_id,
                    command_id=command_id,
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                json_response(self, 200, {"command_id": command_id})
                return

            if request_path == "/mux-video":
                request_payload = validate_mux_video_request(payload)
                command_id = enqueue_ffmpeg_job(request_payload)
                log_event(
                    "mux_video_job_accepted",
                    request_id=request_id,
                    command_id=command_id,
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                json_response(self, 200, {"command_id": command_id})
                return

            request_payload = validate_analyze_mix_request(payload)
            command_id = enqueue_mix_job(request_payload)
            log_event(
                "mix_job_accepted",
                request_id=request_id,
                command_id=command_id,
                duration_seconds=round(time.monotonic() - started, 3),
            )
            json_response(self, 200, {"command_id": command_id})
        except ApiError as exc:
            log_event(
                "request_failed",
                request_id=request_id,
                path=request_path,
                status=exc.status,
                error=truncate(exc.message, 2000),
            )
            if request_path == "/measure-loudness":
                if isinstance(payload, dict) and not audio_url:
                    raw_audio_url = payload.get("audio_url")
                    audio_url = raw_audio_url if isinstance(raw_audio_url, str) else None
                notify_measurement_failure(request_id, exc.status, exc.message, audio_url)
            elif request_path == "/run-ffmpeg-command":
                notify_job_failure("", payload if isinstance(payload, dict) else {}, exc.message, exc.status)
            elif request_path == "/mux-video":
                notify_job_failure("", payload if isinstance(payload, dict) else {}, exc.message, exc.status)
            elif request_path in {"/analyze-and-mix", "/sound-mixer"}:
                notify_mix_failure("", payload if isinstance(payload, dict) else {}, exc.message, exc.status)
            json_response(self, exc.status, {"success": False, "error": exc.message})
        except Exception as exc:
            log_event(
                "request_failed",
                request_id=request_id,
                path=request_path,
                status=500,
                error=truncate(exc, 2000),
            )
            if request_path == "/measure-loudness":
                notify_measurement_failure(request_id, 500, "Internal server error", audio_url)
            elif request_path == "/run-ffmpeg-command":
                notify_job_failure("", payload if isinstance(payload, dict) else {}, "Internal server error", 500)
            elif request_path == "/mux-video":
                notify_job_failure("", payload if isinstance(payload, dict) else {}, "Internal server error", 500)
            elif request_path in {"/analyze-and-mix", "/sound-mixer"}:
                notify_mix_failure("", payload if isinstance(payload, dict) else {}, "Internal server error", 500)
            json_response(self, 500, {"success": False, "error": "Internal server error"})


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log_event(
        "server_started",
        host=HOST,
        port=PORT,
        log_file=str(LOG_FILE),
        notify_email_to=NOTIFY_EMAIL_TO,
        notify_email_from=NOTIFY_EMAIL_FROM,
        resend_configured=bool(RESEND_API_KEY),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
