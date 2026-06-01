/**
 * React `src/i18n/uiStrings.ts` → `php-ui/locale/strings.json` (PHP i18n).
 * Gelistirme: npm run export-php-locale
 */
import { execFileSync } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..")
const tmp = path.join(root, "node_modules", ".cache", "ab-locale-export.mjs")

fs.mkdirSync(path.dirname(tmp), { recursive: true })
execFileSync(
  "npx",
  ["esbuild", "src/i18n/uiStrings.ts", "--bundle", "--format=esm", "--platform=neutral", `--outfile=${tmp}`],
  { cwd: root, stdio: "inherit" },
)

const mod = await import(pathToFileURL(tmp).href + "?t=" + Date.now())
const UI_STRINGS = mod.UI_STRINGS
if (!UI_STRINGS) {
  console.error("export-php-locale: UI_STRINGS missing")
  process.exit(1)
}
const outDir = path.join(root, "php-ui", "locale")
fs.mkdirSync(outDir, { recursive: true })
fs.writeFileSync(path.join(outDir, "strings.json"), JSON.stringify(UI_STRINGS), "utf8")
console.log("Wrote php-ui/locale/strings.json")
