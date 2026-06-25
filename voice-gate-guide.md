# Voice Gate / Non-Vocal Muting API

This endpoint removes noise or non-vocal sections by detecting where singing
voice is present, building a smoothed gain mask, and applying that mask with
FFmpeg. Detected singing sections are left unprocessed except for short
fade-in/fade-out edges.

This is best for recordings where the desired output should be quiet unless a
voice is singing or speaking.

In the mastering UI, the Clean Audio checkbox on a candidate uses this same
engine through `/mastering/reprocess` by sending `clean_audio: true`. That keeps
the candidate's mastering settings unchanged and only adds the non-vocal muting
pass after rendering.

## Endpoint

```http
POST /noise-reduction/voice-gate
```

Alias:

```http
POST /noise-reduction/mute-non-vocal
```

The job is asynchronous. The response contains a `command_id`; poll
`GET /jobs/{command_id}` until `status` is `COMPLETED` or `FAILED`.

## Request

```json
{
  "audio_url": "https://example.com/input.wav",
  "output_filename": "voice-cleaned.wav",
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
  "webhook_url": "",
  "webhook_metadata": {}
}
```

## Options

- `mode`: `conservative`, `balanced`, or `aggressive`.
  Use `conservative` first. It preserves more possible vocal material and is
  less likely to cut off soft singing.
- `attenuation_db`: mute depth for non-vocal areas. Range: `-96` to `-12`.
  Default: `-80`.
- `padding_ms`: time kept before and after detected voice. Range: `60` to
  `600`. More padding is safer for word starts and tails.
- `attack_ms`: fade-in length at the start of vocal regions. Range: `3` to
  `120`.
- `release_ms`: fade-out length at the end of vocal regions. Range: `20` to
  `500`.
- `min_voice_ms`: shortest detected voice region to keep. Range: `40` to
  `800`.
- `min_gap_ms`: gaps shorter than this are bridged so syllables do not chop.
  Range: `40` to `900`.
- `fail_on_no_voice`: when `true`, the job fails instead of returning a fully
  muted file if no confident voice regions are detected.

## Response

Initial response:

```json
{
  "command_id": "voicegate-abc123"
}
```

Completed job:

```json
{
  "command_id": "voicegate-abc123",
  "status": "COMPLETED",
  "output_file": {
    "storage_url": "https://sound-mixer-api.jamrockdev.com/tmp/voicegate-abc123/voice-cleaned.wav"
  },
  "analysis": {
    "duration_seconds": 74.2,
    "voice_segment_count": 12,
    "voice_seconds": 48.7,
    "muted_seconds": 25.5,
    "voice_ratio": 0.6563,
    "segments": [
      { "start": 2.14, "end": 6.91 }
    ],
    "thresholds": {
      "noise_floor_db": -51.4,
      "active_threshold_db": -45.9,
      "vocal_threshold_db": -48.2,
      "presence_threshold_db": -53.1
    }
  },
  "voice_gate_options": {},
  "processing_log": []
}
```

## Frontend Notes

- Use this as a separate tool, not as part of mastering.
- Upload the audio with the existing upload flow or provide a public
  `audio_url`.
- Poll `GET /jobs/{command_id}`.
- Show `analysis.segments`, `voice_seconds`, and `muted_seconds` for debugging.
- If the result cuts off soft words, retry with:
  - `mode: "conservative"`
  - higher `padding_ms`
  - lower `min_voice_ms`
  - lower mute depth, such as `attenuation_db: -45`
- If too much fan/noise remains, retry with:
  - `mode: "balanced"` or `aggressive`
  - lower `padding_ms`
  - higher `attenuation_db` depth, such as `-90`
