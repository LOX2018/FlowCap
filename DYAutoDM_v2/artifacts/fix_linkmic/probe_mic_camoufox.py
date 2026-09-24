# -*- coding: utf-8 -*-
"""连麦麦克风对照实验：Camoufox 下 getUserMedia({audio}) 能否拿到音轨。

用法：
    py314 probe_mic_camoufox.py            # 现状（不注入任何 media prefs）= 负控
    py314 probe_mic_camoufox.py --fix      # 注入 Firefox 原生 media prefs = 正控

判据（可机械判定）：
    PASS = getUserMedia 返回至少 1 条 audio track（audioTrackCount >= 1）
    FAIL = 抛错 / 无音轨（NotAllowedError / NotFoundError / 空 tracks）

背景：Chromium 路径靠 `--use-fake-device-for-media-stream` 等 flag 提供虚拟麦克风；
Camoufox（Firefox 内核）**不受这些 flag 影响**，须用 Firefox 原生 pref。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile


# Firefox 原生 media prefs（等价于 Chromium 那组假媒体 flag 的语义）
FIX_PREFS = {
    # ① 虚拟采集源：无真实麦克风也能产出音频轨（对应 --use-fake-device-for-media-stream）
    "media.navigator.streams.fake": True,
    # ② 自动允许麦克风权限（对应 --use-fake-ui-for-media-stream）
    "permissions.default.microphone": 1,
    # ③ 不再弹系统权限框（对应 --deny-permission-prompts 的反面：直接放行）
    "media.navigator.permission.disabled": True,
    # ④ 明确保持 WebRTC 开启（连麦是 WebRTC 语音；camoufox 的 block_webrtc 会关它）
    "media.peerconnection.enabled": True,
}

PROBE_JS = """
async () => {
  const out = { hasApi: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) };
  if (!out.hasApi) return out;
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    out.ok = true;
    out.audioTrackCount = s.getAudioTracks().length;
    out.trackLabel = (s.getAudioTracks()[0] || {}).label || "";
    s.getTracks().forEach(t => t.stop());
  } catch (e) {
    out.ok = false;
    out.errName = e && e.name;
    out.errMsg = String((e && e.message) || e).slice(0, 200);
  }
  return out;
}
"""


async def run(use_fix: bool) -> int:
    from camoufox.async_api import AsyncCamoufox

    tmp = tempfile.mkdtemp(prefix="probe_mic_")
    # 与生产 usage 一致：persistent_context=True（生产走 launch_camoufox_async）
    kw = dict(persistent_context=True, user_data_dir=tmp,
              headless=True, i_know_what_im_doing=True)
    if use_fix:
        kw["firefox_user_prefs"] = dict(FIX_PREFS)
    print(f"=== 模式：{'正控（注入 Firefox media prefs）' if use_fix else '负控（现状：无 media prefs）'} ===")
    print("    prefs:", kw.get("firefox_user_prefs", "(未注入)"), flush=True)

    result = None
    async with AsyncCamoufox(**kw) as context:
        print("    浏览器已启动，取 page…", flush=True)
        page = context.pages[0] if getattr(context, "pages", None) else await context.new_page()
        print("    goto https://example.com …", flush=True)
        await page.goto("https://example.com", wait_until="domcontentloaded", timeout=45000)
        print("    执行 getUserMedia 探针…", flush=True)
        result = await page.evaluate(PROBE_JS)
        await page.close()

    print("    探针读数:", result, flush=True)
    ok = bool(result and result.get("ok") and (result.get("audioTrackCount") or 0) >= 1)
    verdict = "PASS" if ok else "FAIL"
    print(f"    判定: {verdict}  (audioTrackCount={result.get('audioTrackCount') if result else None}"
          f" err={result.get('errName') if result else None})", flush=True)
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="注入 Firefox 原生 media prefs")
    a = ap.parse_args()
    return asyncio.run(run(a.fix))


if __name__ == "__main__":
    raise SystemExit(main())
