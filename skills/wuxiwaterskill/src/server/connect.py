import httpx
import json
import os
import logging

from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

TOKEN_URL = os.getenv("TOKEN_URL")
APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")

def get_token():
    data = {
        "app_id": APP_ID,
        "app_secret": APP_SECRET
    }
    with httpx.Client(timeout=30) as client:
        token_response = client.post(TOKEN_URL, json=data)
        token_response.raise_for_status()

        token_json = token_response.json()
        data = token_json.get("data", {})
        access_token = data.get("access_token", {})

        if not access_token:
            raise RuntimeError(f"没有从响应中取到access_token")

    return access_token


def main():
    logging.basicConfig(level=logging.INFO)
    token = get_token()
    logging.info(token)


if __name__ == "__main__":
    main()