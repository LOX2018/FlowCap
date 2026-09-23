# -*- coding: utf-8 -*-
"""P4 单测：CENC 视频解密 / 视频缓存服务 / 群聊 conv_type。

验证策略（**不伪造密文**）：
  用 ffmpeg **真实生成**一个 H.264 MP4，再用测试内自写的封装器构造
  CENC 加密 MP4（senc/saiz/stsz/stsc/stco + AES-128-CTR），
  然后断言 `decrypt_cenc_mp4` 能把它**逐字节还原**成原始明文。
这样验证的是真算法（真 AES-CTR、真 box 布局、真样本偏移），不是「跑通不报错」。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.cenc_video as CV        # noqa: E402
import services.im_video as IV          # noqa: E402
import services.chatlab_export as CE    # noqa: E402

_ACCT = "acct1"
_CONV = "0:1:100:200"


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _ffprobe() -> str | None:
    """2026-09-18 审查修复：`ffprobe` 与 `ffmpeg` 是两个可执行文件，
    只查 ffmpeg 会出现「守卫通过、调用 FileNotFoundError」的假失败。"""
    return shutil.which("ffprobe")


def _make_src_mp4(path: str, hole_bytes: int = 0) -> bool:
    """用 ffmpeg 生成真实 H.264 MP4；`hole_bytes>0` 时在 stbl 内**预留 free box**。

    为什么要预留：真实 CENC 文件的 senc/saiz 是**文件生成时就在**的，
    加密**不改变文件长度**。测试若「事后插入」box 会改变长度，与真实文件不符
    （实测：插入 senc+saiz 后 2859→2914 字节，导致长度一致性校验正确报错）。
    故先生成等大的 `free` box，加密时**原地覆盖**成 senc+saiz。
    """
    exe = _ffmpeg()
    if not exe:
        return False
    r = subprocess.run(
        [exe, "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10",
         "-t", "1", "-pix_fmt", "yuv420p", "-c:v", "libx264",
         "-movflags", "+faststart", path],
        capture_output=True)
    if not (r.returncode == 0 and os.path.exists(path)
            and os.path.getsize(path) > 500):
        return False
    if hole_bytes <= 0:
        return True
    buf = bytearray(open(path, "rb").read())
    moov = next(((bt, p, sz) for bt, p, sz in _iter_boxes(buf, 0, len(buf))
                 if bt == b"moov"), None)
    if not moov:
        return False
    trak = next(((bt, p, sz) for bt, p, sz in
                 _iter_boxes(buf, moov[1] + 8, moov[1] + moov[2]) if bt == b"trak"), None)
    mdia = next(((bt, p, sz) for bt, p, sz in
                 _iter_boxes(buf, trak[1] + 8, trak[1] + trak[2]) if bt == b"mdia"), None)
    minf = next(((bt, p, sz) for bt, p, sz in
                 _iter_boxes(buf, mdia[1] + 8, mdia[1] + mdia[2]) if bt == b"minf"), None)
    stbl = next(((bt, p, sz) for bt, p, sz in
                 _iter_boxes(buf, minf[1] + 8, minf[1] + minf[2]) if bt == b"stbl"), None)
    if not stbl:
        return False
    hole_end_before = stbl[1] + stbl[2]      # 插入点（此后所有字节后移）
    hole = _box("free", b"\x00" * hole_bytes)
    end = stbl[1] + stbl[2]
    buf[end:end] = hole
    for _bt, p, _sz in (stbl, minf, mdia, trak, moov):
        cur = struct.unpack(">I", buf[p:p + 4])[0]
        buf[p:p + 4] = struct.pack(">I", cur + len(hole))
    # ⚠️ 必须修正 stco/co64 的绝对偏移：hole 插在 moov 内（mdat 之前），
    #    其后所有字节整体后移 len(hole) —— 不修正的话 stco 会指到旧位置
    #    （实测读出来的「样本」全是 0，解密后自然对不上）。
    for name, width, code in ((b"stco", 4, ">I"), (b"co64", 8, ">Q")):
        box = next(((bt, p, sz) for bt, p, sz in
                    _iter_boxes(buf, stbl[1] + 8, stbl[1] + stbl[2] + len(hole))
                    if bt == name), None)
        if not box:
            continue
        n = struct.unpack(">I", buf[box[1] + 12:box[1] + 16])[0]
        base = box[1] + 16
        vals = [struct.unpack(code, buf[base + i * width:base + (i + 1) * width])[0]
                for i in range(n)]
        if vals and min(vals) > hole_end_before:
            for i, v in enumerate(vals):
                struct.pack_into(code, buf, base + i * width, v + len(hole))
    open(path, "wb").write(bytes(buf))
    return True


def _box(t: str, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + t.encode() + payload


def _full_box(t: str, version: int, flags: int, payload: bytes) -> bytes:
    return _box(t, bytes([version]) + flags.to_bytes(3, "big") + payload)


def _iter_boxes(buf: bytes, start: int, end: int):
    pos = start
    while pos < end - 8:
        bs = struct.unpack(">I", buf[pos:pos + 4])[0]
        bt = buf[pos + 4:pos + 8]
        if bs < 8 or pos + bs > end:
            break
        yield bt, pos, bs
        pos += bs


def _cenc_encrypt_mp4(plain: bytes, key_hex: str, iv_size: int = 8,
                      subsample: bool = True) -> bytes:
    """把普通 MP4 就地改造成 CENC 加密 MP4（测试用最小实现，与真实文件同构）。

    要点（都来自真实 CENC 的规范约束）：
      · **senc 必须覆盖全部样本**（entries 数 == stsz 样本数），否则真实文件不合法；
      · 每样本独立 IV；子样本模式下「明文头 / 密文负载 / 明文尾」交替，
        CTR 计数器只随密文推进；
      · senc/saiz 覆盖预先预留的 `free` box，**不改变文件长度**。
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = bytes.fromhex(key_hex)
    buf = bytearray(plain)

    def _iter2(s: int, e: int):
        return list(_iter_boxes(buf, s, e))

    def find(t: bytes, s: int, e: int):
        return next(((bt, p, sz) for bt, p, sz in _iter2(s, e) if bt == t), None)

    moov = find(b"moov", 0, len(buf))
    assert moov, "无 moov"
    trak = find(b"trak", moov[1] + 8, moov[1] + moov[2])
    assert trak, "无 trak"
    mdia = find(b"mdia", trak[1] + 8, trak[1] + trak[2])
    minf = find(b"minf", mdia[1] + 8, mdia[1] + mdia[2])
    stbl = find(b"stbl", minf[1] + 8, minf[1] + minf[2])
    stsz = find(b"stsz", stbl[1] + 8, stbl[1] + stbl[2])
    stsc = find(b"stsc", stbl[1] + 8, stbl[1] + stbl[2])
    stco = find(b"stco", stbl[1] + 8, stbl[1] + stbl[2])
    assert stsz and stsc and stco, "缺 stsz/stsc/stco"
    sizes = CV._parse_stsz(buf[stsz[1] + 8:stsz[1] + stsz[2]])
    stsc_e = CV._parse_stsc(buf[stsc[1] + 8:stsc[1] + stsc[2]])
    offs = CV._parse_stco(buf[stco[1] + 8:stco[1] + stco[2]])
    samples = CV._sample_offsets(stsc_e, offs, sizes)
    assert samples, "样本表为空"

    senc_payload = struct.pack(">I", len(samples))
    for i, (off, size) in enumerate(samples):
        iv = (i + 1).to_bytes(iv_size, "big")
        iv16 = iv.ljust(16, b"\x00")
        sample = bytes(buf[off:off + size])
        if subsample and size >= 32:
            clear1, clear2 = 8, 8
            prot = size - clear1 - clear2
            enc = Cipher(algorithms.AES(key), modes.CTR(iv16)).encryptor().update(
                sample[clear1:clear1 + prot])
            buf[off:off + size] = sample[:clear1] + enc + sample[clear1 + prot:]
            subs = [(clear1, prot), (clear2, 0)]
        else:
            enc = Cipher(algorithms.AES(key), modes.CTR(iv16)).encryptor().update(sample)
            buf[off:off + size] = enc
            subs = []
        senc_payload += iv
        if subsample:
            senc_payload += struct.pack(">H", len(subs))
            for cl, pr in subs:
                senc_payload += struct.pack(">HI", cl, pr)

    flags = 0x000002 if subsample else 0
    senc_box = _full_box("senc", 0, flags, senc_payload)
    saiz_box = _full_box("saiz", 0, 0, bytes([iv_size]) + struct.pack(">I", len(samples)))
    new_boxes = senc_box + saiz_box
    # 原地覆盖预留的 free box（**不改变文件长度**，与真实 CENC 文件一致）
    hole = next(((bt, p, sz) for bt, p, sz in _iter2(stbl[1] + 8, stbl[1] + stbl[2])
                 if bt == b"free" and sz >= len(new_boxes) + 8), None)
    if hole is None:
        raise AssertionError("测试夹具缺少足够大的 free 预留 box")
    start, free_end = hole[1], hole[1] + hole[2]
    buf[start:start + len(new_boxes)] = new_boxes
    tail = start + len(new_boxes)
    remain = free_end - tail
    if remain >= 8:
        # 剩余空间写成合法 free box（保持文件长度不变，且不干扰 box 遍历）
        buf[tail:tail + 8] = struct.pack(">I", remain) + b"free"
        buf[tail + 8:free_end] = b"\x00" * (remain - 8)
    elif remain > 0:
        buf[tail:free_end] = b"\x00" * remain
    return bytes(buf)


def _all_boxes(buf: bytes, s: int, e: int):
    return list(_iter_boxes(buf, s, e))


def _find_box(buf: bytes, t: bytes, s: int, e: int):
    return next(((bt, p, sz) for bt, p, sz in _all_boxes(buf, s, e) if bt == t), None)


def _sample_bytes(buf: bytes) -> list[bytes]:
    """取第一个 trak 的各样本字节（用于**逐样本**比对解密正确性）。

    为什么不比整个文件：加密后 `stbl` 里的预留 `free` box 会被 `senc`/`saiz`
    覆盖（真实 CENC 文件亦然），文件字节因此必然不同；而**样本（mdat 内的
    媒体数据）必须逐字节还原**才是解密正确的证明。
    """
    import services.cenc_video as CV
    moov = _find_box(buf, b"moov", 0, len(buf))
    trak = _find_box(buf, b"trak", moov[1] + 8, moov[1] + moov[2])
    mdia = _find_box(buf, b"mdia", trak[1] + 8, trak[1] + trak[2])
    minf = _find_box(buf, b"minf", mdia[1] + 8, mdia[1] + mdia[2])
    stbl = _find_box(buf, b"stbl", minf[1] + 8, minf[1] + minf[2])
    stsz = _find_box(buf, b"stsz", stbl[1] + 8, stbl[1] + stbl[2])
    stsc = _find_box(buf, b"stsc", stbl[1] + 8, stbl[1] + stbl[2])
    stco = _find_box(buf, b"stco", stbl[1] + 8, stbl[1] + stbl[2])
    stco64 = _find_box(buf, b"co64", stbl[1] + 8, stbl[1] + stbl[2])
    sizes = CV._parse_stsz(buf[stsz[1] + 8:stsz[1] + stsz[2]])
    offs = (CV._parse_co64(buf[stco64[1] + 8:stco64[1] + stco64[2]]) if stco64
            else CV._parse_stco(buf[stco[1] + 8:stco[1] + stco[2]]))
    stsc_e = CV._parse_stsc(buf[stsc[1] + 8:stsc[1] + stsc[2]])
    return [bytes(buf[o:o + sz]) for o, sz in CV._sample_offsets(stsc_e, offs, sizes)]


class TestCencDecrypt(unittest.TestCase):
    """真 AES-CTR、真 box、真样本偏移的端到端解密验证。"""

    _KEY = "00112233445566778899aabbccddeeff"

    def setUp(self):
        self.assertTrue(_ffmpeg(), "本用例需要 ffmpeg 生成真实 MP4")
        self._tmp = tempfile.mkdtemp(prefix="dy_cenc_")
        self.src = os.path.join(self._tmp, "src.mp4")
        if not _make_src_mp4(self.src, hole_bytes=512):
            self.skipTest("ffmpeg 生成源 MP4 失败")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _roundtrip(self, **kw):
        plain = open(self.src, "rb").read()
        enc = _cenc_encrypt_mp4(plain, self._KEY, **kw)
        self.assertEqual(len(enc), len(plain), "加密不得改变文件长度")
        before = _sample_bytes(enc)
        out = CV.decrypt_cenc_mp4(enc, self._KEY)
        return plain, enc, before, out

    def _assert_samples_restored(self, plain, before, out):
        """**逐样本**断言解密正确（这是「真解开了」的充分证据）。"""
        want = _sample_bytes(plain)
        got = _sample_bytes(out)
        self.assertEqual(len(before), len(want), "样本数应一致")
        self.assertNotEqual(before, want, "加密后样本应与明文不同（否则没加密）")
        self.assertEqual(len(got), len(want), "解密后样本数应一致")
        for i, (w, g) in enumerate(zip(want, got)):
            self.assertEqual(g, w, f"第 {i} 个样本未还原")

    def test_subsample_roundtrip_exact(self):
        """子样本加密 → 解密必须逐样本还原。"""
        plain, enc, before, out = self._roundtrip(subsample=True)
        self._assert_samples_restored(plain, before, out)

    def test_full_sample_roundtrip_exact(self):
        """整样本加密 → 解密必须逐样本还原。"""
        plain, enc, before, out = self._roundtrip(subsample=False)
        self._assert_samples_restored(plain, before, out)

    def test_iv_size_16_roundtrip(self):
        """IV 尺寸 16（saiz=16）也必须正确。"""
        plain, enc, before, out = self._roundtrip(iv_size=16)
        self._assert_samples_restored(plain, before, out)

    def test_wrong_key_does_not_return_plaintext(self):
        """错密钥必须解不出明文（防「其实没解密却报成功」）。"""
        plain = open(self.src, "rb").read()
        enc = _cenc_encrypt_mp4(plain, self._KEY)
        try:
            out = CV.decrypt_cenc_mp4(enc, "ff" * 16)
        except ValueError:
            return                     # 解析失败也可接受
        self.assertNotEqual(_sample_bytes(out), _sample_bytes(plain),
                            "错密钥竟然还原了样本")

    def test_decrypted_output_is_playable_mp4(self):
        """解密结果必须能被 ffmpeg 真正解码（不是「看着像 MP4」）。"""
        _p, enc, _b, out = self._roundtrip()
        dst = os.path.join(self._tmp, "out.mp4")
        open(dst, "wb").write(out)
        r = subprocess.run([_ffmpeg(), "-v", "error", "-i", dst, "-f", "null", "-"],
                           capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[:400])

    def test_bad_key_rejected(self):
        plain = open(self.src, "rb").read()
        for bad in ("", "zz", "00" * 8, "00" * 32):
            with self.assertRaises(ValueError, msg=f"key={bad!r} 应被拒"):
                CV.decrypt_cenc_mp4(plain, bad)

    def test_not_mp4_rejected(self):
        with self.assertRaises(ValueError):
            CV.decrypt_cenc_mp4(b"not an mp4 at all" * 10, self._KEY)

    def test_unencrypted_mp4_reports_no_samples(self):
        """未加密 MP4：不得谎称解密成功。"""
        plain = open(self.src, "rb").read()
        with self.assertRaises(ValueError):
            CV.decrypt_cenc_mp4(plain, self._KEY)

    def test_probe_detects_encryption(self):
        plain = open(self.src, "rb").read()
        enc = _cenc_encrypt_mp4(plain, self._KEY)
        p0, p1 = CV.probe_mp4(plain), CV.probe_mp4(enc)
        self.assertTrue(p0["is_mp4"] and p1["is_mp4"])
        self.assertEqual(p0["encrypted_samples"], 0)
        self.assertEqual(p1["encrypted_samples"], len(_sample_bytes(plain)),
                         "probe 应报告全部加密样本")
        self.assertEqual(p0["tracks"], p1["tracks"])

    def test_malformed_box_does_not_hang(self):
        """畸形 box 尺寸不得造成死循环/越界。"""
        junk = struct.pack(">I", 4) + b"moov" + struct.pack(">I", 0) + b"junk"
        out = CV.parse_boxes(junk, 0, len(junk))
        self.assertIsInstance(out, list)

    def test_extract_video_fields(self):
        obj = {"resource_url": {
            "skey": "abc123",
            "video": {"play_url": "https://v.douyin.com/x.mp4", "duration": 12000},
        }}
        f = CV.extract_video_fields(obj)
        self.assertEqual(f["skey"], "abc123")
        self.assertEqual(f["url"], "https://v.douyin.com/x.mp4")
        self.assertEqual(f["duration"], 12000)
        self.assertEqual(CV.extract_video_fields(None)["skey"], None)


class TestImVideoService(unittest.TestCase):
    """下载 → 解密 → 缓存 服务层（用本地 file:// 之外的可控 HTTP 服务）。"""

    def setUp(self):
        self.assertTrue(_ffmpeg(), "需要 ffmpeg")
        self._tmp = tempfile.mkdtemp(prefix="dy_vid_")
        self.root = self._tmp
        self.src = os.path.join(self._tmp, "src.mp4")
        if not _make_src_mp4(self.src, hole_bytes=512):
            self.skipTest("ffmpeg 生成失败")
        self.key = "00112233445566778899aabbccddeeff"
        plain = open(self.src, "rb").read()
        self.enc = _cenc_encrypt_mp4(plain, self.key)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_rejects_non_http_scheme(self):
        """SSRF 防护：file:// 必须被拒。"""
        r = IV.download_and_decrypt("file:///etc/passwd", self.key, app_root=self.root)
        self.assertFalse(r["ok"])
        self.assertIn("scheme", r["error"])

    def test_missing_skey_rejected(self):
        r = IV.download_and_decrypt("https://x/y.mp4", "", app_root=self.root)
        self.assertFalse(r["ok"])
        self.assertIn("skey", r["error"])

    def test_download_decrypt_and_cache(self):
        """起一个真实本地 HTTP 服务提供密文 → 下载→解密→缓存→再命中缓存。"""
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            payload = self.enc

            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(self.payload)))
                self.end_headers()
                self.wfile.write(self.payload)

            def log_message(self, *a):  # 静音
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{srv.server_port}/v.mp4"
            r1 = IV.download_and_decrypt(url, self.key, app_root=self.root)
            self.assertTrue(r1["ok"], r1)
            self.assertTrue(r1["decrypted"])
            self.assertFalse(r1["cached"])
            self.assertTrue(os.path.exists(r1["path"]))
            # 2026-09-17（E3）：缓存内容**不再是解密原样输出** —— 出盘前会做
            # faststart 重封装（moov 移到文件头），否则 HTML5 <video> 必须读完
            # 整个文件才能起播。故断言**语义等价**而非整体逐字节相等：
            #   ① 明文 MP4 且无残留加密样本；
            #   ② **载荷内容**（mdat 的 payload，即第 5 项）与解密输出一致
            #      —— 注意不能比整个元组：元组第 2 项是偏移，faststart 后必然变。
            cached = open(r1["path"], "rb").read()
            plain = CV.decrypt_cenc_mp4(self.enc, self.key)
            pc = CV.probe_mp4(cached)
            self.assertTrue(pc["is_mp4"])
            self.assertFalse(pc.get("encrypted_samples"))
            self.assertEqual(CV.find_box(cached, 0, len(cached), ["mdat"])[4],
                             CV.find_box(plain, 0, len(plain), ["mdat"])[4],
                             "mdat 载荷应与解密输出一致（解密正确性）")
            # 二次调用命中缓存（cached=True，且零网络）
            srv.shutdown()
            r2 = IV.download_and_decrypt(url, self.key, app_root=self.root)
            self.assertTrue(r2["ok"])
            self.assertTrue(r2["cached"])
        finally:
            try:
                srv.shutdown()
            except Exception:
                pass

    def test_sweep_and_stats(self):
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            payload = self.enc

            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Length", str(len(self.payload)))
                self.end_headers()
                self.wfile.write(self.payload)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_port}/v.mp4"
            IV.download_and_decrypt(url, self.key, app_root=self.root)
            s = IV.stats(self.root)
            self.assertGreaterEqual(s["files"], 1)
            sw = IV.sweep(self.root, force=True)
            self.assertGreaterEqual(sw["removed"], 1)
            self.assertEqual(IV.stats(self.root)["files"], 0)
        finally:
            srv.shutdown()

    def test_http_error_reported(self):
        r = IV.download_and_decrypt("http://127.0.0.1:1/none.mp4", self.key,
                                    app_root=self.root, timeout=2)
        self.assertFalse(r["ok"])


def _mkdb(path=":memory:"):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE dm_conversations(
            account TEXT, conv_id TEXT, peer_id TEXT, peer_name TEXT,
            short_id TEXT DEFAULT '', last_ts REAL DEFAULT 0, unread INTEGER DEFAULT 0,
            avatar TEXT DEFAULT '', conv_type INTEGER DEFAULT 1,
            UNIQUE(account, conv_id));
        CREATE TABLE dm_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT, conv_id TEXT, role TEXT, text TEXT,
            msg_type TEXT DEFAULT 'text', extra TEXT DEFAULT '{}',
            ts REAL NOT NULL, msg_id TEXT, UNIQUE(account, conv_id, msg_id));
    """)
    return c


class TestGroupChat(unittest.TestCase):
    """群聊支持：conv_id 纯数字 → 群聊（照上游判定口径）+ ChatLab 导出标记。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="dy_grp_")
        self.conn = _mkdb()
        self._orig = __import__("database").get_db
        sys.modules["database"].get_db = lambda: self.conn

    def tearDown(self):
        sys.modules["database"].get_db = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_digit_conv_id_is_group(self):
        """2026-09-18 审查修复（A15）：旧实现把判据内联重写一遍再与期望表比对
        （`got` 与 `want` 同源，恒真，永不触达生产代码）。现改为真调单一真相源。"""
        from services.conv_identity import conv_type
        for cid, want in (("738291000111", 2), ("0:1:1:2", 1),
                          ("12345", 2), ("abc", 1), ("", 1), ("  42  ", 2)):
            self.assertEqual(conv_type(cid), want, f"conv_id={cid!r}")

    def test_chatlab_marks_group(self):
        self.conn.execute(
            "INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,conv_type)"
            " VALUES(?,?,?,?,?)", (_ACCT, "738291000111", "9", "工作群", 2))
        self.conn.execute(
            "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (_ACCT, "738291000111", "them", "群消息", "text", "{}", 1700000000.0, "g1"))
        self.conn.commit()
        r = CE.export_chatlab(_ACCT, "738291000111", self._tmp, fmt="json")
        self.assertTrue(r["ok"])
        data = json.loads(open(r["path"], encoding="utf-8").read())
        self.assertEqual(data["meta"]["type"], "group")
        self.assertEqual(data["meta"]["groupId"], "738291000111")
        self.assertIn("群聊", data["meta"]["name"])

    def test_chatlab_marks_private(self):
        self.conn.execute(
            "INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,conv_type)"
            " VALUES(?,?,?,?,?)", (_ACCT, _CONV, "200", "张三", 1))
        self.conn.execute(
            "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (_ACCT, _CONV, "them", "私聊", "text", "{}", 1700000000.0, "p1"))
        self.conn.commit()
        r = CE.export_chatlab(_ACCT, _CONV, self._tmp, fmt="json")
        data = json.loads(open(r["path"], encoding="utf-8").read())
        self.assertEqual(data["meta"]["type"], "private")
        self.assertNotIn("groupId", data["meta"])

    def test_jsonl_header_matches_json(self):
        self.conn.execute(
            "INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,conv_type)"
            " VALUES(?,?,?,?,?)", (_ACCT, "738291000111", "9", "工作群", 2))
        self.conn.execute(
            "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (_ACCT, "738291000111", "me", "我发的", "text", "{}", 1700000001.0, "g2"))
        self.conn.commit()
        r = CE.export_chatlab(_ACCT, "738291000111", self._tmp, fmt="jsonl")
        with open(r["path"], encoding="utf-8") as f:
            head = json.loads(f.readline())
        self.assertEqual(head["_type"], "header")
        self.assertEqual(head["meta"]["type"], "group")


class TestVideoResolveChain(unittest.TestCase):
    """2026-09-17（E3）：tkey→签名地址 取址链 + 按消息取用 + faststart。

    锁定的契约（含**真跑**端到端，非模拟）：
      · `resolve_play_urls` 在页面上下文换签名地址（`batch_play_info`，≤10/批）；
      · `resolve_by_message` 只给 tkey+skey 就能拿到**可解码**的明文 MP4；
      · moov 在尾部的源文件经 `_faststart_mp4` 后 moov 前移且仍可解码。
    """

    def test_parse_play_infos_positional_and_error(self):
        from services.cenc_video import parse_play_infos
        payload = {"err_no": 0, "data": {"play_infos": [
            {"encrypted_url": {"main_url": "https://cdn/a.mp4"}},
            {"encrypted_url": {"backup_url": "https://cdn/b.mp4"}},
            {"encrypted_url": {}},
        ]}}
        got = parse_play_infos(payload, ["t1", "t2", "t3"])
        self.assertEqual(got, {"t1": "https://cdn/a.mp4", "t2": "https://cdn/b.mp4"})
        self.assertEqual(parse_play_infos({"err_no": 5, "data": {}}, ["t1"]), {})

    def test_resolve_play_urls_batch_size_and_isolation(self):
        """分批上限 10，且**单批失败不影响其余批**。"""
        from services.cenc_video import resolve_play_urls_batch, BATCH_SIZE
        calls = []

        def fake(js, arg):
            _path, keys = arg
            calls.append(list(keys))
            if keys and keys[0] == "FAIL":
                return {"status": 500, "body": "{}"}
            return {"status": 200, "body": json.dumps({"err_no": 0,
                "data": {"play_infos": [{"encrypted_url":
                    {"main_url": f"https://cdn/{k}.mp4"}} for k in keys]}})}

        keys = ["FAIL"] + [f"tk{i}" for i in range(1, 10)] + ["tk10", "tk11"]
        got = resolve_play_urls_batch(keys, fake)
        self.assertEqual([len(c) for c in calls], [BATCH_SIZE, 2])
        self.assertNotIn("FAIL", got)
        self.assertIn("tk10", got)
        self.assertIn("tk11", got)

    def test_resolve_by_message_end_to_end(self):
        """真跑：真 H.264 → CENC 加密 → HTTP → 换址 → 解密 → faststart → 可解码。"""
        import http.server
        import socketserver
        import tempfile
        import threading
        from services import im_video as IV

        if not _ffmpeg():
            self.skipTest("ffmpeg 不可用")
        key = "0102030405060708090a0b0c0d0e0f10"
        tmp = tempfile.mkdtemp(prefix="e3_test_")
        src = os.path.join(tmp, "src.mp4")
        self.assertTrue(_make_src_mp4(src, hole_bytes=512))
        plain = open(src, "rb").read()
        cipher = _cenc_encrypt_mp4(plain, key)

        enc = os.path.join(tmp, "enc.mp4")
        with open(enc, "wb") as f:
            f.write(cipher)

        class H(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def translate_path(self, path):
                return enc

        srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        cdn = f"http://127.0.0.1:{srv.server_address[1]}/v.mp4"

        hit = {}

        def fake_exec(js, arg):
            _path, tkeys = arg
            hit["tkeys"] = tkeys
            return {"status": 200, "body": json.dumps({"err_no": 0,
                "data": {"play_infos": [{"encrypted_url": {"main_url": cdn}}
                                        for _ in tkeys]}})}

        out = IV.resolve_by_message("", "", tkey="TK", skey=key,
                                    exec_js=fake_exec, app_root=tmp)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(hit["tkeys"], ["TK"])
        self.assertTrue(out.get("decrypted"))
        got = open(out["path"], "rb").read()
        self.assertEqual(CV.probe_mp4(got)["is_mp4"], True)
        if not _ffprobe():
            self.skipTest("ffprobe 不可用")
        pb = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "stream=codec_name,nb_frames", "-of", "csv=p=0",
                             out["path"]], capture_output=True, text=True)
        self.assertIn("h264", pb.stdout)

    def test_faststart_moves_moov_from_tail(self):
        """moov 在尾部的文件 → faststart 后 moov 前移，且仍可解码。"""
        import pathlib
        import tempfile
        from services import im_video as IV

        if not _ffmpeg():
            self.skipTest("ffmpeg 不可用")
        tmp = tempfile.mkdtemp(prefix="e3_fs_")
        p = os.path.join(tmp, "tail.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "testsrc=size=64x48:rate=10", "-t", "1",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", p],
                       capture_output=True)
        before = open(p, "rb").read()
        mb = CV.find_box(before, 0, len(before), ["moov"])
        db = CV.find_box(before, 0, len(before), ["mdat"])
        self.assertGreater(mb[1], db[1], "前置条件不成立：源文件 moov 未在尾部")

        IV._faststart_mp4(pathlib.Path(p))

        after = open(p, "rb").read()
        ma = CV.find_box(after, 0, len(after), ["moov"])
        da = CV.find_box(after, 0, len(after), ["mdat"])
        self.assertLess(ma[1], da[1], "faststart 未把 moov 移到 mdat 之前")
        pb = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "stream=codec_name", "-of", "csv=p=0", p],
                            capture_output=True, text=True)
        self.assertIn("h264", pb.stdout)

    def test_resolve_requires_something_to_work_with(self):
        """DbC 前置条件：无任何要素时必须明确失败，不得静默返回空。"""
        from services import im_video as IV
        out = IV.resolve_by_message("", "", tkey="", skey="", url="")
        self.assertFalse(out.get("ok"))
        self.assertIn("tkey", out.get("error", ""))




class TestConversationSeq(unittest.TestCase):
    """2026-09-17（E1/E2）：会话详情 `get_conversation` 下发消息 seq。

    契约：seq 连续从 1 起，与 `/render/png` 的 fetch_range **过滤+排序口径一致**，
    前端据此做**逐条/选区**渲染零错位。
    """
    def setUp(self):
        self.conn = _mkdb()
        self._db = sys.modules.setdefault("database", __import__("database"))
        self._orig = self._db.get_db
        self._db.get_db = lambda: self.conn
        # 2026-09-23（HC-10 / M-12）：**强制重导 api.messages**。
        # 机理：`api/messages.py` 在**模块导入期**执行 `from database import get_db`，
        # 把 get_db 固化进自己的模块命名空间。若它已被别的测试模块**先行导入**
        # （实测污染源：test_engine_contract_p1 / test_replay_gates 运行期 import 过），
        # 则本处「改 database.get_db」**传导不到它** ⇒ 它仍读真实库 ⇒ detail=[]。
        # 定式：打桩 database.get_db 之后，必须让「导入期固化 get_db」的消费者**重新绑定**。
        # （`services.chat_render` 是在函数内惰性 `from database import get_db`，无需处理。）
        self._saved_msgs = sys.modules.pop("api.messages", None)
        import api.messages  # noqa: F401  ← 重导后绑定到打桩版 get_db

    def tearDown(self):
        # 2026-09-18 审查修复（A16）：恢复 get_db 后**不要** del sys.modules["database"]。
        # unittest discover 下所有 test_*.py 共享一个进程，删模块会让已 import database 的
        # 模块（如 api.messages）持有陈旧绑定 → 后续用例串库/随执行顺序而变。
        self._db.get_db = self._orig
        # 2026-09-23（M-12）：把我们重导出来的 api.messages 换回**原对象**，避免把
        # 「已绑定打桩 get_db 的副本」泄漏给后续测试（那会引发反向的顺序相关失败）。
        sys.modules.pop("api.messages", None)
        if self._saved_msgs is not None:
            sys.modules["api.messages"] = self._saved_msgs

    def test_seq_continuous_from_1(self):
        from api.messages import get_conversation
        import asyncio
        self.conn.execute(
            "INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,conv_type)"
            " VALUES(?,?,?,?,?)", (_ACCT, _CONV, "200", "李四", 1))
        for i, row in enumerate([
            (_ACCT, _CONV, "me",    "你好吗",       "text",      "{}",           1700000001.0, "a"),
            (_ACCT, _CONV, "them",  "还好呢",       "text",      "{}",           1700000002.0, "b"),
            (_ACCT, _CONV, "me",    "[分享视频] c", "text",      "{}",           1700000003.0, "c"),
            # 被过滤的脏数据：不应出现在 seq
            (_ACCT, _CONV, "them",  "[分享视频]",   "text",      "{}",           1700000004.0, "d"),
            (_ACCT, _CONV, "them",  "",             "50001",     "{}",           1700000005.0, "e"),
            (_ACCT, _CONV, "them",  "[未知媒体]",  "image",     "{}",           1700000006.0, "f"),
        ], start=0):
            self.conn.execute(
                "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
                " VALUES(?,?,?,?,?,?,?,?)", row)
        self.conn.commit()
        res = asyncio.run(get_conversation(_ACCT, _CONV))
        msgs = (res.get("conversation") or {}).get("messages") if isinstance(res, dict) else []
        # 3 条有效（过滤掉 [分享视频]/50001/[未知媒体]）
        self.assertEqual(len(msgs), 3, f"过滤后条数不对: {msgs}")
        seqs = [m.get("seq") for m in msgs]
        self.assertEqual(seqs, [1, 2, 3], f"seq 序列异常: {seqs}")

    def test_seq_same_as_render_fetch_range(self):
        """E1 关键契约：详情端点 seq 与 `fetch_range`（渲染端点）seq **完全一致**。

        口径不一致 ⇒ 前端选区导出错条（把没选中的消息画进长图）。
        额外验证详情里多出的过滤条件（系统引导噪音）两处一致生效。
        """
        from api.messages import get_conversation
        from services import chat_render as CR
        import asyncio
        self.conn.execute(
            "INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,conv_type)"
            " VALUES(?,?,?,?,?)", (_ACCT, _CONV, "200", "李四", 1))
        rows = [
            (_ACCT, _CONV, "me",   "第一条",        "text",  "{}", 1700000001.0, "a"),
            # 系统引导噪音（详情端点过滤；fetch_range 2026-09-18 起同样过滤）
            (_ACCT, _CONV, "them", "对方回复你或互关之前可以发送一条消息", "text", "{}", 1700000002.0, "n1"),
            (_ACCT, _CONV, "them", "第二条",        "text",  "{}", 1700000003.0, "b"),
            (_ACCT, _CONV, "them", "",              "50001", "{}", 1700000004.0, "n2"),
            (_ACCT, _CONV, "me",   "https://www.iesdouyin.com/share/xxx", "text", "{}", 1700000005.0, "n3"),
            (_ACCT, _CONV, "them", "第三条",        "text",  "{}", 1700000006.0, "c"),
        ]
        for row in rows:
            self.conn.execute(
                "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
                " VALUES(?,?,?,?,?,?,?,?)", row)
        self.conn.commit()
        res = asyncio.run(get_conversation(_ACCT, _CONV))
        detail = (res.get("conversation") or {}).get("messages") or []
        render = CR.fetch_range(_ACCT, _CONV)["messages"]
        # 3 条有效（两条系统噪音 + 回执 + share 链接被滤）
        self.assertEqual([m.get("seq") for m in detail], [1, 2, 3])
        self.assertEqual([m["seq"] for m in render], [1, 2, 3])
        # 两边 msg_id 逐条相等 = 同一集合同一序
        self.assertEqual([m.get("msg_id") for m in detail],
                         [m["msg_id"] for m in render])


if __name__ == "__main__":
    unittest.main(verbosity=2)
