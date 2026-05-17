"""Amazon auth compatibility shell disabled during shutdown proof."""

from typing import Optional, Tuple

from old_path_shutdown import disabled_response

OLD_SYNC_DISABLED = True
MARKETPLACE_EXECUTION_DISABLED = True
GOVERNED_PATH_REQUIRED = True
AMAZON_AUTH_DISABLED = True


class AmazonAuthError(Exception):
    """Raised when retired Amazon auth execution is attempted."""

    def __init__(self, message: str, error_code: str = "OLD_SYNC_DISABLED"):
        super().__init__(message)
        self.error_code = error_code


class AmazonNonJsonResponseError(Exception):
    """Compatibility exception for old callers."""


def safe_parse_json(response, context: str = "Amazon API") -> dict:
    return disabled_response("amazon_auth.safe_parse_json", context=context)


def ensure_access_token(store, force_refresh: bool = False) -> str:
    result = disabled_response("amazon_auth.ensure_access_token", force_refresh=force_refresh)
    raise AmazonAuthError(result["error"])


def mark_store_auth_error(store, error: AmazonAuthError) -> None:
    disabled_response("amazon_auth.mark_store_auth_error", error=str(error))
    return None


def should_skip_amazon_sync(store) -> Tuple[bool, Optional[str]]:
    result = disabled_response("amazon_auth.should_skip_amazon_sync")
    return True, result["error"]


def is_auth_failure(error_message: str = None, status_code: int = None, response_text: str = None) -> bool:
    return True
