from __future__ import annotations

from io import BytesIO
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image, UnidentifiedImageError


class RemoteImageError(RuntimeError):
    pass


_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}


def _host_allowed(hostname: str, allowed_hosts: tuple[str, ...]) -> bool:
    host = hostname.lower().strip(".")
    for allowed in allowed_hosts:
        normalized = allowed.lower().strip().strip(".")
        if host == normalized or host.endswith(f".{normalized}"):
            return True
    return False


def _validate_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() != "https":
        raise RemoteImageError("Report image URL must use HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise RemoteImageError("Report image URL must not contain credentials.")
    if not parsed.hostname:
        raise RemoteImageError("Report image URL has no hostname.")
    if not _host_allowed(parsed.hostname, allowed_hosts):
        raise RemoteImageError(f"Report image host is not allowed: {parsed.hostname}")


class _SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: tuple[str, ...], max_redirects: int) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts
        self.max_redirects = max_redirects
        self.redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        self.redirect_count += 1
        if self.redirect_count > self.max_redirects:
            raise RemoteImageError("Remote image exceeded the redirect limit.")
        resolved = urljoin(req.full_url, newurl)
        _validate_url(resolved, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def _validate_image_bytes(data: bytes) -> None:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise RemoteImageError("Remote image format is not supported.")
            image.verify()
    except RemoteImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RemoteImageError("Downloaded content is not a valid supported image.") from exc


def download_remote_image(
    url: str,
    *,
    timeout_seconds: int,
    max_bytes: int,
    allowed_hosts: tuple[str, ...],
    max_redirects: int = 3,
) -> bytes:
    """Download a validated HTTPS image without becoming a generic SSRF client."""
    url = url.strip()
    _validate_url(url, allowed_hosts)

    redirect_handler = _SafeRedirectHandler(allowed_hosts, max_redirects)
    opener = build_opener(redirect_handler)
    request = Request(
        url,
        headers={
            "User-Agent": "SEEFIX-Agent/2.0",
            "Accept": "image/jpeg,image/png,image/webp",
        },
        method="GET",
    )

    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            final_url = response.geturl()
            _validate_url(final_url, allowed_hosts)
            content_type = (response.headers.get_content_type() or "").lower()
            if content_type not in _ALLOWED_MIME:
                raise RemoteImageError(
                    f"Remote report URL returned unsupported MIME type: {content_type or 'unknown'}"
                )
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise RemoteImageError("Remote image exceeds the configured maximum size.")
                except ValueError:
                    pass
            data = response.read(max_bytes + 1)
    except RemoteImageError:
        raise
    except Exception as exc:
        raise RemoteImageError(f"Unable to download the report image: {exc}") from exc

    if not data:
        raise RemoteImageError("Downloaded report image is empty.")
    if len(data) > max_bytes:
        raise RemoteImageError("Downloaded report image exceeds the configured maximum size.")

    _validate_image_bytes(data)
    return data
