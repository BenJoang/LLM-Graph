import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.server.connect import get_token
from src.server.data_get import get_sites_data_preview
from src.server.data_process import process_sites_for_llm

def main() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=20000)

    args = parser.parse_args()

    token = get_token()

    sites_data = get_sites_data_preview(
        token = token,
        hours = args.hours,
        limit = args.limit
    )

    summary = process_sites_for_llm(sites_data, hours = args.hours)
    return summary

if __name__ == "__main__":
    summary = main()
    print(summary)