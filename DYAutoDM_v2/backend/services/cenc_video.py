# -*- coding: utf-8 -*-
"""抖音 IM 视频解密（MPEG-CENC / AES-128-CTR）+ 下载（2026-09-17 新增）。

对照上游 `extractor/cenc.py`（douyin-chat-export v2.0.0）。

## 设计意图

抖音私信里的**视频**用 MPEG-CENC（ISO/IEC 23001-7）加密：
  · AES-128 密钥：`cj.video.skey`（32 位 hex = 16 字节）；
  · 每样本 8 字节 IV 存在 `senc` box（右侧补零到 16 字节，**低位半区是计数器**）；
  · **子样本加密**：样本内「明文 NAL 头 / 密文 NAL 负载」交替；
  · CTR 计数器**只随密文段推进**（跳过明文段不计数）。

加密文件本身是合法 MP4（ftyp/free/mdat/moov），moov 里含 senc/saiz/stsz/stsc/stco。
把 mdat 里的样本原地解密后，就是一个普通可播放 MP4。

## 与上游的差异（有意）

| 点 | 上游 | 本模块 | 理由 |
|---|---|---|---|
| 分块偏移 | 只支持 `stco`(32 位) | **`stco` + `co64`(64 位)** | 大文件会用 co64，只认 stco 会静默解不出 |
| IV 尺寸 | 硬编码 8 | **优先读 `saiz`**，回退 8 | saiz 是规范里 IV 尺寸的权威来源 |
| box 尺寸 | 处理 32/64 位 | 同样处理，另加**边界校验** | 防畸形 box 导致越界/死循环 |
| 解密后校验 | 无 | **返回每轨解密样本数** | 便于上层判定「真的解开了」 |

## 契约

· **纯内存**：不写临时文件（`decrypt_cenc_mp4` 输入/输出都是 bytes）；
· 只解密、不校验播放性（是否可播由上层用 ffmpeg 判定）；
· `skey` 必须是 32 位 hex，否则 `ValueError`（不静默返回原数据）；
· **不携带任何 cookie 下载**：CDN 直链自带签名（照 `origin_image_resolver` 既定范式）。
"""
from __future__ import annotations

import struct
from typing import Any

from loguru import logger

# 容器 box（需要递归下钻的类型）
_CONTAINERS = ("moov", "trak", "mdia", "minf", "stbl", "edts", "udta",
               "meta", "mvex", "moof", "traf", "dinf")

# box 解析安全上限（防畸形文件导致内存/时间爆炸）
_MAX_BOXES = 200000
_MAX_NEST = 32


def parse_boxes(buf: bytes, start: int, end: int, _depth: int = 0
                ) -> list[tuple[str, int, int, int, bytes]]:
    """解析 `[start,end)` 内的 box 序列 → [(type, pos, header_len, size, content)]。

    支持 32 位尺寸、`size==1` 的 64 位尺寸、`size==0`（延伸到末尾）。
    含边界校验：尺寸非法/越界即停止，不会死循环或越界读取。
    """
    out: list[tuple[str, int, int, int, bytes]] = []
    if _depth > _MAX_NEST:
        return out
    pos = start
    end = min(int(end), len(buf))
    while pos < end - 8 and len(out) < _MAX_BOXES:
        try:
            bs = struct.unpack(">I", buf[pos:pos + 4])[0]
            bt = buf[pos + 4:pos + 8].decode("ascii", errors="replace")
        except struct.error:
            break
        hl = 8
        if bs == 1:
            if pos + 16 > end:
                break
            bs = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
            hl = 16
        elif bs == 0:
            bs = end - pos
        # size < header 长度 → 畸形，停止（避免 pos 不前进造成死循环）
        if bs < hl or pos + bs > end:
            break
        out.append((bt, pos, hl, bs, buf[pos + hl:pos + bs]))
        pos += bs
    return out


def find_box(buf: bytes, start: int, end: int, path: list[str],
             ) -> tuple[str, int, int, int, bytes] | None:
    """沿 box 路径下钻（如 ['mdia','minf','stbl','senc']）→ box 元组或 None。"""
    for box in parse_boxes(buf, start, end):
        if box[0] == path[0]:
            if len(path) == 1:
                return box
            return find_box(buf, box[1] + box[2], box[1] + box[3], path[1:])
    return None


def _parse_full(content: bytes) -> tuple[int, int]:
    """FullBox → (version, flags)。"""
    ver = content[0] if content else 0
    flags = struct.unpack(">I", b"\x00" + content[1:4])[0] if len(content) >= 4 else 0
    return ver, flags


def _parse_stsz(content: bytes) -> list[int]:
    """样本尺寸表；`sample_size != 0` 时所有样本同尺寸。"""
    if len(content) < 12:
        return []
    default_size, sample_count = struct.unpack(">II", content[4:12])
    if default_size != 0:
        return [default_size] * sample_count
    need = 12 + sample_count * 4
    if len(content) < need:
        return []
    return list(struct.unpack(f">{sample_count}I", content[12:need]))


def _parse_saiz(content: bytes) -> int:
    """`saiz` → 每样本辅助信息(IV)尺寸；无法判定返回 0。"""
    if len(content) < 9:
        return 0
    flags = struct.unpack(">I", b"\x00" + content[1:4])[0]
    default_size = content[4]
    if default_size:
        return int(default_size)
    if flags & 1:      # sample_count 存在
        return 0       # 逐样本尺寸表：本模块不需要，交给 senc 推断
    return 0


def _parse_senc(content: bytes, iv_size: int = 8) -> list[dict]:
    """`senc` → [{iv, subs:[(clear,prot), ...]}]；subs 为空表示整样本加密。"""
    if len(content) < 8:
        return []
    _, flags = _parse_full(content)
    sample_count = struct.unpack(">I", content[4:8])[0]
    has_sub = bool(flags & 0x000002)
    out: list[dict] = []
    p = 8
    for _ in range(sample_count):
        if p + iv_size > len(content):
            break
        iv = content[p:p + iv_size]
        p += iv_size
        subs: list[tuple[int, int]] = []
        if has_sub:
            if p + 2 > len(content):
                break
            sub_count = struct.unpack(">H", content[p:p + 2])[0]
            p += 2
            for _ in range(sub_count):
                if p + 6 > len(content):
                    break
                clear, prot = struct.unpack(">HI", content[p:p + 6])
                subs.append((clear, prot))
                p += 6
        out.append({"iv": iv, "subs": subs})
    return out


def _parse_stco(content: bytes) -> list[int]:
    """`stco`(32 位) / `co64`(64 位) 的 chunk 偏移表。"""
    if len(content) < 8:
        return []
    entry_count = struct.unpack(">I", content[4:8])[0]
    avail = (len(content) - 8) // 4
    n = min(entry_count, avail)
    return list(struct.unpack(f">{n}I", content[8:8 + n * 4]))


def _parse_co64(content: bytes) -> list[int]:
    if len(content) < 8:
        return []
    entry_count = struct.unpack(">I", content[4:8])[0]
    avail = (len(content) - 8) // 8
    n = min(entry_count, avail)
    return list(struct.unpack(f">{n}Q", content[8:8 + n * 8]))


def _parse_stsc(content: bytes) -> list[tuple[int, int, int]]:
    """`stsc` → [(first_chunk, samples_per_chunk, sample_desc_idx)]。"""
    if len(content) < 8:
        return []
    entry_count = struct.unpack(">I", content[4:8])[0]
    avail = (len(content) - 8) // 12
    n = min(entry_count, avail)
    return [struct.unpack(">III", content[8 + i * 12:20 + i * 12])
            for i in range(n)]


def _samples_per_chunk(stsc: list[tuple[int, int, int]], chunk_idx: int) -> int:
    """给定 chunk 序号(1 起)，取适用的 samples_per_chunk（取最后一个 fc<=idx）。"""
    spc = stsc[0][1] if stsc else 0
    for fc, s, _sdi in stsc:
        if fc <= chunk_idx:
            spc = s
        else:
            break
    return spc


def _sample_offsets(stsc: list[tuple[int, int, int]], stco: list[int],
                    sizes: list[int]) -> list[tuple[int, int]]:
    """展开 → [(偏移, 尺寸)]，与 senc 逐样本对齐。"""
    out: list[tuple[int, int]] = []
    idx = 0
    for chunk_idx, chunk_off in enumerate(stco, start=1):
        spc = _samples_per_chunk(stsc, chunk_idx)
        off = chunk_off
        for _ in range(spc):
            if idx >= len(sizes):
                return out
            sz = sizes[idx]
            out.append((off, sz))
            off += sz
            idx += 1
    return out


def _aes_ctr(key: bytes, iv16: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    c = Cipher(algorithms.AES(key), modes.CTR(iv16))
    return c.decryptor().update(data)


def _decrypt_sample(key: bytes, sample: bytes, info: dict) -> bytes:
    """CENC 单样本解密：明文/密文段交替，CTR 计数器**只随密文推进**。"""
    iv_full = info["iv"].ljust(16, b"\x00")
    subs = info["subs"]
    if not subs:
        return _aes_ctr(key, iv_full, sample)

    layout: list[tuple[str, int]] = []
    for clear, prot in subs:
        layout.append(("c", clear))
        layout.append(("p", prot))
    # ① 收集密文段 → 拼成一条连续 CTR 流（计数器口径正确）
    pos = 0
    cipher_parts: list[bytes] = []
    for kind, length in layout:
        if kind == "p":
            cipher_parts.append(sample[pos:pos + length])
        pos += length
    plain_stream = _aes_ctr(key, iv_full, b"".join(cipher_parts))
    # ② 与明文段按原布局交错还原
    out = bytearray()
    pos = 0
    dpos = 0
    for kind, length in layout:
        if kind == "c":
            out.extend(sample[pos:pos + length])
        else:
            out.extend(plain_stream[dpos:dpos + length])
            dpos += length
        pos += length
    return bytes(out)


def _iv_size_for(senc_content: bytes, saiz_content: bytes | None) -> int:
    """IV 尺寸：优先 saiz，回退 8（上游硬编码值），最后 16。"""
    if saiz_content:
        z = _parse_saiz(saiz_content)
        if z in (8, 16):
            return z
    # saiz 缺失/不可用时按上游默认 8
    return 8


def _decrypt_track(buf: bytearray, t_start: int, t_end: int, key: bytes,
                   iv_size: int) -> tuple[int, str]:
    senc = find_box(buf, t_start, t_end, ["mdia", "minf", "stbl", "senc"])
    stsz = find_box(buf, t_start, t_end, ["mdia", "minf", "stbl", "stsz"])
    stsc = find_box(buf, t_start, t_end, ["mdia", "minf", "stbl", "stsc"])
    stco = find_box(buf, t_start, t_end, ["mdia", "minf", "stbl", "stco"])
    co64 = find_box(buf, t_start, t_end, ["mdia", "minf", "stbl", "co64"])
    # ⚠️ 顺序很重要：**先**判 senc 是否缺失。
    #    未加密轨根本没有 senc，此时 stsz/stsc/stco 都在 —— 若先判后者，
    #    守卫条件不成立会放行到 `senc[4]` → `TypeError: NoneType is not subscriptable`
    #    （实测由 test_unencrypted_mp4_reports_no_samples 抓出）。
    if senc is None:
        return 0, "unencrypted"
    if not (stsz and stsc and (stco or co64)):
        return 0, (f"missing box: stsz={bool(stsz)} stsc={bool(stsc)} "
                   f"stco/co64={bool(stco or co64)}")

    entries = _parse_senc(senc[4], iv_size=iv_size)
    sizes = _parse_stsz(stsz[4])
    stsc_e = _parse_stsc(stsc[4])
    offs = _parse_co64(co64[4]) if co64 else _parse_stco(stco[4])
    samples = _sample_offsets(stsc_e, offs, sizes)
    if len(samples) != len(entries):
        return 0, (f"sample count mismatch: stsz={len(samples)} "
                   f"senc={len(entries)}")

    done = 0
    for i, (off, size) in enumerate(samples):
        if off + size > len(buf):
            return done, f"sample {i} 越界 (off={off} size={size})"
        if size <= 0:
            continue
        buf[off:off + size] = _decrypt_sample(key, bytes(buf[off:off + size]),
                                              entries[i])
        done += 1
    return done, "ok"


def decrypt_cenc_mp4(encrypted: bytes, key_hex: str) -> bytes:
    """解密抖音 IM 的 CENC-MP4（纯内存）。失败抛 `ValueError`。

    前置条件：`key_hex` 为 32 位 hex（16 字节 AES-128 密钥）。
    后置条件：返回等长 bytes；**加密样本已原地解密**（未加密轨原样保留）。
    """
    if not key_hex or not isinstance(key_hex, str):
        raise ValueError("skey 缺失")
    try:
        key = bytes.fromhex(key_hex.strip())
    except ValueError:
        raise ValueError("skey 不是合法 hex")
    if len(key) != 16:
        raise ValueError(f"skey 必须为 16 字节 AES-128（当前 {len(key)} 字节）")
    if not encrypted or len(encrypted) < 16:
        raise ValueError("输入不是有效 MP4（过短）")

    buf = bytearray(encrypted)
    moov = next((b for b in parse_boxes(buf, 0, len(buf)) if b[0] == "moov"), None)
    if not moov:
        raise ValueError("未找到 moov box（非 MP4 或已损坏）")
    traks = [b for b in parse_boxes(buf, moov[1] + moov[2], moov[1] + moov[3])
             if b[0] == "trak"]
    if not traks:
        raise ValueError("moov 内无 trak box")

    total = 0
    for trak in traks:
        ts, te = trak[1] + trak[2], trak[1] + trak[3]
        saiz = find_box(buf, ts, te, ["mdia", "minf", "stbl", "saiz"])
        senc = find_box(buf, ts, te, ["mdia", "minf", "stbl", "senc"])
        iv_size = _iv_size_for(senc[4] if senc else b"", saiz[4] if saiz else None)
        n, why = _decrypt_track(buf, ts, te, key, iv_size)
        if n == 0 and why not in ("unencrypted",):
            logger.warning(f"[CENC-001] " + f"某轨解密未完成: {why}")
        total += n
    if total == 0:
        raise ValueError("未解密任何样本（可能未加密，或 senc/stsz 不匹配）")
    logger.info(f"[CENC-002] " + f"CENC 解密完成: {total} 个样本, {len(encrypted)}B")
    return bytes(buf)


def probe_mp4(data: bytes) -> dict[str, Any]:
    """体检：判断是否 MP4、是否加密、几轨、多少样本（用于**验证解密的真实性**）。"""
    info: dict[str, Any] = {"is_mp4": False, "boxes": [], "tracks": 0,
                           "encrypted_samples": 0, "has_moov": False,
                           "has_mdat": False, "size": len(data)}
    top = parse_boxes(data, 0, len(data))
    info["boxes"] = [b[0] for b in top]
    info["has_moov"] = "moov" in info["boxes"]
    info["has_mdat"] = "mdat" in info["boxes"]
    info["is_mp4"] = "ftyp" in info["boxes"] or info["has_moov"]
    moov = next((b for b in top if b[0] == "moov"), None)
    if not moov:
        return info
    traks = [b for b in parse_boxes(data, moov[1] + moov[2],
                                    moov[1] + moov[3]) if b[0] == "trak"]
    info["tracks"] = len(traks)
    for trak in traks:
        ts, te = trak[1] + trak[2], trak[1] + trak[3]
        senc = find_box(data, ts, te, ["mdia", "minf", "stbl", "senc"])
        if senc:
            info["encrypted_samples"] += len(_parse_senc(senc[4]))
    return info


def extract_video_fields(obj: Any) -> dict[str, Any]:
    """从消息对象里挖视频解密要素 → {skey, url, duration, vid}（挖不到给空值）。

    与图片共用同一套 `resource_url` 结构（对方实测：图片是 64hex skey +
    `origin_url_list`）。视频按同样形态取值，**不做任何主动请求**。
    """
    out: dict[str, Any] = {"skey": None, "url": None, "duration": None, "vid": None}
    if not isinstance(obj, dict):
        return out
    cands: list[dict] = []
    for key in ("video", "video_info", "videoInfo"):
        v = obj.get(key)
        if isinstance(v, dict):
            cands.append(v)
    res = obj.get("resource_url")
    if isinstance(res, dict):
        cands.append(res)
        v = res.get("video")
        if isinstance(v, dict):
            cands.append(v)
    for c in cands:
        sk = c.get("skey") or c.get("secret_key")
        if isinstance(sk, str) and sk:
            out["skey"] = out["skey"] or sk.strip()
        for k in ("play_url", "url", "origin_url", "url_list", "play_url_list",
                  "origin_url_list", "video_url"):
            val = c.get(k)
            if isinstance(val, str) and val.startswith("http"):
                out["url"] = out["url"] or val
            elif isinstance(val, list):
                for it in val:
                    if isinstance(it, str) and it.startswith("http"):
                        out["url"] = out["url"] or it
                        break
        for k in ("duration", "video_duration", "duration_ms"):
            if out["duration"] is None and isinstance(c.get(k), (int, float)):
                out["duration"] = c[k]
        for k in ("vid", "video_id", "item_id"):
            if out["vid"] is None and c.get(k):
                out["vid"] = str(c[k])
    return out
