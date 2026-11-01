import os, requests
from dotenv import load_dotenv
load_dotenv()

resp = requests.post("https://www.strava.com/oauth/token", data={
    "client_id": os.getenv("STRAVA_CLIENT_ID"),
    "client_secret": os.getenv("STRAVA_CLIENT_SECRET"),
    "code": input("Paste code: "),
    "grant_type": "authorization_code",
})
resp.raise_for_status()
print("Refresh token:", resp.json()["refresh_token"])