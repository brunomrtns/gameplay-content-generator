#!/usr/bin/env node
/**
 * check-i18n.js — Placeholder i18n validation script.
 *
 * This is a stub that will be expanded to verify key parity across
 * all locales (pt-BR, en) and all namespaces. For now it just confirms
 * the shared/locales directory exists and has the expected structure.
 *
 * Run: node scripts/check-i18n.js
 */

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const SOURCE = path.join(ROOT, "shared", "locales");
const EXPECTED_LANGS = ["pt-BR", "en"];
const EXPECTED_NS = [
  "common",
  "jobs",
  "stages",
  "errors",
  "dashboard",
  "automation",
];

function main() {
  if (!fs.existsSync(SOURCE)) {
    console.error("[i18n:check] FAIL — shared/locales directory not found");
    process.exit(1);
  }

  let ok = true;
  for (const lang of EXPECTED_LANGS) {
    const langDir = path.join(SOURCE, lang);
    if (!fs.existsSync(langDir)) {
      console.error(`[i18n:check] FAIL — missing language directory: ${lang}`);
      ok = false;
      continue;
    }
    for (const ns of EXPECTED_NS) {
      const file = path.join(langDir, `${ns}.json`);
      if (!fs.existsSync(file)) {
        console.error(`[i18n:check] FAIL — missing namespace file: ${lang}/${ns}.json`);
        ok = false;
        continue;
      }
      try {
        JSON.parse(fs.readFileSync(file, "utf-8"));
      } catch (e) {
        console.error(`[i18n:check] FAIL — invalid JSON: ${lang}/${ns}.json — ${e.message}`);
        ok = false;
      }
    }
  }

  if (ok) {
    console.log("[i18n:check] OK — all locale files present and valid JSON");
    process.exit(0);
  } else {
    process.exit(1);
  }
}

main();
