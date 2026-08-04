"""Self-contained browser UI for structured statistics and free-form RAG QA."""


HTML_PAGE = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>皇室战争数据分析</title>
  <style>
    :root {
      --ink: #172033;
      --muted: #667085;
      --line: #d9dee8;
      --soft: #f3f5f8;
      --paper: #ffffff;
      --accent: #1667c5;
      --accent-soft: #e8f1fb;
      --good: #137333;
      --good-soft: #e8f5ec;
      --warn: #9a6700;
      --warn-soft: #fff5d6;
      --bad: #b42318;
      --bad-soft: #feeceb;
    }
    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
    html { background: #eef1f5; }
    body {
      margin: 0;
      color: var(--ink);
      background: #eef1f5;
      font-family: Arial, "Microsoft YaHei", sans-serif;
      letter-spacing: 0;
    }
    button, input, textarea, select { font: inherit; letter-spacing: 0; }
    button { cursor: pointer; }
    .topbar {
      height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      padding: 0 24px;
      color: #fff;
      background: #182235;
      border-bottom: 3px solid #e4b646;
    }
    .brand { min-width: 0; }
    .brand-name { font-size: 20px; font-weight: 700; white-space: nowrap; }
    .brand-meta { margin-top: 3px; color: #c7d0df; font-size: 12px; overflow-wrap: anywhere; }
    .top-status { display: flex; align-items: center; gap: 8px; font-size: 12px; white-space: nowrap; }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #f2c94c; }
    .status-dot.ready { background: #53c478; }
    .app-shell {
      width: min(1440px, 100%);
      min-height: calc(100vh - 64px);
      margin: 0 auto;
      display: grid;
      grid-template-columns: 216px minmax(0, 1fr);
      background: var(--paper);
      border-left: 1px solid var(--line);
      border-right: 1px solid var(--line);
    }
    .sidebar {
      padding: 18px 12px;
      background: #f8f9fb;
      border-right: 1px solid var(--line);
    }
    .nav-group { display: grid; gap: 4px; }
    .nav-button {
      width: 100%;
      min-height: 42px;
      padding: 9px 12px;
      border: 0;
      border-radius: 6px;
      color: #384152;
      background: transparent;
      text-align: left;
      font-size: 14px;
      font-weight: 600;
    }
    .nav-button:hover { background: #eceff3; }
    .nav-button.active { color: #0b4f9c; background: var(--accent-soft); }
    .main { min-width: 0; padding: 24px clamp(16px, 3vw, 36px) 48px; }
    .scope-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 22px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--line);
    }
    .scope-controls { display: flex; flex-wrap: wrap; gap: 14px; }
    .scope-control { display: grid; gap: 6px; }
    .loadout-details { display: grid; gap: 10px; margin-top: 14px; padding: 12px; border: 1px solid var(--line); border-radius: 6px; background: #f8f9fb; }
    .loadout-heading { color: #344054; font-size: 12px; font-weight: 700; }
    .loadout-tower, .loadout-card-row { display: grid; grid-template-columns: minmax(0, 1fr) 150px; align-items: center; gap: 10px; }
    .loadout-card-label { min-width: 0; color: #445064; font-size: 12px; overflow-wrap: anywhere; }
    .loadout-select { min-height: 34px; width: 100%; padding: 6px 8px; border: 1px solid #bcc5d2; border-radius: 5px; color: var(--ink); background: #fff; }
    .loadout-help { color: var(--muted); font-size: 11px; line-height: 1.45; }
    .scope-label { color: var(--muted); font-size: 11px; font-weight: 700; }
    .segmented { display: inline-flex; border: 1px solid #b8c1ce; border-radius: 6px; overflow: hidden; }
    .segment {
      min-height: 34px;
      padding: 6px 10px;
      border: 0;
      border-right: 1px solid #b8c1ce;
      color: #344054;
      background: #fff;
      font-size: 12px;
      font-weight: 700;
    }
    .segment:last-child { border-right: 0; }
    .segment.active { color: #fff; background: var(--accent); }
    .segment:disabled { color: #98a2b3; background: #f2f4f7; cursor: default; }
    .scope-summary { max-width: 420px; color: var(--muted); font-size: 12px; line-height: 1.5; text-align: right; }
    .view { min-width: 0; }
    .view-heading {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 20px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line);
    }
    h1, h2, h3, p { margin-top: 0; }
    h1 { margin-bottom: 0; font-size: 22px; }
    h2 { margin-bottom: 12px; font-size: 16px; }
    h3 { margin-bottom: 8px; font-size: 14px; }
    .snapshot-id { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; text-align: right; }
    .status-grid, .dashboard-grid, .metric-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }
    .status-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 24px; }
    .metric, .viz-card {
      min-width: 0;
      padding: 14px;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 6px;
    }
    .metric-label { color: var(--muted); font-size: 12px; }
    .metric-value { margin-top: 6px; font-size: 20px; font-weight: 700; overflow-wrap: anywhere; }
    .metric-detail { margin-top: 5px; color: var(--muted); font-size: 12px; line-height: 1.45; }
    .viz-card-title { display: flex; justify-content: space-between; gap: 8px; font-size: 14px; font-weight: 700; }
    .viz-body { margin-top: 12px; display: grid; gap: 7px; color: #445064; font-size: 12px; line-height: 1.5; }
    .status-pill, .warning {
      display: inline-flex;
      align-items: center;
      width: fit-content;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 11px;
      font-weight: 600;
    }
    .status-pill { color: var(--good); background: var(--good-soft); }
    .warning { color: var(--warn); background: var(--warn-soft); }
    .query-layout { display: grid; grid-template-columns: minmax(320px, 0.9fr) minmax(360px, 1.1fr); gap: 24px; }
    .query-form { min-width: 0; }
    .result-panel { min-width: 0; border-left: 1px solid var(--line); padding-left: 24px; }
    .result-empty { color: var(--muted); font-size: 13px; padding: 24px 0; }
    .form-row { display: grid; gap: 8px; margin-bottom: 16px; }
    .field-label { color: #3e4859; font-size: 13px; font-weight: 700; }
    .search-input, textarea {
      width: 100%;
      border: 1px solid #bcc5d2;
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      outline: none;
    }
    .search-input { min-height: 40px; padding: 8px 10px; }
    .search-input:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(22,103,197,0.12); }
    .card-picker { min-width: 0; }
    .picker-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
    .picker-count { color: var(--muted); font-size: 12px; white-space: nowrap; }
    .selected-slots {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 6px;
      margin: 10px 0;
    }
    .selected-slot {
      min-width: 0;
      min-height: 38px;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 5px;
      border: 1px dashed #b8c2d0;
      border-radius: 5px;
      color: #7b8494;
      background: #fafbfc;
      font-size: 12px;
      text-align: center;
      overflow-wrap: anywhere;
    }
    .selected-slot.filled { border-style: solid; color: #0b4f9c; background: var(--accent-soft); }
    .card-grid {
      height: 260px;
      overflow-y: auto;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      align-content: start;
      gap: 6px;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--soft);
    }
    .card-tile {
      min-width: 0;
      min-height: 44px;
      padding: 6px 7px;
      border: 1px solid #cbd2dc;
      border-radius: 5px;
      color: #273244;
      background: #fff;
      font-size: 12px;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }
    .card-tile:hover { border-color: #6d9bd0; }
    .card-tile.selected { color: #0b4f9c; border-color: var(--accent); background: var(--accent-soft); }
    .command-row { display: flex; align-items: center; gap: 8px; margin-top: 14px; }
    .primary, .secondary, .feedback-button {
      min-height: 40px;
      border-radius: 6px;
      padding: 9px 14px;
      font-weight: 700;
    }
    .primary { border: 1px solid var(--accent); color: #fff; background: var(--accent); }
    .primary:hover { background: #0f58ab; }
    .primary:disabled { opacity: 0.55; cursor: default; }
    .secondary, .feedback-button { border: 1px solid #b8c1ce; color: #344054; background: #fff; }
    .form-error { min-height: 18px; margin-top: 8px; color: var(--bad); font-size: 12px; }
    .data-table-wrap { width: 100%; overflow-x: auto; border: 1px solid var(--line); border-radius: 6px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #e4e8ee; text-align: left; white-space: nowrap; }
    th { color: #4e596a; background: #f5f7fa; font-size: 12px; }
    tr:last-child td { border-bottom: 0; }
    .ranking-toolbar { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
    .ranking-actions { display: flex; align-items: flex-end; flex-wrap: wrap; gap: 14px; }
    .ranking-search { width: min(280px, 100%); }
    .ranking-summary { color: var(--muted); font-size: 12px; white-space: nowrap; }
    .ranking-table { min-width: 790px; }
    .ranking-table th { position: sticky; top: 0; z-index: 1; }
    .ranking-table .sorted-column { color: #0b4f9c; background: var(--accent-soft); }
    .ranking-rank { width: 68px; color: var(--muted); font-variant-numeric: tabular-nums; }
    .ranking-card-button { border: 0; padding: 0; color: #0b4f9c; background: transparent; font-weight: 700; }
    .ranking-card-button:hover { text-decoration: underline; }
    .low-sample-text { margin-left: 7px; color: var(--warn); font-size: 11px; }
    .provenance { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
    .chat-box {
      min-height: 360px;
      max-height: 56vh;
      overflow-y: auto;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #f8f9fb;
    }
    .msg { margin-bottom: 14px; display: flex; flex-direction: column; }
    .msg.user { align-items: flex-end; }
    .msg.agent { align-items: flex-start; }
    .meta { margin-bottom: 4px; color: var(--muted); font-size: 11px; }
    .bubble { max-width: 84%; padding: 10px 12px; border-radius: 6px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 14px; }
    .user .bubble { color: #fff; background: #175fae; }
    .agent .bubble { color: var(--ink); background: #fff; border: 1px solid var(--line); }
    .composer { display: grid; grid-template-columns: minmax(0, 1fr) 108px; gap: 10px; margin-top: 12px; }
    textarea { min-height: 96px; resize: vertical; padding: 11px; line-height: 1.5; }
    .composer-actions { display: grid; align-content: start; gap: 8px; }
    .qa-status { min-height: 20px; margin-top: 8px; color: var(--muted); font-size: 12px; }
    .trace-panel { margin-top: 16px; border-top: 1px solid var(--line); padding-top: 12px; }
    .trace-heading { display: flex; justify-content: space-between; gap: 12px; cursor: pointer; font-size: 13px; font-weight: 700; }
    .trace-summary { color: var(--accent); font-weight: 400; }
    .trace-list { display: grid; gap: 6px; margin-top: 10px; color: #4d596c; font: 12px/1.5 Consolas, monospace; }
    .trace-line { padding-left: 8px; border-left: 3px solid #7da9d8; overflow-wrap: anywhere; }
    .debug-trace { color: var(--muted); font-size: 12px; }
    .debug-trace pre { white-space: pre-wrap; overflow-wrap: anywhere; }
    .feedback-actions { display: flex; gap: 6px; margin-top: 5px; }
    .feedback-button { min-height: 30px; padding: 5px 9px; font-size: 11px; }
    .meta-analysis { margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--line); }
    .meta-analysis-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .analysis-output { margin-top: 12px; padding: 14px; border: 1px solid var(--line); border-radius: 6px; background: #f8f9fb; font-size: 14px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
    @media (max-width: 1000px) {
      .query-layout { grid-template-columns: 1fr; }
      .result-panel { border-left: 0; border-top: 1px solid var(--line); padding: 20px 0 0; }
      .status-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .dashboard-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 760px) {
      .topbar { height: auto; min-height: 64px; padding: 12px 14px; align-items: flex-start; }
      .brand-name { font-size: 17px; }
      .brand-meta { max-width: 230px; }
      .app-shell { display: block; min-height: 0; border: 0; }
      .sidebar { position: sticky; top: 0; z-index: 5; overflow-x: auto; padding: 8px; border-right: 0; border-bottom: 1px solid var(--line); }
      .nav-group { display: flex; width: max-content; }
      .nav-button { width: auto; min-height: 38px; padding: 8px 11px; white-space: nowrap; }
      .main { padding: 18px 12px 36px; }
      .scope-toolbar { display: grid; }
      .scope-summary { max-width: none; text-align: left; }
      .ranking-toolbar { display: grid; }
      .ranking-summary { white-space: normal; }
      .view-heading { align-items: flex-start; }
      .snapshot-id { max-width: 170px; }
      .status-grid, .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); height: 300px; }
      .selected-slots { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .loadout-tower, .loadout-card-row { grid-template-columns: 1fr; gap: 5px; }
      .composer { grid-template-columns: 1fr; }
      .composer-actions { grid-template-columns: 1fr 1fr; }
      .bubble { max-width: 94%; }
    }
    @media (max-width: 420px) {
      .status-grid, .metric-grid { grid-template-columns: 1fr; }
      .top-status span:last-child { display: none; }
    }
  </style>
</head>
<body>
  <label id="sampleControl" class="sample-control" for="sampleTarget" hidden>固定生产样本量</label>
  <select id="sampleTarget" hidden disabled><option value="200000" selected>200000</option></select>
  <header class="topbar">
    <div class="brand">
      <div class="brand-name">皇室战争数据分析</div>
      <div id="headerSnapshot" class="brand-meta">正在读取官方快照</div>
    </div>
    <div class="top-status"><span id="headerDot" class="status-dot"></span><span id="headerState">连接中</span></div>
  </header>
  <div class="app-shell">
    <aside class="sidebar" aria-label="功能导航">
      <nav class="nav-group">
        <button class="nav-button active" data-view="home">数据总览</button>
        <button class="nav-button" data-view="qa">自由问答</button>
        <button class="nav-button" data-view="rankings">全卡排名</button>
        <button class="nav-button" data-view="card">单卡查询</button>
        <button class="nav-button" data-view="compare">双卡比较</button>
        <button class="nav-button" data-view="deck">卡组画像</button>
        <button class="nav-button" data-view="matchup">卡组对阵</button>
        <button class="nav-button" data-view="meta">环境体系</button>
      </nav>
    </aside>
    <main class="main">
      <section class="scope-toolbar" aria-label="数据范围">
        <div class="scope-controls">
          <div class="scope-control">
            <div class="scope-label">时间窗口</div>
            <div id="windowSegments" class="segmented">
              <button class="segment active" type="button" data-window="7">最近7天</button>
              <button class="segment" type="button" data-window="d7_14">7-14天前</button>
              <button class="segment" type="button" data-window="d14_21">14-21天前</button>
              <button class="segment" type="button" data-window="d21_28">21-28天前</button>
              <button class="segment" type="button" data-window="d28_35">28-35天前</button>
              <button class="segment" type="button" data-window="35">最近35天</button>
            </div>
          </div>
          <div class="scope-control">
            <div class="scope-label">数据层级</div>
            <div id="rankSegments" class="segmented">
              <button class="segment" type="button" data-level="top_100">前100</button>
              <button class="segment" type="button" data-level="top_200">前200</button>
              <button class="segment" type="button" data-level="top_500">前500</button>
              <button class="segment" type="button" data-level="top_1000">前1000</button>
              <button class="segment active" type="button" data-level="all">全量</button>
            </div>
          </div>
          <div class="scope-control">
            <div class="scope-label">数据口径</div>
            <div id="dataModeSegments" class="segmented" aria-label="数据口径">
              <button class="segment active" type="button" data-mode="base8">普通 8 卡</button>
              <button class="segment" type="button" data-mode="loadout_entity">完整配置</button>
            </div>
          </div>
        </div>
        <div id="scopeSummary" class="scope-summary">正在读取数据范围</div>
      </section>
      <section id="view-home" class="view" data-page="home">
        <div class="view-heading"><h1>数据总览</h1><div id="homeSnapshotId" class="snapshot-id">未就绪</div></div>
        <div id="snapshotGrid" class="status-grid"></div>
        <div class="dashboard-grid">
          <article id="dataLineageViz" class="viz-card" aria-label="数据血缘与快照对齐"><div class="viz-card-title"><span>数据血缘</span><span class="status-pill">等待</span></div><div class="viz-body"></div></article>
          <article id="qualityGateViz" class="viz-card" aria-label="RAG 质量门槛"><div class="viz-card-title"><span>RAG 质量</span><span class="status-pill">等待</span></div><div class="viz-body"></div></article>
          <article id="opsViz" class="viz-card" aria-label="运行与模型状态"><div class="viz-card-title"><span>运行状态</span><span class="status-pill">等待</span></div><div class="viz-body"></div></article>
        </div>
      </section>

      <section id="view-qa" class="view" data-page="qa" hidden>
        <div class="view-heading"><h1>自由问答</h1><div class="snapshot-id">自然语言解析 · 多意图 · 高级 RAG</div></div>
        <div id="chatBox" class="chat-box" aria-live="polite"></div>
        <div class="composer">
          <textarea id="inputBox" aria-label="自由问答输入" placeholder="输入数据分析问题"></textarea>
          <div class="composer-actions"><button id="sendBtn" class="primary">发送</button><button id="clearBtn" class="secondary" type="button">清空</button></div>
        </div>
        <div id="status" class="qa-status"></div>
        <section class="trace-panel" aria-live="polite">
          <details id="executionPanel" open>
            <summary class="trace-heading"><span>执行记录</span><span id="traceSummary" class="trace-summary">等待请求</span></summary>
            <div id="traceList" class="trace-list"></div>
          </details>
          <details class="debug-trace"><summary>调试详情</summary><pre id="debugTrace"></pre></details>
        </section>
      </section>

      <section id="view-card" class="view" data-page="card" hidden>
        <div class="view-heading"><h1>单卡查询</h1><div class="snapshot-id">使用率 · 胜率 · 净胜率 · 评分</div></div>
        <div class="query-layout"><div class="query-form"><div class="card-picker" data-picker="single-card"></div><div class="command-row"><button id="cardSubmit" class="primary">查询</button></div><div id="cardError" class="form-error"></div></div><div id="cardResult" class="result-panel"><div class="result-empty">尚未查询</div></div></div>
      </section>

      <section id="view-rankings" class="view" data-page="rankings" hidden>
        <div class="view-heading"><h1>全卡排名</h1><div class="snapshot-id">当前数据范围内全部卡牌</div></div>
        <div class="ranking-toolbar">
          <div class="ranking-actions">
            <div class="scope-control">
              <div class="scope-label">排序指标</div>
              <div class="segmented" aria-label="全卡排名指标">
                <button class="segment active" type="button" data-ranking-metric="usage_rate">使用率</button>
                <button class="segment" type="button" data-ranking-metric="clean_win_rate">胜率</button>
                <button class="segment" type="button" data-ranking-metric="rating" title="评分综合 Wilson 胜率下界、使用率百分位和样本置信度">评分</button>
              </div>
            </div>
            <div class="scope-control ranking-search">
              <label class="scope-label" for="rankingSearch">筛选卡牌</label>
              <input id="rankingSearch" class="search-input" type="search" placeholder="搜索中文卡名" />
            </div>
          </div>
          <div id="rankingSummary" class="ranking-summary">正在读取全卡数据</div>
        </div>
        <div id="rankingResult"><div class="result-empty">正在读取</div></div>
      </section>

      <section id="view-compare" class="view" data-page="compare" hidden>
        <div class="view-heading"><h1>双卡比较</h1><div class="snapshot-id">同一快照直接比较</div></div>
        <div class="query-layout"><div class="query-form"><div class="card-picker" data-picker="compare-cards"></div><div class="command-row"><button id="compareSubmit" class="primary">比较</button></div><div id="compareError" class="form-error"></div></div><div id="compareResult" class="result-panel"><div class="result-empty">尚未比较</div></div></div>
      </section>

      <section id="view-deck" class="view" data-page="deck" hidden>
        <div class="view-heading"><h1>卡组画像</h1><div class="snapshot-id">严格 8 张普通卡</div></div>
        <div class="query-layout"><div class="query-form"><div class="card-picker" data-picker="deck-profile"></div><div id="deckLoadoutDetails" class="loadout-details full-loadout-only" hidden></div><div class="command-row"><button id="deckSubmit" class="primary">查询卡组</button></div><div id="deckError" class="form-error"></div></div><div id="deckResult" class="result-panel"><div class="result-empty">尚未查询</div></div></div>
      </section>

      <section id="view-matchup" class="view" data-page="matchup" hidden>
        <div class="view-heading"><h1>卡组对阵</h1><div class="snapshot-id">精确八卡对八卡</div></div>
        <div class="query-layout"><div class="query-form"><h2>卡组 A</h2><div class="card-picker" data-picker="matchup-a"></div><div id="matchupALoadoutDetails" class="loadout-details full-loadout-only" hidden></div><h2 style="margin-top:20px">卡组 B</h2><div class="card-picker" data-picker="matchup-b"></div><div id="matchupBLoadoutDetails" class="loadout-details full-loadout-only" hidden></div><div class="command-row"><button id="matchupSubmit" class="primary">查询对阵</button></div><div id="matchupError" class="form-error"></div></div><div id="matchupResult" class="result-panel"><div class="result-empty">尚未查询</div></div></div>
      </section>

      <section id="view-meta" class="view" data-page="meta" hidden>
        <div class="view-heading"><h1>环境体系</h1><button id="metaRefresh" class="secondary">刷新</button></div>
        <div id="metaResult"><div class="result-empty">正在读取</div></div>
        <section class="meta-analysis">
          <div class="meta-analysis-head"><h2>环境证据分析</h2><button id="metaAnalyze" class="primary">生成分析</button></div>
          <div id="metaAnalysisStatus" class="qa-status"></div>
          <div id="metaAnalysisResult"><div class="result-empty">尚未分析</div></div>
        </section>
      </section>
    </main>
  </div>

  <script>
    const state = {
      catalog: [],
      entityCatalog: [],
      pickers: new Map(),
      snapshot: null,
      datasets: new Map(),
      datasetCatalog: null,
      windowDays: "7",
      dataLevel: "all",
      datasetScope: "7d_all",
      deckMode: "base8",
      entityMode: "base8",
      loadoutCatalog: null,
      loadoutCatalogScope: null,
      loadouts: {
        "deck-profile": { towerId: "", specialByCard: {} },
        "matchup-a": { towerId: "", specialByCard: {} },
        "matchup-b": { towerId: "", specialByCard: {} }
      },
      rankingMetric: "usage_rate",
      rankingCards: [],
      rankingPayload: null,
      rankingAbortController: null,
      sessionId: localStorage.getItem("cr_agent_session_id") || crypto.randomUUID()
    };
    localStorage.setItem("cr_agent_session_id", state.sessionId);

    const formatNumber = value => Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
    const formatPercent = value => `${formatNumber(value)}%`;
    const make = (tag, className = "", text = "") => {
      const el = document.createElement(tag);
      if (className) el.className = className;
      if (text !== "") el.textContent = text;
      return el;
    };
    const clear = element => { while (element.firstChild) element.removeChild(element.firstChild); };

    async function requestJSON(url, options) {
      let requestUrl = url;
      let requestOptions = options ? { ...options } : {};
      if (url.startsWith("/api/") && url !== "/api/datasets") {
        const method = String(requestOptions.method || "GET").toUpperCase();
        if (method === "GET") {
          const separator = requestUrl.includes("?") ? "&" : "?";
          requestUrl += `${separator}dataset_scope=${encodeURIComponent(state.datasetScope)}`;
        } else {
          const body = requestOptions.body ? JSON.parse(requestOptions.body) : {};
          requestOptions.body = JSON.stringify({ ...body, dataset_scope: state.datasetScope });
        }
      }
      const response = await fetch(requestUrl, requestOptions);
      let body;
      try { body = await response.json(); } catch (_) { body = {}; }
      if (!response.ok) {
        const error = body.error || body.detail?.error || body.detail || {};
        const failure = new Error(error.message || "请求失败");
        failure.code = error.code || "REQUEST_FAILED";
        failure.details = error.details || {};
        throw failure;
      }
      return body;
    }

    function applyDataMode() {
      state.deckMode = state.entityMode === "loadout_entity" ? "full_loadout" : "base8";
      const dataset = state.datasets.get(state.datasetScope);
      document.querySelectorAll("[data-mode]").forEach(button => {
        button.classList.toggle("active", button.dataset.mode === state.entityMode);
        if (button.dataset.mode === "loadout_entity") {
          button.disabled = Boolean(dataset) && (
            dataset.complete_loadout_ready !== true || dataset.entity_stats_ready !== true
          );
          button.title = button.disabled ? "当前范围缺少完整配置统计" : "";
        }
      });
      document.querySelectorAll(".full-loadout-only").forEach(element => {
        element.hidden = state.deckMode !== "full_loadout";
      });
      document.querySelectorAll("[data-page=deck] .snapshot-id").forEach(element => {
        element.textContent = state.deckMode === "full_loadout" ? "塔楼 + 8 张卡 + 觉醒 / 精英" : "严格 8 张普通卡";
      });
      document.querySelectorAll("[data-page=matchup] .snapshot-id").forEach(element => {
        element.textContent = state.deckMode === "full_loadout" ? "完整配置对完整配置" : "精确八卡对八卡";
      });
      if (state.deckMode === "full_loadout") {
        Promise.all([loadLoadoutCatalog(), loadEntityCatalog()]).then(() => {
          renderLoadoutDetails("deck-profile", "deckLoadoutDetails");
          renderLoadoutDetails("matchup-a", "matchupALoadoutDetails");
          renderLoadoutDetails("matchup-b", "matchupBLoadoutDetails");
          ["single-card", "compare-cards", "deck-profile", "matchup-a", "matchup-b"]
            .forEach(name => state.pickers.get(name)?.render());
        }).catch(error => {
          ["deckLoadoutDetails", "matchupALoadoutDetails", "matchupBLoadoutDetails"].forEach(id => {
            const element = document.getElementById(id);
            if (element) { clear(element); element.hidden = false; element.appendChild(make("div", "form-error", error.message)); }
          });
        });
      }
    }

    function selectEntityMode(mode) {
      const dataset = state.datasets.get(state.datasetScope);
      if (mode === "loadout_entity" && dataset && (
        dataset.complete_loadout_ready !== true || dataset.entity_stats_ready !== true
      )) return;
      state.entityMode = mode;
      ["single-card", "compare-cards", "deck-profile", "matchup-a", "matchup-b"].forEach(name => {
        const picker = state.pickers.get(name);
        if (picker) picker.selection.splice(0, picker.selection.length);
      });
      applyDataMode();
      if (state.deckMode === "base8") {
        ["single-card", "compare-cards", "deck-profile", "matchup-a", "matchup-b"]
          .forEach(name => state.pickers.get(name)?.render());
      }
      const activeView = document.querySelector(".view:not([hidden])")?.dataset.page;
      if (activeView === "rankings") loadCardRankings();
    }

    async function loadLoadoutCatalog() {
      if (state.loadoutCatalog && state.loadoutCatalogScope === state.datasetScope) return state.loadoutCatalog;
      const catalog = await requestJSON("/api/loadouts/catalog");
      state.loadoutCatalog = catalog;
      state.loadoutCatalogScope = state.datasetScope;
      return catalog;
    }

    async function loadEntityCatalog() {
      const catalog = await requestJSON("/api/entities/catalog");
      state.entityCatalog = (catalog.entities || []).map(entity => ({
        ...entity,
        card_id: entity.entity_id,
        display_name_zh: entity.display_name_zh || entity.entity_id
      }));
      return state.entityCatalog;
    }

    function renderLoadoutDetails(pickerName, elementId) {
      const target = document.getElementById(elementId);
      if (!target || state.deckMode !== "full_loadout" || !state.loadoutCatalog) return;
      clear(target);
      const config = state.loadouts[pickerName];
      const heading = make("div", "loadout-heading", "完整配置");
      const help = make("div", "loadout-help", "完整模式会严格匹配塔楼、觉醒和精英状态；未知或样本不足时不会自动退回普通 8 卡统计。");
      const towerRow = make("label", "loadout-tower");
      towerRow.appendChild(make("span", "loadout-card-label", "塔楼"));
      const tower = make("select", "loadout-select");
      tower.appendChild(make("option", "", "请选择塔楼"));
      (state.loadoutCatalog.towers || []).forEach(item => {
        const towerId = item.tower_id || item.id;
        const option = make("option", "", item.display_name_zh || towerId);
        option.value = towerId;
        option.selected = config.towerId === towerId;
        tower.appendChild(option);
      });
      tower.addEventListener("change", () => { config.towerId = tower.value; });
      towerRow.appendChild(tower);
      target.append(heading, help, towerRow);
      const picker = state.pickers.get(pickerName);
      (picker ? picker.selection : []).forEach(card => {
        const row = make("label", "loadout-card-row");
        row.appendChild(make("span", "loadout-card-label", card.display_name_zh));
        const select = make("select", "loadout-select");
        const capability = (state.loadoutCatalog.cards || []).find(item => item.card_id === card.card_id) || {};
        [["normal", "普通"], ["evolution", "觉醒"], ["elite", "精英"]].forEach(([value, label]) => {
          const option = make("option", "", label);
          option.value = value;
          option.disabled = value === "evolution" ? capability.can_evolve === false : value === "elite" ? capability.can_be_elite === false : false;
          option.selected = (config.specialByCard[card.card_id] || "normal") === value;
          select.appendChild(option);
        });
        select.addEventListener("change", () => { config.specialByCard[card.card_id] = select.value; });
        row.appendChild(select);
        target.appendChild(row);
      });
      if (!picker || picker.selection.length !== 8) target.appendChild(make("div", "loadout-help", "先选择完整的 8 张卡牌，再配置状态。"));
    }

    function buildLoadout(pickerName, errorEl) {
      const cards = state.pickers.get(pickerName).selection;
      if (!validateDeck(cards.map(card => card.card_id), errorEl)) return null;
      const config = state.loadouts[pickerName];
      if (!config.towerId) { errorEl.textContent = "请选择塔楼"; return null; }
      const fullCards = cards.map(card => {
        const special = config.specialByCard[card.card_id] || "normal";
        return { card_id: card.card_id, evolution_level: special === "evolution" ? 1 : special === "elite" ? 2 : 0, elite: special === "elite" };
      });
      const evolutionCount = fullCards.filter(card => card.evolution_level === 1).length;
      const eliteCount = fullCards.filter(card => card.elite).length;
      if (evolutionCount > 2 || eliteCount > 2 || evolutionCount + eliteCount > 3) {
        errorEl.textContent = "完整配置限制为最多 2 个觉醒、最多 2 个精英，觉醒与精英合计最多 3 个";
        return null;
      }
      errorEl.textContent = "";
      return { tower_id: config.towerId, cards: fullCards };
    }

    function scopePrefix(windowValue) {
      return ["7", "35"].includes(String(windowValue)) ? `${windowValue}d` : String(windowValue);
    }

    function updateScopePresentation() {
      document.querySelectorAll("[data-window]").forEach(button => {
        button.classList.toggle("active", button.dataset.window === state.windowDays);
        const candidate = `${scopePrefix(button.dataset.window)}_${state.dataLevel}`;
        const dataset = state.datasets.get(candidate);
        button.disabled = dataset ? dataset.ready === false : candidate !== "7d_all";
      });
      document.querySelectorAll("[data-level]").forEach(button => {
        button.classList.toggle("active", button.dataset.level === state.dataLevel);
        const candidate = `${scopePrefix(state.windowDays)}_${button.dataset.level}`;
        const dataset = state.datasets.get(candidate);
        button.disabled = dataset ? dataset.ready === false : candidate !== "7d_all";
      });
      const dataset = state.datasets.get(state.datasetScope);
      document.getElementById("scopeSummary").textContent = dataset?.ready
        ? `${state.datasetScope} · ${formatNumber(dataset.unique_battles)} 场唯一对局 · 周采 ${formatNumber(dataset.weekly_batch_count)} 批 · 日采 ${formatNumber(dataset.daily_batch_count)} 批`
        : "滚动快照尚未发布，当前仅保留旧版 7 天全量兼容数据";
      applyDataMode();
      renderDatasetOverview();
    }

    async function selectDatasetScope(windowDays, dataLevel) {
      const nextScope = `${scopePrefix(windowDays)}_${dataLevel}`;
      const dataset = state.datasets.get(nextScope);
      if (dataset && dataset.ready === false && nextScope !== "7d_all") return;
      state.windowDays = windowDays;
      state.dataLevel = dataLevel;
      state.datasetScope = nextScope;
      updateScopePresentation();
      const rankingVisible = !document.getElementById("view-rankings").hidden;
      const metaVisible = !document.getElementById("view-meta").hidden;
      const refreshes = [];
      if (rankingVisible) refreshes.push(loadCardRankings());
      if (metaVisible) refreshes.push(loadMeta());
      const catalog = await requestJSON("/api/cards/catalog");
      state.catalog = catalog.cards;
      state.pickers.forEach(picker => picker.render());
      state.loadoutCatalog = null;
      state.loadoutCatalogScope = null;
      if (state.deckMode === "full_loadout") await Promise.all([loadLoadoutCatalog(), loadEntityCatalog()]);
      [
        ["deck-profile", "deckLoadoutDetails"],
        ["matchup-a", "matchupALoadoutDetails"],
        ["matchup-b", "matchupBLoadoutDetails"]
      ].forEach(([pickerName, elementId]) => renderLoadoutDetails(pickerName, elementId));
      applyDataMode();
      renderDatasetOverview();
      await Promise.all(refreshes);
    }

    function activateView(name) {
      document.querySelectorAll(".nav-button").forEach(button => button.classList.toggle("active", button.dataset.view === name));
      document.querySelectorAll(".view").forEach(view => { view.hidden = view.id !== `view-${name}`; });
      location.hash = name;
      if (name === "meta") loadMeta();
      if (name === "rankings") loadCardRankings();
    }
    document.querySelectorAll(".nav-button").forEach(button => button.addEventListener("click", () => activateView(button.dataset.view)));

    function pickerCatalog(pickerName) {
      const fullLoadoutPickers = ["deck-profile", "matchup-a", "matchup-b"];
      if (state.deckMode === "full_loadout" && fullLoadoutPickers.includes(pickerName)) {
        return state.loadoutCatalog?.cards || [];
      }
      return state.entityMode === "loadout_entity" && ["single-card", "compare-cards"].includes(pickerName)
        ? state.entityCatalog
        : state.catalog;
    }

    function createPicker(container, limit) {
      const pickerName = container.dataset.picker;
      const selection = [];
      const header = make("div", "picker-header");
      const label = make("div", "field-label", limit === 1 ? "选择卡牌" : `已选择 0 / ${limit}`);
      const count = make("div", "picker-count", `${pickerCatalog(pickerName).length} 项`);
      header.append(label, count);
      const search = make("input", "search-input");
      search.type = "search";
      search.placeholder = "搜索中文卡名";
      search.setAttribute("aria-label", `${pickerName} 搜索卡牌`);
      const slots = make("div", "selected-slots");
      if (limit === 1) slots.style.gridTemplateColumns = "1fr";
      const grid = make("div", "card-grid");
      container.append(header, search, slots, grid);

      function renderSlots() {
        clear(slots);
        for (let index = 0; index < limit; index += 1) {
          const card = selection[index];
          const slot = make("div", `selected-slot${card ? " filled" : ""}`, card ? card.display_name_zh : `卡位 ${index + 1}`);
          if (card) {
            slot.title = "点击移除";
            slot.addEventListener("click", () => toggle(card));
          }
          slots.appendChild(slot);
        }
        label.textContent = limit === 1 ? "选择卡牌" : `已选择 ${selection.length} / ${limit}`;
      }
      function toggle(card) {
        const index = selection.findIndex(value => value.card_id === card.card_id);
        if (index >= 0) selection.splice(index, 1);
        else if (selection.length < limit) selection.push(card);
        else if (limit === 1) selection.splice(0, 1, card);
        render();
      }
      function render() {
        renderSlots();
        clear(grid);
        const catalog = pickerCatalog(pickerName);
        count.textContent = `${catalog.length} 项`;
        const term = search.value.trim().toLowerCase();
        catalog.filter(card => !term || card.display_name_zh.includes(term) || card.card_id.toLowerCase().includes(term)).forEach(card => {
          const selected = selection.some(value => value.card_id === card.card_id);
          const tile = make("button", `card-tile${selected ? " selected" : ""}`, card.display_name_zh);
          tile.type = "button";
          tile.title = card.card_id;
          tile.dataset.cardId = card.card_id;
          tile.addEventListener("click", () => toggle(card));
          grid.appendChild(tile);
        });
        container.dispatchEvent(new Event("pickerchange"));
      }
      search.addEventListener("input", render);
      render();
      const api = {
        selection,
        render,
        ids: () => selection.map(card => card.card_id),
        select: cardId => {
          const card = pickerCatalog(pickerName).find(value => value.card_id === cardId);
          if (!card) return;
          selection.splice(0, selection.length, card);
          render();
        }
      };
      state.pickers.set(pickerName, api);
      return api;
    }

    function metricGrid(items) {
      const grid = make("div", "metric-grid");
      items.forEach(([label, value, detail]) => {
        const metric = make("div", "metric");
        metric.append(make("div", "metric-label", label), make("div", "metric-value", value));
        if (detail) metric.appendChild(make("div", "metric-detail", detail));
        grid.appendChild(metric);
      });
      return grid;
    }
    function renderWarning(parent, warning) {
      if (!warning) return;
      parent.appendChild(make("div", "warning", warning.code === "LOW_SAMPLE_WARNING" ? `低样本：${warning.matched_sample_count} 场` : warning.message));
    }
    function displayCard(cardId) {
      return state.loadoutCatalog?.cards?.find(card => card.card_id === cardId)?.display_name_zh
        || state.catalog.find(card => card.card_id === cardId)?.display_name_zh
        || cardId;
    }
    function displayTower(tower) {
      const towerId = tower?.id || tower?.tower_id;
      return tower?.display_name_zh
        || state.loadoutCatalog?.towers?.find(item => (item.tower_id || item.id) === towerId)?.display_name_zh
        || tower?.name
        || towerId
        || "未知塔楼";
    }
    function formatLoadout(loadout) {
      if (!loadout) return "-";
      const tower = displayTower(loadout.tower || { id: loadout.tower_id });
      const cards = (loadout.cards || []).map(card => {
        const name = displayCard(card.card_id || card.id);
        const mode = card.elite ? "精英" : Number(card.evolution_level || 0) > 0 ? "觉醒" : "普通";
        return `${name}（${mode}）`;
      });
      return `${tower} · ${cards.join(" / ")}`;
    }
    function displayArchetype(name) {
      const names = {
        "E-Giant beatdown": "电磁巨人推进",
        "Hog EQ": "野猪地震",
        "Hog cycle": "野猪速转",
        "Lava air beatdown": "天狗空中推进",
        "Golem beatdown": "石头人推进",
        "PEKKA bridge spam": "皮卡桥头压制",
        "PEKKA control": "皮卡控制",
        "Log bait": "滚木诱饵",
        "X-Bow siege": "连弩攻城",
        "Mortar control": "迫击炮控制",
        "Royal Giant": "皇家巨人",
        "Goblin Giant beatdown": "哥布林巨人推进",
        "Graveyard control": "墓园控制",
        "Balloon pressure": "气球压制",
        "Goblin Drill control": "哥布林钻机控制",
        "Miner control": "矿工控制",
        "Giant beatdown": "巨人推进",
        "Unclassified deck family": "未分类卡组"
      };
      return names[name] || name;
    }
    function renderProvenance(parent, data) {
      const provenance = data.provenance || {};
      const block = make("div", "provenance");
      const matched = Array.isArray(data.matched_sample_count)
        ? data.matched_sample_count.map(formatNumber).join(" / ")
        : formatNumber(data.matched_sample_count);
      block.textContent = `快照 ${provenance.snapshot_id || "未知"} · 总样本 ${formatNumber(provenance.total_sample_battles)} 场 · 纳入 ${formatNumber(provenance.included_battles)} 场 · 排除不完整卡组 ${formatNumber(provenance.excluded_incomplete_decks)} 场 · 本次匹配 ${matched} 场`;
      parent.appendChild(block);
    }
    function renderFailure(target, error) {
      clear(target);
      const title = error.code === "NO_MATCHUP_EVIDENCE" ? "没有精确对阵证据" : "查询失败";
      const localizedMessages = {
        NO_MATCHUP_EVIDENCE: "当前官方快照中，这两套完整八卡卡组之间没有找到任何对局。",
        DECK_NOT_FOUND: "当前官方快照中没有找到这套完整八卡卡组。",
        CARD_NOT_FOUND: "当前官方快照中没有找到这张卡牌。"
      };
      target.append(make("h2", "", title), make("div", "result-empty", localizedMessages[error.code] || error.message));
      if (error.details?.matched_sample_count === 0) target.appendChild(make("div", "warning", "匹配样本：0 场"));
    }
    function table(headers, rows) {
      const wrap = make("div", "data-table-wrap");
      const element = make("table");
      const head = make("thead");
      const headRow = make("tr");
      headers.forEach(label => headRow.appendChild(make("th", "", label)));
      head.appendChild(headRow);
      const body = make("tbody");
      rows.forEach(values => { const row = make("tr"); values.forEach(value => row.appendChild(make("td", "", String(value)))); body.appendChild(row); });
      element.append(head, body); wrap.appendChild(element); return wrap;
    }

    function openCardFromRanking(cardId) {
      state.pickers.get("single-card").select(cardId);
      activateView("card");
      submitCard();
    }
    function renderCardRankings() {
      const target = document.getElementById("rankingResult");
      const summary = document.getElementById("rankingSummary");
      const term = document.getElementById("rankingSearch").value.trim().toLowerCase();
      const cards = state.rankingCards.filter(card =>
        !term || card.display_name_zh.toLowerCase().includes(term) || card.card_name.toLowerCase().includes(term)
      );
      clear(target);
      if (!cards.length) {
        target.appendChild(make("div", "result-empty", "没有符合筛选条件的卡牌"));
        summary.textContent = `0 / ${formatNumber(state.rankingCards.length)} 张`;
        return;
      }
      const wrap = make("div", "data-table-wrap");
      const element = make("table", "ranking-table");
      const headers = [
        ["排名", null], ["卡牌", null], ["使用率", "usage_rate"], ["胜率", "clean_win_rate"],
        ["评分", "rating"], ["出场", null], ["胜 / 负 / 平", null]
      ];
      const head = make("thead");
      const headRow = make("tr");
      headers.forEach(([label, metric]) => headRow.appendChild(make("th", metric === state.rankingMetric ? "sorted-column" : "", label)));
      head.appendChild(headRow);
      const body = make("tbody");
      cards.forEach(card => {
        const row = make("tr");
        row.appendChild(make("td", "ranking-rank", `#${card.rank}`));
        const nameCell = make("td");
        const nameButton = make("button", "ranking-card-button", card.display_name_zh);
        nameButton.type = "button";
        nameButton.title = `查看 ${card.display_name_zh} 单卡详情`;
        nameButton.addEventListener("click", () => openCardFromRanking(card.card_name));
        nameCell.appendChild(nameButton);
        if (card.is_low_sample) nameCell.appendChild(make("span", "low-sample-text", "低样本"));
        row.appendChild(nameCell);
        [
          [formatPercent(card.usage_rate), "usage_rate"],
          [formatPercent(card.clean_win_rate), "clean_win_rate"],
          [formatNumber(card.rating), "rating"],
          [formatNumber(card.appearances), null],
          [`${formatNumber(card.wins)} / ${formatNumber(card.losses)} / ${formatNumber(card.draws)}`, null]
        ].forEach(([value, metric]) => row.appendChild(make("td", metric === state.rankingMetric ? "sorted-column" : "", value)));
        body.appendChild(row);
      });
      element.append(head, body);
      wrap.appendChild(element);
      target.appendChild(wrap);
      const provenance = state.rankingPayload?.provenance || {};
      target.appendChild(make("div", "provenance", `快照 ${provenance.snapshot_id || "未知"} · ${state.datasetScope} · 唯一对局 ${formatNumber(provenance.unique_battles)} 场 · 双侧记录 ${formatNumber(provenance.side_records)} 条`));
      summary.textContent = `${formatNumber(cards.length)} / ${formatNumber(state.rankingCards.length)} 张 · 按${{usage_rate:"使用率",clean_win_rate:"胜率",rating:"评分"}[state.rankingMetric]}降序`;
    }
    async function loadCardRankings() {
      if (state.rankingAbortController) state.rankingAbortController.abort();
      const controller = new AbortController();
      state.rankingAbortController = controller;
      const target = document.getElementById("rankingResult");
      clear(target);
      target.appendChild(make("div", "result-empty", "正在读取全卡排名"));
      try {
        const endpoint = state.entityMode === "loadout_entity" ? "/api/entities/rankings" : "/api/cards/rankings";
        const data = await requestJSON(`${endpoint}?sort_by=${encodeURIComponent(state.rankingMetric)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        state.rankingPayload = data;
        state.rankingCards = state.entityMode === "loadout_entity"
          ? (data.entities || []).map(entity => ({ ...entity, card_name: entity.entity_id }))
          : (data.cards || []);
        renderCardRankings();
      } catch (error) {
        if (error.name !== "AbortError") renderFailure(target, error);
      } finally {
        if (state.rankingAbortController === controller) state.rankingAbortController = null;
      }
    }

    async function submitCard() {
      const target = document.getElementById("cardResult");
      const errorEl = document.getElementById("cardError");
      const ids = state.pickers.get("single-card").ids();
      if (ids.length !== 1) { errorEl.textContent = "请选择 1 张卡牌"; return; }
      errorEl.textContent = "";
      if (state.entityMode === "loadout_entity") return submitEntityCard(ids[0]);
      try {
        const data = await requestJSON(`/api/cards/${encodeURIComponent(ids[0])}/stats`);
        const card = data.card; clear(target); target.appendChild(make("h2", "", displayCard(card.card_name)));
        renderWarning(target, data.warning);
        target.appendChild(metricGrid([["使用率", formatPercent(card.usage_rate)], ["干净胜率", formatPercent(card.clean_win_rate)], ["净胜率", formatPercent(card.net_win_rate)], ["评分", formatNumber(card.rating)], ["出场", formatNumber(card.appearances)], ["胜 / 负 / 平", `${formatNumber(card.wins)} / ${formatNumber(card.losses)} / ${formatNumber(card.draws)}`]]));
        target.appendChild(make("h3", "", "常见搭配"));
        target.appendChild(table(["卡牌", "场数"], data.common_teammates.slice(0, 6).map(item => [displayCard(item.card_id), formatNumber(item.games)])));
        renderProvenance(target, data);
      } catch (error) { renderFailure(target, error); }
    }
    async function submitEntityCard(entityId = state.pickers.get("single-card").ids()[0]) {
      const target = document.getElementById("cardResult");
      try {
        const data = await requestJSON(`/api/entities/${encodeURIComponent(entityId)}/stats`);
        const entity = data.entity; clear(target); target.appendChild(make("h2", "", entity.display_name_zh));
        renderWarning(target, data.warning);
        target.appendChild(metricGrid([["使用率", formatPercent(entity.usage_rate)], ["干净胜率", formatPercent(entity.clean_win_rate)], ["净胜率", formatPercent(entity.net_win_rate)], ["评分", formatNumber(entity.rating)], ["出场", formatNumber(entity.appearances)], ["形态", entity.special_state]]));
        renderProvenance(target, data);
      } catch (error) { renderFailure(target, error); }
    }
    async function submitCompare() {
      const target = document.getElementById("compareResult"); const errorEl = document.getElementById("compareError");
      const ids = state.pickers.get("compare-cards").ids();
      if (ids.length !== 2) { errorEl.textContent = "请选择 2 张不同卡牌"; return; }
      errorEl.textContent = "";
      if (state.entityMode === "loadout_entity") return submitEntityCompare(ids);
      try {
        const data = await requestJSON("/api/cards/compare", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ card_ids: ids }) });
        clear(target); target.appendChild(make("h2", "", "表现对比"));
        target.appendChild(table(["卡牌", "使用率", "胜率", "净胜率", "评分", "样本"], data.cards.map(card => [displayCard(card.card_name), formatPercent(card.usage_rate), formatPercent(card.clean_win_rate), formatPercent(card.net_win_rate), formatNumber(card.rating), formatNumber(card.appearances)])));
        data.warnings.forEach(warning => renderWarning(target, warning)); renderProvenance(target, data);
      } catch (error) { renderFailure(target, error); }
    }
    async function submitEntityCompare(entityIds = state.pickers.get("compare-cards").ids()) {
      const target = document.getElementById("compareResult");
      try {
        const data = await requestJSON("/api/entities/compare", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entity_ids: entityIds }) });
        clear(target); target.appendChild(make("h2", "", "完整配置实体对比"));
        target.appendChild(table(["实体", "使用率", "胜率", "净胜率", "评分", "样本"], data.entities.map(entity => [entity.display_name_zh, formatPercent(entity.usage_rate), formatPercent(entity.clean_win_rate), formatPercent(entity.net_win_rate), formatNumber(entity.rating), formatNumber(entity.appearances)])));
        (data.warnings || []).forEach(warning => renderWarning(target, warning)); renderProvenance(target, data);
      } catch (error) { renderFailure(target, error); }
    }
    function validateDeck(selection, errorEl) {
      if (selection.length !== 8) { errorEl.textContent = "请恰好选择 8 张不同卡牌"; return false; }
      errorEl.textContent = ""; return true;
    }
    async function submitDeck() {
      const target = document.getElementById("deckResult"); const errorEl = document.getElementById("deckError"); const cards = state.pickers.get("deck-profile").ids();
      if (!validateDeck(cards, errorEl)) return;
      try {
        const loadout = state.deckMode === "full_loadout" ? buildLoadout("deck-profile", errorEl) : null;
        if (state.deckMode === "full_loadout" && !loadout) return;
        const payload = state.deckMode === "full_loadout" ? { deck_mode: "full_loadout", loadout } : { deck_mode: "base8", cards };
        const data = await requestJSON("/api/decks/profile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        clear(target); renderWarning(target, data.warning);
        if (data.deck_mode === "full_loadout") {
          const profile = data.loadout || {};
          target.appendChild(make("h2", "", "完整配置画像"));
          target.appendChild(metricGrid([["塔楼", displayTower(profile.loadout?.tower)], ["使用率", formatPercent(profile.usage_rate)], ["干净胜率", formatPercent(profile.clean_win_rate)], ["对局", formatNumber(profile.games)], ["胜 / 负 / 平", `${profile.wins || 0} / ${profile.losses || 0} / ${profile.draws || 0}`]]));
          target.appendChild(make("h3", "", "常见完整配置对手"));
          target.appendChild(table(["对手配置", "场数", "胜率"], (data.common_opponents || []).map(item => [formatLoadout(item.loadout), item.games, formatPercent(item.clean_win_rate)])));
        } else {
          const deck = data.deck; target.appendChild(make("h2", "", displayArchetype(deck.archetype)));
          target.appendChild(metricGrid([["使用率", formatPercent(deck.usage_rate)], ["干净胜率", formatPercent(deck.clean_win_rate)], ["净胜率", formatPercent(deck.net_win_rate)], ["对局", formatNumber(deck.games)], ["胜 / 负 / 平", `${deck.wins} / ${deck.losses} / ${deck.draws}`], ["场均皇冠", formatNumber(deck.crowns / deck.games)]]));
          target.appendChild(make("h3", "", "常见对手")); target.appendChild(table(["对手卡组", "场数", "胜率"], data.common_opponents.map(item => [item.cards.map(displayCard).join(" / "), item.games, formatPercent(item.clean_win_rate)])));
        }
        renderProvenance(target, data);
      } catch (error) { renderFailure(target, error); }
    }
    async function submitMatchup() {
      const target = document.getElementById("matchupResult"); const errorEl = document.getElementById("matchupError"); const deckA = state.pickers.get("matchup-a").ids(); const deckB = state.pickers.get("matchup-b").ids();
      if (!validateDeck(deckA, errorEl) || !validateDeck(deckB, errorEl)) return;
      try {
        const loadoutA = state.deckMode === "full_loadout" ? buildLoadout("matchup-a", errorEl) : null;
        const loadoutB = state.deckMode === "full_loadout" ? buildLoadout("matchup-b", errorEl) : null;
        if (state.deckMode === "full_loadout" && (!loadoutA || !loadoutB)) return;
        const payload = state.deckMode === "full_loadout" ? { deck_mode: "full_loadout", loadout_a: loadoutA, loadout_b: loadoutB } : { deck_mode: "base8", deck_a: deckA, deck_b: deckB };
        const data = await requestJSON("/api/decks/matchup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        clear(target); target.appendChild(make("h2", "", state.deckMode === "full_loadout" ? "完整配置对阵结果" : "精确对阵结果")); renderWarning(target, data.warning);
        target.appendChild(metricGrid([["卡组 A 胜率", formatPercent(data.loadout_a?.clean_win_rate ?? data.deck_a.clean_win_rate)], ["卡组 B 胜率", formatPercent(data.loadout_b?.clean_win_rate ?? data.deck_b.clean_win_rate)], ["匹配对局", formatNumber(data.games)], ["平局", formatNumber(data.draws)], ["A 场均皇冠", formatNumber(data.loadout_a?.average_crowns ?? data.deck_a.average_crowns)], ["B 场均皇冠", formatNumber(data.loadout_b?.average_crowns ?? data.deck_b.average_crowns)]])); renderProvenance(target, data);
      } catch (error) { renderFailure(target, error); }
    }
    async function loadMeta() {
      const target = document.getElementById("metaResult");
      try {
        const data = await requestJSON("/api/meta/archetypes"); clear(target);
        const familyTotals = new Map();
        data.archetypes.forEach(item => familyTotals.set(item.family, (familyTotals.get(item.family) || 0) + item.games));
        const familyRows = Array.from(familyTotals.entries()).sort((left, right) => right[1] - left[1]);
        target.appendChild(table(["上层类型", "样本"], familyRows.map(item => [item[0], formatNumber(item[1])])));
        target.appendChild(table(["体系", "使用率", "干净胜率", "净胜率", "样本"], data.archetypes.map(item => [displayArchetype(item.archetype), formatPercent(item.usage_rate), formatPercent(item.clean_win_rate), formatPercent(item.net_win_rate), formatNumber(item.games)])));
        renderProvenance(target, data);
      } catch (error) { renderFailure(target, error); }
    }

    function renderVisualizationDashboard(snapshot, extra = {}) {
      const artifacts = snapshot.artifacts || {}; const rag = snapshot.rag || {}; const runtime = snapshot.runtime || {};
      const renderCard = (id, status, rows) => { const card = document.getElementById(id); const pill = card.querySelector(".status-pill"); const body = card.querySelector(".viz-body"); pill.textContent = status; clear(body); rows.forEach(([label, value]) => body.appendChild(make("div", "", `${label}：${value}`))); };
      renderCard("dataLineageViz", artifacts.structured_stats?.status === "ready" ? "已对齐" : "未就绪", [["官方快照", snapshot.snapshot_id || "无"], ["审计导出", artifacts.audit_export?.status || "unavailable"], ["结构化索引", artifacts.structured_stats?.status || "unavailable"]]);
      renderCard("qualityGateViz", rag.fingerprint_aligned ? "通过" : "待对齐", [["RAG 状态", rag.status || "unknown"], ["证据文档", Object.values(rag.document_counts || {}).reduce((sum, value) => sum + Number(value || 0), 0)], ["指纹", rag.fingerprint_aligned ? "一致" : "不一致"]]);
      renderCard("opsViz", extra.ready?.status || "可用", [["模型熔断", extra.model?.circuit_state || "unknown"], ["请求", runtime.process_requests || 0], ["失败", runtime.failures || 0]]);
    }
    function renderDatasetOverview(snapshot = state.snapshot) {
      const dataset = state.datasets.get(state.datasetScope);
      if (!dataset) return;
      const counts = dataset.structured_counts || {};
      const rag = state.datasetCatalog?.rag || {};
      document.getElementById("headerDot").classList.toggle("ready", dataset.ready === true);
      document.getElementById("headerState").textContent = dataset.ready ? "数据可用" : "范围未就绪";
      document.getElementById("headerSnapshot").textContent = `${dataset.snapshot_id || state.datasetScope} · ${formatNumber(dataset.unique_battles)} 场`;
      document.getElementById("homeSnapshotId").textContent = dataset.snapshot_id || "未就绪";
      const grid = document.getElementById("snapshotGrid"); clear(grid);
      [
        ["唯一对局", formatNumber(dataset.unique_battles), state.datasetScope],
        ["结构化对局", formatNumber(counts.included_battles), "滚动事实库去重样本"],
        ["完整配置侧记录", formatNumber(counts.full_loadout_side_records), dataset.complete_loadout_ready ? "塔楼与卡牌形态可用" : "当前范围尚未就绪"],
        ["采集批次", formatNumber(Number(dataset.weekly_batch_count || 0) + Number(dataset.daily_batch_count || 0)), `周采 ${formatNumber(dataset.weekly_batch_count)} · 日采 ${formatNumber(dataset.daily_batch_count)} · RAG ${formatNumber(rag.document_count)}`]
      ].forEach(([label,value,detail]) => { const card = make("div", "metric"); card.append(make("div", "metric-label", label), make("div", "metric-value", value), make("div", "metric-detail", detail)); grid.appendChild(card); });
    }
    async function loadSnapshot() {
      try {
        const snapshot = await requestJSON("/snapshot/status"); state.snapshot = snapshot;
        renderDatasetOverview(snapshot);
        const [ready, model] = await Promise.all([fetch("/ready").then(r => r.json()), fetch("/model/status").then(r => r.json())]);
        fetch("/metrics").catch(() => null); fetch("/feedback/stats").catch(() => null);
        renderVisualizationDashboard(snapshot, { ready, model });
      } catch (_) { document.getElementById("headerState").textContent = "后端不可用"; }
    }

    const chatBox = document.getElementById("chatBox"); const inputBox = document.getElementById("inputBox"); const sendBtn = document.getElementById("sendBtn"); const statusEl = document.getElementById("status"); const traceList = document.getElementById("traceList"); const traceSummary = document.getElementById("traceSummary"); const debugTrace = document.getElementById("debugTrace"); const executionPanel = document.getElementById("executionPanel");
    function appendMessage(role, text) { const wrapper = make("div", `msg ${role}`); wrapper.append(make("div", "meta", role === "user" ? "你" : "分析助手")); const bubble = make("div", "bubble", text); wrapper.appendChild(bubble); chatBox.appendChild(wrapper); chatBox.scrollTop = chatBox.scrollHeight; return bubble; }
    function normalizeVisibleAnswerText(text) {
      const sectionNames = { "conclusion": "结论", "data evidence": "数据依据", "data boundaries": "数据边界", "data boundary": "数据边界" };
      const lines = String(text || "").split("\n").map(line => {
        const clean = line.replace(/^\s*#{1,6}\s*/, "");
        const key = clean.trim().replace(/[：:]$/, "").toLowerCase();
        return sectionNames[key] || clean;
      });
      return lines.join("\n").replace(/^\s*\*\s+/gm, "- ").replaceAll("*", "").replaceAll("__", "");
    }
    function resetExecution() { clear(traceList); debugTrace.textContent = ""; executionPanel.open = true; }
    function renderExecution(event) { const line = make("div", "trace-line", `${event.title || event.phase || "处理中"}：${event.detail || ""}${Number.isFinite(event.elapsed_ms) ? ` · ${event.elapsed_ms}ms` : ""}`); line.dataset.stepId = event.step_id || ""; const old = [...traceList.children].find(item => item.dataset.stepId && item.dataset.stepId === line.dataset.stepId); if (old) old.replaceWith(line); else traceList.appendChild(line); }
    function handleSseEvent(event, bubble, statusTarget = statusEl, recordTrace = true) { if (event.request_id) bubble.dataset.requestId = event.request_id; if (event.object === "progress") statusTarget.textContent = event.label || "正在处理"; if (event.object === "execution" && recordTrace) renderExecution(event); if (event.object === "execution" && !recordTrace) statusTarget.textContent = event.title || "正在分析"; if (event.object === "content" && event.type === "text") { bubble.dataset.rawAnswer = (bubble.dataset.rawAnswer || "") + (event.text || ""); bubble.textContent = normalizeVisibleAnswerText(bubble.dataset.rawAnswer); } if (event.object === "trace" && recordTrace) { traceSummary.textContent = (event.trace_id || "已完成").slice(0, 18); debugTrace.textContent = JSON.stringify(event, null, 2); } if (event.object === "error") throw new Error(event.message || "后端处理失败"); }
    async function sendFeedback(requestId, rating) { await requestJSON("/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request_id: requestId, rating }) }); }
    function addFeedbackControls(bubble) { if (!bubble.dataset.requestId) return; const controls = make("div", "feedback-actions"); [["有帮助", "positive"], ["需改进", "negative"]].forEach(([label,rating]) => { const button = make("button", "feedback-button", label); button.addEventListener("click", async () => { await sendFeedback(bubble.dataset.requestId, rating); controls.querySelectorAll("button").forEach(item => item.disabled = true); }); controls.appendChild(button); }); bubble.parentElement.appendChild(controls); }
    async function streamAnswer(message, bubble, statusTarget, recordTrace = true, intentHint = null) {
      const response = await fetch("/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, session_id: state.sessionId, user_id: "web-user-1", intent_hint: intentHint, dataset_scope: state.datasetScope, deck_mode: state.deckMode, entity_mode: state.entityMode }) });
      if (!response.ok || !response.body) throw new Error(await response.text() || "请求失败"); const reader = response.body.getReader(); const decoder = new TextDecoder("utf-8"); let buffer = ""; let complete = false;
      while (!complete) { const { value, done } = await reader.read(); if (done) break; buffer += decoder.decode(value, { stream: true }); let boundary; while ((boundary = buffer.indexOf("\n\n")) >= 0) { const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2); const line = frame.split("\n").find(item => item.startsWith("data: ")); if (!line) continue; const event = JSON.parse(line.slice(6)); handleSseEvent(event, bubble, statusTarget, recordTrace); complete = event.object === "response" && ["completed", "failed"].includes(event.status); } }
      if (!bubble.textContent) bubble.textContent = "没有返回可显示的回答"; addFeedbackControls(bubble); statusTarget.textContent = "";
    }
    async function sendMessage() {
      const message = inputBox.value.trim(); if (!message) return; appendMessage("user", message); inputBox.value = ""; sendBtn.disabled = true; statusEl.textContent = "正在分析"; resetExecution(); const bubble = appendMessage("agent", "");
      try { await streamAnswer(message, bubble, statusEl, true); }
      catch (error) { bubble.textContent = `请求失败：${error.message}`; statusEl.textContent = "请求失败"; }
      finally { sendBtn.disabled = false; }
    }
    async function submitMetaAnalysis() {
      const metaAnalyze = document.getElementById("metaAnalyze"); const statusTarget = document.getElementById("metaAnalysisStatus"); const target = document.getElementById("metaAnalysisResult");
      const message = "当前环境以哪些卡组体系为主？请基于当前官方快照的结构化体系统计和 RAG 证据，分析使用率、胜率、样本量与数据边界，不要提供具体打法。";
      clear(target); const bubble = make("div", "analysis-output", ""); target.appendChild(bubble); metaAnalyze.disabled = true; statusTarget.textContent = "正在检索证据";
      try { await streamAnswer(message, bubble, statusTarget, false, "meta_analysis_query"); }
      catch (error) { bubble.textContent = `分析失败：${error.message}`; statusTarget.textContent = "分析失败"; }
      finally { metaAnalyze.disabled = false; }
    }

    document.getElementById("cardSubmit").addEventListener("click", submitCard); document.getElementById("compareSubmit").addEventListener("click", submitCompare); document.getElementById("deckSubmit").addEventListener("click", submitDeck); document.getElementById("matchupSubmit").addEventListener("click", submitMatchup); document.getElementById("metaRefresh").addEventListener("click", loadMeta); const metaAnalyze = document.getElementById("metaAnalyze"); metaAnalyze.addEventListener("click", submitMetaAnalysis); sendBtn.addEventListener("click", sendMessage);
    document.querySelectorAll("[data-ranking-metric]").forEach(button => button.addEventListener("click", () => {
      state.rankingMetric = button.dataset.rankingMetric;
      document.querySelectorAll("[data-ranking-metric]").forEach(item => item.classList.toggle("active", item === button));
      loadCardRankings();
    }));
    document.getElementById("rankingSearch").addEventListener("input", renderCardRankings);
    document.querySelectorAll("[data-window]").forEach(button => button.addEventListener("click", () => selectDatasetScope(button.dataset.window, state.dataLevel)));
    document.querySelectorAll("[data-level]").forEach(button => button.addEventListener("click", () => selectDatasetScope(state.windowDays, button.dataset.level)));
    document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => selectEntityMode(button.dataset.mode)));
    document.getElementById("clearBtn").addEventListener("click", () => { clear(chatBox); state.sessionId = crypto.randomUUID(); localStorage.setItem("cr_agent_session_id", state.sessionId); resetExecution(); statusEl.textContent = "会话已清空"; });
    inputBox.addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendMessage(); } });

    async function initialize() {
      const datasetCatalog = await requestJSON("/api/datasets");
      state.datasetCatalog = datasetCatalog;
      (datasetCatalog.datasets || []).forEach(dataset => state.datasets.set(dataset.dataset_scope, dataset));
      updateScopePresentation();
      const catalog = await requestJSON("/api/cards/catalog"); state.catalog = catalog.cards;
      [["single-card",1],["compare-cards",2],["deck-profile",8],["matchup-a",8],["matchup-b",8]].forEach(([name,limit]) => createPicker(document.querySelector(`[data-picker="${name}"]`), limit));
      [
        ["deck-profile", "deckLoadoutDetails"],
        ["matchup-a", "matchupALoadoutDetails"],
        ["matchup-b", "matchupBLoadoutDetails"]
      ].forEach(([pickerName, elementId]) => {
        document.querySelector(`[data-picker="${pickerName}"]`).addEventListener("pickerchange", () => renderLoadoutDetails(pickerName, elementId));
      });
      applyDataMode();
      await loadSnapshot(); const requested = location.hash.slice(1); activateView(["home","qa","rankings","card","compare","deck","matchup","meta"].includes(requested) ? requested : "home");
    }
    initialize().catch(error => { document.getElementById("headerState").textContent = error.message; });
  </script>
</body>
</html>
"""
