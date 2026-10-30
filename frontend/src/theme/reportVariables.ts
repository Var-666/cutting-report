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
  }
}
