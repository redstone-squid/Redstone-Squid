# Setting up
This project uses google sheets as the database. You're welcome :)
Below is a guide to getting a google sheets API key, and setting up the spreadsheet so that the codebase runs without errors, as it relies heavily on the structure of the spreadsheet and have a lot of assumptions.
1. Go to https://console.cloud.google.com/projectselector2/apis/credentials/consent?supportedpurview=project and create a new project.
2. User type: External, fill in the required fields, and click save and continue. No need to set up scopes or test users.
3. ?? Go to https://console.cloud.google.com/apis/credentials and click create credentials
4. ?? (Optional) Click Edit API key and restrict it to Google Sheets API only.
5. Go to https://console.cloud.google.com/apis/library/sheets.googleapis.com and enable the Google Sheets API, then click create credentials, and download the json file. (Some issues here, figure it out yourself)
6. Create a file called `client_secret.json` in the Google folder and add the credentials.

