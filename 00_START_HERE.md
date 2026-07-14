# 皇室战争 Agent 启动手册

## 项目做什么

这是面向战队赛统筹场景的 Skill-based Agent。它先把自然语言问题解析为意图和槽位，再路由到赛程、卡牌、卡组、对比、备战或 RAG Skill。

确定性问题优先从本地 JSON 数据回答；开放式环境分析和备战问题会先使用 RAG 检索，再由模型基于证据综合回答，避免把所有问题都交给模型猜测。

## 先测试

```powershell
cd "F:\All projects\agentscope-doc-qa-rescue-codex-crash"
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

## 启动后端

打开第一个 PowerShell：

```powershell
cd "F:\All projects\agentscope-doc-qa-rescue-codex-crash"
if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
    $env:OPENAI_API_KEY = Read-Host "请输入 OpenAI API Key"
}
if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
    throw "未检测到 OPENAI_API_KEY，后端不会调用模型。"
}
Write-Host "已检测到 OPENAI_API_KEY，长度：$($env:OPENAI_API_KEY.Length)"
powershell -ExecutionPolicy Bypass -File .\run_backend.ps1
```

后端地址：`http://127.0.0.1:8091`

健康检查：`http://127.0.0.1:8091/health`

在另一个 PowerShell 中验证后端：

```powershell
Invoke-RestMethod http://127.0.0.1:8091/health
```

## 启动前端可视化

打开第二个 PowerShell：

```powershell
cd "F:\All projects\agentscope-doc-qa-rescue-codex-crash"
powershell -ExecutionPolicy Bypass -File .\run_web.ps1
```

浏览器打开：`http://127.0.0.1:8080`

建议演示问题：

```text
我们第五轮打谁？
使用率第三的卡牌是什么？
现在热门卡组有哪些？
帮我根据下一轮对手做备战建议。
```

前端会用 SSE 逐步显示“解析、检索、模型生成、输出”状态；每次完成后会在“执行记录”中展示解析意图、实际 Skill、计划、检索模式、候选文档和耗时。面试讲解链路：`Query Parser -> Router -> Local JSON 或 RAG -> Model Synthesis -> Trace -> SSE UI`。

## 模型何时调用

赛程、固定排名、单卡胜率等高置信度问题会直接读取本地 JSON，不会消耗模型调用。开放式环境分析和备战问题强制进入 RAG，再调用 OpenAI 模型综合证据；Ollama embedding 不可用时会在 3 秒后自动降级为 BM25。解析器和模型调用分别有 20 秒、120 秒上限，超时会返回明确错误。模型 Key 只从当前进程的 `OPENAI_API_KEY` 环境变量读取。

## 失败先查

```text
1. 后端是否已经先于前端启动。
2. 8091 和 8080 是否被占用。
3. 终端是否提示缺少依赖。
4. 本地 JSON 查询不应依赖真实 LLM Key。
5. Trace 中的 `hybrid` 表示向量和 BM25 都可用，`bm25_only` 表示 Ollama 不可用但已自动降级。
6. 检查 `OPENAI_MODEL`、网络和模型调用超时配置，开放问题不会无限等待。
```

## 模型连接检查

开放式问题会调用模型。`OPENAI_API_KEY`、`OPENAI_MODEL` 与 `OPENAI_BASE_URL` 必须来自同一个 OpenAI 兼容服务商；没有配置 `OPENAI_BASE_URL` 时，程序会使用官方 OpenAI 地址。

启动前可以只检查变量是否存在，不要在终端打印 Key：

```powershell
"OPENAI_API_KEY 已配置：$(-not [string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY))"
"OPENAI_MODEL 已配置：$(-not [string]::IsNullOrWhiteSpace($env:OPENAI_MODEL))"
"OPENAI_BASE_URL 已配置：$(-not [string]::IsNullOrWhiteSpace($env:OPENAI_BASE_URL))"
```

正式介绍见：`README.md`

## 本项目模型配置

本项目使用 Codex 已配置的 OpenAI 兼容中转站：`https://crs.ruinique.com`。启动脚本会设置 `OPENAI_WIRE_API=responses`、`OPENAI_MODEL=gpt-5.5`，并让解析与 RAG 最终综合统一使用 `medium` 推理强度，以控制响应时间。真实凭证仍然只读取 `OPENAI_API_KEY` 环境变量，不写入项目文件。
