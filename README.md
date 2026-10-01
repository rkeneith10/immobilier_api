# ImmoPlatform — Backend

API REST Django pour la plateforme immobilière ImmoPlatform. La structure sépare les réglages communs (`config/settings/base.py`) des valeurs propres à l'environnement de développement et de production. Les domaines métier disposent chacun d'une application Django prête à évoluer ; aucun modèle métier n'est inclus à cette étape.

## Prérequis

- Python 3.11 ou supérieur
- PostgreSQL 14 ou supérieur

## Installation sous Windows PowerShell

```powershell
cd backend
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements\base.txt
Copy-Item .env.example .env
```

Renseignez les variables PostgreSQL et `DJANGO_SECRET_KEY` dans `.env`. Générez une clé avec :

```powershell
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

## Démarrage

```powershell
python manage.py check
python manage.py migrate
python manage.py runserver
```

Pour la production, définissez `DJANGO_SETTINGS_MODULE=config.settings.production` et fournissez les variables d'environnement via le gestionnaire de secrets de votre hébergeur.

## Tests

```powershell
pytest
```

Les tests utilisent PostgreSQL comme base configurée dans `.env`.

## URLs locales

- Health check : http://127.0.0.1:8000/api/health/
- OpenAPI JSON : http://127.0.0.1:8000/api/schema/
- Swagger UI : http://127.0.0.1:8000/api/docs/
- ReDoc : http://127.0.0.1:8000/api/redoc/
- Admin Django : http://127.0.0.1:8000/admin/

Les origines CORS de développement autorisées par défaut sont `http://localhost:3000` et `http://127.0.0.1:3000`. Remplacez-les avec `CORS_ALLOWED_ORIGINS` en production.


Le modèle utilisateur personnalisé est créé dans la migration initiale "users.0001_initial". Configurez une base PostgreSQL vide avant la première migration. Dans l'environnement local de développement de ce workspace, .env utilise "immoplatform_users" ; l'ancienne base "immoplatform" a été conservée sans modification.

## Authentification

- `POST /api/auth/register/` — création d'un compte utilisateur standard.
- `POST /api/auth/login/` — connexion par email et mot de passe, retourne les tokens JWT et le profil public.
- `POST /api/auth/refresh/` — renouvellement du token d'accès.
- `POST /api/auth/logout/` — révocation du refresh token.
- `GET /api/auth/me/` — lecture du profil authentifié.
- `PATCH /api/auth/me/` — modification des champs de profil autorisés.

Le modèle `users.User` est configuré comme modèle d'authentification Django. Une base PostgreSQL neuve est requise pour installer sa migration initiale si une base antérieure contient déjà les migrations du modèle utilisateur intégré de Django.
## Types de propriétés

- `GET /api/properties/types/` et `GET /api/properties/types/{uuid}/` sont publics.
- `POST`, `PATCH` et `DELETE` sont réservés aux comptes ADMIN actifs.
- Recherche : `?search=house` (nom ou slug). Filtre actif : `?is_active=true|false`.
- Pagination : `?page=2&page_size=20` (taille maximale : 100).

Les catégories initiales possibles (`HOUSE`, `APARTMENT`, `STUDIO`, `VILLA`, `ROOM`, `COMMERCIAL`, `LAND`) sont des entrées de base de données et non des choix figés dans le code. Les exemples se trouvent uniquement dans les tests.

## Amenities (équipements)

Les équipements réutilisables sont administrés comme un catalogue, et chaque propriété peut en associer plusieurs via le modèle de jonction `PropertyAmenity`.

- `GET /api/properties/amenities/` et `GET /api/properties/amenities/{uuid}/` sont publics ; les équipements inactifs ne sont visibles que par ADMIN.
- `POST`, `PATCH` et `DELETE /api/properties/amenities/` sont réservés aux comptes ADMIN actifs.
- Recherche : `?search=pool` (nom ou slug). Filtre : `?is_active=true|false`.
- Dans `POST /api/properties/` et `PATCH /api/properties/{uuid}/`, envoyer `amenities` comme une liste d’identifiants UUID existants et actifs, par exemple `"amenities": ["<uuid>"]`.

Un OWNER ou AGENT peut choisir dans ce catalogue lorsqu’il crée ou modifie sa propriété, mais ne peut pas créer d’équipement avec l’API des propriétés. Les UUID inconnus ou inactifs sont rejetés. La paire propriété/équipement est unique en base.

## Favoris

- `GET /api/favorites/` — liste paginée des favoris de l’utilisateur authentifié ; les annonces qui ne sont plus publiées n’y sont pas exposées.
- `POST /api/properties/{uuid}/favorite/` — ajouter une annonce publiée aux favoris. Répéter l’appel ne crée pas de doublon.
- `DELETE /api/properties/{uuid}/favorite/` — retirer le favori de l’utilisateur courant ; les favoris d’autres utilisateurs ne sont jamais modifiés.

Chaque favori est identifié par UUID et l’unicité utilisateur/propriété est garantie en base. La liste charge les relations de propriété et les équipements en requêtes groupées.

## Profils propriétaires et vérification

- `POST /api/auth/owner-profile/me/` — OWNER/AGENT crée son profil (`display_name`, `business_name`, `description`).
- `GET /api/auth/owner-profile/me/` et `PATCH /api/auth/owner-profile/me/` — consulter/modifier son profil. Le statut et la date de vérification sont en lecture seule.
- `POST /api/auth/owner-profile/me/verification/` — soumettre une demande. Un profil doit exister ; une seule demande peut être en attente à la fois.
- `GET /api/auth/owner-verifications/?status=PENDING` — ADMIN consulte l’historique des demandes.
- `PATCH /api/auth/owner-verifications/{uuid}/` — ADMIN examine une demande avec `{ "status": "VERIFIED" }` ou `{ "status": "REJECTED", "rejection_reason": "..." }`.

Chaque nouvelle soumission crée une entrée distincte dans l’historique. La vérification d’un profil ne peut être activée que par l’action d’administration ; l’approbation met à jour `verified_at`, et un rejet exige un motif. Après un rejet, OWNER/AGENT peut soumettre une nouvelle demande.

## Administration et modération

Toutes les routes `/api/admin/` exigent un JWT valide associé à un compte actif avec le rôle `ADMIN` (`AdminOnly`). Les routes principales sont :

- `GET /api/admin/dashboard/` — totaux utilisateurs/owners/propriétés, annonces publiées/en attente/louées, vérifications en attente et signalements.
- `GET /api/admin/users/`, `GET /api/admin/users/{uuid}/`, `PATCH /api/admin/users/{uuid}/` — consulter les comptes, filtrer/rechercher et modifier `role` ou `status`. Un administrateur ne peut pas modifier son propre rôle/statut.
- `GET /api/admin/properties/` et `GET /api/admin/properties/{uuid}/` — liste paginée de toutes les annonces non supprimées ; les actions POST sur `/{uuid}/approve/` (ou `publish/`), `reject/`, `suspend/`, `archive/` et `mark-rented/` appliquent les transitions métier existantes.
- `/api/admin/locations/`, `/api/admin/property-types/` et `/api/admin/amenities/` — CRUD des référentiels, avec les serializers et validations déjà utilisés par les APIs publiques.
- `/api/admin/owner-verifications/` et `/{uuid}/` — consultation paginée et décision d’approbation/rejet.
- `GET /api/admin/reports/`, `GET /api/admin/reports/{uuid}/`, `PATCH /api/admin/reports/{uuid}/` — examiner et traiter les signalements.
- `GET /api/admin/property-reports/`, `GET /api/admin/property-reports/{uuid}/`, `PATCH /api/admin/property-reports/{uuid}/` — consulter et traiter spécifiquement les signalements de propriétés.

Les utilisateurs authentifiés peuvent soumettre un signalement avec `POST /api/reports/`, en indiquant exactement une propriété ou un compte cible, ainsi qu’un motif. Le statut et l’identité du déclarant sont définis côté serveur. Le tableau de bord compte tous les signalements. Les listes d’administration sont paginées (25 par défaut, maximum 100) et chargent leurs relations pour éviter les requêtes N+1.

Un signalement immobilier se crée avec `POST /api/properties/{uuid}/reports/` sur une annonce publiée, avec `reason` (`FAKE_LISTING`, `WRONG_PRICE`, `PROPERTY_RENTED`, `MISLEADING_PHOTOS`, `SCAM`, `OTHER`) et une description facultative. Le déclarant, la propriété et le statut initial `PENDING` sont attribués côté serveur. Un même compte ne peut pas avoir plus d’un signalement `PENDING` ou `REVIEWED` pour la même propriété et le même motif ; après `DISMISSED` ou `ACTION_TAKEN`, un nouveau signalement est permis. L’admin peut traiter un signalement en `REVIEWED`, `DISMISSED` ou `ACTION_TAKEN`; le système conserve l’admin et l’heure de traitement. Ces signalements sont inclus dans `reports_count` du dashboard.

## Inquiries (demandes de contact)

- `POST /api/properties/{uuid}/inquiries/` — utilisateur authentifié envoie `{ "message": "..." }` à propos d’une annonce publiée. L’auteur et le propriétaire destinataire sont définis côté serveur ; les champs `owner`, `owner_id`, `user` et `property` fournis dans le corps sont refusés.
- `GET /api/inquiries/` et `GET /api/inquiries/{uuid}/` — auteur et propriétaire peuvent consulter les demandes qui les concernent ; ADMIN peut toutes les consulter.
- `PATCH /api/inquiries/{uuid}/` — permet de modifier le statut. Le propriétaire de l’annonce ou ADMIN peut le gérer ; l’auteur peut uniquement fermer sa demande (`CLOSED`).

Les demandes commencent au statut `NEW` et leur propriétaire est copié depuis l’annonce au moment de l’envoi.

## Visit requests (demandes de visite)

- `POST /api/properties/{uuid}/visits/` — demande authentifiée pour une propriété `PUBLISHED`, avec `requested_date`, `requested_time` et un `message` facultatif. Les dates/heures passées sont refusées ; `owner`, `user`, `property` et `status` sont attribués côté serveur.
- `GET /api/visit-requests/` et `GET /api/visit-requests/{uuid}/` — le demandeur voit ses demandes, le propriétaire voit celles de ses annonces, et ADMIN peut toutes les consulter.
- `PATCH /api/visit-requests/{uuid}/` — transitions autorisées : PENDING → ACCEPTED/REJECTED par le propriétaire ; PENDING → CANCELLED par le demandeur ; ACCEPTED → COMPLETED par le propriétaire.

Le corps de PATCH contient uniquement le nouveau `status`. Les dates et le message sont immuables après création.

## Notifications internes

- `GET /api/notifications/` — liste paginée des notifications du compte authentifié uniquement.
- `PATCH /api/notifications/{uuid}/read/` — marque une notification personnelle comme lue ; une notification d’un autre compte répond 404.
- `POST /api/notifications/read-all/` — marque toutes les notifications non lues du compte courant comme lues et retourne le nombre modifié.

Les notifications sont créées avec les actions métier : approbation/rejet d’annonce, nouvelle inquiry, nouvelle demande de visite et sa décision (acceptation/refus), nouvelle vérification propriétaire, et signalement d’annonce. Les événements de modération (vérification et signalement) notifient les administrateurs actifs. Les notifications restent internes et ne déclenchent pas d’envoi email.

## Base de monétisation

L’application `monetization` fournit les modèles `Plan` (FREE/PRO/BUSINESS), `Subscription`, `Payment`, `PropertyPromotion` (FEATURED/TOP_SEARCH/HOMEPAGE/BOOST) et `Invoice`, ainsi que des services métier sans endpoint de paiement. Aucun fournisseur réel n’est connecté.

Les abonnements payants commencent en `PAST_DUE`; un paiement est créé en `PENDING` et une facture en `ISSUED`. Le statut `PAID`, l’activation de l’abonnement/de la promotion et le passage de la facture en `PAID` ne surviennent qu’après `PaymentService.confirm_payment`, qui délègue à une implémentation provider abstraite pour vérifier côté serveur la référence, le montant et la devise. Le client ne dispose d’aucun endpoint permettant de définir le statut du paiement. Les plans FREE sont à prix nul et n’exigent pas de paiement.

Les limites sont configurées dans les champs `max_active_properties` (nombre, vide = illimité) et `can_promote_properties` du modèle `Plan`, modifiables dans l’administration Django. La migration initialise le plan FREE avec une limite de deux annonces et sans promotion. Les annonces en brouillon, en attente de modération ou publiées consomment une place ; les annonces rejetées, louées, archivées ou suspendues n’en consomment pas. Un abonnement payé doit être actif et dans sa période de validité pour appliquer ses droits ; sinon, le plan FREE s’applique. `SubscriptionService` centralise ces règles, avec bypass pour ADMIN.

## Recherche et filtres des propriétés

`GET /api/properties/` accepte les paramètres suivants, combinables :

| Paramètre | Fonction |
| --- | --- |
| `location`, `property_type` | UUID de la localisation ou du type |
| `listing_type` | `RENT` ou `SALE` |
| `min_price`, `max_price` | Bornes inclusives du prix |
| `min_bedrooms`, `max_bedrooms` | Bornes inclusives du nombre de chambres |
| `min_bathrooms`, `max_bathrooms` | Bornes inclusives du nombre de salles de bain |
| `furnished`, `is_featured` | Booléens (`true` ou `false`) |
| `search` | Recherche dans le titre et la description |
| `ordering` | `created_at`, `price`, `bedrooms` ou `area`; préfixer par `-` pour le décroissant |
| `page`, `page_size` | Pagination (20 par défaut, maximum 100) |

Par exemple : `GET /api/properties/?location=<uuid>&listing_type=RENT&min_price=500&max_price=1500&min_bedrooms=2&furnished=true&search=sea&ordering=-price`. Les utilisateurs publics ne reçoivent que les annonces `PUBLISHED`, même en combinant les filtres ; OWNER/AGENT voient aussi leurs propres annonces privées et ADMIN voit tous les statuts. Les détails des paramètres sont exposés dans Swagger.

## Statistiques des propriétés

- `GET /api/owner/properties/{property_id}/analytics/` — OWNER/AGENT consulte les vues, favoris, inquiries et demandes de visite d’une de ses propriétés ; les autres comptes ne peuvent pas accéder à ces chiffres.
- `GET /api/admin/analytics/` — ADMIN consulte les totaux globaux pour les propriétés non supprimées.

Une vue est enregistrée lors de l’accès à la fiche détail d’une propriété publiée. Les accès du propriétaire et des admins ne sont pas comptabilisés. Les statistiques utilisent des agrégations de nombre fixes, sans requête par élément de collection.

## Images des propriétés

Les images sont stockées sur Cloudinary. Configurez `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY` et `CLOUDINARY_API_SECRET` dans `.env` (ne partagez jamais ces valeurs). L’upload est limité à 10 Mo par défaut ; `PROPERTY_IMAGE_MAX_UPLOAD_SIZE` permet de modifier cette limite en octets. Les formats acceptés sont JPEG, PNG et WebP, vérifiés à partir du contenu du fichier.

- `POST /api/properties/{property_id}/images/` — upload `multipart/form-data` avec le champ `image`; `alt_text` et `is_primary` sont facultatifs.
- `PATCH /api/properties/{property_id}/images/{image_id}/` — modifier `alt_text`, `sort_order` ou `is_primary`.
- `DELETE /api/properties/{property_id}/images/{image_id}/` — supprimer l’image de Cloudinary et de la base.

Seul le propriétaire (OWNER/AGENT) de l’annonce ou un ADMIN peut gérer ses images. La première image devient principale automatiquement. Une seule image principale est permise par propriété ; l’ordre commence à zéro et reste continu après suppression ou réorganisation. La base garantit l’unicité de l’ordre et de l’image principale. Les tests remplacent les appels Cloudinary par des mocks et ne réalisent aucun upload réel.

## Sécurité de l’API

Les tentatives de connexion sont limitées à 5/minute par adresse cliente, l’inscription à 5/heure, le renouvellement JWT à 30/minute et la déconnexion à 10/minute. Les refresh JWT tournent à chaque utilisation et l’ancien token est mis sur liste noire. En production, configurez un cache partagé entre les workers et définissez `API_NUM_PROXIES` uniquement selon le nombre de reverse proxies de confiance ; sinon, la limitation peut être contournée ou appliquer une même adresse à tous les clients derrière un proxy. CORS accepte uniquement les origines de `CORS_ALLOWED_ORIGINS` et ne permet pas les cookies inter-origines. Le profil limite les avatars à 5 Mo par défaut. Les réglages production redirigent vers HTTPS et activent HSTS.
