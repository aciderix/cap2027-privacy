# Chaîne publique pour le FTPS Demos Kratos

Le service FTPS de `filou.o2switch.net:21` ne présentait que son certificat
serveur lors du diagnostic du 28 octobre 2026. Le fichier
`lets-encrypt-yr1-chain.pem` complète sa chaîne avec :

1. **Let's Encrypt YR1**, signé par **Root YR** ;
2. **Root YR**, certificat croisé signé par **ISRG Root X1**.

Il ne contient aucun secret ni clé privée. Sources : les URI d'émetteurs
publiées dans les certificats (`http://yr1.i.lencr.org/` et
`http://yr.i.lencr.org/`). Bien que distribués via HTTP par l'autorité,
ces certificats ont été vérifiés cryptographiquement jusqu'à la racine
ISRG Root X1 déjà reconnue par le système, avant leur ajout au dépôt.
Ils sont versionnés : aucune récupération de certificat à l'exécution.

Empreintes SHA-256 :

- YR1 : `13:94:96:34:D9:9C:D6:FD:6A:A8:0B:C0:34:FE:FA:CC:EB:19:69:FE:EF:98:65:86:71:3E:CD:BB:05:75:8D:3F`
- Root YR croisé : `07:26:39:D0:B1:40:D5:BF:FA:E1:6A:D9:C3:F6:CC:60:86:04:06:21:F5:1E:E6:1A:6D:46:A8:91:5C:07:CF:76`

`ftp_tls_context()` conserve les racines système, exige un certificat valide
et vérifie le nom du serveur. Le drapeau `VERIFY_X509_PARTIAL_CHAIN` est
explicitement désactivé : les intermédiaires ne doivent pas suffire seuls
comme ancres de confiance. Aucune désactivation de vérification TLS.

YR1 expire le 2 septembre 2028. Si le serveur change de chaîne ou après
expiration, faire corriger le serveur ou actualiser ce complément après
vérification. La solution durable est que l'hébergeur présente sa chaîne
complète ; ce complément devient alors inutile.

Tests locaux sans réseau ni identifiants :

```sh
python -m unittest discover -s scripts -p 'test_demos_kratos_tls.py'
```
