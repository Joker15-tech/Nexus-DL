
# 🔌 NexusDL — API Reference

<div align="center">

**Documentation complète de l'API REST et de l'API Python de NexusDL.**

[![API Version](https://img.shields.io/badge/API-v1-blue?style=for-the-badge)](docs/API.md)
[![OpenAPI](https://img.shields.io/badge/OpenAPI-3.1-green?style=for-the-badge&logo=openapiinitiative&logoColor=white)](http://localhost:8000/docs)
[![REST](https://img.shields.io/badge/REST-JSON-orange?style=for-the-badge)](docs/API.md)
[![WebSocket](https://img.shields.io/badge/WebSocket-supported-purple?style=for-the-badge)](docs/API.md)

[🌐 REST API](#-rest-api) •
[🐍 Python API](#-python-api) •
[📡 WebSocket](#-websocket) •
[🔐 Authentification](#-authentification) •
[📚 Exemples](#-exemples-complets)

</div>

---

## 📖 Table des matières

- [Vue d'ensemble](#-vue-densemble)
- [Démarrage rapide](#-démarrage-rapide)
- [Authentification](#-authentification)
- [REST API](#-rest-api)
  - [Sites](#1-sites)
  - [Recherche](#2-recherche)
  - [Manga](#3-manga)
  - [Chapitres](#4-chapitres)
  - [Téléchargements](#5-téléchargements)
  - [Bibliothèque](#6-bibliothèque)
  - [Paramètres](#7-paramètres)
  - [Cookies](#8-cookies)
  - [Utilisateurs](#9-utilisateurs)
  - [Système](#10-système)
- [WebSocket](#-websocket)
- [Python API](#-python-api)
- [Codes d'erreur](#-codes-derreur)
- [Rate limiting](#-rate-limiting)
- [Pagination](#-pagination)
- [Versioning](#-versioning)
- [Exemples complets](#-exemples-complets)
- [SDK et clients](#-sdk-et-clients)

---

## 🎯 Vue d'ensemble

NexusDL expose **deux APIs** :

| API | Type | Usage | Base URL |
|-----|------|-------|----------|
| **REST API** | HTTP/JSON | Interface web, clients externes | `http://localhost:8000/api` |
| **WebSocket** | WS/JSON | Progression temps réel | `ws://localhost:8000/ws` |
| **Python API** | Python | Scripts, intégrations | `import nexusdl` |

### 📋 Caractéristiques

- ✅ **RESTful** — Ressources identifiables, verbes HTTP standards
- ✅ **JSON** — Toutes les réponses sont en JSON (sauf erreurs binaires)
- ✅ **Async** — Backend FastAPI entièrement asynchrone
- ✅ **Documenté** — OpenAPI 3.1 auto-généré (`/docs`, `/redoc`)
- ✅ **Sécurisé** — JWT + HTTPS + rate limiting
- ✅ **Versionné** — Préfixe `/api/v1/` (v1 stable)
- ✅ **WebSocket** — Progression temps réel via `/ws/progress`
- ✅ **Erreurs cohérentes** — Format uniforme des erreurs

### 🌐 URLs par défaut

| Environnement | URL |
|---------------|-----|
| **Local (dev)** | `http://localhost:8000/api` |
| **Docker** | `http://nexusdl:8000/api` |
| **Production** | `https://votre-domaine.com/api` |

### 📖 Documentation interactive

Une fois NexusDL démarré, accédez à :

| Interface | URL |
|-----------|-----|
| **Swagger UI** | http://localhost:8000/docs |
| **ReDoc** | http://localhost:8000/redoc |
| **OpenAPI JSON** | http://localhost:8000/openapi.json |
| **Health check** | http://localhost:8000/api/health |

---

## 🚀 Démarrage rapide

### Démarrer le serveur

```bash
# Via CLI
nexusdl web --host 0.0.0.0 --port 8000

# Via Docker
docker run -d -p 8000:8000 nexusquantum/nexusdl:latest web

# Via docker compose
docker compose up -d web
```

### Premier appel

```bash
# Vérifier que l'API est en ligne
curl http://localhost:8000/api/health

# Réponse
{
  "status": "healthy",
  "version": "1.0.0",
  "uptime_seconds": 1234,
  "timestamp": "2026-01-15T10:30:00Z"
}
```

### Authentification

```bash
# Se connecter
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "votre-mot-de-passe"}'

# Réponse
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 86400
}
```

### Utiliser le token

```bash
# Chercher un manga
curl http://localhost:8000/api/search?q=one+piece \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

---

## 🔐 Authentification

NexusDL utilise **JWT (JSON Web Tokens)** pour l'authentification.

### Flux complet

```
┌─────────┐                                        ┌─────────────┐
│ Client  │                                        │  NexusDL    │
└────┬────┘                                        └──────┬──────┘
     │                                                    │
     │  1. POST /api/auth/login                           │
     │  {username, password}                              │
     │───────────────────────────────────────────────────▶│
     │                                                    │
     │  2. 200 OK                                         │
     │  {access_token, refresh_token, expires_in}         │
     │◀───────────────────────────────────────────────────│
     │                                                    │
     │  3. GET /api/search                                │
     │  Authorization: Bearer <access_token>              │
     │───────────────────────────────────────────────────▶│
     │                                                    │
     │  4. 200 OK (données)                               │
     │◀───────────────────────────────────────────────────│
     │                                                    │
     │  ... 24h plus tard (token expiré) ...              │
     │                                                    │
     │  5. POST /api/auth/refresh                         │
     │  {refresh_token}                                   │
     │───────────────────────────────────────────────────▶│
     │                                                    │
     │  6. 200 OK (nouveaux tokens)                       │
     │◀───────────────────────────────────────────────────│
     │                                                    │
```

### Types de tokens

| Token | Durée de vie | Usage |
|-------|:------------:|-------|
| **Access Token** | 24h (par défaut) | Authentifier les requêtes API |
| **Refresh Token** | 30 jours | Obtenir un nouvel access token |

### Headers d'authentification

```http
Authorization: Bearer <access_token>
```

### Endpoints d'authentification

| Méthode | Endpoint | Description |
|:-------:|----------|-------------|
| `POST` | `/api/auth/login` | Se connecter |
| `POST` | `/api/auth/register` | Créer un compte (si autorisé) |
| `POST` | `/api/auth/refresh` | Rafraîchir le token |
| `POST` | `/api/auth/logout` | Se déconnecter (invalide le token) |
| `GET` | `/api/auth/me` | Informations sur l'utilisateur connecté |
| `PUT` | `/api/auth/me` | Mettre à jour le profil |
| `POST` | `/api/auth/change-password` | Changer le mot de passe |
| `POST` | `/api/auth/forgot-password` | Demander un reset |
| `POST` | `/api/auth/reset-password` | Reset avec token |

---

## 🌐 REST API

**Base URL :** `http://localhost:8000/api/v1`

### Conventions

| Aspect | Convention |
|--------|-----------|
| **Format** | JSON (sauf binaires) |
| **Encodage** | UTF-8 |
| **Dates** | ISO 8601 UTC (`2026-01-15T10:30:00Z`) |
| **IDs** | UUID v4 ou slug |
| **Pagination** | `?page=1&per_page=50` |
| **Tri** | `?sort=title&order=asc` |
| **Filtres** | `?language=fr&adult=false` |
| **Erreurs** | `{error: {code, message, details}}` |

---

### 1. Sites

Gestion des sites supportés.

#### `GET /sites` — Lister les sites

```http
GET /api/v1/sites
```

**Query parameters :**

| Paramètre | Type | Description | Défaut |
|-----------|------|-------------|:------:|
| `language` | string | Filtrer par langue (`fr`, `en`, `kr`, `jp`) | — |
| `adult` | boolean | Inclure les sites adultes | `false` |
| `enabled` | boolean | Filtrer par activation | — |
| `search` | string | Rechercher par nom | — |
| `page` | integer | Numéro de page | `1` |
| `per_page` | integer | Éléments par page (max 100) | `50` |

**Exemple :**

```bash
curl "http://localhost:8000/api/v1/sites?language=fr&adult=false" \
  -H "Authorization: Bearer <token>"
```

**Réponse `200 OK` :**

```json
{
  "data": [
    {
      "id": "sushiscan_net",
      "name": "SushiScan",
      "domains": ["https://sushiscan.net"],
      "language": "fr",
      "adult": false,
      "enabled": true,
      "capabilities": {
        "supports_search": true,
        "supports_manga_info": true,
        "supports_chapters": true,
        "supports_pages": true,
        "supports_download": true,
        "requires_auth": false,
        "requires_cloudflare_bypass": false,
        "max_concurrent_downloads": 4,
        "rate_limit_per_second": 2.0
      },
      "parser_class": "nexusdl.parsers.fr.sushiscan_net:SushiScanNetParser",
      "notes": "Thème WordPress Madara",
      "health": {
        "status": "online",
        "last_check": "2026-01-15T10:25:00Z",
        "response_time_ms": 234
      }
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 50,
    "total": 1,
    "total_pages": 1
  }
}
```

#### `GET /sites/{site_id}` — Détails d'un site

```http
GET /api/v1/sites/sushiscan_net
```

**Réponse `200 OK` :** Objet site complet.

#### `POST /sites/{site_id}/health` — Vérifier la santé

Force un check de santé du site.

**Réponse `200 OK` :**

```json
{
  "site_id": "sushiscan_net",
  "status": "online",
  "response_time_ms": 234,
  "last_check": "2026-01-15T10:30:00Z"
}
```

---

### 2. Recherche

#### `GET /search` — Recherche simple

```http
GET /api/v1/search?q=one+piece&sites=mangadex,sushiscan_net
```

**Query parameters :**

| Paramètre | Type | Description | Défaut |
|-----------|------|-------------|:------:|
| `q` | string | **Requis.** Terme de recherche | — |
| `sites` | string[] | Sites à interroger (virgules) | Tous activés |
| `language` | string[] | Filtrer par langue | Toutes |
| `adult` | boolean | Inclure contenu adulte | `false` |
| `page` | integer | Page de résultats | `1` |
| `per_page` | integer | Résultats par site | `20` |
| `timeout` | integer | Timeout par site (secondes) | `10` |

**Réponse `200 OK` :**

```json
{
  "query": "one piece",
  "total": 47,
  "results": [
    {
      "id": "mangadex:a1c2e3f4-...",
      "title": "One Piece",
      "alternative_titles": ["ワンピース", "OP"],
      "author": "Eiichiro Oda",
      "description": "L'histoire de Monkey D. Luffy...",
      "cover_url": "https://uploads.mangadex.org/covers/...",
      "year": 1997,
      "status": "ongoing",
      "genres": ["Action", "Adventure", "Comedy"],
      "language": "en",
      "content_rating": "safe",
      "site": "mangadex",
      "site_name": "MangaDex",
      "url": "https://mangadex.org/title/a1c2e3f4-...",
      "chapters_count": 1123,
      "last_update": "2026-01-14T18:30:00Z"
    }
  ],
  "errors": [
    {
      "site": "example_site",
      "error": "Timeout after 10s"
    }
  ],
  "duration_ms": 1234
}
```

#### `POST /search/batch` — Recherche multi-sites avancée

```http
POST /api/v1/search/batch
Content-Type: application/json

{
  "queries": ["one piece", "naruto", "bleach"],
  "sites": ["mangadex", "sushiscan_net"],
  "language": ["fr", "en"],
  "adult": false,
  "max_concurrent": 8
}
```

**Réponse `200 OK` :**

```json
{
  "results": {
    "one piece": [...],
    "naruto": [...],
    "bleach": [...]
  },
  "duration_ms": 3456
}
```

---

### 3. Manga

#### `GET /manga/{manga_id}` — Détails d'un manga

**Réponse `200 OK` :**

```json
{
  "id": "mangadex:a1c2e3f4-...",
  "source_id": "a1c2e3f4-...",
  "site": "mangadex",
  "site_name": "MangaDex",
  "title": "One Piece",
  "alternative_titles": ["ワンピース", "OP"],
  "author": "Eiichiro Oda",
  "artist": "Eiichiro Oda",
  "description": "L'histoire de Monkey D. Luffy...",
  "cover_url": "https://uploads.mangadex.org/covers/...",
  "year": 1997,
  "status": "ongoing",
  "genres": ["Action", "Adventure", "Comedy"],
  "language": "en",
  "content_rating": "safe",
  "url": "https://mangadex.org/title/a1c2e3f4-...",
  "chapters": [
    {
      "id": "chapter-1",
      "source_id": "ch-1",
      "title": "Romance Dawn",
      "number": 1,
      "volume": 1,
      "language": "en",
      "pages_count": 53,
      "published_at": "1997-07-22T00:00:00Z",
      "url": "https://mangadex.org/chapter/..."
    }
  ],
  "in_library": false,
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-01-15T10:30:00Z"
}
```

#### `POST /manga` — Ajouter un manga depuis une URL

```http
POST /api/v1/manga
Content-Type: application/json

{
  "url": "https://mangadex.org/title/a1c2e3f4-...",
  "site": "mangadex"  // optionnel, auto-détecté
}
```

#### `GET /manga/{manga_id}/cover` — Télécharger la cover

Retourne l'image binaire.

#### `DELETE /manga/{manga_id}` — Supprimer un manga

Supprime de la bibliothèque locale (n'affecte pas la source).

---

### 4. Chapitres

#### `GET /manga/{manga_id}/chapters` — Lister les chapitres

**Query parameters :**

| Paramètre | Type | Description |
|-----------|------|-------------|
| `volume` | integer | Filtrer par volume |
| `language` | string | Filtrer par langue |
| `sort` | string | `number`, `published_at` |
| `order` | string | `asc`, `desc` |

**Réponse `200 OK` :**

```json
{
  "manga_id": "mangadex:a1c2e3f4-...",
  "chapters": [
    {
      "id": "chapter-1",
      "source_id": "ch-1",
      "title": "Romance Dawn",
      "number": 1,
      "volume": 1,
      "language": "en",
      "pages_count": 53,
      "published_at": "1997-07-22T00:00:00Z",
      "url": "https://mangadex.org/chapter/...",
      "downloaded": false
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 100,
    "total": 1123,
    "total_pages": 12
  }
}
```

#### `GET /chapters/{chapter_id}` — Détails d'un chapitre

#### `GET /chapters/{chapter_id}/pages` — URLs des pages

**Réponse `200 OK` :**

```json
{
  "chapter_id": "chapter-1",
  "pages": [
    {
      "index": 0,
      "url": "https://uploads.mangadex.org/data/.../1.png",
      "filename": "001.png",
      "width": 1080,
      "height": 1536
    }
  ],
  "total": 53
}
```

---

### 5. Téléchargements

#### `POST /downloads` — Lancer un téléchargement

```http
POST /api/v1/downloads
Content-Type: application/json

{
  "manga_url": "https://mangadex.org/title/a1c2e3f4-...",
  "chapters": ["chapter-1", "chapter-2", "chapter-3"],
  "format": "cbz",
  "dest": "/downloads/One Piece",
  "priority": "normal",
  "overwrite": false,
  "options": {
    "quality": "original",
    "convert_to_jpeg": false,
    "include_comic_info": true
  }
}
```

**Paramètres :**

| Champ | Type | Description | Défaut |
|-------|------|-------------|:------:|
| `manga_url` | string | **Requis.** URL du manga | — |
| `chapters` | string[] | IDs des chapitres (vide = tous) | `[]` |
| `chapter_range` | string | Plage (ex: `1-10`, `all`) | — |
| `format` | string | `zip`, `cbz`, `cbr`, `pdf`, `folder` | `cbz` |
| `dest` | string | Dossier de destination | Config |
| `priority` | string | `low`, `normal`, `high` | `normal` |
| `overwrite` | boolean | Écraser si existe | `false` |

**Réponse `202 Accepted` :**

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "manga": {
    "title": "One Piece",
    "url": "https://mangadex.org/title/..."
  },
  "chapters_count": 3,
  "created_at": "2026-01-15T10:30:00Z",
  "ws_url": "ws://localhost:8000/ws/progress?task_id=550e8400-..."
}
```

#### `GET /downloads` — Lister les téléchargements

**Query parameters :**

| Paramètre | Type | Description |
|-----------|------|-------------|
| `status` | string | `pending`, `running`, `paused`, `done`, `failed`, `cancelled` |
| `manga_id` | string | Filtrer par manga |
| `page` | integer | Page |
| `per_page` | integer | Éléments par page |

**Réponse `200 OK` :**

```json
{
  "data": [
    {
      "task_id": "550e8400-...",
      "status": "running",
      "manga": {
        "title": "One Piece",
        "cover_url": "https://..."
      },
      "chapters": {
        "total": 3,
        "completed": 1,
        "current": "Chapitre 2"
      },
      "progress": 0.33,
      "bytes_downloaded": 52428800,
      "bytes_total": 157286400,
      "speed_bps": 2097152,
      "eta_seconds": 50,
      "started_at": "2026-01-15T10:30:00Z",
      "updated_at": "2026-01-15T10:35:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 50,
    "total": 1
  }
}
```

#### `GET /downloads/{task_id}` — Détails d'un téléchargement

#### `DELETE /downloads/{task_id}` — Annuler un téléchargement

**Query parameters :**

| Paramètre | Type | Description |
|-----------|------|-------------|
| `delete_files` | boolean | Supprimer les fichiers partiels | `false` |

#### `POST /downloads/{task_id}/pause` — Mettre en pause

#### `POST /downloads/{task_id}/resume` — Reprendre

#### `POST /downloads/{task_id}/retry` — Relancer

#### `DELETE /downloads` — Vider l'historique

**Query parameters :**

| Paramètre | Type | Description |
|-----------|------|-------------|
| `status` | string | Filtrer par statut |
| `older_than` | string | Durée (`7d`, `30d`) |

---

### 6. Bibliothèque

#### `GET /library` — Lister la bibliothèque

**Query parameters :**

| Paramètre | Type | Description |
|-----------|------|-------------|
| `q` | string | Recherche plein-texte (FTS5) |
| `author` | string | Filtrer par auteur |
| `genre` | string | Filtrer par genre |
| `status` | string | `ongoing`, `completed`, `hiatus`, `cancelled` |
| `language` | string | Filtrer par langue |
| `sort` | string | `title`, `author`, `added_at`, `updated_at` |
| `order` | string | `asc`, `desc` |
| `page` | integer | Page |
| `per_page` | integer | Éléments (max 100) |

**Réponse `200 OK` :**

```json
{
  "data": [
    {
      "id": "library:uuid",
      "manga": {
        "id": "mangadex:a1c2e3f4-...",
        "title": "One Piece",
        "author": "Eiichiro Oda",
        "cover_url": "https://...",
        "language": "en"
      },
      "chapters_downloaded": 1123,
      "chapters_total": 1123,
      "reading_progress": {
        "last_chapter": "chapter-1120",
        "last_page": 15,
        "percentage": 0.95
      },
      "added_at": "2026-01-10T12:00:00Z",
      "last_read_at": "2026-01-15T20:00:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 50,
    "total": 1
  }
}
```

#### `POST /library` — Ajouter à la bibliothèque

```http
POST /api/v1/library
Content-Type: application/json

{
  "manga_id": "mangadex:a1c2e3f4-...",
  "auto_download": false,
  "tags": ["favori", "action"]
}
```

#### `GET /library/{manga_id}` — Détails dans la bibliothèque

#### `PUT /library/{manga_id}` — Mettre à jour (tags, progression)

```http
PUT /api/v1/library/mangadex:a1c2e3f4-...
Content-Type: application/json

{
  "tags": ["favori"],
  "reading_progress": {
    "last_chapter": "chapter-1121",
    "last_page": 20
  }
}
```

#### `DELETE /library/{manga_id}` — Retirer de la bibliothèque

**Query parameters :**

| Paramètre | Type | Description |
|-----------|------|-------------|
| `delete_files` | boolean | Supprimer les fichiers | `false` |

#### `GET /library/scan` — Scanner les dossiers

```http
POST /api/v1/library/scan
Content-Type: application/json

{
  "paths": ["/downloads", "/manga"],
  "recursive": true,
  "extract_metadata": true
}
```

**Réponse `202 Accepted` :**

```json
{
  "scan_id": "scan-uuid",
  "paths": ["/downloads"],
  "status": "running",
  "ws_url": "ws://localhost:8000/ws/scan?scan_id=scan-uuid"
}
```

#### `GET /library/stats` — Statistiques

```json
{
  "total_manga": 42,
  "total_chapters": 5234,
  "total_size_bytes": 53687091200,
  "total_size_human": "50 GB",
  "by_language": {
    "fr": 20,
    "en": 22
  },
  "by_status": {
    "ongoing": 30,
    "completed": 12
  },
  "recently_added": [...],
  "recently_read": [...]
}
```

---

### 7. Paramètres

#### `GET /settings` — Récupérer les paramètres

**Réponse `200 OK` :**

```json
{
  "general": {
    "language": "fr",
    "theme": "dark",
    "timezone": "Europe/Paris"
  },
  "download": {
    "path": "/downloads",
    "format": "cbz",
    "max_concurrent_tasks": 3,
    "max_concurrent_pages": 8,
    "overwrite": false
  },
  "adult": {
    "enabled": false,
    "require_confirmation": true
  },
  "sites": {
    "disabled": ["example_site"],
    "overrides": {}
  }
}
```

#### `PUT /settings` — Mettre à jour les paramètres

```http
PUT /api/v1/settings
Content-Type: application/json

{
  "general": {
    "language": "en"
  },
  "download": {
    "format": "cbr",
    "max_concurrent_pages": 12
  }
}
```

#### `GET /settings/{section}` — Récupérer une section

Sections : `general`, `download`, `network`, `cloudflare`, `cookies`, `library`, `adult`, `sites`.

#### `PUT /settings/{section}` — Mettre à jour une section

#### `POST /settings/reset` — Réinitialiser

```http
POST /api/v1/settings/reset
Content-Type: application/json

{
  "section": "download",  // optionnel
  "confirm": true
}
```

---

### 8. Cookies

#### `GET /cookies` — Lister les cookies par site

**Réponse `200 OK` :**

```json
{
  "data": [
    {
      "site_id": "sushiscan_net",
      "site_name": "SushiScan",
      "cookies_count": 5,
      "has_cf_clearance": true,
      "last_updated": "2026-01-15T08:00:00Z",
      "expires_at": "2026-01-16T08:00:00Z",
      "status": "valid"
    }
  ]
}
```

#### `POST /cookies/{site_id}/refresh` — Rafraîchir les cookies

Lance Playwright pour récupérer un nouveau `cf_clearance`.

**Réponse `202 Accepted` :**

```json
{
  "site_id": "sushiscan_net",
  "status": "refreshing",
  "ws_url": "ws://localhost:8000/ws/cookies?site_id=sushiscan_net"
}
```

#### `PUT /cookies/{site_id}` — Définir les cookies manuellement

```http
PUT /api/v1/cookies/sushiscan_net
Content-Type: application/json

{
  "cookies": {
    "cf_clearance": "abc123...",
    "session": "xyz789..."
  },
  "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)..."
}
```

#### `DELETE /cookies/{site_id}` — Supprimer les cookies

---

### 9. Utilisateurs

Réservé aux administrateurs.

#### `GET /users` — Lister les utilisateurs

#### `POST /users` — Créer un utilisateur

```http
POST /api/v1/users
Content-Type: application/json

{
  "username": "newuser",
  "email": "user@example.com",
  "password": "strong-password-123",
  "role": "user",  // user, admin
  "preferences": {
    "language": "fr",
    "adult": false
  }
}
```

#### `GET /users/{user_id}` — Détails

#### `PUT /users/{user_id}` — Modifier

#### `DELETE /users/{user_id}` — Supprimer

#### `POST /users/{user_id}/reset-password` — Réinitialiser

---

### 10. Système

#### `GET /health` — État de santé

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "uptime_seconds": 86400,
  "timestamp": "2026-01-15T10:30:00Z",
  "components": {
    "database": "healthy",
    "session_pool": "healthy",
    "playwright": "healthy",
    "event_bus": "healthy"
  },
  "stats": {
    "active_downloads": 2,
    "queued_downloads": 5,
    "total_manga": 42,
    "total_chapters": 5234
  }
}
```

#### `GET /version` — Version

```json
{
  "version": "1.0.0",
  "commit": "abc123def",
  "build_date": "2026-01-15T00:00:00Z",
  "python": "3.12.7",
  "platform": "Linux-6.5.0"
}
```

#### `GET /stats` — Statistiques globales

#### `POST /system/backup` — Créer un backup

#### `POST /system/restore` — Restaurer un backup

#### `GET /logs` — Récupérer les logs

**Query parameters :**

| Paramètre | Type | Description |
|-----------|------|-------------|
| `level` | string | `INFO`, `WARNING`, `ERROR` |
| `since` | string | Timestamp ISO |
| `limit` | integer | Nombre max |

#### `DELETE /cache` — Vider le cache

---

## 📡 WebSocket

NexusDL expose un **WebSocket** pour la progression temps réel.

### Endpoint

```
ws://localhost:8000/ws/progress?token=<jwt>
```

### Message de connexion

À la connexion, envoyez un message d'abonnement :

```json
{
  "action": "subscribe",
  "channels": ["downloads", "library", "logs"],
  "filters": {
    "task_id": "550e8400-...",  // optionnel
    "user_id": "user-uuid"       // optionnel
  }
}
```

### Événements serveur → client

#### `download.started`

```json
{
  "event": "download.started",
  "timestamp": "2026-01-15T10:30:00Z",
  "data": {
    "task_id": "550e8400-...",
    "manga_title": "One Piece",
    "chapters_count": 3
  }
}
```

#### `download.progress`

```json
{
  "event": "download.progress",
  "timestamp": "2026-01-15T10:30:15Z",
  "data": {
    "task_id": "550e8400-...",
    "progress": 0.33,
    "current_chapter": {
      "id": "chapter-2",
      "title": "Chapitre 2",
      "pages_done": 15,
      "pages_total": 53
    },
    "bytes_downloaded": 52428800,
    "bytes_total": 157286400,
    "speed_bps": 2097152,
    "eta_seconds": 50
  }
}
```

#### `download.completed`

```json
{
  "event": "download.completed",
  "timestamp": "2026-01-15T10:35:00Z",
  "data": {
    "task_id": "550e8400-...",
    "output_path": "/downloads/One Piece/One Piece - Tome 01.cbz",
    "bytes_total": 157286400,
    "duration_seconds": 300,
    "chapters_completed": 3
  }
}
```

#### `download.failed`

```json
{
  "event": "download.failed",
  "timestamp": "2026-01-15T10:35:00Z",
  "data": {
    "task_id": "550e8400-...",
    "error": {
      "code": "HTTP_403",
      "message": "Access forbidden - Cloudflare challenge",
      "retry_count": 3
    }
  }
}
```

#### `library.updated`

```json
{
  "event": "library.updated",
  "timestamp": "2026-01-15T10:40:00Z",
  "data": {
    "action": "added",
    "manga_id": "mangadex:a1c2e3f4-...",
    "title": "One Piece"
  }
}
```

#### `log.entry`

```json
{
  "event": "log.entry",
  "timestamp": "2026-01-15T10:40:00Z",
  "data": {
    "level": "INFO",
    "message": "Cookie refreshed for sushiscan_net",
    "context": {
      "site": "sushiscan_net",
      "duration_ms": 1234
    }
  }
}
```

### Message client → serveur

#### `ping` / `pong`

```json
{"action": "ping"}
```

#### `unsubscribe`

```json
{
  "action": "unsubscribe",
  "channels": ["downloads"]
}
```

#### `cancel_download`

```json
{
  "action": "cancel_download",
  "task_id": "550e8400-..."
}
```

### Exemple client JavaScript

```javascript
const ws = new WebSocket(
  'ws://localhost:8000/ws/progress?token=' + accessToken
);

ws.onopen = () => {
  ws.send(JSON.stringify({
    action: 'subscribe',
    channels: ['downloads', 'library']
  }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);

  switch (msg.event) {
    case 'download.progress':
      updateProgressBar(msg.data.task_id, msg.data.progress);
      break;
    case 'download.completed':
      showNotification('Téléchargement terminé !');
      break;
    case 'download.failed':
      showError(msg.data.error.message);
      break;
  }
};

// Reconnexion automatique
ws.onclose = () => {
  setTimeout(() => connect(), 5000);
};
```

### Exemple client Python

```python
import asyncio
import json
import websockets


async def listen_progress(token: str) -> None:
    """Écoute les événements de progression."""
    uri = f"ws://localhost:8000/ws/progress?token={token}"

    async with websockets.connect(uri) as ws:
        # S'abonner
        await ws.send(json.dumps({
            "action": "subscribe",
            "channels": ["downloads", "library"],
        }))

        # Écouter
        async for message in ws:
            event = json.loads(message)

            if event["event"] == "download.progress":
                data = event["data"]
                print(f"Progression : {data['progress'] * 100:.1f}%")
            elif event["event"] == "download.completed":
                print(f"Terminé : {event['data']['output_path']}")


asyncio.run(listen_progress("your-jwt-token"))
```

---

## 🐍 Python API

NexusDL peut être utilisé comme **bibliothèque Python** dans vos scripts.

### Installation

```bash
pip install nexusdl
playwright install chromium
```

### Import

```python
from nexusdl import NexusDL
from nexusdl.core.models import Manga, Chapter, PackagingFormat
```

### Utilisation basique

```python
import asyncio
from pathlib import Path

from nexusdl import NexusDL


async def main() -> None:
    """Exemple d'utilisation basique de NexusDL."""
    async with NexusDL() as nexus:
        # Rechercher
        results = await nexus.search("one piece", languages=["en"])
        print(f"Trouvé {len(results)} résultats")

        # Récupérer un manga
        manga = await nexus.get_manga(results[0].url)
        print(f"Manga : {manga.title} ({len(manga.chapters)} chapitres)")

        # Télécharger les 5 premiers chapitres
        async for result in nexus.download(
            manga,
            dest=Path("~/Manga").expanduser(),
            fmt=PackagingFormat.CBZ,
            chapters=manga.chapters[:5],
        ):
            print(f"✅ {result.chapter.title} → {result.output_path}")


asyncio.run(main())
```

### Classe `NexusDL`

#### Constructeur

```python
NexusDL(
    config_path: Path | None = None,       # Chemin config.yaml
    *,
    config: NexusConfig | None = None,     # Config directe
    registry: SiteRegistry | None = None,  # Registre custom
    auto_start: bool = False,              # Démarrer immédiatement
)
```

#### Méthodes principales

##### `search()`

```python
async def search(
    self,
    query: str,
    *,
    sites: list[str] | None = None,
    languages: list[Language] | None = None,
    include_adult: bool = False,
    page: int = 1,
    per_site: int = 20,
    timeout: float = 10.0,
    max_concurrent: int = 8,
) -> list[SearchResult]:
    """Recherche multi-sites en parallèle.

    Args:
        query: Terme de recherche.
        sites: Sites à interroger (None = tous activés).
        languages: Langues à filtrer.
        include_adult: Inclure le contenu 18+.
        page: Page de résultats.
        per_site: Résultats par site.
        timeout: Timeout par site (secondes).
        max_concurrent: Sites interrogés en parallèle.

    Returns:
        Liste de résultats triés par pertinence.
    """
```

##### `get_manga()`

```python
async def get_manga(
    self,
    url_or_id: str,
    *,
    site: str | None = None,
) -> Manga:
    """Récupère un manga depuis son URL ou ID.

    Args:
        url_or_id: URL complète ou ID.
        site: Site source (auto-détecté si None).

    Returns:
        Manga avec métadonnées complètes.

    Raises:
        MangaNotFoundError: Si introuvable.
        ParseError: Si le parsing échoue.
    """
```

##### `download()`

```python
async def download(
    self,
    manga: Manga,
    *,
    dest: Path,
    fmt: PackagingFormat = PackagingFormat.CBZ,
    chapters: list[Chapter] | None = None,
    chapter_range: str | None = None,
    overwrite: bool = False,
    on_progress: ProgressCallback | None = None,
) -> AsyncIterator[DownloadResult]:
    """Télécharge un manga ou une sélection de chapitres.

    Args:
        manga: Manga à télécharger.
        dest: Dossier de destination.
        fmt: Format d'empaquetage.
        chapters: Chapitres spécifiques (None = tous).
        chapter_range: Plage (ex: "1-10", "all").
        overwrite: Écraser les fichiers existants.
        on_progress: Callback de progression.

    Yields:
        DownloadResult pour chaque chapitre téléchargé.

    Raises:
        DownloadError: Si un chapitre échoue définitivement.
    """
```

##### `get_sites()`

```python
async def get_sites(
    self,
    *,
    language: Language | None = None,
    include_adult: bool = False,
) -> list[SiteConfig]:
    """Liste les sites supportés."""
```

##### `refresh_cookies()`

```python
async def refresh_cookies(self, site_id: str) -> bool:
    """Rafraîchit les cookies d'un site (Cloudflare)."""
```

### Callbacks

```python
from nexusdl.core.models import DownloadProgress


async def on_progress(progress: DownloadProgress) -> None:
    """Callback appelé à chaque progression."""
    print(
        f"[{progress.task_id}] "
        f"{progress.progress * 100:.1f}% "
        f"({progress.current_chapter.title})"
    )


async with NexusDL() as nexus:
    async for result in nexus.download(
        manga,
        dest=Path("/downloads"),
        on_progress=on_progress,
    ):
        pass
```

### Utilisation avancée

#### Config personnalisée

```python
from nexusdl.core.config import NexusConfig
from nexusdl.core.models import Language, PackagingFormat

config = NexusConfig(
    language=Language.FR,
    download={
        "path": "/manga",
        "format": PackagingFormat.CBZ,
        "max_concurrent_tasks": 5,
        "max_concurrent_pages": 12,
    },
    adult={"enabled": True},
)

async with NexusDL(config=config) as nexus:
    ...
```

#### Registre custom

```python
from nexusdl.core.registry import SiteRegistry

registry = SiteRegistry.from_yaml("mon-sites.yaml")
registry.register("my_site", MySiteParser)

async with NexusDL(registry=registry) as nexus:
    ...
```

#### Téléchargement avec sélection fine

```python
from nexusdl.core.models import Chapter

async with NexusDL() as nexus:
    manga = await nexus.get_manga("https://mangadex.org/title/...")

    # Chapitres 1-10 + chapitre 15 uniquement
    chapters = [
        *manga.chapters[0:10],
        next(c for c in manga.chapters if c.number == 15),
    ]

    async for result in nexus.download(
        manga,
        dest=Path("/manga"),
        chapters=chapters,
    ):
        print(f"✅ {result.chapter.title}")
```

#### Gestion d'erreurs

```python
from nexusdl.core.exceptions import (
    NexusDLError,
    MangaNotFoundError,
    SiteUnavailableError,
    DownloadError,
    CloudflareError,
)

async with NexusDL() as nexus:
    try:
        manga = await nexus.get_manga("https://...")
    except MangaNotFoundError as e:
        print(f"Manga introuvable : {e}")
    except SiteUnavailableError as e:
        print(f"Site indisponible : {e}")
    except CloudflareError as e:
        print(f"Cloudflare challenge non résolu : {e}")
    except NexusDLError as e:
        print(f"Erreur NexusDL : {e}")
```

#### Développement d'un parser

```python
from nexusdl.parsers.base import BaseParser
from nexusdl.core.models import Chapter, Language, Manga, Page, SearchResult


class MySiteParser(BaseParser):
    """Parser pour mon-site.com."""

    site_id = "my_site"
    language = Language.FR
    adult = False
    base_url = "https://mon-site.com"

    async def search(self, query: str, *, page: int = 1) -> list[SearchResult]:
        ...

    async def get_manga(self, url_or_id: str) -> Manga:
        ...

    async def get_chapters(self, manga: Manga) -> list[Chapter]:
        ...

    async def get_pages(self, chapter: Chapter) -> list[Page]:
        ...


# Utilisation
async with NexusDL(registry=registry) as nexus:
    nexus.register_parser(MySiteParser)
    results = await nexus.search("test", sites=["my_site"])
```

### API bas niveau

Pour un contrôle total, utilisez directement les modules du core :

```python
from nexusdl.core.registry import SiteRegistry
from nexusdl.core.session import HttpSession, PlaywrightPool
from nexusdl.core.downloader import DownloadManager
from nexusdl.core.packaging import CbzPackager
from nexusdl.core.library import LibraryDatabase


async def low_level_example() -> None:
    """Exemple avec API bas niveau."""
    registry = SiteRegistry.from_yaml("sites.yaml")
    registry.load()

    pool = PlaywrightPool(max_contexts=2)
    await pool.start()

    try:
        session = HttpSession(
            site=registry.get_site("sushiscan_net"),
            playwright_pool=pool,
        )

        async with session:
            parser = registry.get_parser("sushiscan_net")
            results = await parser.search("one piece")
            print(f"{len(results)} résultats")

    finally:
        await pool.stop()
```

---

## 🚨 Codes d'erreur

Toutes les erreurs suivent le même format :

```json
{
  "error": {
    "code": "MANGA_NOT_FOUND",
    "message": "Manga not found for URL: https://...",
    "details": {
      "url": "https://...",
      "site": "mangadex"
    },
    "request_id": "req-uuid",
    "timestamp": "2026-01-15T10:30:00Z"
  }
}
```

### Codes HTTP

| Code | Signification | Quand |
|:----:|---------------|-------|
| **200** | OK | Succès |
| **201** | Created | Ressource créée |
| **202** | Accepted | Tâche asynchrone lancée |
| **204** | No Content | Succès sans corps |
| **400** | Bad Request | Paramètres invalides |
| **401** | Unauthorized | Token manquant/invalide |
| **403** | Forbidden | Permissions insuffisantes |
| **404** | Not Found | Ressource inexistante |
| **409** | Conflict | Conflit (doublon) |
| **422** | Unprocessable Entity | Validation Pydantic échouée |
| **429** | Too Many Requests | Rate limit dépassé |
| **500** | Internal Server Error | Erreur serveur |
| **502** | Bad Gateway | Site source inaccessible |
| **503** | Service Unavailable | Service en maintenance |
| **504** | Gateway Timeout | Timeout site source |

### Codes d'erreur applicatifs

| Code | HTTP | Description |
|------|:----:|-------------|
| `INVALID_CREDENTIALS` | 401 | Mauvais user/password |
| `TOKEN_EXPIRED` | 401 | Token JWT expiré |
| `TOKEN_INVALID` | 401 | Token JWT invalide |
| `INSUFFICIENT_PERMISSIONS` | 403 | Rôle insuffisant |
| `MANGA_NOT_FOUND` | 404 | Manga introuvable |
| `CHAPTER_NOT_FOUND` | 404 | Chapitre introuvable |
| `SITE_NOT_FOUND` | 404 | Site non supporté |
| `SITE_UNAVAILABLE` | 502 | Site source down |
| `CLOUDFLARE_CHALLENGE` | 502 | Challenge Cloudflare non résolu |
| `DOWNLOAD_FAILED` | 500 | Échec de téléchargement |
| `DOWNLOAD_IN_PROGRESS` | 409 | Téléchargement déjà en cours |
| `PACKAGING_FAILED` | 500 | Échec d'empaquetage |
| `INVALID_URL` | 400 | URL invalide |
| `INVALID_FORMAT` | 400 | Format non supporté |
| `RATE_LIMIT_EXCEEDED` | 429 | Trop de requêtes |
| `ADULT_CONTENT_DISABLED` | 403 | Contenu adulte désactivé |
| `FILE_TOO_LARGE` | 413 | Fichier trop volumineux |
| `DATABASE_ERROR` | 500 | Erreur BDD |
| `INTERNAL_ERROR` | 500 | Erreur interne |

### Hiérarchie Python

```python
NexusDLError                          # Base
├── ConfigurationError                # Config invalide
├── RegistryError                     # Registre sites
│   ├── SiteNotFoundError
│   └── ParserLoadError
├── SessionError                      # Session HTTP
│   ├── HttpError
│   │   ├── HttpTimeoutError
│   │   ├── HttpStatusError
│   │   └── CloudflareError
│   └── CookieError
├── ParserError                       # Parsers
│   ├── ParseError
│   ├── MangaNotFoundError
│   └── ChapterNotFoundError
├── DownloadError                     # Téléchargement
│   ├── PageDownloadError
│   ├── RetryExhaustedError
│   └── CancelledError
├── PackagingError                    # Empaquetage
│   ├── ZipSlipError
│   └── ComicInfoError
├── LibraryError                      # Bibliothèque
│   └── DatabaseError
└── AuthError                         # Auth
    ├── InvalidCredentialsError
    └── TokenExpiredError
```

---

## ⏱️ Rate limiting

L'API REST applique un **rate limiting par IP** (et par utilisateur si authentifié).

### Limites par défaut

| Endpoint | Limite | Fenêtre |
|----------|:------:|:-------:|
| `/api/auth/login` | 5 | 1 minute |
| `/api/search` | 30 | 1 minute |
| `/api/downloads` (POST) | 10 | 1 minute |
| `/api/manga/*` | 60 | 1 minute |
| `/api/library/*` | 60 | 1 minute |
| **Global** | 300 | 1 minute |

### Headers de réponse

```http
X-RateLimit-Limit: 300
X-RateLimit-Remaining: 287
X-RateLimit-Reset: 1737000000
Retry-After: 45
```

### Comportement en cas de dépassement

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/json
Retry-After: 45

{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Rate limit exceeded. Try again in 45 seconds.",
    "details": {
      "limit": 300,
      "window_seconds": 60,
      "retry_after": 45
    }
  }
}
```

### Bonnes pratiques

- ✅ **Cache** les réponses côté client
- ✅ **Backoff** exponentiel en cas de 429
- ✅ **Respectez** `Retry-After`
- ✅ **Réutilisez** les sessions HTTP
- ❌ **N'abusez pas** des recherches parallèles

---

## 📄 Pagination

Toutes les listes sont paginées.

### Query parameters

| Paramètre | Type | Défaut | Max |
|-----------|------|:------:|:---:|
| `page` | integer | `1` | — |
| `per_page` | integer | `50` | `100` |

### Réponse

```json
{
  "data": [...],
  "pagination": {
    "page": 1,
    "per_page": 50,
    "total": 1234,
    "total_pages": 25,
    "has_next": true,
    "has_prev": false,
    "next_page": 2,
    "prev_page": null
  }
}
```

### Liens de navigation (headers)

```http
Link: <http://localhost:8000/api/v1/library?page=2>; rel="next",
      <http://localhost:8000/api/v1/library?page=25>; rel="last"
```

---

## 🏷️ Versioning

NexusDL utilise un **versioning d'URL** :

```
/api/v1/...   ← v1 stable (recommandé)
/api/v2/...   ← v2 (futur)
```

### Politique de compatibilité

- ✅ **v1** restera compatible tant que v1 est supportée
- ✅ **Ajouts** (nouveaux champs, endpoints) sont rétrocompatibles
- ⚠️ **Suppressions** (champs retirés) sont réservées à une nouvelle version
- 📅 **Dépréciation** annoncée 6 mois avant

### Header de version

```http
X-NexusDL-API-Version: 1.0.0
```

---

## 💡 Exemples complets

### Exemple 1 : Recherche + téléchargement

```bash
#!/bin/bash
# search_and_download.sh

API="http://localhost:8000/api/v1"
TOKEN=$(curl -s -X POST "$API/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"xxx"}' \
  | jq -r '.access_token')

# Rechercher
RESULTS=$(curl -s "$API/search?q=one+piece&sites=mangadex" \
  -H "Authorization: Bearer $TOKEN")

MANGA_URL=$(echo "$RESULTS" | jq -r '.results[0].url')
echo "Manga trouvé : $MANGA_URL"

# Télécharger
curl -X POST "$API/downloads" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"manga_url\": \"$MANGA_URL\",
    \"chapters\": [\"1\", \"2\", \"3\"],
    \"format\": \"cbz\"
  }"
```

### Exemple 2 : Client Python complet

```python
"""Client Python pour l'API NexusDL."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx


class NexusDLClient:
    """Client API REST NexusDL."""

    def __init__(self, base_url: str = "http://localhost:8000/api/v1") -> None:
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=60.0)
        self.token: str | None = None

    async def __aenter__(self) -> NexusDLClient:
        return self

    async def __aexit__(self, *args) -> None:
        await self.client.aclose()

    async def login(self, username: str, password: str) -> None:
        """Se connecter et stocker le token."""
        response = await self.client.post(
            f"{self.base_url}/auth/login",
            json={"username": username, "password": password},
        )
        response.raise_for_status()
        self.token = response.json()["access_token"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def search(self, query: str, **kwargs) -> dict:
        """Rechercher des mangas."""
        response = await self.client.get(
            f"{self.base_url}/search",
            params={"q": query, **kwargs},
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def download(self, manga_url: str, chapters: list[str]) -> dict:
        """Lancer un téléchargement."""
        response = await self.client.post(
            f"{self.base_url}/downloads",
            json={
                "manga_url": manga_url,
                "chapters": chapters,
                "format": "cbz",
            },
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()


async def main() -> None:
    async with NexusDLClient() as client:
        await client.login("admin", "password")

        results = await client.search("one piece", sites="mangadex")
        print(f"Trouvé {results['total']} résultats")

        if results["results"]:
            manga = results["results"][0]
            task = await client.download(manga["url"], ["1", "2"])
            print(f"Téléchargement lancé : {task['task_id']}")


if __name__ == "__main__":
    asyncio.run(main())
```

### Exemple 3 : Progression temps réel

```python
"""Suivi de progression via WebSocket + API."""
from __future__ import annotations

import asyncio
import json

import httpx
import websockets


async def main() -> None:
    # 1. Login
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "http://localhost:8000/api/v1/auth/login",
            json={"username": "admin", "password": "xxx"},
        )
        token = r.json()["access_token"]

    # 2. Lancer un download
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "http://localhost:8000/api/v1/downloads",
            json={"manga_url": "https://...", "chapters": ["1"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        task = r.json()
        print(f"Task ID : {task['task_id']}")

    # 3. Écouter la progression
    uri = f"ws://localhost:8000/ws/progress?token={token}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({
            "action": "subscribe",
            "channels": ["downloads"],
            "filters": {"task_id": task["task_id"]},
        }))

        async for message in ws:
            event = json.loads(message)

            if event["event"] == "download.progress":
                p = event["data"]["progress"]
                print(f"Progression : {p * 100:.1f}%")
            elif event["event"] == "download.completed":
                print("✅ Terminé !")
                break
            elif event["event"] == "download.failed":
                print(f"❌ Échec : {event['data']['error']['message']}")
                break


asyncio.run(main())
```

---

## 🔌 SDK et clients

### Clients officiels

| Langage | Statut | Repository |
|---------|:------:|------------|
| **Python** | ✅ Inclus (`import nexusdl`) | Ce repo |
| **JavaScript/TypeScript** | 🚧 En développement | [nexusdl-js](https://github.com/NEXUS-QUANTUM/nexusdl-js) |
| **Go** | 📋 Prévu | — |
| **Rust** | 📋 Prévu | — |

### Clients communautaires

Voir [Awesome NexusDL](https://github.com/NEXUS-QUANTUM/awesome-nexusdl) pour les clients tiers.

### Génération automatique

Grâce à OpenAPI 3.1, vous pouvez générer un client dans **n'importe quel langage** :

```bash
# TypeScript
npx @openapitools/openapi-generator-cli generate \
  -i http://localhost:8000/openapi.json \
  -g typescript-fetch \
  -o ./client-ts

# Go
openapi-generator-cli generate \
  -i http://localhost:8000/openapi.json \
  -g go \
  -o ./client-go

# Rust
openapi-generator-cli generate \
  -i http://localhost:8000/openapi.json \
  -g rust \
  -o ./client-rust
```

---

## 📚 Ressources

### Documentation connexe

- 📖 **[README](../README.md)** — Vue d'ensemble
- 📖 **[CONTRIBUTING](../CONTRIBUTING.md)** — Guide de contribution
- 📖 **[SECURITY](../SECURITY.md)** — Politique de sécurité
- 📖 **[Utilisation CLI](source/usage/cli.md)** — Commandes CLI
- 📖 **[Utilisation Web](source/usage/web.md)** — Interface web
- 📖 **[Architecture](source/development/architecture.md)** — Architecture interne
- 📖 **[Ajouter un site](source/development/adding_sites.md)** — Guide parser

### Ressources externes

- 🌐 **[OpenAPI 3.1 Spec](https://spec.openapis.org/oas/v3.1.0)**
- 🌐 **[JSON:API](https://jsonapi.org/)**
- 🌐 **[REST API Best Practices](https://restfulapi.net/)**
- 🌐 **[JWT.io](https://jwt.io/)**

### Support

- 💬 **[Discord](https://discord.gg/NEXUS-QUANTUM)**
- 🐛 **[GitHub Issues](https://github.com/NEXUS-QUANTUM/nexusdl/issues)**
- 💡 **[GitHub Discussions](https://github.com/NEXUS-QUANTUM/nexusdl/discussions)**
- 📧 **[Email](mailto:nexus.quantum@protonmail.com)**

---

<div align="center">

## 🌌 NexusDL API

**Version 1.0.0**

[![GitHub](https://img.shields.io/badge/GitHub-@NEXUS--QUANTUM-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/NEXUS-QUANTUM)
[![Twitter](https://img.shields.io/badge/Twitter-@NEXUS--QUANTUM-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/NEXUS-QUANTUM)
[![Discord](https://img.shields.io/badge/Discord-@NEXUS--QUANTUM-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/NEXUS-QUANTUM)

**Fait avec ❤️ par [NEXUS-QUANTUM](https://github.com/NEXUS-QUANTUM)**

*Dernière mise à jour : 2026-09-20*

</div>
```
