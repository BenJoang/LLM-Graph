from .connect import get_token
from .data_get import get_sites_data_preview
from .data_process import has_latest_discharge_level_at_least


def check_latest_discharge_warning(
    *,
    hours: int = 2,
    limit: int = 2000,
    min_level: int = 3,
) -> dict:
    token = get_token()

    sites_data = get_sites_data_preview(
        token=token,
        hours=hours,
        limit=limit,
    )

    return has_latest_discharge_level_at_least(
        sites_data,
        min_level=min_level,
    )