# 皇室战争数据问答系统启动手册

本文只负责本机 API 和浏览器界面的首次启动、健康检查与安全重启。

- `docs/OPERATIONS.md` 负责运行边界、配置、安全、部署和故障判断。
- `docs/SNAPSHOT_COLLECTION_HANDOFF.md` 是采集计划任务、停止/恢复、状态检查、PushPlus 和发布恢复的唯一命令手册。
- `docs/TESTING.md` 负责测试分层、评测、生成报告和 live smoke 边界。
- `docs/DATA_CONTRACT.md` 与 `docs/RAG_AND_QA.md` 负责数据、接口、RAG 与 SSE 合同。

所有命令均在仓库根目录执行。公开质量门、API/Web 服务和采集任务是彼此独立的运行目标；不要因为启动 API 或浏览器而重启、停止或修改采集任务。

## 首次安装与公开质量门

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

公开门禁不需要真实模型 Key、Supercell token、私有快照或联网 provider。它只使用匿名 fixtures 和 mock；详细测试范围见 `docs/TESTING.md`。

## 本机 API 与浏览器

先启动只读 API：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_api.ps1
```

另开一个 PowerShell 启动浏览器 UI：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_web.ps1
```

默认地址：

| Surface | URL |
|---|---|
| Browser UI | `http://127.0.0.1:8080` |
| API health | `http://127.0.0.1:8091/health` |
| API readiness | `http://127.0.0.1:8091/ready` |
| Dataset catalog | `http://127.0.0.1:8091/api/datasets` |

API 启动不会联系 Supercell。首次载入本机数据和 RAG 索引时，`/health` 可能先可用，`/ready` 仍显示 `degraded` 或 `rag_status=building`；等待预热完成后再验收：

```powershell
Invoke-RestMethod http://127.0.0.1:8091/health
Invoke-RestMethod http://127.0.0.1:8091/ready
Invoke-RestMethod http://127.0.0.1:8091/api/datasets
```

验收时 `/ready.status` 应为 `ready`，并确认 `/api/datasets` 的已发布快照组与本机活动指针一致。范围、快照组和完整配置口径见 `docs/DATA_CONTRACT.md`。

## 本机配置边界

仅在使用可选模型 provider 时，从 `.env.example` 创建本机 `.env` 并设置真实凭证。真实 `OPENAI_API_KEY`、`SUPERCELL_API_TOKEN`、`SUPERCELL_API_TOKENS`、PushPlus token 和管理员密钥必须保存在当前进程、Windows 用户环境或部署密钥存储中，不能写入仓库、日志、终端截取或问题反馈。

API/Web 读取已发布的私有快照；采集凭证和 Supercell 白名单只由独立采集任务使用。环境变量分组、端口和生产部署见 `docs/OPERATIONS.md`；采集预检和 token 槽位见 `docs/SNAPSHOT_COLLECTION_HANDOFF.md`。

## 安全重启 API 或浏览器

优先在原终端按 `Ctrl+C`。若原终端丢失，先核对监听端口及其命令行，只结束本项目的 `runtime_multi.py` 或 `web_app.py` 进程。不要按名称批量结束 Python，也不要停止采集监督器或计划任务。

```powershell
$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -in 8080, 8091 }
$listeners | Select-Object LocalPort, OwningProcess
Get-CimInstance Win32_Process |
  Where-Object { $listeners.OwningProcess -contains $_.ProcessId } |
  Select-Object ProcessId, Name, CommandLine
```

确认命令行属于本项目后，停止相应 API/Web 进程，再依次运行 `run_api.ps1` 和 `run_web.ps1`。采集异常、发布恢复、磁盘空间、计划任务和告警必须按 `docs/SNAPSHOT_COLLECTION_HANDOFF.md` 操作。

## 数据与隐私

`data/` 下的原始对局、事实库、统计、RAG 文档、向量索引、状态与断点，以及日志、SQLite、JSONL、trace、benchmark 输出和导出文件都留在本机。提交前规则见 `CONTRIBUTING.md`；忽略规则不代替人工检查暂存清单。
