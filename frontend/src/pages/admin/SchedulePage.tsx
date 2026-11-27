import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Divider,
  Input,
  InputNumber,
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  ClockCircleOutlined,
  CloudDownloadOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SaveOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { apiFetch, buildQuery, type Schemas } from "../../api/client";
import { formatTime } from "../../utils/labels";

const WEEKDAY_OPTIONS = [
  { label: "周一", value: 1 },
  { label: "周二", value: 2 },
  { label: "周三", value: 3 },
  { label: "周四", value: 4 },
  { label: "周五", value: 5 },
  { label: "周六", value: 6 },
  { label: "周日", value: 7 },
];

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, h) => {
  const hh = String(h).padStart(2, "0");
  return [
    { label: `${hh}:00`, value: `${hh}:00` },
    { label: `${hh}:30`, value: `${hh}:30` },
  ];
}).flat();

const QUICK_SLOTS = ["08:00", "09:00", "12:00", "14:00", "18:00", "20:00", "22:00"];

const TASK_TYPE_LABELS: Record<string, { label: string; color: string }> = {
  pipeline_run: { label: "全链路 / 调度运行", color: "purple" },
  poll: { label: "单源采集", color: "blue" },
  fetch_full_text: { label: "正文补全", color: "cyan" },
  preprocess: { label: "AI 判定预处理", color: "geekblue" },
  generate_content: { label: "AI 文案生成", color: "magenta" },
  recalc_levels: { label: "重算来源等级", color: "default" },
};

const STAGE_LABELS: Record<string, string> = {
  queued: "排队启动中",
  starting: "初始化中",
  crawling: "正在采集新闻信源...",
  full_text: "正在补全原文正文...",
  preprocess: "正在执行 AI 事件判定与去重...",
  generate: "正在执行 AI 悬念文案生成...",
  done: "已完成",
  failed: "执行失败",
};

function formatRunStats(taskType: string, stats: Record<string, unknown> | undefined): string {
  if (!stats) return "—";
  if (taskType === "pipeline_run") {
    const parts: string[] = [];
    if (stats.action) {
      const actMap: Record<string, string> = {
        full: "全链路",
        crawl_only: "仅采集",
        ai_only: "仅AI",
      };
      parts.push(actMap[String(stats.action)] || String(stats.action));
    }
    if (stats.current_stage && stats.current_stage !== "done") {
      parts.push(STAGE_LABELS[String(stats.current_stage)] || String(stats.current_stage));
    }
    if (stats.sources_polled) parts.push(`采源 ${stats.sources_polled} 个`);
    if (stats.inserted) parts.push(`入库 ${stats.inserted} 条`);
    if (stats.preprocessed) parts.push(`判定 ${stats.preprocessed} 条`);
    if (stats.generated) parts.push(`生成 ${stats.generated} 条`);
    if (stats.candidate) parts.push(`进候选 ${stats.candidate} 条`);
    if (stats.tokens) parts.push(`耗 Token ${Number(stats.tokens).toLocaleString()}`);
    return parts.join(" · ") || "—";
  }
  const parts: string[] = [];
  if (stats.fetched !== undefined) parts.push(`抓取 ${stats.fetched}`);
  if (stats.inserted !== undefined) parts.push(`入库 ${stats.inserted}`);
  if (stats.scanned !== undefined) parts.push(`扫描 ${stats.scanned}`);
  if (stats.candidate !== undefined) parts.push(`进候选 ${stats.candidate}`);
  if (stats.tokens !== undefined && Number(stats.tokens) > 0) {
    parts.push(`Token ${Number(stats.tokens).toLocaleString()}`);
  }
  return parts.join(" · ") || "—";
}

function formatDuration(started?: string | null, finished?: string | null): string {
  if (!started) return "—";
  const s = new Date(started).getTime();
  const e = finished ? new Date(finished).getTime() : Date.now();
  const sec = Math.max(0, Math.round((e - s) / 1000));
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

export default function SchedulePage() {
  const [state, setState] = useState<Schemas["ScheduleStateOut"] | null>(null);
  const [runs, setRuns] = useState<Schemas["ScheduleTaskRunItem"][]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [runTypeFilter, setRunTypeFilter] = useState<string | undefined>(undefined);

  // 表单草稿态
  const [mode, setMode] = useState<Schemas["ScheduleStateOut"]["mode"]>("manual");
  const [crawlEnabled, setCrawlEnabled] = useState(true);
  const [aiEnabled, setAiEnabled] = useState(true);
  const [weekdays, setWeekdays] = useState<number[]>([1, 2, 3, 4, 5, 6, 7]);
  const [windowStart, setWindowStart] = useState("09:00");
  const [windowEnd, setWindowEnd] = useState("22:00");
  const [fixedTimes, setFixedTimes] = useState<string[]>(["09:00", "14:00", "20:00"]);
  const [newSlotInput, setNewSlotInput] = useState("");
  const [maxAiPerRun, setMaxAiPerRun] = useState(5);

  // 「现在立即获取」控制台状态
  const [runNowMaxItems, setRunNowMaxItems] = useState(5);
  const [includePending, setIncludePending] = useState(false);

  const syncDraft = (s: Schemas["ScheduleStateOut"]) => {
    setMode(s.mode);
    setCrawlEnabled(s.crawl_enabled);
    setAiEnabled(s.ai_enabled);
    setWeekdays(s.weekdays);
    setWindowStart(s.window_start);
    setWindowEnd(s.window_end);
    setFixedTimes(s.fixed_times);
    setMaxAiPerRun(s.max_ai_items_per_run);
  };

  const loadAll = useCallback(
    (syncForm = false) => {
      setLoading(true);
      Promise.all([
        apiFetch<Schemas["ScheduleStateOut"]>("/api/schedule"),
        apiFetch<Schemas["ScheduleTaskRunListResponse"]>(
          `/api/schedule/runs${buildQuery({ task_type: runTypeFilter, limit: 25 })}`
        ),
      ])
        .then(([s, r]) => {
          setState(s);
          setRuns(r.items ?? []);
          if (syncForm) {
            syncDraft(s);
            setRunNowMaxItems(s.max_ai_items_per_run || 5);
          }
        })
        .catch((err) => message.error(err instanceof Error ? err.message : "加载调度状态失败"))
        .finally(() => setLoading(false));
    },
    [runTypeFilter]
  );

  useEffect(() => {
    loadAll(true);
  }, [loadAll]);

  // 有任务运行时每 3 秒自动刷新进度与流水
  useEffect(() => {
    if (!state?.is_running_now) return;
    const timer = window.setInterval(() => {
      loadAll(false);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [state?.is_running_now, loadAll]);

  const handleSaveSchedule = async () => {
    if (weekdays.length === 0) {
      message.warning("请至少选择 1 个允许运行的星期");
      return;
    }
    if (mode === "fixed_times" && fixedTimes.length === 0) {
      message.warning("固定时间点模式下，请至少添加 1 个触发时刻（如 09:00）");
      return;
    }
    setSaving(true);
    try {
      const updated = await apiFetch<Schemas["ScheduleStateOut"]>("/api/schedule", {
        method: "PUT",
        body: JSON.stringify({
          mode,
          crawl_enabled: crawlEnabled,
          ai_enabled: aiEnabled,
          weekdays,
          window_start: windowStart,
          window_end: windowEnd,
          fixed_times: fixedTimes,
          max_ai_items_per_run: maxAiPerRun,
        }),
      });
      setState(updated);
      syncDraft(updated);
      message.success("调度时间策略已保存并立即生效");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存调度策略失败");
    } finally {
      setSaving(false);
    }
  };

  const handleRunNow = async (action: NonNullable<Schemas["RunNowRequest"]["action"]>) => {
    setTriggering(true);
    try {
      const res = await apiFetch<Schemas["RunNowResponse"]>("/api/schedule/run-now", {
        method: "POST",
        body: JSON.stringify({
          action,
          max_items: runNowMaxItems,
          include_pending: includePending,
        }),
      });
      message.success(res.message);
      loadAll(false);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "启动任务失败");
    } finally {
      setTriggering(false);
    }
  };

  const addFixedSlot = (rawSlot: string) => {
    const cleaned = rawSlot.trim();
    if (!/^([01]\d|2[0-3]):([0-5]\d)$/.test(cleaned)) {
      message.warning("时间格式必须为 24 小时制 HH:MM（例如 09:30 或 20:00）");
      return;
    }
    if (!fixedTimes.includes(cleaned)) {
      setFixedTimes([...fixedTimes, cleaned].sort());
    }
    setNewSlotInput("");
  };

  const q = state?.queue_counts || {};

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <div className="mn-page-head">
        <span className="mn-faint">
          时间策略、手动触发与运行记录
        </span>
        <Button icon={<ReloadOutlined />} onClick={() => loadAll(false)} loading={loading}>
          刷新状态
        </Button>
      </div>

      {/* 顶部实时状态横幅 */}
      {state && (
        <Alert
          type={state.is_running_now ? "warning" : state.mode === "manual" ? "info" : "success"}
          showIcon
          message={
            <Space wrap size={10}>
              <strong>{state.status_summary}</strong>
              {state.is_running_now && (
                <Tag color="processing">
                  运行中任务 #{state.running_task_id}（
                  {STAGE_LABELS[state.running_stage || ""] || state.running_stage}）
                </Tag>
              )}
            </Space>
          }
          description={
            <Space wrap size={16} style={{ marginTop: 4 }}>
              <span>服务器本地时间：{state.server_local_time} ({state.timezone})</span>
              <span>
                此刻自动采集：
                <Tag color={state.crawl_active_now ? "green" : "default"}>
                  {state.crawl_active_now ? "允许" : "静默"}
                </Tag>
              </span>
              <span>
                此刻自动 AI：
                <Tag color={state.ai_active_now ? "green" : "default"}>
                  {state.ai_active_now ? "允许" : "静默"}
                </Tag>
              </span>
              <span>下次计划：{state.next_run_hint}</span>
            </Space>
          }
        />
      )}

      {/* 队列即时概览 */}
      <Row gutter={12}>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="待判定入库（ingested）" value={q.ingested || 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="待 AI 文案生成（judged）" value={q.judged || 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic
              title="待定复核池（pending）"
              value={q.pending || 0}
              valueStyle={{ color: "var(--mn-warning)" }}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic
              title="常规候选池（candidate）"
              value={q.candidate || 0}
              valueStyle={{ color: "var(--mn-primary)" }}
            />
          </Card>
        </Col>
      </Row>

      {/* 卡片 1：现在立即获取控制台 */}
      <Card
        title={
          <Space>
            <ThunderboltOutlined style={{ color: "var(--mn-warning)" }} />
            <span>现在立即获取（按需手动执行，无需单独开启 Worker 窗口）</span>
          </Space>
        }
      >
        <Space direction="vertical" size={14} style={{ width: "100%" }}>
          <Space wrap size={18} align="center">
            <Space>
              <Typography.Text strong>本次 AI 最多加工条数：</Typography.Text>
              <InputNumber
                min={1}
                max={50}
                value={runNowMaxItems}
                onChange={(v) => setRunNowMaxItems(Number(v) || 5)}
                style={{ width: 90 }}
              />
              <Typography.Text type="secondary">（防一次性烧光 API 额度）</Typography.Text>
            </Space>
            <Checkbox
              checked={includePending}
              onChange={(e) => setIncludePending(e.target.checked)}
            >
              若新抓条目不足，顺带从「待定复核池」拉取未加工文章补足
            </Checkbox>
          </Space>

          <Space wrap size={12}>
            <Button
              type="primary"
              size="large"
              icon={<PlayCircleOutlined />}
              loading={triggering || Boolean(state?.is_running_now)}
              onClick={() => handleRunNow("full")}
            >
              立即全链路获取（采新闻 + AI 加工限 {runNowMaxItems} 条）
            </Button>
            <Button
              size="large"
              icon={<CloudDownloadOutlined />}
              loading={triggering || Boolean(state?.is_running_now)}
              onClick={() => handleRunNow("crawl_only")}
            >
              仅立即采集新闻与补正文（0 AI Token）
            </Button>
            <Button
              size="large"
              icon={<RobotOutlined />}
              loading={triggering || Boolean(state?.is_running_now)}
              onClick={() => handleRunNow("ai_only")}
            >
              仅立即跑 AI 加工（限 {runNowMaxItems} 条）
            </Button>
          </Space>
        </Space>
      </Card>

      {/* 卡片 2：自动获取时间策略配置 */}
      <Card
        title={
          <Space>
            <ClockCircleOutlined style={{ color: "var(--mn-primary)" }} />
            <span>自动获取时间策略（控制什么时间跑、什么时间静默不烧额度）</span>
          </Space>
        }
        extra={
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            onClick={handleSaveSchedule}
          >
            保存调度策略
          </Button>
        }
      >
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <div>
            <Typography.Text strong style={{ display: "block", marginBottom: 8 }}>
              1. 选择运行模式
            </Typography.Text>
            <Radio.Group
              value={mode}
              onChange={(e) => setMode(e.target.value)}
              optionType="button"
              buttonStyle="solid"
            >
              <Radio.Button value="manual">纯手动模式（默认静默，绝不偷跑）</Radio.Button>
              <Radio.Button value="window">活跃时间窗（窗内轮询，窗外静默）</Radio.Button>
              <Radio.Button value="fixed_times">固定时间点（到点自动执行一轮）</Radio.Button>
            </Radio.Group>
          </div>

          {mode !== "manual" && (
            <>
              <Divider style={{ margin: "4px 0" }} />
              <Space wrap size={28}>
                <Space>
                  <Switch checked={crawlEnabled} onChange={setCrawlEnabled} />
                  <span>
                    <strong>允许自动采集新闻</strong>（抓取 RSS/API 与补正文，0 AI 消耗）
                  </span>
                </Space>
                <Space>
                  <Switch checked={aiEnabled} onChange={setAiEnabled} />
                  <span>
                    <strong>允许自动 AI 判定与生成</strong>（消耗当前主用 AI 额度）
                  </span>
                </Space>
                <Space>
                  <span>每轮自动 AI 上限：</span>
                  <InputNumber
                    min={1}
                    max={100}
                    value={maxAiPerRun}
                    onChange={(v) => setMaxAiPerRun(Number(v) || 5)}
                    style={{ width: 85 }}
                  />
                  <span>条</span>
                </Space>
              </Space>

              <div>
                <Typography.Text strong style={{ display: "block", marginBottom: 8 }}>
                  2. 允许运行的星期
                </Typography.Text>
                <Checkbox.Group
                  options={WEEKDAY_OPTIONS}
                  value={weekdays}
                  onChange={(vals) => setWeekdays(vals.map(Number))}
                />
              </div>

              {mode === "window" && (
                <div>
                  <Typography.Text strong style={{ display: "block", marginBottom: 8 }}>
                    3. 活跃时间段（支持跨午夜时段，例如 22:00 至 06:00；时间段外自动静默）
                  </Typography.Text>
                  <Space align="center">
                    <span>开始时间</span>
                    <Select
                      value={windowStart}
                      onChange={setWindowStart}
                      options={HOUR_OPTIONS}
                      style={{ width: 110 }}
                    />
                    <span>至</span>
                    <Select
                      value={windowEnd}
                      onChange={setWindowEnd}
                      options={HOUR_OPTIONS}
                      style={{ width: 110 }}
                    />
                  </Space>
                </div>
              )}

              {mode === "fixed_times" && (
                <div>
                  <Typography.Text strong style={{ display: "block", marginBottom: 8 }}>
                    3. 固定触发时间点（每天到达以下时刻各自动执行一轮）
                  </Typography.Text>
                  <Space wrap style={{ marginBottom: 10 }}>
                    {fixedTimes.length === 0 ? (
                      <Typography.Text type="secondary">尚未添加时间点</Typography.Text>
                    ) : (
                      fixedTimes.map((t) => (
                        <Tag
                          key={t}
                          color="blue"
                          closable
                          onClose={(e) => {
                            e.preventDefault();
                            setFixedTimes(fixedTimes.filter((x) => x !== t));
                          }}
                          style={{ fontSize: 13, padding: "4px 10px" }}
                        >
                          {t}
                        </Tag>
                      ))
                    )}
                  </Space>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                    <Input
                      placeholder="输入 HH:MM 如 10:30"
                      value={newSlotInput}
                      onChange={(e) => setNewSlotInput(e.target.value)}
                      onPressEnter={() => addFixedSlot(newSlotInput)}
                      style={{ width: 170 }}
                    />
                    <Button icon={<PlusOutlined />} onClick={() => addFixedSlot(newSlotInput)}>
                      添加时间点
                    </Button>
                    <Typography.Text type="secondary" style={{ marginLeft: 6 }}>
                      快捷添加：
                    </Typography.Text>
                    {QUICK_SLOTS.map((s) => (
                      <Button
                        key={s}
                        size="small"
                        type={fixedTimes.includes(s) ? "primary" : "default"}
                        ghost={fixedTimes.includes(s)}
                        onClick={() => addFixedSlot(s)}
                      >
                        {s}
                      </Button>
                    ))}
                  </div>
                </div>
              )}

              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                提示：自动定时（活跃时间窗 / 固定时间点）由后台常驻进程 <code>python -m app.worker</code> 实时读取生效；
                即使未启动 Worker，你依然可以随时使用上方「现在立即获取」按钮按需执行。
              </Typography.Text>
            </>
          )}
        </Space>
      </Card>

      {/* 卡片 3：最近执行流水 */}
      <Card
        title="最近采集与 AI 执行流水（task_run）"
        extra={
          <Select
            allowClear
            placeholder="按任务类型筛选"
            style={{ width: 170 }}
            value={runTypeFilter}
            onChange={setRunTypeFilter}
            options={Object.entries(TASK_TYPE_LABELS).map(([k, m]) => ({
              value: k,
              label: m.label,
            }))}
          />
        }
      >
        <Table
          size="small"
          rowKey="id"
          loading={loading}
          dataSource={runs}
          pagination={{ pageSize: 10 }}
          scroll={{ x: 900 }}
          columns={[
            { title: "ID", dataIndex: "id", width: 70 },
            {
              title: "任务类型",
              dataIndex: "task_type",
              width: 150,
              render: (v: string) => {
                const m = TASK_TYPE_LABELS[v] || { label: v, color: "default" };
                return <Tag color={m.color}>{m.label}</Tag>;
              },
            },
            {
              title: "触发方式",
              dataIndex: "triggered_by",
              width: 90,
              render: (v: string) => (
                <Tag color={v === "manual" ? "orange" : "default"}>
                  {v === "manual" ? "手动" : v === "scheduler" ? "定时" : v}
                </Tag>
              ),
            },
            {
              title: "状态",
              dataIndex: "status",
              width: 95,
              render: (v: string) => (
                <Tag
                  color={
                    v === "success"
                      ? "green"
                      : v === "running"
                        ? "processing"
                        : v === "failed"
                          ? "red"
                          : "default"
                  }
                >
                  {v === "success"
                    ? "成功"
                    : v === "running"
                      ? "运行中"
                      : v === "failed"
                        ? "失败"
                        : "排队"}
                </Tag>
              ),
            },
            {
              title: "开始时间",
              dataIndex: "started_at",
              width: 125,
              render: (v: string | null, r) => formatTime(v || r.created_at),
            },
            {
              title: "耗时",
              width: 80,
              render: (_, r) => formatDuration(r.started_at, r.finished_at),
            },
            {
              title: "执行结果 / 消耗摘要",
              render: (_, r) => (
                <div>
                  <span>{formatRunStats(r.task_type, r.stats_json)}</span>
                  {r.error && (
                    <div style={{ color: "var(--mn-danger)", fontSize: 12, marginTop: 2 }}>
                      错误：{r.error}
                    </div>
                  )}
                </div>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
