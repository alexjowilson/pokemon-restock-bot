class CheckFailed(Exception):
    """The stock check itself didn't work (network error, bad URL, unexpected response).

    Never means "out of stock" — the scheduler leaves the last known state alone.
    """


class Blocked(CheckFailed):
    """The site refused us (HTTP 403/429). The scheduler backs off harder for these."""
