#!/usr/bin/env node
/**
 * sync-i18n.js — Copies shared/locales/* to both frontend and mobile.
 *
 * Source:  shared/locales/<lang>/<namespace>.json
 * Targets:
 *   - frontend/public/locales/<lang>/<namespace>.json   (loaded via i18next-http-backend)
 *   - mobile/src/i18n/locales/<lang>/<namespace>.json   (bundled via require)
 *
 * Run: node scripts/sync-i18n.js
 */

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const SOURCE = path.join(ROOT, "shared", "locales");
const TARGETS = [
  path.join(ROOT, "frontend", "public", "locales"),
  path.join(ROOT, "mobile", "src", "i18n", "locales"),
];

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function copyDir(src, dst) {
  ensureDir(dst);
  let count = 0;
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const dstPath = path.join(dst, entry.name);
    if (entry.isDirectory()) {
      count += copyDir(srcPath, dstPath);
    } else if (entry.isFile() && entry.name.endsWith(".json")) {
      fs.copyFileSync(srcPath, dstPath);
      count += 1;
    }
  }
  return count;
}

function main() {
  if (!fs.existsSync(SOURCE)) {
    console.error(`[sync-i18n] Source directory not found: ${SOURCE}`);
    process.exit(1);
  }

  let total = 0;
  for (const target of TARGETS) {
    const relTarget = path.relative(ROOT, target);
    // Clean target to remove stale locale files
    if (fs.existsSync(target)) {
      fs.rmSync(target, { recursive: true, force: true });
    }
    const count = copyDir(SOURCE, target);
    console.log(`[sync-i18n] ${relTarget}: copied ${count} file(s)`);
    total += count;
  }
  console.log(`[sync-i18n] Done. ${total} file(s) synced to ${TARGETS.length} target(s).`);
}

main();
