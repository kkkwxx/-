import { useEffect, useState } from "react";
import {
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  message,
} from "antd";
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { apiFetch, type Schemas } from "../../api/client";
import RiskPolicyCard from "../../components/RiskPolicyCard";
import { KEYWORD_TYPE_LABELS } from "../../utils/labels";

export default function RulesPage() {
  const [items, setItems] = useState<Schemas["KeywordRuleOut"][]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const load = () => {
    setLoading(true);
    apiFetch<Schemas["KeywordRuleListResponse"]>("/api/keyword-rules")
      .then((res) => setItems(res.items))
      .catch((err) => message.error(err instanceof Error ? err.message : "加载规则失败"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const handleToggle = async (id: number, enabled: boolean) => {
    try {
      await apiFetch(`/api/keyword-rules/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      message.success(enabled ? "规则已启用" : "规则已停用");
      load();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "更新失败");
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await apiFetch(`/api/keyword-rules/${id}`, { method: "DELETE" });
      message.success("关键词规则已删除");
      load();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  const handleCreate = async () => {
    try {
      const values = await form.validateFields();
      await apiFetch("/api/keyword-rules", {
        method: "POST",
        body: JSON.stringify(values),
      });
      message.success("规则已新增");
      setOpen(false);
      form.resetFields();
      load();
    } catch (err) {
      if (err && typeof err === "object" && "errorFields" in err) return;
      message.error(err instanceof Error ? err.message : "新增失败");
    }
  };

  return (
    <div>
      <RiskPolicyCard />
      <Card
        title="关键词过滤与锚定规则（硬排除 / 软扣分 / 锚定词 / 信号词 / 事件动词）"
      extra={
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            新增规则
          </Button>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
        </Space>
      }
    >
      <Table
        size="small"
        rowKey="id"
        loading={loading}
        dataSource={items}
        pagination={{ pageSize: 30 }}
        columns={[
          {
            title: "规则类型",
            dataIndex: "type",
            width: 180,
            render: (v: string) => (
              <Tag color={v === "exclude_hard" ? "red" : v === "exclude_soft" ? "orange" : "blue"}>
                {KEYWORD_TYPE_LABELS[v] || v}
              </Tag>
            ),
          },
          { title: "分类", dataIndex: "category", width: 150 },
          {
            title: "正则 Pattern",
            dataIndex: "pattern",
            render: (v: string) => <code style={{ wordBreak: "break-all" }}>{v}</code>,
          },
          { title: "权重", dataIndex: "weight", width: 70 },
          { title: "说明", dataIndex: "note", ellipsis: true },
          {
            title: "启用",
            width: 80,
            render: (_, r) => (
              <Switch
                size="small"
                checked={r.enabled}
                onChange={(checked) => handleToggle(r.id, checked)}
              />
            ),
          },
          {
            title: "操作",
            width: 90,
            render: (_, r) => (
              <Popconfirm
                title={`确认删除规则「${r.category}」吗？`}
                okText="确认删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => handleDelete(r.id)}
              >
                <Button danger size="small" icon={<DeleteOutlined />}>
                  删除
                </Button>
              </Popconfirm>
            ),
          },
        ]}
      />

      <Modal
        open={open}
        title="新增关键词规则"
        okText="确认新增"
        cancelText="取消"
        onCancel={() => setOpen(false)}
        onOk={handleCreate}
      >
        <Form form={form} layout="vertical" initialValues={{ scope: "title_summary", weight: 0 }}>
          <Form.Item label="类型" name="type" rules={[{ required: true, message: "请选择规则类型" }]}>
            <Select
              options={Object.entries(KEYWORD_TYPE_LABELS).map(([v, label]) => ({
                value: v,
                label,
              }))}
            />
          </Form.Item>
          <Form.Item label="分类名" name="category" rules={[{ required: true, message: "请输入分类名" }]}>
            <Input placeholder="如：divination / neo / signal" />
          </Form.Item>
          <Form.Item label="正则 Pattern" name="pattern" rules={[{ required: true, message: "请输入正则表达式" }]}>
            <Input.TextArea rows={2} placeholder="如：虫洞|平行宇宙" />
          </Form.Item>
          <Form.Item label="权重（软扣分填负数，信号词填正数）" name="weight">
            <InputNumber style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item label="说明" name="note">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
    </div>
  );
}
