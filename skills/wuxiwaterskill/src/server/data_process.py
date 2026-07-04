from .match_level_strategy import match_level_strategy
from datetime import datetime


def _to_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
    
def _max_row(rows: list[dict], field: str) -> dict | None:
    valid_rows = [
        row for row in rows
        if _to_float(row.get(field)) is not None
    ]
    if not valid_rows:
        return None

    return max(valid_rows, key=lambda row: _to_float(row.get(field)))

def _fmt(row: dict | None, field: str, unit: str) -> str:
    if not row:
        return "无数据"

    value = _to_float(row.get(field))
    time_str = row.get("time_str") or row.get("video_start_time") or "时间未知"

    if value is None:
        return "无数据"

    return f"{value:g}{unit}，时间：{time_str}"

def summarize_one_site(site_data: dict) -> dict:
    site_id = site_data.get("site_id", "未知站点")

    rain_rows = site_data.get("rain_rows") or []
    radar_rows = site_data.get("radar_rows") or []

    discharge_rows = site_data.get("discharge_rows") or []

    max_rain_cum = _max_row(rain_rows, "rain_cum_mm")
    max_rader_water_depth = _max_row(radar_rows, "water_depth_m")
    max_rader_surface_velocity = _max_row(radar_rows, "surface_velocity_mps")

    max_py_surface_velocity = _max_row(discharge_rows, "average_speed_mps")
    max_py_water_depth = _max_row(discharge_rows, "water_level_m")

    return {
        "site_id": site_id,
        "max_rain_cum": max_rain_cum,
        "max_rader_water_depth": max_rader_water_depth,
        "max_rader_surface_velocity": max_rader_surface_velocity,
        "max_py_surface_velocity": max_py_surface_velocity,
        "max_py_water_depth": max_py_water_depth,
    }

def _global_max(site_summaries: list[dict], row_key: str, field: str) -> tuple[str, dict] | None:
    candidates = []

    for summary in site_summaries:
        row = summary.get(row_key)
        if not row:
            continue

        value = _to_float(row.get(field))
        if value is None:
            continue

        candidates.append((summary["site_id"], row))

    if not candidates:
        return None

    return max(candidates, key=lambda item: _to_float(item[1].get(field)))

def _latest_row(rows: list[dict]) -> dict | None:
    valid_rows = [
        row for row in rows
        if row.get("created_ts") is not None
        or row.get("ts") is not None
        or row.get("time_str")
    ]

    if not valid_rows:
        return None

    return max(
        valid_rows,
        key=lambda row: (
            row.get("created_ts")
            or row.get("ts")
            or row.get("time_str")
            or ""
        )
    )

def _latest_valid_discharge_row(rows: list[dict]) -> dict | None:
    valid_rows = []

    for row in rows:
        water_level = _to_float(row.get("water_level_m"))
        average_speed = _to_float(row.get("average_speed_mps"))
        flow = _to_float(row.get("flow_m3s"))

        if water_level is None and average_speed is None and flow is None:
            continue

        valid_rows.append(row)

    return _latest_row(valid_rows)

def _row_ts(row: dict) -> float | None:
    ts = row.get("created_ts") or row.get("ts")
    if ts is not None:
        try:
            return float(ts)
        except (TypeError, ValueError):
            pass

    time_str = row.get("time_str") or row.get("video_start_time")
    if not time_str:
        return None

    try:
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None
    
def _nearest_row_by_time(rows: list[dict], target_row: dict) -> dict | None:
    target_ts = _row_ts(target_row)
    if target_ts is None:
        return None

    candidates = []

    for row in rows:
        row_ts = _row_ts(row)
        if row_ts is None:
            continue

        candidates.append((abs(row_ts - target_ts), row))

    if not candidates:
        return None

    return min(candidates, key=lambda item: item[0])[1]

def _sum_rain_inst_12h_before_max_cum(rain_rows: list[dict]) -> float | None:
    max_cum_row = _max_row(rain_rows, "rain_cum_mm")
    if not max_cum_row:
        return None

    end_ts = _row_ts(max_cum_row)
    if end_ts is None:
        return None

    start_ts = end_ts - 12 * 60 * 60
    total = 0.0
    has_value = False

    for row in rain_rows:
        row_ts = _row_ts(row)
        if row_ts is None:
            continue

        if row_ts < start_ts or row_ts > end_ts:
            continue

        rain_inst = _to_float(row.get("rain_inst_mm"))
        if rain_inst is None:
            continue

        total += rain_inst
        has_value = True

    return total if has_value else None

def _append_metric_line(lines: list[str], label: str, row: dict | None, field: str, unit: str):
    if not row:
        return

    value = _to_float(row.get(field))
    if value is None:
        return

    time_str = row.get("time_str") or row.get("video_start_time") or "时间未知"
    lines.append(f"- {label}：{value:g}{unit}，时间：{time_str}")

def _get_level_name(
    *,
    site_id: str,
    rain_12h_mm: float | None = None,
    radar_water_level_m: float | None = None,
    algorithm_water_level_m: float | None = None,
    radar_flow_speed_mps: float | None = None,
    algorithm_flow_speed_mps: float | None = None,
) -> str:
    try:
        result = match_level_strategy(
            site_id=site_id,
            rain_12h_mm=rain_12h_mm or 0.0,
            radar_water_level_m=radar_water_level_m or 0.0,
            algorithm_water_level_m=algorithm_water_level_m or 0.0,
            radar_flow_speed_mps=radar_flow_speed_mps or 0.0,
            algorithm_flow_speed_mps=algorithm_flow_speed_mps or 0.0,
        )
    except Exception:
        return "未匹配到等级策略"

    return result.get("level_name") or "正常"

def has_latest_discharge_level_at_least(
    sites_data: list[dict],
    min_level: int = 3,
) -> dict:
    matched_sites = []

    for site_data in sites_data:
        if not isinstance(site_data, dict):
            continue

        site_id = site_data.get("site_id", "未知站点")
        rain_rows = site_data.get("rain_rows") or []
        radar_rows = site_data.get("radar_rows") or []
        discharge_rows = site_data.get("discharge_rows") or site_data.get("discharge") or []

        latest = _latest_valid_discharge_row(discharge_rows)
        if not latest:
            continue

        latest_radar = _nearest_row_by_time(radar_rows, latest)
        rain_12h_mm = _sum_rain_inst_12h_before_max_cum(rain_rows)

        water_level = _to_float(latest.get("water_level_m"))
        average_speed = _to_float(latest.get("average_speed_mps"))
        radar_water_level = _to_float((latest_radar or {}).get("water_depth_m"))
        radar_flow_speed = _to_float((latest_radar or {}).get("surface_velocity_mps"))

        try:
            level_result = match_level_strategy(
                site_id=site_id,
                rain_12h_mm=rain_12h_mm,
                radar_water_level_m=radar_water_level,
                algorithm_water_level_m=water_level,
                radar_flow_speed_mps=radar_flow_speed,
                algorithm_flow_speed_mps=average_speed,
            )
        except Exception:
            continue

        level_index = int(level_result.get("level_index") or 0)

        if level_index >= min_level:
            matched_sites.append({
                "site_id": site_id,
                "level_index": level_index,
                "level_name": level_result.get("level_name") or "未知",
                "time": latest.get("time_str") or latest.get("video_start_time"),
            })

    return {
        "matched": bool(matched_sites),
        "min_level": min_level,
        "matched_sites": matched_sites,
    }


def build_llm_summary(site_summaries: list[dict], hours: int) -> str:
    lines = []
    lines.append(r"过去{hours}小时水雨情摘要")
    lines.append(f"共统计 {len(site_summaries)} 个站点。")

    for index, summary in enumerate(site_summaries, start=1):
        site_id = summary["site_id"]
        latest_radar = summary.get("latest_radar") or {}
        latest_discharge = summary.get("latest_discharge") or {}

        lines.append(f"{index}. {site_id}")
        _append_metric_line(lines, "最大累计雨量", summary.get("max_rain_cum"), "rain_cum_mm", "mm")
        _append_metric_line(lines, "最大雷达水深", summary.get("max_rader_water_depth"), "water_depth_m", "m")
        _append_metric_line(lines, "最大雷达表面流速", summary.get("max_rader_surface_velocity"), "surface_velocity_mps", "m/s")
        _append_metric_line(lines, "最大算法测量水深", summary.get("max_py_water_depth"), "water_level_m", "m")
        _append_metric_line(lines, "最大算法测量流速", summary.get("max_py_surface_velocity"), "average_speed_mps", "m/s")

        if latest_radar:
            lines.append(
                "- 最新雷达数据："
                f"水深 {_to_float(latest_radar.get('water_depth_m'))}m，"
                f"表面流速 {_to_float(latest_radar.get('surface_velocity_mps'))}m/s，"
                f"流量 {_to_float(latest_radar.get('flow_m3s'))}m3/s，"
                f"时间：{latest_radar.get('time_str', '时间未知')}"
            )

        if latest_discharge:
            lines.append(
                "- 最新测流数据："
                f"水位 {_to_float(latest_discharge.get('water_level_m'))}m，"
                f"平均流速 {_to_float(latest_discharge.get('average_speed_mps'))}m/s，"
                f"流量 {_to_float(latest_discharge.get('flow_m3s'))}m3/s，"
                f"状态：{latest_discharge.get('status', '未知')}，"
                f"时间：{latest_discharge.get('time_str', '时间未知')}"
            )

        lines.append("")


    return "\n".join(lines)


def process_sites_for_llm(sites_data: list[dict], hours: int) -> str:
    site_summaries = [
        summarize_one_site(site_data)
        for site_data in sites_data
        if isinstance(site_data, dict)
    ]

    if not site_summaries:
        return r"过去{hours}小时水雨情摘要：没有获取到有效站点数据。"

    return build_llm_summary(site_summaries, hours=hours)

def process_latest_discharge_for_llm(sites_data: list[dict]) -> str:
    lines = []
    lines.append("各站点最近一次测流数据")
    lines.append("")

    valid_count = 0

    for index, site_data in enumerate(sites_data, start=1):
        if not isinstance(site_data, dict):
            continue

        site_id = site_data.get("site_id", "未知站点")
        rain_rows = site_data.get("rain_rows") or []
        radar_rows = site_data.get("radar_rows") or []
        discharge_rows = site_data.get("discharge_rows") or site_data.get("discharge") or []

        latest = _latest_valid_discharge_row(discharge_rows)
        if not latest:
            continue

        latest_radar = _nearest_row_by_time(radar_rows, latest)
        nearest_rain = _nearest_row_by_time(rain_rows, latest)
        rain_12h_mm = _sum_rain_inst_12h_before_max_cum(rain_rows)

        

        parts = []

        water_level = _to_float(latest.get("water_level_m"))
        average_speed = _to_float(latest.get("average_speed_mps"))
        flow = _to_float(latest.get("flow_m3s"))

        radar_water_level = _to_float((latest_radar or {}).get("water_depth_m"))
        radar_flow_speed = _to_float((latest_radar or {}).get("surface_velocity_mps"))

        level_name = _get_level_name(
            site_id=site_id,
            rain_12h_mm=rain_12h_mm,
            radar_water_level_m=radar_water_level,
            algorithm_water_level_m=water_level,
            radar_flow_speed_mps=radar_flow_speed,
            algorithm_flow_speed_mps=average_speed,
        )

        if water_level is not None:
            parts.append(f"算法测量水位{water_level:g}m")

        if average_speed is not None:
            parts.append(f"算法测量流速{average_speed:g}m/s")

        if flow is not None:
            parts.append(f"算法测量增量流量{flow:g}m3/s")

        if not parts:
            continue

        time_str = latest.get("time_str") or latest.get("video_start_time") or "时间未知"
        status = latest.get("status")

        valid_count += 1
        lines.append(f"{valid_count}. {site_id}")
        lines.append(f"- 警戒等级：{level_name}")
        lines.append(f"- 最近测流数据：" + "，".join(parts) + f"，时间：{time_str}")
        if latest_radar:
            radar_parts = []

            radar_water_depth = _to_float(latest_radar.get("water_depth_m"))
            radar_surface_velocity = _to_float(latest_radar.get("surface_velocity_mps"))
            radar_flow = _to_float(latest_radar.get("flow_m3s"))

            if radar_water_depth is not None:
                radar_parts.append(f"雷达测量水深{radar_water_depth:g}m")

            if radar_surface_velocity is not None:
                radar_parts.append(f"雷达测量表面流速{radar_surface_velocity:g}m/s")

            if radar_parts:
                radar_time_str = latest_radar.get("time_str") or "时间未知"
                lines.append("- 相关雷达数据：" + "，".join(radar_parts) + f"，时间：{radar_time_str}")

        if nearest_rain:
            rain_cum = _to_float(nearest_rain.get("rain_cum_mm"))

            if rain_cum is not None:
                rain_time_str = nearest_rain.get("time_str") or "时间未知"
                lines.append(f"- 相关雨量数据：累计雨量{rain_cum:g}mm，时间：{rain_time_str}")
            if rain_12h_mm is not None:
                lines.append(f"- 最近12小时雨量和：{rain_12h_mm:g}mm")

        lines.append("")

    if valid_count == 0:
        return "各站点最近一次测流数据：没有获取到有效测流数据。"

    return "\n".join(lines)



