#!/usr/bin/env python3
"""
Contrôle EN LECTURE SEULE de l'export Demos Kratos (FTPS).

Liste le dossier, prend le dernier `demos-kratos-programmes-*.json` et le
dernier `demos-kratos-match-*.json` (les `.part` sont des envois en cours,
ignorés), les télécharge en mémoire et vérifie leur cohérence : format,
version, compteurs `meta.counts`, identifiants uniques et références entre
tableaux. N'écrit nulle part : ni base, ni fichier (hors résumé GitHub).

Format décrit dans le README.md déposé par Demos Kratos à côté des exports.

Env (secrets GitHub Actions) :
    DK_FTP_HOST, DK_FTP_USER, DK_FTP_PASSWORD   obligatoires
    DK_FTP_DIR                                  optionnel (défaut : dossier de connexion)

Test sans FTP :  python3 demos_kratos_check.py --local-dir <dossier des JSON>

Les logs du dépôt étant publics, on n'affiche que noms de fichiers, tailles,
dates, empreintes et compteurs, jamais le contenu ni les identifiants FTP.

Code retour : 0 export valide, 1 export absent ou invalide, 2 configuration.
"""

from __future__ import annotations

import argparse
import ftplib
import io
import json
import os
import ssl
import sys
from collections import Counter
from typing import Any

KINDS = {
    "programmes": ("demos-kratos-programmes-", "demos-kratos/programmes"),
    "match": ("demos-kratos-match-", "demos-kratos/match"),
}
SUPPORTED_VERSION = 1
MAX_BYTES = 100 * 1024 * 1024  # garde-fou : l'export programmes fait ~5 Mo


class ReusedSessionFTP_TLS(ftplib.FTP_TLS):
    """FTPS explicite qui réutilise la session TLS du canal de contrôle sur le
    canal de données : beaucoup de serveurs l'exigent (« 522 … session reuse
    required ») et ftplib ne le fait pas tout seul."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


def latest(names: list[str], prefix: str) -> str | None:
    # Horodatage UTC dans le nom : l'ordre alphabétique est chronologique.
    candidates = sorted(n for n in names if n.startswith(prefix) and n.endswith(".json"))
    return candidates[-1] if candidates else None


def fetch_ftp(directory: str | None) -> tuple[list[str], dict[str, bytes]]:
    # strip() : un espace ou un retour à la ligne collé avec le secret suffit à
    # faire refuser la connexion.
    host, user, password = ((os.environ.get(k) or "").strip() for k in ("DK_FTP_HOST", "DK_FTP_USER", "DK_FTP_PASSWORD"))
    if not (host and user and password):
        print("Secrets DK_FTP_HOST / DK_FTP_USER / DK_FTP_PASSWORD manquants.")
        raise SystemExit(2)
    ftp = ReusedSessionFTP_TLS(context=ssl.create_default_context(), timeout=60)
    ftp.connect(host, 21)
    try:
        ftp.login(user, password)
    except ftplib.error_perm as e:
        # TLS déjà établi à ce stade : seul le couple identifiant / mot de passe est en cause.
        print(f"Connexion FTPS établie, mais identifiants refusés par le serveur : {e}")
        print("Vérifier DK_FTP_USER et DK_FTP_PASSWORD (valeur exacte, sans espace ni guillemets).")
        raise SystemExit(2)
    ftp.prot_p()
    if directory:
        ftp.cwd(directory)
    names = [n.rsplit("/", 1)[-1] for n in ftp.nlst()]
    files: dict[str, bytes] = {}
    for prefix, _ in KINDS.values():
        name = latest(names, prefix)
        if not name:
            continue
        buf = io.BytesIO()

        def sink(chunk: bytes, buf=buf) -> None:
            if buf.tell() + len(chunk) > MAX_BYTES:
                raise ValueError(f"{name} dépasse {MAX_BYTES // 1024 // 1024} Mo")
            buf.write(chunk)

        ftp.retrbinary(f"RETR {name}", sink)
        files[name] = buf.getvalue()
    ftp.quit()
    return names, files


def read_local(directory: str) -> tuple[list[str], dict[str, bytes]]:
    names = sorted(os.listdir(directory))
    files = {}
    for prefix, _ in KINDS.values():
        name = latest(names, prefix)
        if name:
            with open(os.path.join(directory, name), "rb") as f:
                files[name] = f.read()
    return names, files


def dup_ids(rows: list[dict[str, Any]]) -> int:
    return sum(n - 1 for n in Counter(r.get("id") for r in rows).values() if n > 1)


def check(kind: str, doc: dict[str, Any]) -> list[str]:
    """Liste des problèmes trouvés (vide = export cohérent)."""
    errors: list[str] = []
    meta = doc.get("meta") or {}
    if meta.get("format") != KINDS[kind][1]:
        errors.append(f"format inattendu : {meta.get('format')!r}")
    if meta.get("version") != SUPPORTED_VERSION:
        errors.append(f"version {meta.get('version')!r} non supportée (attendu {SUPPORTED_VERSION})")
    if not meta.get("contentHash"):
        errors.append("meta.contentHash absent")
    for key, n in (meta.get("counts") or {}).items():
        if len(doc.get(key) or []) != n:
            errors.append(f"meta.counts.{key} = {n} mais {len(doc.get(key) or [])} éléments")

    if kind == "programmes":
        themes = {t["id"] for t in doc.get("themes", [])}
        cands = {c["id"] for c in doc.get("candidates", [])}
        measures = {m["id"] for m in doc.get("measures", [])}
        for key in ("themes", "candidates", "measures"):
            if dup_ids(doc.get(key, [])):
                errors.append(f"{key} : identifiants en double")
        bad = sum(1 for m in doc.get("measures", [])
                  if m.get("candidateId") not in cands or not m.get("themeIds")
                  or any(t not in themes for t in m["themeIds"]))
        if bad:
            errors.append(f"{bad} mesures avec candidat ou thème inconnu")
        bad = sum(1 for s in doc.get("syntheses", [])
                  if s.get("candidateId") not in cands
                  or (s.get("themeId") is not None and s["themeId"] not in themes)
                  or any(k not in measures for k in s.get("keyMeasureIds", [])))
        if bad:
            errors.append(f"{bad} synthèses avec référence inconnue")
        bad = sum(1 for a in doc.get("affinities", [])
                  if a.get("candidateAId") not in cands or a.get("candidateBId") not in cands
                  or (a.get("themeId") is not None and a["themeId"] not in themes))
        if bad:
            errors.append(f"{bad} affinités avec référence inconnue")
    else:
        stmts = {s["id"] for s in doc.get("statements", [])}
        cands = {c["id"] for c in doc.get("candidates", [])}
        if dup_ids(doc.get("statements", [])):
            errors.append("statements : identifiants en double")
        bad = sum(1 for s in doc.get("stances", [])
                  if s.get("statementId") not in stmts or s.get("candidateId") not in cands
                  or s.get("stance") not in ("FOR", "RATHER_FOR", "RATHER_AGAINST", "AGAINST"))
        if bad:
            errors.append(f"{bad} positions avec référence ou valeur inconnue")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Contrôle en lecture seule de l'export Demos Kratos")
    ap.add_argument("--local-dir", help="lire les JSON dans ce dossier au lieu du FTP")
    args = ap.parse_args()

    names, files = read_local(args.local_dir) if args.local_dir else fetch_ftp(os.environ.get("DK_FTP_DIR"))
    partial = [n for n in names if n.endswith(".part")]
    print(f"Dossier : {len(names)} fichiers ({len(partial)} envoi(s) en cours ignoré(s))")

    lines = ["| Export | Fichier | Taille | Généré le | contentHash | Contenu | État |", "|---|---|---|---|---|---|---|"]
    status = 0
    for kind, (prefix, _) in KINDS.items():
        name = next((n for n in files if n.startswith(prefix)), None)
        if not name:
            print(f"\n[{kind}] aucun fichier {prefix}*.json")
            lines.append(f"| {kind} | — | — | — | — | — | ❌ absent |")
            status = 1
            continue
        raw = files[name]
        try:
            doc = json.loads(raw)
            errors = check(kind, doc)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            doc, errors = {}, [f"JSON illisible : {e}"]
        meta = doc.get("meta") or {}
        counts = ", ".join(f"{k} {v}" for k, v in (meta.get("counts") or {}).items())
        print(f"\n[{kind}] {name} — {len(raw):,} octets")
        print(f"  generatedAt  {meta.get('generatedAt')}")
        print(f"  contentHash  {meta.get('contentHash')}")
        print(f"  counts       {counts}")
        for e in errors:
            print(f"  ERREUR       {e}")
        print(f"  => {'VALIDE' if not errors else 'INVALIDE'}")
        if errors:
            status = 1
        lines.append(f"| {kind} | `{name}` | {len(raw) / 1024:,.0f} Ko | {meta.get('generatedAt')} | "
                     f"`{str(meta.get('contentHash'))[:12]}…` | {counts} | "
                     f"{'✅ valide' if not errors else '❌ ' + '; '.join(errors)} |")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## Export Demos Kratos (contrôle en lecture seule)\n\n" + "\n".join(lines) + "\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
