import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiFetch, buildQuery, type Schemas } from "../../api/client";
import { EVENT_TYPE_META, formatTime } from "../../utils/labels";
import "../../styles/column.css";

export default function ColumnDetailPage() {
  const { id } = useParams();
  const [item, setItem] = useState<Schemas["PublishedSnapshotOut"] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ranking, setRanking] = useState<Schemas["ColumnRankingItem"][]>([]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setItem(null);

    apiFetch<Schemas["PublishedSnapshotOut"]>(`/api/column/articles/${id}`, {
      auth: false,
    })
      .then((res) => {
        if (!cancelled) setItem(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    apiFetch<Schemas["ColumnRankingResponse"]>(
      `/api/column/ranking${buildQuery({ limit: 8 })}`,
      { auth: false }
    )
      .then((res) => {
        if (!cancelled) setRanking(res.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setRanking([]);
      });

    return () => {
      cancelled = true;
    };
  }, [id]);

  const meta = item
    ? EVENT_TYPE_META[item.event_type] || EVENT_TYPE_META.other
    : null;

  return (
    <div className="portal-page">
      <header className="detail-topbar">
        <div className="detail-topbar-inner">
          <Link to="/column" className="detail-back">
            ← 返回神秘新闻首页
          </Link>
          <span style={{ fontSize: 13, color: "#a1a1aa", fontWeight: 600 }}>
            神秘新闻 · 真相揭秘
          </span>
        </div>
      </header>

      <div className="detail-grid">
        <main className="portal-card detail-article-card">
          {loading && <div className="column-empty">正在拆解真相...</div>}
          {error && <div className="column-empty">内容读取失败：{error}</div>}

          {item && meta && (
            <article>
              <div className="detail-meta-bar">
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <span className="cat-badge" style={{ background: meta.color }}>
                    {meta.label}
                  </span>
                  {item.content_nature && (
                    <span className="soft-tag">{item.content_nature}</span>
                  )}
                  {item.style_tag && (
                    <span className="soft-tag">{item.style_tag}</span>
                  )}
                  {item.culture_tag && (
                    <span className="soft-tag culture">{item.culture_tag}</span>
                  )}
                  {item.rank_badge && (
                    <span className={`rank-badge badge-${item.rank_badge}`}>
                      {item.rank_badge}
                    </span>
                  )}
                </div>
                <span className={`source-level-pill level-${item.source_level}`}>
                  {item.source_level} 级权威信源
                </span>
              </div>

              <h1 className="detail-title">{item.title_final}</h1>

              <div className="detail-stats-strip">
                <span style={{ color: "#fb923c", fontWeight: 600 }}>
                  🔥 神秘热度 {item.hot_score ?? 0}
                </span>
                <span>👁 揭秘浏览 {item.view_count ?? 0}</span>
                {(item.cluster_article_count ?? 1) > 1 && (
                  <span style={{ color: "#38bdf8" }}>
                    📡 {item.cluster_article_count} 个独立信源交叉报道
                  </span>
                )}
                <span>发布于 {formatTime(item.published_at)}</span>
              </div>

              {item.disclaimer && (
                <div className="detail-disclaimer">{item.disclaimer}</div>
              )}

              <section className="detail-lead-box">
                <div className="detail-lead-label">悬念一秒破题</div>
                <p className="detail-lead-text">{item.body_lead}</p>
              </section>

              <section className="detail-summary">
                <div
                  style={{
                    fontSize: 12,
                    color: "#a1a1aa",
                    marginBottom: 10,
                    fontWeight: 700,
                    letterSpacing: 0.5,
                  }}
                >
                  事件大白话速览
                </div>
                {item.body_summary}
              </section>

              <section className="detail-source-card">
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                  <strong style={{ color: "#f4f4f5" }}>可核验原始来源</strong>
                  <span style={{ color: "#4ade80", fontWeight: 600 }}>
                    {item.source_level} 级信源 · {item.source_name}
                  </span>
                </div>
                <a
                  href={item.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="detail-source-link"
                >
                  {item.source_url}
                </a>
                <div style={{ fontSize: 12, color: "#71717a" }}>
                  不可变快照版本 v{item.version} · 审核上架时间 {formatTime(item.published_at)}
                </div>
              </section>
            </article>
          )}
        </main>

        {/* 右侧同步展示神秘热榜，方便连续揭秘 */}
        <aside className="portal-right">
          <section className="portal-card rank-card" aria-label="更多神秘热榜">
            <div className="rank-header">
              <div className="rank-title-wrap">
                <span aria-hidden="true">🔥</span>
                <h2 className="rank-title">更多神秘热榜</h2>
              </div>
              <Link to="/column" style={{ fontSize: 12, color: "#ff8200", textDecoration: "none" }}>
                全部 ›
              </Link>
            </div>
            <div className="rank-list">
              {ranking.map((r) => (
                <Link
                  key={r.id}
                  to={`/column/${r.id}`}
                  className="rank-row"
                  style={
                    item && r.id === item.id
                      ? { background: "rgba(255, 130, 0, 0.1)" }
                      : undefined
                  }
                >
                  <span className={`rank-num ${r.rank <= 3 ? `top-${r.rank}` : ""}`}>
                    {r.rank}
                  </span>
                  <div className="rank-content">
                    <div className="rank-item-title">{r.title_final}</div>
                    <div className="rank-item-sub">
                      <span>热度 {r.hot_score}</span>
                      <span>浏览 {r.view_count}</span>
                    </div>
                  </div>
                  {r.rank_badge && (
                    <span className={`rank-badge badge-${r.rank_badge}`}>
                      {r.rank_badge}
                    </span>
                  )}
                </Link>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
