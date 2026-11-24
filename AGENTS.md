# LawTeam-OS 项目铁律

## 1. 领域与契约一致性
- **期限零 LLM 计算**：期限由 `deadline_engine.py`（`chinese-calendar` + `no_roll_custody`）计算，禁覆写 `is_manual_override=True`；改规则或案件属性重算时清理失效未办结旧期限。
- **前后端契约零漂移**：枚举（`manual`/`auto_archive`、`crime_type` 仅存规则枚举、5 个归档子目录名）、字段名（`file_name`/`size_bytes`/`doc_title_hint`/`hearing_location`）及请求体（`PATCH` 用 `exclude_unset=True` 保留置空能力）必须严格对齐，改一端必查另一端。
- **阶段与指定天数**：`authority_specified_days` 仅覆写指定期限类规则；阶段推进匹配 `procedure_subtype` 且单向不倒退。

## 2. 安全、私密与并发
- **`PRIVATE` 案件全链路隔离**：收件箱、台账、日历、企微回调中，**`ADMIN` 亦不可见/不可操作他人私密案**；私密案禁挂成员。
- **权限与凭据防线**：改 `visibility`/`is_archived`/`case_category` 与 `reopen` 删档须 `require_manage=True`；禁改删他人私人日程、禁自降权、禁覆写他人 `personal_custom_alerts`；非管理员隐藏同事推送密钥，`run-once` 限管理员；非开发环境禁建演示账号/一键登录，默认 JWT 密钥禁启动。
- **回调与 SSRF**：企微回调禁无 Token 放行与明文绕过 AES；`triage-action` 禁 `GET` 改库并防 `reopen` 重放；短信抓链限官方域名白名单并校验跳转每一跳禁落内网 IP，外发 Webhook 禁云元数据 IP。
- **并发与时区**：`verify_hmac_signature` 传 UTF-8 `bytes`；去重捕获 `IntegrityError`；`confirm` 加行锁且校验先于移文件；朴素 DB 时间按 UTC 转 `+08:00`，`datetime-local` 提交带 `+08:00`；区分送达日与开庭/落款日，禁盲抓短信首个日期。

## 3. 自动归档、推送与环境
- **默认自动归档闭环**：命中规则且无歧义自动归档（无匹配在办案且有明确案号时全局查重+加锁后自动建案，无案号刑拘短信转人工）；**企微卡片与日历一键确认必须共用完整入案逻辑**；`retry-fetch` 合并而非清空字段；多接收人按人记成败与重试上限，`.ics` 含 `DTSTAMP`+`VTIMEZONE`。
- **部署基线**：`backend/Dockerfile` 先跑 `alembic upgrade head`；`frontend/Dockerfile` 配 `/api` 反代与 SPA 回退；`start.sh` 与 `Dockerfile` 备齐 Chromium 与本地 OCR 依赖。

## 4. 前端 MD3 与文案红线
- **MD3 唯一标准**：`tailwind.config.js` 必须对齐 `--md-sys-*`（禁未定义变量退化成直角）；全站禁用原生方框 `checkbox`/`radio`/`color`/`file`/`datalist`/`window.confirm()` 及直角高亮按钮，统一用 MD3 组件与 `.lt-*` 样式。
- **极简非技术化文案**：严禁暴露 `rule_code`/`judgment_served`/`auto_archive`/`Tier-2`/幂等键/环境变量名/原始 JSON；严禁在每次简化或加功能后回潮新增重复说明框；改完前端必须启动服务实测核心页（含 `/cases/:id` 与弹窗），不得只跑 `npm run build`。

## 5. 适度防御与防漂移门禁
- **适度防御与本机环境**：遵循适度防御原则（不引入重型运维或重型 E2E 框架），Mac 本机前端实测与浏览器自动化一律使用 Ego Lite（`ego-browser`）。
- **防漂移门禁与阶段交付**：修改后端模型/路由或前端类型后必须执行 `cd frontend && npm run gen:api-types`，交付前跑通根目录 `./check.sh`（Ruff + Pyright + `lawteam_test` 隔离测试 + OpenAPI 契约零漂移 + 前端构建 + 红线扫描）；**每完成一个阶段并通过 `./check.sh` 后自动 `git commit` 并 `git push` 到远端**。
