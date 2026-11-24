"""手动 UI 冒烟脚本：需要先启动 Edge remote debugging（默认 9226）。

用法：先启动应用和 Edge，然后 `python tests/ui_smoke.py`。
"""
import base64
import json
import os
import socket
import struct
import time
import urllib.request

PORT = os.environ.get("CDP_PORT", "9226")


class CDP:
    def __init__(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=10) as r:
            targets = json.load(r)
        target = next(t for t in targets if t.get("type") == "page" and "127.0.0.1:8765" in t.get("url", ""))
        hostport, path = target["webSocketDebuggerUrl"].split("://", 1)[1].split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.sock.recv(4096)
        self.next_id = 0
        self.call("Runtime.enable")

    def _send(self, payload):
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def _read(self):
        b1, b2 = self._recv_exact(2)
        opcode = b1 & 0x0F
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        masked = bool(b2 & 0x80)
        mask = self._recv_exact(4) if masked else None
        payload = self._recv_exact(length)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 9:
            return self._read()
        if opcode == 8:
            raise ConnectionError("closed")
        return payload

    def call(self, method, params=None):
        self.next_id += 1
        msg = {"id": self.next_id, "method": method}
        if params:
            msg["params"] = params
        self._send(json.dumps(msg).encode())
        while True:
            data = json.loads(self._read())
            if data.get("id") == self.next_id:
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data.get("result", {})

    def eval(self, expression):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True, "userGesture": True,
        })
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    def screenshot(self, path):
        data = self.call("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as f:
            f.write(base64.b64decode(data["data"]))


def click(c, selector):
    return c.eval(f"(() => {{const e=document.querySelector({json.dumps(selector)}); if(!e) return 'missing'; if(e.disabled) return 'disabled'; e.click(); return 'ok';}})()")


def status(c):
    return c.eval("""({
      title: document.title,
      crumb: document.querySelector('#move-crumb')?.textContent,
      phase: document.querySelector('#phase-crumb')?.textContent,
      engine: document.querySelector('#engine-label')?.textContent,
      modal: document.querySelector('#modal-root')?.innerText?.slice(0,120),
      moveCount: document.querySelector('#stat-moves')?.textContent,
      review: document.querySelector('#review-body')?.classList.contains('hidden') === false
    })""")


def main():
    c = CDP()
    print("initial", status(c))
    # 在棋盘上落一手（E6，9 路）
    c.eval("""(() => {
      const b=document.querySelector('#board'); const r=b.getBoundingClientRect();
      const w=r.width, pad=Math.max(20,w*0.062), cell=(w-pad*2)/8;
      const x=r.left+pad+4*cell, y=r.top+pad+3*cell;
      b.dispatchEvent(new MouseEvent('click',{clientX:x,clientY:y,bubbles:true,cancelable:true,view:window}));
      return 'clicked';
    })()""")
    time.sleep(4)
    print("after move", status(c))
    # 打开帮助 -> 诊断
    click(c, "#help-btn")
    time.sleep(0.3)
    print("diagnostics click", click(c, "[data-diagnostics]"))
    time.sleep(2)
    print("diagnostic rows", c.eval("document.querySelectorAll('.diag-row').length"),
          "status", c.eval("document.querySelector('#diag-list')?.innerText?.slice(0,100)"))
    c.screenshot("data/qa_diagnostics.png")
    click(c, "[data-ok]")
    # 打开棋谱
    click(c, "#records-btn")
    time.sleep(1.2)
    print("records", c.eval("({active:document.querySelectorAll('#active-records .record-row').length, games:document.querySelectorAll('#records-list .record-row').length, stats:document.querySelector('#records-stats')?.innerText})"))
    c.screenshot("data/qa_records.png")
    click(c, "[data-close]")
    print("done")


if __name__ == "__main__":
    main()
