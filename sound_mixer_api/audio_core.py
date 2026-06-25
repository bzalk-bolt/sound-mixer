from .common import *

def parse_loudnorm_json(stderr):
    matches = LOUDNORM_JSON_RE.findall(stderr)
    if not matches:
        raise ApiError(500, "Could not parse loudnorm output")

    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        raise ApiError(500, f"Failed to parse JSON: {exc}") from exc

    response = {"success": True}
    for field in FLOAT_FIELDS:
        try:
            response[field] = float(parsed[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError(500, f"Invalid loudnorm field {field}") from exc

    response["normalization_type"] = parsed.get("normalization_type", "")
    response["target_offset"] = response.pop("target_offset")
    return response


def classify_ffmpeg_failure(stderr):
    lower = stderr.lower()
    if (
        "server returned 4" in lower
        or "server returned 5" in lower
        or "http error" in lower
        or "not found" in lower
        or "connection refused" in lower
        or "connection timed out" in lower
        or "failed to resolve" in lower
        or "i/o error" in lower
        or "error opening input" in lower
    ):
        return 502, "URL unreachable or download failed"

    if (
        "invalid data found when processing input" in lower
        or "could not find codec parameters" in lower
        or "error while decoding" in lower
        or "unsupported codec" in lower
    ):
        return 422, "FFmpeg failed to decode the file"

    return 422, "FFmpeg failed to process the file"


def measure_loudness(audio_url):
    args = [
        "ffmpeg",
        "-i",
        audio_url,
        "-af",
        LOUDNORM_FILTER,
        "-f",
        "null",
        "-",
    ]

    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ApiError(504, f"Server timeout after {TIMEOUT_SECONDS} seconds") from exc
    except FileNotFoundError as exc:
        raise ApiError(500, "FFmpeg is not installed") from exc

    stderr = completed.stderr or ""
    if completed.returncode != 0:
        status, message = classify_ffmpeg_failure(stderr)
        raise ApiError(status, message)

    result = parse_loudnorm_json(stderr)
    return result


def run_command(args, timeout, error_message):
    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{error_message}: timeout after {timeout} seconds") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(f"{error_message}: executable not found") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(f"{error_message}: {stderr[-2000:]}")

    return completed


def parse_optional_float(value):
    try:
        if value in {"-inf", "inf", "+inf"}:
            return float(value)
        return float(value)
    except (TypeError, ValueError):
        return None


def ffprobe_audio(path):
    completed = run_command(
        [
            "ffprobe",
            "-hide_banner",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate,size:stream=index,codec_name,codec_type,sample_rate,channels,channel_layout,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        30,
        "ffprobe failed",
    )
    parsed = json.loads(completed.stdout)
    audio_stream = next(
        (
            stream
            for stream in parsed.get("streams", [])
            if stream.get("codec_type") == "audio"
        ),
        {},
    )
    fmt = parsed.get("format", {})
    return {
        "duration": parse_optional_float(fmt.get("duration")),
        "size": int(fmt.get("size", 0) or 0),
        "bit_rate": int(fmt.get("bit_rate", 0) or 0),
        "codec_name": audio_stream.get("codec_name", ""),
        "sample_rate": int(audio_stream.get("sample_rate", 0) or 0),
        "channels": int(audio_stream.get("channels", 0) or 0),
        "channel_layout": audio_stream.get("channel_layout", ""),
        "stream_bit_rate": int(audio_stream.get("bit_rate", 0) or 0),
    }


def loudnorm_file(path, target_i=-16):
    completed = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"loudnorm=I={target_i}:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        TIMEOUT_SECONDS,
        "loudnorm analysis failed",
    )
    return parse_loudnorm_json(completed.stderr)


def volumedetect_file(path):
    completed = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        TIMEOUT_SECONDS,
        "volume analysis failed",
    )
    metrics = {}
    for line in completed.stderr.splitlines():
        if "mean_volume:" in line:
            metrics["mean_volume"] = parse_optional_float(line.rsplit(":", 1)[1].strip().split()[0])
        elif "max_volume:" in line:
            metrics["max_volume"] = parse_optional_float(line.rsplit(":", 1)[1].strip().split()[0])
    return metrics


def clamp(value, low, high):
    return max(low, min(high, value))


def ffmpeg_number(value):
    return f"{float(value):.6g}"


def parse_vocal_eq_bands(mix_options, key, polarity):
    raw_bands = mix_options.get(key, [])
    if raw_bands in (None, ""):
        return []
    if not isinstance(raw_bands, list):
        return []

    parsed = []
    for band in raw_bands:
        if not isinstance(band, dict):
            continue
        try:
            frequency = float(
                band.get("frequency_hz", band.get("frequency", band.get("f")))
            )
            width_q = float(band.get("width_q", band.get("q", band.get("width", 1))))
            gain_db = float(band.get("gain_db", band.get("gain", band.get("g"))))
        except (TypeError, ValueError):
            continue

        if frequency <= 0 or width_q <= 0 or gain_db == 0:
            continue
        if polarity == "negative" and gain_db >= 0:
            continue
        if polarity == "positive" and gain_db <= 0:
            continue

        parsed.append(
            {
                "frequency_hz": frequency,
                "width_q": width_q,
                "gain_db": gain_db,
            }
        )
    return parsed


def equalizer_filter_from_band(band):
    return (
        f"equalizer=f={ffmpeg_number(band['frequency_hz'])}:"
        f"t=q:w={ffmpeg_number(band['width_q'])}:"
        f"g={ffmpeg_number(band['gain_db'])}"
    )


def parse_int_option(options, key, default, low, high):
    try:
        value = int(options.get(key, default))
    except (TypeError, ValueError):
        value = default
    return int(clamp(value, low, high))


def samples_rms_db(samples):
    if not samples:
        return None
    square_sum = sum(sample * sample for sample in samples)
    rms = math.sqrt(square_sum / len(samples))
    if rms <= 0:
        return -120.0
    return 20 * math.log10(rms / 32768)


def percentile(values, percent):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * clamp(percent, 0, 100) / 100
    lower = int(math.floor(index))
    upper = int(math.ceil(index))
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


