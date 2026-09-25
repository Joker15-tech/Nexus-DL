
<!--
  ╔══════════════════════════════════════════════════════════════════╗
  ║                   NEXUSDL - PULL REQUEST                        ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║  Merci de contribuer à NexusDL ! 🎉                             ║
  ║                                                                  ║
  ║  Veuillez remplir ce template pour faciliter la revue de votre   ║
  ║  PR. Les sections marquées d'un * sont obligatoires.             ║
  ║                                                                  ║
  ║  ⚠️  En soumettant cette PR, vous acceptez de licencier votre    ║
  ║     contribution sous GNU GPL v3.0.                              ║
  ╚══════════════════════════════════════════════════════════════════╝
-->

## 📝 Description *

<!-- Décrivez clairement et brièvement les changements apportés. -->

<!-- Si cette PR résout une ou plusieurs issues, utilisez la syntaxe : -->
<!-- Fixes #123, Closes #456 -->

Cette PR apporte les modifications suivantes :

- 
- 
- 

## 🎯 Type de changement *

<!-- Cochez les cases appropriées avec un `x` -->

- [ ] 🐛 **Bug fix** : Correction d'un bug (non-breaking change)
- [ ] ✨ **Nouvelle fonctionnalité** : Ajout d'une feature (non-breaking change)
- [ ] 💥 **Breaking change** : Modification qui casse la compatibilité
- [ ] 📚 **Documentation** : Mise à jour ou ajout de documentation
- [ ] 🎨 **Refactoring** : Modification du code sans changer son comportement
- [ ] ⚡ **Performance** : Amélioration des performances
- [ ] 🧪 **Tests** : Ajout ou modification de tests
- [ ] 🔧 **Build/CI** : Modification du système de build ou de la CI
- [ ] 🌐 **Parser/Site** : Ajout ou mise à jour d'un parser de site
- [ ] 🎭 **UI/UX** : Amélioration de l'interface utilisateur
- [ ] 🔒 **Sécurité** : Correction de vulnérabilité
- [ ] 🌍 **i18n** : Traduction ou internationalisation
- [ ] 🐳 **Docker** : Modification des Dockerfiles ou docker-compose
- [ ] 📦 **Dépendances** : Mise à jour de dépendances

## 🔗 Issues liées

<!-- Listez les issues concernées par cette PR -->

- Fixes #<!-- numéro de l'issue -->
- Related to #<!-- numéro de l'issue -->
- Blocked by #<!-- numéro de l'issue -->

## 🏗️ Composants impactés

<!-- Cochez les zones du code modifiées -->

- [ ] **Core** (`src/nexusdl/core/`)
- [ ] **CLI** (`src/nexusdl/interfaces/cli/`)
- [ ] **GUI** (`src/nexusdl/interfaces/gui/`)
- [ ] **Web Backend** (`src/nexusdl/interfaces/web/backend/`)
- [ ] **Web Frontend** (`src/nexusdl/interfaces/web/frontend/`)
- [ ] **Parsers** (`src/nexusdl/parsers/`)
- [ ] **Plugins** (`src/nexusdl/plugins/`)
- [ ] **Configuration** (`config/`, `pyproject.toml`, `package.json`)
- [ ] **Documentation** (`docs/`, `README.md`)
- [ ] **CI/CD** (`.github/workflows/`)
- [ ] **Docker** (`docker/`)
- [ ] **Autre** : <!-- précisez -->

## 🧪 Tests

<!-- Décrivez les tests effectués -->

### Tests unitaires

- [ ] Les tests existants passent localement (`pytest` / `pnpm test`)
- [ ] De nouveaux tests ont été ajoutés pour couvrir les changements
- [ ] La couverture de code n'a pas diminué

### Tests manuels

<!-- Décrivez les tests manuels effectués pour vérifier vos changements -->

- [ ] Testé en local sur Linux
- [ ] Testé en local sur macOS
- [ ] Testé en local sur Windows
- [ ] Testé l'interface CLI
- [ ] Testé l'interface GUI
- [ ] Testé l'interface Web

### Étapes de reproduction

<!-- Si vous avez corrigé un bug, listez les étapes pour le reproduire (avant/après) -->

1. 
2. 
3. 

**Avant cette PR :**
> <!-- Décrivez le comportement avant -->

**Après cette PR :**
> <!-- Décrivez le comportement après -->

## 📸 Screenshots / Captures d'écran

<!-- Si cette PR modifie l'interface utilisateur, ajoutez des captures d'écran ou des GIFs -->

| Avant | Après |
|-------|-------|
| <!-- screenshot avant --> | <!-- screenshot après --> |

## 📋 Checklist

<!-- Vérifiez que tous les points suivants sont respectés avant de soumettre -->

### Code quality

- [ ] Mon code suit le style du projet (voir [CONTRIBUTING.md](../CONTRIBUTING.md))
- [ ] J'ai exécuté `ruff check --fix src/` et `ruff format src/` (Python)
- [ ] J'ai exécuté `pnpm lint --fix` et `pnpm format` (Frontend)
- [ ] J'ai exécuté `mypy src/nexusdl` et corrigé les erreurs de type
- [ ] J'ai exécuté `pnpm type-check` (Frontend)
- [ ] Mon code ne génère pas de nouveaux warnings
- [ ] J'ai ajouté des commentaires pour les parties complexes du code
- [ ] J'ai mis à jour les docstrings (Python) et JSDoc (TypeScript)

### Documentation

- [ ] J'ai mis à jour la documentation si nécessaire
- [ ] J'ai mis à jour le `README.md` si nécessaire
- [ ] J'ai mis à jour le `CHANGELOG.md` avec une entrée `[Unreleased]`
- [ ] J'ai documenté les nouvelles options de configuration
- [ ] J'ai documenté les nouvelles commandes CLI / endpoints API

### Breaking changes

- [ ] Cette PR contient des breaking changes (si oui, décrivez ci-dessous)
- [ ] J'ai mis à jour la documentation de migration si nécessaire
- [ ] J'ai incrémenté la version majeure dans `pyproject.toml` et `package.json`

<!-- Si breaking changes, décrivez-ici : -->
<!-- 
### ⚠️ Breaking Changes
- ...
-->

### Dependencies

- [ ] J'ai mis à jour `requirements*.txt` si de nouvelles dépendances ont été ajoutées
- [ ] J'ai mis à jour `package.json` et `pnpm-lock.yaml` si nécessaire
- [ ] Les nouvelles dépendances sont compatibles avec la licence GPL-3.0
- [ ] J'ai justifié l'ajout de chaque nouvelle dépendance

### Security

- [ ] Mon code ne contient pas de secrets (clés API, mots de passe, tokens)
- [ ] J'ai vérifié que les entrées utilisateur sont correctement validées
- [ ] J'ai vérifié qu'il n'y a pas de nouvelles vulnérabilités introduites
- [ ] J'ai exécuté `pip-audit` / `pnpm audit` et corrigé les vulnérabilités critiques

## 📖 Notes pour les reviewers

<!-- Ajoutez ici toute information utile pour faciliter la revue -->

### Points d'attention

<!-- Listez les parties du code qui nécessitent une attention particulière -->

- 
- 

### Questions ouvertes

<!-- Listez les questions ou points en suspens -->

- [ ] 
- [ ] 

### Alternatives considérées

<!-- Décrivez les autres approches que vous avez envisagées -->

- 
- 

## 🚀 Déploiement

<!-- Si cette PR nécessite des étapes spéciales pour le déploiement -->

- [ ] Aucune étape spéciale requise
- [ ] Migration de base de données nécessaire
- [ ] Variables d'environnement à ajouter :
  - `NOM_VARIABLE` : description
- [ ] Configuration à mettre à jour :
  - 
- [ ] Redémarrage des services requis

## 📜 Licence

<!-- 
  ⚠️  OBLIGATOIRE : En cochant cette case, vous certifiez que :
  
  1. Vous êtes l'auteur original de cette contribution OU vous avez
     le droit de la soumettre.
  
  2. Vous acceptez de licencier votre contribution sous les termes de
     la GNU General Public License v3.0 (GPL-3.0), conformément à la
     licence du projet NexusDL.
  
  3. Votre contribution ne viole aucun droit de propriété intellectuelle
     de tiers.
  
  Voir : https://www.gnu.org/licenses/gpl-3.0.html
-->

- [ ] **Je certifie que j'ai le droit de soumettre cette contribution sous licence GPL-3.0**

---

<div align="center">

**Merci pour votre contribution à NexusDL ! 🎉**

*Votre PR sera examinée par les mainteneurs du projet. N'hésitez pas à répondre aux commentaires de revue.*

[📖 Guide de contribution](../CONTRIBUTING.md) • [💬 Discord](https://discord.gg/nexusdl) • [📧 Contact](mailto:contact@nexusdl.dev)

</div>
```

---

### 📄 Fichier : `.github/ISSUE_TEMPLATE/bug_report.md`

```markdown
---
name: "🐛 Rapport de bug"
about: "Signaler un bug pour aider à améliorer NexusDL"
title: "[BUG] "
labels: ["bug", "needs-triage"]
assignees: []
---

<!--
  ╔══════════════════════════════════════════════════════════════════╗
  ║                    NEXUSDL - BUG REPORT                         ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║  Merci de prendre le temps de remplir ce rapport de bug ! 🐛     ║
  ║                                                                  ║
  ║  Avant de soumettre, vérifiez que le bug n'a pas déjà été        ║
  ║  signalé : https://github.com/nexusdl/nexusdl/issues             ║
  ╚══════════════════════════════════════════════════════════════════╝
-->

## 🐛 Description du bug

<!-- Décrivez clairement et brièvement en quoi consiste le bug. -->



## 🔄 Étapes pour reproduire

<!-- Fournissez les étapes détaillées pour reproduire le comportement -->

1. 
2. 
3. 
4. 

## ✅ Comportement attendu

<!-- Décrivez ce que vous attendiez qu'il se passe. -->



## ❌ Comportement actuel

<!-- Décrivez ce qui se passe réellement. -->



## 📸 Captures d'écran / Logs

<!-- Si applicable, ajoutez des captures d'écran ou des logs pour illustrer le problème. -->

<details>
<summary>Logs (cliquez pour dérouler)</summary>

```
Collez vos logs ici
```

</details>

## 🖥️ Environnement

### Système

- **OS** : <!-- ex: Ubuntu 22.04, Windows 11, macOS 14 -->
- **OS Version** : <!-- ex: 22.04.3 LTS -->
- **Architecture** : <!-- ex: x86_64, ARM64 -->

### NexusDL

- **Version** : <!-- ex: 0.1.0 (exécutez `nexusdl --version`) -->
- **Interface utilisée** :
  - [ ] CLI (Textual)
  - [ ] GUI (PyQt6)
  - [ ] Web (FastAPI + Next.js)

### Python

- **Version** : <!-- ex: 3.12.0 (exécutez `python --version`) -->
- **Installation** :
  - [ ] pip
  - [ ] pipx
  - [ ] Docker
  - [ ] Source (git clone)
  - [ ] Autre : <!-- précisez -->

### Navigateur (pour l'interface Web)

- **Navigateur** : <!-- ex: Firefox 120, Chrome 119 -->
- **Version** : 

## ⚙️ Configuration

<!-- Si pertinent, partagez votre configuration (sans informations sensibles !) -->

<details>
<summary>config.yaml (cliquez pour dérouler)</summary>

```yaml
# Collez votre configuration ici (supprimez les secrets)
```

</details>

## 🔍 Informations additionnelles

### Sites concernés

<!-- Si le bug concerne un site spécifique -->

- **Site** : <!-- ex: mangadex, asurascans -->
- **URL du manga** : <!-- si applicable -->
- **Langue du site** : <!-- ex: en, fr -->

### Contexte

<!-- Tout autre contexte qui pourrait aider à diagnostiquer le problème -->

- Fréquence d'occurrence : <!-- ex: systématique, intermittent, une seule fois -->
- Régression : <!-- Est-ce que cela fonctionnait dans une version précédente ? -->
- Workaround : <!-- Avez-vous trouvé une solution de contournement ? -->

## 📋 Checklist

- [ ] J'ai vérifié que ce bug n'a pas déjà été signalé
- [ ] J'ai testé avec la dernière version de NexusDL
- [ ] J'ai inclus toutes les informations demandées ci-dessus
- [ ] J'ai supprimé les informations sensibles (clés API, mots de passe) de mes logs/config

---

<div align="center">

**Merci pour votre rapport ! 🙏**

*Les mainteneurs examineront ce bug dans les plus brefs délais.*

</div>
```

---

### 📄 Fichier : `.github/ISSUE_TEMPLATE/feature_request.md`

```markdown
---
name: "✨ Demande de fonctionnalité"
about: "Suggérer une idée pour NexusDL"
title: "[FEATURE] "
labels: ["enhancement", "needs-triage"]
assignees: []
---

<!--
  ╔══════════════════════════════════════════════════════════════════╗
  ║                NEXUSDL - FEATURE REQUEST                        ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║  Merci de proposer une nouvelle fonctionnalité ! 💡              ║
  ║                                                                  ║
  ║  Avant de soumettre, vérifiez que cette feature n'a pas déjà     ║
  ║  été demandée : https://github.com/nexusdl/nexusdl/issues        ║
  ╚══════════════════════════════════════════════════════════════════╝
-->

## 💡 Description de la fonctionnalité

<!-- Décrivez clairement la fonctionnalité que vous souhaitez. -->



## 🎯 Problème résolu

<!-- Quel problème cette fonctionnalité résout-elle ? -->
<!-- Ex: "Je suis toujours frustré quand..." -->



## 🛠️ Solution proposée

<!-- Décrivez la solution que vous imaginez. -->



## 🎨 Maquettes / Exemples

<!-- Si applicable, ajoutez des maquettes, mockups ou exemples d'autres logiciels -->

<details>
<summary>Maquettes (cliquez pour dérouler)</summary>

<!-- Ajoutez vos images ou descriptions ici -->

</details>

## 🔄 Alternatives considérées

<!-- Décrivez les alternatives ou solutions de contournement que vous avez envisagées -->

- 
- 

## 📊 Cas d'utilisation

<!-- Décrivez des cas d'utilisation concrets -->

### Utilisateur type

<!-- Qui utiliserait cette fonctionnalité ? -->

- [ ] Utilisateur débutant
- [ ] Utilisateur avancé
- [ ] Développeur / Contributeur
- [ ] Administrateur système

### Scénarios

1. **Scénario 1** : 
   - **Contexte** : 
   - **Action** : 
   - **Résultat attendu** : 

2. **Scénario 2** : 
   - **Contexte** : 
   - **Action** : 
   - **Résultat attendu** : 

## 🎯 Priorité

<!-- Selon vous, quelle est la priorité de cette fonctionnalité ? -->

- [ ] 🔴 **Critique** : Bloquant pour mon utilisation
- [ ] 🟠 **Haute** : Amélioration majeure de l'expérience
- [ ] 🟡 **Moyenne** : Nice-to-have
- [ ] 🟢 **Basse** : Petit plus

## 🏗️ Impact technique

<!-- Si vous avez des connaissances techniques, évaluez l'impact -->

### Composants concernés

- [ ] Core (`src/nexusdl/core/`)
- [ ] CLI (`src/nexusdl/interfaces/cli/`)
- [ ] GUI (`src/nexusdl/interfaces/gui/`)
- [ ] Web Backend (`src/nexusdl/interfaces/web/backend/`)
- [ ] Web Frontend (`src/nexusdl/interfaces/web/frontend/`)
- [ ] Parsers (`src/nexusdl/parsers/`)
- [ ] Plugins (`src/nexusdl/plugins/`)
- [ ] Configuration
- [ ] Documentation
- [ ] Je ne sais pas

### Complexité estimée

- [ ] 🟢 **Faible** : Quelques heures de travail
- [ ] 🟡 **Moyenne** : Quelques jours de travail
- [ ] 🔴 **Élevée** : Plusieurs semaines de travail
- [ ] 🤷 **Je ne sais pas**

### Nouvelles dépendances requises

<!-- Listez les nouvelles dépendances qui seraient nécessaires -->

- Python : 
- Node.js : 
- Système : 

## 🤝 Contribution

<!-- Seriez-vous prêt à contribuer à l'implémentation de cette fonctionnalité ? -->

- [ ] 💪 **Oui, je peux implémenter cette feature moi-même**
- [ ] 🤝 **Oui, je peux aider à l'implémentation**
- [ ] 🧪 **Oui, je peux tester la feature**
- [ ] 📚 **Oui, je peux rédiger la documentation**
- [ ] ❌ **Non, je ne peux pas contribuer à l'implémentation**

## 📋 Checklist

- [ ] J'ai vérifié que cette feature n'a pas déjà été demandée
- [ ] J'ai décrit clairement le problème résolu
- [ ] J'ai fourni des cas d'utilisation concrets
- [ ] J'ai évalué l'impact technique (si possible)

## 📖 Contexte additionnel

<!-- Tout autre contexte, références, liens utiles -->

- 
- 

---

<div align="center">

**Merci pour votre suggestion ! 💡**

*Votre idée sera examinée par l'équipe et ajoutée au backlog si elle correspond à la vision du projet.*

</div>
```

---

### 📄 Fichier : `.github/ISSUE_TEMPLATE/new_site.md`

```markdown
---
name: "🌐 Demande d'ajout d'un site"
about: "Demander l'ajout d'un nouveau site de mangas supporté"
title: "[SITE] "
labels: ["new-site", "parser", "needs-triage"]
assignees: []
---

<!--
  ╔══════════════════════════════════════════════════════════════════╗
  ║                NEXUSDL - NEW SITE REQUEST                       ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║  Merci de suggérer un nouveau site ! 🌐                          ║
  ║                                                                  ║
  ║  Avant de soumettre, vérifiez que ce site n'a pas déjà été       ║
  ║  demandé ou supporté :                                           ║
  ║  https://github.com/nexusdl/nexusdl/issues                       ║
  ╚══════════════════════════════════════════════════════════════════╝
-->

## 🌐 Informations sur le site

### Identité

- **Nom du site** : <!-- ex: MangaDex, Asura Scans -->
- **URL principale** : <!-- ex: https://mangadex.org -->
- **URLs alternatives / miroirs** : <!-- ex: https://mangadex.to -->
- **Logo / favicon** : <!-- URL ou description -->

### Contenu

- **Langue principale** :
  - [ ] 🇬🇧 Anglais (en)
  - [ ] 🇫🇷 Français (fr)
  - [ ] 🇪🇸 Espagnol (es)
  - [ ] 🇩🇪 Allemand (de)
  - [ ] 🇮🇹 Italien (it)
  - [ ] 🇵🇹 Portugais (pt)
  - [ ] 🇷🇺 Russe (ru)
  - [ ] 🇯🇵 Japonais (ja)
  - [ ] 🇰🇷 Coréen (ko)
  - [ ] 🇨🇳 Chinois (zh)
  - [ ] 🌍 Multilingue
  - [ ] Autre : <!-- précisez -->

- **Type de contenu** :
  - [ ] Mangas (japonais)
  - [ ] Manhwas (coréen)
  - [ ] Manhuas (chinois)
  - [ ] Webtoons
  - [ ] Comics occidentaux
  - [ ] Autre : <!-- précisez -->

- **Catégorie d'âge** :
  - [ ] ✅ Tout public
  - [ ] 🔞 Contenu adulte (18+)
  - [ ] 🔀 Mixte (contenu public + adulte séparés)

### Statistiques

- **Nombre approximatif de titres** : <!-- ex: ~50,000 -->
- **Fréquence de mise à jour** : <!-- ex: quotidienne, hebdomadaire -->
- **Popularité** : <!-- ex: très populaire, niche, émergent -->

## 🔍 Analyse technique

### Accès au contenu

- **API publique** :
  - [ ] ✅ Oui, documentée
  - [ ] ⚠️ Oui, non documentée
  - [ ] ❌ Non, scraping HTML nécessaire
  
  <!-- Si API, fournissez la documentation -->
  - URL de la doc API : 

- **Structure des URLs** :
  - Page d'un manga : <!-- ex: https://example.com/manga/{id} -->
  - Page d'un chapitre : <!-- ex: https://example.com/manga/{id}/chapter/{num} -->
  - Image d'une page : <!-- ex: https://cdn.example.com/manga/{id}/{chapter}/{page}.jpg -->

### Protections anti-bot

<!-- Cochez toutes les protections présentes -->

- [ ] 🛡️ **Cloudflare** : Protection anti-DDoS / anti-bot
- [ ] 🔐 **Authentification requise** : Compte utilisateur nécessaire
- [ ] 🌐 **JavaScript requis** : Contenu rendu côté client
- [ ] 🚫 **Rate limiting** : Limitation du nombre de requêtes
- [ ] 🔑 **CAPTCHA** : Protection par captcha
- [ ] 🖼️ **Hotlinking protégé** : Images non accessibles directement
- [ ] 🌍 **Géo-bloqué** : Accessible uniquement depuis certains pays
- [ ] 🍪 **Cookies requis** : Nécessite des cookies de session
- [ ] 🚫 **Aucune protection** : Accès libre

### Qualité des images

- **Résolution maximale** : <!-- ex: 1200x1800, HD, 4K -->
- **Formats disponibles** :
  - [ ] JPEG
  - [ ] PNG
  - [ ] WebP
  - [ ] AVIF
- **Watermark** :
  - [ ] ✅ Non
  - [ ] ⚠️ Oui, discret
  - [ ] ❌ Oui, intrusif

## 🎯 Fonctionnalités souhaitées

<!-- Cochez les fonctionnalités que vous aimeriez voir supportées -->

- [ ] 🔍 Recherche de mangas
- [ ] 📖 Lecture en ligne
- [ ] 📥 Téléchargement par chapitre
- [ ] 📚 Téléchargement complet (tous les chapitres)
- [ ] 🔄 Mise à jour automatique (nouveaux chapitres)
- [ ] 🏷️ Récupération des métadonnées (tags, auteurs, statut)
- [ ] 🖼️ Récupération des couvertures
- [ ] 👥 Récupération des groupes de scanlation
- [ ] 🌍 Support multi-langue (si site multilingue)

## 📝 Exemples de contenu

<!-- Fournissez 3-5 exemples de mangas disponibles sur le site -->

1. **Titre** : <!-- ex: One Piece -->
   - URL : 
   - Nombre de chapitres : 
   
2. **Titre** : 
   - URL : 
   - Nombre de chapitres : 
   
3. **Titre** : 
   - URL : 
   - Nombre de chapitres : 

## 🔗 Sites similaires déjà supportés

<!-- Si ce site est similaire à un site déjà supporté, listez-les -->

- 
- 

## 💡 Informations additionnelles

<!-- Tout autre information utile : particularités, historique du site, communauté, etc. -->



## 🤝 Contribution

<!-- Seriez-vous prêt à contribuer au développement du parser pour ce site ? -->

- [ ] 💪 **Oui, je peux développer le parser moi-même**
- [ ] 🧪 **Oui, je peux tester le parser une fois développé**
- [ ] 📚 **Oui, je peux fournir de la documentation sur le site**
- [ ] ❌ **Non, je ne peux pas contribuer au développement**

### Compétences techniques

<!-- Si vous pouvez contribuer, quelles sont vos compétences ? -->

- [ ] Python (développement de parsers)
- [ ] HTML/CSS (analyse de structure)
- [ ] JavaScript (analyse de rendu dynamique)
- [ ] Reverse engineering (APIs non documentées)
- [ ] Tests (écriture de tests unitaires)

## 📋 Checklist

- [ ] J'ai vérifié que ce site n'est pas déjà supporté
- [ ] J'ai vérifié que ce site n'a pas déjà été demandé
- [ ] J'ai fourni toutes les informations techniques demandées
- [ ] J'ai fourni des exemples de contenu valides
- [ ] J'ai évalué les protections anti-bot présentes

## ⚖️ Considérations légales

<!-- 
  ⚠️  IMPORTANT : NexusDL ne supporte que les sites qui respectent les
     droits des auteurs et des traducteurs.
     
  Veuillez confirmer les points suivants :
-->

- [ ] Le site respecte les droits d'auteur (contenu officiel ou avec licence)
- [ ] Le site crédite correctement les auteurs et traducteurs
- [ ] Le site n'héberge pas de contenu piraté
- [ ] OU : Le site est un groupe de scanlation qui retire le contenu sur demande

<!-- Si le site ne respecte pas ces critères, il ne sera PAS ajouté. -->

---

<div align="center">

**Merci pour votre suggestion ! 🌐**

*L'équipe examinera cette demande et l'ajoutera au backlog si elle correspond aux critères du projet.*

**Note** : L'ajout d'un nouveau site dépend de plusieurs facteurs :
- Faisabilité technique
- Respect des droits d'auteur
- Demande de la communauté
- Disponibilité des contributeurs

</div>
```

---

### 📄 Fichier : `.github/ISSUE_TEMPLATE/config.yml`

```yaml
# ============================================================================
# NEXUSDL - Issue Templates Configuration
# ============================================================================
#
# Configuration des templates d'issues pour le dépôt NexusDL.
# Ce fichier définit les templates disponibles et les options supplémentaires.
#
# Documentation : https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/configuring-issue-templates-for-your-repository
# ============================================================================

blank_issues_enabled: false

contact_links:
  - name: "📖 Documentation"
    url: https://docs.nexusdl.dev
    about: "Consultez la documentation officielle avant de poser une question"
  
  - name: "💬 Discord Community"
    url: https://discord.gg/nexusdl
    about: "Rejoignez notre Discord pour discuter avec la communauté"
  
  - name: "📧 Contact"
    url: mailto:contact@nexusdl.dev
    about: "Contactez-nous directement pour les questions privées"
  
  - name: "💡 Discussions"
    url: https://github.com/nexusdl/nexusdl/discussions
    about: "Participez aux discussions générales sur le projet"
  
  - name: "🔒 Signaler une vulnérabilité de sécurité"
    url: https://github.com/nexusdl/nexusdl/security/advisories/new
    about: "Signalez une vulnérabilité de sécurité de manière confidentielle"
```
