"""A tiny fake of the IrroCloud website, for exercising the browser fetcher.

Standard-library HTTP server in a thread. It imitates just enough of a sensor
portal: a login form that sets a cookie, a device list, a device page with a
date range and an Export link that downloads a modern-layout CSV, an account
page, and a logout link. Optional failure modes let tests prove the fetcher
stops (rather than guesses) on a login code page or a bad password.

The real IrroCloud will differ — that is what the morning `discover` run is
for. This fake proves the PLUMBING: login, navigation, download handling,
validation, screenshots on failure.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VALID_EMAIL = "grower@example.com"
VALID_PASSWORD = "tension123"

DEVICES = {
    "1": "SBF Cunningham 6",
    "2": "SBF RV80",
    "3": "SBF Bennett 1N",
    "4": "SBF Whitted",
}

DATA_END = datetime(2026, 8, 24, 6, 0)  # matches the synthetic season's end


def export_csv(device_id: str, start: str, end: str) -> str:
    """Deterministic modern-layout CSV for the requested window."""
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end) + timedelta(days=1)
    except ValueError:
        start_dt = DATA_END - timedelta(days=3)
        end_dt = DATA_END
    end_dt = min(end_dt, DATA_END)
    lines = ["Timestamp,SM1,SM2,SM3,SM4,SM5,SM6"]
    base = 20 + int(device_id) * 3
    t = start_dt
    hour = 0
    while t <= end_dt:
        values = [base + (hour + i * 2) % 17 for i in range(6)]
        lines.append(t.strftime("%Y-%m-%d %H:%M:%S") + "," + ",".join(map(str, values)))
        t += timedelta(hours=1)
        hour += 1
    return "\n".join(lines) + "\n"


PAGES = {
    "login": """<!doctype html><html><head><title>IrroCloud — sign in</title></head>
<body><h1>IrroCloud</h1>{error}
<form method="post" action="/login">
  <label>Email <input type="email" name="email"></label>
  <label>Password <input type="password" name="password"></label>
  <button type="submit">Sign in</button>
</form></body></html>""",
    "twofa": """<!doctype html><html><head><title>Verify it's you</title></head>
<body><h1>Verify it's you</h1><p>Enter the code we texted to your phone.</p>
<form method="post" action="/verify"><input name="code"><button>Verify</button></form>
</body></html>""",
    "devices": """<!doctype html><html><head><title>IrroCloud — devices</title></head>
<body><nav><a href="/devices">Devices</a> <a href="/account">Account</a>
<a href="/logout">Log out</a></nav>
<h1>Your devices</h1><ul>{items}</ul></body></html>""",
    "device": """<!doctype html><html><head><title>{name}</title></head>
<body><nav><a href="/devices">Devices</a> <a href="/account">Account</a>
<a href="/logout">Log out</a></nav>
<h1>{name}</h1>
<p>Last reading: 2026-08-24 06:00 (device local time)</p>
<p>Sensors: SM1 12in, SM2 18in, SM3 12in, SM4 18in, SM5 12in, SM6 18in</p>
<form>
  <label>Start <input type="date" name="start" id="start"></label>
  <label>End <input type="date" name="end" id="end"></label>
</form>
<a id="export" href="/devices/{device_id}/export?start=&end=" download>Export CSV</a>
<script>
  const link = document.getElementById('export');
  function sync() {{
    const s = document.getElementById('start').value;
    const e = document.getElementById('end').value;
    link.href = `/devices/{device_id}/export?start=${{s}}&end=${{e}}`;
  }}
  document.getElementById('start').addEventListener('change', sync);
  document.getElementById('end').addEventListener('change', sync);
</script>
</body></html>""",
    "account": """<!doctype html><html><head><title>Account</title></head>
<body><nav><a href="/devices">Devices</a> <a href="/logout">Log out</a></nav>
<h1>Account settings</h1>
<p>Plan: Grower. API access: contact Irrometer support to enable data API.</p>
<p>Users: grower@example.com (owner)</p></body></html>""",
}


class FakeIrroCloud:
    """Run with:  with FakeIrroCloud() as fake: ... fake.base_url ..."""

    def __init__(self, twofa: bool = False):
        self.twofa = twofa
        self.request_log: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _logged_in(self) -> bool:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                return cookie.get("session") is not None and cookie["session"].value == "ok"

            def _send_html(self, body: str, status: int = 200, headers=None):
                payload = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                outer.request_log.append(f"GET {self.path}")
                url = urlparse(self.path)
                if url.path in ("/", "/login"):
                    return self._send_html(PAGES["login"].format(error=""))
                if url.path == "/logout":
                    return self._send_html(
                        PAGES["login"].format(error="<p>Signed out.</p>"),
                        headers={"Set-Cookie": "session=; Max-Age=0"},
                    )
                if not self._logged_in():
                    return self._send_html(PAGES["login"].format(error=""), status=200)
                if url.path == "/devices":
                    items = "".join(
                        f'<li><a href="/devices/{device_id}">{name}</a></li>'
                        for device_id, name in DEVICES.items()
                    )
                    return self._send_html(PAGES["devices"].format(items=items))
                if url.path == "/account":
                    return self._send_html(PAGES["account"])
                parts = url.path.strip("/").split("/")
                if len(parts) == 2 and parts[0] == "devices" and parts[1] in DEVICES:
                    return self._send_html(
                        PAGES["device"].format(name=DEVICES[parts[1]], device_id=parts[1])
                    )
                if (
                    len(parts) == 3
                    and parts[0] == "devices"
                    and parts[1] in DEVICES
                    and parts[2] == "export"
                ):
                    query = parse_qs(url.query)
                    body = export_csv(
                        parts[1],
                        query.get("start", [""])[0],
                        query.get("end", [""])[0],
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv")
                    self.send_header(
                        "Content-Disposition",
                        f'attachment; filename="{DEVICES[parts[1]]}.csv"',
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if url.path == "/csv":
                    # The real site's endpoint (discovery, 2026-08-24):
                    # GET /csv?&id=<device id> returns the full history.
                    device_id = parse_qs(url.query).get("id", [""])[0]
                    if device_id not in DEVICES:
                        return self._send_html("<h1>Not found</h1>", status=404)
                    body = export_csv(device_id, "", "").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv")
                    self.send_header(
                        "Content-Disposition",
                        'attachment; filename="irrocloud_data.csv"',
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self._send_html("<h1>Not found</h1>", status=404)

            def do_POST(self):
                outer.request_log.append(f"POST {self.path}")
                length = int(self.headers.get("Content-Length", 0))
                form = parse_qs(self.rfile.read(length).decode())
                if self.path == "/login":
                    email = form.get("email", [""])[0]
                    password = form.get("password", [""])[0]
                    if email == VALID_EMAIL and password == VALID_PASSWORD:
                        if outer.twofa:
                            return self._send_html(PAGES["twofa"])
                        self.send_response(302)
                        self.send_header("Set-Cookie", "session=ok; Path=/")
                        self.send_header("Location", "/devices")
                        self.end_headers()
                        return
                    return self._send_html(
                        PAGES["login"].format(error="<p>Wrong email or password.</p>")
                    )
                self._send_html("<h1>Not found</h1>", status=404)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> FakeIrroCloud:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
