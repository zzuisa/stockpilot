import type { GlobalThemeOverrides } from 'naive-ui'

// 沿用原终端交易风：深色底 + 琥珀强调色
export const palette = {
  bg: '#0C1117',
  panel: '#121922',
  panel2: '#0F151D',
  line: '#1E2933',
  line2: '#2A3845',
  amber: '#E8A33D',
  amberDim: '#8A6526',
  up: '#3DD68C',
  down: '#F4604E',
  blue: '#5BA8F5',
  text: '#D9E0E8',
  muted: '#7C8896',
  faint: '#54616E',
} as const

const mono = "'IBM Plex Mono', monospace"
const sans = "'IBM Plex Sans', 'Noto Sans SC', sans-serif"

export const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: palette.amber,
    primaryColorHover: '#F0B45C',
    primaryColorPressed: '#D6922F',
    primaryColorSuppl: palette.amber,
    successColor: palette.up,
    errorColor: palette.down,
    infoColor: palette.blue,
    warningColor: palette.amber,
    textColorBase: palette.text,
    textColor1: palette.text,
    textColor2: palette.text,
    textColor3: palette.muted,
    bodyColor: palette.bg,
    cardColor: palette.panel,
    modalColor: palette.panel,
    popoverColor: palette.panel,
    borderColor: palette.line,
    dividerColor: palette.line,
    inputColor: palette.panel2,
    tableColor: palette.panel,
    tableHeaderColor: palette.panel,
    fontFamily: sans,
    fontFamilyMono: mono,
    borderRadius: '6px',
    baseColor: palette.bg,
    hoverColor: 'rgba(255,255,255,.04)',
  },
  Card: {
    color: palette.panel,
    borderColor: palette.line,
    titleFontSizeMedium: '13px',
    paddingMedium: '14px',
  },
  DataTable: {
    thColor: palette.panel,
    tdColor: palette.panel,
    tdColorHover: 'rgba(255,255,255,.03)',
    borderColor: palette.line,
    thTextColor: palette.faint,
    tdTextColor: palette.text,
    thFontWeight: '500',
    fontSizeMedium: '13px',
  },
  Tabs: {
    tabTextColorActiveLine: palette.amber,
    tabTextColorHoverLine: palette.text,
    barColor: palette.amber,
    tabFontWeightActive: '600',
  },
  Tag: {
    borderRadius: '4px',
  },
  Button: {
    textColorPrimary: palette.bg,
  },
  Statistic: {
    valueFontSize: '22px',
  },
}
