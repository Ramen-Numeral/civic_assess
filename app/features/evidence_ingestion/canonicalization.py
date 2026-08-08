from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_PARAMETERS = {"fbclid", "gclid"}


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.username or parsed.password:
        raise ValueError("Evidence URL must not contain credentials")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("Evidence URL requires a hostname")
    port = parsed.port
    default_port = (parsed.scheme.lower(), port) in {("http", 80), ("https", 443)}
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    parameters = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in TRACKING_PARAMETERS
    ]
    return urlunsplit((
        parsed.scheme.lower(),
        netloc,
        parsed.path or "/",
        urlencode(sorted(parameters)),
        "",
    ))
