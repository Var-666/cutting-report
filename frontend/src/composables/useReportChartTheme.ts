import { computed, watch } from 'vue'
import { useThemeVars } from 'naive-ui'
import { useThemeStore } from '../store/modules/theme'

export function useReportChartTheme(redraw: () => void) {
  const theme = useThemeStore()
  const vars = useThemeVars()
  const darkMode = computed(() => theme.darkMode)
  const colors = computed(() => ({ primary: vars.value.primaryColor, secondary: vars.value.successColor, accent: vars.value.warningColor }))
  // Like Soybean useEcharts, recreate instances when the theme changes.
  watch([darkMode, vars], redraw, { flush: 'post' })
  return { darkMode, colors, vars }
}
