import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

// Committed ESLint configuration: Next.js core-web-vitals rules in flat-config
// form (no interactive `next lint` step, CI-safe via `npm run lint`).
const eslintConfig = [
  ...compat.extends("next/core-web-vitals"),
  {
    ignores: ["node_modules/**", ".next/**", "output/**", "web/**"],
  },
];

export default eslintConfig;
