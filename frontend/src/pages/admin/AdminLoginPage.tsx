import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button, Card, ConfigProvider, Form, Input, Space, Spin, Typography, message } from "antd";
import zhCN from "antd/locale/zh_CN";
import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { apiFetch, type Schemas } from "../../api/client";

function safeRedirectTarget(raw: string | null): string {
  if (!raw || !raw.startsWith("/admin") || raw.startsWith("/admin/login") || raw.startsWith("//")) {
    return "/admin/review";
  }
  return raw;
}

export default function AdminLoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const redirectTo = safeRedirectTarget(searchParams.get("redirect"));

  const [checking, setChecking] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiFetch<Schemas["MeResponse"]>("/api/auth/me")
      .then((res) => {
        if (!cancelled && res.username) {
          navigate(redirectTo, { replace: true });
        }
      })
      .catch(() => {
        // 未登录，留在登录页
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [navigate, redirectTo]);

  const handleLogin = async (values: { username: string; password: string }) => {
    setSubmitting(true);
    try {
      await apiFetch<Schemas["LoginResponse"]>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username: values.username.trim(),
          password: values.password,
        }),
      });
      message.success("登录成功");
      navigate(redirectTo, { replace: true });
    } catch (err) {
      message.error(err instanceof Error ? err.message : "账号或密码错误");
    } finally {
      setSubmitting(false);
    }
  };

  if (checking) {
    return (
      <div style={{ display: "grid", placeItems: "center", minHeight: "100vh", background: "#0f172a" }}>
        <Space direction="vertical" align="center">
          <Spin size="large" />
          <Typography.Text style={{ color: "#cbd5e1" }}>正在校验登录状态...</Typography.Text>
        </Space>
      </div>
    );
  }

  return (
    <ConfigProvider locale={zhCN}>
      <div
        style={{
          display: "grid",
          placeItems: "center",
          minHeight: "100vh",
          background: "linear-gradient(135deg, #0c0c0f 0%, #161829 55%, #1e1b4b 100%)",
          padding: 16,
        }}
      >
        <Card
          style={{ width: 390, borderRadius: 12, boxShadow: "0 18px 48px rgba(0,0,0,0.45)" }}
          title="神秘新闻 · 后台管理系统登录"
        >
          <Form layout="vertical" onFinish={handleLogin} autoComplete="off">
            <Form.Item
              label="管理员账号"
              name="username"
              rules={[{ required: true, message: "请输入管理员账号" }]}
            >
              <Input
                prefix={<UserOutlined style={{ color: "#94a3b8" }} />}
                placeholder="请输入账号"
                size="large"
                autoComplete="username"
              />
            </Form.Item>
            <Form.Item
              label="登录密码"
              name="password"
              rules={[{ required: true, message: "请输入登录密码" }]}
            >
              <Input.Password
                prefix={<LockOutlined style={{ color: "#94a3b8" }} />}
                placeholder="请输入密码"
                size="large"
                autoComplete="current-password"
              />
            </Form.Item>
            <Button type="primary" htmlType="submit" size="large" block loading={submitting}>
              登录后台
            </Button>
          </Form>
        </Card>
      </div>
    </ConfigProvider>
  );
}
