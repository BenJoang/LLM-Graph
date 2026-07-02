from .data_get import get_token, get_sites_data_preview
from .data_process import process_latest_discharge_for_llm

if __name__ == "__main__":
    token = get_token()
    sites_data = get_sites_data_preview(token, hours=12)
    summary = process_latest_discharge_for_llm(sites_data)
    print(summary)
