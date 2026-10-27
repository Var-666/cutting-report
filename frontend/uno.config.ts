import { defineConfig, presetWind3, transformerDirectives, transformerVariantGroup } from 'unocss'
import { presetSoybeanAdmin } from './src/vendor/soybean-admin/packages/uno-preset/index.ts'
import { themeVars } from './src/theme/vars.ts'

export default defineConfig({
  presets: [presetWind3({ dark: 'class' }), presetSoybeanAdmin()],
  transformers: [transformerDirectives(), transformerVariantGroup()],
  theme: {
    ...themeVars,
    fontSize: { 'icon-xs': '.875rem', 'icon-small': '1rem', icon: '1.125rem', 'icon-large': '1.5rem', 'icon-xl': '2rem' },
  },
  shortcuts: {
    'flex-center': 'flex items-center justify-center',
    'flex-y-center': 'flex items-center',
    'flex-col-stretch': 'flex flex-col items-stretch',
    'transition-all-300': 'transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] motion-reduce:transition-none',
    'card-wrapper': 'rd-8px shadow-sm',
  },
})
