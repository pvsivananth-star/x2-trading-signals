"""Optional, allow-listed HTTPS source download for historical dumps."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import posixpath
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class DownloadError(RuntimeError):
    """Raised when a historical source cannot be downloaded safely."""


ALLOWED_HOSTS = {"data.binance.vision"}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_: object, **__: object) -> None:
        raise DownloadError("redirects are not allowed for historical source downloads")



def materialize_source(source: str, download_dir: Path) -> Path:
    """Return a local path, downloading only an allow-listed HTTPS source."""

    parsed = urlparse(source)
    if parsed.scheme == "":
        path = Path(source)
        if not path.is_file():
            raise DownloadError(f"source file does not exist: {path}")
        return path
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise DownloadError("only HTTPS historical dumps from an allow-listed provider host are supported")

    filename = Path(posixpath.basename(unquote(parsed.path))).name
    if not filename or filename in {".", ".."}:
        raise DownloadError("source URL has no safe filename")
    download_dir.mkdir(parents=True, exist_ok=True)
    url_key = sha256(source.encode("utf-8")).hexdigest()[:16]
    target = download_dir / f"{url_key}-{filename}"
    temporary = target.with_suffix(target.suffix + ".partial")
    request = Request(source, headers={"User-Agent": "x2-trading-signals/0.1"})
    try:
        opener = build_opener(_NoRedirect)
        with opener.open(request, timeout=60) as response, temporary.open("wb") as output:
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
                raise DownloadError("download response host is not allow-listed")
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(target)
    except DownloadError:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise DownloadError(f"historical source download failed: {source}") from exc
    return target
