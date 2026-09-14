/**
 * 账号管理页（重设计版 · 对标 better-douyin 设计体系）
 *
 * 迁移自: DY_Spider_base/web/pages/accounts.js（原版 389 处 createElement，最大文件）
 * 原版职责: 增删、扫码、角色（监测/发送）、引擎校验、守护启停
 * 迁移要点:
 *   - React.createElement → JSX（389 处全转）
 *   - getAccounts 8s 轮询 setInterval → React Query useQuery
 *   - .catch(() => {}) 静默吞错 → 带 push 反馈（修复原版 bug）
 *   - 扫码状态查询 → props.api.scanStatus(name) 轮询（替代旧版间接推断）
 *   - "全部校验"黑屏 bug → 数据始终经 mapAcct 映射，类型守卫消除空字段访问
 *   - 守护进程启动 → sidecar.ts 的 startBrowserDaemonReady/startRecvDaemon（不使用 props.api）
 *     startBrowserDaemonReady 会等待 BCC /status 就绪（alive=true）后再置真，避免 alive=false 期间调用方撞“容器未启动”
 *
 * ## 本次改动（重设计 · 只改呈现层，业务逻辑零改动）
 * - 旧 global.css 类（`.card`/`.stat`/`.acct-card`/`.acct-row`/`.acct-section`/`.acct-log-grid`/
 *   `.fp-card`/`.fp-env`/`.batch-bar`/`.fpill`/`.count-line`/`.field`/`.drawer*`/`.overlay*`/
 *   `.nav`/`.demo-tag`/`.comment-table`…）与内联 `style={{…}}`
 *   → `components/page/kit`（Section/Stat/StatRow/KeyValue/Tone/Blank/SegmentedTabs/Toolbar/
 *     FormField）+ `components/ui/*`（Button/Badge/Card/Input/Textarea/Select/Skeleton/StatusDot）
 *   + Tailwind 任意值类引用 `tokens.css` 令牌（深浅主题自动生效）
 * - 查阅模式表格：旧 `.comment-table` 裸 `<th>/<td>` → 具名 `Th`/`Td`（与 tasks.tsx 同规格）
 * - 抽屉：旧 `.drawer-backdrop`/`.drawer` → 固定定位 + `bg-black/55 backdrop-blur-sm`（crawl.tsx 范式）
 * - 图标补 lucide-react（替代 💡/✅/❌/📭/▸/▾/‹/× 等纯装饰字形）
 * - 保留的原始约束：mapAcct 字段映射、enginePill 等级→颜色映射、扫码状态机与文案、
 *   UID/凭证判据、代理字段语义与保存调用、所有 queryKey 与轮询间隔、乐观更新与 refetch 时机
 */
import {
  Fragment, useEffect, useMemo, useState,
  type Dispatch, type ReactNode, type SetStateAction,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import {
  Plus, Layers, RefreshCw, Trash2, ExternalLink, Pencil, Eye, X,
  SlidersHorizontal, AlertTriangle, Gauge, Lightbulb, CheckCircle2,
  XCircle, Inbox, ChevronRight, ChevronDown, ArrowLeft, Download,
} from "lucide-react";
import { PageProps } from "../api/client";
import { openExternal } from "../utils/openExternal";
import { Avatar, TABS, hue, tick } from "../components/ui";
import { stopBrowserDaemon, stopRecvDaemon } from "../api/sidecar";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Input, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusDot } from "@/components/ui/status-dot";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import {
  Section, Stat, StatRow, KeyValue, Tone, Blank, SegmentedTabs,
  Toolbar, FormField,
} from "@/components/page/kit";
import { cn } from "@/lib/utils";

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
  lastRun?: LastRun;
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
  isValid: boolean;
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
  verify?: { wp?: EngineInfo; dm?: EngineInfo; uid?: string; auto_fix_triggered?: boolean };
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
    isValid,
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
    lastRun: a.lastRun || {
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

/* ── 呈现层具名单元（避免每处重复 className；表格单元与 tasks.tsx 同规格） ── */

/** 表头单元（查阅模式表格用）。 */
function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th
      className={cn(
        "whitespace-nowrap border-b border-[var(--color-border)] px-3 py-2 text-left",
        "text-[0.7rem] font-semibold tracking-[0.03em] text-[var(--color-text-secondary)]",
        className
      )}
    >
      {children}
    </th>
  );
}

/** 表格单元（查阅模式表格用）。 */
function Td({
  children,
  mono,
  muted,
  className,
  colSpan,
}: {
  children?: ReactNode;
  mono?: boolean;
  muted?: boolean;
  className?: string;
  colSpan?: number;
}) {
  return (
    <td
      colSpan={colSpan}
      className={cn(
        "border-b border-[var(--color-border)] px-3 py-2 align-middle",
        "text-[0.78rem] text-[var(--color-text)]",
        mono && "font-mono tabular-nums",
        muted && "text-[var(--color-text-muted)]",
        className
      )}
    >
      {children}
    </td>
  );
}

/** 顶部统计卡（旧 `.card.stat`；保留 data-od-id 锚点）。 */
function StatCard({
  anchor,
  label,
  value,
  unit,
}: {
  anchor: string;
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
}) {
  return (
    <Card className="p-4" data-od-id={anchor}>
      <Stat label={label} value={value} unit={unit} />
    </Card>
  );
}

/** 区块小标题（旧 `.acct-section h4` / `.card h3`）。 */
function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <h4 className="mb-2.5 text-[0.72rem] uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
      {children}
    </h4>
  );
}

/** 抽屉/弹层小标题（旧 `<h3>` 在 card 内）。 */
function PanelTitle({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-2.5 text-[0.86rem] font-semibold tracking-tight text-[var(--color-text)]">
      {children}
    </h3>
  );
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

  // 账号列表（读取 App 常驻轮询的共享缓存；操作后自行 refetch）
  const { data: rawAccounts, isLoading, refetch } = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.getAccounts()) as RawAccount[],
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
    // 启动（等待 BCC /status 就绪后再置真，避免 alive=false 期间调用方撞“容器未启动”）
    push("正在启动凭证守护（BCC），等待浏览器就绪…");
    api.addLog("INFO", `正在启动凭证守护 · ${a.name}（等待 BCC 就绪）`).catch(() => {});
    // 会员体系（v0.37.0）：改走 backend /ensure-bcc（backend 进程内 spawn 自带
    // DY_MEMBER/DY_MEMBER_KEY 环境变量；Rust 直 spawn 不带会员环境，BCC 无法解密凭证）
    fetch(`http://127.0.0.1:8000/api/accounts/${encodeURIComponent(a.name)}/ensure-bcc`, {
      method: "POST",
      headers: { "X-Member-Token": (window.localStorage.getItem("dy_member_token") || "") },
    })
      .then((r) => r.json())
      .then((st) => {
        if (!st.ok) throw new Error(st.msg || "BCC 启动失败");
      })
      .then(() => {
        qc.setQueryData<RawAccount[]>(["accounts"], (old) =>
          (old || []).map((x) => (x.name === a.name ? { ...x, browserDaemonAlive: true } : x)),
        );
        push("凭证守护已启动");
        api.addLog("SUCCESS", `凭证守护已启动 · ${a.name}`).catch(() => {});
      })
      .catch((e: unknown) => {
        // BCC sidecar 已 spawn 但 /status 未就绪（指纹内核冷启动超时等）——不置真
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
    // 会员体系（v0.37.0）：改走 backend /ensure-recv（进程内 spawn 自带会员环境）
    fetch(`http://127.0.0.1:8000/api/accounts/${encodeURIComponent(a.name)}/ensure-recv`, {
      method: "POST",
      headers: { "X-Member-Token": (window.localStorage.getItem("dy_member_token") || "") },
    })
      .then((r) => r.json())
      .then((st) => {
        if (!st.ok) throw new Error(st.msg || "私信守护启动失败");
      })
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
                detail: "正在检测私信守护与凭证守护的有效性（不拉取会话列表）",
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
          // 2026-08-31：校验可能触发浏览器重捕（耗时数分钟），提示停留 12s 避免错过
          push(
            "引擎校验完成 · " + a.name + " · wp: " + (v.wp && v.wp.label) +
              " · dm: " + (v.dm && v.dm.label),
            12000,
          );
          api.addLog("SUCCESS", `引擎校验完成 · ${a.name} · wp: ${v.wp && v.wp.label} · dm: ${v.dm && v.dm.label}`).catch(() => {});
          // 方案 A：校验发现凭证失效时后端已自动唤醒指纹浏览器重捕，提示用户完成授权
          if (v.auto_fix_triggered) {
            push("⚠️ 凭证失效，已自动拉起指纹浏览器重新捕获 · " + a.name + " · 请在弹出的窗口中完成授权/加载页面");
            api.addLog("WARN", `凭证失效，已自动唤醒指纹浏览器重捕 · ${a.name}`).catch(() => {});
          }
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
    <PageContainer>
      <PageHeader
        title="账号管理"
        description="授权账号凭证监控 · 运行日志 · 指纹浏览器环境"
        actions={
          <>
            <Button variant="ghost" data-od-id="acct-add" onClick={() => setAddOpen(true)}>
              <Plus className="h-3.5 w-3.5" />新增账号
            </Button>
            <Button
              variant="ghost"
              data-od-id="acct-batch"
              onClick={() => {
                setBatchMode((b) => !b);
                setBatchSel(new Set());
              }}
            >
              <Layers className="h-3.5 w-3.5" />{batchMode ? "退出批量" : "批量管理"}
            </Button>
            <Button variant="ghost" onClick={checkAll}>
              <RefreshCw className="h-3.5 w-3.5" />全部校验
            </Button>
            {!props.ready && <Badge variant="outline">未连接</Badge>}
          </>
        }
      />

      <StatRow cols={4} className="mb-3.5">
        <StatCard anchor="acct-total" label="已授权账号" value={shownAccounts.length} unit="个" />
        <StatCard
          anchor="acct-valid"
          label="有效凭证"
          value={<span className="text-[var(--color-success)]">{validCnt}</span>}
          unit="个"
        />
        <StatCard
          anchor="acct-expired"
          label="过期 / 异常"
          value={
            <span className="text-[var(--color-danger)]">{shownAccounts.length - validCnt}</span>
          }
          unit="个"
        />
        <StatCard
          anchor="acct-fp"
          label="指纹浏览器在线"
          value={<span className="text-[var(--color-accent)]">{fpRunning}</span>}
          unit={"/ " + fpSet.length}
        />
      </StatRow>

      <div className="flex flex-col gap-3.5" data-od-id="account-list">
        {batchMode && (
          <Card
            data-od-id="acct-batch-bar"
            className={cn(
              "flex flex-wrap items-center gap-3 px-3.5 py-2.5",
              "border-[var(--color-accent)] bg-[var(--color-accent-soft)]"
            )}
          >
            <Button variant="secondary" size="sm" onClick={batchAll}>
              {batchSel.size === shownAccounts.length ? "取消全选" : "全选"}
            </Button>
            <span className="text-[0.8rem] text-[var(--color-text-secondary)]">
              已选{" "}
              <b className="font-mono text-[var(--color-accent)]">{batchSel.size}</b>{" "}
              / {shownAccounts.length} 个账号
            </span>
            <div className="flex-1" />
            <Button variant="secondary" size="sm" onClick={batchCheck}>
              批量校验
            </Button>
            <Button variant="secondary" size="sm" onClick={batchExport}>
              批量导出
            </Button>
            <Button variant="danger-outline" size="sm" onClick={batchDelete}>
              <Trash2 className="h-3 w-3" />删除所选
            </Button>
          </Card>
        )}

        {showSkeleton
          ? Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={"sk" + i} className="h-[220px] w-full rounded-[var(--radius-md)]" />
            ))
          : shownAccounts.map((a) => (
              <Card
                key={a.id}
                data-od-id={"acct-" + a.id}
                onClick={() => selectAsMonitor(a)}
                className={cn(
                  // 边框颜色反映账号有效/失效：有效(wp ok)绿色、失效红色；监测账号额外描一圈光环
                  "flex cursor-pointer flex-col overflow-hidden border-2",
                  a.isValid
                    ? "border-[var(--color-success)]"
                    : "border-[var(--color-danger)]",
                  a.isMonitor
                    ? "shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-info)_35%,transparent)]"
                    : a.isValid
                      ? "shadow-[0_0_0_2px_color-mix(in_srgb,var(--color-success)_18%,transparent)]"
                      : "shadow-[0_0_0_2px_color-mix(in_srgb,var(--color-danger)_18%,transparent)]"
                )}
              >
                <div className="flex flex-wrap items-center gap-3 border-b border-[var(--color-border)] p-4">
                  {batchMode && (
                    <input
                      type="checkbox"
                      className="h-4 w-4 shrink-0 cursor-pointer accent-[var(--color-accent)]"
                      checked={batchSel.has(a.id)}
                      onChange={() => toggleBatch(a.id)}
                      aria-label={"选择 " + a.name}
                    />
                  )}
                  <Avatar name={a.name} h={a.hue} lg />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-[0.95rem] font-semibold text-[var(--color-text)]">
                      {a.name}
                      <Tone tone={a.tokenValid ? "ok" : a.lvl === "nosign" ? "warn" : "danger"}>
                        {a.lvlLabel || (a.tokenValid ? "凭证有效" : "凭证过期")}
                      </Tone>
                    </div>
                    <div className="mt-0.5 font-mono text-[0.75rem] text-[var(--color-text-muted)]">
                      UID {a.uid} · 上次校验 {a.lastCheck}
                    </div>
                  </div>
                  <Toolbar className="shrink-0">
                    <Button
                      size="sm"
                      variant={manageOpen === a.id ? "default" : "ghost"}
                      onClick={(e) => {
                        e.stopPropagation();
                        setManageOpen(manageOpen === a.id ? null : a.id);
                      }}
                      title="展开账号管理面板（指纹浏览器 / 守护 / 代理 / 凭证）"
                    >
                      <SlidersHorizontal className="h-3.5 w-3.5" />
                      {manageOpen === a.id ? "收起管理" : "管理"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        openEdit(a);
                      }}
                      title="编辑账号信息并刷新登录凭证"
                    >
                      <Pencil className="h-3.5 w-3.5" />刷新凭证
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        setReviewAccount(a);
                      }}
                    >
                      <Eye className="h-3.5 w-3.5" />查阅模式
                    </Button>
                  </Toolbar>
                </div>

                {/* 管理面板：原卡片四板块（守护服务 / 上次运行日志 / 引擎校验 / 关联指纹浏览器） */}
                  {manageOpen === a.id && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      transition={{ duration: 0.2 }}
                      className="overflow-hidden"
                    >
                      <div
                        className="grid min-w-0 grid-cols-1 border-[var(--color-border)] lg:grid-cols-2"
                        onClick={(e) => e.stopPropagation()}
                      >
                  {/* 守护服务 */}
                  <div className="border-b border-[var(--color-border)] p-4 lg:col-start-1 lg:row-start-1 lg:border-r">
                    <SectionLabel>守护服务</SectionLabel>
                    <KeyValue
                      cols={1}
                      items={[
                        {
                          k: "凭证守护",
                          v: (
                            <span className="flex flex-wrap items-center gap-2">
                              <Tone tone={a.browserDaemonAlive ? "ok" : "mute"}>
                                {a.browserDaemonAlive ? "运行中" : "未运行"}
                              </Tone>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={(e) => { e.stopPropagation(); toggleBrowserDaemon(a); }}
                              >
                                {a.browserDaemonAlive ? "停止" : "启动"}
                              </Button>
                              <span className="text-[0.68rem] text-[var(--color-text-muted)]">
                                :{a.browserDaemonPort}
                              </span>
                            </span>
                          ),
                        },
                        {
                          k: "私信守护",
                          v: (
                            <span className="flex flex-wrap items-center gap-2">
                              <Tone tone={a.recvDaemonAlive ? "ok" : "mute"}>
                                {a.recvDaemonAlive ? "运行中" : "未运行"}
                              </Tone>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={(e) => { e.stopPropagation(); toggleRecvDaemon(a); }}
                              >
                                {a.recvDaemonAlive ? "停止" : "启动"}
                              </Button>
                              <span className="text-[0.68rem] text-[var(--color-text-muted)]">
                                :{a.recvDaemonPort}
                              </span>
                            </span>
                          ),
                        },
                      ]}
                    />
                  </div>

                  {/* 上次运行记录 */}
                  <div className="p-4 lg:col-start-1 lg:row-start-2 lg:border-r">
                    <SectionLabel>上次运行记录</SectionLabel>
                    <KeyValue
                      cols={1}
                      items={[
                        {
                          k: "直播间",
                          v: (
                            <span className="flex flex-wrap items-center gap-2">
                              <span
                                className={
                                  a.lastRun.room === "—"
                                    ? "text-[var(--color-text-muted)]"
                                    : "text-[var(--color-text)]"
                                }
                              >
                                {a.lastRun.room}
                              </span>
                              {a.lastRun.roomUrl && (
                                <a
                                  href={a.lastRun.roomUrl}
                                  rel="noopener noreferrer"
                                  className="inline-flex h-6 shrink-0 items-center gap-1
                                             rounded-[8px] border border-[var(--color-border)]
                                             px-2 text-[0.68rem] leading-none
                                             text-[var(--color-text-secondary)]
                                             transition-colors duration-[var(--duration-fast)]
                                             hover:bg-[var(--color-surface-raised)]
                                             hover:text-[var(--color-text)]"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    // 2026-09-01：WebView 里 <a target="_blank"> 静默失败，
                                    // 必须走 shell.open()（见 utils/openExternal.ts）
                                    void openExternal(a.lastRun.roomUrl || "");
                                    push("已打开直播间：" + a.lastRun.room);
                                  }}
                                >
                                  <ExternalLink className="h-3 w-3" />前往直播间
                                </a>
                              )}
                            </span>
                          ),
                        },
                        { k: "运行时间", v: a.lastRun.time },
                        { k: "运行时长", v: a.lastRun.duration },
                        { k: "累计运行", v: a.lastRun.totalRuns + " 次" },
                      ]}
                    />
                    <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-5">
                      <div className="rounded-[var(--radius-sm)] bg-[var(--color-background)] px-2.5 py-2">
                        <div className="mb-0.5 text-[0.68rem] text-[var(--color-text-muted)]">
                          捕获评论
                        </div>
                        <div className="font-mono text-[0.94rem] font-semibold text-[var(--color-text)]">
                          {a.lastRun.comments.toLocaleString()}
                        </div>
                      </div>
                      <div className="rounded-[var(--radius-sm)] bg-[var(--color-background)] px-2.5 py-2">
                        <div className="mb-0.5 text-[0.68rem] text-[var(--color-text-muted)]">
                          发送私信
                        </div>
                        <div className="font-mono text-[0.94rem] font-semibold text-[var(--color-accent)]">
                          {a.lastRun.dmSent}
                        </div>
                      </div>
                      <div className="rounded-[var(--radius-sm)] bg-[var(--color-background)] px-2.5 py-2">
                        <div className="mb-0.5 text-[0.68rem] text-[var(--color-text-muted)]">
                          下播私信
                        </div>
                        <div className="font-mono text-[0.94rem] font-semibold text-[var(--color-warning)]">
                          {a.lastRun.dmAfterLive}
                        </div>
                      </div>
                      <div className="rounded-[var(--radius-sm)] bg-[var(--color-background)] px-2.5 py-2">
                        <div className="mb-0.5 text-[0.68rem] text-[var(--color-text-muted)]">
                          成功
                        </div>
                        <div className="font-mono text-[0.94rem] font-semibold text-[var(--color-success)]">
                          {a.lastRun.dmSuccess}
                        </div>
                      </div>
                      <div className="rounded-[var(--radius-sm)] bg-[var(--color-background)] px-2.5 py-2">
                        <div className="mb-0.5 text-[0.68rem] text-[var(--color-text-muted)]">
                          失败
                        </div>
                        <div className="font-mono text-[0.94rem] font-semibold text-[var(--color-danger)]">
                          {a.lastRun.dmFail}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 引擎校验 */}
                  <div className="border-b border-[var(--color-border)] p-4 lg:col-start-2 lg:row-start-1">
                    <SectionLabel>引擎校验</SectionLabel>
                    <div className="flex items-stretch gap-2.5">
                      <div className="flex flex-1 flex-col gap-2.5">
                        <div className="rounded-[var(--radius-sm)] bg-[var(--color-background)] px-3 py-2.5">
                          <div className="flex items-center gap-2">
                            <span className="text-[0.8rem] font-semibold text-[var(--color-text)]">
                              wp 引擎
                            </span>
                            <Tone tone={enginePill(a.wpEngine.level)}>
                              {a.wpEngine.level === "stopped" ? "未运行" : a.wpEngine.label}
                            </Tone>
                          </div>
                        </div>
                        {(a.wpEngine.level === "fail" || a.dmEngine.level === "fail") && (
                          <Button
                            variant="danger"
                            size="sm"
                            className="mt-2"
                            onClick={(e) => { e.stopPropagation(); handleScanVerify(a); }}
                          >
                            <AlertTriangle className="h-3.5 w-3.5" />立即处理验证
                          </Button>
                        )}
                        <div className="rounded-[var(--radius-sm)] bg-[var(--color-background)] px-3 py-2.5">
                          <div className="flex items-center gap-2">
                            <span className="text-[0.8rem] font-semibold text-[var(--color-text)]">
                              私信引擎
                            </span>
                            <Tone tone={enginePill(a.dmEngine.level)}>{a.dmEngine.label}</Tone>
                          </div>
                        </div>
                      </div>
                      <div className="flex flex-col items-stretch justify-center">
                        <Button
                          variant="secondary"
                          size="sm"
                          className="min-w-24"
                          onClick={(e) => { e.stopPropagation(); runCheck(a); }}
                        >
                          <Gauge className="h-3.5 w-3.5" />引擎校验
                        </Button>
                      </div>
                    </div>
                  </div>

                  {/* 关联指纹浏览器 */}
                  <div className="p-4 lg:col-start-2 lg:row-start-2">
                    <SectionLabel>关联指纹浏览器</SectionLabel>
                    <div
                      className="cursor-pointer rounded-[var(--radius-sm)] border
                                 border-[var(--color-border)] bg-[var(--color-surface-raised)] p-4"
                      onDoubleClick={(e) => {
                        e.stopPropagation();
                        onOpenFingerprint(a.name);
                      }}
                      title="双击打开指纹浏览器"
                    >
                      <div className="flex flex-wrap items-center gap-2.5">
                        <StatusDot
                          tone={a.fp.status === "running" ? "ok" : "warn"}
                          pulse={a.fp.status === "running"}
                        />
                        <span className="text-[0.82rem] font-medium text-[var(--color-text)]">
                          {a.fp.name}
                        </span>
                        <Tone tone={a.fp.status === "running" ? "ok" : "warn"}>
                          {a.fp.status === "running" ? "运行中" : "已停止"}
                        </Tone>
                        <div className="flex-1" />
                        <Button
                          variant="ghost"
                          size="sm"
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
                        </Button>
                      </div>
                      <KeyValue
                        cols={2}
                        className="mt-2.5"
                        items={[
                          { k: "系统", v: a.fp.os },
                          {
                            k: "代理",
                            mono: true,
                            v: <span className="text-[0.72rem]">{a.fp.proxy}</span>,
                          },
                          { k: "分辨率", v: a.fp.resolution },
                          {
                            k: "UA",
                            v: <span className="block max-w-[180px] truncate">{a.fp.ua}</span>,
                          },
                          { k: "WebRTC", v: a.fp.webrtc },
                          { k: "时区", v: a.fp.timezone },
                        ]}
                      />
                    </div>
                  </div>
                    </div>
                    </motion.div>
                )}
              </Card>
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
                          const p = proxyForm;
                          setProxyTesting(true);
                          setProxyTestResult(null);
                          // 真实探测：三态通用（含直连），返回真实出口 IP + 归属地
                          api
                            .proxyTest(proxyAcct.name, {
                              type: p.type,
                              host: p.host,
                              port: p.port,
                              user: p.user,
                              pass: p.pass,
                            })
                            .then((d) => {
                              if (!d || !d.ok) {
                                setProxyTestResult({
                                  ok: false,
                                  msg: (d && d.error) || "连接测试失败（无法获取出口 IP）",
                                });
                                setProxyTesting(false);
                                return;
                              }
                              const loc = [d.country, d.region, d.city].filter(Boolean).join(" · ");
                              const flag: Record<string, string> = {
                                China: "🇨🇳", "United States": "🇺🇸", Japan: "🇯🇵",
                                "Hong Kong": "🇭🇰", Singapore: "🇸🇬", Taiwan: "🇹🇼",
                              };
                              const fl = flag[d.country] || "🌐";
                              const modeLabel =
                                d.mode === "direct" ? "不走代理" : d.mode === "system" ? "系统代理" : "独立节点";
                              const warn = d.is_proxy ? " ⚠️该IP被标记为代理" : "";
                              setProxyTestResult({
                                ok: true,
                                msg: `出口 IP：${d.ip}\n归属：${fl} ${loc || "未知"}｜${modeLabel}${warn}`
                                  + (d.isp ? `\n运营商：${d.isp}` : ""),
                              });
                              setProxyTesting(false);
                            })
                            .catch((e) => {
                              setProxyTestResult({ ok: false, msg: "测试失败: " + errMsg(e) });
                              setProxyTesting(false);
                            });
                        }}
                        onSave={() => {
                          const p = proxyForm;
                          setProxyTesting(true);
                          api
                            .saveProxy(proxyAcct.name, {
                              type: p.type,
                              host: p.host,
                              port: p.port,
                              user: p.user,
                              pass: p.pass,
                            })
                            .then((d) => {
                              setProxyTesting(false);
                              if (d && d.ok) {
                                push("代理配置已保存 · " + proxyAcct.name + (d.msg ? " · " + d.msg : ""));
                                // 刷新账号列表以反映新的代理状态
                                qc.invalidateQueries({ queryKey: ["accounts"] });
                              } else {
                                push("保存失败: " + ((d && d.msg) || "未知错误"));
                              }
                              setProxyAcct(null);
                            })
                            .catch((e) => {
                              setProxyTesting(false);
                              push("保存失败: " + errMsg(e));
                              setProxyAcct(null);
                            });
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
    </PageContainer>
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
  push: (msg: string, holdMs?: number) => void;
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
      className="fixed inset-0 z-50 flex flex-col overflow-hidden bg-[var(--color-background)]"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      data-od-id="acct-review"
    >
      <header
        className="flex flex-wrap items-center gap-5 border-b border-[var(--color-border)]
                   px-5 py-2.5"
      >
        <div className="flex items-baseline gap-2.5 whitespace-nowrap">
          <span
            aria-hidden="true"
            className="h-[22px] w-[22px] shrink-0 self-center rounded-[6px]
                       bg-[var(--color-accent)]"
          />
          <h1 className="text-[0.94rem] font-semibold tracking-tight text-[var(--color-text)]">
            抖音数据控制台
          </h1>
          <span className="font-mono text-[0.68rem] uppercase tracking-[0.06em]
                           text-[var(--color-text-muted)]">
            Douyin Console
          </span>
        </div>
        <nav className="flex flex-1 flex-wrap gap-0.5" aria-label="主导航">
          {TABS.map(([id, label]) => (
            <button
              key={id}
              data-od-id={"review-tab-" + id}
              className={cn(
                "whitespace-nowrap rounded-[9px] px-3 py-1.5 text-[0.82rem]",
                "transition-colors duration-[var(--duration-fast)]",
                id === "accounts"
                  ? "bg-[var(--color-accent-soft)] font-medium text-[var(--color-accent)]"
                  : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
              )}
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
        <div className="flex items-center gap-2 whitespace-nowrap">
          <Badge variant="outline" className="font-mono">
            <StatusDot tone="ok" pulse />Cookie <b className="font-semibold">有效</b>
          </Badge>
          <Badge variant="outline" className="font-mono">
            <StatusDot tone="ok" pulse />直播 <b className="font-semibold">已连接</b>
          </Badge>
          <Badge variant="accent">查阅模式</Badge>
        </div>
      </header>

      <div
        className="flex flex-wrap items-center gap-3 border-b border-[var(--color-border)]
                   bg-[var(--color-surface)] px-5 py-3.5"
      >
        <Button variant="ghost" onClick={onClose}>
          <ArrowLeft className="h-4 w-4" />返回账号管理
        </Button>
        <Avatar name={a.name} h={a.hue} sm />
        <h2 className="text-[0.94rem] font-semibold text-[var(--color-text)]">
          {a.name} · 查阅模式
        </h2>
        <Tone tone={a.tokenValid ? "ok" : a.lvl === "nosign" ? "warn" : "danger"}>
          {a.lvlLabel || (a.tokenValid ? "凭证有效" : "凭证过期")}
        </Tone>
        <div className="flex-1" />
        <Badge variant="outline">只读 · {a.uid}</Badge>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-10 pt-4.5">
        <StatRow cols={5} className="mb-3.5" data-od-id="review-stats">
          <Card className="p-4">
            <Stat label="捕获评论" value={r.comments.toLocaleString()} unit="条" />
          </Card>
          <Card className="p-4">
            <Stat
              label="发送私信"
              value={<span className="text-[var(--color-accent)]">{r.dmSent}</span>}
              unit="条"
            />
          </Card>
          <Card className="p-4">
            <Stat
              label="下播私信"
              value={<span className="text-[var(--color-warning)]">{r.dmAfterLive}</span>}
              unit="条"
            />
          </Card>
          <Card className="p-4">
            <Stat
              label="成功"
              value={<span className="text-[var(--color-success)]">{r.dmSuccess}</span>}
            />
          </Card>
          <Card className="p-4">
            <Stat
              label="失败"
              value={<span className="text-[var(--color-danger)]">{r.dmFail}</span>}
            />
          </Card>
          <Card className="p-4">
            <Stat
              label="成功率"
              value={
                <span
                  className={
                    r.dmSent > 0 && r.dmSuccess / r.dmSent > 0.9
                      ? "text-[var(--color-success)]"
                      : "text-[var(--color-warning)]"
                  }
                >
                  {r.dmSent > 0 ? Math.round((r.dmSuccess / r.dmSent) * 100) : 0}
                </span>
              }
              unit="%"
            />
          </Card>
        </StatRow>

        <div
          className="grid grid-cols-1 items-start gap-3.5 lg:grid-cols-[1fr_320px]"
          data-od-id="review-content"
        >
          <div>
            <Toolbar className="mb-3">
              <Input
                className="h-8 min-w-[180px] flex-1 text-[0.78rem]"
                placeholder="搜索昵称 / 评论内容 / 私信文案…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              <Select value={st} onValueChange={setSt}>
                <SelectTrigger className="h-8 w-[130px] text-[0.78rem]" aria-label="私信状态筛选">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部状态</SelectItem>
                  <SelectItem value="un">未私信</SelectItem>
                  <SelectItem value="wait">待发送</SelectItem>
                  <SelectItem value="sent">已发送</SelectItem>
                  <SelectItem value="fail">发送失败</SelectItem>
                </SelectContent>
              </Select>
              <Button variant="ghost" size="sm" onClick={() => setAsc((s) => !s)}>
                {asc ? "时间 ↑" : "时间 ↓"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSt("all");
                  setQ("");
                }}
              >
                重置
              </Button>
              <Button onClick={exportCSV}>
                <Download className="h-3.5 w-3.5" />导出 CSV
              </Button>
            </Toolbar>
            <div className="mb-2.5 flex flex-wrap items-center gap-2.5 text-[0.75rem] text-[var(--color-text-muted)]">
              <span>
                共{" "}
                <b className="font-mono font-semibold text-[var(--color-text)]">
                  {filtered.length}
                </b>{" "}
                条
              </span>
              <SegmentedTabs
                value={st}
                onChange={setSt}
                items={(
                  [
                    ["all", "全部", rows.length],
                    ["un", "未私信", cnt("un")],
                    ["wait", "待发送", cnt("wait")],
                    ["sent", "已发送", cnt("sent")],
                    ["fail", "发送失败", cnt("fail")],
                  ] as [string, string, number][]
                ).map(([id, l, c]) => ({
                  value: id,
                  label: (
                    <>
                      {l}
                      <span className="font-mono text-[0.68rem] text-[var(--color-text-muted)]">
                        {c}
                      </span>
                    </>
                  ),
                }))}
              />
            </div>
            <Card className="overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse">
                  <thead>
                    <tr>
                      <Th className="w-[30px]" />
                      <Th>发送时间</Th>
                      <Th>发言人</Th>
                      <Th>评论内容</Th>
                      <Th>私信状态</Th>
                      <Th>私信文案</Th>
                      <Th>私信时间</Th>
                      <Th className="w-[120px] text-right">操作</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.length === 0 && (
                      <tr>
                        <Td colSpan={8} className="py-9 text-center">
                          <Blank>
                            <Inbox className="mb-1.5 h-6 w-6" />
                            暂无评论记录 · 等待直播间捕获后自动入库
                          </Blank>
                        </Td>
                      </tr>
                    )}
                    {filtered.map((row) => (
                      <Fragment key={row.id}>
                        <tr
                          className="transition-colors duration-[var(--duration-fast)]
                                     hover:bg-[var(--color-surface-raised)]"
                        >
                          <Td>
                            {exp === row.id ? (
                              <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                            ) : (
                              <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                            )}
                          </Td>
                          <Td mono className="whitespace-nowrap">{row.time}</Td>
                          <Td>
                            <span className="inline-flex items-center gap-2">
                              <Avatar name={row.name} h={hue(row.name.length)} sm />
                              <span className="whitespace-nowrap font-medium text-[var(--color-text)]">
                                {row.name}
                              </span>
                              {row.lv < 99 && (
                                <span
                                  className="rounded-[4px] border border-[color-mix(in_srgb,var(--color-warning)_40%,transparent)]
                                             px-1 font-mono text-[0.66rem] text-[var(--color-warning)]"
                                >
                                  Lv.{row.lv}
                                </span>
                              )}
                            </span>
                          </Td>
                          <Td className="max-w-[260px]">
                            <span className="block truncate">{row.content}</span>
                          </Td>
                          <Td>
                            {row.dmStatus === "un" ? (
                              <span className="font-mono text-[var(--color-text-muted)]">—</span>
                            ) : (
                              <Tone tone={DM_META[row.dmStatus]?.[1] || "mute"}>
                                {DM_META[row.dmStatus]?.[0] || ""}
                              </Tone>
                            )}
                          </Td>
                          <Td className="max-w-[180px]">
                            <span
                              className={cn(
                                "block truncate",
                                row.dmText
                                  ? "text-[var(--color-text)]"
                                  : "text-[var(--color-text-muted)]"
                              )}
                            >
                              {row.dmText || (
                                <span className="font-mono text-[var(--color-text-muted)]">
                                  未发送
                                </span>
                              )}
                            </span>
                          </Td>
                          <Td mono className="whitespace-nowrap">
                            {row.dmTime || (
                              <span className="font-mono text-[var(--color-text-muted)]">—</span>
                            )}
                          </Td>
                          <Td>
                            <Toolbar className="justify-end gap-1">
                              <Button
                                variant="link"
                                size="sm"
                                onClick={() => setExp(exp === row.id ? null : row.id)}
                              >
                                详情
                              </Button>
                              <Button
                                variant="link"
                                size="sm"
                                onClick={() => {
                                  goMsg?.(row.name, row.content);
                                  push("已跳转私信中心 · " + row.name);
                                }}
                              >
                                发私信
                              </Button>
                            </Toolbar>
                          </Td>
                        </tr>
                        {exp === row.id && (
                          <tr key={row.id + "-d"}>
                            <Td colSpan={8} className="bg-[var(--color-surface-raised)] px-2.5 pb-3.5 pt-1.5">
                              <Card className="mb-2.5 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface-raised)] p-3.5">
                                <h4 className="mb-2 text-[0.75rem] tracking-[0.06em] text-[var(--color-text-muted)]">
                                  评论历史 · {row.name}
                                </h4>
                                <div className="mb-3 flex flex-col gap-1.5">
                                  {rows
                                    .filter((x) => x.name === row.name)
                                    .slice(0, 5)
                                    .map((x, i) => (
                                      <div key={i} className="flex items-baseline gap-2.5 text-[0.78rem]">
                                        <span className="shrink-0 font-mono text-[0.7rem] text-[var(--color-text-muted)]">
                                          {x.time}
                                        </span>
                                        <span>{x.content}</span>
                                      </div>
                                    ))}
                                </div>
                                <h4 className="mb-2 text-[0.75rem] tracking-[0.06em] text-[var(--color-text-muted)]">
                                  私信内容
                                </h4>
                                <div className="text-[0.8rem]">
                                  {row.dmStatus === "un" ? (
                                    <span className="font-mono text-[var(--color-text-muted)]">
                                      尚未对该发言人发送私信
                                    </span>
                                  ) : (
                                    <span>
                                      <Tone tone={DM_META[row.dmStatus]?.[1] || "mute"}>
                                        {DM_META[row.dmStatus]?.[0] || ""}
                                      </Tone>
                                      {"　"}
                                      {row.dmText || "（文案未填写）"}
                                      {row.dmTime ? "　·　" + row.dmTime : ""}
                                    </span>
                                  )}
                                </div>
                              </Card>
                            </Td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          <div className="flex flex-col gap-3.5">
            <Section className="mb-0">
              <PanelTitle>运行信息</PanelTitle>
              <KeyValue
                cols={1}
                items={[
                  {
                    k: "直播间",
                    v: (
                      <span className="text-[var(--color-accent)]">{r.room}</span>
                    ),
                  },
                  { k: "运行时间", v: r.time },
                  { k: "运行时长", v: r.duration },
                  { k: "累计运行", v: r.totalRuns + " 次" },
                ]}
              />
            </Section>
            <Section className="mb-0">
              <PanelTitle>指纹浏览器</PanelTitle>
              <div className="mb-1 flex flex-wrap items-center gap-2.5">
                <StatusDot
                  tone={a.fp.status === "running" ? "ok" : "warn"}
                  pulse={a.fp.status === "running"}
                />
                <span className="text-[0.82rem] font-medium text-[var(--color-text)]">
                  {a.fp.name}
                </span>
                <Tone tone={a.fp.status === "running" ? "ok" : "warn"}>
                  {a.fp.status === "running" ? "运行中" : "已停止"}
                </Tone>
              </div>
              <KeyValue
                cols={1}
                items={[
                  { k: "系统", v: a.fp.os },
                  { k: "代理", mono: true, v: <span className="text-[0.7rem]">{a.fp.proxy}</span> },
                  { k: "分辨率", v: a.fp.resolution },
                  { k: "UA", v: <span className="block truncate text-[0.66rem]">{a.fp.ua}</span> },
                  { k: "WebRTC", v: a.fp.webrtc },
                  { k: "时区", v: a.fp.timezone },
                ]}
              />
            </Section>
            <Section className="mb-0">
              <PanelTitle>操作</PanelTitle>
              <div className="flex flex-col gap-2">
                <Button
                  className="w-full"
                  onClick={() => push("已对全部未私信用户批量发送私信")}
                >
                  批量发送私信
                </Button>
                <Button variant="ghost" className="w-full" onClick={exportCSV}>
                  <Download className="h-3.5 w-3.5" />导出全部数据
                </Button>
                <Button
                  variant="ghost"
                  className="w-full"
                  onClick={() => push("已跳转私信中心 · " + a.name)}
                >
                  进入私信中心
                </Button>
              </div>
            </Section>
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
  // 代理模式（环境门阀）：模式由这里的选择单方面决定，与本机环境无关。
  //   system → 走系统代理；direct → 走本机 IP；socks5/http/https → 走独立节点。
  const proxyTypes: { id: string; label: string; desc: string }[] = [
    { id: "socks5", label: "SOCKS5", desc: "独立节点 · 支持UDP/TCP" },
    { id: "http", label: "HTTP", desc: "独立节点 · 兼容性好" },
    { id: "https", label: "HTTPS", desc: "独立节点 · 加密传输" },
    { id: "system", label: "系统代理", desc: "跟随本机系统代理设置" },
    { id: "direct", label: "不走代理", desc: "走本机 IP · 豁免代理端口" },
  ];
  return (
    <>
      <motion.div
        key="proxy-backdrop"
        className="fixed inset-0 z-[60] bg-black/55 backdrop-blur-sm"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.15 }}
        onClick={onClose}
      />
      <motion.div
        key="proxy-drawer"
        className="fixed bottom-0 right-0 top-0 z-[61] flex w-[480px] max-w-[92vw]
                   flex-col border-l border-[var(--color-border)]
                   bg-[var(--color-background)] shadow-[var(--shadow-lg)]"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", damping: 28, stiffness: 320 }}
        data-od-id="proxy-drawer"
      >
        <div className="flex shrink-0 items-center gap-2.5 border-b border-[var(--color-border)]
                        bg-[var(--color-surface)] px-4.5 py-3.5">
          <Avatar name={a.name} h={a.hue} />
          <div className="flex-1">
            <div className="text-[0.88rem] font-semibold text-[var(--color-text)]">
              代理配置 · {a.name}
            </div>
            <div className="font-mono text-[0.72rem] text-[var(--color-text-muted)]">
              UID: {a.uid} · {a.fp.name}
            </div>
          </div>
          <Tone tone={a.fp.status === "running" ? "ok" : "warn"}>
            {a.fp.status === "running" ? "运行中" : "已停止"}
          </Tone>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4.5 pb-6 pt-4">
          <Section className="mb-3.5">
            <PanelTitle>代理类型</PanelTitle>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              {proxyTypes.map((pt) => (
                <button
                  key={pt.id}
                  className={cn(
                    "cursor-pointer rounded-[var(--radius-sm)] px-3 py-2.5 text-left",
                    "transition-colors duration-[var(--duration-fast)]",
                    form.type === pt.id
                      ? "border-2 border-[var(--color-accent)] bg-[var(--color-accent-soft)]"
                      : "border border-[var(--color-border)] bg-[var(--color-surface)] hover:bg-[var(--color-surface-raised)]"
                  )}
                  onClick={() => setForm((f) => ({ ...f, type: pt.id }))}
                >
                  <div
                    className={cn(
                      "text-[0.8rem] font-semibold",
                      form.type === pt.id
                        ? "text-[var(--color-accent)]"
                        : "text-[var(--color-text)]"
                    )}
                  >
                    {pt.label}
                  </div>
                  <div className="mt-0.5 text-[0.68rem] text-[var(--color-text-muted)]">
                    {pt.desc}
                  </div>
                </button>
              ))}
            </div>
          </Section>

          {form.type !== "direct" && form.type !== "system" && (
            <Section className="mb-3.5">
              <PanelTitle>连接设置</PanelTitle>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <FormField label="主机地址">
                  <Input
                    className="font-mono"
                    placeholder="127.0.0.1"
                    value={form.host}
                    onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))}
                  />
                </FormField>
                <FormField label="端口">
                  <Input
                    className="font-mono"
                    placeholder="1080"
                    value={form.port}
                    onChange={(e) => setForm((f) => ({ ...f, port: e.target.value }))}
                  />
                </FormField>
                <FormField label={<>用户名 <span className="font-normal text-[var(--color-text-muted)]">(可选)</span></>}>
                  <Input
                    placeholder="留空则无认证"
                    value={form.user}
                    onChange={(e) => setForm((f) => ({ ...f, user: e.target.value }))}
                  />
                </FormField>
                <FormField label={<>密码 <span className="font-normal text-[var(--color-text-muted)]">(可选)</span></>}>
                  <Input
                    type="password"
                    placeholder="留空则无认证"
                    value={form.pass}
                    onChange={(e) => setForm((f) => ({ ...f, pass: e.target.value }))}
                  />
                </FormField>
              </div>
              <div
                className="mt-3 flex items-center gap-2 rounded-[var(--radius-sm)]
                           bg-[var(--color-surface-raised)] px-3.5 py-2.5 text-[0.75rem]
                           text-[var(--color-text-muted)]"
              >
                <Lightbulb className="h-4 w-4 shrink-0 text-[var(--color-warning)]" />
                <span>
                  完整地址：
                  <b className="font-mono font-semibold text-[var(--color-text)]">
                    {form.type}://{form.host}
                    {form.port ? ":" + form.port : ""}
                  </b>
                </span>
              </div>
            </Section>
          )}

          <Section className="mb-3.5">
            <PanelTitle>连接测试</PanelTitle>
            <div className="mb-2 text-[0.72rem] text-[var(--color-text-muted)]">
              三种模式均可测试，返回真实出口 IP 与归属地（直连=走本机 IP）
            </div>
            <Toolbar className="mb-3">
              <Input
                className="flex-1"
                placeholder="测试目标 URL（默认 https://www.douyin.com）"
                value={form.testUrl}
                onChange={(e) => setForm((f) => ({ ...f, testUrl: e.target.value }))}
              />
              <Button disabled={testing} onClick={onTest}>
                {testing ? "测试中…" : "测试连接"}
              </Button>
            </Toolbar>
            {testResult && (
              <div
                className={cn(
                  "flex items-center gap-2 rounded-[var(--radius-sm)] px-3.5 py-2.5 text-[0.8rem]",
                  testResult.ok
                    ? "bg-[var(--color-success-soft)]"
                    : "bg-[var(--color-danger-soft)]"
                )}
              >
                {testResult.ok ? (
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-[var(--color-success)]" />
                ) : (
                  <XCircle className="h-4 w-4 shrink-0 text-[var(--color-danger)]" />
                )}
                <div>
                  <div
                    className={cn(
                      "font-semibold",
                      testResult.ok
                        ? "text-[var(--color-success)]"
                        : "text-[var(--color-danger)]"
                    )}
                  >
                    {testResult.ok ? "连接成功" : "连接失败"}
                  </div>
                  <div className="mt-0.5 whitespace-pre-line text-[0.75rem] leading-relaxed
                                  text-[var(--color-text-muted)]">
                    {testResult.msg}
                  </div>
                </div>
              </div>
            )}
            {(form.type === "direct" || form.type === "system") && (
              <div
                className="rounded-[var(--radius-sm)] bg-[var(--color-surface-raised)]
                           px-3.5 py-2.5 text-[0.75rem] text-[var(--color-text-muted)]"
              >
                {form.type === "direct"
                  ? "不走代理：流量直接以本机 IP 发出（豁免代理软件端口），无需测试"
                  : "系统代理：跟随本机系统代理设置，无需填写节点信息"}
              </div>
            )}
          </Section>

          <Section>
            <PanelTitle>当前配置预览</PanelTitle>
            <div
              className="grid grid-cols-[80px_1fr] gap-x-2.5 gap-y-1.5 text-[0.78rem]"
            >
              <span className="text-[var(--color-text-muted)]">代理类型</span>
              <span className="font-mono font-semibold">{form.type.toUpperCase()}</span>
              <span className="text-[var(--color-text-muted)]">地址</span>
              <span className="font-mono">
                {form.type === "direct" ? "直连" : form.host + (form.port ? ":" + form.port : "")}
              </span>
              <span className="text-[var(--color-text-muted)]">认证</span>
              <span className="font-mono">{form.user ? form.user + " / ••••" : "无"}</span>
              <span className="text-[var(--color-text-muted)]">影响账号</span>
              <span>
                {a.name} ({a.uid})
              </span>
            </div>
          </Section>
        </div>

        <div className="flex shrink-0 justify-end gap-2 border-t border-[var(--color-border)]
                        bg-[var(--color-surface)] px-4.5 py-3">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={onSave}>
            保存配置
          </Button>
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
        className="fixed inset-0 z-[60] bg-black/55 backdrop-blur-sm"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.15 }}
        onClick={onClose}
      />
      <motion.div
        key={"acct-drawer-" + mode}
        className="fixed bottom-0 right-0 top-0 z-[61] flex w-[480px] max-w-[92vw]
                   flex-col border-l border-[var(--color-border)]
                   bg-[var(--color-background)] shadow-[var(--shadow-lg)]"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", damping: 28, stiffness: 320 }}
        data-od-id={isEdit ? "acct-edit-drawer" : "acct-add-drawer"}
      >
        <div className="flex shrink-0 items-center gap-2.5 border-b border-[var(--color-border)]
                        bg-[var(--color-surface)] px-4.5 py-3.5">
          <Avatar name={isEdit ? account?.name || "+" : "+"} h={isEdit ? account?.hue || "200" : "200"} />
          <div className="flex-1">
            <div className="text-[0.88rem] font-semibold text-[var(--color-text)]">
              {isEdit ? "编辑账号信息" : "新增授权账号"}
            </div>
            <div className="font-mono text-[0.72rem] text-[var(--color-text-muted)]">
              {isEdit ? "修改账号信息并刷新登录凭证" : "授权新抖音账号并创建指纹环境"}
            </div>
          </div>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4.5 pb-6 pt-4">
          <Section className="mb-3.5">
            <PanelTitle>账号信息</PanelTitle>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <FormField label="昵称">
                <Input
                  placeholder="如：阿强探店"
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  autoFocus
                />
              </FormField>
              <FormField
                label={
                  <>
                    UID{" "}
                    <span className="font-normal text-[var(--color-text-muted)]">
                      (可选 · 扫码后自动获取)
                    </span>
                  </>
                }
              >
                <Input
                  className="font-mono"
                  placeholder="留空即可，扫码后自动读入"
                  value={form.uid}
                  onChange={(e) => setForm((f) => ({ ...f, uid: e.target.value }))}
                />
              </FormField>
            </div>
            {!isEdit && (
              <FormField
                className="mt-3"
                label={
                  <>
                    Cookie{" "}
                    <span className="font-normal text-[var(--color-text-muted)]">
                      (可选 · 留空则扫码授权)
                    </span>
                  </>
                }
              >
                <Textarea
                  className="font-mono"
                  rows={3}
                  placeholder="粘贴 Cookie 字符串，用于免扫码登录"
                  value={form.cookie}
                  onChange={(e) => setForm((f) => ({ ...f, cookie: e.target.value }))}
                />
              </FormField>
            )}
          </Section>
          <div
            className="rounded-[var(--radius-sm)] bg-[var(--color-surface-raised)]
                       px-3.5 py-2.5 text-[0.75rem] leading-relaxed
                       text-[var(--color-text-muted)]"
          >
            {isEdit
              ? "点击「重新获取凭证」后，将重新拉起内置指纹浏览器并展示抖音扫码二维码，扫码完成后最新凭证（Cookie / 签名）自动写回该账号。"
              : "保存后将自动弹出内置指纹浏览器并展示抖音扫码二维码，扫码完成后凭证自动写回该账号；之后可在账号卡片中配置代理与凭证校验。"}
          </div>
        </div>

        <div className="flex shrink-0 justify-end gap-2 border-t border-[var(--color-border)]
                        bg-[var(--color-surface)] px-4.5 py-3">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={onSave} disabled={!form.name.trim()}>
            {isEdit ? "重新获取凭证" : "确认新增"}
          </Button>
        </div>
      </motion.div>
    </>
  );
}