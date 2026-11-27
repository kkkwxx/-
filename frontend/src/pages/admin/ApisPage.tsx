import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  ApiOutlined,
  CheckCircleOutlined,
  CloudDownloadOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { apiFetch, type Schemas } from "../../api/client";
import {
  HEALTH_STATUS_META,
  SOURCE_LEVEL_META,
  formatTime,
} from "../../utils/labels";

const ROLE_META: Record<string, { label: string; color: string }> = {
  primary: { label: "当前主用", color: "green" },
  fallback: { label: "备用降级", color: "orange" },
  standby: { label: "待命保存", color: "default" },
};

const VENDOR_LABELS: Record<string, string> = {
  dashscope: "阿里百炼",
  deepseek: "DeepSeek",
  siliconflow: "硅基流动",
  openai: "OpenAI",
  gemini: "Gemini 兼容",
  ollama: "本地 Ollama",
  custom: "自定义网关",
};

export default function ApisPage() {
  // ---- Tab 1: AI API 提供商 ----
  const [providers, setProviders] = useState<Schemas["AiProviderOut"][]>([]);
  const [presets, setPresets] = useState<Schemas["AiProviderPresetOut"][]>([]);
  const [envFallbackMask, setEnvFallbackMask] = useState<string | null>(null);
  const [envFallbackConfigured, setEnvFallbackConfigured] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [testingId, setTestingId] = useState<number | null>(null);

  const [aiModalOpen, setAiModalOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<Schemas["AiProviderOut"] | null>(null);
  const [aiSaving, setAiSaving] = useState(false);
  const [aiForm] = Form.useForm();

  // ---- Tab 2: 新闻 API 信源（复用 source 表 type=api） ----
  const [newsApis, setNewsApis] = useState<Schemas["SourceListItem"][]>([]);
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsModalOpen, setNewsModalOpen] = useState(false);
  const [editingNews, setEditingNews] = useState<Schemas["SourceListItem"] | null>(null);
  const [newsSaving, setNewsSaving] = useState(false);
  const [newsForm] = Form.useForm();

  const loadProviders = useCallback(() => {
    setAiLoading(true);
    apiFetch<Schemas["AiProviderListResponse"]>("/api/ai-providers")
      .then((res) => {
        setProviders(res.items ?? []);
        setPresets(res.presets ?? []);
        setEnvFallbackConfigured(Boolean(res.env_fallback_configured));
        setEnvFallbackMask(res.env_fallback_mask ?? null);
      })
      .catch((err) => message.error(err instanceof Error ? err.message : "加载 AI 提供商失败"))
      .finally(() => setAiLoading(false));
  }, []);

  const loadNewsApis = useCallback(() => {
    setNewsLoading(true);
    apiFetch<Schemas["SourceListResponse"]>("/api/sources?type=api&page_size=50")
      .then((res) => setNewsApis(res.items ?? []))
      .catch((err) => message.error(err instanceof Error ? err.message : "加载新闻 API 信源失败"))
      .finally(() => setNewsLoading(false));
  }, []);

  useEffect(() => {
    loadProviders();
    loadNewsApis();
  }, [loadProviders, loadNewsApis]);

  // ---- AI 提供商动作 ----
  const openCreateProvider = () => {
    setEditingProvider(null);
    const defaultPreset = presets.find((p) => p.vendor === "deepseek") || presets[0];
    aiForm.resetFields();
    aiForm.setFieldsValue({
      vendor: defaultPreset?.vendor || "deepseek",
      base_url: defaultPreset?.base_url || "",
      model_classify: defaultPreset?.default_model_classify || "",
      model_generate: defaultPreset?.default_model_generate || "",
      daily_token_budget: defaultPreset?.default_budget ?? 50000,
      role: providers.some((p) => p.role === "primary") ? "fallback" : "primary",
      enabled: true,
      clear_api_key: false,
    });
    setAiModalOpen(true);
  };

  const openEditProvider = (row: Schemas["AiProviderOut"]) => {
    setEditingProvider(row);
    aiForm.resetFields();
    aiForm.setFieldsValue({
      code: row.code,
      name: row.name,
      vendor: row.vendor,
      base_url: row.base_url,
      api_key: "",
      clear_api_key: false,
      model_classify: row.model_classify,
      model_generate: row.model_generate,
      daily_token_budget: row.daily_token_budget,
      role: row.role,
      enabled: row.enabled,
      note: row.note || "",
    });
    setAiModalOpen(true);
  };

  const applyPreset = (vendor: string) => {
    const preset = presets.find((p) => p.vendor === vendor);
    if (!preset) return;
    aiForm.setFieldsValue({
      vendor: preset.vendor,
      base_url: preset.base_url,
      model_classify: preset.default_model_classify,
      model_generate: preset.default_model_generate,
      daily_token_budget: preset.default_budget,
      name: aiForm.getFieldValue("name") || preset.label,
    });
  };

  const handleSaveProvider = async () => {
    try {
      const values = await aiForm.validateFields();
      setAiSaving(true);
      if (editingProvider) {
        await apiFetch(`/api/ai-providers/${editingProvider.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            name: values.name,
            vendor: values.vendor,
            base_url: values.base_url,
            api_key: values.api_key || undefined,
            clear_api_key: Boolean(values.clear_api_key),
            model_classify: values.model_classify,
            model_generate: values.model_generate,
            daily_token_budget: values.daily_token_budget,
            role: values.role,
            enabled: values.enabled,
            note: values.note,
          }),
        });
        message.success("AI 提供商配置已更新");
      } else {
        await apiFetch("/api/ai-providers", {
          method: "POST",
          body: JSON.stringify({
            code: values.code,
            name: values.name,
            vendor: values.vendor,
            base_url: values.base_url,
            api_key: values.api_key || undefined,
            model_classify: values.model_classify,
            model_generate: values.model_generate,
            daily_token_budget: values.daily_token_budget,
            role: values.role,
            enabled: values.enabled,
            note: values.note,
          }),
        });
        message.success("AI 提供商已新增（密钥已加密存储）");
      }
      setAiModalOpen(false);
      loadProviders();
    } catch (err) {
      if (err && typeof err === "object" && "errorFields" in err) return;
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setAiSaving(false);
    }
  };

  const handleSetRole = async (id: number, role: "primary" | "fallback" | "standby") => {
    try {
      await apiFetch(`/api/ai-providers/${id}/role`, {
        method: "POST",
        body: JSON.stringify({ role }),
      });
      message.success(`已切换为「${ROLE_META[role]?.label || role}」`);
      loadProviders();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "切换角色失败");
    }
  };

  const handleToggleProvider = async (id: number, enabled: boolean) => {
    try {
      await apiFetch(`/api/ai-providers/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      message.success(enabled ? "已启用该提供商" : "已停用该提供商");
      loadProviders();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "更新失败");
    }
  };

  const handleTestProvider = async (id: number) => {
    setTestingId(id);
    try {
      const res = await apiFetch<Schemas["AiProviderTestResponse"]>(
        `/api/ai-providers/${id}/test`,
        { method: "POST" }
      );
      if (res.ok) {
        message.success(`连通测试通过（${res.latency_ms}ms · ${res.method}）：${res.detail}`);
      } else {
        message.error(`连通测试失败（${res.latency_ms}ms）：${res.detail}`);
      }
      loadProviders();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "测试失败");
    } finally {
      setTestingId(null);
    }
  };

  const handleDeleteProvider = async (id: number) => {
    try {
      await apiFetch(`/api/ai-providers/${id}`, { method: "DELETE" });
      message.success("AI 提供商已删除");
      loadProviders();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  // ---- 新闻 API 信源动作 ----
  const openCreateNewsApi = () => {
    setEditingNews(null);
    newsForm.resetFields();
    newsForm.setFieldsValue({
      level: "B",
      poll_interval_min: 60,
      enabled: true,
      clear_api_key: false,
    });
    setNewsModalOpen(true);
  };

  const openEditNewsApi = (row: Schemas["SourceListItem"]) => {
    setEditingNews(row);
    newsForm.resetFields();
    newsForm.setFieldsValue({
      code: row.code,
      name: row.name,
      level: row.level,
      url: row.url || "",
      poll_interval_min: row.poll_interval_min,
      api_key: "",
      api_key_env: row.api_key_env || "",
      clear_api_key: false,
      enabled: row.enabled,
      note: row.note || "",
    });
    setNewsModalOpen(true);
  };

  const handleSaveNewsApi = async () => {
    try {
      const values = await newsForm.validateFields();
      setNewsSaving(true);
      if (editingNews) {
        await apiFetch(`/api/sources/${editingNews.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            name: values.name,
            level: values.level,
            url: values.url,
            poll_interval_min: values.poll_interval_min,
            api_key: values.api_key || undefined,
            clear_api_key: Boolean(values.clear_api_key),
            api_key_env: values.api_key_env || null,
            enabled: values.enabled,
            note: values.note || null,
          }),
        });
        message.success("新闻 API 信源已更新");
      } else {
        await apiFetch("/api/sources", {
          method: "POST",
          body: JSON.stringify({
            code: values.code,
            name: values.name,
            type: "api",
            level: values.level,
            url: values.url,
            poll_interval_min: values.poll_interval_min,
            api_key: values.api_key || undefined,
            api_key_env: values.api_key_env || null,
            enabled: values.enabled,
            note: values.note || null,
          }),
        });
        message.success("新闻 API 信源已新增");
      }
      setNewsModalOpen(false);
      loadNewsApis();
    } catch (err) {
      if (err && typeof err === "object" && "errorFields" in err) return;
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setNewsSaving(false);
    }
  };

  const handleToggleNewsApi = async (id: number, enabled: boolean) => {
    try {
      await apiFetch(`/api/sources/${id}/toggle`, {
        method: "POST",
        body: JSON.stringify({ enabled }),
      });
      message.success(enabled ? "已启用 API 信源" : "已停用 API 信源");
      loadNewsApis();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "切换失败");
    }
  };

  const handlePollNewsApi = async (id: number) => {
    try {
      const res = await apiFetch<Schemas["PollResponse"]>(`/api/sources/${id}/poll`, {
        method: "POST",
      });
      message.success(`已投递采集任务 #${res.task_run_id}`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "投递失败");
    }
  };

  const handleDeleteNewsApi = async (id: number) => {
    try {
      await apiFetch(`/api/sources/${id}`, { method: "DELETE" });
      message.success("新闻 API 信源已删除");
      loadNewsApis();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  const primaryProvider = providers.find((p) => p.role === "primary" && p.enabled);
  const fallbackProvider = providers.find((p) => p.role === "fallback" && p.enabled);

  return (
    <Card
      title={
        <Space>
          <ApiOutlined />
          <span>API 管理（AI 模型提供商 & 新闻 API 信源）</span>
        </Space>
      }
    >
      <Tabs
        items={[
          {
            key: "ai",
            label: `AI API 提供商管理（${providers.length}）`,
            children: (
              <Space direction="vertical" size={14} style={{ width: "100%" }}>
                <Alert
                  type={primaryProvider ? "info" : "warning"}
                  showIcon
                  icon={<SafetyCertificateOutlined />}
                  message={
                    <Space wrap size={12}>
                      <span>
                        <strong>当前主用：</strong>
                        {primaryProvider
                          ? `${primaryProvider.name}（${primaryProvider.model_classify} / ${primaryProvider.model_generate}）`
                          : envFallbackConfigured
                            ? `未配置数据库主用，自动回退 .env 百炼配置（${envFallbackMask || "已配置"}）`
                            : "尚未启用任何 AI 提供商"}
                      </span>
                      <span>
                        <strong>备用降级：</strong>
                        {fallbackProvider
                          ? `${fallbackProvider.name}（主用超预算或 429/5xx 时自动接管）`
                          : "未设置（建议配置 1 个备用源防限流中断）"}
                      </span>
                    </Space>
                  }
                  description="所有 API Key 均采用服务端对称认证加密落库，界面与审计日志仅展示脱敏掩码，绝不明文回传。"
                />

                <Space>
                  <Button type="primary" icon={<PlusOutlined />} onClick={openCreateProvider}>
                    新增 AI 提供商
                  </Button>
                  <Button icon={<ReloadOutlined />} onClick={loadProviders} loading={aiLoading}>
                    刷新
                  </Button>
                </Space>

                <Table
                  size="small"
                  rowKey="id"
                  loading={aiLoading}
                  dataSource={providers}
                  pagination={false}
                  scroll={{ x: 1120 }}
                  columns={[
                    {
                      title: "提供商 / 接口地址",
                      width: 260,
                      render: (_, r) => (
                        <div>
                          <Space size={6}>
                            <strong>{r.name}</strong>
                            <Tag>{VENDOR_LABELS[r.vendor] || r.vendor}</Tag>
                          </Space>
                          <div style={{ fontSize: 12, color: "#64748b", wordBreak: "break-all" }}>
                            {r.base_url}
                          </div>
                        </div>
                      ),
                    },
                    {
                      title: "调度角色",
                      dataIndex: "role",
                      width: 105,
                      render: (v: string) => {
                        const m = ROLE_META[v] || { label: v, color: "default" };
                        return <Tag color={m.color}>{m.label}</Tag>;
                      },
                    },
                    {
                      title: "API Key（脱敏）",
                      width: 150,
                      render: (_, r) =>
                        r.has_api_key ? (
                          <Tag color="geekblue">{r.api_key_mask || "已加密配置"}</Tag>
                        ) : r.vendor === "ollama" ? (
                          <Tag>免 Key</Tag>
                        ) : (
                          <Tag color="red">未配置</Tag>
                        ),
                    },
                    {
                      title: "分类 / 生成模型",
                      width: 185,
                      render: (_, r) => (
                        <div style={{ fontSize: 12 }}>
                          <div>分类：{r.model_classify}</div>
                          <div>生成：{r.model_generate}</div>
                        </div>
                      ),
                    },
                    {
                      title: "今日 Token / 日预算",
                      width: 165,
                      render: (_, r) => {
                        const used = r.today_tokens ?? 0;
                        const budget = r.daily_token_budget ?? 0;
                        if (budget <= 0) {
                          return <span style={{ fontSize: 12 }}>{used.toLocaleString()} / 不限</span>;
                        }
                        const pct = Math.min(100, Math.round((used / budget) * 100));
                        return (
                          <div>
                            <div style={{ fontSize: 12 }}>
                              {used.toLocaleString()} / {budget.toLocaleString()}
                            </div>
                            <Progress
                              percent={pct}
                              size="small"
                              status={pct >= 100 ? "exception" : "active"}
                              showInfo={false}
                            />
                          </div>
                        );
                      },
                    },
                    {
                      title: "连通测试",
                      width: 125,
                      render: (_, r) =>
                        r.last_test_status === "ok" ? (
                          <Tooltip title={`最近测试：${formatTime(r.last_test_at)}`}>
                            <Tag color="green" icon={<CheckCircleOutlined />}>
                              连通正常
                            </Tag>
                          </Tooltip>
                        ) : r.last_test_status === "failed" ? (
                          <Tooltip title={r.last_test_error || "连通失败"}>
                            <Tag color="red">测试失败</Tag>
                          </Tooltip>
                        ) : (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            未测试
                          </Typography.Text>
                        ),
                    },
                    {
                      title: "启用",
                      width: 70,
                      render: (_, r) => (
                        <Switch
                          size="small"
                          checked={r.enabled}
                          onChange={(checked) => handleToggleProvider(r.id, checked)}
                        />
                      ),
                    },
                    {
                      title: "操作",
                      width: 280,
                      fixed: "right",
                      render: (_, r) => (
                        <Space size={4} wrap>
                          {r.role !== "primary" && (
                            <Button
                              size="small"
                              type="primary"
                              ghost
                              onClick={() => handleSetRole(r.id, "primary")}
                            >
                              设主用
                            </Button>
                          )}
                          {r.role !== "fallback" && (
                            <Button
                              size="small"
                              onClick={() => handleSetRole(r.id, "fallback")}
                            >
                              设备用
                            </Button>
                          )}
                          <Button
                            size="small"
                            icon={<ThunderboltOutlined />}
                            loading={testingId === r.id}
                            onClick={() => handleTestProvider(r.id)}
                          >
                            测试
                          </Button>
                          <Button
                            size="small"
                            icon={<EditOutlined />}
                            onClick={() => openEditProvider(r)}
                          >
                            编辑
                          </Button>
                          <Popconfirm
                            title={`确认删除 AI 提供商「${r.name}」吗？`}
                            okText="确认删除"
                            cancelText="取消"
                            okButtonProps={{ danger: true }}
                            onConfirm={() => handleDeleteProvider(r.id)}
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
              </Space>
            ),
          },
          {
            key: "news_api",
            label: `新闻 API 信源管理（${newsApis.length}）`,
            children: (
              <Space direction="vertical" size={14} style={{ width: "100%" }}>
                <Alert
                  type="info"
                  showIcon
                  message="与「信源与域名规则」共用底层 source 表（筛选 type=api），支持直接加密录入新闻 API Key 或绑定环境变量名。"
                />
                <Space>
                  <Button type="primary" icon={<PlusOutlined />} onClick={openCreateNewsApi}>
                    新增新闻 API 信源
                  </Button>
                  <Button icon={<ReloadOutlined />} onClick={loadNewsApis} loading={newsLoading}>
                    刷新
                  </Button>
                </Space>

                <Table
                  size="small"
                  rowKey="id"
                  loading={newsLoading}
                  dataSource={newsApis}
                  pagination={{ pageSize: 20 }}
                  scroll={{ x: 980 }}
                  columns={[
                    {
                      title: "API 信源 / URL",
                      width: 280,
                      render: (_, r) => (
                        <div>
                          <strong>{r.name}</strong>{" "}
                          <Typography.Text type="secondary">({r.code})</Typography.Text>
                          <div style={{ fontSize: 12, color: "#64748b", wordBreak: "break-all" }}>
                            {r.url || "—"}
                          </div>
                        </div>
                      ),
                    },
                    {
                      title: "源级",
                      dataIndex: "level",
                      width: 85,
                      render: (v: string) => (
                        <Tag color={SOURCE_LEVEL_META[v]?.color}>
                          {SOURCE_LEVEL_META[v]?.label || v}
                        </Tag>
                      ),
                    },
                    {
                      title: "API Key 状态",
                      width: 170,
                      render: (_, r) =>
                        r.api_key_mask ? (
                          <Tag color="geekblue">{r.api_key_mask}</Tag>
                        ) : r.api_key_env ? (
                          <Tag color={r.key_configured ? "green" : "orange"}>
                            ENV: {r.api_key_env}
                          </Tag>
                        ) : (
                          <Tag>免 Key</Tag>
                        ),
                    },
                    {
                      title: "健康状态",
                      dataIndex: "health_status",
                      width: 100,
                      render: (v: string) => (
                        <Tag color={HEALTH_STATUS_META[v]?.color}>
                          {HEALTH_STATUS_META[v]?.label || v}
                        </Tag>
                      ),
                    },
                    { title: "周期(分)", dataIndex: "poll_interval_min", width: 85 },
                    {
                      title: "启用",
                      width: 70,
                      render: (_, r) => (
                        <Switch
                          size="small"
                          checked={r.enabled}
                          onChange={(checked) => handleToggleNewsApi(r.id, checked)}
                        />
                      ),
                    },
                    {
                      title: "操作",
                      width: 220,
                      fixed: "right",
                      render: (_, r) => (
                        <Space size={4}>
                          <Button
                            size="small"
                            icon={<CloudDownloadOutlined />}
                            disabled={!r.enabled}
                            onClick={() => handlePollNewsApi(r.id)}
                          >
                            立即采
                          </Button>
                          <Button
                            size="small"
                            icon={<EditOutlined />}
                            onClick={() => openEditNewsApi(r)}
                          >
                            编辑
                          </Button>
                          <Popconfirm
                            title={`确认删除新闻 API 信源「${r.name}」吗？`}
                            description="若已有采回数据将禁止删除并提示改用停用。"
                            okText="确认删除"
                            cancelText="取消"
                            okButtonProps={{ danger: true }}
                            onConfirm={() => handleDeleteNewsApi(r.id)}
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
              </Space>
            ),
          },
        ]}
      />

      {/* 新增/编辑 AI 提供商弹窗 */}
      <Modal
        open={aiModalOpen}
        title={editingProvider ? `编辑 AI 提供商：${editingProvider.name}` : "新增 AI API 提供商"}
        okText="保存配置"
        cancelText="取消"
        confirmLoading={aiSaving}
        width={620}
        onCancel={() => setAiModalOpen(false)}
        onOk={handleSaveProvider}
      >
        <Form form={aiForm} layout="vertical">
          <Form.Item label="厂商预设模板" name="vendor" rules={[{ required: true }]}>
            <Select
              onChange={applyPreset}
              options={presets.map((p) => ({
                value: p.vendor,
                label: `${p.label} — ${p.note}`,
              }))}
            />
          </Form.Item>
          <Space style={{ width: "100%", display: "flex" }} size={12}>
            <Form.Item
              label="唯一编码 (code)"
              name="code"
              style={{ flex: 1 }}
              rules={[{ required: true, message: "如 deepseek_backup" }]}
            >
              <Input disabled={Boolean(editingProvider)} placeholder="如 deepseek_backup" />
            </Form.Item>
            <Form.Item
              label="显示名称"
              name="name"
              style={{ flex: 1 }}
              rules={[{ required: true, message: "请输入提供商名称" }]}
            >
              <Input placeholder="如 DeepSeek 备用线路" />
            </Form.Item>
          </Space>
          <Form.Item
            label="OpenAI 兼容 Base URL"
            name="base_url"
            rules={[{ required: true, message: "请输入 https://... Base URL" }]}
          >
            <Input placeholder="https://api.deepseek.com/v1" />
          </Form.Item>
          <Form.Item
            label={
              editingProvider
                ? `API Key（留空保持原密钥 ${editingProvider.api_key_mask || "不变"}）`
                : "API Key（将加密存储）"
            }
            name="api_key"
          >
            <Input.Password
              autoComplete="new-password"
              placeholder={editingProvider ? "不修改请留空" : "输入 sk-...（本地 Ollama 可留空）"}
            />
          </Form.Item>
          {editingProvider && (
            <Form.Item name="clear_api_key" valuePropName="checked" style={{ marginTop: -10 }}>
              <Checkbox>清空已保存的 API Key（仅本地免 Key 网关勾选）</Checkbox>
            </Form.Item>
          )}
          <Space style={{ width: "100%", display: "flex" }} size={12}>
            <Form.Item
              label="事件分类模型（低成本）"
              name="model_classify"
              style={{ flex: 1 }}
              rules={[{ required: true, message: "请输入分类模型名" }]}
            >
              <Input placeholder="qwen-flash / deepseek-chat" />
            </Form.Item>
            <Form.Item
              label="悬念文案生成模型"
              name="model_generate"
              style={{ flex: 1 }}
              rules={[{ required: true, message: "请输入生成模型名" }]}
            >
              <Input placeholder="qwen-plus / deepseek-chat" />
            </Form.Item>
          </Space>
          <Space style={{ width: "100%", display: "flex" }} size={12}>
            <Form.Item
              label="日 Token 预算（0=不限）"
              name="daily_token_budget"
              style={{ flex: 1 }}
              rules={[{ required: true }]}
            >
              <InputNumber min={0} step={10000} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item label="调度角色" name="role" style={{ flex: 1 }} rules={[{ required: true }]}>
              <Select
                options={[
                  { value: "primary", label: "当前主用（优先调用）" },
                  { value: "fallback", label: "备用降级（主用失败/超预算接管）" },
                  { value: "standby", label: "待命保存（暂不参与调度）" },
                ]}
              />
            </Form.Item>
            <Form.Item label="启用" name="enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Form.Item label="备注" name="note">
            <Input.TextArea rows={2} placeholder="例如：限额较低，用作备用线路" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 新增/编辑新闻 API 信源弹窗 */}
      <Modal
        open={newsModalOpen}
        title={editingNews ? `编辑新闻 API 信源：${editingNews.name}` : "新增新闻 API 信源"}
        okText="保存信源"
        cancelText="取消"
        confirmLoading={newsSaving}
        width={580}
        onCancel={() => setNewsModalOpen(false)}
        onOk={handleSaveNewsApi}
      >
        <Form form={newsForm} layout="vertical">
          <Space style={{ width: "100%", display: "flex" }} size={12}>
            <Form.Item
              label="唯一编码 (code)"
              name="code"
              style={{ flex: 1 }}
              rules={[{ required: true, message: "如 spaceflight_news_api" }]}
            >
              <Input disabled={Boolean(editingNews)} placeholder="如 gnews_space" />
            </Form.Item>
            <Form.Item
              label="信源名称"
              name="name"
              style={{ flex: 1 }}
              rules={[{ required: true, message: "请输入信源名称" }]}
            >
              <Input placeholder="如 NASA JPL CNEOS" />
            </Form.Item>
          </Space>
          <Form.Item
            label="接口 URL"
            name="url"
            rules={[{ required: true, message: "请输入 https:// 接口地址" }]}
          >
            <Input placeholder="https://ssd-api.jpl.nasa.gov/cad.api?..." />
          </Form.Item>
          <Space style={{ width: "100%", display: "flex" }} size={12}>
            <Form.Item label="默认源级" name="level" style={{ flex: 1 }} rules={[{ required: true }]}>
              <Select
                options={[
                  { value: "A", label: "A 级（权威官方/科研机构）" },
                  { value: "B", label: "B 级（可核验科技媒体）" },
                  { value: "C", label: "C 级（社区/聚合线索）" },
                ]}
              />
            </Form.Item>
            <Form.Item
              label="轮询周期（分钟）"
              name="poll_interval_min"
              style={{ flex: 1 }}
              rules={[{ required: true }]}
            >
              <InputNumber min={5} max={1440} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item label="启用" name="enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Form.Item
            label={
              editingNews
                ? `直接录入 API Key（留空保持 ${editingNews.api_key_mask || "原值"}）`
                : "直接录入 API Key（可选，加密落库）"
            }
            name="api_key"
          >
            <Input.Password autoComplete="new-password" placeholder="免 Key 接口可留空" />
          </Form.Item>
          {editingNews && (
            <Form.Item name="clear_api_key" valuePropName="checked" style={{ marginTop: -10 }}>
              <Checkbox>清空已加密保存的 API Key</Checkbox>
            </Form.Item>
          )}
          <Form.Item label="或绑定环境变量名（可选）" name="api_key_env">
            <Input placeholder="如 GNEWS_API_KEY" />
          </Form.Item>
          <Form.Item label="备注" name="note">
            <Input.TextArea rows={2} placeholder="记录接口限频或使用说明" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
