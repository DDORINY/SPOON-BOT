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
