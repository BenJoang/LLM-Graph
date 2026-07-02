import httpx
import json
import os
import logging

from pathlib import Path
from dotenv import load_dotenv
from .connect import get_token
from datetime import datetime, timedelta

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

SITE_TIMESERIES_URL=os.getenv("SITE_TIMESERIES_URL")
GET_SITES_URL=os.getenv("GET_SITES_URL")
DATA_PREVIEW_URL = os.getenv("DATA_PREVIEW_URL")

def get_site_ids(token) -> list:
    with httpx.Client(timeout=30) as client:
        site_resp = client.get(GET_SITES_URL, headers={"AccessToken": token})
        site_resp.raise_for_status()
        site_resp_json = site_resp.json()
        site_ids = site_resp_json.get("sites")

    return site_ids

def get_onesite_timeseries_data(client: httpx.Client, site_id: str, token: str) -> dict:
    resp = client.get(
        SITE_TIMESERIES_URL,
        headers={"AccessToken": token},
        params={
            "site_id":site_id,
            "hours": 1
        }
    )

    resp.raise_for_status()
    return resp.json()

def get_onesite_data_preview(client: httpx.Client, token: str, site_id: str, t0: str, t1: str, limit: int = 200) -> dict:
    resp = client.get(
        DATA_PREVIEW_URL,
        headers={"AccessToken": token},
        params = {
            "site_id":site_id,
            "t0": t0,
            "t1": t1,
            "limit": limit
        }
    )

    resp.raise_for_status()
    return resp.json()

def get_sites_timeseries_data(token: str):
    site_ids = get_site_ids(token)
    results = []
    with httpx.Client(timeout=30) as client:
        for site in site_ids:
            data = get_onesite_timeseries_data(client, token=token, site_id=site)
            logging.info(json.dumps(data, ensure_ascii=False, indent=2))
            results.append(data)
    return results

def get_sites_data_preview(token: str, hours: int = 24, limit: int = 20000):
    site_ids = get_site_ids(token)

    t1_dt = datetime.now()
    t0_dt = t1_dt - timedelta(hours=hours)

    t0 = t0_dt.strftime("%Y-%m-%d %H:%M:%S")
    t1 = t1_dt.strftime("%Y-%m-%d %H:%M:%S")

    results = []

    with httpx.Client(timeout=30) as client:
        for site_id in site_ids:
            data = get_onesite_data_preview(
                client=client,
                token=token,
                site_id=site_id,
                t0=t0,
                t1=t1,
                limit=limit,
            )
            results.append(data)

    return results


def main():
    logging.basicConfig(level=logging.INFO)
    token = get_token()
    results = get_sites_data_preview(token, hours=24)
    #logging.info(summary_text)

def test1():
    logging.basicConfig(level=logging.INFO)
    token = get_token()
    t1_dt = datetime.now()
    t0_dt = t1_dt - timedelta(hours=5)

    t0 = t0_dt.strftime("%Y-%m-%d %H:%M:%S")
    t1 = t1_dt.strftime("%Y-%m-%d %H:%M:%S")
    with httpx.Client(timeout=30) as client:
        data = get_onesite_data_preview(
            client=client, 
            token = token, 
            site_id="大宁河大桥", 
            t0=t0,
            t1=t1,
            limit=10
        )
        logging.info(json.dumps(data, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()