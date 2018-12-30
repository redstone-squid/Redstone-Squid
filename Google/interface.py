import os
import sys
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Establishing connection with google APIs
def connect():
    scopes = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ]

    credentials = None

    if not os.path.isfile('Google/client_secret.json'):
        # Getting service account credentials from environment variables
        credentials = os.environ.get('GOOGLE_CREDENTIALS')
        
        # Checking environment variables exist
        if not credentials:
            raise Exception('Specify google credentials with a client_secret.json or environment variables.')

        # Formatting credentials
        credentials = json.loads(credentials)
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials, scopes)
    else:
        # Getting service account credentials from json file
        credentials = ServiceAccountCredentials.from_json_keyfile_name('Google/client_secret.json', scopes)

    return gspread.authorize(credentials)

gc = connect()