# CAP 2027 — partie publique

Dépôt public de l'application **CAP 2027** (s'informer, comparer, choisir pour la
présidentielle 2027). Il contient :

- **la politique de confidentialité** de l'application (`index.html`, publiée via
  GitHub Pages) ;
- **les tâches de collecte de données** de l'application, exécutées par GitHub Actions :

| Workflow | Rôle | Fréquence |
|---|---|---|
| `external-rss.yml` | Récupère les flux RSS que nos serveurs ne peuvent pas lire directement (X via nitter, Le Figaro…) et les transmet à la base CAP 2027 | toutes les 30 min |
| `demos-kratos-check.yml` | Vérifie le dernier export de [demos-kratos.fr](https://demos-kratos.fr) (programmes des candidats, jeu « Le Match ») et le compare à la base ; import à la demande | tous les jours (à blanc) |
| `keepalive.yml` | Garde les tâches planifiées actives (GitHub les suspend après 60 jours sans activité) | 2 fois par mois |

Aucun secret n'est stocké dans le dépôt : les jetons et identifiants sont dans les
secrets GitHub Actions. L'URL Supabase et la clé *publishable* présentes dans les
workflows sont publiques par nature (elles sont embarquées dans l'application).

## Données Demos Kratos

Données : demos-kratos.fr — extraites des programmes officiels, de manière neutre, avec
des outils utilisant l'IA. Licence
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/deed.fr).
