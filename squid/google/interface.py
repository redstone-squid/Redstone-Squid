import json
import os
from typing import cast

import gspread
from google.oauth2.service_account import Credentials

from squid.core.errors import ConfigurationError


# Establishing connection with Google APIs
def connect() -> tuple[Credentials, gspread.Client]:
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

    if not os.path.isfile("google/client_secret.json"):
        # Getting service account credentials from environment variables
        credentials = os.environ.get("GOOGLE_CREDENTIALS")

        # Checking environment variables exist
        if not credentials:
            msg = "Specify google credentials with a client_secret.json or environment variables."
            raise ConfigurationError(msg, context={"field": "GOOGLE_CREDENTIALS"})

        # Formatting credentials
        credentials_info = json.loads(credentials)
        if not isinstance(credentials_info, dict):
            msg = "GOOGLE_CREDENTIALS must contain a JSON object."
            raise ConfigurationError(msg, context={"field": "GOOGLE_CREDENTIALS"})
        credentials = Credentials.from_service_account_info(cast(dict[str, object], credentials_info), scopes=scopes)
    else:
        # Getting service account credentials from json file
        credentials = Credentials.from_service_account_file("google/client_secret.json", scopes=scopes)

    return credentials, gspread.authorize(credentials)


class Connection:
    """Singleton class to manage the connection to Google Sheets."""

    _CREDS: Credentials | None = None
    _GC: gspread.Client | None = None

    @staticmethod
    def get() -> gspread.Client:
        if Connection._GC is None or Connection._CREDS is None or Connection._CREDS.expired:
            Connection._CREDS, Connection._GC = connect()
        return Connection._GC
