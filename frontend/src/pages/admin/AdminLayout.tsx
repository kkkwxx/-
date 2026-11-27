import { Suspense, useEffect, useState } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Button,
  ConfigProvider,
  Layout,
  Menu,
  Space,
  Spin,
  Typography,
  message,
} from "antd";
import zhCN from "antd/locale/zh_CN";
import {
  ApiOutlined,
  AuditOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  FilterOutlined,
  LogoutOutlined,
  MobileOutlined,
  ScheduleOutlined,
} from "@ant-design/icons";
import { apiFetch, type Schemas } from "../../api/client";

const { Header, Sider, Content } = Layout;

export default function AdminLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const [username, setUsername] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let cancelled = false;
    apiFetch<Schemas["MeResponse"]>("/api/auth/me")
      .then((res) => {
        if (!cancelled) setUsername(res.username);
      })
      .catch(() => {
        if (!cancelled) setUsername(null);
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLogout = async () => {
    await apiFetch("/api/auth/logout", { method: "POST" }).catch(() => {});
    setUsername(null);
    message.info("已退出登录");
    navigate("/admin/login", { replace: true });
  };

  if (checking) {
    return (
      <div style={{ display: "grid", placeItems: "center", minHeight: "100vh" }}>
        <Space direction="vertical" align="center">
          <Spin size="large" />
          <Typography.Text type="secondary">正在校验管理员登录态...</Typography.Text>
        </Space>
      </div>
    );
  }

  if (!username) {
    const currentTarget = `${location.pathname}${location.search}`;
    return (
      <Navigate
        to={`/admin/login?redirect=${encodeURIComponent(currentTarget)}`}
        replace
      />
    );
  }

  const menuItems = [
    { key: "/admin/dashboard", icon: <DashboardOutlined />, label: "运营监控总览" },
    { key: "/admin/review", icon: <AuditOutlined />, label: "人工审核工作台" },
    { key: "/admin/sources", icon: <CloudServerOutlined />, label: "信源与域名规则" },
    { key: "/admin/rules", icon: <FilterOutlined />, label: "风控策略与规则" },
    { key: "/admin/apis", icon: <ApiOutlined />, label: "API 管理" },
    { key: "/admin/schedule", icon: <ScheduleOutlined />, label: "采集与调度" },
  ];

  const selectedKey =
    menuItems.find((item) => location.pathname.startsWith(item.key))?.key ||
    "/admin/review";

  return (
    <ConfigProvider locale={zhCN}>
      <Layout style={{ minHeight: "100vh" }}>
        <Sider width={220} theme="dark">
          <div
            style={{
              height: 56,
              display: "flex",
              alignItems: "center",
              padding: "0 20px",
              color: "#fff",
              fontWeight: 700,
              fontSize: 16,
              borderBottom: "1px solid rgba(255,255,255,0.08)",
            }}
          >
            神秘新闻 · 运营台
          </div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[selectedKey]}
            items={menuItems}
            onClick={({ key }) => navigate(key)}
          />
        </Sider>
        <Layout>
          <Header
            style={{
              background: "#fff",
              padding: "0 24px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              borderBottom: "1px solid #f0f0f0",
            }}
          >
            <Typography.Text strong>
              标题负责勾人，正文负责说清楚 · 全人工审核发布闭环
            </Typography.Text>
            <Space>
              <Button icon={<MobileOutlined />} onClick={() => navigate("/column")}>
                预览公开栏目页
              </Button>
              <Typography.Text type="secondary">当前：{username}</Typography.Text>
              <Button size="small" icon={<LogoutOutlined />} onClick={handleLogout}>
                退出
              </Button>
            </Space>
          </Header>
          <Content style={{ padding: 20 }}>
            <Suspense
              fallback={
                <div style={{ padding: 48, textAlign: "center" }}>
                  <Spin />
                </div>
              }
            >
              <Outlet />
            </Suspense>
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  );
}
