import json
import os
import urllib.parse
import urllib.request

from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GCSI_GRANITE_API_KEY")
project_id = os.getenv("GCSI_GRANITE_PROJECT_ID")
api_url = os.getenv("GCSI_GRANITE_API_URL")

if "version=" not in api_url:
    separator = "&" if "?" in api_url else "?"
    api_url = f"{api_url}{separator}version=2023-05-29"
model_id = os.getenv("GCSI_GRANITE_MODEL_ID")

print("PROJECT:", project_id)
print("URL:", api_url)
print("MODEL:", model_id)
print("KEY:", "SET" if api_key else "MISSING")

# ---------------------------------------------------------
# 1. Get IBM IAM access token
# ---------------------------------------------------------

iam_url = "https://iam.cloud.ibm.com/identity/token"

iam_data = urllib.parse.urlencode({
    "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
    "apikey": api_key,
}).encode()

iam_request = urllib.request.Request(
    iam_url,
    data=iam_data,
    headers={
        "Content-Type": "application/x-www-form-urlencoded",
    },
)

try:
    with urllib.request.urlopen(iam_request) as response:
        iam_body = json.loads(response.read())

    access_token = iam_body["access_token"]

    print("IAM HTTP: 200")
    print("IAM TOKEN: RECEIVED")

except urllib.error.HTTPError as e:
    print("IAM HTTP:", e.code)
    print("IAM FAILED")
    print(e.read().decode())
    raise SystemExit(1)

# ---------------------------------------------------------
# 2. Call Granite
# ---------------------------------------------------------

payload = {
    "model_id": model_id,
    "input": "Say hello in one short sentence.",
    "parameters": {
        "decoding_method": "greedy",
        "max_new_tokens": 50,
    },
    "project_id": project_id,
}

request = urllib.request.Request(
    api_url,
    data=json.dumps(payload).encode(),
    headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    },
    method="POST",
)

try:
    with urllib.request.urlopen(request) as response:
        body = json.loads(response.read())

    print("Granite HTTP: 200")
    print("RESULT: INFERENCE SUCCESS")
    print("RESPONSE:", json.dumps(body, indent=2))

except urllib.error.HTTPError as e:
    body = e.read().decode()

    print("Granite HTTP:", e.code)
    print("RESULT: INFERENCE FAILED")
    print("RESPONSE:", body)