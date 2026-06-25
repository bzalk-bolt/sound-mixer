# Sound Mixer AI Mastering API - UI Integration Guide

Public base URL:

```text
https://sound-mixer-api.jamrockdev.com
```

This guide is for frontend/UI builders integrating the AI mastering workflow.
The frontend does not call OpenAI directly. It only calls this API. The backend
analyzes audio, asks the configured AI planner for a constrained mastering plan,
renders candidates with FFmpeg, scores them, and returns preview URLs.

## Product Flow

Recommended UI flow:

1. User uploads or selects a mix.
2. UI uploads the file to `POST /mastering/upload`.
3. API returns a temporary public `audio_url`.
4. UI calls `POST /mastering/master` with that `audio_url`.
4. UI polls `GET /jobs/{command_id}` until the job completes.
5. UI shows the top 3 `recommended_candidates`.
6. User previews and selects one candidate.
7. UI calls `POST /mastering/preference` to save the user choice.
8. UI calls `POST /mastering/finalize` with the selected `candidate_id`.
9. UI polls `GET /jobs/{final_command_id}` until final WAV/MP3 URL is ready.
10. UI lets the user download or save the final master.

## Browser Access

The API supports browser CORS requests.

Allowed methods:

```text
GET, POST, HEAD, OPTIONS
```

Allowed request headers:

```text
Content-Type, X-API-KEY
```

No API key is currently required. If `LOUDNESS_API_KEY` is enabled later, send:

```http
X-API-KEY: your-api-key
```

## Source File Requirements

`audio_url` must be a public `http` or `https` URL reachable by the backend.
Do not send a browser `blob:` URL to `/mastering/master`; the backend cannot
fetch browser-only object URLs.

Recommended input formats:

- WAV
- AIFF
- FLAC
- MP3
- M4A
- AAC

Best UX recommendation:

- Upload the source mix from the browser to `POST /mastering/upload`.
- Use the returned `audio_url` when starting mastering.
- Then send that URL to this API.

## Upload A Browser File

Use this endpoint when the user selects a local file in the browser.

```http
POST /mastering/upload
Content-Type: audio/wav
X-Filename: my-mix.wav
```

Request body:

```text
raw audio file bytes
```

Response:

```json
{
  "success": true,
  "upload_id": "upload-abc123",
  "filename": "my-mix.wav",
  "bytes": 12345678,
  "audio_url": "https://sound-mixer-api.jamrockdev.com/tmp/upload-abc123/my-mix.wav",
  "expires_in_seconds": 7200
}
```

Use `audio_url` in `POST /mastering/master`.

## Discover Profiles

Use this to populate profile selectors or show system capabilities.

```http
GET /mastering/profiles
```

Example response shape:

```json
{
  "profiles": {
    "modern_pop_streaming": {
      "profile": "modern_pop_streaming",
      "target_lufs": -10.5,
      "target_true_peak": -1.0,
      "target_lra": 5.5
    }
  },
  "candidate_grid": {
    "styles": ["warm", "balanced", "open"],
    "loudness_levels": ["conservative", "standard", "loud"]
  },
  "ai_planner": {
    "provider": "auto",
    "openai_configured": true,
    "openai_model": "gpt-5.5"
  }
}
```

Supported profile IDs:

- `modern_pop_streaming`
- `hiphop_loud`
- `acoustic_natural`
- `rock_punchy`
- `edm_loud_clean`
- `country_radio`
- `podcast_voice`
- `warm_vintage`
- `open_bright`

The UI can either let the user choose a profile or omit `profile` and let the
backend choose based on analysis.

## Optional Analysis-Only Step

Use this if you want to show analysis before rendering candidates.

```http
POST /mastering/analyze
Content-Type: application/json
```

Request:

```json
{
  "audio_url": "https://example.com/uploads/song-mix.wav"
}
```

Response:

```json
{
  "success": true,
  "analysis": {
    "loudness": {
      "integrated_lufs": -18.4,
      "short_term_lufs_max": null,
      "lra": 8.1,
      "true_peak_db": -3.4,
      "crest_factor": 13.2
    },
    "spectrum": {
      "sub_20_60": -18.2,
      "bass_60_120": -14.6,
      "low_mid_120_300": -11.8,
      "mid_300_1000": -13.2,
      "presence_1000_4000": -16.1,
      "harsh_4000_8000": -12.0,
      "air_8000_16000": -20.3
    },
    "stereo": {
      "width_score": 0.71,
      "mono_correlation": 0.38,
      "low_end_width": 0.44
    },
    "dynamics": {
      "transient_density": 0.62,
      "compression_guess": "under_compressed",
      "clipping_detected": false
    },
    "classification": {
      "genre_guess": "pop",
      "vocal_prominence": "low",
      "mix_quality": "needs_targeted_mastering",
      "mastering_goal": "streaming_loud_modern",
      "mix_problem_tags": ["vocal_buried", "low_mid_muddy"],
      "already_mastered": false
    }
  }
}
```

## Start Mastering Candidate Job

```http
POST /mastering/master
Content-Type: application/json
```

Request:

```json
{
  "audio_url": "https://example.com/uploads/song-mix.wav",
  "reference_url": "https://example.com/references/reference-master.wav",
  "profile": "modern_pop_streaming",
  "planner": "auto",
  "user_goal": "balanced streaming master with clear vocals",
  "preview_seconds": 75,
  "output_filename": "FINAL_MASTER.wav",
  "webhook_metadata": {
    "track_id": "track_123",
    "user_id": "user_456"
  }
}
```

Required:

- `audio_url`

Optional:

- `reference_url`: a reference master to match.
- `profile`: target profile ID.
- `planner`: `auto`, `openai`, or `rule`.
- `user_goal`: user-facing intent text, such as "warmer" or "louder for streaming".
- `preview_seconds`: 20 to 180. Default is 75.
- `output_filename`: final filename used later as a default.
- `webhook_metadata`: passthrough object stored with job data.

Planner modes:

- `auto`: use OpenAI when configured, otherwise deterministic fallback.
- `openai`: require OpenAI. Job fails if AI planner fails.
- `rule`: bypass AI and use deterministic planner.

Normal UI should use:

```json
{
  "planner": "auto"
}
```

Immediate response:

```json
{
  "command_id": "master-abc123"
}
```

Store this `command_id` in frontend state and poll job status.

## Poll Mastering Job

```http
GET /jobs/master-abc123
```

Queued/running response:

```json
{
  "command_id": "master-abc123",
  "status": "RUNNING",
  "created_at": 1781720000.0
}
```

Completed response includes:

```json
{
  "command_id": "master-abc123",
  "status": "COMPLETED",
  "source_analysis": {},
  "reference_analysis": null,
  "target_profile": {},
  "planner_result": {
    "provider": "openai",
    "model": "gpt-5.5",
    "status": "completed"
  },
  "candidate_scores": [],
  "recommended_candidates": [
    {
      "candidate_id": "balanced_standard",
      "style": "balanced",
      "loudness": "standard",
      "score": 86.4,
      "preview_file": {
        "storage_url": "https://sound-mixer-api.jamrockdev.com/tmp/master-abc123/balanced_standard.mp3"
      },
      "plan": {},
      "post_analysis": {},
      "score_breakdown": {
        "score": 86.4,
        "spectral_distance_from_target": 0.18,
        "harshness_score": 0.12,
        "overcompression_risk": "low"
      }
    }
  ],
  "processing_log": [
    {
      "ts": "2026-06-17T22:00:00Z",
      "event": "mastering_source_analysis_completed",
      "analysis": {
        "integrated_lufs": -18.4,
        "true_peak_db": -3.4,
        "width_score": 0.0,
        "mono_correlation": 1.0,
        "effective_mono": true,
        "stereo_assessment": "dual_mono"
      }
    },
    {
      "ts": "2026-06-17T22:00:20Z",
      "event": "mastering_candidate_scored",
      "candidate_id": "balanced_standard",
      "score_breakdown": {
        "score": 86.4
      }
    }
  ],
  "output_filename": "FINAL_MASTER.wav"
}
```

Failed response:

```json
{
  "command_id": "master-abc123",
  "status": "FAILED",
  "error_message": "Description of what failed"
}
```

Polling recommendation:

- Poll every 2 to 5 seconds.
- Show progress states as `Queued`, `Analyzing`, `Rendering previews`, `Scoring`, even though the backend currently returns `RUNNING`.
- Stop polling on `COMPLETED` or `FAILED`.

## Presenting Preview Candidates

Use `recommended_candidates`, not the full `candidate_scores`, for the main UI.

Recommended UI card fields:

- Candidate label: derive from `style` and `loudness`.
- Score: `score`.
- Audio preview URL: `preview_file.storage_url`.
- Rendered preview metrics: `post_analysis.loudness.integrated_lufs`,
  `post_analysis.loudness.true_peak_db`,
  `post_analysis.stereo.width_score`,
  `post_analysis.stereo.mono_correlation`, and
  `post_analysis.stereo.stereo_assessment`.
- Descriptive tags:
  - `warm`
  - `balanced`
  - `open`
  - `conservative`
  - `standard`
  - `loud`

Suggested labels:

```text
balanced_standard -> Balanced
open_standard -> Open / Bright
warm_conservative -> Warm / Natural
balanced_loud -> Balanced / Loud
```

Do not expose the full `ffmpeg_filtergraph` to normal users. It is useful for
admin/debug views only.

## Save User Preference

Call this when the user chooses a candidate. This creates preference data for
future model training.

```http
POST /mastering/preference
Content-Type: application/json
```

Request:

```json
{
  "command_id": "master-abc123",
  "winner_candidate_id": "balanced_standard",
  "compared_candidate_ids": [
    "balanced_standard",
    "open_standard",
    "warm_conservative"
  ],
  "user_metadata": {
    "track_id": "track_123",
    "user_id": "user_456"
  }
}
```

Response:

```json
{
  "success": true
}
```

This endpoint does not render audio. It only records the user choice.

## Finalize Selected Master

After the user selects a candidate, render the full-length output:

```http
POST /mastering/finalize
Content-Type: application/json
```

Request:

```json
{
  "command_id": "master-abc123",
  "candidate_id": "balanced_standard",
  "output_filename": "FINAL_MASTER_track_123.wav"
}
```

Immediate response:

```json
{
  "command_id": "final-def456"
}
```

Poll:

```http
GET /jobs/final-def456
```

Completed final response:

```json
{
  "command_id": "final-def456",
  "status": "COMPLETED",
  "source_command_id": "master-abc123",
  "selected_candidate": "balanced_standard",
  "final_outputs": {
    "out_1": {
      "storage_url": "https://sound-mixer-api.jamrockdev.com/tmp/final-def456/FINAL_MASTER_track_123.wav"
    }
  },
  "final_analysis": {},
  "ffmpeg_filtergraph": "..."
}
```

Use `final_outputs.out_1.storage_url` as the download URL.

## Output URL Lifetime

Generated preview and final files are temporary.

Current retention:

```text
2 hours
```

Frontend should either:

- Prompt the user to download immediately.
- Or have your backend copy the `storage_url` into permanent storage.

## Reference Matching UX

If you support reference tracks, the UI can offer:

- Upload reference track
- Choose from preset references
- Skip reference matching

When `reference_url` is provided, the backend adapts the target profile toward
the reference track's measured loudness, spectrum, dynamics, and stereo width.

Good UI copy:

```text
Use a reference track if you want this master to move toward a specific sound.
```

## Suggested Frontend State Machine

```text
idle
uploading_source
source_ready
starting_mastering
mastering_running
previews_ready
saving_preference
finalizing
final_ready
failed
```

Minimum state object:

```json
{
  "sourceUrl": "",
  "referenceUrl": "",
  "masterCommandId": "",
  "finalCommandId": "",
  "status": "idle",
  "recommendedCandidates": [],
  "selectedCandidateId": "",
  "finalDownloadUrl": "",
  "errorMessage": ""
}
```

## JavaScript Example

```js
const API_BASE = "https://sound-mixer-api.jamrockdev.com";

async function startMastering({ audioUrl, referenceUrl, profile, userGoal }) {
  const response = await fetch(`${API_BASE}/mastering/master`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      audio_url: audioUrl,
      reference_url: referenceUrl || undefined,
      profile: profile || undefined,
      planner: "auto",
      user_goal: userGoal || "",
      preview_seconds: 75,
      output_filename: "FINAL_MASTER.wav"
    })
  });

  if (!response.ok) {
    throw new Error(`Mastering request failed: ${response.status}`);
  }

  return response.json();
}

async function getJob(commandId) {
  const response = await fetch(`${API_BASE}/jobs/${commandId}`);
  if (!response.ok) {
    throw new Error(`Job lookup failed: ${response.status}`);
  }
  return response.json();
}

async function pollJob(commandId, onUpdate) {
  while (true) {
    const job = await getJob(commandId);
    onUpdate?.(job);

    if (job.status === "COMPLETED") return job;
    if (job.status === "FAILED") {
      throw new Error(job.error_message || "Job failed");
    }

    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
}

async function savePreference({ commandId, winnerCandidateId, comparedCandidateIds, metadata }) {
  const response = await fetch(`${API_BASE}/mastering/preference`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      command_id: commandId,
      winner_candidate_id: winnerCandidateId,
      compared_candidate_ids: comparedCandidateIds,
      user_metadata: metadata || {}
    })
  });

  if (!response.ok) {
    throw new Error(`Preference save failed: ${response.status}`);
  }

  return response.json();
}

async function finalizeMaster({ commandId, candidateId, outputFilename }) {
  const response = await fetch(`${API_BASE}/mastering/finalize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      command_id: commandId,
      candidate_id: candidateId,
      output_filename: outputFilename || "FINAL_MASTER.wav"
    })
  });

  if (!response.ok) {
    throw new Error(`Finalize request failed: ${response.status}`);
  }

  return response.json();
}
```

## Error Handling

Common user-facing errors:

- Upload URL expired or not public.
- Source file cannot be decoded by FFmpeg.
- Mastering job timed out.
- AI planner failed when `planner` is set to `openai`.
- Finalize requested for an expired or missing source job.

Recommended UI behavior:

- Show a retry button for failed jobs.
- For AI planner failures, retry with `planner: "auto"` or `planner: "rule"`.
- Ask the user to re-upload if the source job expired.
- Never show raw FFmpeg logs to normal users.

## Reprocessing One Candidate

After previews are ready, the UI can adjust and re-render one candidate without
starting the whole mastering job over.

Endpoint:

```http
POST /mastering/reprocess
```

Request:

```json
{
  "command_id": "master-abc123",
  "candidate_id": "balanced_standard",
  "preview_seconds": 75,
  "clean_audio": true,
  "voice_gate_options": {
    "mode": "conservative",
    "attenuation_db": -80,
    "padding_ms": 190,
    "attack_ms": 25,
    "release_ms": 140,
    "min_voice_ms": 140,
    "min_gap_ms": 180,
    "fail_on_no_voice": true
  },
  "adjustments": {
    "brightness": 1.2,
    "warmth": -0.6,
    "presence": 1.5,
    "bass": 0.0,
    "low_mid": -0.8,
    "air": 0.9,
    "de_ess": 0.35,
    "harshness": -1.4,
    "boxiness": -0.8,
    "body": 1.1,
    "mono_bass": 0.45,
    "dynamic_eq": 0.4,
    "stereo_width": 0.08,
    "cleanup": 0.06,
    "cleanup_gate": -45.0,
    "cleanup_noise": 8.0,
    "loudness": -10.5,
    "volume_db": 4.0,
    "input_gain_db": 0.0,
    "compression": 1.6,
    "compression_threshold": -20.0,
    "compression_attack": 20.0,
    "compression_release": 120.0,
    "compression_mix": 0.85,
    "saturation": 0.02,
    "limiter": -1.5,
    "ambience": 0.12
  }
}
```

Adjustment ranges:

- `brightness`, `warmth`, `presence`, `bass`, `low_mid`, `air`: `-4.5` to `4.5` dB
- `body`: `-4.5` to `4.5` dB around `120-250 Hz`
- `boxiness`: `-4.5` to `4.5` dB around `300-500 Hz`
- `harshness`: `-4.5` to `4.5` dB around `3k-6k`
- `de_ess`: `0.0` to `1.0`
- `dynamic_eq`: `0.0` to `1.0`
- `stereo_width`: `0.0` to `0.18`
- `mono_bass`: `0.0` to `1.0`
- `cleanup`: `0.003` to `0.2` gate range
- `cleanup_gate`: `-60.0` to `-28.0` dB
- `cleanup_noise`: `0.0` to `24.0` dB
- `loudness`: `-16.0` to `-8.0` LUFS
- `volume_db`: `-12.0` to `12.0`
- `input_gain_db`: `-6.0` to `6.0`
- `compression`: `1.1` to `3.0` ratio
- `compression_threshold`: `-30.0` to `-10.0` dB
- `compression_attack`: `5.0` to `80.0` ms
- `compression_release`: `50.0` to `300.0` ms
- `compression_mix`: `0.35` to `1.0`
- `saturation`: `0.0` to `0.08`
- `limiter`: `-3.0` to `-1.5` dBTP
- `ambience`: `0.0` to `0.35`

The mastering job response includes `candidate.control_settings`. Initialize
the card's sliders from that object, not from neutral defaults. These are the
actual processor settings used to render the candidate, so a user can move
reverb from `0.12` to `0.16`, loudness from `-10.5` to `-10.0`, or volume from
`0.0` to `2.0` without guessing what created the current preview.

The `adjustments` object sent to `/mastering/reprocess` may include all
settings or only the settings that changed. Any omitted setting is filled from
the candidate's original `control_settings`/base plan on the server.

Set `clean_audio: true` when the user checks Clean Audio in the candidate card.
The server renders the candidate with the same mastering settings first, then
passes that rendered preview through the voice-gate cleanup engine. The
returned candidate keeps the same `control_settings` and includes
`voice_cleaning.enabled: true` plus a `voice_cleaning.analysis` summary. If that
candidate is finalized, the final master receives the same clean-audio pass.

Response:

```json
{
  "success": true,
  "candidate": {},
  "processing_log": []
}
```

Replace only the matching candidate card with `candidate`. The returned
`preview_file.storage_url` points to a new edited preview file, so the browser
should not reuse the previous audio. The same `candidate_id` is preserved, so
finalization can still use the selected card normally.

Reprocess adjustments are evaluated against the candidate's original base plan,
not cumulatively against the last edit. This makes Reset and slider changes
predictable.

## What The Frontend Should Not Do

Do not:

- Send raw FFmpeg commands.
- Call OpenAI directly.
- Expose the OpenAI key.
- Depend on `candidate_scores` ordering without checking `score`.
- Assume temporary output URLs last forever.
- Let the user choose arbitrary mastering parameters outside backend-supported options.

## Admin/Debug Fields

These fields are useful for internal/debug views:

- `planner_result`
- `source_analysis`
- `reference_analysis`
- `target_profile`
- `candidate_scores[].plan`
- `candidate_scores[].ffmpeg_filtergraph`
- `candidate_scores[].post_analysis`
- `candidate_scores[].score_breakdown`
- `processing_log`

Use `source_analysis` for before-mastering metrics. Use
`recommended_candidates[].post_analysis` for what the rendered preview actually
measured after FFmpeg processing. `processing_log` is intended for admin/support
views and explains each backend stage: download, source analysis, target
selection, planner result, candidate plan, render, post-analysis, score, and
ranking.

Normal user UI should focus on:

- Candidate label
- Preview player
- Recommended badge
- Download final master

## Minimal Happy Path

1. Upload audio to storage and get `audio_url`.
2. `POST /mastering/master`.
3. Poll `GET /jobs/{command_id}`.
4. Render cards from `recommended_candidates`.
5. User selects one.
6. `POST /mastering/preference`.
7. `POST /mastering/finalize`.
8. Poll final job.
9. Download `final_outputs.out_1.storage_url`.
