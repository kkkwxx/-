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
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import { ReloadOutlined, ScheduleOutlined } from "@ant-design/icons";
import { apiFetch, type Schemas } from "../../api/client";
import RiskPolicyCard from "../../components/RiskPolicyCard";
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

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          运营监控总览
        </Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
          刷新指标
        </Button>
      </div>

      <RiskPolicyCard compact />

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

      <Row gutter={16}>
        <Col xs={12} md={6}>
          <Card hoverable onClick={() => navigate("/admin/review?status=candidate")}>
            <Statistic
              title="常规候选池待审"
              value={p.candidate || 0}
              valueStyle={{ color: "#1677ff" }}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card hoverable onClick={() => navigate("/admin/review?status=pending")}>
            <Statistic
              title="待定复核池（C级/灵异/高风险）"
              value={p.pending || 0}
              valueStyle={{ color: "#fa8c16" }}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card hoverable onClick={() => navigate("/admin/review?status=approved")}>
            <Statistic
              title="已审待发布 / 在线栏目数"
              value={p.approved || 0}
              suffix={`/ ${data?.active_published_total || 0}`}
              valueStyle={{ color: "#52c41a" }}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card hoverable onClick={() => navigate("/admin/sources")}>
            <Statistic
              title="今日采集 / 未收录域名"
              value={data?.today_ingested || 0}
              suffix={`/ ${data?.unknown_domain_count || 0}`}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col xs={24} lg={12}>
          <Card title="今日 AI Token 预算消耗">
            <Progress
              percent={tokenPercent}
              status={tokenPercent >= 90 ? "exception" : "active"}
            />
            <Typography.Text type="secondary">
              已消耗 {(data?.today_tokens || 0).toLocaleString()} /{" "}
              {(data?.daily_token_budget || 200000).toLocaleString()} tokens（超预算自动熔断）
            </Typography.Text>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="全链路状态分布">
            <Space wrap>
              {Object.entries(ARTICLE_STATUS_META).map(([key, meta]) => (
                <Tag key={key} color={meta.color} style={{ padding: "4px 10px", fontSize: 13 }}>
                  {meta.label}：{p[key] || 0}
                </Tag>
              ))}
            </Space>
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col xs={24} lg={10}>
          <Card title="五大栏目分布">
            <Space direction="vertical" style={{ width: "100%" }}>
              {Object.entries(EVENT_TYPE_META).map(([k, meta]) => (
                <div
                  key={k}
                  style={{ display: "flex", justifyContent: "space-between", padding: "4px 0" }}
                >
                  <span>
                    <Tag color={meta.color}>{meta.label}</Tag>
                  </span>
                  <strong>{data?.event_type_counts?.[k] || 0} 条</strong>
                </div>
              ))}
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={14}>
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
      </Row>
    </Space>
  );
}
