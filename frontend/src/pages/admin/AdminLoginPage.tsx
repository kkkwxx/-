import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button, ConfigProvider, Form, Input, Spin, message } from "antd";
import zhCN from "antd/locale/zh_CN";
import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { apiFetch, type Schemas } from "../../api/client";
import adminTheme from "../../theme";

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
      <div className="mn-boot">
        <div style={{ textAlign: "center" }}>
          <Spin size="large" />
          <div className="mn-faint" style={{ marginTop: 12 }}>
            正在校验登录状态...
          </div>
        </div>
      </div>
    );
  }

  return (
    <ConfigProvider locale={zhCN} theme={adminTheme}>
      <div className="mn-login">
        <div className="mn-login-panel">
          <div className="mn-login-card">
            <div className="mn-login-head">
              <span className="mn-login-mark">神</span>
              <div>
                <div className="mn-login-title">神秘新闻 · 运营后台</div>
                <div className="mn-login-subtitle">全人工审核发布闭环</div>
              </div>
            </div>
            <div className="mn-login-body">
              <Form layout="vertical" onFinish={handleLogin} autoComplete="off">
                <Form.Item
                  label="管理员账号"
                  name="username"
                  rules={[{ required: true, message: "请输入管理员账号" }]}
                >
                  <Input
                    prefix={<UserOutlined style={{ color: "var(--mn-text-tertiary)" }} />}
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
                    prefix={<LockOutlined style={{ color: "var(--mn-text-tertiary)" }} />}
                    placeholder="请输入密码"
                    size="large"
                    autoComplete="current-password"
                  />
                </Form.Item>
                <Button
                  type="primary"
                  htmlType="submit"
                  size="large"
                  block
                  loading={submitting}
                  style={{ marginTop: 4 }}
                >
                  登录后台
                </Button>
              </Form>
            </div>
          </div>
          <div className="mn-login-foot">
            仅限运营人员使用，登录行为将被记录
            <br />
            访问公开栏目页：/column
          </div>
        </div>
      </div>
    </ConfigProvider>
  );
}
