# 皇室战争 Agent 启动手册

## 项目做什么

这是面向战队赛统筹场景的 Skill-based Agent。它先把自然语言问题解析为意图和槽位，再路由到赛程、卡牌、卡组、对比、备战或 RAG Skill。

确定性问题优先从本地 JSON 数据回答；开放式备战问题再使用混合检索和生成，避免把所有问题都交给模型猜测。

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

前端展示的是聊天结果；面试讲解时继续说明后端链路：`Query Parser -> Router -> Skill -> 数据查询或 RAG -> Answer -> Trace`。

## 模型何时调用

赛程、固定排名、单卡胜率等高置信度问题会直接读取本地 JSON，不会消耗模型调用。开放式环境分析会在 Ollama embedding 可用时进入 RAG；本地解析无法确定意图时会调用 OpenAI 模型做兜底解析。模型 Key 只从当前进程的 `OPENAI_API_KEY` 环境变量读取。

## 失败先查

```text
1. 后端是否已经先于前端启动。
2. 8091 和 8080 是否被占用。
3. 终端是否提示缺少依赖。
4. 本地 JSON 查询不应依赖真实 LLM Key。
5. RAG 问题可能依赖 Ollama、embedding 模型或对应配置。
```

正式介绍见：`README.md`
