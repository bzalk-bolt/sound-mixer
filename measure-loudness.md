# FFmpeg Sound Mixer API

Base URL:

```text
https://sound-mixer-api.jamrockdev.com
```

No authentication is currently required.

Operational logging is written on the server at `/data/sound-mixer-api.log`.
Email notifications are disabled by default on this copied service. Set
`SOUND_MIXER_RESEND_API_KEY` to enable clone-specific notifications.

The AI mastering planner uses OpenAI when `SOUND_MIXER_OPENAI_API_KEY` is set.
Without that key, `planner: "auto"` falls back to the deterministic rule planner.
Use `planner: "rule"` to bypass AI or `planner: "openai"` to require OpenAI and
fail if it is not configured.

Frontend integration guide:

```text
https://sound-mixer-api.jamrockdev.com/mastering-ui-guide.md
```

## Endpoint: Mastering Profiles

```http
GET /mastering/profiles
```

Returns available target profiles, the candidate grid, and whether the OpenAI
planner is configured.

## Endpoint: AI Mastering Analysis

```http
POST /mastering/analyze
Content-Type: application/json
```

Downloads an audio URL and returns a structured analysis object. This is the
first step of the AI mastering pipeline; it does not render audio.

```json
{
  "audio_url": "https://example.com/input-mix.wav"
}
```

The response includes:

- `loudness`: integrated LUFS, LRA, true peak, and crest factor
- `spectrum`: seven band RMS measurements from sub through air
- `stereo`: width score, mono correlation, and low-end width
- `dynamics`: transient density, compression guess, and clipping risk
- `classification`: broad genre/profile hints and mix problem tags

## Endpoint: AI Mastering Candidates

```http
POST /mastering/master
Content-Type: application/json
```

Creates a fast mastering setup job. The service downloads the source, analyzes
it once, chooses a target profile, and returns three deterministic preset
starting points: Open / Bright, Warm, and Balanced. It does not render, analyze,
or score initial preview candidates; each preset points at the uploaded source
audio until the user reprocesses or finalizes it.

```json
{
  "audio_url": "https://example.com/input-mix.wav",
  "reference_url": "https://example.com/reference-master.wav",
  "profile": "modern_pop_streaming",
  "planner": "auto",
  "user_goal": "balanced streaming master with clear vocals",
  "preview_seconds": 75,
  "debug_stage_artifacts_enabled": true,
  "debug_waveform_points": 512,
  "output_filename": "FINAL_MASTER.wav",
  "webhook_url": "https://fvaaxuhjofhbngkalqcv.supabase.co/functions/v1/mastering-callback",
  "webhook_metadata": {
    "track_id": "abc123"
  }
}
```

`reference_url`, `profile`, `planner`, `user_goal`, `webhook_url`, and
`webhook_metadata` are optional.
`reference_url` is accepted for request compatibility, but the fast initial load
does not download or analyze reference tracks. Reference-style changes should be
made through reprocess/custom controls. Initial debug stage artifacts are also
skipped because no initial processing render is performed.

Planner modes:

- `auto`: use OpenAI when configured, otherwise use the deterministic planner
- `openai`: require OpenAI and fail if it is not configured or returns an error
- `rule`: use the deterministic planner only

Supported target profiles:

- `modern_pop_streaming`
- `hiphop_loud`
- `acoustic_natural`
- `rock_punchy`
- `edm_loud_clean`
- `country_radio`
- `podcast_voice`
- `warm_vintage`
- `open_bright`

The candidate grid is:

- Styles: `warm`, `balanced`, `open`
- Loudness levels: `conservative`, `standard`, `loud`

Immediate response:

```json
{
  "command_id": "master-unique-id"
}
```

Poll status:

```http
GET /jobs/master-unique-id
```

Completed jobs include:

- `source_analysis`
- `reference_analysis`, when supplied
- `target_profile`
- `planner_result`
- `candidate_scores`
- `recommended_candidates`
- `recommended_candidates[].preview_file.storage_url`
- `recommended_candidates[].plan`
- `recommended_candidates[].control_settings`
- `recommended_candidates[].scoring_status`, set to `skipped`
- `recommended_candidates[].render_status`, set to `not_rendered`
- `debug_artifacts.enabled`, set to `false`

The AI layer is intentionally constrained: it produces a safe mastering plan,
not raw FFmpeg. The renderer validates and clamps the plan before compiling it
to a native FFmpeg filter graph.

## Endpoint: Finalize Master

```http
POST /mastering/finalize
Content-Type: application/json
```

Renders the full-length final master from a selected candidate plan.

```json
{
  "command_id": "master-unique-id",
  "candidate_id": "balanced_standard",
  "output_filename": "FINAL_MASTER.wav"
}
```

Immediate response:

```json
{
  "command_id": "final-unique-id"
}
```

Poll `GET /jobs/final-unique-id` for `final_outputs.out_1.storage_url` and
`final_analysis`.

## Endpoint: Save Mastering Preference

```http
POST /mastering/preference
Content-Type: application/json
```

Saves a user choice for later preference-model training.

```json
{
  "command_id": "master-unique-id",
  "winner_candidate_id": "balanced_standard",
  "compared_candidate_ids": [
    "balanced_standard",
    "open_standard",
    "warm_loud"
  ],
  "user_metadata": {
    "track_id": "abc123"
  }
}
```

Preferences are appended inside the container data volume at
`/data/mastering-preferences.jsonl`.

## Endpoint: Measure Loudness

```http
POST /measure-loudness
Content-Type: application/json
```

### Request Body

```json
{
  "audio_url": "https://example.com/path/to/audio-file.mp3"
}
```

`audio_url` is required and must be a publicly accessible `http` or `https`
URL. Any format FFmpeg can decode is supported, including MP3, WAV, M4A, FLAC,
OGG, MP4, and WebM.

### Behavior

The API runs this FFmpeg command synchronously:

```bash
ffmpeg -i "<audio_url>" -af loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json -f null -
```

It parses the loudnorm JSON printed to stderr and returns numeric values. The
main value is `input_i`, the integrated loudness of the input in LUFS.

### Success Response

HTTP `200`

```json
{
  "success": true,
  "input_i": -24.52,
  "input_tp": -2.04,
  "input_lra": 7.3,
  "input_thresh": -34.82,
  "output_i": -16.0,
  "output_tp": -1.5,
  "output_lra": 5.5,
  "output_thresh": -26.2,
  "normalization_type": "dynamic",
  "target_offset": 0.0
}
```

## Endpoint: Run FFmpeg Command

```http
POST /run-ffmpeg-command
Content-Type: application/json
```

### Request Body

```json
{
  "input_files": {
    "in_1": "https://example.com/input-audio-or-video.webm",
    "in_2": "https://example.com/optional-second-input.mp3",
    "in_3": "https://example.com/optional-third-input.mp3"
  },
  "output_files": {
    "out_1": "VOCALS_abc123.mp3"
  },
  "ffmpeg_command": "-i {{in_1}} -vn -acodec libmp3lame -q:a 2 {{out_1}}",
  "webhook_url": "https://fvaaxuhjofhbngkalqcv.supabase.co/functions/v1/receive-vocals",
  "webhook_metadata": {
    "recording_id": "abc123"
  }
}
```

### Field Definitions

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `input_files` | object | Yes | Key-value pairs of input labels to public URLs. Labels must be `in_1`, `in_2`, `in_3`, etc. |
| `output_files` | object | Yes | Key-value pairs of output labels to filenames. Labels must be `out_1`, `out_2`, etc. Filenames must not contain path separators. |
| `ffmpeg_command` | string | Yes | FFmpeg arguments with `{{in_1}}`, `{{in_2}}`, `{{out_1}}` placeholders. Do not include shell syntax. Including a leading `ffmpeg` is accepted but not required. |
| `webhook_url` | string | Yes | Must be an allowed webhook URL. Allowed primary webhooks include `receive-vocals`, `receive-mixed-audio`, and `receive-muxed-video` for the current Supabase project. |
| `webhook_metadata` | object | Yes | Arbitrary JSON object passed through to the webhook unchanged. |

### Immediate Response

The server accepts the request, queues processing, and immediately returns:

```json
{
  "command_id": "job-unique-id"
}
```

### Asynchronous Behavior

The server then:

1. Downloads every `input_files` URL into local temp storage.
2. Replaces command placeholders with local file paths.
3. Runs FFmpeg without a shell.
4. Serves generated outputs from temporary public URLs under `/tmp`.
5. Posts the final result to `webhook_url`.

Output URLs are publicly downloadable for at least 1 hour. Current retention is
2 hours.

### Success Webhook

```json
{
  "data": {
    "status": "COMPLETED",
    "output_files": {
      "out_1": {
    "storage_url": "https://sound-mixer-api.jamrockdev.com/tmp/job-unique-id/VOCALS_abc123.mp3"
      }
    },
    "original_request": {
      "output_files": {
        "out_1": "VOCALS_abc123.mp3"
      }
    },
    "webhook_metadata": {
      "recording_id": "abc123"
    }
  }
}
```

### Failure Webhook

```json
{
  "data": {
    "status": "FAILED",
    "error_message": "Description of what went wrong",
    "error_status": "ffmpeg_error",
    "output_files": {},
    "original_request": {
      "output_files": {
        "out_1": "VOCALS_abc123.mp3"
      }
    },
    "webhook_metadata": {
      "recording_id": "abc123"
    }
  }
}
```

### Required Webhook Fields

The webhook receiver relies on these exact fields:

| Field | Requirement |
| --- | --- |
| `data.status` | Must be `COMPLETED` or `FAILED`. |
| `data.output_files.out_1.storage_url` | Public URL where the output file can be downloaded. |
| `data.original_request.output_files.out_1` | Original output filename string. |
| `data.webhook_metadata` | Passed through unchanged. |

### Filename Prefix Routing

| Prefix | Meaning | Triggered by |
| --- | --- | --- |
| `VOCALS_` | Vocal extraction from video recording | `process-video-audio` |
| `COMBINED_` | Video merged with vocals and backing track | `process-video-merge` |
| `KIOSK_VOCALS_` | Kiosk vocal extraction | `kiosk-submit-recording` |
| `KIOSK_PREPROCESSED_` | Kiosk preprocessed audio | `kiosk-preprocess-audio` |

### Supported Command Examples

Vocal extraction:

```bash
-i {{in_1}} -vn -acodec libmp3lame -q:a 2 {{out_1}}
```

Video merge with backing track:

```bash
-i {{in_1}} -i {{in_2}} -i {{in_3}} -filter_complex "[1:a]volume=0.5[backing];[2:a]volume=2dB[vocals];[backing][vocals]amix=inputs=2:duration=longest[a]" -map 0:v -map "[a]" -c:v copy -c:a aac -ac 2 {{out_1}}
```

Video merge legacy:

```bash
-i {{in_1}} -i {{in_2}} -filter_complex "[0:a][1:a]amerge=inputs=2[a]" -map 0:v -map "[a]" -c:v copy -c:a aac -ac 2 {{out_1}}
```

Kiosk audio preprocessing:

```bash
-i {{in_1}} -af highpass=f=80,afftdn=nf=-25,agate=threshold=0.01:attack=1:release=100,loudnorm=I=-13:TP=-1.5:LR=11 -codec:a libmp3lame -q:a 2 {{out_1}}
```

The loudnorm `I` value may vary per song. The server accepts the legacy
`LR=11` spelling shown above and normalizes it to FFmpeg's `LRA=11` option
before execution.

### cURL Example

```bash
curl -X POST https://sound-mixer-api.jamrockdev.com/run-ffmpeg-command \
  -H "Content-Type: application/json" \
  -d '{
    "input_files": {
      "in_1": "https://storage.rendi.dev/sample/big_buck_bunny_720p_5sec_intro.mp4"
    },
    "output_files": {
      "out_1": "VOCALS_test.mp3"
    },
    "ffmpeg_command": "-i {{in_1}} -vn -acodec libmp3lame -q:a 2 {{out_1}}",
    "webhook_url": "https://fvaaxuhjofhbngkalqcv.supabase.co/functions/v1/receive-vocals",
    "webhook_metadata": {
      "recording_id": "test"
    }
  }'
```

## Shared Error Response

Synchronous validation errors return HTTP `4xx` or `5xx`:

```json
{
  "success": false,
  "error": "Description of what went wrong"
}
```

Common status codes:

- `400`: Required field missing or invalid.
- `413`: Request body is too large.
- `415`: `Content-Type` is not `application/json`.
- `422`: FFmpeg could not decode or process media for the loudness endpoint.
- `502`: The input URL was unreachable for the loudness endpoint.
- `504`: The loudness endpoint exceeded its synchronous timeout.
- `500`: Internal server error.

## Endpoint: Mux Video

```http
POST /mux-video
Content-Type: application/json
```

This endpoint downloads a video file, a final mixed audio file, and a logo. It
first creates a synced intermediate by copying the source video stream and
replacing the audio with the supplied final mix, then burns the logo into that
already-synced intermediate before posting the result to a webhook.

By default, the endpoint compares the original video's audio against the final
mix and applies a small delay/trim correction before muxing. This keeps the
final mix aligned to the camera video when earlier mix processing shifted the
audio against the backing track.

Because the logo changes the video pixels, this endpoint re-encodes video. For
`.mp4`, `.m4v`, and `.mov` outputs, the server encodes video as H.264 and audio
as AAC. For `.webm` outputs, it uses a fast VP8 WebM profile and Opus audio.
Logo-burned video is normalized to 30fps to avoid timestamp drift from
variable-frame-rate source recordings.

### Request Body

```json
{
  "video_url": "https://example.com/input-video.webm",
  "audio_url": "https://example.com/final-mix.mp3",
  "burn_logo": true,
  "auto_audio_sync": true,
  "audio_delay_seconds": 0,
  "logo_url": "https://thesingingleague.com/TSL_Logo.png",
  "logo_height_ratio": 0.2,
  "logo_margin_ratio": 0.03,
  "output_files": {
    "out_1": "FINAL_MUX_recording-id.webm"
  },
  "webhook_url": "https://fvaaxuhjofhbngkalqcv.supabase.co/functions/v1/receive-muxed-video",
  "webhook_metadata": {
    "recording_id": "recording-id"
  }
}
```

`output_files` is optional. If omitted, the server generates `out_1` as
`FINAL_MUX_<recording-id>.<ext>` when `webhook_metadata.recording_id` is
provided. It chooses `.webm` for WebM source videos, otherwise `.mp4`.

`logo_url`, `logo_height_ratio`, and `logo_margin_ratio` are optional. The
default logo is `https://thesingingleague.com/TSL_Logo.png`, scaled to 20% of
the video height with a 3% bottom/right margin.

`burn_logo` is optional and defaults to `true`. Set it to `false` to only
replace the audio and skip the logo burn.

`auto_audio_sync` is optional and defaults to `true`. When enabled, the server
estimates the timing offset between the source video's original audio and the
final mix, then applies the correction before muxing. `audio_delay_seconds` is
optional and defaults to `0`; use it to add a manual delay or trim on top of the
automatic estimate.

### Immediate Response

```json
{
  "command_id": "job-unique-id"
}
```

### Webhook Payload

The completion callback uses the same payload shape as `/run-ffmpeg-command`:

```json
{
  "data": {
    "status": "SUCCESS",
    "output_files": {
      "out_1": {
    "storage_url": "https://sound-mixer-api.jamrockdev.com/tmp/job-unique-id/FINAL_MUX_recording-id.webm"
      }
    },
    "original_request": {
      "output_files": {
        "out_1": "FINAL_MUX_recording-id.webm"
      }
    },
    "webhook_metadata": {
      "recording_id": "recording-id"
    }
  }
}
```

## Endpoint: Analyze And Mix

```http
POST /analyze-and-mix
Content-Type: application/json
```

`POST /sound-mixer` is also available as a clearer alias for this same
pipeline on the copied sound-mixer service.

This endpoint deterministically analyzes a backing track and vocal track, chooses
a conservative production mix recipe, renders a final audio file, and exposes it
as a temporary public URL.

### Request Body

```json
{
  "backing_url": "https://example.com/backing-track.mp3",
  "vocal_url": "https://example.com/vocal-track.mp3",
  "reference_vocal_url": "https://example.com/original-guide-vocal.mp3",
  "output_files": {
    "out_1": "FINAL_MIX_abc123.mp3"
  },
  "webhook_url": "https://fvaaxuhjofhbngkalqcv.supabase.co/functions/v1/receive-mixed-audio",
  "webhook_metadata": {
    "recording_id": "abc123"
  }
}
```

`reference_vocal_url` is optional. When supplied, it should be the original
guide vocal, reference vocal, or full reference track containing the original
vocal for the same song. The server uses it to estimate timing drift between the
recorded vocal and the reference, then shifts the recorded vocal before mixing.
`guide_vocal_url` is accepted as an alias.

`webhook_url` is optional. For final mix jobs, use
`https://fvaaxuhjofhbngkalqcv.supabase.co/functions/v1/receive-mixed-audio`.
Allowed webhook URLs also include the existing receive-vocals endpoints for the
older processing pipeline.

### Deterministic Mix Logic

The server measures both files with `ffprobe`, `loudnorm`, `volumedetect`,
`astats`, and `silencedetect`. It then applies these rules:

- Detect one-sided or heavily imbalanced stereo vocals using per-channel RMS and
  peak spread.
- If the vocal is one-sided or imbalanced by 12 dB or more, sum it to centered
  mono before processing.
- If `reference_vocal_url` is supplied, build vocal envelopes for the reference
  and recorded vocal, cross-correlate them, and estimate a timing offset.
- If the timing offset confidence is high enough, shift the recorded vocal before
  mixing. Positive detected delay means the recorded vocal was late; the server
  trims/advances it by the inverse amount.
- Process the vocal in this order: gate, cleanup/denoise, normalization,
  reductive EQ, dynamics, additive EQ, then space.
- Render temporary first-pass backing and vocal stems, measure those stems,
  adjust the vocal against the measured backing level, then render the final
  mix.

Default targets:

| Stage | Target |
| --- | --- |
| Backing | Backing bed at about `-6 dB` |
| Vocal | Polished vocal chain, `-12 LUFS` first pass, then measured against the processed backing stem |
| Final Mix | Limited balanced kiosk/audition output |

The mix measures active 1-second RMS windows for the backing and vocal. If the
vocal is low, it can automatically boost the vocal before the measured
second-pass leveler. The second-pass leveler renders temporary first-pass stems,
measures their active 1-second RMS windows, and adjusts the vocal so its active
level lands near the processed backing level before the final weighted mix.

You can override these with `mix_options`:

```json
{
  "mix_options": {
    "backing_bed_volume_db": -6,
    "vocal_target_i": -12,
    "vocal_compressor_makeup_db": 6,
    "vocal_polish_enabled": true,
    "vocal_gate_enabled": true,
    "vocal_gate_threshold": 0.005,
    "vocal_gate_ratio": 2,
    "vocal_gate_attack_ms": 5,
    "vocal_gate_release_ms": 120,
    "vocal_denoise_enabled": true,
    "vocal_denoise_noise_floor_db": -25,
    "vocal_highpass_hz": 100,
    "vocal_deesser_intensity": 0.35,
    "vocal_deesser_max": 0.5,
    "vocal_deesser_frequency": 0.55,
    "vocal_reductive_eq_bands": [
      { "frequency_hz": 250, "width_q": 1.2, "gain_db": -2 }
    ],
    "vocal_additive_eq_bands": [
      { "frequency_hz": 3500, "width_q": 0.8, "gain_db": 1.5 }
    ],
    "vocal_plate_reverb_enabled": true,
    "vocal_short_delay_enabled": true,
    "debug_stage_artifacts_enabled": true,
    "debug_waveform_points": 512,
    "vocal_boost_db": 0,
    "vocal_second_pass_enabled": true,
    "vocal_second_pass_target_over_backing_db": 0,
    "vocal_second_pass_max_boost_db": 9,
    "vocal_second_pass_max_cut_db": 12,
    "target_vocal_over_backing_db": 3,
    "max_auto_vocal_boost_db": 3,
    "max_alignment_shift_seconds": 15,
    "alignment_frame_ms": 40,
    "minimum_alignment_confidence": 0.08,
    "backing_weight": 1.5,
    "vocal_weight": 1.8,
    "ducking_threshold": 0.02,
    "ducking_ratio": 12,
    "final_true_peak": -1
  }
}
```

`vocal_reductive_eq_bands` only applies negative `gain_db` values. Non-negative
entries are ignored. `vocal_additive_eq_bands` only applies positive `gain_db`
values. Non-positive entries are ignored.

By default the server also renders stage feedback artifacts for the vocal chain.
Set `debug_stage_artifacts_enabled` to `false` to skip those renders.
`debug_waveform_points` controls the size of each public waveform JSON file and
is clamped between `64` and `2048`.

### Immediate Response

```json
{
  "command_id": "mix-unique-id"
}
```

### Job Status

```http
GET /jobs/{command_id}
```

Returns queued/running/completed/failed status. Completed mix jobs include:

- `output_files.out_1.storage_url`
- `analysis`
- `mix_decisions`
- `debug_artifacts`
- `webhook_result`, when a webhook URL was supplied

`debug_artifacts` is a URL manifest for the debug panel. It does not inline audio
or waveform arrays. The shape is:

```json
{
  "enabled": true,
  "kind": "vocal_stage_feedback",
  "base": {
    "original_vocal_waveform": {
      "storage_url": "https://sound-mixer-api.jamrockdev.com/tmp/mix-id/00-original-vocal-waveform-abc123.json",
      "summary": {}
    }
  },
  "stages": [
    {
      "order": 1,
      "key": "gate",
      "label": "Gate",
      "mix": {
        "storage_url": "https://sound-mixer-api.jamrockdev.com/tmp/mix-id/01-gate-mix-abc123.wav"
      },
      "waveform": {
        "storage_url": "https://sound-mixer-api.jamrockdev.com/tmp/mix-id/01-gate-waveform-abc123.json"
      },
      "compare_to": "original_vocal_waveform",
      "summary": {
        "peak": 0.72,
        "rms_db_p50": -28.1,
        "rms_db_p75": -18.4
      },
      "stage_filters": []
    }
  ]
}
```

When `reference_vocal_url` is supplied, timing details are returned at:

- `analysis.timing_alignment.detected_vocal_delay_ms`
- `analysis.timing_alignment.applied_vocal_shift_ms`
- `analysis.timing_alignment.confidence`
- `mix_decisions.applied_vocal_shift_ms`

If the audio render succeeds but the webhook returns a non-2xx response, the job
still stays `COMPLETED` so the final mix URL is not lost. In that case
`webhook_result.ok` is `false` and includes the webhook status/error details.
The server also sends an email notification with status
`COMPLETED_WITH_WEBHOOK_ERROR`.

### Success Webhook

```json
{
  "data": {
    "status": "COMPLETED",
    "output_files": {
      "out_1": {
    "storage_url": "https://sound-mixer-api.jamrockdev.com/tmp/mix-unique-id/FINAL_MIX_abc123.mp3"
      }
    },
    "original_request": {
      "output_files": {
        "out_1": "FINAL_MIX_abc123.mp3"
      }
    },
    "webhook_metadata": {
      "recording_id": "abc123"
    },
    "analysis": {},
    "mix_decisions": {},
    "debug_artifacts": {}
  }
}
```

### Failure Webhook

```json
{
  "data": {
    "status": "FAILED",
    "error_message": "Description of what went wrong",
    "error_status": "mix_error",
    "output_files": {},
    "original_request": {
      "output_files": {
        "out_1": "FINAL_MIX_abc123.mp3"
      }
    },
    "webhook_metadata": {
      "recording_id": "abc123"
    }
  }
}
```
