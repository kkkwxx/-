import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Col,
  InputNumber,
  Row,
  Segmented,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from "antd";
import {
  RedoOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { apiFetch, type Schemas } from "../api/client";
import { formatTime } from "../utils/labels";

type ProfileKey = Schemas["RiskPolicyOut"]["profile"];

const PROFILE_BADGE: Record<ProfileKey, { label: string; color: string }> = {
  strict: { label: "严格风控", color: "red" },
  balanced: { label: "均衡默认", color: "blue" },
  relaxed: { label: "宽松召回", color: "green" },
};

interface Props {
  compact?: boolean;
}

export default function RiskPolicyCard({ compact = false }: Props) {
  const navigate = useNavigate();
  const [policy, setPolicy] = useState<Schemas["RiskPolicyOut"] | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // 可微调阈值草稿状态
  const [profile, setProfile] = useState<ProfileKey>("balanced");
  const [evMin, setEvMin] = useState(0.7);
  const [notEvMin, setNotEvMin] = useState(0.8);
  const [freshHours, setFreshHours] = useState(48);
  const [trgm, setTrgm] = useState(0.65);
  const [titleMin, setTitleMin] = useState(7);
  const [titleMax, setTitleMax] = useState(20);
  const [requireFt, setRequireFt] = useState(false);

  const syncFromPolicy = (p: Schemas["RiskPolicyOut"]) => {
    setPolicy(p);
    setProfile(p.profile);
    setEvMin(p.thresholds.event_confidence_min);
    setNotEvMin(p.thresholds.not_event_confidence_min);
    setFreshHours(p.thresholds.freshness_hours);
    setTrgm(p.thresholds.dedup_trgm_threshold);
    setTitleMin(p.thresholds.title_min_chars);
    setTitleMax(p.thresholds.title_max_chars);
    setRequireFt(p.thresholds.require_full_text_for_candidate);
  };

  useEffect(() => {
    setLoading(true);
    apiFetch<Schemas["RiskPolicyOut"]>("/api/policy")
      .then(syncFromPolicy)
      .catch((err) => message.error(err instanceof Error ? err.message : "加载风控策略失败"))
      .finally(() => setLoading(false));
  }, []);

  const handleSwitchProfile = async (nextProfile: ProfileKey) => {
    setSaving(true);
    try {
      const updated = await apiFetch<Schemas["RiskPolicyOut"]>("/api/policy", {
        method: "PUT",
        body: JSON.stringify({ profile: nextProfile, reset_overrides: true }),
      });
      syncFromPolicy(updated);
      message.success(`已切换为「${PROFILE_BADGE[nextProfile].label}」档位，全链路立即生效`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "切换档位失败");
    } finally {
      setSaving(false);
    }
  };

  const handleSaveCustom = async () => {
    if (titleMin > titleMax) {
      message.warning("标题最小字数不能大于最大字数");
      return;
    }
    setSaving(true);
    try {
      const updated = await apiFetch<Schemas["RiskPolicyOut"]>("/api/policy", {
        method: "PUT",
        body: JSON.stringify({
          profile,
          event_confidence_min: evMin,
          not_event_confidence_min: notEvMin,
          freshness_hours: freshHours,
          dedup_trgm_threshold: trgm,
          title_min_chars: titleMin,
          title_max_chars: titleMax,
          require_full_text_for_candidate: requireFt,
          reset_overrides: false,
        }),
      });
      syncFromPolicy(updated);
      message.success("自定义风控阈值已保存并热生效");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存阈值失败");
    } finally {
      setSaving(false);
    }
  };

  const activePreset = (policy?.presets ?? []).find((p) => p.key === profile);
  const segmentedOptions = [
    { label: "严格风控", value: "strict" },
    { label: "均衡默认", value: "balanced" },
    { label: "宽松召回", value: "relaxed" },
  ];

  if (compact) {
    return (
      <Card
        size="small"
        loading={loading && !policy}
        title={
          <Space>
            <SafetyCertificateOutlined style={{ color: "var(--mn-primary)" }} />
            <span>风控严厉度策略选择器</span>
            <Tag color={PROFILE_BADGE[profile].color}>{PROFILE_BADGE[profile].label}</Tag>
            {policy?.has_custom_overrides && <Tag color="purple">含自定义阈值</Tag>}
          </Space>
        }
        extra={
          <Button
            size="small"
            icon={<SettingOutlined />}
            onClick={() => navigate("/admin/rules")}
          >
            阈值微调
          </Button>
        }
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 12,
          }}
        >
          <Segmented
            value={profile}
            disabled={saving}
            options={segmentedOptions}
            onChange={(val) => handleSwitchProfile(val as ProfileKey)}
          />
          <Space wrap size={6}>
            <Tag>事件置信度 ≥ {evMin}</Tag>
            <Tag>时效 ≤ {freshHours}h</Tag>
            <Tag>去重阈值 {trgm}</Tag>
            <Tag>标题 {titleMin}–{titleMax} 字</Tag>
            {requireFt && <Tag color="volcano">无全文必进待定池</Tag>}
          </Space>
        </div>
      </Card>
    );
  }

  return (
    <Card
      loading={loading && !policy}
      title={
        <Space>
          <SafetyCertificateOutlined style={{ color: "var(--mn-primary)" }} />
          <span>风控严厉度档位开关与阈值动态配置</span>
          <Tag color={PROFILE_BADGE[profile].color}>{PROFILE_BADGE[profile].label}</Tag>
          {policy?.has_custom_overrides && <Tag color="purple">自定义微调生效中</Tag>}
        </Space>
      }
      extra={
        policy?.updated_at ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            最近更新：{policy.updated_by} · {formatTime(policy.updated_at)}
          </Typography.Text>
        ) : null
      }
      style={{ marginBottom: 16 }}
    >
      <Space direction="vertical" size={14} style={{ width: "100%" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 12,
          }}
        >
          <Segmented
            size="large"
            value={profile}
            disabled={saving}
            options={segmentedOptions}
            onChange={(val) => handleSwitchProfile(val as ProfileKey)}
          />
          <Space>
            <Button
              icon={<RedoOutlined />}
              disabled={saving || !policy?.has_custom_overrides}
              onClick={() => handleSwitchProfile(profile)}
            >
              恢复当前档位默认值
            </Button>
            <Button type="primary" loading={saving} onClick={handleSaveCustom}>
              保存阈值配置（实时生效）
            </Button>
          </Space>
        </div>

        {activePreset && (
          <Alert
            type="info"
            showIcon
            message={`当前档位说明（${activePreset.label}）：${activePreset.description}`}
            description="安全底线不受档位影响：全人工审核、禁止红线绝对化词拦截、C 级信源与灵异/异常类进待定池及免责限定语始终强制开启。"
          />
        )}

        <Row gutter={[16, 12]}>
          <Col xs={12} md={8} lg={4}>
            <div style={{ fontSize: 12, color: "var(--mn-text-secondary)", marginBottom: 4 }}>
              进候选池置信度门槛
            </div>
            <InputNumber
              min={0.3}
              max={0.99}
              step={0.05}
              value={evMin}
              onChange={(v) => v !== null && setEvMin(v)}
              style={{ width: "100%" }}
            />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <div style={{ fontSize: 12, color: "var(--mn-text-secondary)", marginBottom: 4 }}>
              直接判非事件置信度
            </div>
            <InputNumber
              min={0.5}
              max={0.99}
              step={0.05}
              value={notEvMin}
              onChange={(v) => v !== null && setNotEvMin(v)}
              style={{ width: "100%" }}
            />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <div style={{ fontSize: 12, color: "var(--mn-text-secondary)", marginBottom: 4 }}>
              推送时效门禁（小时）
            </div>
            <InputNumber
              min={6}
              max={168}
              step={6}
              value={freshHours}
              onChange={(v) => v !== null && setFreshHours(v)}
              style={{ width: "100%" }}
            />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <div style={{ fontSize: 12, color: "var(--mn-text-secondary)", marginBottom: 4 }}>
              去重相似度阈值
            </div>
            <InputNumber
              min={0.3}
              max={0.95}
              step={0.05}
              value={trgm}
              onChange={(v) => v !== null && setTrgm(v)}
              style={{ width: "100%" }}
            />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <div style={{ fontSize: 12, color: "var(--mn-text-secondary)", marginBottom: 4 }}>
              标题去标点字数区间
            </div>
            <Space.Compact style={{ width: "100%" }}>
              <InputNumber
                min={4}
                max={15}
                value={titleMin}
                onChange={(v) => v !== null && setTitleMin(v)}
                style={{ width: "50%" }}
              />
              <InputNumber
                min={12}
                max={30}
                value={titleMax}
                onChange={(v) => v !== null && setTitleMax(v)}
                style={{ width: "50%" }}
              />
            </Space.Compact>
          </Col>
          <Col xs={12} md={8} lg={4}>
            <div style={{ fontSize: 12, color: "var(--mn-text-secondary)", marginBottom: 4 }}>
              无全文强制进待定池
            </div>
            <div style={{ paddingTop: 4 }}>
              <Switch
                checked={requireFt}
                onChange={setRequireFt}
                checkedChildren="严格"
                unCheckedChildren="允许摘要"
              />
            </div>
          </Col>
        </Row>
      </Space>
    </Card>
  );
}
