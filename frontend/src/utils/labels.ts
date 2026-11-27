export const ARTICLE_STATUS_META: Record<string, { label: string; color: string }> = {
  ingested: { label: "已采集待预处理", color: "default" },
  processing: { label: "处理中", color: "processing" },
  judged: { label: "待 AI 加工", color: "cyan" },
  candidate: { label: "常规候选", color: "blue" },
  pending: { label: "待定复核", color: "orange" },
  approved: { label: "已审核待发布", color: "geekblue" },
  published: { label: "已发布", color: "green" },
  unpublished: { label: "已下架", color: "volcano" },
  rejected: { label: "人工拒绝", color: "red" },
  rule_rejected: { label: "规则/非事件拦截", color: "default" },
  duplicate: { label: "重复事件", color: "purple" },
  expired: { label: "超时过期", color: "default" },
};

export const EVENT_TYPE_META: Record<string, { label: string; color: string; short: string }> = {
  deep_space_particle: { label: "深空 / 粒子", color: "#4f46e5", short: "深空粒子" },
  neo: { label: "近地天体", color: "#dc2626", short: "近地天体" },
  exoplanet: { label: "新星球 / 类地行星", color: "#0284c7", short: "类地行星" },
  bci_consciousness: { label: "脑机 / 意识", color: "#7c3aed", short: "脑机意识" },
  anomaly_psychic: { label: "异常 / 灵能", color: "#d97706", short: "异常灵能" },
  other: { label: "其他", color: "#64748b", short: "其他" },
};

export const HEALTH_STATUS_META: Record<string, { label: string; color: string }> = {
  ok: { label: "正常", color: "green" },
  degraded: { label: "降级", color: "orange" },
  down: { label: "宕机", color: "red" },
  auth_error: { label: "鉴权失败", color: "magenta" },
  starved: { label: "空转无新", color: "gold" },
  disabled: { label: "已停用", color: "default" },
};

export const SOURCE_LEVEL_META: Record<string, { label: string; color: string }> = {
  A: { label: "A 级权威", color: "green" },
  B: { label: "B 级可核验", color: "blue" },
  C: { label: "C 级线索", color: "orange" },
};

export const RISK_FLAG_LABELS: Record<string, string> = {
  supernatural: "灵异/异常边界",
  c_level_source: "C 级线索源",
  prohibited_claim: "命中红线绝对化词",
  title_has_spoiler: "标题含解释后缀",
  title_length_invalid: "标题字数越界",
  body_lead_length: "导语字数偏离",
  body_summary_length: "摘要字数偏离",
  bureaucratic_cliche: "含公文套话",
  missing_supernatural_disclaimer: "缺灵异免责限定语",
  verify_failed: "轻校验未通过",
  stale_event: "旧闻翻炒提示",
  missing_publish_time: "缺原始发布时间",
  no_full_text: "仅基于摘要（无全文）",
  cross_verified_by_ab: "已获 A/B 级交叉佐证",
  budget_exceeded: "超日预算熔断",
  off_topic_borderline: "泛宇宙边界待确认",
  unverified_source: "无官方来源核验",
};

export const KEYWORD_TYPE_LABELS: Record<string, string> = {
  exclude_hard: "硬排除（直接拒绝）",
  exclude_soft: "软扣分（白名单可救回）",
  anchor: "栏目锚定词",
  signal: "泛宇宙信号词（加分）",
  event_verb: "事件动词（白名单触发）",
};

export function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
