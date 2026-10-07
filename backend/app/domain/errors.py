"""Stable application errors, independent of transport."""


class DiscoveryError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.headers = headers


def unauthenticated() -> DiscoveryError:
    # One message for missing, malformed, expired, revoked and unknown tokens.
    return DiscoveryError(
        "UNAUTHENTICATED", "A valid access token is required.", 401, {"WWW-Authenticate": 'Bearer realm="api"'}
    )


def forbidden() -> DiscoveryError:
    return DiscoveryError("FORBIDDEN", "You do not have permission for this operation.", 403)


def not_found(code: str, message: str) -> DiscoveryError:
    return DiscoveryError(code, message, 404)
