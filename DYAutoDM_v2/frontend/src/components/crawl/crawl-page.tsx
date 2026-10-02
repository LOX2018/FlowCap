import { useState, useEffect, useRef } from "react";
import {
  SearchIcon, MessageSquare, Send, Filter, X, Loader2, Square,
} from "lucide-react";
import { PageProps } from "../../api/client";
import { Avatar, hue } from "../../components/ui";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Section, Row, Blank, SkeletonRows, Toolbar,
} from "@/components/page/kit";
import {
  Tabs, TabsList, TabsTrigger, TabsContent,
} from "@/components/ui/tabs";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import { fmtNumShort, fmtTs, ORDER_OPTS, DUR_OPTS, PT_OPTS } from "./crawl-shared";
// ★ ADR-034（2026-10-03，用户 2026-10-03 拍板，方向反转 ADR-033）：
//   原 ADR-033 把「内容浏览」融进**采集页**（采集为宿主）。
//   现按用户新指令反转：**采集功能融进「内容总览」**（内容为宿主，采集为 tab）。
//   —— 导航只保留「内容」一项；采集作为其二级 tab，链路仍是
//      「浏览 → 选中 → 采集 → 私信」，只是入口名称与层级反过来。
//
// ★ 本文件**不再导入 PlatformPage**（2026-10-03）：
//   保留它会形成 `platform-page → crawl-page → platform-page` 的**循环依赖**
//   （ESM 循环下模块初始化顺序不确定，且两边都 lazy import 时会放大）。
//   而采集页已不再是导航项，它内部那个「内容浏览」二级 tab 也就没有存在意义
//   ——「浏览作品」的唯一入口就是「内容总览」页本身。
//   故此处删除该 tab，让本文件成为**纯粹的采集工作台**（可被内容页 embedded 挂载）。

/**
 * 采集工作台（ADR-034）—— 可独立成页，也可被 PlatformPage 以 `embedded` 挂为 tab。
 *
 * @param embedded  true = 只吐 Tabs 主体（宿主已有 PageContainer/Tabs）
 * @param account   嵌入模式下由宿主提供账号（与内容 tab 共享同一账号）
 * @param accounts  嵌入模式下由宿主提供账号名列表
 */
export default function CrawlPage(props: PageProps & {
  embedded?: boolean;
  account?: string;
  accounts?: string[];
  onSelectAweme?: (awemeId: string) => void;
}) {
  const { push, ready, api } = props;
  const [q, setQ] = useState("");
  const [order, setOrder] = useState("0");
  const [pt, setPt] = useState("0");
  const [dur, setDur] = useState("");
  const [searching, setSearching] = useState(false);
  const [did, setDid] = useState(false);
  const [results, setResults] = useState<any[]>([]);
  // ★ v0.46.18：搜索域被平台风控时的原因（非空 = 被拦截）。
  //   与 results.length===0 必须区分：空结果可能是「真没搜到」，也可能是「被风控」。
  const [blocked, setBlocked] = useState("");
  const [accounts, setAccounts] = useState<{ name: string; loggedIn: boolean }[]>([]);
  const [cmtFor, setCmtFor] = useState<any | null>(null);
  const [cmts, setCmts] = useState<any[]>([]);
  const [cmtLoading, setCmtLoading] = useState(false);
  const [dmTpl, setDmTpl] = useState("你好，看到你评论了我的内容，想和你聊聊～");
  const [authorTpl, setAuthorTpl] = useState(
    "你好，刷到你的作品很感兴趣，想和你聊聊合作～",
  );
  const [dmState, setDmState] = useState<Record<string, string>>({});
  const [batchResult, setBatchResult] = useState<Record<string, any[]>>({});
  const [dmPreview, setDmPreview] = useState<Record<string, any[]>>({});
  const [cmtFilter, setCmtFilter] = useState("");
  const filteredCmts = cmtFilter.trim()
    ? cmts.filter((c) => (c?.content || c?.text || "").includes(cmtFilter.trim()))
    : cmts;
  const [batching, setBatching] = useState(false);
  // ★ ADR-034：嵌入模式（宿主=内容总览）下账号由**宿主**提供，与宿主其它 tab 共享同一账号；
  //   独立成页时用本页自己的 state（下方 useEffect 会拉已登录账号并自动选第一个）。
  const [ownAccount, setOwnAccount] = useState("");
  const account = (props as { account?: string }).account ?? ownAccount;
  // ★ 2026-09-30 方案1：多作品批量采集（勾选 → 串行采评论）。默认全不选。
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [batchCollecting, setBatchCollecting] = useState(false);
  const batchCancelRef = useRef(false);
  const pickedIds = results.filter((v) => picked[v.awemeId]).map((v) => v.awemeId);
  // ★ 2026-09-30 C 方案：匿名预览（零凭证探针）—— 搜索后自动跑，只预览不私信。
  const [anonPreview, setAnonPreview] = useState<Record<string, any[]>>({});
  const [anonLoading, setAnonLoading] = useState(false);
  /**
   * ★ ADR-033：从「内容浏览」tab 选中的作品 ID。
   *
   * 用途：用户在内容 tab 里点开一个作品 → 切回「采集」tab 时，
   * 该作品已作为采集目标待命（不必再搜一遍、再凭记忆找）。
   *
   * ⚠️ 为何不直接自动采集：采集是**账号凭证写操作 + 消耗风控额度**，
   *    用户只是「看了一眼」就自动采，违反「所有请求由用户显式动作触发」铁律。
   *    这里只**预填目标**，采集仍由用户点按钮触发。
   */
  const [selectedAweme, setSelectedAweme] = useState<string>("");

  useEffect(() => {
    let alive = true;
    api
      .getAccounts()
      .then((list: any) => {
        if (!alive) return;
        const ls = (list || []).filter((a: any) => a.loggedIn);
        setAccounts(ls);
        // ★ ADR-034：仅独立成页时自动选第一个账号；嵌入模式账号由宿主给。
        if (!account && ls.length) setOwnAccount(ls[0].name);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  /** 拉匿名预览（零凭证探针）。搜索**尚未返回**即可先拉第一屏 —— 见 runSearch 内的首屏预热。 */
  const fetchAnonPreview = async (ids: string[], limit = 0) => {
    if (!ids.length) return;
    const want = limit > 0 ? ids.slice(0, limit) : ids;
    const todo = want.filter((id) => !anonPreview[id]);
    if (!todo.length) return;
    setAnonLoading(true);
    try {
      const r: any = await api.crawlCommentsAnonPreview({ aweme_ids: todo });
      const byId: Record<string, any[]> = {};
      (r?.per_work || []).forEach((w: any) => { byId[w.aweme_id] = w.items || []; });
      setAnonPreview((s) => ({ ...s, ...byId }));
      const hit = Object.values(byId).filter((a) => a.length).length;
      push(`匿名预览完成：${hit}/${todo.length} 个视频有评论预览（零凭证）`, 6000);
    } catch {
      /* 预览失败静默：不影响搜索与渲染 */
    } finally {
      setAnonLoading(false);
    }
  };

  const runSearch = async () => {
    const kw = q.trim();
    if (!kw) return push("请输入搜索关键词");
    if (!ready) return push("未连接后端，无法搜索");
    if (!account) return push("请先在账号管理页登录一个账号");
    setSearching(true);
    setDid(true);
    setResults([]);
    setAnonPreview({});
    // ★ 2026-09-30：搜索**发起的同时**并行拉第一屏匿名预览（零凭证、与账号无关）。
    //   两请求互不等待 ⇒ 预览结果不必等卡片渲染完（命中缓存后开抽屉即出）。
    void (async () => {
      setAnonLoading(true);
      try {
        const r: any = await api.crawlSearch({
          account, query: kw, kind: "video",
          // ★ 2026-09-30 接线：不传 num/sort_type/... ⇒ 由「采集策略」决定
          //   （排序/时段/时长/条数 集中在设置页的采集策略里维护，一处可调）。
          //   仅当用户在页面显式改过筛选器时才覆盖 —— 见 sendPolicyOverrides()。
          ...policyOverrides(),
        });
        setResults(r.items || []);
        // ★ v0.46.18「禁止假成功」：后端在搜索域被平台风控时返回
        //   blocked=true + blocked_reason，此时 items 必为空。
        //   必须显式告知用户「被风控拦截」，否则「命中 0 条」会被
        //   读成「这个关键词没有作品」——实测 2026-10-02 就是这个坑。
        if (r.blocked) {
          setBlocked(r.blocked_reason || "被平台风控拦截，请稍后重试。");
          push(`搜索被风控拦截：${r.blocked_reason || "请稍后重试"}`);
        } else {
          setBlocked("");
          push(`搜索完成，命中 ${r.total} 条`);
        }
        void fetchAnonPreview((r.items || []).map((v: any) => v.awemeId).filter(Boolean));
      } catch (e: any) {
        push(`搜索失败：${e?.message || e}`);
      } finally {
        setSearching(false);
        setAnonLoading(false);
      }
    })();
  };

  // 注：匿名预览不再用「结果变化」的 effect 触发（那会与 runSearch 内的
  // 并行拉取重复请求）。现由 runSearch 直接调 fetchAnonPreview，避免双发。

  const openComments = async (v: any) => {
    setCmtFor(v);
    const awemeId = v.awemeId;
    // 优先级：完整采集缓存 > 匿名预览 > 现采
    const full = batchResult[awemeId];
    if (full && full.length) {
      setCmts(full);
      setDmPreview({});
      push(`展示完整采集结果，共 ${full.length} 条`);
      return;
    }
    const prev = anonPreview[awemeId];
    if (prev && prev.length) {
      // 匿名预览：免凭证、免账号风险，但不可翻页、无数字 uid（不能私信）
      setCmts(prev);
      setDmPreview({ [awemeId]: prev });
      push(`展示匿名预览 ${prev.length} 条（不消耗账号；要全量请点「完整采集」）`, 6000);
      return;
    }
    if (!account) return push("请先登录账号（预览无数据时需账号才能完整采集）");
    setCmts([]);
    setCmtLoading(true);
    try {
      const r = await api.crawlComments({ account, aweme_id: awemeId, limit: 100 });
      setCmts(r.items || []);
      push(`评论采集完成，共 ${r.total} 条`);
    } catch (e: any) {
      push(`评论采集失败：${e?.message || e}`);
    } finally {
      setCmtLoading(false);
    }
  };

  /** 抽屉内「完整采集」：绕开预览缓存，用真实凭证全量翻页采当前视频。 */
  const fetchFullForCurrent = async () => {
    if (!cmtFor) return;
    if (!account) return push("完整采集需要账号");
    const awemeId = cmtFor.awemeId;
    setCmtLoading(true);
    try {
      const r = await api.crawlComments({ account, aweme_id: awemeId, limit: 200 });
      setCmts(r.items || []);
      setDmPreview({});
      setBatchResult((s) => ({ ...s, [awemeId]: r.items || [] }));
      push(`完整采集完成，共 ${r.total} 条`, 6000);
    } catch (e: any) {
      push(`完整采集失败：${e?.message || e}`, 8000);
    } finally {
      setCmtLoading(false);
    }
  };

  /** 批量采集：对勾选的作品**串行**采评论（后端 + 间隔），只采不发。
   *  ★ 2026-10-02：接入高价值关键词过滤 + 支持终止。 */
  const runBatchCollect = async () => {
    if (!account) return push("请先登录账号");
    if (!pickedIds.length) return push("请先勾选要采集的作品");
    if (batchCollecting) return;
    setBatchCollecting(true);
    batchCancelRef.current = false;
    try {
      // ★ 2026-10-02：从配置中心读取高价值关键词门槛
      let minScore = 0;
      try {
        const cfg = await api.getConfig();
        const crawlCfg = (cfg.config as any)?.crawl || {};
        minScore = Math.max(0, parseInt(crawlCfg.batch_min_score || "0", 10) || 0);
      } catch { /* 配置读取失败时不过滤 */ }
      const r = await api.crawlCommentsBatch({
        account,
        aweme_ids: pickedIds,
        limit: 100,
        min_score: minScore,
      });
      const failed = (r.per_work || []).filter((w) => w.status !== "ok");
      push(
        (r.cancelled ? "批量采集已终止：" : "批量采集完成：") +
          `${r.ok_works}/${r.works} 个作品成功，共 ${r.total_comments} 条评论` +
          (failed.length ? `（${failed.length} 个失败）` : ""),
        8000,
      );
      // 把采集到的评论挂到对应作品上，便于就地打开查看
      const byId: Record<string, any[]> = {};
      (r.per_work || []).forEach((w) => { byId[w.aweme_id] = w.items || []; });
      setBatchResult(byId);
    } catch (e: any) {
      push(`批量采集失败：${e?.message || e}`, 8000);
    } finally {
      setBatchCollecting(false);
    }
  };

  /** 终止正在进行的批量采集（★ 2026-10-02）。 */
  const cancelBatchCollect = async () => {
    if (!batchCollecting) return;
    batchCancelRef.current = true;
    try {
      await api.crawlCommentsBatchCancel(account);
      push("终止信号已发送，采集将在当前作品完成后停止", 4000);
    } catch (e: any) {
      push(`终止失败：${e?.message || e}`, 4000);
    }
  };

  const sendDm = async (uid: string, nickname: string) => {
    if (!uid) return push("该评论缺少 uid，无法私信");
    if (!dmTpl.trim()) return push("请先填写私信文案");
    setDmState((s) => ({ ...s, [uid]: "sending" }));
    try {
      const r = await api.crawlDm({ account, uid, text: dmTpl });
      setDmState((s) => ({ ...s, [uid]: r.ok ? "sent" : `失败:${r.reason}` }));
      push(r.ok ? `已向 ${nickname || uid} 发送私信` : `发送失败：${r.reason}`);
    } catch (e: any) {
      setDmState((s) => ({ ...s, [uid]: `失败:${e?.message || e}` }));
      push(`发送失败：${e?.message || e}`);
    }
  };

  const sendAuthorDm = async (v: any) => {
    if (!v.uid) return push("该作品缺少作者 uid，无法私信");
    if (!authorTpl.trim()) return push("请先填写私信作者文案");
    setDmState((s) => ({ ...s, [v.uid]: "sending" }));
    try {
      const r = await api.crawlDm({ account, uid: v.uid, text: authorTpl });
      setDmState((s) => ({ ...s, [v.uid]: r.ok ? "sent" : `失败:${r.reason}` }));
      push(r.ok ? `已向作者 ${v.nickname || v.uid} 发送私信` : `发送失败：${r.reason}`);
    } catch (e: any) {
      setDmState((s) => ({ ...s, [v.uid]: `失败:${e?.message || e}` }));
      push(`发送失败：${e?.message || e}`);
    }
  };

  /** 批量私信：从已采集评论中按高价值关键词筛选候选，逐条发送（★ 2026-10-02）。
   *  与「批量采集」权限分离：采集只采不发，发送从采集结果中筛选。
   *  筛选逻辑在后端执行（SSOT），发送走统一发送闸门。 */
  const sendBatch = async () => {
    if (!cmtFor) return;
    if (!dmTpl.trim()) return push("请先填写私信文案");
    if (batching) return;
    if (!cmts.length) return push("当前无采集结果，请先采集评论");
    setBatching(true);
    try {
      // ★ 2026-10-02：调用后端 /dm/batch，筛选逻辑在后端执行
      const r = await api.crawlDmBatch({
        account,
        text: dmTpl,
        items: cmts.map((c: any) => ({
          uid: c.uid || "",
          nickname: c.nickname || "",
          text: c.text || "",
        })),
        // ★ 2026-10-02：0 = 「不覆盖」⇒ 后端以配置中心 crawl.batch_min_score 为准。
        // （传正数才是「本次临时覆盖」；传 0 不会把门槛踩回不过滤。）
        min_score: 0,
        max_send: 0,
        interval: 0,
      });
      const ns: Record<string, string> = {};
      (r.results || []).forEach((x) => {
        ns[x.uid] = x.ok ? "sent" : `失败:${x.reason}`;
      });
      setDmState((s) => ({ ...s, ...ns }));
      push(
        `批量私信完成：候选 ${r.candidates} · 成功 ${r.sent_ok} · 失败 ${r.sent_fail} · 限流 ${r.rate_limited}`,
        6000,
      );
    } catch (e: any) {
      push(`批量私信失败：${e?.message || e}`);
    } finally {
      setBatching(false);
    }
  };

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") setCmtFor(null);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const q0 = "0";
  const empty = did && !searching && results.length === 0;
  // ★ v0.46.18：被风控时**不能**显示「未命中，换个关键词」——那把平台限制
  //   说成了「你的关键词没作品」，是误导。blocked 优先于 empty。
  const blockedEmpty = empty && !!blocked;
  // ★ 2026-09-30 接线：**只把用户真正改过的筛选器**带给后端，其余留空 ⇒ 用采集策略。
  //   判据 = 与初始值比对；未改动 ⇒ 不传 ⇒ 策略生效（改造前是恒传写死默认，策略永无效）。
  const policyOverrides = () => {
    const out: Record<string, unknown> = {};
    if (order !== q0) out.sort_type = order;
    if (pt !== q0) out.publish_time = pt;
    if (dur !== "") out.filter_duration = dur;
    return out;
  };
  // ★ 当前抽屉展示的是否为「匿名预览」数据（无数字 uid ⇒ 不可私信）
  const isPreview = !!(cmtFor && dmPreview[cmtFor.awemeId]);

  return (
    <>
      {/* ★ ADR-034（2026-10-03）：方向反转 —— 采集作为「内容总览」的一个 tab。
          嵌入模式（embedded=true）下：
            · 不套 PageContainer / PageHeader（宿主已有）
            · 不再渲染「内容浏览」tab（否则 tab 套 tab）
            · 只吐「采集」工作台主体，账号由宿主提供（与宿主其它 tab 共享） */}
      {!(props as { embedded?: boolean }).embedded && (
        <PageContainer>
          <PageHeader
            title="数据采集 · 评论截流"
            description="复用账号凭证被动签名，不批量查询用户"
          />
        </PageContainer>
      )}

      <Tabs defaultValue="collect" className="mb-4">
        <TabsList>
          <TabsTrigger value="collect">
            <SearchIcon className="h-3.5 w-3.5" />采集
          </TabsTrigger>
          {/* ★ ADR-034：原「内容浏览」二级 tab 已删除 ——
              「浏览作品」的唯一入口是「内容总览」页；采集则作为它的 tab 存在。
              保留它会造成 tab 套 tab + 与宿主循环依赖。 */}
        </TabsList>

        <TabsContent value="collect">
      {/* ★ ADR-033：从「内容浏览」tab 带回来的采集目标。
          只**展示并等待用户点按钮**，不自动发起采集（风控铁律）。 */}
      {selectedAweme && !results.some((v: any) => v.awemeId === selectedAweme) ? (
        <Card className="mb-4 border-[var(--color-accent)]">
          <CardContent className="flex items-center gap-3 p-3">
            <Badge variant="accent">内容浏览选中</Badge>
            <code className="min-w-0 flex-1 truncate font-mono text-[0.76rem]
                             text-[var(--color-text-secondary)]"
                  title={selectedAweme}>
              {selectedAweme}
            </code>
            <Button
              size="sm"
              disabled={!account}
              title="用当前账号采集该作品的评论区"
              onClick={() => openComments({ awemeId: selectedAweme, title: selectedAweme })}
            >
              <MessageSquare className="h-3.5 w-3.5" />采评论
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelectedAweme("")}>
              <X className="h-3.5 w-3.5" />
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {/* 账号 + 搜索面板 */}
      <Section className="mb-4">
        <Toolbar>
          {/* ★ ADR-034：嵌入模式（宿主=内容总览）下账号由宿主统一选择，
              此处不再重复渲染下拉 —— 否则同一页出现两个账号选择器、切 tab 不同步。 */}
          {!(props as { embedded?: boolean }).embedded ? (
            <Select value={account} onValueChange={setOwnAccount}>
              <SelectTrigger className="h-9 w-[170px]">
                <SelectValue placeholder="选择账号" />
              </SelectTrigger>
              <SelectContent>
                {accounts.length === 0 && (
                  <SelectItem value="__none" disabled>（无可登录账号）</SelectItem>
                )}
                {accounts.map((a) => (
                  <SelectItem key={a.name} value={a.name}>{a.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            /* 嵌入模式：只读回显当前账号（可为空），不提供第二处选择入口 */
            <span className="flex h-9 items-center rounded border border-[var(--border)] px-3 text-[0.78rem] text-[var(--color-text-secondary)]">
              {account || "（宿主未选账号）"}
            </span>
          )}

          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
            placeholder="搜索视频关键词"
            className="min-w-[200px] flex-1"
          />

          <Button onClick={runSearch} disabled={searching} className="shrink-0">
            {searching
              ? <><Loader2 className="h-4 w-4 animate-spin" />搜索中…</>
              : <><SearchIcon className="h-4 w-4" />搜索</>}
          </Button>
        </Toolbar>

        <div className="mt-3 flex flex-wrap items-center gap-4 text-[0.74rem]
                        text-[var(--color-text-secondary)]">
          <label className="flex items-center gap-1.5">
            排序
            <Select value={order} onValueChange={setOrder}>
              <SelectTrigger className="w-[118px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                {ORDER_OPTS.map((o) => (
                  <SelectItem key={o.v} value={o.v}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="flex items-center gap-1.5">
            发布时间
            <Select value={pt} onValueChange={setPt}>
              <SelectTrigger className="w-[110px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                {PT_OPTS.map((o) => (
                  <SelectItem key={o.v} value={o.v}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="flex items-center gap-1.5">
            视频时长
            <Select value={dur || "__all"} onValueChange={(v) => setDur(v === "__all" ? "" : v)}>
              <SelectTrigger className="w-[120px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                {DUR_OPTS.map((o) => (
                  <SelectItem key={o.v || "__all"} value={o.v || "__all"}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        </div>
      </Section>

      {searching ? (
        <div className="grid grid-cols-4 gap-3">
          {[0, 1, 2, 3].map((i) => (
            <Card key={i} className="overflow-hidden">
              <div className="aspect-[3/4] animate-pulse bg-[var(--color-surface-raised)]" />
              <CardContent className="space-y-2 p-3">
                <div className="h-3 animate-pulse rounded bg-[var(--color-surface-raised)]" />
                <div className="h-3 w-2/3 animate-pulse rounded bg-[var(--color-surface-raised)]" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : blockedEmpty ? (
        <Blank>
          <div data-od-id="crawl-search-blocked" className="space-y-1">
            <div className="font-medium text-[var(--color-warning)]">搜索被平台风控拦截</div>
            <div className="text-[0.8rem] text-[var(--color-text-muted)]">{blocked}</div>
            <div className="text-[0.75rem] text-[var(--color-text-muted)]">
              这是平台对该账号搜索功能临时限制，不是「{q}」没有作品。
              通常数小时内自动解除；持续出现请重新扫码登录或更换网络。
            </div>
          </div>
        </Blank>
      ) : empty ? (
        <Blank>{did ? `未命中「${q}」，换一个关键词试试` : "输入关键词并点击「搜索」，查看结果集"}</Blank>
      ) : (
        <>
          <Section className="mb-3" actions={
            <div className="flex items-center gap-2">
              <Badge variant="outline">{results.length} 条结果</Badge>
              {anonLoading && (
                <span className="flex items-center gap-1 text-[0.7rem]
                                 text-[var(--color-text-muted)]">
                  <Loader2 className="h-3 w-3 animate-spin" />匿名预览中…
                </span>
              )}
              <div className="flex items-center gap-1.5">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={batchCollecting || pickedIds.length === 0}
                  onClick={runBatchCollect}
                  title="对勾选的作品串行采集评论（只采集，不发送）"
                >
                  {batchCollecting
                    ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />批量采集中…</>
                    : <>批量采集{pickedIds.length ? `（${pickedIds.length}）` : ""}</>}
                </Button>
                {batchCollecting && (
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={cancelBatchCollect}
                    title="终止当前批量采集"
                  >
                    <Square className="h-3 w-3" />终止
                  </Button>
                )}
              </div>
            </div>
          }>
            <div className="flex items-center gap-2">
              <span className="shrink-0 text-[0.74rem] text-[var(--color-text-secondary)]">
                作者话术
              </span>
              <Input
                value={authorTpl}
                onChange={(e) => setAuthorTpl(e.target.value)}
                placeholder="「私信作者」按钮使用的话术（与评论区话术分离）"
                className="flex-1"
              />
            </div>
          </Section>

          <div className="grid grid-cols-4 gap-3">
            {results.map((v: any, i: number) => {
              const vId = v.awemeId || "v" + i;
              const hu = Number(hue(i + 1)) % 360;
              const st = dmState[v.uid];
              return (
                <Card key={vId} className="overflow-hidden">
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => openComments(v)}
                      title="点击采集该视频评论区"
                      className="relative block aspect-[3/4] w-full cursor-pointer overflow-hidden"
                      style={{
                        background: v.cover
                          ? undefined
                          : `linear-gradient(135deg, oklch(40% 0.13 ${hu}), oklch(24% 0.08 ${hu}))`,
                        backgroundImage: v.cover ? `url("${v.cover}")` : undefined,
                        backgroundSize: "cover",
                        backgroundPosition: "center",
                      }}
                    >
                      <span className="absolute left-2 top-7 rounded-full bg-black/55 px-2 py-0.5
                                       text-[0.62rem] text-white/90 backdrop-blur">
                        douyin
                      </span>
                      <span className="absolute bottom-2 right-2 rounded-full bg-black/55 px-2 py-0.5
                                       text-[0.68rem] text-white/90 backdrop-blur">
                        💬 {fmtNumShort(v.cmts)}
                      </span>
                      {anonPreview[v.awemeId] && (
                        <span className="absolute left-2 bottom-2 rounded-full bg-black/65 px-2 py-0.5
                                         text-[0.62rem] text-white/90 backdrop-blur"
                              title="匿名预览（零凭证，仅≤20条，不可翻页/私信）">
                          预览 {anonPreview[v.awemeId].length}
                        </span>
                      )}
                    </button>
                    {/* ★ 2026-10-02：勾选项移到卡片左上角（封面图之上） */}
                    <input
                      type="checkbox"
                      className="absolute left-2 top-2 z-10 h-4 w-4 cursor-pointer accent-[var(--color-accent)]"
                      checked={!!picked[v.awemeId]}
                      onChange={(e) =>
                        setPicked((s) => ({ ...s, [v.awemeId]: e.target.checked }))
                      }
                      title="勾选后可「批量采集」"
                    />
                  </div>

                  <CardContent className="p-2.5">
                    <div className="line-clamp-2 text-[0.76rem] leading-snug text-[var(--color-text)]">
                      {v.title || "（无标题）"}
                    </div>
                    <div className="mt-1.5 flex items-center gap-2 text-[0.68rem]
                                    text-[var(--color-text-muted)]">
                      <span className="font-mono">▶ {fmtNumShort(v.plays)}</span>
                      <span className="font-mono">♥ {fmtNumShort(v.likes)}</span>
                      <span className="truncate">{v.nickname || "未知作者"}</span>
                    </div>
                    <div className="mt-2 flex items-center gap-1.5">
                      <Button variant="ghost" size="sm" onClick={() => openComments(v)}>
                        <MessageSquare className="h-3.5 w-3.5" />采评论
                      </Button>
                      {v.uid && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={st === "sending" || st === "sent"}
                          onClick={() => sendAuthorDm(v)}
                        >
                          {st === "sending" ? "发送中…" : st === "sent" ? "已私信" : "私信作者"}
                        </Button>
                      )}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </>
      )}

      {/* 评论区抽屉 —— Radix Dialog（替代旧 .overlay，自带焦点管理与 Esc 关闭） */}
      {cmtFor && (
        <div
          className="fixed inset-0 z-[var(--z-drawer)] flex justify-end modal-scrim"
          onClick={() => setCmtFor(null)}
        >
          <div
            className="flex h-full w-full max-w-[760px] flex-col border-l
                       border-[var(--color-border)] bg-[var(--color-surface-solid)]
                       shadow-[var(--shadow-lg)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex shrink-0 items-center gap-3 border-b
                            border-[var(--color-border)] p-4">
              {cmtFor.cover && (
                <img
                  src={cmtFor.cover}
                  alt=""
                  className="h-12 w-12 shrink-0 rounded-[var(--radius-sm)] object-cover"
                />
              )}
              <h2 className="min-w-0 flex-1 truncate text-[0.95rem] font-semibold
                             text-[var(--color-text)]">
                评论区 · {cmtFor.title?.slice(0, 24) || cmtFor.awemeId}
                {isPreview && (
                  <span className="ml-2 rounded-full bg-[var(--color-surface-raised)]
                                   px-2 py-0.5 align-middle text-[0.6rem]
                                   font-normal text-[var(--color-text-muted)]">
                    匿名预览 · 不全
                  </span>
                )}
              </h2>
              {isPreview && (
                <Button size="sm" variant="outline" disabled={cmtLoading || !account}
                        onClick={fetchFullForCurrent}
                        title="用真实账号全量采集该视频评论（可私信）">
                  {cmtLoading
                    ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />采集中…</>
                    : <>完整采集</>}
                </Button>
              )}
              <Button variant="ghost" size="icon-sm" onClick={() => setCmtFor(null)}>
                <X className="h-4 w-4" />
              </Button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <Section className="mb-3">
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="w-16 shrink-0 text-[0.74rem]
                                     text-[var(--color-text-secondary)]">评论话术</span>
                    <Input
                      value={dmTpl}
                      onChange={(e) => setDmTpl(e.target.value)}
                      placeholder="发给评论用户的话术"
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="flex w-16 shrink-0 items-center gap-1 text-[0.74rem]
                                     text-[var(--color-text-secondary)]">
                      <Filter className="h-3 w-3" />筛选
                    </span>
                    <Input
                      value={cmtFilter}
                      onChange={(e) => setCmtFilter(e.target.value)}
                      placeholder="只对评论含该关键词的用户发（留空=全部）"
                      className="flex-1"
                    />
                  </div>
                </div>

                <div className="mt-3 flex items-center gap-2">
                  <span className="text-[0.74rem] text-[var(--color-text-muted)]">
                    命中 {cmtFilter ? filteredCmts.length : cmts.length} 条评论的用户，可单发或批量
                  </span>
                  <div className="flex-1" />
                  <Button
                    size="sm"
                    disabled={batching || !dmTpl.trim() || cmts.length === 0}
                    title={cmts.length === 0 ? "请先采集评论" : undefined}
                    onClick={sendBatch}
                  >
                    {batching
                      ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />批量发送中…</>
                      : <><Send className="h-3.5 w-3.5" />批量私信全部</>}
                  </Button>
                </div>
              </Section>

              {cmtLoading ? (
                <SkeletonRows rows={5} />
              ) : cmts.length === 0 ? (
                <Blank>未采到评论</Blank>
              ) : (
                <div className="divide-y divide-[var(--color-border)]">
                  {cmts.map((c: any) => {
                    const st = dmState[c.uid];
                    return (
                      <Row key={c.cid} className="!px-0 py-2.5">
                        <Avatar name={c.nickname || "路人"} h={hue((c.cid || "x").length)} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-baseline gap-2">
                            <span className="text-[0.8rem] font-medium text-[var(--color-text)]">
                              {c.nickname || "匿名"}
                            </span>
                            {c.ip && (
                              <span className="font-mono text-[0.68rem]
                                               text-[var(--color-text-muted)]">{c.ip}</span>
                            )}
                          </div>
                          <div className="mt-0.5 text-[0.8rem] text-[var(--color-text-secondary)]">
                            {c.text}
                          </div>
                          <div className="mt-0.5 font-mono text-[0.68rem]
                                          text-[var(--color-text-muted)]">
                            ♥ {fmtNumShort(c.digg)} · {fmtTs(c.ts)}
                          </div>
                        </div>
                        {c.uid ? (
                          <Button
                            variant={st === "sent" ? "success-outline" : "ghost"}
                            size="sm"
                            disabled={st === "sending" || st === "sent"}
                            onClick={() => sendDm(c.uid, c.nickname)}
                          >
                            {st === "sending" ? "发送中…" : st === "sent" ? "已私信" : "私信"}
                          </Button>
                        ) : (
                          <span className="shrink-0 text-[0.62rem] text-[var(--color-text-muted)]"
                                title="匿名预览无数字 uid，完整采集后可私信">
                            无私信ID
                          </span>
                        )}
                      </Row>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
        </TabsContent>

      </Tabs>
    </>
  );
}
