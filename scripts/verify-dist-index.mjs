/**
 * Uretim dist/index.html'in gelistirme /src/main.tsx referansi tasimadigini dogrular.
 * `npm run build` zincirinin sonunda calisir.
 */
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..")
const htmlPath = path.join(root, "dist", "index.html")

if (!fs.existsSync(htmlPath)) {
  console.error("verify-dist-index: dist/index.html yok — once vite build tamamlanmali.")
  process.exit(1)
}

const html = fs.readFileSync(htmlPath, "utf8")

if (html.includes("src/main.tsx") || html.includes('"/src/') || html.includes("'/src/")) {
  console.error(
    "verify-dist-index: dist/index.html hala /src/... iceriyor — bu dosya yayinlanmamali; Vite build bozuk veya yanlis index kopyalanmis.",
  )
  process.exit(1)
}

if (!/\bassets\/[^"']+\.(js|mjs|css)/.test(html)) {
  console.error("verify-dist-index: dist/index.html icinde assets/*.js veya *.css bulunamadi.")
  process.exit(1)
}

console.log("verify-dist-index: OK (uretim index.html)")
