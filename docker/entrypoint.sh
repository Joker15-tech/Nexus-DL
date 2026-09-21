#!/bin/sh
# ============================================================================
#  NexusDL — Docker entrypoint
#  Point d'entrée de tous les conteneurs NexusDL.
#
#  Responsabilités :
#    1. Validation de l'environnement (secrets, chemins, utilisateur)
#    2. Génération des configs manquantes depuis les exemples
#    3. Génération des secrets (JWT, CSRF, encryption) si absents
#    4. Attente des dépendances (Redis, FlareSolverr, DB)
#    5. Migrations (config + base de données)
#    6. Exécution de la commande principale avec transfert de signaux
#
#  Variables d'environnement reconnues :
#    NEXUSDL_ENV                  development | staging | production | headless
#    NEXUSDL_ROLE                 web | worker | cli | migrate | shell
#    NEXUSDL_CONFIG_DIR           Dossier de config (défaut: /config)
#    NEXUSDL_LIBRARY_DIR          Dossier bibliothèque (défaut: /data)
#    NEXUSDL_LOG_DIR              Dossier logs (défaut: /logs)
#    NEXUSDL_SECRET_KEY           Clé AES-GCM cookies (hex 64 chars)
#    NEXUSDL_WEB__AUTH__JWT_SECRET  Secret JWT (si web + auth)
#    NEXUSDL_WEB__SECURITY__CSRF_SECRET  Secret CSRF
#    NEXUSDL_WAIT_FOR             Liste "host:port" séparés par virgule
#    NEXUSDL_SKIP_MIGRATIONS      true pour sauter les migrations
#    NEXUSDL_SKIP_CONFIG_GEN      true pour sauter la génération de config
#    NEXUSDL_GENERATE_TLS         true pour générer un cert auto-signé
#    NEXUSDL_USER                 Utilisateur pour drop privileges (défaut: nexusdl)
#    NEXUSDL_SKIP_DROP_PRIVILEGES true pour rester root (déconseillé)
#
#  Codes de sortie :
#    0    → succès
#    1    → erreur de configuration
#    2    → dépendance injoignable (timeout d'attente)
#    3    → migration échouée
#    126  → commande non exécutable
#    127  → commande introuvable
# ============================================================================

set -eu

# --- Constantes --------------------------------------------------------------
readonly SCRIPT_NAME="nexusdl-entrypoint"
readonly CONFIG_DIR="${NEXUSDL_CONFIG_DIR:-/config}"
readonly LIBRARY_DIR="${NEXUSDL_LIBRARY_DIR:-/data}"
readonly LOG_DIR="${NEXUSDL_LOG_DIR:-/logs}"
readonly CACHE_DIR="${NEXUSDL_CACHE_DIR:-/cache}"
readonly CERTS_DIR="${NEXUSDL_CERTS_DIR:-/etc/nginx/certs}"
readonly WAIT_TIMEOUT="${NEXUSDL_WAIT_TIMEOUT:-60}"
readonly MIGRATE_TIMEOUT="${NEXUSDL_MIGRATE_TIMEOUT:-120}"
readonly RUN_AS_USER="${NEXUSDL_USER:-nexusdl}"
readonly RUN_AS_UID="${NEXUSDL_UID:-1000}"
readonly RUN_AS_GID="${NEXUSDL_GID:-1000}"

# --- Couleurs (désactivées si pas de TTY) ------------------------------------
if [ -t 1 ]; then
    C_RESET="$(printf '\033[0m')"
    C_RED="$(printf '\033[31m')"
    C_GREEN="$(printf '\033[32m')"
    C_YELLOW="$(printf '\033[33m')"
    C_BLUE="$(printf '\033[34m')"
    C_CYAN="$(printf '\033[36m')"
    C_BOLD="$(printf '\033[1m')"
else
    C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""
    C_BLUE=""; C_CYAN=""; C_BOLD=""
fi

# --- Logging -----------------------------------------------------------------
log_info()  { printf '%s[%s]%s %s\n' "$C_CYAN" "$SCRIPT_NAME" "$C_RESET" "$*"; }
log_ok()    { printf '%s[%s]%s %s✓%s %s\n' "$C_CYAN" "$SCRIPT_NAME" "$C_RESET" "$C_GREEN" "$C_RESET" "$*"; }
log_warn()  { printf '%s[%s]%s %s⚠%s %s\n' "$C_CYAN" "$SCRIPT_NAME" "$C_RESET" "$C_YELLOW" "$C_RESET" "$*" >&2; }
log_error() { printf '%s[%s]%s %s✗%s %s\n' "$C_CYAN" "$SCRIPT_NAME" "$C_RESET" "$C_RED" "$C_RESET" "$*" >&2; }
log_fatal() { log_error "$@"; exit 1; }

log_step() {
    printf '\n%s%s▶ %s%s\n' "$C_BOLD" "$C_BLUE" "$*" "$C_RESET"
}

# ============================================================================
#  Trap : arrêt propre + transfert de signaux
# ============================================================================

# PID du processus principal (rempli par run_as_user)
MAIN_PID=""

# shellcheck disable=SC2317
forward_signal() {
    signal="$1"
    if [ -n "${MAIN_PID}" ] && kill -0 "${MAIN_PID}" 2>/dev/null; then
        log_info "Transmission de SIG${signal} au PID ${MAIN_PID}"
        kill "-${signal}" "${MAIN_PID}" 2>/dev/null || true
    fi
}

# shellcheck disable=SC2317
on_exit() {
    exit_code=$?
    if [ "${exit_code}" -ne 0 ]; then
        log_error "Sortie avec le code ${exit_code}"
    fi
}

trap 'forward_signal TERM' TERM
trap 'forward_signal INT' INT
trap 'forward_signal QUIT' QUIT
trap 'on_exit' EXIT

# ============================================================================
#  Étape 1 : validation de l'environnement
# ============================================================================

validate_environment() {
    log_step "Validation de l'environnement"

    # --- Utilisateur non-root -------------------------------------------------
    current_uid="$(id -u)"
    if [ "${current_uid}" -eq 0 ]; then
        log_warn "Exécution en root — un drop de privilèges sera tenté après setup"
    fi

    # --- Répertoires requis ---------------------------------------------------
    for dir in "$CONFIG_DIR" "$LIBRARY_DIR" "$LOG_DIR" "$CACHE_DIR"; do
        if [ ! -d "$dir" ]; then
            log_info "Création du dossier : $dir"
            mkdir -p "$dir"
        fi
        if [ ! -w "$dir" ]; then
            log_fatal "Dossier non accessible en écriture : $dir"
        fi
    done

    # --- Version Python (sanity check) ---------------------------------------
    if ! command -v python >/dev/null 2>&1; then
        log_fatal "Python introuvable dans le PATH"
    fi
    py_version="$(python --version 2>&1 | awk '{print $2}')"
    log_info "Python détecté : ${py_version}"

    # --- NEXUSDL_ENV valide --------------------------------------------------
    env_value="${NEXUSDL_ENV:-production}"
    case "$env_value" in
        development|testing|staging|production|headless) : ;;
        *) log_fatal "NEXUSDL_ENV invalide : ${env_value} (attendu: development|testing|staging|production|headless)" ;;
    esac
    log_ok "Environnement : ${env_value}"

    # --- NEXUSDL_ROLE valide -------------------------------------------------
    role="${NEXUSDL_ROLE:-web}"
    case "$role" in
        web|worker|cli|migrate|shell) : ;;
        *) log_fatal "NEXUSDL_ROLE invalide : ${role} (attendu: web|worker|cli|migrate|shell)" ;;
    esac
    log_ok "Rôle : ${role}"
}

# ============================================================================
#  Étape 2 : génération de la config depuis les exemples
# ============================================================================

generate_config() {
    if [ "${NEXUSDL_SKIP_CONFIG_GEN:-false}" = "true" ]; then
        log_info "Génération de config désactivée (NEXUSDL_SKIP_CONFIG_GEN=true)"
        return 0
    fi

    log_step "Génération de la configuration"

    # --- config.yaml ----------------------------------------------------------
    config_file="${CONFIG_DIR}/config.yaml"
    if [ ! -f "$config_file" ]; then
        example="/app/config/config.example.yaml"
        if [ -f "$example" ]; then
            log_info "Création de ${config_file} depuis l'exemple"
            cp "$example" "$config_file"
        else
            log_warn "Exemple introuvable (${example}) — utilisation des défauts du code"
        fi
    else
        log_ok "config.yaml trouvé"
    fi

    # --- logging.yaml ---------------------------------------------------------
    logging_file="${CONFIG_DIR}/logging.yaml"
    if [ ! -f "$logging_file" ] && [ -f "/app/config/logging.yaml" ]; then
        log_info "Création de ${logging_file}"
        cp "/app/config/logging.yaml" "$logging_file"
    fi

    # --- sites_overrides.yaml (optionnel) ------------------------------------
    overrides="${CONFIG_DIR}/sites_overrides.yaml"
    if [ ! -f "$overrides" ] && [ -f "/app/config/sites_overrides.example.yaml" ]; then
        log_info "Création de ${overrides} (personnalisation vide)"
        cp "/app/config/sites_overrides.example.yaml" "$overrides"
    fi

    # --- proxy.yaml (optionnel) ----------------------------------------------
    proxy="${CONFIG_DIR}/proxy.yaml"
    if [ ! -f "$proxy" ] && [ -f "/app/config/proxy.example.yaml" ]; then
        # On ne copie pas par défaut (proxy souvent non voulu) — juste informer
        log_info "proxy.yaml non créé (copier manuellement depuis proxy.example.yaml si besoin)"
    fi

    log_ok "Configuration prête"
}

# ============================================================================
#  Étape 3 : génération des secrets
# ============================================================================

generate_secret_hex() {
    # 32 octets → 64 caractères hex
    python -c 'import secrets; print(secrets.token_hex(32))'
}

ensure_secrets() {
    log_step "Vérification des secrets"

    secrets_file="${CONFIG_DIR}/.secrets.env"

    # Charge les secrets existants depuis le fichier persistant
    if [ -f "$secrets_file" ]; then
        log_info "Chargement des secrets depuis ${secrets_file}"
        # shellcheck disable=SC1090
        set -a
        . "$secrets_file"
        set +a
    fi

    # --- NEXUSDL_SECRET_KEY (chiffrement cookies AES-GCM) --------------------
    if [ -z "${NEXUSDL_SECRET_KEY:-}" ]; then
        NEXUSDL_SECRET_KEY="$(generate_secret_hex)"
        export NEXUSDL_SECRET_KEY
        log_warn "NEXUSDL_SECRET_KEY généré — stocker dans ${secrets_file} pour persister"
    fi

    # --- NEXUSDL_WEB__AUTH__JWT_SECRET (si rôle web) -------------------------
    if [ "${NEXUSDL_ROLE:-web}" = "web" ]; then
        if [ -z "${NEXUSDL_WEB__AUTH__JWT_SECRET:-}" ]; then
            NEXUSDL_WEB__AUTH__JWT_SECRET="$(generate_secret_hex)"
            export NEXUSDL_WEB__AUTH__JWT_SECRET
            log_warn "JWT secret généré (éphémère — perte des sessions au restart)"
        fi
        if [ -z "${NEXUSDL_WEB__SECURITY__CSRF_SECRET:-}" ]; then
            NEXUSDL_WEB__SECURITY__CSRF_SECRET="$(generate_secret_hex)"
            export NEXUSDL_WEB__SECURITY__CSRF_SECRET
        fi
    fi

    # --- Persistance ---------------------------------------------------------
    if [ ! -f "$secrets_file" ]; then
        log_info "Sauvegarde des secrets dans ${secrets_file}"
        {
            printf '# Généré automatiquement par %s — NE PAS COMMITTER\n' "$SCRIPT_NAME"
            printf '# Généré le %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            printf 'NEXUSDL_SECRET_KEY=%s\n' "$NEXUSDL_SECRET_KEY"
            [ -n "${NEXUSDL_WEB__AUTH__JWT_SECRET:-}" ] && \
                printf 'NEXUSDL_WEB__AUTH__JWT_SECRET=%s\n' "$NEXUSDL_WEB__AUTH__JWT_SECRET"
            [ -n "${NEXUSDL_WEB__SECURITY__CSRF_SECRET:-}" ] && \
                printf 'NEXUSDL_WEB__SECURITY__CSRF_SECRET=%s\n' "$NEXUSDL_WEB__SECURITY__CSRF_SECRET"
        } > "$secrets_file"
        chmod 600 "$secrets_file"
    fi

    log_ok "Secrets vérifiés"
}

# ============================================================================
#  Étape 4 : attente des dépendances
# ============================================================================

wait_for_one() {
    host_port="$1"
    host="${host_port%%:*}"
    port="${host_port##*:}"

    log_info "Attente de ${host}:${port} (timeout ${WAIT_TIMEOUT}s)"

    start_time="$(date +%s)"
    while true; do
        # Utilise /dev/tcp si disponible (bash), sinon netcat, sinon Python
        if command -v nc >/dev/null 2>&1; then
            if nc -z -w 2 "$host" "$port" 2>/dev/null; then
                log_ok "${host}:${port} disponible"
                return 0
            fi
        elif python -c "
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(('$host', $port))
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
            log_ok "${host}:${port} disponible"
            return 0
        fi

        now="$(date +%s)"
        elapsed=$((now - start_time))
        if [ "$elapsed" -ge "$WAIT_TIMEOUT" ]; then
            log_error "Timeout d'attente pour ${host}:${port} (${elapsed}s)"
            return 2
        fi

        sleep 1
    done
}

wait_for_dependencies() {
    if [ -z "${NEXUSDL_WAIT_FOR:-}" ]; then
        return 0
    fi

    log_step "Attente des dépendances"

    # Découpe la liste séparée par des virgules
    # shellcheck disable=SC2086
    old_ifs="$IFS"
    IFS=','
    set -- $NEXUSDL_WAIT_FOR
    IFS="$old_ifs"

    for dep in "$@"; do
        # Trim espaces
        dep="$(printf '%s' "$dep" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        [ -z "$dep" ] && continue
        if ! wait_for_one "$dep"; then
            log_fatal "Dépendance injoignable : $dep"
        fi
    done
}

# ============================================================================
#  Étape 5 : migrations
# ============================================================================

run_migrations() {
    if [ "${NEXUSDL_SKIP_MIGRATIONS:-false}" = "true" ]; then
        log_info "Migrations désactivées (NEXUSDL_SKIP_MIGRATIONS=true)"
        return 0
    fi

    log_step "Migrations"

    # --- Migration de config -------------------------------------------------
    if [ -f /app/scripts/migrate_config.py ]; then
        log_info "Vérification de la version du schéma de config"
        if timeout "${MIGRATE_TIMEOUT}" python /app/scripts/migrate_config.py check >/dev/null 2>&1; then
            log_ok "Config à jour"
        else
            log_warn "Migration de config nécessaire — application"
            if ! timeout "${MIGRATE_TIMEOUT}" python /app/scripts/migrate_config.py migrate --apply --backup; then
                log_error "Migration de config échouée"
                return 3
            fi
            log_ok "Config migrée"
        fi
    else
        log_info "Script de migration de config absent — étape sautée"
    fi

    # --- Migration de base de données ----------------------------------------
    if [ "${NEXUSDL_ROLE:-web}" = "web" ] || [ "${NEXUSDL_ROLE:-web}" = "worker" ] || [ "${NEXUSDL_ROLE:-web}" = "migrate" ]; then
        db_file="${LIBRARY_DIR}/library.db"
        if [ -f "$db_file" ]; then
            log_info "Base de données détectée — vérification du schéma"
            # Le module de migration DB est chargé par core.library.database au démarrage
            # Ici on délègue à un one-shot Python
            if ! timeout "${MIGRATE_TIMEOUT}" python -c "
import asyncio
from nexusdl.core.library.database import LibraryDatabase
async def main():
    db = LibraryDatabase()
    await db.init()
    await db.close()
asyncio.run(main())
" 2>&1; then
                log_warn "Migration DB échouée ou non nécessaire (premier démarrage ?)"
            else
                log_ok "Schéma de base de données à jour"
            fi
        else
            log_info "Aucune base existante — elle sera créée au premier accès"
        fi
    fi
}

# ============================================================================
#  Étape 6 : génération de certificats TLS auto-signés (dev/staging)
# ============================================================================

generate_tls_certs() {
    if [ "${NEXUSDL_GENERATE_TLS:-false}" != "true" ]; then
        return 0
    fi

    log_step "Génération de certificats TLS auto-signés"

    mkdir -p "$CERTS_DIR"

    if [ -f "${CERTS_DIR}/fullchain.pem" ] && [ -f "${CERTS_DIR}/privkey.pem" ]; then
        log_ok "Certificats déjà présents — réutilisation"
        return 0
    fi

    if ! command -v openssl >/dev/null 2>&1; then
        log_warn "openssl introuvable — génération de certificats impossible"
        return 0
    fi

    hostname_value="${NEXUSDL_TLS_HOSTNAME:-localhost}"

    log_info "Génération pour ${hostname_value}"

    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "${CERTS_DIR}/privkey.pem" \
        -out "${CERTS_DIR}/fullchain.pem" \
        -subj "/C=FR/ST=IDF/L=Paris/O=NexusDL/CN=${hostname_value}" \
        -addext "subjectAltName=DNS:${hostname_value},DNS:localhost,IP:127.0.0.1" \
        >/dev/null 2>&1

    # Copie du certificat comme chaîne (auto-signé → chaîne = certificat)
    cp "${CERTS_DIR}/fullchain.pem" "${CERTS_DIR}/chain.pem"

    chmod 600 "${CERTS_DIR}/privkey.pem"
    chmod 644 "${CERTS_DIR}/fullchain.pem" "${CERTS_DIR}/chain.pem"

    log_ok "Certificats générés dans ${CERTS_DIR}"
}

# ============================================================================
#  Étape 7 : drop de privilèges
# ============================================================================

drop_privileges() {
    if [ "${NEXUSDL_SKIP_DROP_PRIVILEGES:-false}" = "true" ]; then
        log_warn "Drop de privilèges désactivé (NEXUSDL_SKIP_DROP_PRIVILEGES=true)"
        return 0
    fi

    if [ "$(id -u)" -ne 0 ]; then
        # Déjà non-root
        return 0
    fi

    # --- Crée l'utilisateur si absent ----------------------------------------
    if ! id "$RUN_AS_USER" >/dev/null 2>&1; then
        log_info "Création de l'utilisateur ${RUN_AS_USER} (uid=${RUN_AS_UID})"
        addgroup -g "$RUN_AS_GID" "$RUN_AS_USER" 2>/dev/null || \
            groupadd -g "$RUN_AS_GID" "$RUN_AS_USER" 2>/dev/null || true
        adduser -D -u "$RUN_AS_UID" -G "$RUN_AS_USER" -h "/home/${RUN_AS_USER}" -s /sbin/nologin "$RUN_AS_USER" 2>/dev/null || \
            useradd -u "$RUN_AS_UID" -g "$RUN_AS_GID" -M -s /sbin/nologin "$RUN_AS_USER" 2>/dev/null || true
    fi

    # --- Chown des dossiers --------------------------------------------------
    log_info "Attribution des dossiers à ${RUN_AS_USER}:${RUN_AS_GID}"
    for dir in "$CONFIG_DIR" "$LIBRARY_DIR" "$LOG_DIR" "$CACHE_DIR"; do
        chown -R "${RUN_AS_UID}:${RUN_AS_GID}" "$dir" 2>/dev/null || true
    done
    [ -d "$CERTS_DIR" ] && chown -R "${RUN_AS_UID}:${RUN_AS_GID}" "$CERTS_DIR" 2>/dev/null || true
}

run_as_user() {
    # Exécute la commande donnée en tant que RUN_AS_USER si root, sinon directement.
    if [ "$(id -u)" -eq 0 ] && [ "${NEXUSDL_SKIP_DROP_PRIVILEGES:-false}" != "true" ]; then
        # gosu > su-exec > setpriv > runuser, selon ce qui est dispo
        if command -v gosu >/dev/null 2>&1; then
            exec gosu "${RUN_AS_UID}:${RUN_AS_GID}" "$@"
        elif command -v su-exec >/dev/null 2>&1; then
            exec su-exec "${RUN_AS_UID}:${RUN_AS_GID}" "$@"
        elif command -v setpriv >/dev/null 2>&1; then
            exec setpriv --reuid="$RUN_AS_UID" --regid="$RUN_AS_GID" --init-groups "$@"
        else
            log_warn "Aucun outil de drop (gosu/su-exec/setpriv) — exécution en root"
            exec "$@"
        fi
    else
        exec "$@"
    fi
}

# ============================================================================
#  Étape 8 : exécution selon le rôle
# ============================================================================

run_role_command() {
    role="${NEXUSDL_ROLE:-web}"

    case "$role" in
        web)
            log_step "Démarrage du backend Web (uvicorn)"
            host="${NEXUSDL_WEB__HOST:-0.0.0.0}"
            port="${NEXUSDL_WEB__PORT:-8000}"
            workers="${NEXUSDL_WEB__WORKERS:-1}"
            log_info "Écoute sur ${host}:${port} (workers=${workers})"

            if [ "$workers" -gt 1 ]; then
                # Mode multi-workers : uvicorn parent gère le pool
                exec uvicorn nexusdl.interfaces.web.backend.main:app \
                    --host "$host" \
                    --port "$port" \
                    --workers "$workers" \
                    --log-level info \
                    --no-access-log \
                    --proxy-headers \
                    --forwarded-allow-ips '*'
            else
                # Mode single-worker : hot-reload possible en dev
                if [ "${NEXUSDL_ENV:-production}" = "development" ]; then
                    exec uvicorn nexusdl.interfaces.web.backend.main:app \
                        --host "$host" \
                        --port "$port" \
                        --reload \
                        --reload-dir /app/src/nexusdl \
                        --log-level debug
                else
                    exec uvicorn nexusdl.interfaces.web.backend.main:app \
                        --host "$host" \
                        --port "$port" \
                        --log-level info \
                        --no-access-log \
                        --proxy-headers \
                        --forwarded-allow-ips '*'
                fi
            fi
            ;;

        worker)
            log_step "Démarrage du worker de téléchargement"
            exec python -m nexusdl worker --foreground
            ;;

        cli)
            log_step "Exécution du CLI"
            if [ "$#" -eq 0 ]; then
                log_info "Aucune commande fournie — démarrage du CLI interactif"
                exec python -m nexusdl
            else
                exec python -m nexusdl "$@"
            fi
            ;;

        migrate)
            log_step "Mode migration one-shot"
            exec python -m nexusdl migrate --apply
            ;;

        shell)
            log_step "Shell de debug"
            if [ "$#" -eq 0 ]; then
                exec /bin/sh
            else
                exec "$@"
            fi
            ;;

        *)
            log_fatal "Rôle inconnu : ${role}"
            ;;
    esac
}

# ============================================================================
#  Main
# ============================================================================

main() {
    log_info "Démarrage de NexusDL"
    log_info "Version : ${NEXUSDL_VERSION:-unknown}"

    validate_environment
    generate_config
    ensure_secrets
    wait_for_dependencies
    generate_tls_certs
    run_migrations
    drop_privileges

    log_step "Lancement"
    log_info "Commande : $*"

    # Si l'utilisateur a fourni une commande explicite, elle prime sur le rôle
    if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ]; then
        # Commande explicite (ex: /bin/bash, sh, python, ...)
        run_as_user "$@"
    else
        # Sinon, on suit le rôle
        run_role_command "$@"
    fi
}

main "$@"
