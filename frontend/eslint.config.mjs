import { defineConfig, globalIgnores } from 'eslint/config'
import tseslint from 'typescript-eslint'
import vueParser from 'vue-eslint-parser'

export default defineConfig(
  globalIgnores(['dist/', 'src/api/generated/', 'src/vendor/']),
  {
    files: ['src/**/*.ts', 'src/**/*.vue', 'vite.config.ts', 'vitest.config.ts', 'uno.config.ts'],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        project: ['./tsconfig.json', './tsconfig.node.json'],
        tsconfigRootDir: import.meta.dirname,
        extraFileExtensions: ['.vue'],
      },
    },
    plugins: { '@typescript-eslint': tseslint.plugin },
    rules: {
      '@typescript-eslint/no-unnecessary-condition': 'error',
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-floating-promises': ['error', { ignoreVoid: false, checkThenables: true }],
      '@typescript-eslint/only-throw-error': ['error', {
        allowThrowingAny: false, allowThrowingUnknown: false, allowRethrowing: false,
      }],
      'no-empty': ['error', { allowEmptyCatch: false }],
    },
  },
  {
    files: ['src/**/*.vue'],
    languageOptions: {
      parser: vueParser,
      parserOptions: { parser: tseslint.parser },
    },
  },
  {
    files: ['src/utils/**/*.ts'],
    ignores: ['src/utils/storage.ts'], // Browser storage is an external I/O boundary.
    rules: {
      'no-restricted-syntax': ['error', {
        selector: 'TryStatement',
        message: 'Let failures propagate to the caller at the application boundary.',
      }],
    },
  },
)
