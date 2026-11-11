"""Production ASGI entry point with JSON-formatted Uvicorn logs."""
import uvicorn

from .config import FORWARDED_ALLOW_IPS, PORT, WEB_CONCURRENCY
from .logging_config import JSON_LOGGING_CONFIG, configure_logging


def main() -> None:
    configure_logging()
    uvicorn.run(
        "support_agent.api:app",
        host="0.0.0.0",
        port=PORT,
        workers=WEB_CONCURRENCY,
        forwarded_allow_ips=FORWARDED_ALLOW_IPS,
        log_config=JSON_LOGGING_CONFIG,
    )


if __name__ == "__main__":
    main()
