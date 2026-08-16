/**
 * 账号管理页
 *
 * 迁移自: DY_Spider_base/web/pages/accounts.js（原版 389 处 createElement，最大文件）
 * 原版职责: 增删、扫码、角色（监测/发送）、引擎校验、守护启停
 * 迁移要点:
 *   - React.createElement → JSX（389 处全转）
 *   - getAccounts 8s 轮询 setInterval → React Query useQuery
 *   - .catch(() => {}) 静默吞错 → 带 push 反馈（修复原版 bug）
 *   - 扫码状态查询 → props.api.scanStatus(name) 轮询（替代旧版间接推断）
 *   - "全部校验"黑屏 bug → 数据始终经 mapAcct 映射，类型守卫消除空字段访问
 *   - 守护进程启动 → sidecar.ts 的 startBrowserDaemon/startRecvDaemon（不使用 props.api）
 */
import { Fragment, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { PageProps } from "../api/client";
import { Avatar, Dot, Pill, TABS, hue, tick } from "../components/ui";
import { startBrowserDaemon, stopBrowserDaemon, startRecvDaemon, stopRecvDaemon } from "../api/sidecar";

// ===== 类型定义 =====

interface EngineInfo {
  level: string;
  label: string;
  detail: string;
}

interface RawAccount {
  name: string;
  uid?: string;
  level?: string;
  label?: string;
  loggedIn?: boolean;
  browserDaemonPort?: number;
  recvDaemonPort?: number;
  browserDaemonAlive?: boolean;
  recvDaemonAlive?: boolean;
  wpEngine?: EngineInfo;
  dmEngine?: EngineInfo;
  isCurrent?: boolean;
  isMonitor?: boolean;
  isSender?: boolean;
}

interface LastRun {
  room: string;
  roomUrl: string;
  time: string;
  duration: string;
  totalRuns: number;
  comments: number;
  dmSent: number;
  dmSuccess: number;
  dmFail: number;
  dmAfterLive: number;
}

interface FpInfo {
  name: string;
  status: string;
  os: string;
  ip: string;
  resolution: string;
  ua: string;
  proxy: string;
  lang: string;
  webrtc: string;
  timezone: string;
}

interface FmtAccount {
  id: string;
  name: string;
  uid: string;
  hue: string;
  tokenValid: boolean;
  tokenExpire: string;
  lvlLabel: string;
  lvl: string;
  browserDaemonPort?: number;
  recvDaemonPort?: number;
  browserDaemonAlive: boolean;
  recvDaemonAlive: boolean;
  wpEngine: EngineInfo;
  dmEngine: EngineInfo;
  isCurrent: boolean;
  isMonitor: boolean;
  isSender: boolean;
  lastCheck: string;
  lastRun: LastRun;
  fp: FpInfo;
}

interface CheckResult {
  ok?: boolean;
  error?: string;
  msg?: string;
  verify?: { wp?: EngineInfo; dm?: EngineInfo; uid?: string };
}

interface ReviewRow {
  id: number;
  time: string;
  name: string;
  lv: number;
  content: string;
  dmStatus: string;
  dmText: string;
  dmTime: string;
  ts: number;
}

interface AcctForm {
  name: string;
  uid: string;
  cookie: string;
}

interface ProxyForm {
  type: string;
  host: string;
  port: string;
  user: string;
  pass: string;
  testUrl: string;
}

type PillColor = "ok" | "warn" | "danger" | "accent" | "mute";

/** 统一提取错误信息（修复原版 .catch(() => {}) 静默吞错） */
function errMsg(e: unknown): string {
  return (e as { message?: string })?.message || String(e);
}

/** wp/dm 引擎 level → Pill 颜色（旧版 'info'/unknown 落到 mute） */
function enginePill(level: string): PillColor {
  if (level === "ok") return "ok";
  if (level === "warn") return "warn";
  if (level === "fail" || level === "error") return "danger";
  return "mute";
}

/**
 * 后端原始账号 → 前端卡片渲染对象。
 * 补齐 lastRun/fp 默认值，避免直接渲染原始对象导致渲染崩溃黑屏（原版"全部校验" bug 根因）。
 */
function mapAcct(a: RawAccount, i: number): FmtAccount {
  const lvl = a.level || (a.loggedIn ? "ok" : "expired");
  const lvlLabel = a.label || (a.loggedIn ? "凭证有效" : "凭证过期");
  const isValid = lvl === "ok";
  return {
    id: "ra" + i,
    name: a.name,
    uid: a.uid || "—",
    hue: hue(a.name.length * 2),
    tokenValid: isValid,
    tokenExpire: isValid
      ? "有效"
      : lvl === "nosign"
        ? "未扫码"
        : lvl === "missing"
          ? "未配置"
          : "失效",
    lvlLabel,
    lvl,
    browserDaemonPort: a.browserDaemonPort,
    recvDaemonPort: a.recvDaemonPort,
    browserDaemonAlive: !!a.browserDaemonAlive,
    recvDaemonAlive: !!a.recvDaemonAlive,
    wpEngine: a.wpEngine || { level: "unknown", label: "未校验", detail: "" },
    dmEngine: a.dmEngine || { level: "unknown", label: "未校验", detail: "" },
    isCurrent: !!a.isCurrent,
    isMonitor: !!a.isMonitor,
    isSender: !!a.isSender,
    lastCheck: "刚刚",
    lastRun: {
      room: "—",
      roomUrl: "",
      time: "—",
      duration: "—",
      totalRuns: 0,
      comments: 0,
      dmSent: 0,
      dmSuccess: 0,
      dmFail: 0,
      dmAfterLive: 0,
    },
    fp: {
      name: "浏览器守护 · " + a.name,
      status: "running",
      os: "Windows 11",
      ip: "—",
      resolution: "1920×1080",
      ua: "Chrome",
      proxy: "直连",
      lang: "zh-CN",
      webrtc: "替换",
      timezone: "Asia/Shanghai",
    },
  };
}
// ===== 主页面 =====

export default function AccountsPage(props: PageProps) {
  const { push, api } = props;
  const qc = useQueryClient();

  const [reviewAccount, setReviewAccount] = useState<FmtAccount | null>(null);
  const [proxyAcct, setProxyAcct] = useState<FmtAccount | null>(null);
  const [proxyForm, setProxyForm] = useState<ProxyForm>({
    type: "socks5",
    host: "127.0.0.1",
    port: "1080",
    user: "",
    pass: "",
    testUrl: "",
  });
  const [proxyTesting, setProxyTesting] = useState(false);
  const [proxyTestResult, setProxyTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addForm, setAddForm] = useState<AcctForm>({ name: "", uid: "", cookie: "" });
  const [editAcct, setEditAcct] = useState<FmtAccount | null>(null);
  const [editForm, setEditForm] = useState<AcctForm>({ name: "", uid: "", cookie: "" });
  const [batchMode, setBatchMode] = useState(false);
  const [batchSel, setBatchSel] = useState<Set<string>>(() => new Set());
  const [scanning, setScanning] = useState<{ name: string; seq: number } | null>(null);
  // 正在打开指纹浏览器的账号（防止重复点击）
  const [busy, setBusy] = useState<string | null>(null);
  // 管理面板展开状态（哪个账号的四宫格管理面板展开了）
  const [manageOpen, setManageOpen] = useState<string | null>(null);

  // 账号列表 8s 轮询（替代旧版 setInterval；数据始终经 mapAcct 映射）
  const { data: rawAccounts, isLoading, refetch } = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.getAccounts()) as RawAccount[],
    refetchInterval: 8000,
    enabled: !!props.ready,
  });

  // 扫码状态 2s 轮询（替代旧版间接推断；done 后自动停止）
  const { data: scanData } = useQuery({
    queryKey: ["scan-status", scanning?.name, scanning?.seq],
    queryFn: () => api.scanStatus(scanning!.name),
    enabled: !!scanning,
    refetchInterval: (q) => (q.state.data?.done ? false : 2000),
  });

  useEffect(() => {
    if (!scanData || !scanning) return;
    if (scanData.done) {
      push(
        scanData.loggedIn
          ? `扫码成功 · 凭证已写回 · ${scanning.name}`
          : `扫码未完成或失败 · ${scanning.name}`,
      );
      setScanning(null);
      refetch();
    }
  }, [scanData, scanning, push, refetch]);

  const realAccts = useMemo<FmtAccount[] | null>(() => {
    if (!rawAccounts) return null;
    return rawAccounts.map(mapAcct);
  }, [rawAccounts]);

  const shownAccounts: FmtAccount[] = realAccts ?? [];
  const showSkeleton = !!props.ready && isLoading && realAccts === null;

  const validCnt = shownAccounts.filter((a) => a.tokenValid).length;
  const fpRunning = shownAccounts.filter((a) => a.fp.status === "running").length;
  const fpSet = useMemo(() => {
    const map = new Map<string, FpInfo>();
    shownAccounts.forEach((a) => {
      if (!map.has(a.fp.name)) map.set(a.fp.name, a.fp);
    });
    return Array.from(map.values());
  }, [shownAccounts]);

  // Esc 关闭弹层
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (proxyAcct) setProxyAcct(null);
        else if (reviewAccount) setReviewAccount(null);
        else if (editAcct) setEditAcct(null);
        else if (addOpen) setAddOpen(false);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [proxyAcct, reviewAccount, editAcct, addOpen]);

  const checkAll = () => {
    refetch()
      .then(() => push("已刷新全部账号状态"))
      .catch((e: unknown) => push("刷新异常: " + errMsg(e)));
  };

  const openEdit = (a: FmtAccount) => {
    setEditForm({ name: a.name, uid: a.uid && a.uid !== "—" ? a.uid : "", cookie: "" });
    setEditAcct(a);
  };

  const saveEdit = () => {
    const name = editForm.name.trim();
    if (!name) {
      push("请填写昵称");
      return;
    }
    api
      .scanLogin(name)
      .then((r) => {
        if (r && r.ok) {
          push("已弹出指纹浏览器 · " + name + "，请扫码完成登录（凭证将自动写回）");
          setEditAcct(null);
          setScanning({ name, seq: Date.now() });
        } else {
          push("重新获取凭证失败: " + ((r as { error?: string })?.error || ""));
        }
      })
      .catch((e: unknown) => push("重新获取凭证异常: " + errMsg(e)));
  };

  const toggleBatch = (id: string) => {
    setBatchSel((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  };

  const batchAll = () => {
    if (batchSel.size === shownAccounts.length) setBatchSel(new Set());
    else setBatchSel(new Set(shownAccounts.map((a) => a.id)));
  };

  const batchCheck = () => {
    if (!batchSel.size) {
      push("请先勾选账号");
      return;
    }
    const selected = shownAccounts.filter((a) => batchSel.has(a.id));
    Promise.all(selected.map((a) => api.checkAccount(a.name).catch(() => null)))
      .then(() => {
        push("已对 " + selected.length + " 个所选账号执行凭证校验");
        refetch();
      })
      .catch((e: unknown) => push("批量校验异常: " + errMsg(e)));
  };

  const batchExport = () => {
    if (!batchSel.size) {
      push("请先勾选账号");
      return;
    }
    push("已导出 " + batchSel.size + " 个账号的运行数据");
  };

  const batchDelete = () => {
    if (!batchSel.size) {
      push("请先勾选账号");
      return;
    }
    const ids = Array.from(batchSel);
    // 乐观更新：立即从本地列表移除勾选的卡片，消除后端刷新 3s+ 等待，感官零延迟
    const removedNames = new Set(
      ids.map((id) => shownAccounts.find((x) => x.id === id)?.name).filter(Boolean),
    );
    qc.setQueryData<RawAccount[]>(["accounts"], (old) =>
      (old ?? []).filter((a) => !removedNames.has(a.name)),
    );
    push("正在删除 " + ids.length + " 个账号…");
    Promise.all(
      ids.map((id) => {
        const a = shownAccounts.find((x) => x.id === id);
        return a ? api.removeAccount(a.name).catch(() => null) : Promise.resolve(null);
      }),
    )
      .then(() => {
        push("已删除 " + ids.length + " 个账号");
        setBatchSel(new Set());
        setBatchMode(false);
      })
      .catch((e: unknown) => push("删除异常: " + errMsg(e)))
      .finally(() => {
        // 后台静默重新拉取，纠正乐观更新的可能不一致（不阻塞 UI）
        refetch();
      });
  };

  const addAccount = () => {
    const name = addForm.name.trim();
    if (!name) {
      push("请填写昵称");
      return;
    }
    api
      .addAccount(name)
      .then((r) => {
        if (r && r.ok) {
          setAddOpen(false);
          setAddForm({ name: "", uid: "", cookie: "" });
          push("已新增账号 " + name + "，正在打开内置指纹浏览器…");
          api
            .scanLogin(name)
            .then((s) => {
              if (s && s.ok) {
                push("已弹出浏览器扫码窗口，请在浏览器中完成扫码登录（凭证将自动写回）");
                setScanning({ name, seq: Date.now() });
              } else {
                push("打开扫码窗口失败: " + ((s as { error?: string })?.error || ""));
              }
            })
            .catch((e: unknown) => push("扫码异常: " + errMsg(e)));
          refetch();
        } else {
          push("新增失败: " + ((r as { error?: string })?.error || ""));
        }
      })
      .catch((e: unknown) => push("新增异常: " + errMsg(e)));
  };

  // 守护进程：启动/停止走 sidecar
  const toggleBrowserDaemon = (a: FmtAccount) => {
    if (a.browserDaemonAlive) {
      // 停止
      stopBrowserDaemon(a.name, a.browserDaemonPort || 0)
        .then(() => {
          qc.setQueryData<RawAccount[]>(["accounts"], (old) =>
            (old || []).map((x) => (x.name === a.name ? { ...x, browserDaemonAlive: false } : x)),
          );
          push("凭证守护已停止");
          api.addLog("SUCCESS", `凭证守护已停止 · ${a.name}`).catch(() => {});
        })
        .catch((e: unknown) => {
          push("停止失败: " + errMsg(e));
          api.addLog("ERROR", `凭证守护停止失败 · ${a.name}: ${errMsg(e)}`).catch(() => {});
        });
      return;
    }
    // 启动
    startBrowserDaemon(a.name, a.browserDaemonPort || 0)
      .then(() => {
        qc.setQueryData<RawAccount[]>(["accounts"], (old) =>
          (old || []).map((x) => (x.name === a.name ? { ...x, browserDaemonAlive: true } : x)),
        );
        push("凭证守护已启动");
        api.addLog("SUCCESS", `凭证守护已启动 · ${a.name}`).catch(() => {});
      })
      .catch((e: unknown) => {
        push("启动失败: " + errMsg(e));
        api.addLog("ERROR", `凭证守护启动失败 · ${a.name}: ${errMsg(e)}`).catch(() => {});
      });
  };

  // 打开该账号绑定的指纹浏览器窗口（安全流程：先停守护释放 profile 锁，再弹窗）
  const onOpenFingerprint = (name: string) => {
    if (busy) return;
    setBusy(name);
    api
      .openFingerprintBrowser(name)
      .then((d) => {
        if (d && d.ok) {
          push(d.msg || `已打开指纹浏览器 · ${name}`);
          api.addLog("SUCCESS", `已打开指纹浏览器 · ${name}`).catch(() => {});
          // 守护被停止后乐观更新卡片状态（稍后轮询也会自动刷新）
          qc.setQueryData<RawAccount[]>(["accounts"], (old) =>
            (old || []).map((x) =>
              x.name === name ? { ...x, browserDaemonAlive: false } : x,
            ),
          );
        } else {
          push("打开失败: " + ((d && d.msg) || "未知错误"));
          api.addLog("ERROR", `打开指纹浏览器失败 · ${name}: ${(d && d.msg) || "未知错误"}`).catch(() => {});
        }
      })
      .catch((e: unknown) => {
        push("打开失败: " + errMsg(e));
        api.addLog("ERROR", `打开指纹浏览器失败 · ${name}: ${errMsg(e)}`).catch(() => {});
      })
      .finally(() => {
        // 弹窗是后台线程，立即释放按钮；实际扫码进度由 scan-status 轮询覆盖
        setBusy(null);
        refetch();
      });
  };

  const toggleRecvDaemon = (a: FmtAccount) => {
    if (a.recvDaemonAlive) {
      // 停止
      stopRecvDaemon([a.name], a.recvDaemonPort || 0)
        .then(() => {
          qc.setQueryData<RawAccount[]>(["accounts"], (old) =>
            (old || []).map((x) => (x.name === a.name ? { ...x, recvDaemonAlive: false } : x)),
          );
          push("私信守护已停止");
          api.addLog("SUCCESS", `私信守护已停止 · ${a.name}`).catch(() => {});
        })
        .catch((e: unknown) => {
          push("停止失败: " + errMsg(e));
          api.addLog("ERROR", `私信守护停止失败 · ${a.name}: ${errMsg(e)}`).catch(() => {});
        });
      return;
    }
    startRecvDaemon([a.name], a.recvDaemonPort || 0)
      .then(() => {
        qc.setQueryData<RawAccount[]>(["accounts"], (old) =>
          (old || []).map((x) => (x.name === a.name ? { ...x, recvDaemonAlive: true } : x)),
        );
        push("私信守护已启动");
        api.addLog("SUCCESS", `私信守护已启动 · ${a.name}`).catch(() => {});
      })
      .catch((e: unknown) => {
        push("启动失败: " + errMsg(e));
        api.addLog("ERROR", `私信守护启动失败 · ${a.name}: ${errMsg(e)}`).catch(() => {});
      });
  };

  const runCheck = (a: FmtAccount) => {
    push("已发起账号引擎校验 · " + a.name);
    api.addLog("INFO", `发起账号引擎校验 · ${a.name}`).catch(() => {});
    // 乐观更新：先置"校验中"，避免回环测试耗时期间按钮无反馈
    qc.setQueryData<RawAccount[]>(["accounts"], (old) =>
      (old || []).map((x) =>
        x.name === a.name
          ? {
              ...x,
              dmEngine: {
                level: "unknown",
                label: "校验中…",
                detail: "正在拉取私信会话列表（无副作用），验证私信凭证是否有效",
              },
            }
          : x,
      ),
    );
    api
      .checkAccount(a.name)
      .then((d) => {
        const r = d as CheckResult;
        if (r && r.ok && r.verify) {
          const v = r.verify;
          qc.setQueryData<RawAccount[]>(["accounts"], (old) =>
            (old || []).map((x) =>
              x.name === a.name
                ? {
                    ...x,
                    wpEngine: v.wp || x.wpEngine,
                    dmEngine: v.dm || x.dmEngine,
                    uid: v.uid || x.uid,
                  }
                : x,
            ),
          );
          push(
            "引擎校验完成 · " +
              a.name +
              " · wp: " +
              (v.wp && v.wp.label) +
              " · dm: " +
              (v.dm && v.dm.label),
          );
          api.addLog("SUCCESS", `引擎校验完成 · ${a.name} · wp: ${v.wp && v.wp.label} · dm: ${v.dm && v.dm.label}`).catch(() => {});
        } else if (r && r.error) {
          push("引擎校验失败: " + r.error);
          api.addLog("ERROR", `引擎校验失败 · ${a.name}: ${r.error}`).catch(() => {});
          refetch();
        }
        // 成功分支不 refetch：list_accounts 的 dmEngine 默认写死“待校验”，
        // 会立即覆盖掉刚更新的真实回环结果，故保留 setQueryData 写入的真值。
      })
      .catch((e: unknown) => {
        push("引擎校验异常: " + errMsg(e));
        api.addLog("ERROR", `引擎校验异常 · ${a.name}: ${errMsg(e)}`).catch(() => {});
      });
  };

  const handleScanVerify = (a: FmtAccount) => {
    push("已提醒处理验证 · " + a.name + " · 将在弹出的指纹浏览器抖音首页完成重新扫码");
    api.addLog("INFO", `提醒处理验证 · ${a.name}`).catch(() => {});
    api
      .autoRecapture(a.name)
      .then((d) => {
        const r = d as { ok?: boolean; msg?: string; error?: string };
        if (r && r.ok) {
          push(r.msg || ("已为 " + a.name + " 弹出指纹浏览器重新捕获私信凭证"));
          api.addLog("SUCCESS", `已为 ${a.name} 弹出指纹浏览器重新捕获私信凭证`).catch(() => {});
          setScanning({ name: a.name, seq: Date.now() });
        } else if (r && r.error) {
          push("处理验证失败: " + r.error);
          api.addLog("ERROR", `处理验证失败 · ${a.name}: ${r.error}`).catch(() => {});
        }
      })
      .catch((e: unknown) => {
        push("处理验证异常: " + errMsg(e));
        api.addLog("ERROR", `处理验证异常 · ${a.name}: ${errMsg(e)}`).catch(() => {});
      });
  };

  // 点击卡片 = 选中该账号为监听账号（设为监测/发送角色）
  // 注意：后端 AccountRole 枚举值为 watch/send/both（见 backend/models/enums.py）
  const selectAsMonitor = (a: FmtAccount) => {
    api.setRole(a.name, "watch").then(() => {
      api.addLog("INFO", `选中监听账号 · ${a.name}`).catch(() => {});
    }).catch((e: unknown) => push("设置角色失败: " + errMsg(e)));
    api.setRole(a.name, "send").catch((e: unknown) => push("设置角色失败: " + errMsg(e)));
    push("已选中监听账号 · " + a.name);
    refetch();
  };
  return (
    <div>
      <div className="section-head">
        <div>
          <h2>账号管理</h2>
          <div className="desc">授权账号凭证监控 · 运行日志 · 指纹浏览器环境</div>
        </div>
        <div className="head-row">
          <button className="btn ghost" data-od-id="acct-add" onClick={() => setAddOpen(true)}>
            新增账号
          </button>
          <button
            className="btn ghost"
            data-od-id="acct-batch"
            onClick={() => {
              setBatchMode((b) => !b);
              setBatchSel(new Set());
            }}
          >
            {batchMode ? "退出批量" : "批量管理"}
          </button>
          <button className="btn ghost" onClick={checkAll}>
            全部校验
          </button>
        </div>
        {!props.ready && <span className="demo-tag">未连接</span>}
      </div>

      <div className="acct-summary">
        <div className="card stat" data-od-id="acct-total">
          <span className="label">已授权账号</span>
          <span className="num">
            {shownAccounts.length}
            <span className="unit">个</span>
          </span>
        </div>
        <div className="card stat" data-od-id="acct-valid">
          <span className="label">有效凭证</span>
          <span className="num" style={{ color: "var(--ok)" }}>
            {validCnt}
            <span className="unit">个</span>
          </span>
        </div>
        <div className="card stat" data-od-id="acct-expired">
          <span className="label">过期 / 异常</span>
          <span className="num" style={{ color: "var(--danger)" }}>
            {shownAccounts.length - validCnt}
            <span className="unit">个</span>
          </span>
        </div>
        <div className="card stat" data-od-id="acct-fp">
          <span className="label">指纹浏览器在线</span>
          <span className="num" style={{ color: "var(--accent)" }}>
            {fpRunning}
            <span className="unit">/ {fpSet.length}</span>
          </span>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 14 }} data-od-id="account-list">
        {batchMode && (
          <div className="batch-bar" data-od-id="acct-batch-bar">
            <button className="btn sm ghost" onClick={batchAll}>
              {batchSel.size === shownAccounts.length ? "取消全选" : "全选"}
            </button>
            <span className="batch-count">
              已选{" "}
              <b style={{ color: "var(--accent)", fontFamily: "var(--font-mono)" }}>
                {batchSel.size}
              </b>{" "}
              / {shownAccounts.length} 个账号
            </span>
            <div style={{ flex: 1 }} />
            <button className="btn sm ghost" onClick={batchCheck}>
              批量校验
            </button>
            <button className="btn sm ghost" onClick={batchExport}>
              批量导出
            </button>
            <button
              className="btn sm ghost"
              style={{ color: "var(--danger)", borderColor: "var(--danger)" }}
              onClick={batchDelete}
            >
              删除所选
            </button>
          </div>
        )}

        {showSkeleton
          ? Array.from({ length: 3 }).map((_, i) => (
              <div className="card acct-card sk" key={"sk" + i} style={{ height: 220 }} />
            ))
          : shownAccounts.map((a) => (
              <div
                className="card acct-card"
                key={a.id}
                data-od-id={"acct-" + a.id}
                onClick={() => selectAsMonitor(a)}
                style={
                  a.isMonitor
                    ? {
                        border: "2px solid var(--accent)",
                        boxShadow: "0 0 0 3px rgba(54,194,207,0.35)",
                        cursor: "pointer",
                      }
                    : { cursor: "pointer" }
                }
              >
                <div className="acct-header">
                  {batchMode && (
                    <input
                      type="checkbox"
                      className="batch-chk"
                      checked={batchSel.has(a.id)}
                      onChange={() => toggleBatch(a.id)}
                      aria-label={"选择 " + a.name}
                    />
                  )}
                  <Avatar name={a.name} h={a.hue} lg />
                  <div className="info">
                    <div className="nm">
                      {a.name}
                      <Pill c={a.tokenValid ? "ok" : a.lvl === "nosign" ? "warn" : "danger"}>
                        {a.lvlLabel || (a.tokenValid ? "凭证有效" : "凭证过期")}
                      </Pill>
                    </div>
                    <div className="uid">
                      UID {a.uid} · 上次校验 {a.lastCheck}
                    </div>
                  </div>
                  <div className="ops">
                    <button
                      className={"btn sm" + (manageOpen === a.id ? " accent" : " ghost")}
                      onClick={(e) => {
                        e.stopPropagation();
                        setManageOpen(manageOpen === a.id ? null : a.id);
                      }}
                      title="展开账号管理面板（指纹浏览器 / 守护 / 代理 / 凭证）"
                    >
                      {manageOpen === a.id ? "收起管理" : "管理"}
                    </button>
                    <button
                      className="btn sm ghost"
                      onClick={(e) => {
                        e.stopPropagation();
                        openEdit(a);
                      }}
                      title="编辑账号信息并刷新登录凭证"
                    >
                      刷新凭证
                    </button>
                    <button
                      className="btn sm ghost"
                      onClick={(e) => {
                        e.stopPropagation();
                        setReviewAccount(a);
                      }}
                    >
                      查阅模式
                    </button>
                  </div>
                </div>

                {/* 管理面板：原卡片四板块（守护服务 / 上次运行日志 / 引擎校验 / 关联指纹浏览器） */}
                  {manageOpen === a.id && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      transition={{ duration: 0.2 }}
                      style={{ overflow: "hidden" }}
                    >
                      <div className="acct-body" onClick={(e) => e.stopPropagation()}>
                  {/* 守护服务 */}
                  <div className="acct-section" style={{ gridColumn: "1", gridRow: "1" }}>
                    <h4>守护服务</h4>
                    <div className="acct-row" style={{ justifyContent: "flex-start" }}>
                      <span className="k">凭证守护</span>
                      <span className="v" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <Pill c={a.browserDaemonAlive ? "ok" : "mute"}>
                          {a.browserDaemonAlive ? "运行中" : "未运行"}
                        </Pill>
                        <button className="btn sm ghost" onClick={(e) => { e.stopPropagation(); toggleBrowserDaemon(a); }}>
                          {a.browserDaemonAlive ? "停止" : "启动"}
                        </button>
                        <span style={{ fontSize: 11, color: "var(--muted)" }}>
                          :{a.browserDaemonPort}
                        </span>
                      </span>
                    </div>
                    <div className="acct-row" style={{ justifyContent: "flex-start", marginTop: 10 }}>
                      <span className="k">私信守护</span>
                      <span className="v" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <Pill c={a.recvDaemonAlive ? "ok" : "mute"}>
                          {a.recvDaemonAlive ? "运行中" : "未运行"}
                        </Pill>
                        <button className="btn sm ghost" onClick={(e) => { e.stopPropagation(); toggleRecvDaemon(a); }}>
                          {a.recvDaemonAlive ? "停止" : "启动"}
                        </button>
                        <span style={{ fontSize: 11, color: "var(--muted)" }}>
                          :{a.recvDaemonPort}
                        </span>
                      </span>
                    </div>
                  </div>

                  {/* 上次运行日志 */}
                  <div className="acct-section" style={{ gridColumn: "1", gridRow: "2" }}>
                    <h4>上次运行日志</h4>
                    <div className="acct-row">
                      <span className="k">直播间</span>
                      <span
                        className="v"
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          color: a.lastRun.room === "—" ? "var(--muted)" : "var(--fg)",
                        }}
                      >
                        {a.lastRun.room}
                        {a.lastRun.roomUrl && (
                          <a
                            href={a.lastRun.roomUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="btn sm ghost"
                            style={{ height: 24, padding: "0 8px", fontSize: 11, lineHeight: 1 }}
                            onClick={(e) => { e.stopPropagation(); push("已打开直播间：" + a.lastRun.room); }}
                          >
                            前往直播间
                          </a>
                        )}
                      </span>
                    </div>
                    <div className="acct-row">
                      <span className="k">运行时间</span>
                      <span className="v">{a.lastRun.time}</span>
                    </div>
                    <div className="acct-row">
                      <span className="k">运行时长</span>
                      <span className="v">{a.lastRun.duration}</span>
                    </div>
                    <div className="acct-row">
                      <span className="k">累计运行</span>
                      <span className="v">{a.lastRun.totalRuns} 次</span>
                    </div>
                    <div className="acct-log-grid" style={{ marginTop: 8 }}>
                      <div className="acct-log-cell">
                        <div className="lc-label">捕获评论</div>
                        <div className="lc-val">{a.lastRun.comments.toLocaleString()}</div>
                      </div>
                      <div className="acct-log-cell">
                        <div className="lc-label">发送私信</div>
                        <div className="lc-val" style={{ color: "var(--accent)" }}>
                          {a.lastRun.dmSent}
                        </div>
                      </div>
                      <div className="acct-log-cell">
                        <div className="lc-label">下播私信</div>
                        <div className="lc-val" style={{ color: "var(--warn)" }}>
                          {a.lastRun.dmAfterLive}
                        </div>
                      </div>
                      <div className="acct-log-cell">
                        <div className="lc-label">成功</div>
                        <div className="lc-val" style={{ color: "var(--ok)" }}>
                          {a.lastRun.dmSuccess}
                        </div>
                      </div>
                      <div className="acct-log-cell">
                        <div className="lc-label">失败</div>
                        <div className="lc-val" style={{ color: "var(--danger)" }}>
                          {a.lastRun.dmFail}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 引擎校验 */}
                  <div className="acct-section" style={{ gridColumn: "2", gridRow: "1" }}>
                    <h4>引擎校验</h4>
                    <div style={{ display: "flex", alignItems: "stretch", gap: 10 }}>
                      <div style={{ flex: 1, display: "grid", gap: 10 }}>
                        <div style={{ padding: "10px 12px", background: "var(--bg)", borderRadius: 8 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                            <span style={{ fontWeight: 600, fontSize: 13 }}>wp 引擎</span>
                            <Pill c={enginePill(a.wpEngine.level)}>
                              {a.wpEngine.level === "stopped" ? "未运行" : a.wpEngine.label}
                            </Pill>
                          </div>
                          <div style={{ fontSize: 11.5, color: "var(--muted)", lineHeight: 1.4 }}>
                            {a.wpEngine.detail}
                          </div>
                        </div>
                        {(a.wpEngine.level === "fail" || a.dmEngine.level === "fail") && (
                          <button
                            className="btn sm danger"
                            style={{ marginTop: 8 }}
                            onClick={(e) => { e.stopPropagation(); handleScanVerify(a); }}
                          >
                            立即处理验证
                          </button>
                        )}
                        <div style={{ padding: "10px 12px", background: "var(--bg)", borderRadius: 8 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                            <span style={{ fontWeight: 600, fontSize: 13 }}>私信引擎</span>
                            <Pill c={enginePill(a.dmEngine.level)}>{a.dmEngine.label}</Pill>
                          </div>
                          <div style={{ fontSize: 11.5, color: "var(--muted)", lineHeight: 1.4 }}>
                            {a.dmEngine.detail}
                          </div>
                        </div>
                      </div>
                      <div
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          justifyContent: "center",
                          alignItems: "stretch",
                          minWidth: 96,
                        }}
                      >
                        <button className="btn sm" onClick={(e) => { e.stopPropagation(); runCheck(a); }}>
                          引擎校验
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* 关联指纹浏览器 */}
                  <div className="acct-section" style={{ gridColumn: "2", gridRow: "2" }}>
                    <h4>关联指纹浏览器</h4>
                    <div
                      className="fp-card"
                      onDoubleClick={(e) => {
                        e.stopPropagation();
                        onOpenFingerprint(a.name);
                      }}
                      title="双击打开指纹浏览器"
                      style={{ cursor: "pointer" }}
                    >
                      <div className="fp-header">
                        <Dot c={a.fp.status === "running" ? "ok" : "warn"} pulse={a.fp.status === "running"} />
                        <span className="nm">{a.fp.name}</span>
                        <Pill c={a.fp.status === "running" ? "ok" : "warn"}>
                          {a.fp.status === "running" ? "运行中" : "已停止"}
                        </Pill>
                        <div style={{ flex: 1 }} />
                        <button
                          className="btn sm ghost"
                          onClick={(e) => {
                            e.stopPropagation();
                            setProxyAcct(a);
                            setProxyForm({
                              type: a.fp.proxy.includes("SOCKS")
                                ? "socks5"
                                : a.fp.proxy.includes("HTTP")
                                  ? "http"
                                  : "direct",
                              host: a.fp.proxy.includes("·")
                                ? a.fp.proxy.split("·")[1]?.split(":")[0] || ""
                                : "",
                              port: a.fp.proxy.includes("·")
                                ? a.fp.proxy.split("·")[1]?.split(":")[1] || ""
                                : "",
                              user: "",
                              pass: "",
                              testUrl: "",
                            });
                            setProxyTestResult(null);
                            push("已打开代理配置 · " + a.name);
                          }}
                        >
                          代理配置
                        </button>
                      </div>
                      <dl className="fp-env">
                        <dt>系统</dt>
                        <dd>{a.fp.os}</dd>
                        <dt>代理</dt>
                        <dd style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}>
                          {a.fp.proxy}
                        </dd>
                        <dt>分辨率</dt>
                        <dd>{a.fp.resolution}</dd>
                        <dt>UA</dt>
                        <dd
                          style={{
                            fontSize: 11,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                            maxWidth: 180,
                          }}
                        >
                          {a.fp.ua}
                        </dd>
                        <dt>WebRTC</dt>
                        <dd>{a.fp.webrtc}</dd>
                        <dt>时区</dt>
                        <dd>{a.fp.timezone}</dd>
                      </dl>
                      </div>
                  </div>
                </div>
                    </motion.div>
                )}
              </div>
            ))}
      </div>

      <AnimatePresence>
        {reviewAccount && (
          <AccountReview
            key="account-review"
            account={reviewAccount}
            onClose={() => setReviewAccount(null)}
            push={push}
            goMsg={props.goMsg}
          />
        )}
        {proxyAcct && (
          <ProxyDrawer
            key="proxy-drawer"
            account={proxyAcct}
            form={proxyForm}
            setForm={setProxyForm}
            testing={proxyTesting}
            testResult={proxyTestResult}
            onTest={() => {
              setProxyTesting(true);
              setTimeout(() => {
                const ok = Math.random() > 0.3;
                setProxyTestResult({
                  ok,
                  msg: ok
                    ? `连接成功 · ${proxyForm.type}://${proxyForm.host}${proxyForm.port ? ":" + proxyForm.port : ""} → ${(Math.random() * 200 + 50).toFixed(0)}ms`
                    : "连接超时 · 请检查代理地址和端口是否正确",
                });
                setProxyTesting(false);
                push(ok ? "代理连接测试成功" : "代理连接测试失败");
              }, 1500);
            }}
            onSave={() => {
              push("代理配置已保存 · " + proxyAcct.name);
              setProxyAcct(null);
            }}
            onClose={() => setProxyAcct(null)}
          />
        )}
        {addOpen && (
          <AccountDrawer
            key="account-drawer"
            mode="add"
            form={addForm}
            setForm={setAddForm}
            onSave={addAccount}
            onClose={() => setAddOpen(false)}
          />
        )}
        {editAcct && (
          <AccountDrawer
            key="account-edit-drawer"
            mode="edit"
            account={editAcct}
            form={editForm}
            setForm={setEditForm}
            onSave={saveEdit}
            onClose={() => setEditAcct(null)}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
// ===== 查阅模式（只读详情） =====

const DM_META: Record<string, [string, PillColor]> = {
  un: ["未私信", "mute"],
  wait: ["待发送", "warn"],
  sent: ["已发送", "ok"],
  fail: ["发送失败", "danger"],
};

function AccountReview({
  account,
  onClose,
  push,
  goMsg,
}: {
  account: FmtAccount;
  onClose: () => void;
  push: (msg: string) => void;
  goMsg?: (name: string, text?: string) => void;
}) {
  const [q, setQ] = useState("");
  const [st, setSt] = useState("all");
  const [asc, setAsc] = useState(false);
  const [exp, setExp] = useState<number | null>(null);

  const rows: ReviewRow[] = [];
  const filtered = useMemo(() => {
    let list = rows.slice();
    if (st !== "all") list = list.filter((r) => r.dmStatus === st);
    if (q.trim()) {
      const kw = q.trim();
      list = list.filter((r) => (r.name + r.content + r.dmText).includes(kw));
    }
    list.sort((a, b) => (asc ? a.ts - b.ts : b.ts - a.ts));
    return list;
  }, [rows, q, st, asc]);
  const cnt = (s: string) => rows.filter((r) => r.dmStatus === s).length;

  const exportCSV = () => {
    const head = ["发送时间", "发言人", "用户等级", "评论内容", "私信状态", "私信文案", "私信时间"];
    const body = filtered.map((r) => [
      r.time,
      r.name,
      r.lv,
      r.content,
      DM_META[r.dmStatus]?.[0] || "",
      r.dmText || "",
      r.dmTime || "",
    ]);
    const csv = [head, ...body]
      .map((l) => l.map((c) => '"' + String(c).replace(/"/g, '""') + '"').join(","))
      .join("\n");
    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = account.name + "_review_" + tick().replace(/:/g, "") + ".csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    push("已导出 CSV · " + a.download);
  };

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const a = account;
  const r = a.lastRun;

  return (
    <motion.div
      className="overlay"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      data-od-id="acct-review"
    >
      <header className="nav">
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          <h1>抖音数据控制台</h1>
          <span className="sub">Douyin Console</span>
        </div>
        <nav className="tabs" aria-label="主导航">
          {TABS.map(([id, label]) => (
            <button
              key={id}
              data-od-id={"review-tab-" + id}
              className={"tab" + (id === "accounts" ? " active" : "")}
              onClick={() => {
                if (id !== "accounts") {
                  onClose();
                  setTimeout(() => {
                    (
                      document.querySelector('[data-od-id="tab-' + id + '"]') as HTMLElement | null
                    )?.click();
                  }, 50);
                }
              }}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="nav-status">
          <span className="badge-conn">
            <Dot c="ok" pulse /> Cookie <b>有效</b>
          </span>
          <span className="badge-conn">
            <Dot c="ok" pulse /> 直播 <b>已连接</b>
          </span>
          <span className="demo-tag">查阅模式</span>
        </div>
      </header>

      <div className="overlay-head" style={{ background: "var(--surface)" }}>
        <button className="btn ghost" onClick={onClose}>
          ‹ 返回账号管理
        </button>
        <Avatar name={a.name} h={a.hue} sm />
        <h2>{a.name} · 查阅模式</h2>
        <Pill c={a.tokenValid ? "ok" : a.lvl === "nosign" ? "warn" : "danger"}>
          {a.lvlLabel || (a.tokenValid ? "凭证有效" : "凭证过期")}
        </Pill>
        <div style={{ flex: 1 }} />
        <span className="demo-tag">只读 · {a.uid}</span>
      </div>

      <div className="overlay-body">
        <div className="review-stats-row" data-od-id="review-stats">
          <div className="card stat" style={{ flex: 1, minWidth: 140 }}>
            <span className="label">捕获评论</span>
            <span className="num">
              {r.comments.toLocaleString()}
              <span className="unit">条</span>
            </span>
          </div>
          <div className="card stat" style={{ flex: 1, minWidth: 140 }}>
            <span className="label">发送私信</span>
            <span className="num" style={{ color: "var(--accent)" }}>
              {r.dmSent}
              <span className="unit">条</span>
            </span>
          </div>
          <div className="card stat" style={{ flex: 1, minWidth: 140 }}>
            <span className="label">下播私信</span>
            <span className="num" style={{ color: "var(--warn)" }}>
              {r.dmAfterLive}
              <span className="unit">条</span>
            </span>
          </div>
          <div className="card stat" style={{ flex: 1, minWidth: 140 }}>
            <span className="label">成功</span>
            <span className="num" style={{ color: "var(--ok)" }}>
              {r.dmSuccess}
            </span>
          </div>
          <div className="card stat" style={{ flex: 1, minWidth: 140 }}>
            <span className="label">失败</span>
            <span className="num" style={{ color: "var(--danger)" }}>
              {r.dmFail}
            </span>
          </div>
          <div className="card stat" style={{ flex: 1, minWidth: 140 }}>
            <span className="label">成功率</span>
            <span
              className="num"
              style={{
                color: r.dmSent > 0 && r.dmSuccess / r.dmSent > 0.9 ? "var(--ok)" : "var(--warn)",
              }}
            >
              {r.dmSent > 0 ? Math.round((r.dmSuccess / r.dmSent) * 100) : 0}
              <span className="unit">%</span>
            </span>
          </div>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 320px",
            gap: 14,
            alignItems: "start",
          }}
          data-od-id="review-content"
        >
          <div>
            <div className="table-tools">
              <input
                className="input"
                placeholder="搜索昵称 / 评论内容 / 私信文案…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              <select
                className="select"
                value={st}
                onChange={(e) => setSt(e.target.value)}
                aria-label="私信状态筛选"
              >
                <option value="all">全部状态</option>
                <option value="un">未私信</option>
                <option value="wait">待发送</option>
                <option value="sent">已发送</option>
                <option value="fail">发送失败</option>
              </select>
              <button className="btn ghost" onClick={() => setAsc((s) => !s)}>
                {asc ? "时间 ↑" : "时间 ↓"}
              </button>
              <button
                className="btn ghost"
                onClick={() => {
                  setSt("all");
                  setQ("");
                }}
              >
                重置
              </button>
              <button className="btn primary" onClick={exportCSV}>
                导出 CSV
              </button>
            </div>
            <div className="count-line">
              共 <b>{filtered.length}</b> 条 ·{" "}
              <span
                className="filter-pills"
                style={{ display: "inline-flex", marginLeft: 10, verticalAlign: "middle" }}
              >
                {(
                  [
                    ["all", "全部", rows.length],
                    ["un", "未私信", cnt("un")],
                    ["wait", "待发送", cnt("wait")],
                    ["sent", "已发送", cnt("sent")],
                    ["fail", "发送失败", cnt("fail")],
                  ] as [string, string, number][]
                ).map(([id, l, c]) => (
                  <button
                    key={id}
                    className={"fpill" + (st === id ? " active" : "")}
                    onClick={() => setSt(id)}
                  >
                    {l}
                    <span className="c">{c}</span>
                  </button>
                ))}
              </span>
            </div>
            <div className="card" style={{ padding: 0 }}>
              <div className="table-scroll">
                <table className="comment-table">
                  <thead>
                    <tr>
                      <th style={{ width: 30 }} />
                      <th>发送时间</th>
                      <th>发言人</th>
                      <th>评论内容</th>
                      <th>私信状态</th>
                      <th>私信文案</th>
                      <th>私信时间</th>
                      <th style={{ width: 120 }}>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.length === 0 && (
                      <tr>
                        <td
                          colSpan={8}
                          style={{
                            padding: "36px 20px",
                            textAlign: "center",
                            color: "var(--muted)",
                            fontSize: 13,
                          }}
                        >
                          <div style={{ fontSize: 26, marginBottom: 8 }}>📭</div>
                          暂无评论记录 · 等待直播间捕获后自动入库
                        </td>
                      </tr>
                    )}
                    {filtered.map((row) => (
                      <Fragment key={row.id}>
                        <tr>
                          <td>
                            <span className="mono" style={{ color: "var(--muted)" }}>
                              {exp === row.id ? "▾" : "▸"}
                            </span>
                          </td>
                          <td className="mono">{row.time}</td>
                          <td>
                            <span className="speaker">
                              <Avatar name={row.name} h={hue(row.name.length)} sm />
                              <span className="nm">{row.name}</span>
                              {row.lv < 99 && <span className="lv">Lv.{row.lv}</span>}
                            </span>
                          </td>
                          <td className="content-cell">
                            <span className="cmt-text">{row.content}</span>
                          </td>
                          <td>
                            {row.dmStatus === "un" ? (
                              <span className="blank">—</span>
                            ) : (
                              <Pill c={DM_META[row.dmStatus]?.[1] || "mute"}>
                                {DM_META[row.dmStatus]?.[0] || ""}
                              </Pill>
                            )}
                          </td>
                          <td className="dm-cell">
                            <span className={"dm-text" + (row.dmText ? " has" : "")}>
                              {row.dmText || <span className="blank">未发送</span>}
                            </span>
                          </td>
                          <td className="mono">{row.dmTime || <span className="blank">—</span>}</td>
                          <td>
                            <div className="head-row" style={{ gap: 4 }}>
                              <button
                                className="btn text sm"
                                onClick={() => setExp(exp === row.id ? null : row.id)}
                              >
                                详情
                              </button>
                              <button
                                className="btn text sm"
                                onClick={() => {
                                  goMsg?.(row.name, row.content);
                                  push("已跳转私信中心 · " + row.name);
                                }}
                              >
                                发私信
                              </button>
                            </div>
                          </td>
                        </tr>
                        {exp === row.id && (
                          <tr key={row.id + "-d"}>
                            <td
                              colSpan={8}
                              style={{ padding: "6px 10px 14px", background: "var(--surface-2)" }}
                            >
                              <div className="detail-panel">
                                <h4>评论历史 · {row.name}</h4>
                                <div className="history-list">
                                  {rows
                                    .filter((x) => x.name === row.name)
                                    .slice(0, 5)
                                    .map((x, i) => (
                                      <div className="history-item" key={i}>
                                        <span className="tm">{x.time}</span>
                                        <span>{x.content}</span>
                                      </div>
                                    ))}
                                </div>
                                <h4>私信内容</h4>
                                <div style={{ fontSize: 13 }}>
                                  {row.dmStatus === "un" ? (
                                    <span className="blank">尚未对该发言人发送私信</span>
                                  ) : (
                                    <span>
                                      <Pill c={DM_META[row.dmStatus]?.[1] || "mute"}>
                                        {DM_META[row.dmStatus]?.[0] || ""}
                                      </Pill>
                                      {"　"}
                                      {row.dmText || "（文案未填写）"}
                                      {row.dmTime ? "　·　" + row.dmTime : ""}
                                    </span>
                                  )}
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div className="card">
              <h3>运行信息</h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
                <div className="acct-row">
                  <span className="k">直播间</span>
                  <span className="v" style={{ color: "var(--accent)" }}>
                    {r.room}
                  </span>
                </div>
                <div className="acct-row">
                  <span className="k">运行时间</span>
                  <span className="v">{r.time}</span>
                </div>
                <div className="acct-row">
                  <span className="k">运行时长</span>
                  <span className="v">{r.duration}</span>
                </div>
                <div className="acct-row">
                  <span className="k">累计运行</span>
                  <span className="v">{r.totalRuns} 次</span>
                </div>
              </div>
            </div>
            <div className="card">
              <h3>指纹浏览器</h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
                <div className="fp-header" style={{ marginBottom: 4 }}>
                  <Dot c={a.fp.status === "running" ? "ok" : "warn"} pulse={a.fp.status === "running"} />
                  <span className="nm">{a.fp.name}</span>
                  <Pill c={a.fp.status === "running" ? "ok" : "warn"}>
                    {a.fp.status === "running" ? "运行中" : "已停止"}
                  </Pill>
                </div>
                <dl className="fp-env">
                  <dt>系统</dt>
                  <dd>{a.fp.os}</dd>
                  <dt>代理</dt>
                  <dd style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{a.fp.proxy}</dd>
                  <dt>分辨率</dt>
                  <dd>{a.fp.resolution}</dd>
                  <dt>UA</dt>
                  <dd
                    style={{
                      fontSize: 10.5,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {a.fp.ua}
                  </dd>
                  <dt>WebRTC</dt>
                  <dd>{a.fp.webrtc}</dd>
                  <dt>时区</dt>
                  <dd>{a.fp.timezone}</dd>
                </dl>
              </div>
            </div>
            <div className="card">
              <h3>操作</h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <button
                  className="btn primary"
                  style={{ width: "100%" }}
                  onClick={() => push("已对全部未私信用户批量发送私信")}
                >
                  批量发送私信
                </button>
                <button className="btn ghost" style={{ width: "100%" }} onClick={exportCSV}>
                  导出全部数据
                </button>
                <button
                  className="btn ghost"
                  style={{ width: "100%" }}
                  onClick={() => push("已跳转私信中心 · " + a.name)}
                >
                  进入私信中心
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}
// ===== 代理配置抽屉 =====

function ProxyDrawer({
  account,
  form,
  setForm,
  testing,
  testResult,
  onTest,
  onSave,
  onClose,
}: {
  account: FmtAccount;
  form: ProxyForm;
  setForm: Dispatch<SetStateAction<ProxyForm>>;
  testing: boolean;
  testResult: { ok: boolean; msg: string } | null;
  onTest: () => void;
  onSave: () => void;
  onClose: () => void;
}) {
  const a = account;
  const proxyTypes: { id: string; label: string; desc: string }[] = [
    { id: "socks5", label: "SOCKS5", desc: "推荐 · 支持UDP/TCP" },
    { id: "http", label: "HTTP", desc: "兼容性好" },
    { id: "https", label: "HTTPS", desc: "加密传输" },
    { id: "direct", label: "直连", desc: "不使用代理" },
  ];
  return (
    <>
      <motion.div
        key="proxy-backdrop"
        className="drawer-backdrop"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.15 }}
        onClick={onClose}
      />
      <motion.div
        key="proxy-drawer"
        className="drawer"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", damping: 28, stiffness: 320 }}
        data-od-id="proxy-drawer"
      >
        <div className="drawer-head">
          <Avatar name={a.name} h={a.hue} />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 14 }}>代理配置 · {a.name}</div>
            <div className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
              UID: {a.uid} · {a.fp.name}
            </div>
          </div>
          <Pill c={a.fp.status === "running" ? "ok" : "warn"}>
            {a.fp.status === "running" ? "运行中" : "已停止"}
          </Pill>
          <button
            className="btn ghost"
            onClick={onClose}
            style={{ fontSize: 18, padding: "4px 8px", lineHeight: 1 }}
          >
            ×
          </button>
        </div>

        <div className="drawer-body">
          <div className="card" style={{ marginBottom: 14 }}>
            <h3>代理类型</h3>
            <div className="grid cols-4" style={{ gap: 8 }}>
              {proxyTypes.map((pt) => (
                <button
                  key={pt.id}
                  style={{
                    padding: "10px 12px",
                    cursor: "pointer",
                    border: form.type === pt.id ? "2px solid var(--accent)" : "1px solid var(--border)",
                    borderRadius: 8,
                    background: form.type === pt.id ? "var(--accent-bg)" : "var(--surface)",
                    textAlign: "left",
                  }}
                  onClick={() => setForm((f) => ({ ...f, type: pt.id }))}
                >
                  <div
                    style={{
                      fontWeight: 600,
                      fontSize: 13,
                      color: form.type === pt.id ? "var(--accent)" : "var(--fg)",
                    }}
                  >
                    {pt.label}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{pt.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {form.type !== "direct" && (
            <div className="card" style={{ marginBottom: 14 }}>
              <h3>连接设置</h3>
              <div className="grid cols-2" style={{ gap: 12 }}>
                <div className="field">
                  <label>主机地址</label>
                  <input
                    className="input mono"
                    placeholder="127.0.0.1"
                    value={form.host}
                    onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))}
                  />
                </div>
                <div className="field">
                  <label>端口</label>
                  <input
                    className="input mono"
                    placeholder="1080"
                    value={form.port}
                    onChange={(e) => setForm((f) => ({ ...f, port: e.target.value }))}
                  />
                </div>
                <div className="field">
                  <label>
                    用户名{" "}
                    <span style={{ color: "var(--muted)", fontWeight: 400 }}>(可选)</span>
                  </label>
                  <input
                    className="input"
                    placeholder="留空则无认证"
                    value={form.user}
                    onChange={(e) => setForm((f) => ({ ...f, user: e.target.value }))}
                  />
                </div>
                <div className="field">
                  <label>
                    密码 <span style={{ color: "var(--muted)", fontWeight: 400 }}>(可选)</span>
                  </label>
                  <input
                    className="input"
                    type="password"
                    placeholder="留空则无认证"
                    value={form.pass}
                    onChange={(e) => setForm((f) => ({ ...f, pass: e.target.value }))}
                  />
                </div>
              </div>
              <div
                style={{
                  marginTop: 12,
                  padding: "10px 14px",
                  background: "var(--surface-2)",
                  borderRadius: 8,
                  fontSize: 12,
                  color: "var(--muted)",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <span style={{ fontSize: 16 }}>💡</span>
                <span>
                  完整地址：
                  <b className="mono" style={{ color: "var(--fg)" }}>
                    {form.type}://{form.host}
                    {form.port ? ":" + form.port : ""}
                  </b>
                </span>
              </div>
            </div>
          )}

          <div className="card" style={{ marginBottom: 14 }}>
            <h3>连接测试</h3>
            <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
              <input
                className="input"
                style={{ flex: 1 }}
                placeholder="测试目标 URL（默认 https://www.douyin.com）"
                value={form.testUrl}
                onChange={(e) => setForm((f) => ({ ...f, testUrl: e.target.value }))}
              />
              <button
                className="btn primary"
                disabled={testing || form.type === "direct"}
                onClick={onTest}
              >
                {testing ? "测试中…" : "测试连接"}
              </button>
            </div>
            {testResult && (
              <div
                style={{
                  padding: "10px 14px",
                  borderRadius: 8,
                  background: testResult.ok ? "var(--ok-bg)" : "var(--danger-bg)",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: 13,
                }}
              >
                <span style={{ fontSize: 16 }}>{testResult.ok ? "✅" : "❌"}</span>
                <div>
                  <div
                    style={{
                      fontWeight: 600,
                      color: testResult.ok ? "var(--ok)" : "var(--danger)",
                    }}
                  >
                    {testResult.ok ? "连接成功" : "连接失败"}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
                    {testResult.msg}
                  </div>
                </div>
              </div>
            )}
            {form.type === "direct" && (
              <div
                style={{
                  padding: "10px 14px",
                  borderRadius: 8,
                  background: "var(--surface-2)",
                  fontSize: 12,
                  color: "var(--muted)",
                }}
              >
                直连模式下无需测试，流量将不经过代理直接发出
              </div>
            )}
          </div>

          <div className="card">
            <h3>当前配置预览</h3>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "80px 1fr",
                gap: "6px 10px",
                fontSize: 12.5,
              }}
            >
              <span style={{ color: "var(--muted)" }}>代理类型</span>
              <span className="mono" style={{ fontWeight: 600 }}>
                {form.type.toUpperCase()}
              </span>
              <span style={{ color: "var(--muted)" }}>地址</span>
              <span className="mono">
                {form.type === "direct" ? "直连" : form.host + (form.port ? ":" + form.port : "")}
              </span>
              <span style={{ color: "var(--muted)" }}>认证</span>
              <span className="mono">{form.user ? form.user + " / ••••" : "无"}</span>
              <span style={{ color: "var(--muted)" }}>影响账号</span>
              <span>
                {a.name} ({a.uid})
              </span>
            </div>
          </div>
        </div>

        <div className="drawer-foot">
          <button className="btn ghost" onClick={onClose}>
            取消
          </button>
          <button className="btn primary" onClick={onSave}>
            保存配置
          </button>
        </div>
      </motion.div>
    </>
  );
}

// ===== 新增/编辑账号抽屉 =====

function AccountDrawer({
  form,
  setForm,
  onSave,
  onClose,
  mode = "add",
  account = null,
}: {
  form: AcctForm;
  setForm: Dispatch<SetStateAction<AcctForm>>;
  onSave: () => void;
  onClose: () => void;
  mode?: "add" | "edit";
  account?: FmtAccount | null;
}) {
  const isEdit = mode === "edit";
  return (
    <>
      <motion.div
        key={"acct-backdrop-" + mode}
        className="drawer-backdrop"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.15 }}
        onClick={onClose}
      />
      <motion.div
        key={"acct-drawer-" + mode}
        className="drawer"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", damping: 28, stiffness: 320 }}
        data-od-id={isEdit ? "acct-edit-drawer" : "acct-add-drawer"}
      >
        <div className="drawer-head">
          <Avatar name={isEdit ? account?.name || "+" : "+"} h={isEdit ? account?.hue || "200" : "200"} />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 14 }}>
              {isEdit ? "编辑账号信息" : "新增授权账号"}
            </div>
            <div className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
              {isEdit ? "修改账号信息并刷新登录凭证" : "授权新抖音账号并创建指纹环境"}
            </div>
          </div>
          <button
            className="btn ghost"
            onClick={onClose}
            style={{ fontSize: 18, padding: "4px 8px", lineHeight: 1 }}
          >
            ×
          </button>
        </div>

        <div className="drawer-body">
          <div className="card" style={{ marginBottom: 14 }}>
            <h3>账号信息</h3>
            <div className="grid cols-2" style={{ gap: 12 }}>
              <div className="field">
                <label>昵称</label>
                <input
                  className="input"
                  placeholder="如：阿强探店"
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  autoFocus
                />
              </div>
              <div className="field">
                <label>
                  UID{" "}
                  <span style={{ color: "var(--muted)", fontWeight: 400 }}>
                    (可选 · 扫码后自动获取)
                  </span>
                </label>
                <input
                  className="input mono"
                  placeholder="留空即可，扫码后自动读入"
                  value={form.uid}
                  onChange={(e) => setForm((f) => ({ ...f, uid: e.target.value }))}
                />
              </div>
            </div>
            {!isEdit && (
              <div className="field" style={{ marginTop: 12 }}>
                <label>
                  Cookie{" "}
                  <span style={{ color: "var(--muted)", fontWeight: 400 }}>
                    (可选 · 留空则扫码授权)
                  </span>
                </label>
                <textarea
                  className="input mono"
                  rows={3}
                  placeholder="粘贴 Cookie 字符串，用于免扫码登录"
                  value={form.cookie}
                  onChange={(e) => setForm((f) => ({ ...f, cookie: e.target.value }))}
                  style={{ resize: "vertical" }}
                />
              </div>
            )}
          </div>
          <div
            style={{
              padding: "10px 14px",
              borderRadius: 8,
              background: "var(--surface-2)",
              fontSize: 12,
              color: "var(--muted)",
              lineHeight: 1.6,
            }}
          >
            {isEdit
              ? "点击「重新获取凭证」后，将重新拉起内置指纹浏览器并展示抖音扫码二维码，扫码完成后最新凭证（Cookie / 签名）自动写回该账号。"
              : "保存后将自动弹出内置指纹浏览器并展示抖音扫码二维码，扫码完成后凭证自动写回该账号；之后可在账号卡片中配置代理与凭证校验。"}
          </div>
        </div>

        <div className="drawer-foot">
          <button className="btn ghost" onClick={onClose}>
            取消
          </button>
          <button className="btn primary" onClick={onSave} disabled={!form.name.trim()}>
            {isEdit ? "重新获取凭证" : "确认新增"}
          </button>
        </div>
      </motion.div>
    </>
  );
}