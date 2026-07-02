site-timeseries返回数据示例：

{
  "site_id": "大河乡大桥",
  "hours": 24,
  "rain": [
    {
      "ts": 1781499600,
      "time_str": "2026-06-15 10:20:00",
      "site_id": "大河乡大桥",
      "device_id": "RAIN001",
      "rain_inst_mm": 0.2,
      "rain_cum_mm": 12.4
    }
  ],
  "radar": [
    {
      "ts": 1781499600,
      "time_str": "2026-06-15 10:20:00",
      "site_id": "大河乡大桥",
      "device_id": "RADAR001",
      "water_depth_m": 1.23,
      "surface_velocity_mps": 0.85,
      "flow_m3s": 8.91,
      "cum_volume_m3": 12345.6
    }
  ],
  "latest_radar": {
    "ts": 1781499600,
    "time_str": "2026-06-15 10:20:00",
    "site_id": "大河乡大桥",
    "device_id": "RADAR001",
    "water_depth_m": 1.23,
    "surface_velocity_mps": 0.85,
    "flow_m3s": 8.91,
    "cum_volume_m3": 12345.6
  },
  "latest_discharge": {
    "created_ts": 1781499660,
    "time_str": "2026-06-15 10:21:00",
    "site_id": "大河乡大桥",
    "station_name": "大河乡大桥",
    "serial": "GK4632683",
    "channel_no": 1,
    "water_level_m": 1.24,
    "average_speed_mps": 0.82,
    "flow_m3s": 8.76,
    "status": "OK",
    "message": "",
    "video_start_time": "2026-06-15 10:20:00",
    "video_end_time": "2026-06-15 10:20:30",
    "video_path": "/LQJ_floodvideos/flood_alert/data/video_download/GK4632683_dahexiang/ch1/2026-06-15/xxx.mp4",
    "source": "video_polling",
    "measurement_mode": "discharge"
  }
}

data-preview输入示例：
site_id	是	无	站点 ID
t0	是	无	开始时间，例如 2026-06-15 00:00:00
t1	是	无	结束时间，例如 2026-06-15 23:59:59
limit	否	200	每类数据最多返回条数，最大 20000

返回示例：
{
  "site_id": "大河乡大桥",
  "t0": "2026-06-15 00:00:00",
  "t1": "2026-06-15 23:59:59",
  "counts": {
    "rain": 10,
    "radar": 10,
    "discharge": 3,
    "ingest_runs": 1,
    "video_artifacts": 3
  },
  "rain_rows": [],
  "radar_rows": [],
  "discharge_rows": [],
  "ingest_runs": [],
  "video_artifacts": []
}