import tseslint from '@typescript-eslint/eslint-plugin';
import tsParser from '@typescript-eslint/parser';
export default [{files:['**/*.ts'],languageOptions:{parser:tsParser,parserOptions:{sourceType:'module',ecmaVersion:2022}},plugins:{'@typescript-eslint':tseslint},rules:{'@typescript-eslint/no-explicit-any':'error','no-empty':'off'}}];
