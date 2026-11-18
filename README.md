# Meridian 区域媒体证据分析服务

本服务供研究团队对本地提供的媒体观察记录进行地区、议题和受众比较。系统不抓取网页、不自动推断报道倾向，也不声称合成演示能代表真实群体。

来源登记绑定地区、媒体和允许词表；观察记录按来源和外部编号保留修订。两名研究员独立编码，分歧由第三人裁定。按地区、故事、议题和受众去重，报告显示分母、排除原因和原始证据数。

地区对比同时给出两种口径：原有故事口径（每个故事计一次，转载不重复）与来源均衡口径（各来源等权平均其自身倾向分布）。两者共用同一份证据，但分母不同：故事口径以故事数为分母，来源均衡口径以有有效故事的来源数为分母。对比结果还列出各来源贡献、多来源故事与转载观察数、未解决编码分歧，以及每个来源的采集窗口状态（`complete`、`partial`、`open`、`drifted`、`unframed`）。某地区证据不足时状态为 `insufficient` 或 `no_evidence`，缺失不会被当作零倾向。

使用 `python -m meridian.api --db meridian.sqlite3` 启动 FastAPI。主要接口包括 `POST /sources`、`POST /observations`、`POST /decisions`、`POST /adjudications`、`POST /batches`、`POST /reports` 和 `POST /releases`。采集窗口使用 `/frames`，导出使用 `/reports/export` 或 `/releases/{release_id}/export`。地区对比使用 `POST /reports/compare-regions`（可传 `concentration_threshold`），来源均衡视图另见 `POST /reports/source-balance`。冻结发布仍保留发布时的原始报告；`GET /releases/{release_id}/compare` 会额外给出当前来源构成与来源均衡视角，不修改已发布数据。

运行 `python -m unittest discover -s tests -v`、`python -m compileall -q meridian` 和 `python -m meridian.demo`。数据库使用 SQLite；测试和演示使用临时本地数据库。
