"""Google Sheets client construction from validated configuration."""

from typing import cast

import gspread
from google.oauth2.service_account import Credentials

from squid.config import GoogleConfig
from squid.core.errors import ConfigurationError

SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]


def connect(config: GoogleConfig) -> tuple[Credentials, gspread.Client]:
    """Create authenticated Google credentials and a Sheets client."""
    if config.credentials_info is None:
        msg = "Google credentials are not configured."
        raise ConfigurationError(
            msg,
            context={"field": "SQUID_GOOGLE_CREDENTIALS_JSON"},
            developer_action="Configure either the JSON value or an explicit credentials file.",
        )

    credentials_info = cast(dict[str, object], config.credentials_info)
    credentials = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return credentials, gspread.authorize(credentials)


class Connection:
    """Cache a Google Sheets client for one injected credential configuration."""

    def __init__(self, config: GoogleConfig) -> None:
        self._config = config
        self._credentials: Credentials | None = None
        self._client: gspread.Client | None = None

    def get(self) -> gspread.Client:
        """Return a usable client, refreshing the cached credentials when needed."""
        if self._client is None or self._credentials is None or self._credentials.expired:
            self._credentials, self._client = connect(self._config)
        return self._client
