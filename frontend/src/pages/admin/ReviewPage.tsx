import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Drawer,
  Input,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  DeleteOutlined,
  ReloadOutlined,
  SendOutlined,
  SyncOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { apiFetch, buildQuery, type Schemas } from "../../api/client";
import {
  ARTICLE_STATUS_META,
  EVENT_TYPE_META,
  RISK_FLAG_LABELS,
  SOURCE_LEVEL_META,
  formatTime,
} from "../../utils/labels";

const QUEUE_TABS = [
  { key: "candidate", label: "常规候选池" },
  { key: "pending", label: "待定复核池" },
  { key: "approved", label: "已通过待发布" },
  { key: "published", label: "已发布" },
  { key: "judged", label: "待 AI 加工" },
  { key: "rejected", label: "人工拒绝" },
  { key: "rule_rejected,duplicate,expired", label: "自动拦截/重复/过期" },
];

export default function ReviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [statusTab, setStatusTab] = useState(searchParams.get("status") || "candidate");
  const [eventType, setEventType] = useState<string | undefined>(undefined);
  const [sourceLevel, setSourceLevel] = useState<string | undefined>(undefined);
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [list, setList] = useState<Schemas["ArticleListResponse"] | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([]);
  const [deleting, setDeleting] = useState(false);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<Schemas["ArticleDetail"] | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const detailReqId = useRef(0);

  // 编辑态字段
  const [titleFinal, setTitleFinal] = useState("");
  const [bodyLead, setBodyLead] = useState("");
  const [bodySummary, setBodySummary] = useState("");
  const [cultureTag, setCultureTag] = useState("");
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [evidenceNote, setEvidenceNote] = useState("");

  // 拒绝弹窗
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");

  const isDirty = Boolean(
    detail &&
      (titleFinal !== (detail.title_final || detail.title || "") ||
        bodyLead !== (detail.body_lead || "") ||
        bodySummary !== (detail.body_summary || "") ||
        cultureTag !== (detail.culture_tag || "") ||
        evidenceUrl.trim() !== "" ||
        evidenceNote.trim() !== "")
  );

  useEffect(() => {
    const paramStatus = searchParams.get("status");
    if (paramStatus && paramStatus !== statusTab) {
      setStatusTab(paramStatus);
      setPage(1);
    }
  }, [searchParams, statusTab]);

  const loadList = useCallback(() => {
    setLoading(true);
    apiFetch<Schemas["ArticleListResponse"]>(
      `/api/articles${buildQuery({
        status: statusTab,
        event_type: eventType,
        source_level: sourceLevel,
        keyword: keyword.trim() || undefined,
        page,
        page_size: pageSize,
      })}`
    )
      .then(setList)
      .catch((err) => message.error(err instanceof Error ? err.message : "加载列表失败"))
      .finally(() => setLoading(false));
  }, [statusTab, eventType, sourceLevel, keyword, page, pageSize]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  const syncFormFromDetail = (d: Schemas["ArticleDetail"]) => {
    setTitleFinal(d.title_final || d.title || "");
    setBodyLead(d.body_lead || "");
    setBodySummary(d.body_summary || "");
    setCultureTag(d.culture_tag || "");
    setEvidenceUrl("");
    setEvidenceNote("");
  };

  const openDetail = (id: number) => {
    const reqId = ++detailReqId.current;
    setSelectedId(id);
    setDetailLoading(true);
    apiFetch<Schemas["ArticleDetail"]>(`/api/articles/${id}`)
      .then((d) => {
        if (detailReqId.current !== reqId) return;
        setDetail(d);
        syncFormFromDetail(d);
      })
      .catch((err) => {
        if (detailReqId.current !== reqId) return;
        message.error(err instanceof Error ? err.message : "加载详情失败");
      })
      .finally(() => {
        if (detailReqId.current === reqId) setDetailLoading(false);
      });
  };

  const handleSaveEdit = async () => {
    if (!detail) return;
    setSaving(true);
    try {
      const updated = await apiFetch<Schemas["ArticleDetail"]>(
        `/api/articles/${detail.id}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            title_final: titleFinal,
            body_lead: bodyLead,
            body_summary: bodySummary,
            culture_tag: cultureTag || null,
            evidence_url: evidenceUrl.trim() || undefined,
            evidence_note: evidenceNote.trim() || undefined,
          }),
        }
      );
      setDetail(updated);
      syncFormFromDetail(updated);
      loadList();
      if (updated.verification.passed) {
        message.success("已保存并通过确定性轻校验");
      } else {
        message.warning("已保存，但轻校验仍有未通过项，请查看右侧提示");
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleReviewAction = async (
    action: Schemas["ReviewActionRequest"]["action"],
    reason?: string
  ): Promise<boolean> => {
    if (!detail) return false;
    if (isDirty && (action === "approve" || action === "to_pending")) {
      message.warning("当前有未保存的编辑内容，请先点击「保存修改并重跑轻校验」再执行审核动作");
      return false;
    }
    setSaving(true);
    try {
      const updated = await apiFetch<Schemas["ArticleDetail"]>(
        `/api/articles/${detail.id}/review`,
        {
          method: "POST",
          body: JSON.stringify({ action, reject_reason: reason }),
        }
      );
      setDetail(updated);
      syncFormFromDetail(updated);
      loadList();
      message.success("审核状态已更新");
      return true;
    } catch (err) {
      message.error(err instanceof Error ? err.message : "操作失败");
      return false;
    } finally {
      setSaving(false);
    }
  };

  const handlePublish = async () => {
    if (!detail) return;
    if (isDirty) {
      message.warning("当前有未保存的编辑内容，请先保存修改并重新通过审核后再发布");
      return;
    }
    setSaving(true);
    try {
      await apiFetch<Schemas["PublishedSnapshotOut"]>(
        `/api/articles/${detail.id}/publish`,
        {
          method: "POST",
          body: JSON.stringify({}),
        }
      );
      message.success("已写入不可变发布快照并上线栏目页！");
      openDetail(detail.id);
      loadList();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "发布失败");
    } finally {
      setSaving(false);
    }
  };

  const handleUnpublishOrRepublish = async (isActive: boolean) => {
    if (!detail?.latest_published) return;
    setSaving(true);
    const endpoint = isActive
      ? `/api/published/${detail.latest_published.id}/unpublish`
      : `/api/published/${detail.latest_published.id}/republish`;
    try {
      await apiFetch(endpoint, { method: "POST" });
      message.success(isActive ? "已下架" : "已重新上架");
      openDetail(detail.id);
      loadList();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "操作失败");
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteArticle = async (id: number) => {
    setDeleting(true);
    try {
      await apiFetch(`/api/articles/${id}`, { method: "DELETE" });
      message.success(`文章 #${id} 已删除`);
      setSelectedRowKeys((prev) => prev.filter((k) => k !== id));
      if (selectedId === id) {
        setSelectedId(null);
        setDetail(null);
      }
      loadList();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  };

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) return;
    setDeleting(true);
    try {
      const res = await apiFetch<Schemas["BatchDeleteArticlesResponse"]>(
        "/api/articles/batch-delete",
        {
          method: "POST",
          body: JSON.stringify({ ids: selectedRowKeys }),
        }
      );
      const delCount = res.deleted_ids?.length ?? 0;
      const skipCount = res.skipped?.length ?? 0;
      if (delCount > 0 && skipCount === 0) {
        message.success(`已批量删除 ${delCount} 篇文章`);
      } else if (delCount > 0 && skipCount > 0) {
        message.warning(`已删除 ${delCount} 篇，跳过 ${skipCount} 篇（已发布或含历史快照）`);
      } else {
        message.warning(res.skipped?.[0]?.reason || "所选文章均不可删除");
      }
      setSelectedRowKeys([]);
      if (selectedId && res.deleted_ids?.includes(selectedId)) {
        setSelectedId(null);
        setDetail(null);
      }
      loadList();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "批量删除失败");
    } finally {
      setDeleting(false);
    }
  };

  const counts = list?.counts_by_status || {};
  const tabCount = (key: string) =>
    key.split(",").reduce((acc, k) => acc + (counts[k] || 0), 0);

  return (
    <Card styles={{ body: { paddingTop: 12 } }}>
      <Tabs
        activeKey={statusTab}
        onChange={(k) => {
          setStatusTab(k);
          setPage(1);
          setSelectedRowKeys([]);
          setSearchParams({ status: k });
        }}
        items={QUEUE_TABS.map((t) => ({
          key: t.key,
          label: (
            <Space size={6}>
              <span>{t.label}</span>
              <Badge count={tabCount(t.key)} showZero color="var(--mn-text-tertiary)" overflowCount={999} />
            </Space>
          ),
        }))}
      />

      <Space wrap style={{ marginBottom: 14 }}>
        <Select
          allowClear
          placeholder="按五大栏目筛选"
          style={{ width: 170 }}
          value={eventType}
          onChange={(v) => {
            setEventType(v);
            setPage(1);
          }}
          options={Object.entries(EVENT_TYPE_META).map(([v, m]) => ({
            value: v,
            label: m.label,
          }))}
        />
        <Select
          allowClear
          placeholder="来源等级"
          style={{ width: 130 }}
          value={sourceLevel}
          onChange={(v) => {
            setSourceLevel(v);
            setPage(1);
          }}
          options={[
            { value: "A", label: "A 级权威" },
            { value: "B", label: "B 级可核验" },
            { value: "C", label: "C 级线索" },
          ]}
        />
        <Input.Search
          allowClear
          placeholder="搜索标题/导语/媒体"
          style={{ width: 240 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={() => {
            setPage(1);
            loadList();
          }}
        />
        <Button icon={<ReloadOutlined />} onClick={loadList} loading={loading}>
          刷新
        </Button>
        {selectedRowKeys.length > 0 && (
          <Popconfirm
            title={`确认批量删除已选的 ${selectedRowKeys.length} 篇文章吗？`}
            description="删除后不可恢复（已发布或存在历史发布快照的文章会被自动跳过）"
            okText="确认删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={handleBatchDelete}
          >
            <Button danger icon={<DeleteOutlined />} loading={deleting}>
              批量删除（{selectedRowKeys.length}）
            </Button>
          </Popconfirm>
        )}
      </Space>

      <Table
        rowKey="id"
        loading={loading}
        dataSource={list?.items || []}
        scroll={{ x: 1080 }}
        rowSelection={{
          selectedRowKeys,
          onChange: (keys) => setSelectedRowKeys(keys.map(Number)),
          getCheckboxProps: (row) => ({
            disabled: row.status === "published",
          }),
        }}
        pagination={{
          current: page,
          pageSize,
          total: list?.total || 0,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (nextPage, nextSize) => {
            setPage(nextPage);
            setPageSize(nextSize);
            setSelectedRowKeys([]);
          },
        }}
        columns={[
          {
            title: "封面钩子标题 / 原标题",
            width: 320,
            render: (_, row) => (
              <div>
                <div style={{ fontWeight: 600, fontSize: 14 }}>
                  {row.title_final || <Typography.Text type="secondary">（待生成标题）</Typography.Text>}
                </div>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  原文：{row.title}
                </Typography.Text>
              </div>
            ),
          },
          {
            title: "栏目",
            dataIndex: "event_type",
            width: 130,
            render: (v: string | null) => {
              if (!v) return "—";
              const m = EVENT_TYPE_META[v] || EVENT_TYPE_META.other;
              return <Tag color={m.color}>{m.short}</Tag>;
            },
          },
          {
            title: "信源",
            width: 140,
            render: (_, row) => {
              const m = SOURCE_LEVEL_META[row.source_level] || { label: row.source_level, color: "default" };
              return (
                <Space direction="vertical" size={0}>
                  <Tag color={m.color}>{m.label}</Tag>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {row.publisher_name || "未知媒体"}
                  </Typography.Text>
                </Space>
              );
            },
          },
          {
            title: "风险 / 评分",
            width: 170,
            render: (_, row) => (
              <Space wrap size={4}>
                {row.hook_score != null && row.hook_score > 0 && (
                  <Tag color="magenta">钩子 {row.hook_score}★</Tag>
                )}
                {(row.risk_flags ?? []).slice(0, 2).map((f) => (
                  <Tag key={f} color={f === "cross_verified_by_ab" ? "green" : "warning"}>
                    {RISK_FLAG_LABELS[f] || f}
                  </Tag>
                ))}
              </Space>
            ),
          },
          {
            title: "状态",
            dataIndex: "status",
            width: 120,
            render: (v: string) => {
              const m = ARTICLE_STATUS_META[v] || { label: v, color: "default" };
              return <Tag color={m.color}>{m.label}</Tag>;
            },
          },
          {
            title: "时间",
            width: 115,
            render: (_, row) => formatTime(row.published_at_raw || row.created_at),
          },
          {
            title: "操作",
            width: 160,
            fixed: "right",
            render: (_, row) => (
              <Space size={6}>
                <Button type="primary" size="small" onClick={() => openDetail(row.id)}>
                  对照审核
                </Button>
                <Popconfirm
                  title="确认删除这篇文章吗？"
                  description="删除后不可恢复，但审计与 AI 留痕会保留。"
                  okText="确认删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  disabled={row.status === "published"}
                  onConfirm={() => handleDeleteArticle(row.id)}
                >
                  <Button
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    disabled={row.status === "published"}
                    loading={deleting}
                    title={row.status === "published" ? "已发布文章不可直接删除" : "删除文章"}
                  >
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Drawer
        open={selectedId !== null}
        onClose={() => {
          if (isDirty) {
            Modal.confirm({
              title: "有未保存的编辑内容",
              content: "关闭抽屉将丢失尚未保存的修改，确认放弃并关闭吗？",
              okText: "放弃修改并关闭",
              cancelText: "继续编辑",
              onOk: () => {
                setSelectedId(null);
                setDetail(null);
              },
            });
            return;
          }
          setSelectedId(null);
          setDetail(null);
        }}
        width={1120}
        title={
          detail ? (
            <Space>
              <span>文章 #{detail.id} 原文对照审核</span>
              <Tag color={ARTICLE_STATUS_META[detail.status]?.color}>
                {ARTICLE_STATUS_META[detail.status]?.label || detail.status}
              </Tag>
              <Tag color={SOURCE_LEVEL_META[detail.source_level]?.color}>
                {detail.source_level} 级（{detail.source_level_reason || "—"}）
              </Tag>
            </Space>
          ) : (
            "加载中..."
          )
        }
      >
        {detailLoading || !detail ? (
          <Card loading />
        ) : (
          <Row gutter={18}>
            {/* 左栏：原始新闻与核验线索 */}
            <Col xs={24} lg={11}>
              <Space direction="vertical" size={12} style={{ width: "100%" }}>
                <Card size="small" title="原始新闻快照（只读对照）">
                  <Typography.Title level={5} style={{ marginTop: 0 }}>
                    {detail.raw_title || detail.title}
                  </Typography.Title>
                  <Descriptions size="small" column={1} bordered>
                    <Descriptions.Item label="出处媒体">
                      {detail.publisher_name || "未知"}（{detail.resolve_status === "resolved" ? "已解析原链" : "聚合来源"}）
                    </Descriptions.Item>
                    <Descriptions.Item label="原始链接">
                      {detail.original_url || detail.raw_url ? (
                        <a
                          href={detail.original_url || detail.raw_url || "#"}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {detail.original_url || detail.raw_url}
                        </a>
                      ) : (
                        "—"
                      )}
                    </Descriptions.Item>
                    <Descriptions.Item label="时间四元组">
                      发布：{formatTime(detail.published_at_raw)} | 抓取：{formatTime(detail.fetched_at)}
                      <br />
                      事件：{formatTime(detail.event_time)} | 首次发现：{formatTime(detail.first_seen_at)}
                    </Descriptions.Item>
                    <Descriptions.Item label="内容性质 / 置信度">
                      {detail.content_nature || "—"} /{" "}
                      {detail.event_confidence !== null && detail.event_confidence !== undefined
                        ? `${Math.round(detail.event_confidence * 100)}%`
                        : "—"}
                    </Descriptions.Item>
                  </Descriptions>

                  {detail.full_text_status && detail.full_text_status !== "ok" && detail.full_text_status !== "skipped" && (
                    <Alert
                      style={{ marginTop: 10 }}
                      type="warning"
                      showIcon
                      message={`未取得完整正文（状态：${detail.full_text_status}），AI 仅基于标题与摘要生成，请点原链接核对。`}
                    />
                  )}

                  <Divider orientation="left" plain style={{ margin: "12px 0 8px" }}>
                    原文摘要 / 正文
                  </Divider>
                  <div
                    style={{
                      maxHeight: 240,
                      overflowY: "auto",
                      whiteSpace: "pre-wrap",
                      fontSize: 13,
                      lineHeight: 1.65,
                      background: "var(--mn-surface-muted)",
                      padding: 10,
                      borderRadius: 6,
                    }}
                  >
                    {detail.full_text || detail.raw_summary || detail.summary || "无正文内容"}
                  </div>
                </Card>

                {detail.fact_anchor && (
                  <Alert
                    type="info"
                    showIcon
                    message="事实锚点（fact_anchor · 仅审核参考，不对外展示）"
                    description={detail.fact_anchor}
                  />
                )}

                {(detail.cluster_siblings ?? []).length > 0 && (
                  <Card size="small" title={`同事件簇其他信源（${(detail.cluster_siblings ?? []).length} 条交叉佐证）`}>
                    {(detail.cluster_siblings ?? []).map((s) => (
                      <div key={s.id} style={{ marginBottom: 6, fontSize: 13 }}>
                        <Tag color={SOURCE_LEVEL_META[s.source_level]?.color}>{s.source_level}</Tag>
                        <span>{s.publisher_name || "未知"}：{s.title}</span>
                      </div>
                    ))}
                  </Card>
                )}

                {(detail.evidence_notes ?? []).length > 0 && (
                  <Card size="small" title="佐证与核验记录">
                    {(detail.evidence_notes ?? []).map((ev, i) => (
                      <div key={i} style={{ fontSize: 12, marginBottom: 6 }}>
                        <Tag>{String(ev.type || "note")}</Tag>
                        <span>{String(ev.note || ev.title || "")} </span>
                        {ev.url ? (
                          <a href={String(ev.url)} target="_blank" rel="noreferrer">
                            {String(ev.url)}
                          </a>
                        ) : null}
                      </div>
                    ))}
                  </Card>
                )}
              </Space>
            </Col>

            {/* 右栏：AI 加工编辑、轻校验与 7 项发布 Checklist */}
            <Col xs={24} lg={13}>
              <Space direction="vertical" size={12} style={{ width: "100%" }}>
                {(detail.title_candidates ?? []).length > 0 && (
                  <Card size="small" title="AI 候选标题（一键点选填入）">
                    <Space direction="vertical" style={{ width: "100%" }} size={6}>
                      {(detail.title_candidates ?? []).map((cand, idx) => (
                        <div
                          key={idx}
                          onClick={() => setTitleFinal(cand.title)}
                          style={{
                            cursor: "pointer",
                            padding: "8px 10px",
                            borderRadius: 6,
                            border:
                              titleFinal === cand.title
                                ? "1.5px solid var(--mn-primary)"
                                : "1px solid var(--mn-border)",
                            background: titleFinal === cand.title ? "var(--mn-primary-soft)" : "var(--mn-surface)",
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                          }}
                        >
                          <div>
                            <strong>{cand.title}</strong>
                            {cand.rationale && (
                              <div style={{ fontSize: 12, color: "var(--mn-text-secondary)" }}>{cand.rationale}</div>
                            )}
                          </div>
                          <Space size={4}>
                            <Tag>{cand.style_tag}</Tag>
                            <Tag color="magenta">{cand.hook_score}★</Tag>
                          </Space>
                        </div>
                      ))}
                    </Space>
                  </Card>
                )}

                <Card size="small" title="成品编辑（保存即触发实时轻校验）">
                  <Space direction="vertical" style={{ width: "100%" }} size={10}>
                    <div>
                      <Typography.Text strong>
                        封面钩子标题（title_final，只保留前半句悬念）
                      </Typography.Text>
                      <Input
                        value={titleFinal}
                        onChange={(e) => setTitleFinal(e.target.value)}
                        showCount
                        maxLength={28}
                      />
                    </div>
                    <div>
                      <Typography.Text strong>
                        正文第一段破题（body_lead，20—60 字解释悬念）
                      </Typography.Text>
                      <Input.TextArea
                        rows={2}
                        value={bodyLead}
                        onChange={(e) => setBodyLead(e.target.value)}
                        showCount
                      />
                    </div>
                    <div>
                      <Typography.Text strong>
                        大白话摘要（body_summary，80—120 字）
                      </Typography.Text>
                      <Input.TextArea
                        rows={3}
                        value={bodySummary}
                        onChange={(e) => setBodySummary(e.target.value)}
                        showCount
                      />
                    </div>
                    <Row gutter={8}>
                      <Col span={8}>
                        <Typography.Text type="secondary">文化视角标签（可选）</Typography.Text>
                        <Input
                          placeholder="如：五行·金/玄幽"
                          value={cultureTag}
                          onChange={(e) => setCultureTag(e.target.value)}
                        />
                      </Col>
                      <Col span={8}>
                        <Typography.Text type="secondary">补充佐证链接（C 级用）</Typography.Text>
                        <Input
                          placeholder="https://..."
                          value={evidenceUrl}
                          onChange={(e) => setEvidenceUrl(e.target.value)}
                        />
                      </Col>
                      <Col span={8}>
                        <Typography.Text type="secondary">核验备注</Typography.Text>
                        <Input
                          placeholder="已与官方通报核对"
                          value={evidenceNote}
                          onChange={(e) => setEvidenceNote(e.target.value)}
                        />
                      </Col>
                    </Row>
                    <Button type="default" onClick={handleSaveEdit} loading={saving}>
                      保存修改并重跑轻校验
                    </Button>
                  </Space>
                </Card>

                {/* 轻校验结果 */}
                {!detail.verification.passed ? (
                  <Alert
                    type="error"
                    showIcon
                    message="轻校验未通过"
                    description={(detail.verification.errors ?? []).join("；")}
                  />
                ) : (detail.verification.warnings ?? []).length > 0 ? (
                  <Alert
                    type="warning"
                    showIcon
                    message="轻校验通过（有优化建议）"
                    description={(detail.verification.warnings ?? []).join("；")}
                  />
                ) : (
                  <Alert type="success" showIcon message="轻校验全部通过（无红线词与格式风险）" />
                )}

                {/* 7 项发布 Checklist */}
                <Card size="small" title="7 项发布 Checklist（全部绿灯 + 人工审核通过后可发布）">
                  <Space direction="vertical" size={4} style={{ width: "100%" }}>
                    {(detail.publish_checklist ?? []).map((c) => (
                      <div
                        key={c.key}
                        style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}
                      >
                        <span>
                          {c.passed ? (
                            <CheckCircleOutlined style={{ color: "var(--mn-success)", marginRight: 6 }} />
                          ) : (
                            <CloseCircleOutlined style={{ color: "var(--mn-danger)", marginRight: 6 }} />
                          )}
                          {c.label}
                        </span>
                        <Typography.Text type="secondary">{c.detail}</Typography.Text>
                      </div>
                    ))}
                  </Space>
                </Card>

                {/* 操作按钮条 */}
                <Card size="small">
                  <Space wrap>
                    <Button
                      type="primary"
                      icon={<CheckCircleOutlined />}
                      onClick={() => handleReviewAction("approve")}
                      loading={saving}
                      disabled={detail.verification.hard_failed}
                    >
                      审核通过
                    </Button>
                    <Button
                      type="primary"
                      style={{ background: "var(--mn-success)" }}
                      icon={<SendOutlined />}
                      onClick={handlePublish}
                      loading={saving}
                      disabled={!detail.can_publish}
                    >
                      立即发布到栏目
                    </Button>
                    <Button
                      icon={<WarningOutlined />}
                      onClick={() => handleReviewAction("to_pending")}
                      loading={saving}
                    >
                      转入待定池
                    </Button>
                    <Button
                      danger
                      icon={<CloseCircleOutlined />}
                      onClick={() => {
                        setRejectReason("");
                        setRejectOpen(true);
                      }}
                      loading={saving}
                    >
                      拒绝
                    </Button>
                    <Button
                      icon={<SyncOutlined />}
                      onClick={() => handleReviewAction("re_generate")}
                      loading={saving}
                    >
                      退回 AI 重生成
                    </Button>
                    {detail.latest_published && (
                      <Button
                        danger={detail.latest_published.is_active}
                        onClick={() =>
                          handleUnpublishOrRepublish(detail.latest_published!.is_active)
                        }
                        loading={saving}
                      >
                        {detail.latest_published.is_active ? "下架线上快照" : "重新上架快照"}
                      </Button>
                    )}
                    {detail.status !== "published" && !detail.latest_published && (
                      <Popconfirm
                        title="确认永久删除该文章吗？"
                        description="删除后将从列表移除，操作记录会保留在审计日志中。"
                        okText="确认删除"
                        cancelText="取消"
                        okButtonProps={{ danger: true }}
                        onConfirm={() => handleDeleteArticle(detail.id)}
                      >
                        <Button danger icon={<DeleteOutlined />} loading={deleting}>
                          删除文章
                        </Button>
                      </Popconfirm>
                    )}
                  </Space>
                </Card>
              </Space>
            </Col>
          </Row>
        )}
      </Drawer>

      <Modal
        open={rejectOpen}
        title="填写人工拒绝原因"
        okText="确认拒绝"
        cancelText="取消"
        confirmLoading={saving}
        onCancel={() => setRejectOpen(false)}
        onOk={async () => {
          if (!rejectReason.trim()) {
            message.warning("请填写拒绝原因");
            return;
          }
          const ok = await handleReviewAction("reject", rejectReason.trim());
          if (ok) {
            setRejectOpen(false);
          }
        }}
      >
        <Input.TextArea
          rows={3}
          placeholder="例如：标题与原文事实偏差、来源不可核验、纯商业软文..."
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
        />
      </Modal>
    </Card>
  );
}
