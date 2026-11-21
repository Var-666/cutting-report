import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config'

export default mergeConfig(viteConfig, defineConfig({
  test: {
    include: ['tests/**/*.spec.{ts,tsx,mjs}'],
    restoreMocks: true,
    unstubGlobals: true,
  },
}))
