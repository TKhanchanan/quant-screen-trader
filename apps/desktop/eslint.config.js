import eslint from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['out', 'dist', 'coverage'] },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['electron/**/*.ts', '*.ts', 'tests/**/*.ts'],
    languageOptions: {
      globals: globals.node
    }
  },
  {
    files: ['src/renderer/src/**/*.{ts,tsx}'],
    languageOptions: {
      globals: globals.browser
    },
    plugins: {
      'react-hooks': reactHooks
    },
    rules: reactHooks.configs.recommended.rules
  }
)
