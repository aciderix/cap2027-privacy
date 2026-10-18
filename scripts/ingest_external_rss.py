#!/usr/bin/env python3
"""
Fetch RSS/Atom feeds for any news_sources flagged fetch_via_github=true,
then POST the raw XML to the ingest-from-xml edge function so it
parses + dedups + tags + inserts via the existing pipeline.

Used by .github/workflows/external-rss.yml. Runs from the GitHub
Actions IP pool, so it can reach endpoints that block the Supabase
egress IP (Le Figaro, X mirrors via nitter, etc.).

Required env:
    SUPABASE_URL              public, ok in workflow file
    SUPABASE_PUBLISHABLE_KEY  public anon key, ok in workflow file
    INGEST_FROM_XML_TOKEN     shared secret, MUST be a GitHub secret

Optional env:
    DRY_RUN=1                 fetch every feed and check the token, but
                              POST nothing: no write to the database.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
RSS_TIMEOUT_S = 30
POST_TIMEOUT_S = 120
PER_SOURCE_DELAY_S = 1.0  # be nice to upstream + edge function
RSS_MAX_BYTES = 10 * 1024 * 1024  # 10 MB hard cap — reject unexpectedly large bodies

_PRIVATE_IP_PREFIXES = ("127.", "10.", "169.254.", "192.168.", "::1", "fc", "fd")

# Instances nitter de repli pour les sources kind=twitter : si l'instance de
# rss_url tombe (nitter.net : HTTP 410 depuis le 21/08/2026), on essaie les
# suivantes dans l'ordre. Elles servent le même RSS (guid = id du post, liens
# /status/<id>#m, images /pic/...). Garder "nitter." dans le nom d'hôte :
# l'app réécrit ces URLs vers x.com / pbs.twimg.com via une regex sur
# "nitter." (src/lib/twitter.ts), déjà embarquée dans les APK publiés.
NITTER_MIRRORS = (
    "nitter.meowing.monster",
    "nitter.net",
    "nitter.poast.org",
    "nitter.privacydev.net",
)


def _validate_rss_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"RSS URL must use https, got: {parsed.scheme!r}")
    host = parsed.hostname or ""
    if any(host.startswith(p) for p in _PRIVATE_IP_PREFIXES):
        raise ValueError(f"RSS URL targets a private/loopback address: {host!r}")
    if all(c.isdigit() or c == "." for c in host):
        raise ValueError(f"RSS URL must use a hostname, not a bare IP: {host!r}")


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(f"missing env var: {name}\n")
        sys.exit(2)
    return v


def list_sources(supabase_url: str, key: str) -> list[dict[str, Any]]:
    url = (
        f"{supabase_url}/rest/v1/news_sources"
        "?fetch_via_github=eq.true&is_active=eq.true"
        "&select=slug,name,rss_url,kind"
    )
    req = urllib.request.Request(
        url,
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_rss(rss_url: str) -> bytes:
    _validate_rss_url(rss_url)
    req = urllib.request.Request(
        rss_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=RSS_TIMEOUT_S) as r:
        data = r.read(RSS_MAX_BYTES + 1)
    if len(data) > RSS_MAX_BYTES:
        raise ValueError(f"RSS body exceeds {RSS_MAX_BYTES // 1024 // 1024} MB limit")
    return data


def feed_urls(src: dict[str, Any]) -> list[str]:
    """rss_url, puis pour une source X la même URL sur chaque miroir nitter."""
    url = src["rss_url"]
    if src.get("kind") != "twitter":
        return [url]
    parsed = urllib.parse.urlparse(url)
    if "nitter" not in (parsed.hostname or ""):
        return [url]
    return [url] + [
        parsed._replace(scheme="https", netloc=host).geturl()
        for host in NITTER_MIRRORS
        if host != parsed.hostname
    ]


def has_items(xml: bytes) -> bool:
    # Une instance morte ou bloquée répond souvent 200 avec une page HTML
    # (anti-bot, suspension) : sans <item>/<entry>, on passe à la suivante.
    return b"<item" in xml or b"<entry" in xml


def post_xml(supabase_url: str, slug: str, token: str, xml: bytes) -> tuple[int, dict[str, Any]]:
    url = f"{supabase_url}/functions/v1/ingest-from-xml?source={urllib.parse.quote(slug)}"
    req = urllib.request.Request(
        url,
        data=xml,
        method="POST",
        headers={
            "X-Ingest-Token": token,
            "Content-Type": "application/xml",
        },
    )
    with urllib.request.urlopen(req, timeout=POST_TIMEOUT_S) as r:
        body = r.read()
        try:
            return r.status, json.loads(body)
        except json.JSONDecodeError:
            return r.status, {"raw": body.decode("utf-8", errors="replace")[:500]}


def check_token(supabase_url: str, token: str) -> bool:
    """Vérifie le jeton sans rien écrire : sur un slug inexistant, la fonction
    répond 404 « source not found » si le jeton est bon, 401 sinon (le contrôle
    du jeton passe avant toute lecture ou écriture)."""
    try:
        post_xml(supabase_url, "__dry_run_token_check__", token, b"<rss/>")
    except urllib.error.HTTPError as e:
        return e.code == 404
    return False


def main() -> int:
    supabase_url = env("SUPABASE_URL").rstrip("/")
    publishable_key = env("SUPABASE_PUBLISHABLE_KEY")
    ingest_token = env("INGEST_FROM_XML_TOKEN")
    dry_run = os.environ.get("DRY_RUN") == "1"

    if dry_run:
        ok = check_token(supabase_url, ingest_token)
        print(f"DRY RUN — no POST. Ingest token {'accepted' if ok else 'REJECTED'} by ingest-from-xml.")
        if not ok:
            return 1

    try:
        sources = list_sources(supabase_url, publishable_key)
    except Exception as e:
        print(f"ERR listing sources: {e}", flush=True)
        return 2
    if not sources:
        print("No sources flagged fetch_via_github=true. Nothing to do.")
        return 0

    print(f"Sources to fetch: {len(sources)}")
    summary: list[dict[str, Any]] = []
    for src in sources:
        slug = src["slug"]
        print(f"\n=== {slug} ({src.get('name')}) ===")
        xml = None
        errors: list[str] = []
        for rss_url in feed_urls(src):
            print(f"  GET {rss_url}")
            try:
                data = fetch_rss(rss_url)
            except urllib.error.HTTPError as e:
                errors.append(f"{rss_url}: HTTP {e.code}")
                print(f"    FETCH FAIL: HTTP {e.code}")
                continue
            except Exception as e:
                errors.append(f"{rss_url}: {e}")
                print(f"    FETCH FAIL: {e}")
                continue
            if not has_items(data):
                errors.append(f"{rss_url}: no <item>/<entry>")
                print(f"    FETCH FAIL: {len(data):,} bytes, no <item>/<entry>")
                continue
            xml = data
            print(f"  fetched {len(xml):,} bytes")
            break
        if xml is None:
            summary.append({"slug": slug, "fetch_error": " | ".join(errors)})
            continue
        if dry_run:
            summary.append({"slug": slug, "dry_run": True, "bytes": len(xml), "items": xml.count(b"<item") + xml.count(b"<entry")})
            continue

        try:
            status, body = post_xml(supabase_url, slug, ingest_token, xml)
            print(f"  POST → HTTP {status}: {json.dumps(body, ensure_ascii=False)[:300]}")
            summary.append({"slug": slug, "status": status, **{k: v for k, v in body.items() if k != "error"}})
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")[:500]
            print(f"  POST FAIL: HTTP {e.code}: {body_text}")
            summary.append({"slug": slug, "post_error": f"HTTP {e.code}: {body_text}"})
        except Exception as e:
            print(f"  POST FAIL: {e}")
            summary.append({"slug": slug, "post_error": str(e)})

        time.sleep(PER_SOURCE_DELAY_S)

    print("\n=== Summary ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    # Les sources qui marchent sont ingérées quoi qu'il arrive, mais le run
    # passe en échec dès qu'une source échoue : l'ancienne règle (échec
    # seulement si TOUT échoue) a masqué la mort de nitter.net pendant deux
    # mois parce que Le Figaro, lui, répondait.
    failures = [s for s in summary if "fetch_error" in s or "post_error" in s]
    if failures:
        print(f"{len(failures)}/{len(summary)} sources failed — exit 1")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
