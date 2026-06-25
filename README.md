# FFmpeg Sound Mixer API

Copied FFmpeg processing API for sound-mixer changes and experiments.

## Endpoint

```bash
curl -X POST https://sound-mixer-api.jamrockdev.com/sound-mixer \
  -H "Content-Type: application/json" \
  -d '{
    "backing_url": "https://example.com/backing-track.mp3",
    "vocal_url": "https://example.com/vocal-track.mp3",
    "output_files": {
      "out_1": "FINAL_MIX_test.mp3"
    }
  }'
```

Set `LOUDNESS_API_KEY` in the compose environment to require `X-API-KEY`.

The copied loudness and FFmpeg utility endpoints are still available:

- `POST /measure-loudness`
- `POST /run-ffmpeg-command`
- `POST /mux-video`
- `POST /analyze-and-mix`
- `POST /sound-mixer`
- `POST /mastering/analyze`
- `POST /mastering/master`
- `POST /mastering/finalize`
- `POST /mastering/preference`
- `GET /mastering/profiles`
- `GET /jobs/{command_id}`

## AI Mastering MVP

```bash
curl -X POST https://sound-mixer-api.jamrockdev.com/mastering/master \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "https://example.com/mix.wav",
    "profile": "modern_pop_streaming",
    "planner": "auto",
    "user_goal": "balanced streaming master with clear vocals",
    "preview_seconds": 75,
    "output_filename": "FINAL_MASTER.wav"
  }'
```

The job analyzes the source, chooses a target profile, renders 9 preview
candidates, scores them, and returns the top 3 through `GET /jobs/{command_id}`.
Use `POST /mastering/finalize` with the selected `candidate_id` to render the
full master.

Set `SOUND_MIXER_OPENAI_API_KEY` to enable the OpenAI planner. Without it,
`planner: "auto"` falls back to the deterministic rule planner. Use
`planner: "rule"` to bypass AI, or `planner: "openai"` to require OpenAI and
fail if it is not configured.
