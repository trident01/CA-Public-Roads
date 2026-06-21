#!/usr/bin/env python3
"""
Minimal HTTP server with Range request support for PMTiles.
Usage:  python3 scripts/serve_range.py [port]
"""
import os
import re
import sys
from http import HTTPStatus
from http.server import HTTPServer, SimpleHTTPRequestHandler


class RangeHTTPRequestHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        range_header = self.headers.get("Range", "")
        m = re.match(r"^bytes=(\d+)-(\d+)?$", range_header)
        if not m:
            return super().send_head()

        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return super().send_head()

        file_size = os.path.getsize(path)
        start = int(m.group(1))
        end_str = m.group(2)
        end = int(end_str) if end_str else file_size - 1
        if start >= file_size or start > end:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return None
        if end >= file_size:
            end = file_size - 1
        length = end - start + 1

        ctype = self.guess_type(path)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", self.date_time_string(os.path.getmtime(path)))
        self.end_headers()
        return (open(path, "rb"), start, length)

    def do_GET(self):
        f = self.send_head()
        if isinstance(f, tuple):
            source, offset, length = f
            source.seek(offset)
            remaining = length
            while remaining:
                chunk_size = min(65536, remaining)
                data = source.read(chunk_size)
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)
            source.close()
            return
        if f is None:
            return
        if f:
            super().copyfile(f, self.wfile)
            f.close()

    def do_HEAD(self):
        f = self.send_head()
        if isinstance(f, tuple):
            f[0].close()
        elif f:
            f.close()

    def guess_type(self, path):
        import mimetypes
        ctype, _ = mimetypes.guess_type(path)
        return ctype or "application/octet-stream"


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    server = HTTPServer(("", port), RangeHTTPRequestHandler)
    print(f"Serving with Range support at http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
