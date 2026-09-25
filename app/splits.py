"""分段配速统计：按固定里程把每个原始 trkseg 切成若干段。

- 每个原始 trkseg 独立从 0 累计 haversine 球面距离，互不合并；
- 采样线段跨越一个或多个里程边界时，按距离比例在线段两端时间内线性插值边界时间；
- 末尾不足一段的尾段也列出；
- 相邻轨迹点时间不递增（或缺时间）时，该 trkseg 标记为无法计算；
- 零里程分段的配速为 None（前端显示 "—"，不出现无穷配速）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .geo import TrackPoint, haversine_m

MIN_SPLIT_KM = 0.1
MAX_SPLIT_KM = 10.0
DEFAULT_SPLIT_KM = 1.0

# 尾段距离小于该值视为"恰好整段"，不再单列尾段（浮点余量）
_TAIL_EPS_M = 1e-6


@dataclass
class Split:
    index: int                      # 该 trkseg 内的段号（从 1 开始）
    distance_m: float
    duration_s: float
    pace_s_per_km: Optional[float]  # 零里程时为 None
    start_time: Optional[datetime]
    end_time: Optional[datetime]


@dataclass
class SegmentSplits:
    segment_index: int              # 原始 trkseg 序号（从 1 开始）
    ok: bool
    message: Optional[str]          # ok=False 时说明无法计算的原因
    total_distance_m: float
    splits: list                    # list[Split]


def _make_split(index: int, distance_m: float, start_time, end_time) -> Split:
    duration = (end_time - start_time).total_seconds()
    pace = None
    if distance_m > 0.0:  # 零里程不计算配速，避免无穷大
        pace = duration / (distance_m / 1000.0)
    return Split(
        index=index,
        distance_m=distance_m,
        duration_s=duration,
        pace_s_per_km=pace,
        start_time=start_time,
        end_time=end_time,
    )


def _time_problem(seg) -> Optional[str]:
    """返回该 trkseg 无法计算配速的原因；可计算时返回 None。"""
    for i, (a, b) in enumerate(zip(seg, seg[1:])):
        if a.time is None or b.time is None:
            return "存在缺少时间的轨迹点，无法计算配速"
        if b.time <= a.time:
            return f"相邻轨迹点时间不递增（第 {i + 1}、{i + 2} 个点），无法计算配速"
    return None


def _segment_splits(seg, segment_index: int, split_m: float) -> SegmentSplits:
    total = 0.0
    for a, b in zip(seg, seg[1:]):
        total += haversine_m(a, b)

    problem = _time_problem(seg)
    if problem is not None:
        return SegmentSplits(segment_index, False, problem, total, [])

    splits: list = []
    if len(seg) >= 2:
        acc = 0.0                    # 当前采样线段起点的累计里程
        next_boundary = split_m      # 下一个里程边界
        span_start_time = seg[0].time
        span_start_dist = 0.0
        for a, b in zip(seg, seg[1:]):
            d = haversine_m(a, b)
            # 一条采样线段可能跨越多个里程边界，逐个切出并按距离比例插值时间
            while d > 0.0 and acc + d >= next_boundary:
                ratio = (next_boundary - acc) / d
                boundary_time = a.time + (b.time - a.time) * ratio
                splits.append(_make_split(
                    len(splits) + 1,
                    next_boundary - span_start_dist,
                    span_start_time,
                    boundary_time,
                ))
                span_start_time = boundary_time
                span_start_dist = next_boundary
                next_boundary += split_m
            acc += d
        # 末尾不足一段也列出；恰好整段（浮点余量内）则不再单列
        remaining = total - span_start_dist
        if remaining > _TAIL_EPS_M or not splits:
            splits.append(_make_split(
                len(splits) + 1, remaining, span_start_time, seg[-1].time,
            ))

    return SegmentSplits(segment_index, True, None, total, splits)


def compute_splits(segments, split_km: float) -> list:
    """对每个原始 trkseg 分别按 split_km 公里切段，返回 list[SegmentSplits]。"""
    if not (MIN_SPLIT_KM <= split_km <= MAX_SPLIT_KM):
        raise ValueError(
            f"每段里程须在 {MIN_SPLIT_KM}～{MAX_SPLIT_KM} 公里之间"
        )
    split_m = split_km * 1000.0
    return [
        _segment_splits(seg, idx, split_m)
        for idx, seg in enumerate(segments, start=1)
    ]
