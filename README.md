# 徒步轨迹分享 · 敏感地点遮蔽

上传含时间的 GPX 轨迹，在**无底图**预览图上点击敏感地点（如家、公司）并设定遮蔽半径，
系统按**轨迹线段与遮蔽圆的交点**精确裁切，导出为**互不连接的 GPX `trkseg`** 后再分享。
上传后还会自动给出**分段配速统计**（默认每 1 公里一段，可调 0.1～10 公里）。

- 后端：Python / FastAPI（几何裁切与配速统计在服务端完成）
- 前端：原生 JavaScript + Canvas（无任何框架、无地图底图依赖）

## 一条命令启动

```bash
docker compose up --build
```

依赖安装与初始化全部在镜像构建和容器启动时自动完成，无需任何额外步骤。

## 访问地址

- 网页：**http://localhost:8000**（端口 8000）

## 配置

| 配置项 | 位置 | 说明 |
| --- | --- | --- |
| 对外端口 | `docker-compose.yml` 中 `ports` | 默认 `"8000:8000"`，改左侧即可（如 `"8080:8000"`） |
| 上传大小限制 | `app/main.py` 中 `MAX_UPLOAD_BYTES` | 默认 50 MB |
| 数据存储 | 内存 | 演示用途：轨迹仅存内存，容器重启即清空，无需数据库 |

无其他必需配置（不需要 API Key、不依赖外部服务）。

## 使用流程

1. **上传 GPX**：选择含 `<time>` 的 GPX 文件（缺少时间会被拒绝并提示）。可用
   `examples/sample.gpx` 快速体验。
2. **设置遮蔽**：在右侧预览图上**点击**敏感地点即添加遮蔽圆，半径在左侧面板设定
   （添加前设定新圆半径；已有圆可在列表中单独修改或删除）。
3. **查看结果**：页面实时显示 **隐藏段数**、**裁切前距离**、**裁切后距离** 与可见分段数；
   蓝色为可见轨迹，红色虚线为被隐藏段。
4. **分段配速**：上传后自动按每 1 公里列出每个原始 `trkseg` 的分段表（距离、耗时、
   分/公里配速、起止时间）；在"每段里程"输入框可改为 0.1～10 公里，修改后自动重算。
5. **下载**：点击"下载遮蔽后的 GPX"，得到一条 `trk` 下多个互不连接的 `trkseg`。

## 分段配速统计（实现要点）

- **按原始 trkseg 分别累计**：每个原始 `trkseg` 独立从 0 累计 haversine 球面距离，
  互不合并；结果按"原始分段 #1、#2……"分组展示。
- **边界时间插值**：采样线段跨越一个或多个里程边界时，按距离比例在线段两端时间之间
  线性插值边界时刻（与遮蔽裁切使用同一套插值逻辑）。
- **尾段**：末尾不足一整段的部分也单列一段；总里程恰好是整段倍数时不再多列。
- **异常提示**：某 `trkseg` 内相邻轨迹点时间不递增（相等或倒退）时，该分段提示
  "无法计算配速"，不影响其他分段；零里程分段的配速显示 "—"，不会出现无穷大。
- **参数**：每段里程 `split_km`，范围 0.1～10 公里，默认 1 公里；
  网页上修改输入框即自动重算，API 见下文 `/api/splits`。

## 裁切规则（实现要点）

- **穿越识别**：对每个原始采样线段与每个遮蔽圆求解析交点（局部切平面投影中解二次方程），
  即使相邻两个采样点都在圆外、但线段穿越圆，也会被识别并裁开。
- **时间插值**：交点处的时间（及海拔）按交点在线段上的比例 `t` 线性插值，
  导出时精确到毫秒。
- **分段边界**：可见轨迹不跨原始 `trkseg` 边界合并；每段连续可见部分导出为一个独立
  `trkseg`，隐藏部分不出现在导出文件中。
- **距离统计**：裁切前/后距离均用 haversine 大地线距离累计，裁切后距离包含插值边界点。
- **精度说明**：遮蔽判定在圆心切平面投影中进行，常用半径（数十米至数公里）下与大地线
  的偏差为亚厘米级；导出的边界点经纬度保留 7 位小数（约 1 cm）。

## 项目结构

```
app/
  main.py          # FastAPI 路由：/api/upload、/api/mask、/api/export、/api/splits、静态页面
  geo.py           # 几何核心：haversine、线段-圆求交、按可见性裁切
  gpxutil.py       # GPX 1.1 解析（强制要求 time）与多 trkseg 导出
  splits.py        # 分段配速：按固定里程切分 trkseg、插值边界时间
  static/          # 原生 JS 前端（index.html / app.js / style.css）
tests/test_geo.py    # 裁切核心逻辑单元测试（9 项）
tests/test_splits.py # 分段配速单元测试（9 项）
examples/sample.gpx# 示例轨迹（两个 trkseg，含时间）
Dockerfile         # python:3.12-slim，构建期安装依赖，启动即运行
docker-compose.yml # 单服务，映射 8000 端口
requirements.txt
```

## 本地开发（不用 Docker）

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 运行单元测试（核心逻辑不依赖 FastAPI）
python3 tests/test_geo.py        # 或 python3 -m pytest tests/
python3 tests/test_splits.py
```

## API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/upload` | 表单上传 GPX，返回 `track_id`、轨迹点与统计 |
| `POST` | `/api/mask` | `{track_id, circles:[{lat,lon,radius_m}]}` → 可见/隐藏分段、隐藏段数、裁切前后距离 |
| `POST` | `/api/export` | 同上参数，返回多 `trkseg` 的 GPX 文件下载 |
| `POST` | `/api/splits` | `{track_id, split_km}` → 按原始 `trkseg` 分组的分段配速 |

`/api/splits` 参数与返回：

- `split_km`：每段里程（公里），**0.1～10，默认 1**；越界返回 422。
- 返回 `segments[]`：每项含 `segment_index`（原始 `trkseg` 序号）、`ok`、`message`
  （时间不递增等原因时为提示文案）、`total_distance_m` 与 `splits[]`。
- `splits[]` 每项：`index`、`distance_m`、`duration_s`、`pace_s_per_km`
  （零里程时为 `null`）、`start_time` / `end_time`（ISO 8601，边界处为插值时刻）。

示例：

```bash
curl -X POST http://localhost:8000/api/splits \
  -H "Content-Type: application/json" \
  -d '{"track_id": "<上传返回的 track_id>", "split_km": 1}'
```
