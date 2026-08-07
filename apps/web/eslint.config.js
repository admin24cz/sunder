import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';
import prettier from 'eslint-config-prettier';

export default tseslint.config(
  { ignores: ['dist', 'coverage', 'playwright-report', 'test-results'] },
  {
    extends: [
      js.configs.recommended,
      // Type-aware linting. Costs a slower lint run and buys rules that can
      // actually see through a type, which is the point of choosing TypeScript
      // for the statistics and segment maths in the first place.
      ...tseslint.configs.strictTypeChecked,
      ...tseslint.configs.stylisticTypeChecked,
    ],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      parserOptions: {
        project: ['./tsconfig.app.json', './tsconfig.node.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // Spec section 16: no `any` without a justifying comment. An error rather
      // than a warning, so it has to be silenced deliberately with an eslint
      // comment that says why — which is exactly the justification the spec asks
      // for, recorded at the site rather than in a review thread.
      '@typescript-eslint/no-explicit-any': 'error',

      // Unused variables are an error, but an underscore prefix opts out — the
      // usual escape hatch for a deliberately ignored callback argument.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
  // Prettier last: it turns off every stylistic rule the formatter owns.
  prettier,
);
