import os
import sys
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Establishing connection with google APIs
def connect():
    scope = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ]

    credentials = None
    if os.path.isfile('Google/client_secret.json'):
        # Getting service account credentials from json file
        credentials = ServiceAccountCredentials.from_json_keyfile_name('Google/client_secret.json', scope)
    else:
        # Getting service account credentials from environment variables
        credentials = {
            "type": os.environ.get('GOOGLE_TYPE'),
            "project_id": os.environ.get('GOOGLE_PROJECT_ID'),
            "private_key_id": os.environ.get('GOOGLE_PRIVATE_KEY_ID'),
            "private_key": os.environ.get('GOOGLE_PRIVATE_KEY'),
            "client_email": os.environ.get('GOOGLE_CLIENT_EMAIL'),
            "client_id": os.environ.get('GOOGLE_CLIENT_ID'),
            "auth_uri": os.environ.get('GOOGLE_AUTH_URI'),
            "token_uri": os.environ.get('GOOGLE_TOKEN_URI'),
            "auth_provider_x509_cert_url": os.environ.get('GOOGLE_AUTH_PROVIDER_X509_CERT_URL'),
            "client_x509_cert_url": os.environ.get('GOOGLE_CLIENT_X509_CERT_URL')
        }
        
        # Checking environment variables exist
        for element in credentials.keys():
            if not credentials[element]:
                raise Exception('Specify google credentials with a client_secret.json or environment variables.')

        # Formatting json into service account credentials object
        credentials = ServiceAccountCredentials.from_json(credentials)

    return gspread.authorize(credentials)

gc = connect()