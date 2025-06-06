# Interface.py

Establishes a connection to Google sheets. Currently unused in the code.
It will be used in the future to move data from Google sheet/form to the database.

# Setting up
Below is a guide to getting a Google sheets API.
1. Go to https://console.cloud.google.com/projectselector2/apis/credentials/consent?supportedpurview=project and create a new project.
2. User type: External, fill in the required fields, and click save and continue. No need to set up scopes or test users.
3. Go to https://console.cloud.google.com/apis/library/sheets.googleapis.com and enable the Google Sheets API, then click create credentials, and download the json file. (Some issues here, figure it out yourself)
4. Go to https://console.cloud.google.com/apis/credentials and click **create credentials > create service account**. A service account is like a fake user that acts as a bridge between the sheets and the API. Fill in the required fields and click create.
5. Click the pencil icon next to the service account you just created and click **add key > create new key > JSON**. This will download a json file. This is your API key.
6. Create a file called `client_secret.json` in the Google folder and add the credentials, or add the json to your environmental variables as `GOOGLE_CREDENTIALS`.
7. Create a new Google sheet and share it with the email address of the service account you just created. This will allow the service account to access the sheet.
8. You can now use the `Interface` class to access the sheet. You may also want to create a forms to collect data and connect the form and the sheet.


