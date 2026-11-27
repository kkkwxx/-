import { Suspense, useEffect, useState } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Button, ConfigProvider, Layout, Menu, Popover, Spin, message } from "antd";
import zhCN from "antd/locale/zh_CN";
import {
  ApiOutlined,
  AuditOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  DownOutlined,
  FilterOutlined,
  LogoutOutlined,
  MobileOutlined,
  ScheduleOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import { apiFetch, type Schemas } from "../../api/client";
import adminTheme from "../../theme";

const { Header, Sider, Content } = Layout;

type NavItem = {
  key: string;
  label: string;
  desc: string;
  icon: React.ReactNode;
};

const NAV_ITEMS: NavItem[] = [
  {
    key: "/admin/dashboard",
    label: "运营监控总览",
    desc: "全链路指标、Token 预算与信源健康",
    icon: <DashboardOutlined />,
  },
  {
    key: "/admin/review",
    label: "人工审核工作台",
    desc: "原文对照 + 7 项 Checklist 全人工发布",
    icon: <AuditOutlined />,
  },
  {
    key: "/admin/sources",
    label: "信源与域名规则",
    desc: "采集信源、域名白名单与健康状态",
    icon: <CloudServerOutlined />,
  },
  {
    key: "/admin/rules",
    label: "风控策略与规则",
    desc: "三档严厉度、排除词与动态阈值",
    icon: <FilterOutlined />,
  },
  {
    key: "/admin/apis",
    label: "API 管理",
    desc: "AI 供应商接入与密钥轮换",
    icon: <ApiOutlined />,
  },
  {
    key: "/admin/schedule",
    label: "采集与调度",
    desc: "定时采集、手动触发与运行记录",
    icon: <ScheduleOutlined />,
  },
];

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

  const selectedKey =
    NAV_ITEMS.find((item) => location.pathname.startsWith(item.key))?.key ||
    "/admin/review";
  const current = NAV_ITEMS.find((item) => item.key === selectedKey);

  if (checking) {
    return (
      <div className="mn-boot">
        <div style={{ textAlign: "center" }}>
          <Spin size="large" />
          <div className="mn-faint" style={{ marginTop: 12 }}>
            正在校验管理员登录态...
          </div>
        </div>
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

  return (
    <ConfigProvider locale={zhCN} theme={adminTheme}>
      <Layout className="mn-admin">
        <Sider width={272} theme="light" className="mn-sider">
          <div className="mn-brand">
            <span className="mn-brand-mark">神</span>
            <span className="mn-brand-text">
              <span className="mn-brand-title">神秘新闻</span>
              <span className="mn-brand-sub">运营后台</span>
            </span>
          </div>
          <Menu
            theme="light"
            mode="inline"
            selectedKeys={[selectedKey]}
            items={NAV_ITEMS.map((item) => ({
              key: item.key,
              icon: item.icon,
              label: item.label,
            }))}
            onClick={({ key }) => navigate(key)}
          />
          <div className="mn-user-box">
            <Popover
              trigger="click"
              placement="topLeft"
              arrow={false}
              classNames={{ root: "mn-user-popover" }}
              styles={{ body: { padding: 0 } }}
              content={
                <div className="mn-user-pop">
                  <div className="mn-user-pop-head">
                    <span className="mn-sider-avatar mn-sider-avatar--lg">
                      {(username || "A").slice(0, 1).toUpperCase()}
                    </span>
                    <span className="mn-user-pop-meta">
                      <strong>{username}</strong>
                      <span>管理员 · 单账号模式</span>
                    </span>
                  </div>
                  <div className="mn-user-pop-sep" />
                  <button type="button" className="mn-user-pop-item" disabled>
                    <SwapOutlined />
                    <span className="mn-user-pop-label">切换账号</span>
                    <span className="mn-user-pop-note">接口待开发</span>
                  </button>
                </div>
              }
            >
              <button type="button" className="mn-sider-user">
                <span className="mn-sider-avatar">
                  {(username || "A").slice(0, 1).toUpperCase()}
                </span>
                <span className="mn-sider-user-meta">
                  <span className="mn-sider-user-name">{username}</span>
                  <span className="mn-sider-user-role">管理员 · 单账号</span>
                </span>
                <DownOutlined className="mn-sider-user-caret" />
              </button>
            </Popover>
            <div className="mn-user-box-sep" />
            <button
              type="button"
              className="mn-sider-logout"
              onClick={handleLogout}
            >
              <LogoutOutlined />
              <span>退出登录</span>
            </button>
          </div>
        </Sider>
        <Layout className="mn-main">
          <Header className="mn-header">
            <div className="mn-header-left">
              <span className="mn-header-title">
                {current?.label ?? "运营监控总览"}
              </span>
              <span className="mn-header-desc">{current?.desc ?? ""}</span>
            </div>
            <div className="mn-header-right">
              <Button
                type="text"
                icon={<MobileOutlined />}
                href="/column"
                target="_blank"
                rel="noopener noreferrer"
              >
                预览公开栏目页
              </Button>
            </div>
          </Header>
          <Content className="mn-content">
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
