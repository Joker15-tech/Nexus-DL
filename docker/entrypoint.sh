#!/bin/sh
# ============================================================================
# NEXUSDL - Entrypoint Script
# ============================================================================
# Ce script s'exécute au démarrage du conteneur Docker.
# Il prépare l'environnement (répertoires, permissions) puis lance la commande
# principale passée en argument (généralement uvicorn).
#
# Utilisation dans le Dockerfile :
#   COPY docker/entrypoint.sh /entrypoint.sh
#   RUN chmod +x /entrypoint.sh
#   ENTRYPOINT ["/entrypoint.sh"]
#   CMD ["uvicorn", "nexusdl.interfaces.web.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
# ============================================================================

set -e  # Arrêter le script immédiatement en cas d'erreur

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║                  NEXUSDL - Docker Entrypoint                     ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# ============================================================================
# 1. CRÉATION DES RÉPERTOIRES NÉCESSAIRES
# ============================================================================
echo "📁 Création des répertoires de travail..."
mkdir -p /app/data/downloads
mkdir -p /app/data/library
mkdir -p /app/logs
mkdir -p /app/cache
mkdir -p /app/config

# ============================================================================
# 2. GESTION DES PERMISSIONS
# ============================================================================
echo "🔒 Configuration des permissions..."
# 777 est utilisé ici pour garantir la compatibilité quel que soit l'utilisateur 
# qui exécute le conteneur (root ou utilisateur non-privilégié).
chmod -R 777 /app/data /app/logs /app/cache /app/config

# ============================================================================
# 3. VÉRIFICATIONS PRÉLIMINAIRES
# ============================================================================
echo "🔍 Vérifications préliminaires..."

# Vérifier que Python est accessible
if command -v python >/dev/null 2>&1; then
    PYTHON_VERSION=$(python --version)
    echo "✅ Python détecté : $PYTHON_VERSION"
else
    echo "❌ Erreur : Python n'est pas installé ou n'est pas dans le PATH."
    exit 1
fi

# Vérifier que Playwright est installé (pour le scraping)
if command -v playwright >/dev/null 2>&1; then
    echo "✅ Playwright CLI détecté."
else
    echo "⚠️ Avertissement : Playwright n'est pas dans le PATH. Les fonctionnalités de scraping échoueront."
fi

# Vérifier la présence du module nexusdl
if python -c "import nexusdl" 2>/dev/null; then
    echo "✅ Module 'nexusdl' importé avec succès."
else
    echo "❌ Erreur : Le module 'nexusdl' est introuvable. Vérifiez l'installation du package."
    exit 1
fi

# ============================================================================
# 4. EXÉCUTION DE LA COMMANDE PRINCIPALE
# ============================================================================
echo ""
echo "🚀 Démarrage de l'application..."
echo "Commande : $*"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# 'exec' remplace le processus shell par le processus de l'application.
# Cela permet à l'application de recevoir correctement les signaux (SIGTERM, SIGINT) 
# pour un arrêt propre du conteneur.
exec "$@"
