# coding=utf-8
"""匿名设备指纹生成器 —— 为每个匿名请求生成不同的设备标识。

## 为什么需要

风控系统会通过**设备指纹聚合**识别匿名用户：
- 如果所有匿名请求都用同一个 UA / ttwid / s_v_web_id，风控会识别为同一用户
- 即使请求来自不同 IP，指纹相同也会被关联

## 设计原则

1. **每次请求生成不同指纹**：ttwid、s_v_web_id、webid 都随机生成
2. **UA 轮换**：桌面端和移动端 UA 随机选择
3. **🔴 设备一致性（铁律）**：同一个请求内的所有标识必须来自**同一个设备模板**
   - UA 说是 iPhone → 屏幕尺寸必须是移动端（如 390x844）
   - UA 说是 Windows → 屏幕尺寸必须是桌面端（如 1920x1080）
   - 引擎名称必须与浏览器匹配（Chrome → Blink，Safari → WebKit）
   - **禁止"吕布骑狗"**：iPhone UA + Windows 分辨率 = 风控秒识别

## 使用方式

```python
from services.anon_fingerprint import AnonFingerprint

# 生成一组匿名指纹（自动保证设备一致性）
fp = AnonFingerprint.generate()

# 使用指纹发请求
headers = fp.to_headers()
params = fp.to_params()
# 屏幕尺寸等参数也来自同一个 fp 实例
screen_width = fp.screen_width
screen_height = fp.screen_height
```
"""
from __future__ import annotations

import random
import string
from dataclasses import dataclass
from typing import Optional


# ============================================================================
# 设备模板（每个模板内的参数必须一致）
# ============================================================================

@dataclass
class DeviceTemplate:
    """设备模板 —— 一组自洽的设备参数。

    🔴 铁律：模板内的所有参数必须来自同一个真实设备，禁止跨模板混用。
    """
    # UA
    ua: str
    # 屏幕尺寸
    screen_width: int
    screen_height: int
    # 浏览器信息
    browser_name: str
    browser_version: str
    # 引擎信息（必须与浏览器匹配）
    engine_name: str
    engine_version: str
    # 操作系统
    os_name: str
    os_version: str
    # 平台
    platform: str
    # CPU 核心数
    cpu_core_num: int
    # 设备内存（GB）
    device_memory: int


# 桌面端设备模板（Windows + macOS）
DESKTOP_TEMPLATES = [
    # Windows Chrome
    DeviceTemplate(
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        screen_width=1920, screen_height=1080,
        browser_name="Chrome", browser_version="120.0.0.0",
        engine_name="Blink", engine_version="120.0.0.0",
        os_name="Windows", os_version="10",
        platform="PC", cpu_core_num=8, device_memory=16,
    ),
    DeviceTemplate(
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        screen_width=1920, screen_height=1080,
        browser_name="Chrome", browser_version="119.0.0.0",
        engine_name="Blink", engine_version="119.0.0.0",
        os_name="Windows", os_version="10",
        platform="PC", cpu_core_num=8, device_memory=16,
    ),
    DeviceTemplate(
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        screen_width=2560, screen_height=1440,
        browser_name="Chrome", browser_version="118.0.0.0",
        engine_name="Blink", engine_version="118.0.0.0",
        os_name="Windows", os_version="10",
        platform="PC", cpu_core_num=16, device_memory=32,
    ),
    # Windows Edge
    DeviceTemplate(
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        screen_width=1920, screen_height=1080,
        browser_name="Edge", browser_version="120.0.0.0",
        engine_name="Blink", engine_version="120.0.0.0",
        os_name="Windows", os_version="10",
        platform="PC", cpu_core_num=8, device_memory=16,
    ),
    # macOS Safari
    DeviceTemplate(
        ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
        screen_width=2560, screen_height=1440,
        browser_name="Safari", browser_version="17.1",
        engine_name="WebKit", engine_version="605.1.15",
        os_name="Mac OS X", os_version="10.15.7",
        platform="PC", cpu_core_num=10, device_memory=16,
    ),
    DeviceTemplate(
        ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        screen_width=1920, screen_height=1080,
        browser_name="Safari", browser_version="17.0",
        engine_name="WebKit", engine_version="605.1.15",
        os_name="Mac OS X", os_version="10.15.7",
        platform="PC", cpu_core_num=8, device_memory=16,
    ),
]

# 移动端设备模板（iOS + Android）
MOBILE_TEMPLATES = [
    # iOS Safari
    DeviceTemplate(
        ua="Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
        screen_width=390, screen_height=844,
        browser_name="Safari", browser_version="17.1",
        engine_name="WebKit", engine_version="605.1.15",
        os_name="iPhone OS", os_version="17.1",
        platform="iPhone", cpu_core_num=6, device_memory=6,
    ),
    DeviceTemplate(
        ua="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        screen_width=393, screen_height=852,
        browser_name="Safari", browser_version="17.0",
        engine_name="WebKit", engine_version="605.1.15",
        os_name="iPhone OS", os_version="17.0",
        platform="iPhone", cpu_core_num=6, device_memory=6,
    ),
    DeviceTemplate(
        ua="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        screen_width=390, screen_height=844,
        browser_name="Safari", browser_version="16.6",
        engine_name="WebKit", engine_version="605.1.15",
        os_name="iPhone OS", os_version="16.6",
        platform="iPhone", cpu_core_num=6, device_memory=6,
    ),
    # Android Chrome
    DeviceTemplate(
        ua="Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        screen_width=360, screen_height=800,
        browser_name="Chrome", browser_version="120.0.0.0",
        engine_name="Blink", engine_version="120.0.0.0",
        os_name="Android", os_version="14",
        platform="Android", cpu_core_num=8, device_memory=12,
    ),
    DeviceTemplate(
        ua="Mozilla/5.0 (Linux; Android 13; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
        screen_width=360, screen_height=780,
        browser_name="Chrome", browser_version="119.0.0.0",
        engine_name="Blink", engine_version="119.0.0.0",
        os_name="Android", os_version="13",
        platform="Android", cpu_core_num=8, device_memory=12,
    ),
    DeviceTemplate(
        ua="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        screen_width=393, screen_height=851,
        browser_name="Chrome", browser_version="120.0.0.0",
        engine_name="Blink", engine_version="120.0.0.0",
        os_name="Android", os_version="14",
        platform="Android", cpu_core_num=8, device_memory=12,
    ),
]

# 所有设备模板
ALL_TEMPLATES = DESKTOP_TEMPLATES + MOBILE_TEMPLATES


@dataclass
class AnonFingerprint:
    """一组匿名设备指纹（单次请求内一致）。

    🔴 铁律：所有参数必须来自同一个 DeviceTemplate，禁止跨模板混用。
    """

    # 设备模板（决定 UA、屏幕尺寸、浏览器、引擎、操作系统等）
    template: DeviceTemplate

    # 随机生成的设备标识（每次请求都不同）
    ttwid: str
    s_v_web_id: str
    webid: str
    msToken: str

    @classmethod
    def generate(cls, mobile: Optional[bool] = None) -> "AnonFingerprint":
        """生成一组新的匿名指纹。

        Args:
            mobile: 是否使用移动端模板（None = 随机选择桌面/移动）

        Returns:
            AnonFingerprint 实例（所有参数来自同一个设备模板）
        """
        # 选择设备模板
        if mobile is True:
            template = random.choice(MOBILE_TEMPLATES)
        elif mobile is False:
            template = random.choice(DESKTOP_TEMPLATES)
        else:
            template = random.choice(ALL_TEMPLATES)

        # 生成随机设备标识
        ttwid = cls._gen_ttwid()
        s_v_web_id = cls._gen_s_v_web_id()
        webid = cls._gen_webid()
        msToken = cls._gen_msToken()

        return cls(
            template=template,
            ttwid=ttwid,
            s_v_web_id=s_v_web_id,
            webid=webid,
            msToken=msToken,
        )

    # ====================================================================
    # 便捷属性（从模板读取，保证设备一致性）
    # ====================================================================

    @property
    def ua(self) -> str:
        return self.template.ua

    @property
    def screen_width(self) -> int:
        return self.template.screen_width

    @property
    def screen_height(self) -> int:
        return self.template.screen_height

    @property
    def browser_name(self) -> str:
        return self.template.browser_name

    @property
    def browser_version(self) -> str:
        return self.template.browser_version

    @property
    def engine_name(self) -> str:
        return self.template.engine_name

    @property
    def engine_version(self) -> str:
        return self.template.engine_version

    @property
    def os_name(self) -> str:
        return self.template.os_name

    @property
    def os_version(self) -> str:
        return self.template.os_version

    @property
    def platform(self) -> str:
        return self.template.platform

    @property
    def cpu_core_num(self) -> int:
        return self.template.cpu_core_num

    @property
    def device_memory(self) -> int:
        return self.template.device_memory

    # ====================================================================
    # 转换方法
    # ====================================================================

    def to_headers(self, referer: str = "https://www.douyin.com/") -> dict:
        """转换为请求头字典。"""
        return {
            "user-agent": self.ua,
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "referer": referer,
        }

    def to_params(self) -> dict:
        """转换为参数字典（包含所有设备参数）。

        🔴 所有参数都来自同一个模板，保证设备一致性。
        """
        return {
            "webid": self.webid,
            "verifyFp": self.s_v_web_id,
            "fp": self.s_v_web_id,
            "msToken": self.msToken,
            # 设备参数（从模板读取）
            "device_platform": "webapp" if self.platform == "PC" else "android",
            "aid": "6383",
            "channel": "channel_pc_web" if self.platform == "PC" else "channel_standard",
            "pc_client_type": "1" if self.platform == "PC" else "0",
            "version_code": "170400",
            "version_name": "17.4.0",
            "cookie_enabled": "true",
            "screen_width": str(self.screen_width),
            "screen_height": str(self.screen_height),
            "browser_language": "zh-CN",
            "browser_platform": "Win32" if self.os_name == "Windows" else "MacIntel" if self.os_name == "Mac OS X" else "Linux armv8l",
            "browser_name": self.browser_name,
            "browser_version": self.browser_version,
            "browser_online": "true",
            "engine_name": self.engine_name,
            "engine_version": self.engine_version,
            "os_name": self.os_name,
            "os_version": self.os_version,
            "cpu_core_num": str(self.cpu_core_num),
            "device_memory": str(self.device_memory),
            "platform": self.platform,
        }

    # ====================================================================
    # 随机生成器
    # ====================================================================

    @staticmethod
    def _gen_ttwid() -> str:
        """生成 ttwid（设备级标识）。

        格式（来自上游真实 cookie 情报）：
            ttwid=1%7C<random>%7C<timestamp>%7C<hash>
        其中 %7C 是 "|" 的 URL 编码。

        示例：
            1%7CLOO5jA3xKFP2HUC4tFAnPpFGRifnKCdQ8kuwwY24h9Y%7C1695982617%7C032f9efe9aef7c1a3ec2fd13f460a3565f556fd68c6b227985c65747e3111a28
        """
        # 第一段：随机字符串（约 40 字符）
        chars = string.ascii_letters + string.digits + "-_"
        part1 = "".join(random.choices(chars, k=40))
        # 第二段：时间戳（10 位数字）
        import time
        part2 = str(int(time.time() * 1000))
        # 第三段：hash（约 64 字符）
        part3 = "".join(random.choices(string.ascii_letters + string.digits + "-_", k=64))
        # 用 %7C（URL 编码的 |）连接
        return f"1%7C{part1}%7C{part2}%7C{part3}"

    @staticmethod
    def _gen_s_v_web_id() -> str:
        """生成 s_v_web_id（设备指纹）。

        格式（来自上游真实 cookie 情报）：
            s_v_web_id=verify_<random>_<random>_<random>_<random>_<random>

        示例：
            verify_ln4g95yq_8yd5gq1d_ZOJz_4i0Z_8g5H_VnqOInAXfDjQ
        """
        # 5 段随机字符串（每段约 10-12 字符）
        chars = string.ascii_letters + string.digits
        parts = ["".join(random.choices(chars, k=random.randint(10, 12))) for _ in range(5)]
        return "verify_" + "_".join(parts)

    @staticmethod
    def _gen_webid() -> str:
        """生成 webid（19 位数字）。"""
        return "".join(random.choices(string.digits, k=19))

    @staticmethod
    def _gen_msToken() -> str:
        """生成 msToken（107 字符）。"""
        chars = string.ascii_letters + string.digits + "="
        return "".join(random.choices(chars, k=107))


def get_anon_fingerprint(mobile: Optional[bool] = None) -> AnonFingerprint:
    """获取一组新的匿名指纹（便捷函数）。

    Args:
        mobile: 是否使用移动端模板（None = 随机选择桌面/移动）

    Returns:
        AnonFingerprint 实例
    """
    return AnonFingerprint.generate(mobile=mobile)
