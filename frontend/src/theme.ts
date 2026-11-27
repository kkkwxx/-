import type { ThemeConfig } from "antd";

/**
 * 后台统一主题：中性灰 + 青柠绿（配色板实测 5 色）
 * 与 styles/admin.css 中的 --mn-* 令牌保持一致，改色只需同步这两处。
 *
 * 配色板只有 5 个色相：#AEE680 青柠绿 / #122E28 深墨绿 / #7A7A7A 中灰 /
 * #FEFEFE 近白 / #E6E6E6 画布灰；其余颜色只能由这 5 色加白/加墨派生。
 *
 * 用法约定：
 * - accent（#aee680）只做**实底填充**（按钮、选中胶囊、图表条、图标 chip），
 *   其上文字/图标一律用 accentInk（深墨绿 #122e28，9.97:1）
 * - primaryInk（#3d6b12）用于**可读的绿色文字/链接**（白底 6.3:1）
 */
export const MN = {
  /* 基础色板（与配色板一一对应，只有这 5 色） */
  lime: "#aee680",
  ink: "#122e28",
  gray: "#7a7a7a",
  white: "#fefefe",
  canvas: "#e6e6e6",

  /* 派生 */
  limeSoft: "#eef9e5",
  limeHover: "#97ca73",
  limeDeep: "#7faf66",
  limeInk: "#3d6b12",
  inkSoft: "#1d3a33",
  ink2: "#4d625e",
  gray1: "#ececec",
  gray2: "#e0e0e0",
  gray3: "#cccccc",

  /* 语义 */
  bg: "#e6e6e6",
  surface: "#fefefe",
  surfaceMuted: "#f2f2f2",
  border: "#e0e0e0",
  borderSubtle: "#ececec",
  text: "#122e28",
  textSecondary: "#4d625e",
  textTertiary: "#7a7a7a",

  accent: "#aee680",
  accentSoft: "#eef9e5",
  accentHover: "#97ca73",
  accentInk: "#122e28",
  primaryInk: "#3d6b12",
  primaryInkHover: "#2f5610",

  /* 侧栏：底色 #f0f0f0，选中项纯白卡 + 深绿字（参考图样式） */
  siderBg: "#f0f0f0",
  siderHover: "#f7f7f7",
  siderSelectedBg: "#fefefe",
  siderSelectedInk: "#122e28",

  success: "#2f6b3c",
  warning: "#8a5a00",
  danger: "#9f1d16",
} as const;

const fontFamily = [
  "-apple-system",
  "BlinkMacSystemFont",
  '"PingFang SC"',
  '"Microsoft YaHei"',
  '"Segoe UI"',
  "Roboto",
  "sans-serif",
].join(", ");

export const adminTheme: ThemeConfig = {
  token: {
    colorPrimary: MN.accent,
    colorInfo: MN.accent,
    colorSuccess: MN.success,
    colorWarning: MN.warning,
    colorError: MN.danger,
    colorLink: MN.primaryInk,
    /* 实底主色上的文字/图标颜色（浅绿底必须用深绿墨，否则不可读） */
    colorTextLightSolid: MN.accentInk,

    colorTextBase: MN.text,
    colorText: MN.text,
    colorTextSecondary: MN.textSecondary,
    colorTextTertiary: MN.textTertiary,
    colorTextQuaternary: "#a8a8a8",

    colorBgLayout: MN.bg,
    colorBgContainer: MN.surface,
    colorBgElevated: MN.surface,
    colorFillQuaternary: MN.surfaceMuted,
    colorFillTertiary: "#f2f2f2",

    colorBorder: MN.border,
    colorBorderSecondary: MN.borderSubtle,

    borderRadius: 8,
    borderRadiusLG: 10,
    borderRadiusSM: 6,
    borderRadiusXS: 4,

    controlHeight: 34,
    controlHeightSM: 28,
    controlHeightLG: 40,

    fontSize: 13,
    fontSizeLG: 14,
    fontFamily,

    lineWidth: 1,
    wireframe: false,
    motionDurationMid: "0.16s",
  },
  components: {
    Layout: {
      bodyBg: MN.bg,
      headerBg: MN.surface,
      headerHeight: 56,
      headerPadding: "0 20px",
      siderBg: MN.siderBg,
      triggerBg: MN.surfaceMuted,
      triggerColor: MN.textSecondary,
    },
    Menu: {
      itemBg: "transparent",
      subMenuItemBg: "transparent",
      itemColor: MN.textSecondary,
      itemHoverColor: MN.text,
      /* hover 只比栏底亮一档（#f7f7f7），绝不能等于"选中=纯白" */
      itemHoverBg: MN.siderHover,
      /* 选中项：纯白底 + 深绿字（参考图样式） */
      itemSelectedColor: MN.siderSelectedInk,
      itemSelectedBg: MN.siderSelectedBg,
      itemActiveBg: MN.siderSelectedBg,
      itemHeight: 42,
      itemMarginInline: 10,
      itemMarginBlock: 3,
      itemBorderRadius: 9,
      iconSize: 16,
      iconMarginInlineEnd: 12,
      activeBarWidth: 0,
      activeBarBorderWidth: 0,
      collapsedIconSize: 16,
    },
    Card: {
      headerBg: MN.surface,
      headerHeight: 44,
      headerFontSize: 13,
      paddingLG: 16,
      boxShadowTertiary: "none",
    },
    Table: {
      headerBg: MN.surfaceMuted,
      headerColor: MN.textSecondary,
      headerSplitColor: "transparent",
      headerBorderRadius: 8,
      borderColor: MN.borderSubtle,
      rowHoverBg: MN.limeSoft,
      cellPaddingBlock: 11,
      cellPaddingInline: 12,
      footerBg: MN.surfaceMuted,
    },
    Button: {
      primaryShadow: "none",
      defaultShadow: "none",
      dangerShadow: "none",
      fontWeight: 500,
      defaultBorderColor: MN.border,
      defaultColor: MN.text,
      paddingInline: 14,
    },
    Input: {
      activeShadow: "0 0 0 3px rgba(174, 230, 128, 0.38)",
      hoverBorderColor: MN.gray3,
      activeBorderColor: MN.limeDeep,
      paddingInline: 10,
    },
    Select: {
      optionSelectedBg: MN.accentSoft,
      optionActiveBg: MN.surfaceMuted,
      optionSelectedColor: MN.accentInk,
      optionSelectedFontWeight: 500,
    },
    Tabs: {
      inkBarColor: MN.ink,
      itemColor: MN.textSecondary,
      itemHoverColor: MN.text,
      itemSelectedColor: MN.text,
      titleFontSize: 13,
      horizontalItemPadding: "8px 0",
      horizontalItemGutter: 22,
    },
    Tag: {
      defaultBg: MN.surfaceMuted,
      defaultColor: MN.textSecondary,
    },
    Descriptions: {
      labelBg: MN.surfaceMuted,
      titleMarginBottom: 10,
      itemPaddingBottom: 10,
    },
    Statistic: {
      titleFontSize: 12,
      contentFontSize: 24,
    },
    Progress: {
      defaultColor: MN.accent,
      remainingColor: MN.gray1,
    },
    Modal: {
      titleFontSize: 14,
      headerBg: MN.surface,
      contentBg: MN.surface,
    },
    Form: {
      labelColor: MN.textSecondary,
      labelFontSize: 12,
      itemMarginBottom: 18,
      verticalLabelPadding: "0 0 6px",
    },
    Alert: {
      defaultPadding: "9px 14px",
      withDescriptionPadding: "14px 16px",
    },
  },
};

export default adminTheme;