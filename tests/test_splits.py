"""分段配速统计的单元测试。

可直接运行：python3 tests/test_splits.py
或使用 pytest：python3 -m pytest tests/
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.geo import TrackPoint, haversine_m
from app.gpxutil import parse_gpx
from app.splits import compute_splits

T0 = datetime(2026, 5, 1, 8, 0, 0, tzinfo=timezone.utc)


def make_seg(points):
    """points: [(lat, lon, 秒偏移), ...]"""
    return [
        TrackPoint(lat=la, lon=lo, ele=500.0, time=T0 + timedelta(seconds=s))
        for la, lo, s in points
    ]


def test_segment_crossing_multiple_boundaries():
    """一条采样线段跨越多个里程边界：逐段切出并按距离比例插值边界时间。"""
    a = TrackPoint(lat=31.0, lon=121.0, time=T0)
    b = TrackPoint(lat=31.01, lon=121.0, time=T0 + timedelta(seconds=100))
    d = haversine_m(a, b)  # 约 1109 m，跨越 500 m 与 1000 m 两个边界
    res = compute_splits([[a, b]], 0.5)

    assert len(res) == 1 and res[0].ok
    splits = res[0].splits
    assert len(splits) == 3  # 两个整段 + 末尾不足一段的尾段
    assert abs(splits[0].distance_m - 500.0) < 1e-6
    assert abs(splits[1].distance_m - 500.0) < 1e-6
    assert abs(splits[2].distance_m - (d - 1000.0)) < 1e-6

    # 边界时间按距离比例插值：t = 100 * boundary / d
    t1 = T0 + timedelta(seconds=100.0 * 500.0 / d)
    t2 = T0 + timedelta(seconds=100.0 * 1000.0 / d)
    assert abs((splits[0].end_time - t1).total_seconds()) < 0.01
    assert abs((splits[1].start_time - t1).total_seconds()) < 0.01
    assert abs((splits[1].end_time - t2).total_seconds()) < 0.01
    assert splits[0].start_time == T0
    assert splits[2].end_time == b.time

    # 匀速运动：各段配速一致，pace = 100 s / (d m) 换算为 s/km
    expected_pace = 100.0 / (d / 1000.0)
    for sp in splits:
        assert abs(sp.pace_s_per_km - expected_pace) < 1e-4
        assert abs(sp.duration_s - expected_pace * sp.distance_m / 1000.0) < 1e-4


def test_distance_accumulated_per_trkseg():
    """每个原始 trkseg 独立从 0 累计，不跨分段合并。"""
    seg1 = make_seg([(31.000, 121.0, 0), (31.006, 121.0, 60)])
    seg2 = make_seg([(31.010, 121.0, 0), (31.016, 121.0, 60)])
    d1 = haversine_m(seg1[0], seg1[1])  # 约 666 m
    d2 = haversine_m(seg2[0], seg2[1])
    res = compute_splits([seg1, seg2], 0.5)

    assert [r.segment_index for r in res] == [1, 2]
    for r, d in zip(res, (d1, d2)):
        assert r.ok
        assert abs(r.total_distance_m - d) < 1e-9
        # 各 trkseg 都是 500 m 整段 + 约 166 m 尾段；若跨段合并则首段会是 500+166
        assert len(r.splits) == 2
        assert abs(r.splits[0].distance_m - 500.0) < 1e-6
        assert abs(r.splits[1].distance_m - (d - 500.0)) < 1e-6
        assert [sp.index for sp in r.splits] == [1, 2]  # 段号各自从 1 开始


def test_exact_multiple_has_no_tail():
    """总里程恰好是整段倍数时，不再单列尾段。"""
    a = TrackPoint(lat=31.0, lon=121.0, time=T0)
    b = TrackPoint(lat=31.01, lon=121.0, time=T0 + timedelta(seconds=100))
    d = haversine_m(a, b)
    res = compute_splits([[a, b]], d / 2000.0)  # 每段恰好为总里程的一半

    splits = res[0].splits
    assert len(splits) == 2
    for sp in splits:
        assert abs(sp.distance_m - d / 2.0) < 1e-6


def test_non_increasing_time_reported():
    """相邻时间不递增（含相等、倒退）：提示无法计算，不出分段。"""
    for offsets in ((0, 50, 50), (0, 60, 30)):
        seg = make_seg([(31.0, 121.0, offsets[0]), (31.001, 121.0, offsets[1]),
                        (31.002, 121.0, offsets[2])])
        res = compute_splits([seg], 0.1)
        assert not res[0].ok
        assert "不递增" in res[0].message
        assert res[0].splits == []
        assert res[0].total_distance_m > 0  # 距离统计不受时间影响


def test_missing_time_reported():
    seg = make_seg([(31.0, 121.0, 0), (31.001, 121.0, 60)])
    seg[1].time = None
    res = compute_splits([seg], 0.1)
    assert not res[0].ok
    assert "时间" in res[0].message


def test_zero_distance_no_infinite_pace():
    """原地不动的轨迹：零里程分段配速为 None，不产生无穷大。"""
    seg = make_seg([(31.0, 121.0, 0), (31.0, 121.0, 30)])
    res = compute_splits([seg], 1.0)

    assert res[0].ok
    assert res[0].total_distance_m == 0.0
    assert len(res[0].splits) == 1
    sp = res[0].splits[0]
    assert sp.distance_m == 0.0
    assert sp.pace_s_per_km is None  # 前端显示 "—" 而非无穷配速
    assert sp.duration_s == 30.0


def test_single_point_segment_has_no_splits():
    seg = make_seg([(31.0, 121.0, 0)])
    res = compute_splits([seg], 1.0)
    assert res[0].ok
    assert res[0].splits == []


def test_split_km_range_validated():
    seg = make_seg([(31.0, 121.0, 0), (31.001, 121.0, 60)])
    for bad in (0.05, 0.0, -1.0, 10.5, 100.0):
        try:
            compute_splits([seg], bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"split_km={bad} 应被拒绝")
    for good in (0.1, 1.0, 10.0):
        assert compute_splits([seg], good)[0].ok


def test_sample_gpx_end_to_end():
    """示例 GPX：两个 trkseg 分别累计，尾段不足 1 km 也列出。"""
    sample = Path(__file__).resolve().parents[1] / "examples" / "sample.gpx"
    segments, _ = parse_gpx(sample.read_bytes())
    res = compute_splits(segments, 1.0)  # 默认每段 1 公里

    assert len(res) == len(segments) == 2
    for r, seg in zip(res, segments):
        assert r.ok
        total = sum(haversine_m(a, b) for a, b in zip(seg, seg[1:]))
        assert abs(r.total_distance_m - total) < 1e-9
        full = int(total // 1000.0)
        expected_n = full + (1 if total - full * 1000.0 > 1e-6 else 0)
        assert len(r.splits) == expected_n
        # 各段距离之和等于该 trkseg 总里程，时间首尾相接且覆盖整个分段
        assert abs(sum(sp.distance_m for sp in r.splits) - total) < 1e-6
        assert r.splits[0].start_time == seg[0].time
        assert r.splits[-1].end_time == seg[-1].time
        for prev, nxt in zip(r.splits, r.splits[1:]):
            assert prev.end_time == nxt.start_time
        # 整段都是 1000 m，只有尾段不足 1000 m
        for sp in r.splits[:-1]:
            assert abs(sp.distance_m - 1000.0) < 1e-6
        assert 0.0 < r.splits[-1].distance_m <= 1000.0 + 1e-6


def _run_all():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
