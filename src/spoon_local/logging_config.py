"""Uvicorn access records contain only method, path and status, never queries."""

import logging


class SafeAccessFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, version, status = record.args
            record.args = (client, method, path.split("?", 1)[0], version, status)
        else:
            record.msg = "HTTP request completed"
            record.args = ()
        return True


def configure_safe_access_logs():
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, SafeAccessFilter) for item in logger.filters):
        logger.addFilter(SafeAccessFilter())


def configure_safe_http_logs():
    """Sanitize transport records before any handler can render headers/errors."""
    previous = logging.getLogRecordFactory()
    if getattr(previous, '_spoon_safe_http', False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        if record.name == 'httpx' or record.name.startswith(('httpx.', 'httpcore')):
            record.msg = 'HTTP transport activity (details omitted)'
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record

    factory._spoon_safe_http = True
    logging.setLogRecordFactory(factory)
