"""Mock Plex + TMDb upstreams for Luma e2e tests (no network/API key needed).

Plex search and TMDb search are deliberately *literal* (case-insensitive
substring), like the real services, so typo tolerance must come from Luma.
"""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from xml.sax.saxutils import quoteattr

MOVIES = [
    (1, "Oppenheimer", 2023, 872585), (2, "Interstellar", 2014, 157336),
    (3, "The Godfather", 1972, 238), (4, "The Shawshank Redemption", 1994, 278),
    (5, "The Godfather Part II", 1974, 240), (6, "Inception", 2010, 27205),
    (7, "Interstate 60", 2002, 15000), (8, "The Dark Knight", 2008, 155),
    (9, "Open Range", 2003, 9000), (10, "Godzilla", 2014, 124905),
    (11, "Shaun of the Dead", 2004, 747), (12, "Interview with the Vampire", 1994, 628),
    (13, "Up", 2009, 14160), (14, "Upgrade", 2018, 500664), (15, "Amélie", 2001, 194),
    (16, "Superbad", 2007, 8363), (17, "Grown Ups", 2010, 38365),
]
import os, random
_SEED = int(os.environ.get("MOCK_SEED_ITEMS", "0"))
_rng = random.Random(42)
_W = "shadow river night city lost star dark king queen last first blood iron storm silent ghost secret golden broken winter summer red blue black white house road war love dream fire".split()
for i in range(_SEED):
    MOVIES.append((100000 + i, " ".join(_rng.choice(_W).title() for _ in range(_rng.randint(1, 4))), 1950 + i % 75, 9000000 + i))
STATE = {"mode": "ok", "hits": 0}
TV = [(101, "Breaking Bad", 2008, 1396), (102, "The Office", 2005, 2316)]

def sub(q, t):
    return q.lower() in t.lower()

def tmdb_match(q, t):
    """Literal like TMDb: full substring, or any whole query word (>=4 chars) in the title."""
    q, t = q.lower(), t.lower()
    return q in t or any(len(w) >= 4 and w in t.split() for w in q.split())

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, body, ctype):
        b = body.encode()
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urlparse(self.path); p = u.path; qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        q = qs.get("query", "")
        # ---------- TMDb ----------
        if p == "/__ctl":
            if "mode" in qs: STATE["mode"] = qs["mode"]
            if "reset" in qs: STATE["hits"] = 0
            return self.send(json.dumps(STATE), "application/json")
        if p.startswith("/tmdb"):
            p = p[5:]
            if p.startswith("/search/"):  # count only searches, not background /popular pages
                STATE["hits"] += 1
            if STATE["mode"] == "429":
                self.send_response(429); self.send_header("Retry-After", "30")
                self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(b'{"status_code":25,"status_message":"Your request count is over the allowed limit"}'); return
            mv = lambda m: {"id": m[3], "title": m[1], "release_date": f"{m[2]}-01-01", "media_type": "movie",
                            "popularity": 50.0, "vote_average": 8.0, "vote_count": 1000, "poster_path": None, "overview": ""}
            tv = lambda s: {"id": s[3], "name": s[1], "first_air_date": f"{s[2]}-01-01", "media_type": "tv",
                            "popularity": 40.0, "vote_average": 8.0, "vote_count": 900, "poster_path": None, "overview": ""}
            base = MOVIES[:17]
            if p == "/search/movie": r = [mv(m) for m in base if tmdb_match(q, m[1])]
            elif p == "/search/tv": r = [tv(s) for s in TV if tmdb_match(q, s[1])]
            elif p == "/search/multi": r = [mv(m) for m in base if tmdb_match(q, m[1])] + [tv(s) for s in TV if tmdb_match(q, s[1])]
            elif p == "/search/person": r = []
            elif p == "/movie/popular": r = [mv(m) for m in MOVIES[:17]] if qs.get("page") == "1" else []
            elif p == "/tv/popular": r = [tv(s) for s in TV] if qs.get("page") == "1" else []
            else: r = []
            return self.send(json.dumps({"page": 1, "results": r, "total_results": len(r)}), "application/json")
        # ---------- Plex ----------
        want_json = "json" in self.headers.get("Accept", "")
        if p == "/":
            return self.send('<MediaContainer friendlyName="MockPlex" machineIdentifier="mock" version="1.40"/>', "text/xml")
        if p == "/library/sections":
            if want_json:
                return self.send(json.dumps({"MediaContainer": {"Directory": [
                    {"key": "1", "title": "Movies", "type": "movie"}, {"key": "2", "title": "TV Shows", "type": "show"}]}}), "application/json")
            return self.send('<MediaContainer><Directory key="1" title="Movies" type="movie"><Location path="/m"/></Directory>'
                             '<Directory key="2" title="TV Shows" type="show"><Location path="/t"/></Directory></MediaContainer>', "text/xml")
        if p.endswith("/all"):
            sid = p.split("/")[3]; start = int(qs.get("X-Plex-Container-Start", 0))
            items = MOVIES if sid == "1" else TV
            if not want_json:
                tag = "Video" if sid == "1" else "Directory"
                els = "".join(f'<{tag} ratingKey="{i[0]}" title={quoteattr(i[1])} year="{i[2]}" type="{"movie" if sid=="1" else "show"}"/>' for i in items)
                return self.send(f'<MediaContainer size="{len(items)}" totalSize="{len(items)}">{els}</MediaContainer>', "text/xml")
            meta = [] if start else [{"ratingKey": i[0], "title": i[1], "year": i[2], "viewCount": 1} for i in items]
            return self.send(json.dumps({"MediaContainer": {"Metadata": meta, "totalSize": len(items)}}), "application/json")
        if p.endswith("/search"):
            sid = p.split("/")[3]
            if sid == "1":
                els = "".join(f'<Video ratingKey="{m[0]}" title={quoteattr(m[1])} year="{m[2]}" type="movie" addedAt="1700000000"/>'
                              for m in MOVIES if sub(q, m[1]))
            else:
                els = "".join(f'<Directory ratingKey="{s[0]}" title={quoteattr(s[1])} year="{s[2]}" type="show"/>'
                              for s in TV if sub(q, s[1]))
            return self.send(f"<MediaContainer>{els}</MediaContainer>", "text/xml")
        self.send("<MediaContainer/>", "text/xml")

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 5299), H).serve_forever()
