"""The humanomation gate, as a page instead of a paste-the-path ritual.

The CLI loop asked a person to: open a URL, solve a challenge, save the page,
find the file they just saved, and type its path back into a terminal. Three of
those five steps are filesystem bookkeeping, not work only a human can do --
which is the opposite of what humanomation is for.

A local page collapses it. The browser that solves the challenge is already
holding the HTML; it can hand it straight back. Solve, then click.

Serves on 127.0.0.1 only, from the stdlib, with no build step and no network
dependency. It is a tool for one operator on one machine, and it should still
work in five years with no npm install.
"""
from __future__ import annotations

import json
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .gates import list_gates, resume
from .store import Store

HOST, PORT = "127.0.0.1", 8787


# --- gate normalisation -------------------------------------------------------
# open_gate() and browser.fetch() write different payload shapes: one carries
# gate_type/capture, the other leaves them null. A person must never be shown
# the word "None" where a description belongs.

def _describe(job: dict) -> dict:
    p = job.get("payload") or {}
    url = p.get("url", "")
    q = " ".join((p.get("query") or "").split())
    gate = p.get("gate_type") or "captcha"
    capture = p.get("capture") or ["the page HTML after you pass the challenge"]

    bits = parse_qs(urlparse(url).query)
    county = (bits.get("db") or [""])[0]
    lname = (bits.get("lname") or [""])[0]
    year = ((bits.get("FiledDateL") or [""])[0].split("/") or [""])[-1]

    if county and lname:
        seeking = (f"Every {county.title()} County case filed against a party named "
                   f"{lname.title()}" + (f" in {year}." if year else "."))
    else:
        seeking = f"Results for {q}." if q else "The records behind this search."

    age_s = max(0, time.time() - job.get("created_at", time.time()))
    return {
        "token": job["token"], "url": url, "query": q, "source": job.get("source", ""),
        "gate": gate, "capture": capture, "seeking": seeking,
        "county": county.title(), "lname": lname.title(), "year": year,
        "age_s": age_s, "age": _age(age_s),
    }


def _age(s: float) -> str:
    if s < 90:
        return "just now"
    if s < 5400:
        return f"{int(s // 60)} min ago"
    if s < 172800:
        return f"{int(s // 3600)} hr ago"
    return f"{int(s // 86400)} days ago"


def collect(store: Store) -> list[dict]:
    """Open gates, oldest first, with same-target duplicates collapsed.

    The live queue held the identical OSCN search parked twice under different
    tokens. Showing both invites solving the same Turnstile twice.
    """
    gates = [_describe(j) for j in list_gates(store)]
    gates.sort(key=lambda g: -g["age_s"])

    merged: dict[str, dict] = {}
    for g in gates:
        k = g["url"]
        if k in merged:
            merged[k]["also"].append(g["token"])
        else:
            g["also"] = []
            merged[k] = g
    return list(merged.values())


# --- artifact validation (the thing `gate resume` never did) -----------------

def validate(job: dict, body: str) -> tuple[bool, str]:
    """Does this artifact plausibly answer the gate it claims to?

    `gate resume` archived whatever it was handed, with a SHA-256, as the
    record of that search. A person mid-flow can easily hand back the wrong
    tab. For a tool whose product is provenance, that is the one place a human
    slip becomes a permanent false record.

    Deliberately advisory, not a lock: it rejects the clearly-wrong and warns
    on the unconvincing, because a real page can always surprise us.
    """
    if not body or len(body.strip()) < 200:
        return False, "That looks empty — under 200 characters. Copy the whole page."

    low = body.lower()
    if any(s in low for s in ("verify you are human", "just a moment",
                             "cf-browser-verification", "challenge-platform")):
        return False, "This is still the challenge page. Pass it first, then capture."

    lname = (job.get("lname") or "").lower()
    county = (job.get("county") or "").lower()
    if lname and lname not in low:
        if "found no records" in low or "no records" in low:
            return True, f"No records for {job['lname']} — that is a finding, not a failure."
        return False, (f"'{job['lname']}' does not appear anywhere in this page. "
                       "This may be a different tab.")
    if county and county not in low and "oscn" not in low:
        return True, "Captured, though the county name is absent — worth a look."
    return True, "Looks right."


# --- server -------------------------------------------------------------------

def _handler(db_path=None):
    """Build a handler that opens its OWN Store per request.

    sqlite connections belong to the thread that created them. HTTPServer is
    serial today, so one Store would in fact survive -- but binding the
    connection to the request keeps that an implementation detail rather than a
    dependency, and switching to ThreadingHTTPServer would otherwise raise
    ProgrammingError on the first POST.
    """
    from .archive import archive
    from .gate_page import render

    class H(BaseHTTPRequestHandler):
        @property
        def store(self) -> Store:
            if not hasattr(self, "_store"):
                self._store = Store(db_path)
            return self._store

        def finish(self):
            # The per-request connection is this request's to release.
            super().finish()
            st = getattr(self, "_store", None)
            if st is not None:
                st.close()

        def _local_only(self) -> bool:
            """Reject requests that did not originate from this machine's own UI.

            The page writes an archive record with a SHA-256 and closes a gate
            against it -- provenance, in a tool whose product is provenance. So
            a drive-by POST from a page the operator happens to have open must
            not reach that code.

            Host: blocks DNS rebinding, where an attacker-controlled name
            resolves to 127.0.0.1 and the browser treats it as same-origin.
            Content-Type: a cross-site form POST with enctype=text/plain is a
            CORS *simple* request and gets no preflight; requiring JSON puts the
            preflight back. Origin: belt and braces where the browser sends it.
            """
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            if host not in ("127.0.0.1", "localhost", "::1"):
                return False
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                return False
            origin = self.headers.get("Origin")
            if origin:
                oh = (urlparse(origin).hostname or "")
                if oh not in ("127.0.0.1", "localhost", "::1"):
                    return False
            return True

        def log_message(self, *a):        # keep the terminal for the operator
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if urlparse(self.path).path != "/":
                return self._send(404, "not found", "text/plain")
            self._send(200, render(collect(self.store)))

        def do_POST(self):
            if urlparse(self.path).path != "/resume":
                return self._send(404, "not found", "text/plain")
            if not self._local_only():
                return self._send(403, json.dumps(
                    {"ok": False, "message": "Refused: request did not come from the local gate page."}),
                    "application/json")
            n = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, json.dumps({"ok": False, "message": "Unreadable request."}),
                                  "application/json")

            token = data.get("token", "")
            body = data.get("body") or ""
            store = self.store
            gates = {g["token"]: g for g in collect(store)}
            g = gates.get(token)
            if not g:
                return self._send(404, json.dumps(
                    {"ok": False, "message": "That gate is no longer open."}),
                    "application/json")

            ok, message = validate(g, body)
            if not ok:
                return self._send(200, json.dumps({"ok": False, "message": message}),
                                  "application/json")

            # Archive once, then close this token AND every duplicate of it --
            # the same search parked twice should cost the operator one solve.
            rec = archive(body.encode(), f"gate-{token}.html", g["url"],
                          f"humanomation:gate-ui:{data.get('source', 'paste')}")
            cleared = 0
            for t in [token] + g["also"]:
                res, err = resume(store, t, files=None, do_archive=False)
                if err is None:
                    cleared += 1
                    store.set_job(t, "done", {"files": [rec], "resumed_at": time.time()})

            extra = f" Cleared {cleared} gates." if cleared > 1 else ""
            self._send(200, json.dumps({
                "ok": True, "sha": rec["sha256"],
                "message": f"{message} Archived.{extra}",
            }), "application/json")

    return H


def serve(store: Store | None = None, open_browser: bool = True,
          host: str = HOST, port: int = PORT) -> int:
    """Run the gate worklist until the operator stops it."""
    store = store or Store()
    n = len(collect(store))
    httpd = HTTPServer((host, port), _handler(store.path))
    url = f"http://{host}:{port}/"
    print(f"Gate worklist: {url}")
    print(f"{n} gate(s) awaiting a human." if n else "No gates are open — the page will say so.")
    print("Ctrl-C when you are done.")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0
