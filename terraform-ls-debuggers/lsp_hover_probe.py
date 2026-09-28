#!/usr/bin/env python3
"""Minimal LSP client that talks raw JSON-RPC to terraform-ls over stdio and
probes textDocument/hover at given positions, bypassing the editor entirely.

Usage:
  python3 lsp_hover_probe.py \
    --tfls /path/to/terraform-ls \
    --workdir /path/to/module/dir \
    --file locals.tf \
    --at 46:concat --at 29:merge

--at LINE:WORD hovers at the first occurrence of WORD on that 1-indexed line.
--at LINE:COL  hovers at that exact 1-indexed line / 0-indexed character.
"""

import argparse
import json
import select
import subprocess
import sys
import threading
import time


def parse_target(spec, lines):
    line_str, rest = spec.split(":", 1)
    line_1idx = int(line_str)
    line_text = lines[line_1idx - 1]
    if rest.isdigit():
        char = int(rest)
    else:
        col = line_text.find(rest)
        if col == -1:
            raise ValueError(f"{rest!r} not found on line {line_1idx}: {line_text!r}")
        char = col + len(rest) // 2
    return line_1idx, rest, char


class LspClient:
    def __init__(self, tfls_path, log_file):
        self.proc = subprocess.Popen(
            [tfls_path, "serve", f"-log-file={log_file}"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        assert self.proc.stderr is not None
        self.stdin = self.proc.stdin
        self.stdout = self.proc.stdout
        self.stderr = self.proc.stderr
        self._id = 0
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self):
        for line in self.stderr:
            sys.stderr.write("[terraform-ls stderr] " + line.decode("utf-8", "replace"))

    def _next_id(self):
        self._id += 1
        return self._id

    def send(self, method, params, is_request=True):
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        if is_request:
            msg["id"] = self._next_id()
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        self.stdin.write(header + body)
        self.stdin.flush()
        return msg.get("id")

    def read_message(self):
        headers = {}
        while True:
            line = self.stdout.readline()
            if not line:
                return None
            line = line.decode("utf-8").strip()
            if line == "":
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip()] = v.strip()
        length = int(headers.get("Content-Length", "0"))
        body = self.stdout.read(length)
        return json.loads(body.decode("utf-8"))

    def drain_pending(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([self.proc.stdout], [], [], 0.3)
            if not r:
                break
            self.read_message()

    def wait_for_response(self, request_id, timeout):
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = select.select([self.proc.stdout], [], [], 1.0)
            if r:
                m = self.read_message()
                if m and m.get("id") == request_id:
                    return m
        return None

    def shutdown(self):
        self.send("shutdown", {})
        self.send("exit", {}, is_request=False)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--tfls", required=True, help="path to terraform-ls binary")
    ap.add_argument(
        "--workdir", required=True, help="module/workspace directory (used as rootUri)"
    )
    ap.add_argument("--file", required=True, help="file to open, relative to --workdir")
    ap.add_argument(
        "--at",
        action="append",
        required=True,
        dest="targets",
        help="LINE:WORD or LINE:COL to hover at; repeatable",
    )
    ap.add_argument("--log-file", default="/tmp/terraform-ls-probe.log")
    ap.add_argument(
        "--settle", type=float, default=6.0, help="seconds to wait for initial indexing"
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="seconds to wait for each hover response",
    )
    args = ap.parse_args()

    file_path = f"{args.workdir}/{args.file}"
    file_uri = "file://" + file_path
    root_uri = "file://" + args.workdir

    with open(file_path) as f:
        lines = f.read().splitlines()

    client = LspClient(args.tfls, args.log_file)

    client.send(
        "initialize",
        {
            "processId": None,
            "rootUri": root_uri,
            "workspaceFolders": [{"uri": root_uri, "name": "probe"}],
            "capabilities": {
                "textDocument": {"hover": {"contentFormat": ["markdown", "plaintext"]}}
            },
        },
    )
    # drain the initialize response (and any preceding window/showMessage notifications)
    client.drain_pending(2)

    client.send("initialized", {}, is_request=False)
    client.send(
        "textDocument/didOpen",
        {
            "textDocument": {
                "uri": file_uri,
                "languageId": "terraform",
                "version": 1,
                "text": "\n".join(lines) + "\n",
            }
        },
        is_request=False,
    )

    # Let terraform-ls finish its async indexing jobs before hovering, otherwise
    # you'll race it and get a spurious "-32800 file not found" hover error.
    time.sleep(args.settle)
    client.drain_pending(1)

    for spec in args.targets:
        line_1idx, word, char = parse_target(spec, lines)
        req_id = client.send(
            "textDocument/hover",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line_1idx - 1, "character": char},
            },
        )
        resp = client.wait_for_response(req_id, args.timeout)
        result = resp.get("result") if resp else "TIMEOUT (no response)"
        print(f"line {line_1idx:4d} col {char:3d} {word:15s} -> {result!r}"[:300])

    client.shutdown()


if __name__ == "__main__":
    main()
