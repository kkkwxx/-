import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch, buildQuery, type Schemas } from "../../api/client";
import { EVENT_TYPE_META, formatTime } from "../../utils/labels";
import "../../styles/column.css";

const CATEGORIES: Array<{ key: string; label: string }> = [
  { key: "", label: "全部神秘事件" },
  { key: "deep_space_particle", label: "深空 / 粒子" },
  { key: "neo", label: "近地天体" },
  { key: "exoplanet", label: "新星球" },
  { key: "bci_consciousness", label: "脑机 / 意识" },
  { key: "anomaly_psychic", label: "异常 / 灵能" },
];

type FeedMode = "hot" | "latest" | "high_hook" | "level_a";

const CENTER_TABS: Array<{ key: FeedMode; label: string }> = [
  { key: "hot", label: "热门" },
  { key: "latest", label: "实时" },
  { key: "high_hook", label: "高能悬念" },
  { key: "level_a", label: "权威A源" },
];

function sourceInitial(name: string): string {
  const cleaned = (name || "神").trim();
  return cleaned.slice(0, 1).toUpperCase() || "神";
}

export default function ColumnPage() {
  const navigate = useNavigate();
  const [activeType, setActiveType] = useState("");
  const [feedMode, setFeedMode] = useState<FeedMode>("hot");
  const [searchInput, setSearchInput] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [page, setPage] = useState(1);

  const [items, setItems] = useState<Schemas["PublishedSnapshotOut"][]>([]);
  const [total, setTotal] = useState(0);
  const [countsByType, setCountsByType] = useState<Record<string, number>>({});
  const [totalActive, setTotalActive] = useState(0);
  const [todayPublished, setTodayPublished] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [ranking, setRanking] = useState<Schemas["ColumnRankingItem"][]>([]);
  const [rankLoading, setRankLoading] = useState(true);
  const [rankTick, setRankTick] = useState(0);

  // 中间新闻流加载（受分类、子 Tab、搜索关键词、页码驱动）
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    if (page === 1) {
      setItems([]);
    }

    const queryParams: Record<string, string | number | undefined> = {
      event_type: activeType || undefined,
      q: appliedQuery || undefined,
      sort: feedMode === "latest" ? "latest" : "hot",
      source_level: feedMode === "level_a" ? "A" : undefined,
      min_hook_score: feedMode === "high_hook" ? 4 : undefined,
      page,
      page_size: 20,
    };

    apiFetch<Schemas["ColumnArticleListResponse"]>(
      `/api/column/articles${buildQuery(queryParams)}`,
      { auth: false }
    )
      .then((res) => {
        if (cancelled) return;
        setTotal(res.total);
        setCountsByType(res.counts_by_type ?? {});
        setTotalActive(res.total_active ?? res.total);
        setTodayPublished(res.today_published ?? 0);
        setItems((prev) => (page === 1 ? res.items : [...prev, ...res.items]));
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [activeType, feedMode, appliedQuery, page]);

  // 右侧「神秘热榜」加载（全站综合热度 Top 10）
  useEffect(() => {
    let cancelled = false;
    setRankLoading(true);
    apiFetch<Schemas["ColumnRankingResponse"]>(
      `/api/column/ranking${buildQuery({ limit: 10 })}`,
      { auth: false }
    )
      .then((res) => {
        if (!cancelled) setRanking(res.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setRanking([]);
      })
      .finally(() => {
        if (!cancelled) setRankLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [rankTick]);

  const selectCategory = useCallback((catKey: string) => {
    setActiveType((prev) => {
      if (prev !== catKey) setPage(1);
      return catKey;
    });
  }, []);

  const selectFeedMode = useCallback((mode: FeedMode) => {
    setFeedMode((prev) => {
      if (prev !== mode) setPage(1);
      return mode;
    });
  }, []);

  const handleQuickShortcut = (mode: FeedMode) => {
    setActiveType("");
    setFeedMode(mode);
    setPage(1);
  };

  const handleSearchSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = searchInput.trim();
    if (trimmed !== appliedQuery) {
      setAppliedQuery(trimmed);
      setPage(1);
    }
  };

  const handleClearSearch = () => {
    setSearchInput("");
    if (appliedQuery) {
      setAppliedQuery("");
      setPage(1);
    }
  };

  return (
    <div className="portal-page">
      {/* 顶部深空地球夜景 Banner + 居中品牌名与搜索栏 */}
      <header className="portal-hero">
        <div className="portal-hero-inner">
          <Link
            to="/column"
            className="portal-brand"
            onClick={() => {
              setActiveType("");
              setFeedMode("hot");
              handleClearSearch();
            }}
          >
            <span className="portal-brand-logo" aria-hidden="true">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="9" stroke="#fff" strokeWidth="1.8" />
                <ellipse cx="12" cy="12" rx="11" ry="4.2" transform="rotate(-24 12 12)" stroke="#fff" strokeWidth="1.6" />
                <circle cx="12" cy="12" r="2.6" fill="#fff" />
              </svg>
            </span>
            <div className="portal-brand-text">
              <h1 className="portal-brand-title">神秘新闻</h1>
              <span className="portal-brand-sub">标题负责勾人 · 正文负责说清楚</span>
            </div>
          </Link>

          <form className="portal-search-form" onSubmit={handleSearchSubmit} role="search">
            <span className="portal-search-icon" aria-hidden="true">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none">
                <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
                <path d="M20 20L16.5 16.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </span>
            <input
              type="search"
              className="portal-search-input"
              placeholder="搜索神秘事件、深空信号、小行星、脑机接口或来源..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              aria-label="搜索神秘新闻"
            />
            {(searchInput || appliedQuery) && (
              <button
                type="button"
                className="portal-search-clear"
                onClick={handleClearSearch}
                aria-label="清空搜索"
                title="清空搜索"
              >
                ×
              </button>
            )}
            <button type="submit" className="portal-search-btn">
              搜索
            </button>
          </form>
        </div>
      </header>

      {/* 移动端/平板折叠分类横条 */}
      <nav className="portal-mobile-cats" aria-label="移动端分类导航">
        {CATEGORIES.map((c) => (
          <button
            key={c.key || "all"}
            type="button"
            className={`mobile-cat-pill ${activeType === c.key ? "active" : ""}`}
            onClick={() => selectCategory(c.key)}
          >
            {c.label}
          </button>
        ))}
      </nav>

      {/* 三栏主体布局：左分类 · 中新闻流 · 右入口卡与神秘热榜 */}
      <div className="portal-container">
        {/* 左栏：快捷入口 + 5 大神秘分类 */}
        <aside className="portal-card portal-left" aria-label="栏目分类导航">
          <div className="left-nav-group">
            <button
              type="button"
              className={`left-nav-btn ${activeType === "" && feedMode === "hot" ? "active" : ""}`}
              onClick={() => handleQuickShortcut("hot")}
            >
              <span className="left-nav-label">
                <span aria-hidden="true">🔥</span>
                <span>热门推荐</span>
              </span>
            </button>
            <button
              type="button"
              className={`left-nav-btn ${activeType === "" && feedMode === "latest" ? "active" : ""}`}
              onClick={() => handleQuickShortcut("latest")}
            >
              <span className="left-nav-label">
                <span aria-hidden="true">⏱</span>
                <span>最新揭秘</span>
              </span>
            </button>
            <button
              type="button"
              className={`left-nav-btn ${activeType === "" && feedMode === "level_a" ? "active" : ""}`}
              onClick={() => handleQuickShortcut("level_a")}
            >
              <span className="left-nav-label">
                <span aria-hidden="true">🛡</span>
                <span>权威 A 源</span>
              </span>
            </button>
          </div>

          <div className="left-nav-divider" />
          <div className="left-nav-section-title">神秘探索频道</div>

          <div className="left-nav-group">
            {CATEGORIES.map((c) => {
              const count = c.key === "" ? totalActive : (countsByType[c.key] ?? 0);
              const isActive = activeType === c.key;
              return (
                <button
                  key={c.key || "all"}
                  type="button"
                  className={`left-nav-btn ${isActive ? "active" : ""}`}
                  onClick={() => selectCategory(c.key)}
                >
                  <span className="left-nav-label">
                    <span className="left-nav-dot" />
                    <span>{c.label}</span>
                  </span>
                  <span className="left-nav-count">{count}</span>
                </button>
              );
            })}
          </div>
        </aside>

        {/* 中栏：顶部子 Tab + 纯悬念新闻卡片流 */}
        <main className="portal-center">
          <div className="portal-card center-tabs-bar">
            <div className="center-tabs" role="tablist" aria-label="新闻排序筛选">
              {CENTER_TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  role="tab"
                  aria-selected={feedMode === t.key}
                  className={`center-tab ${feedMode === t.key ? "active" : ""}`}
                  onClick={() => selectFeedMode(t.key)}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <span className="center-total-hint">共 {total} 条线索</span>
          </div>

          {appliedQuery && (
            <div className="search-active-banner">
              <span>
                搜索关键词「<strong>{appliedQuery}</strong>」共找到 {total} 条相关神秘事件
              </span>
              <button
                type="button"
                className="search-reset-btn"
                onClick={handleClearSearch}
              >
                清除搜索
              </button>
            </div>
          )}

          {loading && items.length === 0 && (
            <div className="portal-card column-empty">正在接收来自深空的信号...</div>
          )}
          {error && (
            <div className="portal-card column-empty">信号连接异常：{error}</div>
          )}
          {!loading && !error && items.length === 0 && (
            <div className="portal-card column-empty">
              {appliedQuery
                ? `未找到与「${appliedQuery}」相关的神秘事件，试试换个关键词或清空搜索。`
                : "当前筛选条件下暂无最新神秘事件，请切换其他频道看看。"}
            </div>
          )}

          <div className="feed-list">
            {items.map((item) => {
              const meta = EVENT_TYPE_META[item.event_type] || EVENT_TYPE_META.other;
              const clusterN = item.cluster_article_count ?? 1;
              return (
                <Link
                  key={item.id}
                  to={`/column/${item.id}`}
                  className="feed-card"
                >
                  <div className="feed-card-head">
                    <div className="feed-source-box">
                      <span
                        className="feed-avatar"
                        style={{ background: meta.color }}
                        aria-hidden="true"
                      >
                        {sourceInitial(item.source_name)}
                      </span>
                      <div className="feed-source-meta">
                        <div className="feed-source-row">
                          <span className="feed-source-name">{item.source_name}</span>
                          <span className={`source-level-pill level-${item.source_level}`}>
                            {item.source_level} 级源
                          </span>
                        </div>
                        <span className="feed-time">
                          发布于 {formatTime(item.published_at)}
                        </span>
                      </div>
                    </div>

                    <div className="feed-tags">
                      <span className="cat-badge" style={{ background: meta.color }}>
                        {meta.short}
                      </span>
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
                  </div>

                  {/* 纯悬念标题流（零剧透，点击进入详情页揭秘） */}
                  <h2 className="feed-title">{item.title_final}</h2>

                  <div className="feed-footer">
                    <div className="feed-metrics">
                      <span className="feed-metric hot" title="综合神秘热度值">
                        🔥 热度 {item.hot_score ?? 0}
                      </span>
                      <span className="feed-metric" title="详情页揭秘浏览次数">
                        👁 浏览 {item.view_count ?? 0}
                      </span>
                      {clusterN > 1 && (
                        <span className="feed-metric cluster" title="多信源交叉报道数">
                          📡 {clusterN} 源共报
                        </span>
                      )}
                    </div>
                    <span className="feed-cta">点击揭秘 ›</span>
                  </div>
                </Link>
              );
            })}
          </div>

          {!loading && items.length < total && (
            <div className="load-more-wrap">
              <button
                type="button"
                className="load-more-btn"
                onClick={() => setPage((p) => p + 1)}
              >
                加载更多（已显示 {items.length} / {total}）
              </button>
            </div>
          )}
        </main>

        {/* 右栏：栏目探索卡 + 神秘热榜 Top 10（纯读者视图，无任何后台入口） */}
        <aside className="portal-right">
          <section className="promo-card" aria-label="栏目探索概览">
            <h2 className="promo-title">发现深空未解线索</h2>
            <p className="promo-desc">
              聚焦深空粒子、近地天体、系外行星、脑机意识与前沿异象——标题负责勾人，正文负责说清楚。
            </p>
            <div className="promo-stats">
              <div className="promo-stat">
                <div className="promo-stat-val">{totalActive}</div>
                <div className="promo-stat-label">当前在展</div>
              </div>
              <div className="promo-stat">
                <div className="promo-stat-val">{todayPublished}</div>
                <div className="promo-stat-label">今日上新</div>
              </div>
            </div>
            <button
              type="button"
              className="promo-btn"
              style={{ border: "none", cursor: "pointer" }}
              disabled={ranking.length === 0 && items.length === 0}
              onClick={() => {
                const pool = ranking.length > 0 ? ranking : items;
                if (pool.length === 0) return;
                const pick = pool[Math.floor(Math.random() * pool.length)];
                navigate(`/column/${pick.id}`);
              }}
            >
              随机揭秘一条线索
            </button>
          </section>

          <section className="portal-card rank-card" aria-label="神秘热榜">
            <div className="rank-header">
              <div className="rank-title-wrap">
                <span aria-hidden="true">🔥</span>
                <h2 className="rank-title">神秘热榜</h2>
              </div>
              <button
                type="button"
                className="rank-refresh-btn"
                onClick={() => setRankTick((t) => t + 1)}
                title="刷新榜单"
              >
                ↻ 刷新
              </button>
            </div>

            <div className="rank-formula-hint">
              综合跨源热度 · 悬念分 · A/B权威源 · 真实浏览 · 时效
            </div>

            {rankLoading && ranking.length === 0 ? (
              <div className="column-empty" style={{ padding: "24px 0" }}>
                榜单计算中...
              </div>
            ) : ranking.length === 0 ? (
              <div className="column-empty" style={{ padding: "24px 0" }}>
                暂无上榜线索
              </div>
            ) : (
              <div className="rank-list">
                {ranking.map((r) => (
                  <Link
                    key={r.id}
                    to={`/column/${r.id}`}
                    className="rank-row"
                    title={r.title_final}
                  >
                    <span
                      className={`rank-num ${r.rank <= 3 ? `top-${r.rank}` : ""}`}
                    >
                      {r.rank}
                    </span>
                    <div className="rank-content">
                      <div className="rank-item-title">{r.title_final}</div>
                      <div className="rank-item-sub">
                        <span>热度 {r.hot_score}</span>
                        <span>浏览 {r.view_count}</span>
                        {(r.cluster_article_count ?? 1) > 1 && (
                          <span>{r.cluster_article_count}源</span>
                        )}
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
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
