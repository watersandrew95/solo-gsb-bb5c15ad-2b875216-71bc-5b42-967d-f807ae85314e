"""FastAPI 入口：上传 GPX、遮蔽计算、导出、静态页面。"""
from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .geo import MaskCircle, haversine_m, mask_track
from .gpxutil import build_gpx, parse_gpx
from .splits import DEFAULT_SPLIT_KM, MAX_SPLIT_KM, MIN_SPLIT_KM, compute_splits

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

app = FastAPI(title="徒步轨迹分享 · 敏感地点遮蔽")

# 内存存储（演示用途，重启即清空）：track_id -> {"segments": ..., "name": ...}
TRACKS: dict = {}


class CircleIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_m: float = Field(gt=0, le=100_000)


class MaskRequest(BaseModel):
    track_id: str
    circles: list[CircleIn] = []


class SplitsRequest(BaseModel):
    track_id: str
    split_km: float = Field(
        default=DEFAULT_SPLIT_KM, ge=MIN_SPLIT_KM, le=MAX_SPLIT_KM,
        description="每段里程（公里），0.1～10，默认 1",
    )


def _point_dict(p):
    return {
        "lat": p.lat,
        "lon": p.lon,
        "ele": p.ele,
        "time": p.time.isoformat() if p.time else None,
    }


def _get_track(track_id: str):
    track = TRACKS.get(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="轨迹不存在或已过期，请重新上传")
    return track


def _run_mask(track, circles_in):
    circles = [MaskCircle(lat=c.lat, lon=c.lon, radius_m=c.radius_m) for c in circles_in]
    return mask_track(track["segments"], circles)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/upload")
async def upload(file: UploadFile):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 50 MB 限制")
    try:
        segments, name = parse_gpx(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    track_id = uuid.uuid4().hex[:12]
    TRACKS[track_id] = {"segments": segments, "name": name}

    distance = sum(
        haversine_m(a, b) for seg in segments for a, b in zip(seg, seg[1:])
    )
    times = [p.time for seg in segments for p in seg if p.time]
    return {
        "track_id": track_id,
        "name": name,
        "segments": [[_point_dict(p) for p in seg] for seg in segments],
        "stats": {
            "points": sum(len(s) for s in segments),
            "segments": len(segments),
            "distance_m": distance,
            "start_time": min(times).isoformat() if times else None,
            "end_time": max(times).isoformat() if times else None,
        },
    }


@app.post("/api/mask")
def mask(req: MaskRequest):
    track = _get_track(req.track_id)
    result = _run_mask(track, req.circles)
    return {
        "visible_segments": [[_point_dict(p) for p in seg] for seg in result.visible_segments],
        "hidden_segments": [[_point_dict(p) for p in seg] for seg in result.hidden_segments],
        "hidden_intervals": result.hidden_intervals,
        "distance_before_m": result.distance_before_m,
        "distance_after_m": result.distance_after_m,
        "visible_points": sum(len(s) for s in result.visible_segments),
    }


@app.post("/api/export")
def export(req: MaskRequest):
    track = _get_track(req.track_id)
    result = _run_mask(track, req.circles)
    name = f"{track['name']}-masked"
    gpx_bytes = build_gpx(result.visible_segments, name)
    filename = quote(f"{name}.gpx")
    return Response(
        content=gpx_bytes,
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


def _split_dict(sp):
    return {
        "index": sp.index,
        "distance_m": sp.distance_m,
        "duration_s": sp.duration_s,
        "pace_s_per_km": sp.pace_s_per_km,
        "start_time": sp.start_time.isoformat() if sp.start_time else None,
        "end_time": sp.end_time.isoformat() if sp.end_time else None,
    }


@app.post("/api/splits")
def splits(req: SplitsRequest):
    track = _get_track(req.track_id)
    result = compute_splits(track["segments"], req.split_km)
    return {
        "track_id": req.track_id,
        "split_km": req.split_km,
        "segments": [
            {
                "segment_index": seg.segment_index,
                "ok": seg.ok,
                "message": seg.message,
                "total_distance_m": seg.total_distance_m,
                "splits": [_split_dict(sp) for sp in seg.splits],
            }
            for seg in result
        ],
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
