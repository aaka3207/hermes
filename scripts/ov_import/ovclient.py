"""Minimal OpenViking HTTP client for the import scripts.

Reads url and api_key from ~/.openviking/ovcli.conf (the `ov` CLI profile) or the
OV_URL / OV_API_KEY environment variables. Never logs the key.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


RETRY_STATUS = {502, 503, 504, 520, 521, 522, 523, 524}


class OVError(RuntimeError):
    pass


class OV:
    def __init__(self, url=None, api_key=None, timeout=120, retries=4):
        conf = {}
        path = Path(os.path.expanduser("~/.openviking/ovcli.conf"))
        if path.exists():
            conf = json.loads(path.read_text())
        self.url = (url or os.environ.get("OV_URL") or conf.get("url", "")).rstrip("/")
        self.key = api_key or os.environ.get("OV_API_KEY") or conf.get("api_key")
        self.timeout = timeout
        self.retries = retries
        if not self.url or not self.key:
            raise OVError("no OpenViking url/api_key (ovcli.conf or OV_URL/OV_API_KEY)")

    def _req(self, method, path, params=None, body=None, raw=False):
        url = f"{self.url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-API-Key", self.key)
        req.add_header("User-Agent", "ov-import/0.1 (hermes scripts)")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        # Retry connection failures and gateway errors (the server sits behind Cloudflare and
        # drops connections intermittently). Every call these scripts make is safe to repeat.
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = resp.read()
                break
            except urllib.error.HTTPError as e:
                if e.code in RETRY_STATUS and attempt < self.retries:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise OVError(f"{method} {path} -> {e.code}: {e.read()[:400].decode(errors='replace')}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt == self.retries:
                    raise OVError(f"{method} {path}: {e}") from None
                time.sleep(5 * (attempt + 1))
        if raw:
            return payload
        doc = json.loads(payload)
        if doc.get("status") not in ("ok", None) and not doc.get("ok"):
            raise OVError(f"{method} {path}: {str(doc)[:400]}")
        return doc.get("result", doc)

    # content
    def download(self, uri):
        return self._req("GET", "/api/v1/content/download", {"uri": uri}, raw=True)

    def read(self, uri):
        return self._req("GET", "/api/v1/content/read", {"uri": uri})

    def abstract(self, uri):
        return self._req("GET", "/api/v1/content/abstract", {"uri": uri})

    def overview(self, uri):
        return self._req("GET", "/api/v1/content/overview", {"uri": uri})

    def write(self, uri, content, mode="replace", tags=None, tag_mode="replace", wait=False):
        body = {"uri": uri, "content": content, "mode": mode, "wait": wait}
        if tags is not None:
            body.update(tags=tags, tag_mode=tag_mode)
        return self._req("POST", "/api/v1/content/write", body=body)

    def set_tags(self, uri, tags, mode="append", recursive=False):
        return self._req("POST", "/api/v1/content/set_tags",
                         body={"uri": uri, "tags": tags, "mode": mode, "recursive": recursive})

    # filesystem
    def ls(self, uri, recursive=False):
        return self._req("GET", "/api/v1/fs/ls", {"uri": uri, "recursive": str(recursive).lower(),
                                                  "node_limit": 10000})

    def stat(self, uri):
        return self._req("GET", "/api/v1/fs/stat", {"uri": uri})

    # retrieval
    def find(self, query, target_uri=None, limit=10, tags=None, level=None, **extra):
        body = {"query": query, "limit": limit}
        if target_uri:
            body["target_uri"] = target_uri
        if tags:
            body["tags"] = tags
        if level is not None:
            body["level"] = level
        body.update(extra)
        return self._req("POST", "/api/v1/search/find", body=body)

    def grep(self, uri, pattern, tags=None, **extra):
        body = {"uri": uri, "pattern": pattern}
        if tags:
            body["tags"] = tags
        body.update(extra)
        return self._req("POST", "/api/v1/search/grep", body=body)

    def task(self, task_id):
        return self._req("GET", f"/api/v1/tasks/{task_id}")
