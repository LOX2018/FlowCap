/**
 * CrawlFloatingPanel —— 采集悬浮窗（2026-10-03 新增）
 *
 * ## 设计意图
 *
 * 用户要求：采集功能独立出来，作为悬浮窗形式呈现。
 * 内容中心所有子页面（除站内通知）都纳入采集范围。
 *
 * ★ 2026-10-03 形态变更：居中模态弹窗 → **页面右下角停靠面板**。
 *   诉求：一边看页面一边操作，不要全屏遮罩把人锁在弹窗里。
 *   ⇒ 弃用 `Modal`（居中 + modal-scrim 全屏遮罩），改为自写 fixed 停靠容器；
 *   保留 Esc 关闭、header/body/footer 三段与全部原有内容。
 *   仍复用 `ModalHeader / ModalBody / ModalFooter` —— 它们是纯布局 div，
 *   **不带遮罩、不做 portal**，与「不用 Modal 组件」不冲突。
 *
 * ## 功能
 *
 * 1. 发送私信条数（0 = 不限制）
 * 2. 发送账号选择（多选）
 * 3. 评论日期范围
 * 4. 批量采集按钮（对勾选的作品执行采集）
 *
 * 标签**不在此选择** —— 由页面头部统一下拉传入 `tagId`（一处选择，跨 tab 共享）。
 *
 * ## 数据来源
 *
 * - 账号列表：props.api.getAccounts()
 * - 批量采集：props.api.crawlCommentsBatch()
 */
import { useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { MessageSquare, Loader2 } from "lucide-react";
import { ModalHeader, ModalBody, ModalFooter } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import type { PageProps } from "@/api/client";

export interface CrawlFloatingPanelProps {
  open: boolean;
  onClose: () => void;
  /** 当前 tab 勾选的作品 ID 列表 */
  selectedIds: string[];
  /** 当前账号（用于默认选中） */
  currentAccount: string;
  // ★ 2026-10-03（用户指令）：采集标签在**页面头部**选择后传入；
  //   悬浮窗自己也提供下拉（用户后续要求放回），故需要 onTagChange 双向同步。
  tagId?: string;
  onTagChange?: (tagId: string) => void;
  /** 采集模式：works = 作品采集（默认），users = 用户采集 */
  mode?: "works" | "users";
  props: PageProps;
}

export default function CrawlFloatingPanel({
  open,
  onClose,
  selectedIds,
  currentAccount,
  tagId = "",
  onTagChange,
  mode = "works",
  props,
}: CrawlFloatingPanelProps) {
  const { api, push } = props;

  // 作品 / 用户 —— mode 的单一真源：驱动全部文案与校验口径
  //（标题/描述/按钮/已勾选提示/空勾选校验都用它，避免各处重复判 mode）
  const isUsers = mode === "users";
  const unit = isUsers ? "用户" : "作品";

  // 账号列表
  // 高价值标签（用户要求放回悬浮窗）
  const tagsQ = useQuery({
    queryKey: ["crawl-panel-tags"],
    queryFn: () => api.listTags(),
    enabled: open,
    staleTime: 30_000,
  });
  const tags = tagsQ.data?.tags || [];

  // 采集策略（用户要求放回悬浮窗）
  const policiesQ = useQuery({
    queryKey: ["crawl-policies"],
    queryFn: () => api.listCrawlPolicies(),
    enabled: open,
    staleTime: 30_000,
  });
  const policies = policiesQ.data?.items || [];

  const accountsQ = useQuery({
    queryKey: ["crawl-panel-accounts"],
    queryFn: async () => (await api.getAccounts()) as { name: string; loggedIn: boolean }[],
    enabled: open,
    staleTime: 30_000,
  });


  // 发送状态
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<{
    ok: number;
    fail: number;
    total: number;
    /** ★ 2026-10-03：私信阶段读数（未开私信时全 0） */
    dmCandidates?: number;
    dmSent?: number;
    dmFail?: number;
  } | null>(null);

  // 选中的账号（默认当前账号）
  const [selectedAccounts, setSelectedAccounts] = useState<string[]>([]);
  // 发送条数（0 = 不限制）
  const [maxSend, setMaxSend] = useState("0");
  // 评论日期范围（空 = 不限）
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  // ★ 2026-10-03（用户指令）：勾选框 → **目标下拉**
  //   crawl = 仅采集（只采不发）；crawl+dm = 全流程（采集 → 私信）
  const [goal, setGoal] = useState<"crawl" | "crawl+dm">("crawl");
  const sendAfterCrawl = goal === "crawl+dm";
  // 采集策略 id（空 = 用账号绑定/全局默认策略）
  const [policyId, setPolicyId] = useState("");
  // ★ 2026-10-03（用户指令）：「开启高价值采集时，只保留关键词命中的评论，
  //   其余全部丢弃」。
  //
  //   为何这里是常量 1 而不是可调门槛：
  //     · 后端契约「`crawl.batch_min_score` 是唯一权威来源，请求体 min_score
  //       仅作覆盖、**0 视为不覆盖**」（见 `/dm/batch` 同款实现）；
  //     · 传 0 ⇒ 不过滤（等于关掉）；传正数 ⇒ 走该标签配置里的门槛。
  //   ⇒ 「开启」= 传一个正数下界。具体阈值仍由**配置中心/标签**决定，
  //     前端不硬编码业务阈值（那会造成第二份真值）。
  //   ⚠️ 传 1 而非配置值：语义是「确保进入过滤分支」，具体门槛由后端取配置。
  const hvMinScore = 1;
  // 私信文案（私信阶段必填）
  const [dmTpl, setDmTpl] = useState("");

  // ★ 2026-10-03：正在运行的采集任务（任务中心接线）。
  //   ⚠️ 后端状态是**进程级内存**（进程重启即丢失），故：
  //   · 仅在面板打开时轮询（不做后台常驻轮询，省请求）；
  //   · staleTime 3s + refetchInterval 5s，关闭面板即停止；
  //   · **不得**把 running 以外的历史当「任务历史」长期展示（会误导用户
  //     以为有持久化记录）—— 故只显示 running 的，其余折叠为一行计数。
  const tasksQ = useQuery({
    queryKey: ["crawl-tasks"],
    queryFn: () => api.crawlTasks(),
    enabled: open,
    staleTime: 3_000,
    refetchInterval: open ? 5_000 : false,
  });
  const allTasks = tasksQ.data?.tasks || [];
  const runningTasks = allTasks.filter((t) => t.status === "running");
  const finishedCount = allTasks.length - runningTasks.length;

  // 打开时重置状态
  useEffect(() => {
    if (open) {
      setSendResult(null);
      setSending(false);
      setStartDate("");
      setEndDate("");
      // 默认选中当前账号
      if (currentAccount) {
        setSelectedAccounts([currentAccount]);
      }
    }
  }, [open, currentAccount]);

  // ★ Esc 关闭：原由 Modal 组件提供（见 ui/modal.tsx 的同名 effect）。
  //   停靠面板不再用 Modal ⇒ 此处自持，行为与全站弹窗保持一致。
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const accounts = (accountsQ.data || []).filter((a) => a.loggedIn);

  // 批量采集（★ 2026-10-03：采集 → 私信 一条流水线）
  //
  // 🔴 修复的**假成功**（本轮实测发现）：修复前 `selectedTag`（标签下拉）与
  // `maxSend`（发送条数）只进 UI state、**从未发给后端** ⇒ 用户选了标签、填了
  // 条数都毫无效果且无任何报错；`crawlDmBatch` 更是**全文未被调用过**，
  // 悬浮窗只能采集、**私信根本发不出去**。
  // 现按用户 2026-10-03 的定义接线：
  //   「采集是前置的」—— 采集本身只采不发（后端 /comments/batch 语义）；
  //   「经过高价值标签过滤后自动进入私信队列」—— 由 `sendAfterCrawl` 开关控制，
  //   开启时对**有数字 uid** 的评论调 /dm/batch（tag_id 传本次选中标签）。
  // ⚠️ 匿名预览拿不到 uid（后端日志原文「仅供预览，不可翻页/无私信 uid」），
  //    故私信只对**真凭证采集**的结果生效，匿名探针阶段不可能进私信队列。
  const handleBatchCrawl = async () => {
    if (!selectedAccounts.length) {
      push("请先选择发送账号");
      return;
    }
    if (!selectedIds.length) {
      push(`请先勾选要采集的${unit}`);
      return;
    }
    if (sendAfterCrawl && !dmTpl.trim()) {
      push("已开启采集后私信，但私信文案为空 —— 请填写文案或关闭该开关");
      return;
    }
    setSending(true);
    setSendResult(null);
    try {
      // 逐账号：采集 → 汇总 → （可选）私信
      let totalOk = 0;
      let totalFail = 0;
      let dmCandidates = 0;
      let dmSent = 0;
      let dmFail = 0;
      for (const account of selectedAccounts) {
        // ★ 2026-10-03：登记任务（任务中心）。**失败不阻断采集** ——
        //   任务跟踪是「可观测性」，不是业务前置；采集本身才是。
        //   但必须如实告知用户登记失败（不静默吞掉，否则又变成假成功）。
        let taskId = "";
        try {
          const reg = await api.crawlTaskRegister({
            account,
            aweme_ids: selectedIds,
            // ★ 2026-10-03（用户指令）：与采集请求**同一口径**。
            //   此前恒传 0 ⇒ 任务中心显示的门槛与实际执行的不一致。
            min_score: hvMinScore,
            start_date: startDate || "",
            end_date: endDate || "",
          });
          taskId = reg.task_id || "";
        } catch (e) {
          push(`任务登记失败（采集仍会继续）：${(e as Error)?.message || e}`, 6000);
        }
        const report = (patch: Record<string, unknown>) => {
          if (!taskId) return;
          api.crawlTaskProgress(taskId, patch)
            .then(() => tasksQ.refetch())
            .catch(() => { /* 进度上报失败不阻断采集 */ });
        };
        report({ phase: "collect", total: selectedIds.length, done: 0 });

        const r = await api.crawlCommentsBatch({
          account,
          aweme_ids: selectedIds,
          // ⚠️ 刻意**不传 limit**：后端约定「留空 = 用采集策略的 num」，
          //   传 100 会把策略盖掉（策略就又变成假接线）。
          // ★ 2026-10-03（用户指令）：开启高价值过滤（只留关键词命中的）。
          //   传 1 = 「进入过滤分支」，实际门槛由后端按标签/配置中心取
          //   （请求体 0 会被当成「不覆盖」⇒ 恒不过滤）。
          min_score: hvMinScore,
          // ★ 同一标签下传给采集，让「采集阶段」就用该标签的关键词表过滤，
          //   而不只是私信阶段才过滤。
          tag_id: tagId || "",
          start_date: startDate || undefined,
          end_date: endDate || undefined,
          policy_id: policyId || undefined,
        });
        totalOk += r.ok_works || 0;
        totalFail += (r.works || 0) - (r.ok_works || 0);
        report({
          phase: "collect",
          done: r.works || 0,
          total: r.works || selectedIds.length,
          ok_works: r.ok_works || 0,
          fail_works: (r.works || 0) - (r.ok_works || 0),
          status: "done",
        });

        // 私信：汇总本次采集到的**有 uid** 评论（后端再做高价值过滤 + 闸门）
        if (sendAfterCrawl) {
          const items = (r.per_work || [])
            .flatMap((w: { items?: Record<string, unknown>[] }) => w.items || [])
            .map((c) => ({
              uid: String(c.uid || ""),
              nickname: String(c.nickname || ""),
              text: String(c.text || ""),
            }))
            .filter((c) => c.uid);   // 无 uid 不可私信（fail-closed，不静默丢弃）
          if (items.length) {
            report({ phase: "dm", total: items.length, done: 0 });
            const d = await api.crawlDmBatch({
              account,
              text: dmTpl.trim(),
              items,
              // ★ 2026-10-03：传 hvMinScore（与采集阶段同一口径）。
              //   ⚠️ 原注释「0 = 不覆盖，由标签/配置中心决定门槛」**是错的**：
              //   传 0 时后端确实回落读配置中心，但**本次显式选的标签门槛
              //   会被绕过**（请求体 0 = 不覆盖 ⇒ 走账号绑定标签，
              //   而非用户此刻在面板里选的那个）。
              //   与采集阶段统一传 hvMinScore，两个阶段口径才一致。
              min_score: hvMinScore,
              max_send: Number(maxSend) || 0,   // 0 = 不限
              interval: 0,
              // ★ 方案A：页面头部所选标签作临时覆盖，空串 = 沿用账号绑定
              tag_id: tagId || "",
            });
            dmCandidates += d.candidates || 0;
            dmSent += d.sent_ok || 0;
            dmFail += d.sent_fail || 0;
            report({
              phase: "dm",
              done: (d.sent_ok || 0) + (d.sent_fail || 0),
              total: d.candidates || items.length,
              status: "done",
            });
          }
        }
      }
      setSendResult({
        ok: totalOk,
        fail: totalFail,
        total: selectedIds.length,
        dmCandidates,
        dmSent,
        dmFail,
      });
      const dmMsg = sendAfterCrawl
        ? `；私信 候选 ${dmCandidates} · 成功 ${dmSent} · 失败 ${dmFail}`
        : "";
      push(`批量采集完成：作品成功 ${totalOk}，失败 ${totalFail}${dmMsg}`, 8000);
    } catch (e) {
      push(`批量采集失败：${(e as Error)?.message || e}`, 8000);
    } finally {
      setSending(false);
    }
  };

  if (!open) return null;

  // ★ 右下角停靠面板（替代原居中 Modal）：
  //   · fixed bottom-4 right-4，宽 380px，最大高 min(78vh, 640px)
  //   · **无全屏遮罩** ⇒ 页面其余部分照常可点（这是本次改动的核心诉求）
  //   · 材质用项目既有浮层令牌 modal-surface（92% 实底 + 发丝线 + 大投影），
  //     不用 modal-scrim（那是遮罩专用，会把整页压暗）
  return createPortal(
    <div
      role="dialog"
      aria-modal={false}
      aria-labelledby="crawl-floating-title"
      data-od-id="crawl-floating-panel"
      className="modal-surface fixed bottom-4 right-4 z-[var(--z-modal)]
                 flex max-h-[min(78vh,640px)] w-[380px] max-w-[calc(100vw-2rem)]
                 flex-col overflow-hidden rounded-[var(--radius-lg)]"
    >
      <ModalHeader
        onClose={onClose}
        icon={<MessageSquare className="h-4 w-4" />}
        title={<span id="crawl-floating-title">{isUsers ? "用户采集设置" : "采集设置"}</span>}
        description={isUsers
          ? "配置批量采集参数，然后对勾选的用户执行采集"
          : "配置批量采集参数，然后对勾选的作品执行采集"}
      />
      <ModalBody className="space-y-4">
        {/* ★ 2026-10-03：正在运行的任务（任务中心接线） */}
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[0.78rem] font-medium text-[var(--color-text-secondary)]">
              正在运行的任务
              {runningTasks.length > 0 && (
                <span className="ml-1.5 text-[var(--color-accent)]">{runningTasks.length}</span>
              )}
            </span>
            {finishedCount > 0 && (
              <button
                type="button"
                className="text-[0.68rem] text-[var(--color-text-muted)] hover:underline"
                title="清理已结束的任务记录（后端为进程级内存，重启即清空）"
                onClick={() => {
                  api.crawlTasksClear()
                    .then(() => tasksQ.refetch())
                    .catch((e) => push(`清理任务记录失败：${(e as Error)?.message || e}`));
                }}
              >
                清理已结束（{finishedCount}）
              </button>
            )}
          </div>
          {tasksQ.isError ? (
            <p className="text-[0.7rem] text-[var(--color-danger)]">
              读取任务失败：{(tasksQ.error as Error)?.message || "后端无响应"}
            </p>
          ) : runningTasks.length === 0 ? (
            <p className="text-[0.7rem] text-[var(--color-text-muted)]">
              当前没有运行中的采集任务
            </p>
          ) : (
            <div className="space-y-1.5">
              {runningTasks.map((t) => (
                <div
                  key={t.id}
                  className="rounded-[var(--radius-sm)] border border-[var(--color-border)] px-2.5 py-1.5"
                >
                  <div className="flex items-center justify-between text-[0.72rem]">
                    <span className="truncate text-[var(--color-text)]">
                      {t.account} · {t.phase || "queued"}
                    </span>
                    <span className="font-mono text-[var(--color-text-muted)]">
                      {t.done}/{t.total || (t.aweme_ids || []).length}
                    </span>
                  </div>
                  <div className="mt-1 h-1 overflow-hidden rounded-full bg-[var(--color-surface-raised)]">
                    <div
                      className="h-full bg-[var(--color-accent)] transition-[width]"
                      style={{
                        width: `${
                          t.total > 0
                            ? Math.min(100, Math.round((t.done / t.total) * 100))
                            : 0
                        }%`,
                      }}
                    />
                  </div>
                  <div className="mt-0.5 text-[0.66rem] text-[var(--color-text-muted)]">
                    成功 {t.ok_works} · 失败 {t.fail_works}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 发送账号 */}
        <div>
          <label className="mb-1.5 block text-[0.78rem] font-medium text-[var(--color-text-secondary)]">
            发送账号（可多选）
          </label>
          <div className="flex flex-wrap gap-1.5">
            {accounts.length === 0 && (
              <span className="text-[0.72rem] text-[var(--color-text-muted)]">
                暂无已登录账号
              </span>
            )}
            {accounts.map((a) => {
              const selected = selectedAccounts.includes(a.name);
              return (
                <button
                  key={a.name}
                  type="button"
                  onClick={() => {
                    setSelectedAccounts((prev) =>
                      selected
                        ? prev.filter((x) => x !== a.name)
                        : [...prev, a.name]
                    );
                  }}
                  className={
                    "rounded-[8px] border px-2.5 py-1 text-[0.72rem] transition-colors " +
                    (selected
                      ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                      : "border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-raised)]")
                  }
                >
                  {a.name}
                </button>
              );
            })}
          </div>
        </div>

        {/* ★ 2026-10-03（用户指令）：四选项改 **2×2 网格**，共两行。
            原为 1 列 4 行，纵向占用过多、面板过高。
            用 grid-cols-2 + gap-x-3 保持两列对齐；控件 w-full 撑满各自列。 */}
        <div className="grid grid-cols-2 gap-x-3 gap-y-3">
          {/* 发送条数 */}
          <div>
            <label className="mb-1.5 block text-[0.78rem] font-medium text-[var(--color-text-secondary)]">
              发送私信条数（0 = 不限制）
            </label>
            <Input
              type="number"
              min={0}
              value={maxSend}
              onChange={(e) => setMaxSend(e.target.value)}
              placeholder="0"
              className="w-full"
            />
          </div>

          {/* ★ 目标下拉：用「目标」决定跑纯采集还是全流程（采集→私信） */}
          <div>
            <label className="mb-1.5 block text-[0.78rem] font-medium text-[var(--color-text-secondary)]">
              目标
            </label>
            <Select
              value={goal}
              onValueChange={(v) => setGoal(v as "crawl" | "crawl+dm")}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="crawl">仅采集</SelectItem>
                <SelectItem value="crawl+dm">采集并私信</SelectItem>
              </SelectContent>
            </Select>
            {goal === "crawl+dm" && (
              <Input
                value={dmTpl}
                onChange={(e) => setDmTpl(e.target.value)}
                placeholder="私信文案"
                className="mt-1.5"
              />
            )}
          </div>

          {/* 高价值标签（放回悬浮窗） */}
          <div>
            <label className="mb-1.5 block text-[0.78rem] font-medium text-[var(--color-text-secondary)]">
              高价值标签
            </label>
            <Select
              value={tagId}
              onValueChange={(v) => { onTagChange?.(v); }}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="默认标签" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">默认标签</SelectItem>
                {tags.map((t) => (
                  <SelectItem key={t.id} value={t.id}>{t.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* 采集策略（放回悬浮窗） */}
          <div>
            <label className="mb-1.5 block text-[0.78rem] font-medium text-[var(--color-text-secondary)]">
              采集策略
            </label>
            <Select value={policyId} onValueChange={setPolicyId}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="默认策略" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">默认策略</SelectItem>
                {policies.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}（{p.comment_limit || p.num} 条）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        {/* ↑ 2×2 网格闭合 */}

        {/* 评论日期范围 */}
        <div>
          <label className="mb-1.5 block text-[0.78rem] font-medium text-[var(--color-text-secondary)]">
            评论日期范围（留空 = 不限）
          </label>
          <div className="flex items-center gap-2">
            <Input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="flex-1"
            />
            <span className="text-[0.72rem] text-[var(--color-text-muted)]">至</span>
            <Input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="flex-1"
            />
          </div>
        </div>

        {/* 采集结果 */}
        {sendResult && (
          <div
            className={
              "rounded-[var(--radius-sm)] px-3 py-2 text-[0.78rem] " +
              (sendResult.fail > 0 || (sendResult.dmFail || 0) > 0
                ? "bg-[var(--color-danger)]/10 text-[var(--color-danger)]"
                : "bg-[var(--color-ok)]/10 text-[var(--color-ok)]")
            }
          >
            采集完成：成功 {sendResult.ok} 个{unit}，失败 {sendResult.fail} 个
            {sendResult.total > 0 && `，共 ${sendResult.total} 个`}
            {/* ★ 私信阶段读数：分母/分子都写清，避免「成功」含义不明 */}
            {sendAfterCrawl && (
              <div className="mt-1 border-t border-[var(--color-border)] pt-1">
                私信：候选 {sendResult.dmCandidates ?? 0} · 成功 {sendResult.dmSent ?? 0} · 失败 {sendResult.dmFail ?? 0}
              </div>
            )}
          </div>
        )}

        {/* 已勾选数量提示 */}
        <div className="text-[0.72rem] text-[var(--color-text-muted)]">
          已勾选 {selectedIds.length} 个{unit}
        </div>
      </ModalBody>
      <ModalFooter>
        <Button variant="ghost" onClick={onClose}>
          取消
        </Button>
        <Button
          onClick={handleBatchCrawl}
          disabled={
            sending || !selectedIds.length || !selectedAccounts.length ||
            (sendAfterCrawl && !dmTpl.trim())   // 开启私信但文案为空 ⇒ 不给跑
          }
          title={
            sendAfterCrawl && !dmTpl.trim()
              ? "已开启采集后私信，但私信文案为空"
              : undefined
          }
        >
          {sending ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {sendAfterCrawl ? "全流程中…" : "启动中…"}
            </>
          ) : (
            <>
              <MessageSquare className="h-3.5 w-3.5" />
              启动
            </>
          )}
        </Button>
      </ModalFooter>
    </div>,
    document.body
  );
}
