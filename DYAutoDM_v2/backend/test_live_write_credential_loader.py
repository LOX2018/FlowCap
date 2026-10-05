# coding=utf-8
"""写接口凭证载入门禁（2026-10-01）。

## 守什么（实测驱动的回归）

用户实测日志：
```
[LIVE-002] [danmaku] 发送异常 …: Empty string does not encode a sequence
[LIVE-042] [like]    发送异常 …: Empty string does not encode a sequence
```

**根因（实机确证）**：`api/live.py::_auth_for` 用了 `utils.common_util.load_env`
这个**弱载入器**，它与项目标准载入器 `DYLoginApi._load_auth_from_env` 有两处致命差异：

| 项 | `common_util.load_env`（弱） | `DYLoginApi._load_auth_from_env`（标准） |
|---|---|---|
| 签名还原 | `perepare_auth(cookies, "", "")` ← **空 web_protect/keys** | `perepare_auth(cookies, web_protect, keys)` |
| 私钥形态 | `_src['DY_PRIVATE_KEY']` **原样** | `_decode_private_key(...)` **还原换行** |

⇒ 私钥在存储态是**字面量 `\\n`**，未还原即丢给 `SigningKey.from_pem` ⇒
`Empty string does not encode a sequence`。**只有写接口**会解析私钥
（`with_bd`→`generate_bd_ticket_client_data`），只读走 `with_bd_readonly` 不碰私钥
⇒ 这正是「只读能用、写就异常」的那一环。

**同族**：`api/platform.py` 早在 2026-09-14 就因同一缺陷（cookie 恒为空）改用了标准
载入器；`api/live.py` 与 `api/linkmic.py` 是**未收敛的残留**。本门禁钉死这一族。

## 第二处（同批实机确证的独立缺陷）

`utils/bd_ticket.py` 的分隔符守卫**恒误拦合法凭证**：真实 `ticket` 是 base64
（实测 `hash.mgTYBN0DfPL9gfxlJ…==`，**必含 `=`**），而守卫「含 `=` 即拒绝」⇒
写接口全线抛 `ValueError: ticket 含非法分隔符`。上游 `cv-cat/DouYin_Spider`
对应函数**没有**该校验。修法：按**注入可行性**分别判定 —— `ticket` 只拦 `&`
（`=` 属值内 base64 padding，不产生新键），`api` 仍拦 `&` 与 `=`。

全确定性：纯源码级断言 + 纯函数断言，**不触网、不读真凭证**。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")

_BE = os.path.dirname(os.path.abspath(__file__))


class WeakLoaderGate(unittest.TestCase):
    """W1-W3：写接口不得使用弱载入器。"""

    @staticmethod
    def _auth_for_uses_standard_loader(rel: str) -> tuple:
        """返回 (是否调用标准载入器, 是否调用弱载入器) —— 均按 **AST 真实调用**判定。

        ⚠️ 不能用字符串匹配：这些函数的 docstring **故意**写着弱载入器名字来解释根因，
        字符串匹配会把文档误判成残留（本门禁首版即踩此坑，W1/W2/W3 全红）。
        """
        import ast
        path = os.path.join(_BE, *rel.split("/"))
        tree = ast.parse(open(path, encoding="utf-8").read())
        std = weak = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Attribute):
                if f.attr == "_load_auth_from_env":
                    std = True
                if f.attr == "load_env" and node.args:   # 带位置参数 = 账号凭证加载
                    weak = True
        return std, weak

    def test_w1_live_auth_uses_standard_loader(self):
        """W1：`api/live.py` 必须**调用**标准载入器，且**不得**带参调用弱载入器。"""
        std, weak = self._auth_for_uses_standard_loader("api/live.py")
        self.assertTrue(std, "api/live.py 未调用 DYLoginApi._load_auth_from_env（写接口会因私钥未还原而抛错）")
        self.assertFalse(weak, "api/live.py 仍以位置参数调用弱载入器 load_env")

    def test_w2_linkmic_auth_uses_standard_loader(self):
        """W2：`api/linkmic.py`（同族第三处）同样必须收敛。"""
        std, weak = self._auth_for_uses_standard_loader("api/linkmic.py")
        self.assertTrue(std, "api/linkmic.py 未收敛到标准载入器")
        self.assertFalse(weak, "api/linkmic.py 仍以位置参数调用弱载入器 load_env")

    def test_w3_no_weak_loader_call_in_write_endpoints(self):
        """W3（族级）：写接口模块**实际调用**中不得出现 `…load_env(env_path)`。

        ⚠️ 必须用 **AST** 判「真实调用」，不能用字符串匹配 —— 这三个文件的
        docstring 里**故意**写着 `load_env(env_path)` 来解释本次根因，
        字符串匹配会把「文档说明」误判成「残留调用」（本门禁首版即踩此坑）。
        """
        import ast

        for rel in ("api/live.py", "api/linkmic.py", "api/platform.py"):
            path = os.path.join(_BE, *rel.split("/"))
            tree = ast.parse(open(path, encoding="utf-8").read())
            bad = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                # 形态：<something>.load_env(<至少一个位置参数>)
                if isinstance(f, ast.Attribute) and f.attr == "load_env" and node.args:
                    bad.append(f"{rel}:{node.lineno}")
            self.assertEqual(bad, [],
                             f"{rel} 仍以位置参数实际调用弱载入器 load_env：{bad}"
                             "（账号凭证加载必须走 DYLoginApi._load_auth_from_env）")


class BDTicketGuardGate(unittest.TestCase):
    """T1-T3：bd-ticket 分隔符守卫不得误拦 base64 ticket。"""

    def test_t1_base64_ticket_with_padding_passes(self):
        """T1（核心）：真实形态的 base64 ticket（含 `=`）**必须**能生成签名材料。"""
        from utils.bd_ticket import generate_bd_ticket_client_data
        # 与实测同形：hash.<b64>==
        fake_ticket = "hash.mgTYBN0DfPL9gfxlJ+Ab/Cd8efghijklmnopqrstuv=="
        out = generate_bd_ticket_client_data("/webcast/room/like/", fake_ticket, "ts.2.abc",
                                             # 合法 PEM 才能走到签名（此处用极小种子密钥）
                                             _tiny_pem())
        self.assertIsInstance(out, str)
        self.assertGreater(len(out), 20, "未产出签名材料")

    def test_t2_ampersand_in_ticket_still_rejected(self):
        """T2（负控）：`ticket` 含 `&` 仍**必须**拒绝（它能伪造额外键值对）。"""
        from utils.bd_ticket import generate_bd_ticket_client_data
        with self.assertRaises(ValueError):
            generate_bd_ticket_client_data("/webcast/room/like/",
                                           "hash.a&path=/evil", "ts.2.abc", _tiny_pem())

    def test_t3_equals_in_api_still_rejected(self):
        """T3（负控）：`api`（契约是纯路径）含 `=` 仍**必须**拒绝。"""
        from utils.bd_ticket import generate_bd_ticket_client_data
        with self.assertRaises(ValueError):
            generate_bd_ticket_client_data("/webcast/room/like/?x=1", "hash.a", "ts.2.abc",
                                           _tiny_pem())


def _tiny_pem() -> str:
    """生成一个最小合法 PEM 私钥（NIST256p）供纯函数测试用。"""
    from ecdsa import SigningKey, NIST256p
    sk = SigningKey.generate(curve=NIST256p)
    return sk.to_pem().decode()


if __name__ == "__main__":
    unittest.main(verbosity=2)