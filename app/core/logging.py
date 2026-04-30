import logging
import sys

import logfire

from app.core.config import settings


def configure_logging() -> None:
    log_level = getattr(logging, settings.app_log_level.upper(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    if settings.logfire_token:
        logfire.configure(token=settings.logfire_token, service_name="jobmatch")
