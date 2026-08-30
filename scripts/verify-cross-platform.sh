#!/usr/bin/env bash
# =============================================================================
# verify-cross-platform.sh — Verifica paridade web ↔ mobile antes do deploy
#
# Compara hashes de arquivos correspondentes entre o frontend web (React/Vite)
# e o app mobile (React Native). Se um lado mudou e o outro não, trava o fluxo.
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  REGRA DEFINITIVA — NÃO MODIFICAR                                       │
# │                                                                         │
# │  É PROIBIDO adicionar qualquer flag/option/mecanismo de skip para esta  │
# │  verificação. Não adicione --skip-xplat-verify, --force, --no-verify    │
# │  ou qualquer coisa similar.                                             │
# │                                                                         │
# │  A única forma de passar com divergências é via tela de consentimento   │
# │  interativo, digitando exatamente:                                      │
# │    "eu tenho consentimento que essa funcionalidade nao se aplica a      │
# │     midia <web|mobile>"                                                 │
# │                                                                         │
# │  Não existe caminho de escape automatizado. Esta regra é definitiva.    │
# └─────────────────────────────────────────────────────────────────────────┘
#
# Uso:
#   ./verify-cross-platform.sh              # modo interativo (pede consentimento)
#   ./verify-cross-platform.sh --non-interactive  # trava sem pedir (para CI)
#   ./verify-cross-platform.sh --reset      # reseta estado (primeira vez)
#   ./verify-cross-platform.sh --status     # mostra estado atual sem verificar
#
# IMPORTANTE: NÃO existe --consent. O consentimento é exclusivamente
# interativo e exige justificativa por escrito antes da frase. Isso impede
# uso programático/automatizado do consentimento como atalho para divergências
# que deveriam ser corrigidas na raiz.
#
# Lógica:
#   1. Computa hash MD5 de cada arquivo mapeado (web e mobile)
#   2. Compara com estado salvo do último deploy bem-sucedido
#   3. Para cada par (web ↔ mobile):
#      - Ambos mudaram  → OK
#      - Nenhum mudou   → OK
#      - Web mudou, mobile não → BLOQUEIA (mudança só no web)
#      - Mobile mudou, web não → BLOQUEIA (mudança só no mobile)
#   4. Se bloqueou, lista os arquivos divergentes e pede consentimento explícito:
#      "eu tenho consentimento que essa funcionalidade nao se aplica a midia <web|mobile>"
#   5. Após consentimento ou paridade total, salva novo estado
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MOBILE_ROOT="$(cd "$PROJECT_ROOT/mobile" 2>/dev/null && pwd || echo "")"

# Estado: hashes da última verificação bem-sucedida
STATE_FILE="$PROJECT_ROOT/.cross-platform-state"
# Resultado da verificação atual (lido pelo deploy.sh)
RESULT_FILE="$PROJECT_ROOT/.cross-platform-result"

# Cores
log()  { echo -e "\033[1;34m[xplat]\033[0m $*"; }
ok()   { echo -e "\033[1;32m  ✓\033[0m $*"; }
warn() { echo -e "\033[1;33m  ⚠\033[0m $*"; }
err()  { echo -e "\033[1;31m  ✗\033[0m $*" >&2; }

# Write result file for deploy.sh to read
# Format: MOBILE_CHANGED=<0|1> WEB_CHANGED=<0|1> CONSENTED=<0|1>
# Arrays declared early so write_result can safely access them with set -u
declare -a WEB_ONLY_CHANGES=()
declare -a MOBILE_ONLY_CHANGES=()
declare -a BOTH_CHANGED=()
write_result() {
  local mobile_changed=0 web_changed=0 consented=0
  [[ ${#WEB_ONLY_CHANGES[@]} -gt 0 ]] && web_changed=1
  [[ ${#MOBILE_ONLY_CHANGES[@]} -gt 0 ]] && mobile_changed=1
  [[ ${#BOTH_CHANGED[@]} -gt 0 ]] && { web_changed=1; mobile_changed=1; }
  [[ "${1:-}" == "consented" ]] && consented=1
  echo "MOBILE_CHANGED=$mobile_changed WEB_CHANGED=$web_changed CONSENTED=$consented" > "$RESULT_FILE"
}

# ── Argumentos ────────────────────────────────────────────────────────────────
INTERACTIVE=1
RESET=0
STATUS_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --non-interactive) INTERACTIVE=0; shift ;;
    --reset)           RESET=1; shift ;;
    --status)          STATUS_ONLY=1; shift ;;
    -h|--help)
      echo "Uso: ./verify-cross-platform.sh [opções]"
      echo ""
      echo "  --non-interactive  Trava sem pedir consentimento (para CI)"
      echo "  --reset            Reseta estado (primeira vez ou após mudanças intencionais)"
      echo "  --status           Mostra estado atual sem verificar"
      echo ""
      echo "  Consentimento não-interativo: crie o arquivo"
      echo "    .cross-platform-consent"
      echo "  com uma justificativa por linha (mín. 10 chars cada), uma por"
      echo "  mídia divergente. One-shot: consumido e deletado após uso."
      exit 0
      ;;
    *) echo "Argumento desconhecido: $1"; exit 1 ;;
  esac
done

# ── Verificar que o mobile existe ─────────────────────────────────────────────
if [[ -z "$MOBILE_ROOT" || ! -d "$MOBILE_ROOT" ]]; then
  err "Diretório do mobile não encontrado em $PROJECT_ROOT/mobile"
  err "Verifique se o repositório mobile está no caminho correto."
  exit 1
fi

# ── Mapeamento web ↔ mobile ───────────────────────────────────────────────────
# Cada linha: "web_file|mobile_file|category"
# category é usado na mensagem de consentimento
MAP=(
  # ── Páginas ↔ Screens ──────────────────────────────────────────────────────
  "frontend/src/pages/dashboard.tsx|src/screens/DashboardScreen.tsx|Dashboard"
  "frontend/src/pages/videos.tsx|src/screens/VideosScreen.tsx|Vídeos"
  "frontend/src/pages/content.tsx|src/screens/ContentScreen.tsx|Conteúdo"
  "frontend/src/pages/jobs.tsx|src/screens/JobsScreen.tsx|Jobs"
  "frontend/src/pages/ideas.tsx|src/screens/IdeasScreen.tsx|Ideias"
  "frontend/src/pages/automation.tsx|src/screens/AutomationScreen.tsx|Automação"
  "frontend/src/pages/admin.tsx|src/screens/AdminScreen.tsx|Admin"
  "frontend/src/pages/kids.tsx|src/screens/KidsScreen.tsx|Kids"
  "frontend/src/pages/kids-ideas.tsx|src/screens/KidsIdeasScreen.tsx|Ideias Kids"
  "frontend/src/pages/login.tsx|src/screens/LoginScreen.tsx|Login"

  # ── API ────────────────────────────────────────────────────────────────────
  "frontend/src/lib/api.ts|src/api/endpoints.ts|API"

  # ── Auth ───────────────────────────────────────────────────────────────────
  "frontend/src/lib/auth.ts|src/hooks/useAuth.tsx|Auth"

  # ── Componentes compartilhados ─────────────────────────────────────────────
  "frontend/src/components/worker-status.tsx|src/screens/DashboardScreen.tsx|Worker Status"
  # game-search-modal.tsx é web-only (no mobile a busca é inline no ContentScreen)
  # — não parear para evitar falso positivo quando ContentScreen muda por outro motivo
)

# ── Helpers ───────────────────────────────────────────────────────────────────

# Computa hash de um arquivo. Retorna "missing" se não existir.
hash_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    md5sum "$file" | cut -d' ' -f1
  else
    echo "missing"
  fi
}

# Computa hash de um arquivo no contexto do projeto web
hash_web() {
  hash_file "$PROJECT_ROOT/$1"
}

# Computa hash de um arquivo no contexto do mobile
hash_mobile() {
  hash_file "$MOBILE_ROOT/$1"
}

# Carrega estado anterior. Formato: "web_path|mobile_path|web_hash|mobile_hash"
load_state() {
  if [[ ! -f "$STATE_FILE" ]]; then
    return 0  # sem estado = primeira vez
  fi
  cat "$STATE_FILE"
}

# Salva estado atual
save_state() {
  local tmp=""
  for entry in "${MAP[@]}"; do
    IFS='|' read -r web_file mobile_file category <<< "$entry"
    local wh mh
    wh=$(hash_web "$web_file")
    mh=$(hash_mobile "$mobile_file")
    tmp+="${web_file}|${mobile_file}|${wh}|${mh}"$'\n'
  done
  echo -n "$tmp" > "$STATE_FILE"
}

# ── Modo --status ─────────────────────────────────────────────────────────────
if [[ "$STATUS_ONLY" -eq 1 ]]; then
  log "Estado atual de paridade web ↔ mobile:"
  echo ""
  printf "  %-50s %-50s %s\n" "WEB" "MOBILE" "STATUS"
  printf "  %-50s %-50s %s\n" "──────────────────────────────────────────────────" "──────────────────────────────────────────────────" "──────"
  for entry in "${MAP[@]}"; do
    IFS='|' read -r web_file mobile_file category <<< "$entry"
    s_wh=$(hash_web "$web_file")
    s_mh=$(hash_mobile "$mobile_file")
    s_web_status="✓"
    s_mobile_status="✓"
    [[ "$s_wh" == "missing" ]] && s_web_status="✗"
    [[ "$s_mh" == "missing" ]] && s_mobile_status="✗"
    printf "  %-50s %-50s %s/%s\n" "$web_file" "$mobile_file" "$s_web_status" "$s_mobile_status"
  done
  echo ""
  if [[ -f "$STATE_FILE" ]]; then
    ok "Estado salvo em: $STATE_FILE"
  else
    warn "Nenhum estado salvo (primeira vez)"
  fi
  exit 0
fi

# ── Modo --reset ──────────────────────────────────────────────────────────────
if [[ "$RESET" -eq 1 ]]; then
  log "Resetando estado de paridade..."
  save_state
  ok "Estado salvo. Próxima verificação usará este como baseline."
  exit 0
fi

# ── Verificação principal ─────────────────────────────────────────────────────
log "Verificando paridade web ↔ mobile..."
echo ""

# Carregar estado anterior em um array associativo
declare -A PREV_WEB_HASH
declare -A PREV_MOBILE_HASH

if [[ -f "$STATE_FILE" ]]; then
  while IFS='|' read -r wfile mfile whash mhash; do
    [[ -z "$wfile" ]] && continue
    PREV_WEB_HASH["$wfile"]="$whash"
    PREV_MOBILE_HASH["$mfile"]="$mhash"
  done < "$STATE_FILE"
fi

HAS_FIRST_RUN=0
if [[ ! -f "$STATE_FILE" ]]; then
  HAS_FIRST_RUN=1
  warn "Primeira execução — salvando estado inicial sem bloquear."
  echo ""
fi

# Arrays para追踪 divergências
# Arrays already declared near write_result() for set -u safety
declare -a NONE_CHANGED=()        # nenhum mudou (OK)
declare -a MISSING_FILES=()       # arquivo não existe

for entry in "${MAP[@]}"; do
  IFS='|' read -r web_file mobile_file category <<< "$entry"

  cur_wh=$(hash_web "$web_file")
  cur_mh=$(hash_mobile "$mobile_file")

  prev_wh="${PREV_WEB_HASH[$web_file]:-none}"
  prev_mh="${PREV_MOBILE_HASH[$mobile_file]:-none}"

  # Detectar arquivos ausentes
  if [[ "$cur_wh" == "missing" ]]; then
    MISSING_FILES+=("WEB: $web_file ($category)")
    continue
  fi
  if [[ "$cur_mh" == "missing" ]]; then
    MISSING_FILES+=("MOBILE: $mobile_file ($category)")
    continue
  fi

  # Primeira execução: não bloquear
  if [[ "$HAS_FIRST_RUN" -eq 1 ]]; then
    continue
  fi

  web_changed=0
  mobile_changed=0

  if [[ "$cur_wh" != "$prev_wh" ]]; then
    web_changed=1
  fi
  if [[ "$cur_mh" != "$prev_mh" ]]; then
    mobile_changed=1
  fi

  if [[ $web_changed -eq 1 && $mobile_changed -eq 1 ]]; then
    BOTH_CHANGED+=("$category: $web_file ↔ $mobile_file")
  elif [[ $web_changed -eq 1 && $mobile_changed -eq 0 ]]; then
    WEB_ONLY_CHANGES+=("$category|$web_file|$mobile_file")
  elif [[ $web_changed -eq 0 && $mobile_changed -eq 1 ]]; then
    MOBILE_ONLY_CHANGES+=("$category|$web_file|$mobile_file")
  else
    NONE_CHANGED+=("$category")
  fi
done

# ── Relatório ─────────────────────────────────────────────────────────────────

if [[ $HAS_FIRST_RUN -eq 1 ]]; then
  save_state
  ok "Estado inicial salvo. Próximo deploy vai verificar paridade."
  exit 0
fi

# Mostrar mudanças sincronizadas
if [[ ${#BOTH_CHANGED[@]} -gt 0 ]]; then
  ok "${#BOTH_CHANGED[@]} par(es) com mudanças sincronizadas (web + mobile):"
  for item in "${BOTH_CHANGED[@]}"; do
    echo "    $item"
  done
  echo ""
fi

# Mostrar sem mudanças
if [[ ${#NONE_CHANGED[@]} -gt 0 ]]; then
  ok "${#NONE_CHANGED[@]} par(es) sem mudanças."
fi

# Arquivos ausentes
if [[ ${#MISSING_FILES[@]} -gt 0 ]]; then
  warn "Arquivos ausentes:"
  for item in "${MISSING_FILES[@]}"; do
    echo "    $item"
  done
  echo ""
fi

# ── Bloquear se houver divergências ───────────────────────────────────────────

BLOCK=0

if [[ ${#WEB_ONLY_CHANGES[@]} -gt 0 ]]; then
  err "${#WEB_ONLY_CHANGES[@]} alteração(ões) APENAS no web (sem contrapartida no mobile):"
  echo ""
  for item in "${WEB_ONLY_CHANGES[@]}"; do
    IFS='|' read -r cat wfile mfile <<< "$item"
    echo "    [$cat]"
    echo "      web:    $wfile  ✓ mudou"
    echo "      mobile: $mfile  ✗ NÃO mudou"
  done
  echo ""
  BLOCK=1
fi

if [[ ${#MOBILE_ONLY_CHANGES[@]} -gt 0 ]]; then
  err "${#MOBILE_ONLY_CHANGES[@]} alteração(ões) APENAS no mobile (sem contrapartida no web):"
  echo ""
  for item in "${MOBILE_ONLY_CHANGES[@]}"; do
    IFS='|' read -r cat wfile mfile <<< "$item"
    echo "    [$cat]"
    echo "      web:    $wfile  ✗ NÃO mudou"
    echo "      mobile: $mfile  ✓ mudou"
  done
  echo ""
  BLOCK=1
fi

if [[ $BLOCK -eq 0 ]]; then
  echo ""
  ok "Paridade web ↔ mobile verificada. Tudo sincronizado."
  write_result
  save_state
  exit 0
fi

# ── Etapa de consentimento explícito ──────────────────────────────────────────
# PROIBIDO adicionar qualquer mecanismo de skip desta verificação.
# A única forma de prosseguir com divergências é:
#   1. Modo interativo: digitar a justificativa (mín. 10 chars)
#   2. Arquivo .cross-platform-consent: justificativa escrita (mín. 10 chars)
#      — one-shot: consumido e deletado após uso
#      — logado no audit log como "non-interactive"
# Não adicione --force, --skip, --yes, ou qualquer flag que bypass esta etapa.

CONSENT_FILE="$PROJECT_ROOT/.cross-platform-consent"
CONSENT_LOG="$PROJECT_ROOT/.cross-platform-consent-log"

# Construir lista de mídias com divergências
declare -a MEDIAS_TO_CONFIRM=()
if [[ ${#WEB_ONLY_CHANGES[@]} -gt 0 ]]; then
  MEDIAS_TO_CONFIRM+=("web")
fi
if [[ ${#MOBILE_ONLY_CHANGES[@]} -gt 0 ]]; then
  MEDIAS_TO_CONFIRM+=("mobile")
fi

# ── Caminho não-interativo via arquivo de consentimento ──
# O arquivo deve conter uma justificativa por linha, na ordem das mídias
# divergentes. One-shot: deletado após uso.
# PROTEÇÃO CONTRA ABUSO: se o mesmo par de arquivos já foi consentido
# via arquivo 3+ vezes no log de auditoria, o consentimento via arquivo
# é recusado — só passa no modo interativo. Isso impede usar o arquivo
# como atalho recorrente em vez de corrigir o pareamento.
MAX_FILE_CONSENTS=3

if [[ -f "$CONSENT_FILE" && ${#MEDIAS_TO_CONFIRM[@]} -gt 0 ]]; then
  mapfile -t CONSENT_LINES < "$CONSENT_FILE"
  ALL_CONFIRMED=1
  line_idx=0
  for media in "${MEDIAS_TO_CONFIRM[@]}"; do
    if [[ "$media" == "web" ]]; then
      other_media="mobile"
      changed_files=("${WEB_ONLY_CHANGES[@]}")
    else
      other_media="web"
      changed_files=("${MOBILE_ONLY_CHANGES[@]}")
    fi
    justification="${CONSENT_LINES[$line_idx]:-}"
    line_idx=$((line_idx + 1))
    if [[ ${#justification} -lt 10 ]]; then
      err "Consentimento no arquivo para mídia '$media' muito curto (< 10 chars)."
      ALL_CONFIRMED=0
      continue
    fi

    # Verificar histórico de consentimentos via arquivo para os mesmos arquivos
    repeat_count=0
    if [[ -f "$CONSENT_LOG" ]]; then
      for item in "${changed_files[@]}"; do
        IFS='|' read -r cat wfile mfile <<< "$item"
        # Contar quantas vezes este arquivo apareceu em consentimentos não-interativos
        count=$(grep -c "non-interactive.*$wfile\|non-interactive.*$mfile" "$CONSENT_LOG" 2>/dev/null || echo 0)
        if [[ "$count" -gt "$repeat_count" ]]; then
          repeat_count=$count
        fi
      done
    fi

    if [[ "$repeat_count" -ge "$MAX_FILE_CONSENTS" ]]; then
      err "ABUSO DETECTADO: O par de arquivos para mídia '$media' já foi"
      err "consentido via arquivo $repeat_count vezes. Limite: $MAX_FILE_CONSENTS."
      err "Corrija o pareamento em verify-cross-platform.sh OU consenta"
      err "interativamente. O atalho via arquivo foi bloqueado para este par."
      ALL_CONFIRMED=0
      continue
    fi

    if [[ "$repeat_count" -ge 1 ]]; then
      warn "Este par já foi consentido via arquivo $repeat_count vez(es)."
      warn "Limite de $MAX_FILE_CONSENTS antes de bloquear. Considere corrigir o pareamento."
    fi

    ok "Consentimento (arquivo) confirmado para mídia: $media"
    ok "Justificativa: $justification"
    # Log com os arquivos para permitir detecção de abuso
    files_str=""
    for item in "${changed_files[@]}"; do
      IFS='|' read -r cat wfile mfile <<< "$item"
      files_str="$files_str $wfile"
    done
    echo "[$(date -Iseconds)] CONSENT(non-interactive): media=$media other=$other_media files=\"$files_str\" reason=\"$justification\"" >> "$CONSENT_LOG"
  done
  # One-shot: consumir o arquivo
  rm -f "$CONSENT_FILE"
  if [[ $ALL_CONFIRMED -eq 1 ]]; then
    ok "Todas as divergências foram consentidas (via arquivo). Continuando deploy..."
    write_result "consented"
    save_state
    exit 0
  else
    err "Consentimento via arquivo incompleto. Deploy bloqueado."
    err "Crie $CONSENT_FILE com uma justificativa (mín. 10 chars) por mídia divergente."
    exit 1
  fi
fi

if [[ $INTERACTIVE -eq 0 ]]; then
  err "Divergências detectadas e modo não-interativo ativo."
  err "Corrija as divergências, rode em modo interativo, ou crie"
  err "  $CONSENT_FILE com uma justificativa por mídia divergente (mín. 10 chars)."
  exit 1
fi

echo ""
log "═══════════════════════════════════════════════════════════════"
log "  DIVERGÊNCIAS DETECTADAS"
log "═══════════════════════════════════════════════════════════════"
echo ""
echo "  O deploy foi bloqueado porque há mudanças em uma mídia (web ou"
echo "  mobile) que não foram refletidas na outra."
echo ""
echo "  Se a mudança é INTENCIONAL e se aplica apenas a uma mídia"
echo "  (ex: feature específica de web, ou feature específica de mobile),"
echo "  você precisa confirmar explicitamente."
echo ""
echo "  Para cada mídia com divergência, digite a frase exata:"
echo ""

ALL_CONFIRMED=1

for media in "${MEDIAS_TO_CONFIRM[@]}"; do
  # Listar o que mudou nesta mídia
  echo ""
  if [[ "$media" == "web" ]]; then
    echo "  ── Mudanças só no WEB ──"
    for item in "${WEB_ONLY_CHANGES[@]}"; do
      IFS='|' read -r cat wfile mfile <<< "$item"
      echo "    • $cat: $wfile"
    done
  else
    echo "  ── Mudanças só no MOBILE ──"
    for item in "${MOBILE_ONLY_CHANGES[@]}"; do
      IFS='|' read -r cat wfile mfile <<< "$item"
      echo "    • $cat: $mfile"
    done
  fi
  echo ""

  # O consentimento é sobre a mídia que NÃO mudou (a outra lado).
  # Se mudou só no mobile → a funcionalidade não se aplica ao web (que não mudou).
  # Se mudou só no web → a funcionalidade não se aplica ao mobile (que não mudou).
  if [[ "$media" == "web" ]]; then
    other_media="mobile"
  else
    other_media="web"
  fi

  echo "  Justifique por que esta funcionalidade não se aplica à outra"
  echo "  plataforma (mín. 10 caracteres)."
  echo "  Exemplos válidos: ajuste nativo de Android, feature web-only de UI."
  echo "  Se for falso positivo do verificador, NÃO use consentimento —"
  echo "  corrija o pareamento no scripts/verify-cross-platform.sh."
  echo ""

  # Exigir justificativa por escrito (mínimo 10 caracteres)
  read -r -p "  > " justification
  if [[ ${#justification} -lt 10 ]]; then
    err "Justificativa muito curta. Deploy bloqueado."
    err "Se é falso positivo, corrija o pareamento no verify-cross-platform.sh."
    ALL_CONFIRMED=0
    continue
  fi

  ok "Consentimento confirmado para mídia: $media"
  ok "Justificativa: $justification"
  # Registrar no log para auditoria
  echo "[$(date -Iseconds)] CONSENT(interactive): media=$media other=$other_media reason=\"$justification\"" >> "$CONSENT_LOG"
done

echo ""
if [[ $ALL_CONFIRMED -eq 1 ]]; then
  ok "Todas as divergências foram consentidas. Continuando deploy..."
  write_result "consented"
  save_state
  exit 0
else
  err "Consentimento incompleto. Deploy bloqueado."
  err "Corrija as divergências ou forneça o consentimento correto."
  exit 1
fi
