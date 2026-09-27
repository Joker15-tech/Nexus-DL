#!/bin/bash
set -e

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║           NEXUSDL GUI - Starting Docker Container               ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "📺 Display:      $DISPLAY"
echo "📏 Resolution:   ${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}"
echo "🔌 VNC Port:     $VNC_PORT"
echo "🌐 noVNC Port:   $NOVNC_PORT"
echo "🔑 VNC Password: $VNC_PASSWORD"
echo ""

# Créer le répertoire pour le socket X11
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

# Démarrer supervisor (qui gère Xvfb, fluxbox, x11vnc, websockify et l'app)
echo "🚀 Starting supervisor..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
