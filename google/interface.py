import os
import json

import gspread
from oauth2client.service_account import ServiceAccountCredentials


# Establishing connection with google APIs
def connect():
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

    if not os.path.isfile("google/client_secret.json"):
        # Getting service account credentials from environment variables
        credentials = os.environ.get("GOOGLE_CREDENTIALS")

        # Checking environment variables exist
        if not credentials:
            raise RuntimeError("Specify google credentials with a client_secret.json or environment variables.")

        # Formatting credentials
        credentials = json.loads(credentials)
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials, scopes)  # pyright: ignore [reportArgumentType]
    else:
        # Getting service account credentials from json file
        credentials = ServiceAccountCredentials.from_json_keyfile_name("google/client_secret.json", scopes)  # pyright: ignore [reportArgumentType]

    return credentials, gspread.authorize(credentials)  # pyright: ignore [reportPrivateImportUsage, reportArgumentType]


class Connection:
    """Singleton class to manage the connection to Google Sheets."""

    _CREDS: ServiceAccountCredentials | None = None
    _GC: gspread.Client | None = None  # pyright: ignore [reportPrivateImportUsage]

    @staticmethod
    def get():
        if not Connection._GC or Connection._CREDS.access_token_expired:  # type: ignore
            Connection._CREDS, Connection._GC = connect()
        return Connection._GC
