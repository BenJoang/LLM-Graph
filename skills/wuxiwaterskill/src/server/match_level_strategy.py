from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_PATH = Path(__file__).with_name("level_strategy.json")


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _level_index(level: Dict[str, Any], fallback: int) -> int:
    try:
        return int(level.get("index") or fallback)
    except (TypeError, ValueError):
        return fallback


def _infer_channel_no(camera: Dict[str, Any]) -> Optional[int]:
    for key in ("channel_no", "channel"):
        value = camera.get(key)
        if value is not None and str(value).strip():
            try:
                return int(value)
            except (TypeError, ValueError):
                pass

    json_path = str(camera.get("json_path") or "")
    match = re.search(r"(?:^|[\\/_.-])ch(\d+)(?:[\\/_.-]|$)", json_path, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"找不到策略配置文件: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"策略配置文件格式错误: {path}")
    return data

def _find_camera(config: Dict[str, Any], serial: str, channel_no: int) -> Dict[str, Any]:
    serial_norm = str(serial or "").strip().upper()
    if not serial_norm:
        raise ValueError("serial 不能为空")

    candidates = []
    for camera in config.get("cameras") or []:
        if not isinstance(camera, dict):
            continue
        if str(camera.get("serial") or "").strip().upper() != serial_norm:
            continue
        camera_channel = _infer_channel_no(camera)
        if camera_channel is None or camera_channel == int(channel_no):
            candidates.append(camera)

    if not candidates:
        raise ValueError(f"未找到匹配的摄像头策略: serial={serial}, channel_no={channel_no}")
    return candidates[0]

def _norm_text(value: Any) -> str:
    return str(value or "").strip()


def _find_camera_by_site_id(config: Dict[str, Any], site_id: str) -> Dict[str, Any]:
    site_norm = _norm_text(site_id)
    if not site_norm:
        raise ValueError("site_id 不能为空")

    for camera in config.get("cameras") or []:
        if not isinstance(camera, dict):
            continue

        aliases = camera.get("aliases") or []
        candidates = [
            camera.get("site_id"),
            camera.get("name"),
            camera.get("key"),
            *aliases,
        ]

        if site_norm in {_norm_text(item) for item in candidates if _norm_text(item)}:
            return camera

    raise ValueError(f"未找到匹配的站点策略: site_id={site_id}")


def _shared_level(config: Dict[str, Any], level_index: int) -> Dict[str, Any]:
    shared = config.get("shared") if isinstance(config.get("shared"), dict) else {}
    for item in shared.get("levels") or []:
        if isinstance(item, dict) and _level_index(item, 0) == level_index:
            return item
    return {}


def _metric_matches(label: str, value: float, threshold: Optional[float], unit: str) -> Optional[Dict[str, Any]]:
    if threshold is None:
        return None
    return {
        "metric": label,
        "value": value,
        "threshold": threshold,
        "unit": unit,
        "matched": value >= threshold,
    }


def match_level_strategy(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    site_id: Optional[str] = None,
    serial: Optional[str] = None,
    channel_no: Optional[int] = None,
    rain_12h_mm: float = 0.0,
    radar_water_level_m: float = 0.0,
    algorithm_water_level_m: float = 0.0,
    radar_flow_speed_mps: float = 0.0,
    algorithm_flow_speed_mps: float = 0.0,
) -> Dict[str, Any]:
    config = _load_config(config_path)

    if site_id:
        camera = _find_camera_by_site_id(config, site_id)
    elif serial:
        camera = _find_camera(config, serial, channel_no or 1)
    else:
        raise ValueError("site_id 或 serial 至少需要提供一个")

    measurement_time_sec = _safe_float(
        (config.get("shared") or {}).get("measurement_time_sec"),
        default=20.0,
    )

    rain_12h_mm = _safe_float(rain_12h_mm)
    radar_water_level_m = _safe_float(radar_water_level_m)
    algorithm_water_level_m = _safe_float(algorithm_water_level_m)
    radar_flow_speed_mps = _safe_float(radar_flow_speed_mps)
    algorithm_flow_speed_mps = _safe_float(algorithm_flow_speed_mps)

    effective_water_level_m = max(
        radar_water_level_m,
        algorithm_water_level_m,
    )
    effective_flow_speed_mps = max(
        radar_flow_speed_mps,
        algorithm_flow_speed_mps,
    )

    matched_levels: List[Dict[str, Any]] = []
    levels = camera.get("levels") or []

    for fallback_index, level in enumerate(levels, start=1):
        if not isinstance(level, dict):
            continue

        level_index = _level_index(level, fallback_index)
        level_name = str(level.get("name") or f"{level_index}级")
        shared_level = _shared_level(config, level_index)

        checks = [
            _metric_matches(
                "雨量传感器12小时内降雨量",
                rain_12h_mm,
                _optional_float(level.get("rain_12h_mm")),
                "mm",
            ),
            _metric_matches(
                "雷达传感器/算法测量水位",
                effective_water_level_m,
                _optional_float(level.get("sensor_algorithm_water_level_m")),
                "m",
            ),
            _metric_matches(
                "雷达传感器/算法测量流速",
                effective_flow_speed_mps,
                _optional_float(level.get("sensor_algorithm_flow_speed_mps")),
                "m/s",
            ),
        ]

        matched_checks = [
            check for check in checks
            if check is not None and check["matched"]
        ]

        if not matched_checks:
            continue

        matched_levels.append(
            {
                "index": level_index,
                "name": level_name,
                "measurement_time_sec": measurement_time_sec,
                "trigger_interval_min": _optional_float(
                    shared_level.get("trigger_interval_min")
                ),
                "matched_criteria": matched_checks,
            }
        )

    best = max(matched_levels, key=lambda item: item["index"], default=None)
    resolved_channel_no = channel_no if channel_no is not None else _infer_channel_no(camera)

    return {
        "matched": best is not None,
        "level_name": best.get("name") if best else "正常",
        "level_index": best.get("index") if best else 0,
        "site_id": site_id or camera.get("site_id") or camera.get("name"),
        "serial": serial or camera.get("serial"),
        "channel_no": resolved_channel_no,
        "camera": {
            "key": camera.get("key"),
            "name": camera.get("name"),
            "site_id": camera.get("site_id"),
            "serial": camera.get("serial"),
            "channel_no": _infer_channel_no(camera),
            "json_path": camera.get("json_path"),
            "base_water_level_m": camera.get("base_water_level_m"),
        },
        "inputs": {
            "rain_12h_mm": rain_12h_mm,
            "radar_water_level_m": radar_water_level_m,
            "algorithm_water_level_m": algorithm_water_level_m,
            "radar_flow_speed_mps": radar_flow_speed_mps,
            "algorithm_flow_speed_mps": algorithm_flow_speed_mps,
            "effective_water_level_m": effective_water_level_m,
            "effective_flow_speed_mps": effective_flow_speed_mps,
        },
        "matched_level": best,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Match current sensor inputs against the five-level strategy config.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="策略配置 JSON 路径，默认 config/level_strategy.json")
    parser.add_argument("--serial", required=True, help="当前摄像头序列号")
    parser.add_argument("--channel-no", type=int, required=True, help="当前摄像头通道号")
    parser.add_argument("--rain-12h-mm", type=float, default=0.0, help="雨量传感器12小时内降雨量，默认 0")
    parser.add_argument("--radar-water-level-m", type=float, default=0.0, help="雷达传感器水位，默认 0")
    parser.add_argument("--algorithm-water-level-m", type=float, default=0.0, help="算法测量水位，默认 0")
    parser.add_argument("--radar-flow-speed-mps", type=float, default=0.0, help="雷达传感器流速，默认 0")
    parser.add_argument("--algorithm-flow-speed-mps", type=float, default=0.0, help="算法测量流速，默认 0")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    result = match_level_strategy(
        config_path=Path(args.config),
        serial=args.serial,
        channel_no=args.channel_no,
        rain_12h_mm=args.rain_12h_mm,
        radar_water_level_m=args.radar_water_level_m,
        algorithm_water_level_m=args.algorithm_water_level_m,
        radar_flow_speed_mps=args.radar_flow_speed_mps,
        algorithm_flow_speed_mps=args.algorithm_flow_speed_mps,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
