"""Fixed reason codes only: never log request headers, bodies or exceptions."""
import logging

logger = logging.getLogger('spoon_local.auth_diagnostics')
CODES = frozenset({
    'host_missing', 'host_duplicate', 'host_mismatch', 'cookie_duplicate',
    'auth_disabled', 'origin_missing', 'origin_duplicate', 'origin_null',
    'origin_mismatch', 'content_type_invalid', 'body_too_large', 'form_invalid',
    'session_cookie_missing', 'session_unavailable', 'session_expired',
    'session_mismatch', 'csrf_missing_or_duplicate', 'csrf_mismatch',
    'auth_already_active', 'auth_start_failed',
})


def diagnose(scope, code):
    if code not in CODES:
        raise ValueError('Unknown diagnostic code')
    scope['auth_failure_code'] = code
    logger.warning('auth_rejected:%s', code)


def rejection_headers(scope):
    code = scope.get('auth_failure_code')
    return {'X-Auth-Failure-Code': code} if code in CODES else {}
