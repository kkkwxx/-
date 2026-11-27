import { useEffect, useState } from "react";
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import {
  CloudDownloadOutlined,
  DeleteOutlined,
  PlusOutlined,
  ReloadOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import { apiFetch, type Schemas } from "../../api/client";
import {
  HEALTH_STATUS_META,
  SOURCE_LEVEL_META,
  formatTime,
} from "../../utils/labels";

export default function SourcesPage() {
  const [sources, setSources] = useState<Schemas["SourceListItem"][]>([]);
  const [unknowns, setUnknowns] = useState<Schemas["UnknownDomainItem"][]>([]);
  const [rules, setRules] = useState<Schemas["DomainRuleItem"][]>([]);
  const [loading, setLoading] = useState(false);

  const [importOpen, setImportOpen] = useState(false);
  const [importForm] = Form.useForm();

  const loadAll = () => {
    setLoading(true);
    Promise.all([
      apiFetch<Schemas["SourceListResponse"]>("/api/sources?page_size=50"),
      apiFetch<Schemas["UnknownDomainListResponse"]>("/api/source-domain-rules/unknown?page_size=50"),
      apiFetch<Schemas["DomainRuleListResponse"]>("/api/source-domain-rules?page_size=100"),
    ])
      .then(([s, u, r]) => {
        setSources(s.items);
        setUnknowns(u.items);
        setRules(r.items);
      })
      .catch((err) => message.error(err instanceof Error ? err.message : "加载失败"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadAll();
  }, []);

  const handleToggle = async (id: number, enabled: boolean) => {
    try {
      await apiFetch(`/api/sources/${id}/toggle`, {
        method: "POST",
        body: JSON.stringify({ enabled }),
      });
      message.success(enabled ? "已启用信源" : "已停用信源");
      loadAll();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "切换失败");
    }
  };

  const handlePoll = async (id: number) => {
    try {
      const res = await apiFetch<Schemas["PollResponse"]>(`/api/sources/${id}/poll`, {
        method: "POST",
      });
      message.success(`已投递采集任务 #${res.task_run_id}，等待 Worker 执行`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "投递失败");
    }
  };

  const handleResetHealth = async (id: number) => {
    try {
      await apiFetch(`/api/sources/${id}/reset-health`, { method: "POST" });
      message.success("已重置健康度");
      loadAll();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "重置失败");
    }
  };

  const handleDeleteSource = async (id: number) => {
    try {
      await apiFetch(`/api/sources/${id}`, { method: "DELETE" });
      message.success("信源已删除");
      loadAll();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "删除信源失败");
    }
  };

  const handleDeleteUnknownDomain = async (domain: string) => {
    try {
      await apiFetch(`/api/source-domain-rules/unknown/${encodeURIComponent(domain)}`, {
        method: "DELETE",
      });
      message.success(`已忽略并移除未收录域名 ${domain}`);
      loadAll();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  const handleDeleteDomainRule = async (id: number) => {
    try {
      await apiFetch(`/api/source-domain-rules/${id}`, { method: "DELETE" });
      message.success("域名规则已停用删除（软删除，保留历史溯源）");
      loadAll();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "操作失败");
    }
  };

  const handleEnableDomainRule = async (id: number) => {
    try {
      await apiFetch(`/api/source-domain-rules/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: true }),
      });
      message.success("域名规则已重新启用");
      loadAll();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "启用失败");
    }
  };

  const handleClassifyDomain = async (domain: string, level: "A" | "B" | "C") => {
    try {
      await apiFetch("/api/source-domain-rules", {
        method: "POST",
        body: JSON.stringify({ domain, level, note: "运营台一键归类" }),
      });
      message.success(`已将 ${domain} 归类为 ${level} 级，建议点击重算存量文章等级`);
      loadAll();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "归类失败");
    }
  };

  const handleRecalc = async () => {
    try {
      const res = await apiFetch<Schemas["RecalcResponse"]>("/api/sources/recalc-levels", {
        method: "POST",
      });
      message.success(`已投递重算任务 #${res.task_run_id}`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "触发重算失败");
    }
  };

  const handleManualImport = async () => {
    try {
      const values = await importForm.validateFields();
      const res = await apiFetch<Schemas["ManualImportResponse"]>("/api/raw-items", {
        method: "POST",
        body: JSON.stringify({
          url: values.url,
          title: values.title,
          summary: values.summary,
          source_code: "manual",
          note: values.note || "后台手工导入线索",
        }),
      });
      message.success(
        `导入成功！文章 #${res.article_id} 定级为 ${res.source_level}（${res.source_level_reason}）`
      );
      setImportOpen(false);
      importForm.resetFields();
    } catch (err) {
      if (err && typeof err === "object" && "errorFields" in err) return;
      message.error(err instanceof Error ? err.message : "手工导入失败");
    }
  };

  return (
    <Card
      title="信源管理、域名规则与手工线索导入"
      extra={
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setImportOpen(true)}>
            手工导入线索 URL
          </Button>
          <Button icon={<SyncOutlined />} onClick={handleRecalc}>
            重算未发布文章等级
          </Button>
          <Button icon={<ReloadOutlined />} onClick={loadAll} loading={loading}>
            刷新
          </Button>
        </Space>
      }
    >
      <Tabs
        items={[
          {
            key: "sources",
            label: `信源列表（${sources.length}）`,
            children: (
              <Table
                size="small"
                rowKey="id"
                loading={loading}
                dataSource={sources}
                pagination={{ pageSize: 25 }}
                columns={[
                  {
                    title: "信源",
                    render: (_, r) => (
                      <div>
                        <strong>{r.name}</strong>{" "}
                        <Typography.Text type="secondary">({r.code})</Typography.Text>
                      </div>
                    ),
                  },
                  { title: "类型", dataIndex: "type", width: 100 },
                  {
                    title: "源级",
                    dataIndex: "level",
                    width: 90,
                    render: (v: string) => (
                      <Tag color={SOURCE_LEVEL_META[v]?.color}>{SOURCE_LEVEL_META[v]?.label || v}</Tag>
                    ),
                  },
                  {
                    title: "健康状态",
                    dataIndex: "health_status",
                    width: 110,
                    render: (v: string) => (
                      <Tag color={HEALTH_STATUS_META[v]?.color}>
                        {HEALTH_STATUS_META[v]?.label || v}
                      </Tag>
                    ),
                  },
                  { title: "周期(分)", dataIndex: "poll_interval_min", width: 85 },
                  {
                    title: "最近采集",
                    dataIndex: "last_poll_at",
                    width: 120,
                    render: (v: string | null) => formatTime(v),
                  },
                  {
                    title: "启用",
                    width: 80,
                    render: (_, r) => (
                      <Switch
                        size="small"
                        checked={r.enabled}
                        disabled={r.code === "manual"}
                        onChange={(checked) => handleToggle(r.id, checked)}
                      />
                    ),
                  },
                  {
                    title: "操作",
                    width: 220,
                    render: (_, r) => (
                      <Space size={4}>
                        <Button
                          size="small"
                          icon={<CloudDownloadOutlined />}
                          disabled={!r.enabled || r.type === "manual"}
                          onClick={() => handlePoll(r.id)}
                        >
                          立即采
                        </Button>
                        {r.health_status !== "ok" && r.health_status !== "disabled" && (
                          <Button size="small" onClick={() => handleResetHealth(r.id)}>
                            重置
                          </Button>
                        )}
                        <Popconfirm
                          title={`确认删除信源「${r.name}」吗？`}
                          description="若该源已有采回数据，系统会拒绝删除并提示改用停用。"
                          okText="确认删除"
                          cancelText="取消"
                          okButtonProps={{ danger: true }}
                          disabled={r.code === "manual"}
                          onConfirm={() => handleDeleteSource(r.id)}
                        >
                          <Button
                            danger
                            size="small"
                            icon={<DeleteOutlined />}
                            disabled={r.code === "manual"}
                          >
                            删除
                          </Button>
                        </Popconfirm>
                      </Space>
                    ),
                  },
                ]}
              />
            ),
          },
          {
            key: "unknown",
            label: `未收录域名待归类（${unknowns.length}）`,
            children: (
              <Table
                size="small"
                rowKey="domain"
                loading={loading}
                dataSource={unknowns}
                pagination={{ pageSize: 20 }}
                columns={[
                  { title: "注册域", dataIndex: "domain" },
                  { title: "命中次数", dataIndex: "hit_count", width: 90 },
                  { title: "样本标题", dataIndex: "sample_title", ellipsis: true },
                  {
                    title: "一键归类 / 清理",
                    width: 310,
                    render: (_, r) => (
                      <Space size={4}>
                        <Button size="small" onClick={() => handleClassifyDomain(r.domain, "A")}>
                          归为 A 级
                        </Button>
                        <Button size="small" onClick={() => handleClassifyDomain(r.domain, "B")}>
                          归为 B 级
                        </Button>
                        <Button size="small" onClick={() => handleClassifyDomain(r.domain, "C")}>
                          归为 C 级
                        </Button>
                        <Popconfirm
                          title={`确认忽略并移除未收录域名 ${r.domain} 吗？`}
                          okText="确认删除"
                          cancelText="取消"
                          okButtonProps={{ danger: true }}
                          onConfirm={() => handleDeleteUnknownDomain(r.domain)}
                        >
                          <Button danger size="small" icon={<DeleteOutlined />}>
                            删除
                          </Button>
                        </Popconfirm>
                      </Space>
                    ),
                  },
                ]}
              />
            ),
          },
          {
            key: "rules",
            label: `已配置域名规则（${rules.length}）`,
            children: (
              <Table
                size="small"
                rowKey="id"
                dataSource={rules}
                pagination={{ pageSize: 30 }}
                columns={[
                  { title: "注册域", dataIndex: "domain" },
                  {
                    title: "等级",
                    dataIndex: "level",
                    render: (v: string) => (
                      <Tag color={SOURCE_LEVEL_META[v]?.color}>{SOURCE_LEVEL_META[v]?.label || v}</Tag>
                    ),
                  },
                  {
                    title: "启用",
                    dataIndex: "enabled",
                    render: (v: boolean) => <Tag color={v ? "green" : "default"}>{v ? "生效" : "停用"}</Tag>,
                  },
                  { title: "备注", dataIndex: "note" },
                  {
                    title: "操作",
                    width: 120,
                    render: (_, r) =>
                      r.enabled ? (
                        <Popconfirm
                          title={`确认停用删除域名规则 ${r.domain} 吗？`}
                          description="采用软删除（置为停用），以保留历史文章的来源定级依据。"
                          okText="确认删除"
                          cancelText="取消"
                          okButtonProps={{ danger: true }}
                          onConfirm={() => handleDeleteDomainRule(r.id)}
                        >
                          <Button danger size="small" icon={<DeleteOutlined />}>
                            删除
                          </Button>
                        </Popconfirm>
                      ) : (
                        <Button size="small" onClick={() => handleEnableDomainRule(r.id)}>
                          重新启用
                        </Button>
                      ),
                  },
                ]}
              />
            ),
          },
        ]}
      />

      <Modal
        open={importOpen}
        title="手工导入线索 URL（覆盖 C 级线索与演示兜底）"
        okText="确认导入"
        cancelText="取消"
        onCancel={() => setImportOpen(false)}
        onOk={handleManualImport}
      >
        <Form form={importForm} layout="vertical">
          <Form.Item
            label="原始链接 URL"
            name="url"
            rules={[{ required: true, message: "请输入原始新闻链接 URL" }]}
          >
            <Input placeholder="https://..." />
          </Form.Item>
          <Form.Item
            label="原始标题"
            name="title"
            rules={[{ required: true, message: "请输入原始标题" }]}
          >
            <Input placeholder="例如：泰国灵异综艺《UNSEEN：通灵者之战》中国道士参赛引发讨论" />
          </Form.Item>
          <Form.Item label="原文摘要" name="summary">
            <Input.TextArea rows={3} placeholder="粘贴新闻摘要，供后续事件判定与 AI 加工使用" />
          </Form.Item>
          <Form.Item label="导入备注" name="note">
            <Input placeholder="可选备注" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
