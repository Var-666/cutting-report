import type { GlobalTheme } from 'naive-ui'
// Legacy business pages share the same Naive UI tokens as native controls.
export function reportThemeVariables(theme: NonNullable<GlobalTheme['common']>) {
  return {
    '--text-color-1': theme.textColor1,
    '--text-color-2': theme.textColor2,
    '--text-color-3': theme.textColor3,
    '--theme-border-color': theme.borderColor,
    '--theme-border-radius': theme.borderRadius,
    '--theme-divider-color': theme.dividerColor,
    '--theme-hover-color': theme.hoverColor,
    '--theme-input-color': theme.inputColor,
    '--theme-code-color': theme.codeColor,
    '--theme-popover-color': theme.popoverColor,
    '--theme-table-header-color': theme.tableHeaderColor,
    '--theme-disabled-color': theme.inputColorDisabled,
    '--theme-text-disabled': theme.textColorDisabled,
    '--theme-on-primary-color': theme.baseColor,
    '--theme-primary-hover': theme.primaryColorHover,
    '--theme-primary-pressed': theme.primaryColorPressed,
    '--theme-shadow': theme.boxShadow1,
    '--theme-popover-shadow': theme.boxShadow2,
    // Light mode follows the report design. Dark mode uses the current theme surfaces and accents.
    '--analysis-surface': 'rgb(var(--container-bg-color))',
    '--analysis-panel-bg': 'light-dark(#f4f6fa, color-mix(in srgb, var(--analysis-surface) 55%, rgb(var(--layout-bg-color))))',
    '--analysis-border': 'light-dark(#d0d7e3, var(--theme-border-color))',
    '--analysis-accent': 'light-dark(color-mix(in srgb, #2f54eb 82%, rgb(var(--info-color))), rgb(var(--info-color)))',
    '--analysis-hero-start': 'light-dark(#e6edff, color-mix(in srgb, var(--analysis-accent) 18%, var(--analysis-surface)))',
    '--analysis-hero-end': 'light-dark(#f7f9ff, var(--analysis-surface))',
    '--analysis-shadow': '0 1px 4px light-dark(rgb(60 70 120 / .12), rgb(0 0 0 / .24))',
    '--analysis-error-accent': 'light-dark(#d92d20, rgb(var(--error-color)))',
    '--analysis-error-ink': 'light-dark(#fff, #17191f)',
    '--analysis-warning-accent': 'light-dark(#f79009, rgb(var(--warning-color)))',
    '--analysis-warning-ink': '#231800',
  }
}
