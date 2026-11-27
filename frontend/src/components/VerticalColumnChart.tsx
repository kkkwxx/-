import { useEffect, useId, useMemo, useRef, useState } from "react";

export type VColDatum = {
  key: string;
  label: string;
  value: number;
  /** Tooltip 与柱下标签展示的附加指标（如占比） */
  extra?: { label: string; value: string };
};

type VerticalColumnChartProps = {
  data: VColDatum[];
  title?: string;
  subtitle?: string;
  /** 时间范围选择器（不传则不渲染） */
  ranges?: { key: string; label: string }[];
  range?: string;
  onRangeChange?: (key: string) => void;
  /** 图例文案，不传则不渲染图例 */
  legend?: string;
  selectedKey?: string | null;
  onSelect?: (key: string) => void;
  height?: number;
  valueFormatter?: (value: number) => string;
};

/*
 * 版式常量（对齐参考图：竖柱、柱底渐隐、下方两级标签、无网格线）
 * 参考图实测：柱宽 43 / 间距 9（宽:节距 ≈ 0.83）、顶角微圆、无轴线
 */
const PLOT_HEIGHT = 170;
const TOP_PAD = 16;
const LABEL_BLOCK = 54;
const BAR_MAX_W = 64;
const BAR_W_RATIO = 0.62;
const BAR_MIN_H = 3;

/** 只有顶部两角圆角、底部贴基线的柱子路径 */
function topRoundedPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  return [
    `M${x},${y + h}`,
    `L${x},${y + rr}`,
    `Q${x},${y} ${x + rr},${y}`,
    `L${x + w - rr},${y}`,
    `Q${x + w},${y} ${x + w},${y + rr}`,
    `L${x + w},${y + h}`,
    "Z",
  ].join(" ");
}

/**
 * 竖向柱状图（参考图样式，零依赖 SVG）
 * - 柱体：浅绿竖向渐变，柱顶最绿、柱底渐隐到近白，顶角微圆
 * - 无网格线 / 无坐标轴（数值直接印在柱下，保证数据可读且不引入视觉噪音）
 * - 柱下两行标签：栏目名 + 「数值 · 占比」
 * - hover 高亮柱底衬块 + Tooltip；点击选中；宽度随容器自适应；数据全部由 props 驱动
 */
export default function VerticalColumnChart({
  data,
  title,
  subtitle,
  ranges,
  range,
  onRangeChange,
  legend,
  selectedKey,
  onSelect,
  height,
  valueFormatter = (v) => v.toLocaleString(),
}: VerticalColumnChartProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(720);
  const [innerSelected, setInnerSelected] = useState<string | null>(data[0]?.key ?? null);
  const [hover, setHover] = useState<number | null>(null);
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const apply = () => setWidth(Math.max(280, el.clientWidth));
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const selected = selectedKey !== undefined ? selectedKey : innerSelected;
  const canvasHeight = height ?? TOP_PAD + PLOT_HEIGHT + LABEL_BLOCK;
  const plotHeight = canvasHeight - TOP_PAD - LABEL_BLOCK;

  const layout = useMemo(() => {
    const n = Math.max(1, data.length);
    const band = width / n;
    const barW = Math.min(BAR_MAX_W, Math.max(16, band * BAR_W_RATIO));
    const maxValue = Math.max(1, ...data.map((d) => d.value));
    const baseline = TOP_PAD + plotHeight;
    const total = data.reduce((sum, d) => sum + d.value, 0);

    const cols = data.map((d, i) => {
      const bandX = i * band;
      const rawH = (d.value / maxValue) * plotHeight;
      const h = d.value <= 0 ? 0 : Math.max(BAR_MIN_H, rawH);
      return {
        datum: d,
        index: i,
        bandX,
        bandW: band,
        x: bandX + (band - barW) / 2,
        barW,
        h,
        barY: baseline - h,
        centerX: bandX + band / 2,
        share: total > 0 ? (d.value / total) * 100 : 0,
        baseline,
      };
    });

    return { band, barW, cols, baseline, plotHeight, total };
  }, [width, data, plotHeight]);

  const { cols, baseline, band } = layout;
  const activeCol = hover !== null ? cols[hover] : null;

  const handleSelect = (key: string) => {
    if (selectedKey === undefined) setInnerSelected(key);
    onSelect?.(key);
  };

  // Tooltip 水平位置夹在画布内，避免贴边溢出；竖直方向保证整块留在画布内（高的柱子也不会顶出卡片）
  const tipLeft = activeCol
    ? Math.min(Math.max(activeCol.centerX, 70), Math.max(70, width - 70))
    : 0;
  const tipTop = activeCol ? Math.max(56, activeCol.barY - 10) : 0;

  return (
    <div className="mn-vcol" ref={wrapRef}>
      {(title || ranges?.length) && (
        <div className="mn-vcol-head">
          <div>
            {title && <div className="mn-vcol-title">{title}</div>}
            {subtitle && <div className="mn-vcol-sub">{subtitle}</div>}
          </div>
          <div className="mn-vcol-head-right">
            {legend && (
              <span className="mn-vcol-legend">
                <span className="mn-vcol-swatch" />
                {legend}
              </span>
            )}
            {ranges && ranges.length > 0 && (
              <div className="mn-vcol-ranges">
                {ranges.map((r) => (
                  <button
                    key={r.key}
                    type="button"
                    className={`mn-vcol-range${r.key === range ? " is-active" : ""}`}
                    onClick={() => onRangeChange?.(r.key)}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="mn-vcol-canvas">
        <svg
          className="mn-vcol-svg"
          width="100%"
          height={canvasHeight}
          viewBox={`0 0 ${width} ${canvasHeight}`}
          role="img"
          aria-label={title ?? "竖向柱状图"}
        >
          <defs>
            {/* 整幅图共用一条竖向渐变：柱越高越接近顶部的高饱和绿，柱底统一渐隐到近白 */}
            <linearGradient
              id={`${uid}-fill`}
              gradientUnits="userSpaceOnUse"
              x1="0"
              y1={baseline - plotHeight}
              x2="0"
              y2={baseline}
            >
              <stop offset="0%" stopColor="#aee680" />
              <stop offset="45%" stopColor="#caf0ae" />
              <stop offset="100%" stopColor="#e2f5cf" />
            </linearGradient>
            <linearGradient
              id={`${uid}-fill-active`}
              gradientUnits="userSpaceOnUse"
              x1="0"
              y1={baseline - plotHeight}
              x2="0"
              y2={baseline}
            >
              <stop offset="0%" stopColor="#122e28" />
              <stop offset="100%" stopColor="#3a5a51" />
            </linearGradient>
          </defs>

          {cols.map((col) => {
            const isActive = col.datum.key === selected;
            const isHover = hover === col.index;
            const fill = isActive ? `url(#${uid}-fill-active)` : `url(#${uid}-fill)`;
            return (
              <g
                key={col.datum.key}
                className={`mn-vcol-col${isActive ? " is-active" : ""}${isHover ? " is-hover" : ""}`}
                onClick={() => handleSelect(col.datum.key)}
                onMouseEnter={() => setHover(col.index)}
                onMouseLeave={() => setHover((h) => (h === col.index ? null : h))}
                tabIndex={0}
                role="button"
                aria-label={`${col.datum.label} ${valueFormatter(col.datum.value)} 占比 ${col.share.toFixed(1)}%`}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    handleSelect(col.datum.key);
                  }
                }}
              >
                {/* 选中 / 悬浮的柱底衬块 */}
                {(isActive || isHover) && (
                  <rect
                    className="mn-vcol-band"
                    x={col.bandX + band * 0.09}
                    y={TOP_PAD - 4}
                    width={band * 0.82}
                    height={plotHeight + 8}
                    rx={10}
                  />
                )}

                {/* 柱子本体 */}
                {col.h > 0 && (
                  <path
                    className="mn-vcol-bar"
                    style={{ animationDelay: `${col.index * 50}ms` }}
                    d={topRoundedPath(col.x, col.barY, col.barW, col.h, 6)}
                    fill={fill}
                  />
                )}

                {/* 第一行：栏目名 */}
                <text
                  x={col.centerX}
                  y={baseline + 21}
                  textAnchor="middle"
                  className={`mn-vcol-label${isActive ? " is-active" : ""}`}
                >
                  {col.datum.label}
                </text>

                {/* 第二行：数值 · 占比（数据必须可读，不依赖 Tooltip） */}
                <text x={col.centerX} y={baseline + 39} textAnchor="middle">
                  <tspan className={`mn-vcol-num${isActive ? " is-active" : ""}`}>
                    {valueFormatter(col.datum.value)}
                  </tspan>
                  {col.datum.extra && width >= 520 && (
                    <tspan className="mn-vcol-share" dx="5">
                      {col.datum.extra.value}
                    </tspan>
                  )}
                </text>
              </g>
            );
          })}
        </svg>

        {activeCol && (
          <div
            className="mn-vcol-tip"
            style={{ left: tipLeft, top: tipTop }}
          >
            <div className="mn-vcol-tip-title">{activeCol.datum.label}</div>
            <div className="mn-vcol-tip-row">
              <span>数值</span>
              <strong>{valueFormatter(activeCol.datum.value)}</strong>
            </div>
            {activeCol.datum.extra && (
              <div className="mn-vcol-tip-row">
                <span>{activeCol.datum.extra.label}</span>
                <strong>{activeCol.datum.extra.value}</strong>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}