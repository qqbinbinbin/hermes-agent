# 原生异步任务的持久化身份

`POST /v1/runs` 原有的立即返回与后台执行能力保持不变。新增可选
`Idempotency-Key`，用于已获授权的同一次任务提交，不是自动重试授权。

- 同一 profile、同一 key、同一请求体及会话作用域只领取一次 run。
- 同 key 更改载荷返回 409；持久化失败返回 503，不退回内存执行。
- 任务身份及状态保存在该 profile 的 `api_runs.db`，不保存提交正文，
  只保存请求摘要及已有状态结构；文件权限为 owner-only。
- `GET /v1/runs/{id}` 可回读持久化终态。进程重建后遇到非终态记录，
  返回 `unknown`、`persisted_status`、`reconciliation_required=true`。
  该记录不能证明原执行者仍在运行，也不能授权重新调用模型。
- 再次提交已登记 key 只返回同一 run，不接管、重放或重置费用。
- 未传 key 的原生请求保持原行为；持久化身份不替代调用方鉴权。

目前未实现跨进程自动恢复、持久化租约或供应商请求级去重。平台接线必须先完成
执行者存活核对、原会话恢复与累计预算验证，不能把本项当作完整的断电恢复方案。
FUXI 不得利用该接口替 Hermes 规划 Wiki 主题；Skill 继续负责语义整理。

隔离验证：`python3 -m unittest tests.gateway.test_api_run_store -v`。
另以实际 API adapter 加合成 agent 验证 202、独立运行、重复提交、冲突、
存储失效以及新 adapter 查询。未调用供应商，未更改生产 profile 或发布代码。

## 原生会话续接

受认证的runs请求可携带`X-Hermes-Session-Id`，使用与chat入口相同的会话数据库及compression tip恢复真实历史。不由FUXI拼装旧消息，不创建第二套历史存储。请求体session_id若与头不符，或同时指定conversation_history/previous_response_id，返回400；历史数据库不可用返回503，不静默开启无历史的新回合。

能力发现显式返回`run_persistent_idempotency`与`run_session_continuity`。会话续接头参与提交身份摘要。续接不是自动重试授权，也不会使未知运行态自动重启。

FUXI仓库`scripts/runtime/probe-hermes-native-resume.py`使用真实临时会话数据库和合成agent验证历史、compression tip和拒绝路径。候选模块仅在隔离进程加载，未覆盖容器文件，供应商请求为0。
