import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Col,
  Progress,
  Row,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  AuditOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudDownloadOutlined,
  ReloadOutlined,
  ScheduleOutlined,
} from "@ant-design/icons";
import { apiFetch, type Schemas } from "../../api/client";
import RiskPolicyCard from "../../components/RiskPolicyCard";
import VerticalColumnChart from "../../components/VerticalColumnChart";
import {
  ARTICLE_STATUS_META,
  EVENT_TYPE_META,
  HEALTH_STATUS_META,
} from "../../utils/labels";

export default function DashboardPage() {
  const navigate = useNavigate();
  const [data, setData] = useState<Schemas["DashboardOverview"] | null>(null);
  const [schedule, setSchedule] = useState<Schemas["ScheduleStateOut"] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    Promise.all([
      apiFetch<Schemas["DashboardOverview"]>("/api/dashboard/overview"),
      apiFetch<Schemas["ScheduleStateOut"]>("/api/schedule").catch(() => null),
    ])
      .then(([overview, sched]) => {
        setData(overview);
        setSchedule(sched);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "加载监控数据失败"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  if (loading && !data) {
    return <Spin size="large" />;
  }

  const p = data?.pipeline_counts || {};
  const tokenPercent = Math.round((data?.token_usage_ratio || 0) * 100);
  const tokenBudget = data?.daily_token_budget || 200000;

  const kpis = [
    {
      key: "candidate",
      label: "常规候选池待审",
      value: p.candidate || 0,
      suffix: "",
      sub: "等待人工过审",
      tone: "blue",
      icon: <AuditOutlined />,
      to: "/admin/review?status=candidate",
    },
    {
      key: "pending",
      label: "待定复核池",
      value: p.pending || 0,
      suffix: "",
      sub: "C 级 / 灵异 / 高风险",
      tone: "amber",
      icon: <ClockCircleOutlined />,
      to: "/admin/review?status=pending",
    },
    {
      key: "approved",
      label: "已审待发布",
      value: p.approved || 0,
      suffix: `/ ${data?.active_published_total || 0} 在线`,
      sub: "在线栏目已发布内容",
      tone: "green",
      icon: <CheckCircleOutlined />,
      to: "/admin/review?status=approved",
    },
    {
      key: "ingested",
      label: "今日采集",
      value: data?.today_ingested || 0,
      suffix: "",
      sub: `未收录域名 ${data?.unknown_domain_count || 0} 个`,
      tone: "slate",
      icon: <CloudDownloadOutlined />,
      to: "/admin/sources",
    },
  ];

  const eventCounts = Object.entries(EVENT_TYPE_META).map(([key, meta]) => ({
    key,
    label: meta.short,
    value: data?.event_type_counts?.[key] || 0,
  }));
  const eventTotal = eventCounts.reduce((sum, item) => sum + item.value, 0) || 1;
  const eventBars = eventCounts.map((item) => ({
    ...item,
    extra: { label: "占比", value: `${Math.round((item.value / eventTotal) * 100)}%` },
  }));

  return (
    <Space direction="vertical" size={14} style={{ width: "100%" }}>
      <div className="mn-page-head">
        <span className="mn-faint">指标来自最近一次加载，可随时手动刷新</span>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
          刷新指标
        </Button>
      </div>

      {schedule && (
        <Alert
          type={schedule.is_running_now ? "warning" : schedule.mode === "manual" ? "info" : "success"}
          showIcon
          icon={<ScheduleOutlined />}
          message={
            <Space wrap size={10}>
              <strong>采集与 AI 调度状态：</strong>
              <span>{schedule.status_summary}</span>
              <Tag color="blue">下次计划：{schedule.next_run_hint}</Tag>
            </Space>
          }
          action={
            <Button size="small" type="primary" onClick={() => navigate("/admin/schedule")}>
              立即获取 / 调时间
            </Button>
          }
        />
      )}

      {error && (
        <Alert
          type="error"
          showIcon
          message={`监控指标加载失败：${error}`}
          action={
            <Button size="small" onClick={load}>
              重试
            </Button>
          }
        />
      )}

      <div className="mn-kpi-grid">
        {kpis.map((kpi) => (
          <button
            type="button"
            key={kpi.key}
            className={`mn-kpi mn-kpi--${kpi.tone}`}
            onClick={() => navigate(kpi.to)}
          >
            <span className={`mn-kpi-icon mn-kpi-icon--${kpi.tone}`}>{kpi.icon}</span>
            <span className="mn-kpi-body">
              <span className="mn-kpi-label">{kpi.label}</span>
              <span className="mn-kpi-value">
                {kpi.value}
                {kpi.suffix && <em>{kpi.suffix}</em>}
              </span>
              <span className="mn-kpi-sub">{kpi.sub}</span>
            </span>
          </button>
        ))}
      </div>

      <Row gutter={[14, 14]}>
        <Col xs={24} xl={15}>
          <Card styles={{ body: { padding: 16 } }}>
            <VerticalColumnChart
              data={eventBars}
              title="五大栏目分布"
              subtitle="按事件聚类统计 · 点击条目可选中"
              ranges={[{ key: "all", label: "当前全量" }]}
              range="all"
              legend="条数"
              valueFormatter={(v) => `${v} 条`}
            />
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card title="今日 AI Token 预算" extra={<span className="mn-faint">超预算自动熔断</span>}>
            <div className="mn-meter-value">
              <strong>{(data?.today_tokens || 0).toLocaleString()}</strong>
              <span> / {tokenBudget.toLocaleString()} tokens</span>
            </div>
            <Progress
              percent={tokenPercent}
              showInfo={false}
              status={tokenPercent >= 90 ? "exception" : "normal"}
              strokeColor="var(--mn-primary)"
              trailColor="var(--mn-accent-soft)"
            />
            <div className="mn-meter-foot">
              <span>已用 {tokenPercent}%</span>
              <span>剩余 {Math.max(0, 100 - tokenPercent)}%</span>
            </div>
            <div className="mn-divider" />
            <div className="mn-legend-title">全链路状态分布</div>
            <Space wrap size={8}>
              {Object.entries(ARTICLE_STATUS_META).map(([key, meta]) => (
                <Tag key={key} color={meta.color}>
                  {meta.label}：{p[key] || 0}
                </Tag>
              ))}
            </Space>
          </Card>
        </Col>
      </Row>

      <Row gutter={[14, 14]}>
        <Col xs={24} xl={15}>
          <Card
            title="异常信源告警"
            extra={
              <Button size="small" onClick={() => navigate("/admin/sources")}>
                前往处理
              </Button>
            }
          >
            {!data?.alert_sources?.length ? (
              <Alert type="success" message="当前所有已启用信源健康状态正常" showIcon />
            ) : (
              <Table
                size="small"
                rowKey="id"
                pagination={false}
                dataSource={data.alert_sources}
                columns={[
                  { title: "信源", dataIndex: "name" },
                  {
                    title: "状态",
                    dataIndex: "health_status",
                    render: (v: string) => {
                      const m = HEALTH_STATUS_META[v] || { label: v, color: "default" };
                      return <Tag color={m.color}>{m.label}</Tag>;
                    },
                  },
                  { title: "连续失败", dataIndex: "consecutive_failures" },
                  {
                    title: "最近错误",
                    dataIndex: "last_error",
                    ellipsis: true,
                    render: (v: string | null) => v || "—",
                  },
                ]}
              />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card title="今日发布概况">
            <div className="mn-stat-lines">
              <div className="mn-stat-line">
                <span>今日发布</span>
                <strong>{data?.today_published || 0} 条</strong>
              </div>
              <div className="mn-stat-line">
                <span>在线内容总数</span>
                <strong>{data?.active_published_total || 0} 条</strong>
              </div>
              <div className="mn-stat-line">
                <span>信源健康</span>
                <strong>
                  {(data?.source_health_counts?.ok || 0)} 正常 /{" "}
                  {(data?.source_health_counts?.disabled || 0)} 停用
                </strong>
              </div>
              <div className="mn-stat-line">
                <span>规则拦截</span>
                <strong>{p.rule_rejected || 0} 条</strong>
              </div>
            </div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              数据取自 dashboard/overview，刷新后即时更新。
            </Typography.Text>
          </Card>
        </Col>
      </Row>

      <RiskPolicyCard compact />
    </Space>
  );
}
