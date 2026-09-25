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
