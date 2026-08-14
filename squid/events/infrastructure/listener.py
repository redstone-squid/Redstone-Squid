"""Optional PostgreSQL LISTEN wake hints for the durable event poller."""

from pydantic import SecretStr

from squid.persistence.wake_listener import PostgresWakeListener

CHANNEL = "squid_domain_events"


class DomainEventWakeListener(PostgresWakeListener):
    """Wake the durable event poller when a domain event is committed."""

    def __init__(self, url: SecretStr, *, reconnect_seconds: float = 5) -> None:
        super().__init__(url, channel=CHANNEL, reconnect_seconds=reconnect_seconds)
