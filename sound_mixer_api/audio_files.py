from .common import *

def cleanup_old_outputs():
    cutoff = time.time() - OUTPUT_TTL_SECONDS
    for path in OUTPUT_ROOT.iterdir():
        try:
            if path.stat().st_mtime < cutoff:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        except OSError as exc:
            log_event("output_cleanup_failed", path=str(path), error=truncate(exc, 1000))


def extension_from_url(url):
    suffix = Path(urlparse(url).path).suffix
    if suffix and SAFE_FILENAME_RE.match(f"file{suffix}"):
        return suffix[:16]
    return ".bin"


def download_file(url, destination):
    request = Request(url, headers={"User-Agent": "ffmpeg-sound-mixer-api/0.1"})
    try:
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            with destination.open("wb") as file:
                shutil.copyfileobj(response, file)
    except HTTPError as exc:
        raise RuntimeError(f"Download failed with HTTP {exc.code}: {url}") from exc
    except (OSError, URLError) as exc:
        raise RuntimeError(f"Download failed: {url}") from exc

