/**
 * 评论面板 —— 内容页「播放 + 评论**同时**展示」（ADR-018 F3）
 *
 * ## 为什么挂在这里（platform 页，而不是 crawl 页）
 *
 * 读代码确认（不是猜）：
 *   · `platform-page.tsx` 持有播放器浮层（`FullscreenPlayer`，L526-558），
 *     是**唯一**有「正在播放的作品 aweme_id」的页面；
 *   · `crawl-page.tsx` 是**关键词搜索**链路（先搜作品 → 点评论 → 私信），
 *     它**没有播放器**（`fullscreen-player.tsx:21-22` 注释明写
 *     「评论能力在 crawl-page 采集链路」），挂它就得先造播放器；
 *   · 用户 2026-09-15 已决策「采集 = 内容浏览的高级模式」⇒ 采集面板
 *     `crawl-panel.tsx` 本就被 platform 页引用（L508）。
 * ⇒ 评论区块挂在 platform 页播放器浮层**右侧**，与播放同屏并发加载。
 *
 * ## 与播放「同时」展示（不是切 Tab 才加载）
 *
 * 组件挂载即 `useQuery`（`enabled` 只依赖 account + awemeId），
 * 与播放器取址**并发**进行 —— 不放在任何 TabsContent 内。
 *
 * ## 风控红线（D7）
 * 昵称**只用评论自带**的 `user_nickname`；本组件不发起任何批量用户补查请求。
 *
 * ## D1（默认不自动发）
 * 「发私信」**只能手动点按钮**；组件内无任何自动/批量/定时发送逻辑。
 * 后端返回 `accepted` 只是**入队受理**，UI 如实显示受理态，不谎称已送达。
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { MessageSquare, Send, Loader2, ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { EmptyState, LoadingState, ErrorState } from "@/components/ui/empty-state";
import { platformApi, type CommentFullItem } from "@/api/platform";
import { fmtAgo, fmtNum } from "@/lib/utils";

interface Props {
  account: string;
  awemeId: string;
  /** 私信文案（可编辑，默认给一句中性开场白） */
  dmTplDefault?: string;
  push: (msg: string, holdMs?: number) => void;
}

/** 单条评论行（一级或楼中楼共用）。 */
function CommentRow({
  c, indent = false, sending, onSend,
}: {
  c: CommentFullItem | CommentFullItem["reply_comment"][number];
  indent?: boolean;
  sending?: boolean;
  onSend?: (uid: string, nickname: string) => void;
}) {
  const uid = String((c as { user_uid?: string }).user_uid || "");
  const nickname = String((c as { user_nickname?: string }).user_nickname || "");
  return (
    <div
      className={
        indent
          ? "ml-4 border-l border-[var(--border)] pl-2"
          : ""
      }
    >
      <div className="flex items-start justify-between gap-2 rounded px-2 py-1.5 hover:bg-[var(--color-surface-raised)]">
        <div className="min-w-0 flex-1">
          <div className="truncate text-[0.75rem] font-medium text-[var(--color-text)]">
            {/* 昵称**仅取评论自带**，取不到就显示 uid（绝不补查、不显示假昵称） */}
            {nickname || (uid ? `uid ${uid}` : "匿名")}
          </div>
          <div className="text-[0.72rem] leading-relaxed text-[var(--color-text-secondary)]">
            {c.text || "（无内容）"}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-[0.65rem] text-[var(--color-text-muted)]">
            {c.create_time ? <span>{fmtAgo(c.create_time)}</span> : null}
            <span>👍 {fmtNum(c.digg_count || 0)}</span>
          </div>
        </div>
        <div className="shrink-0">
          {/* 手动触发：无 uid 的评论不渲染按钮（避免假可用） */}
          {uid && onSend ? (
            <Button size="sm" variant="ghost" disabled={sending}
                    onClick={() => onSend(uid, nickname)}>
              {sending ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                       : <Send className="h-3.5 w-3.5" />}
              私信
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function CommentPanel({ account, awemeId, dmTplDefault, push }: Props) {
  const [dmTpl, setDmTpl] = useState(
    dmTplDefault || "你好，看到你评论了我的内容，想和你聊聊～");
  const [sendingUid, setSendingUid] = useState<string>("");
  const [dmState, setDmState] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // ★ 与播放**同时**加载：组件挂载即取，不依赖任何 Tab 切换。
  const q = useQuery({
    queryKey: ["platform-comments-full", account, awemeId],
    queryFn: () => platformApi.commentsFull(account, awemeId, "", 50, true, 5),
    enabled: !!account && !!awemeId,
    staleTime: 120_000,
  });

  const sendOne = async (uid: string, nickname: string) => {
    if (!dmTpl.trim()) {
      push("请先填写私信文案", 4000);
      return;
    }
    setSendingUid(uid);
    try {
      const r = await platformApi.commentDm(account, uid, dmTpl.trim(), nickname);
      // ⚠️ accepted = **入队受理**，不是投递成功 —— 如实显示，不谎称已发送。
      const msg = r.accepted
        ? `已受理：${nickname || uid}（等待投递回执确认）`
        : `未受理：${r.error || "未知原因"}`;
      setDmState((s) => ({ ...s, [uid]: r.accepted ? "已受理" : `失败: ${r.error || ""}` }));
      push(msg, 5000);
    } catch (e) {
      setDmState((s) => ({ ...s, [uid]: `异常: ${(e as Error)?.message || ""}` }));
      push(`私信异常：${String((e as Error)?.message || e).slice(0, 120)}`, 6000);
    } finally {
      setSendingUid("");
    }
  };

  if (!awemeId) {
    return <EmptyState title="还没有选中作品" description="点开一个作品后，这里会与播放同时加载它的评论。" />;
  }
  if (q.isPending) return <LoadingState label="评论加载中…" />;
  if (q.isError) {
    return <ErrorState message={String((q.error as Error)?.message || "评论加载失败")}
                       onRetry={q.refetch} />;
  }

  const items = (q.data?.items || []) as CommentFullItem[];
  // ★ 如实呈现：风控拦截 ≠ 「本来就没评论」
  if (q.data?.blocked) {
    return (
      <EmptyState
        title="评论被风控拦截"
        description={q.data.reason
          || "平台侧（Argus）拦截了本次评论请求，未能取到数据。这不是「没有评论」——请稍后再试或确认账号登录态。"}
        action={{ label: "重试", onClick: q.refetch }}
      />
    );
  }
  if (!items.length) {
    return <EmptyState title="该作品暂无评论" description="已成功请求，平台返回的评论列表为空。" />;
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <MessageSquare className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        <span className="text-[0.78rem] font-medium">评论</span>
        <Badge variant="outline">{items.length}</Badge>
        {q.isFetching ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
      </div>

      <Input
        value={dmTpl}
        onChange={(e) => setDmTpl(e.target.value)}
        placeholder="私信文案…"
      />

      <div className="max-h-[46vh] space-y-1 overflow-y-auto">
        {items.map((c) => {
          const open = !!expanded[c.cid];
          const replies = c.reply_comment || [];
          return (
            <div key={c.cid} className="rounded border border-[var(--border)]">
              <CommentRow c={c} sending={sendingUid === c.user_uid}
                          onSend={sendOne} />
              {dmState[c.user_uid] ? (
                <div className="px-2 pb-1 text-[0.65rem] text-[var(--color-text-muted)]">
                  {dmState[c.user_uid]}
                </div>
              ) : null}
              {replies.length ? (
                <div className="pb-1.5">
                  <button
                    className="flex items-center gap-1 px-2 text-[0.68rem] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                    onClick={() => setExpanded((s) => ({ ...s, [c.cid]: !open }))}
                  >
                    {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                    {replies.length} 条回复
                  </button>
                  {open ? (
                    <div className="mt-1 space-y-0.5">
                      {replies.map((r) => (
                        <CommentRow key={r.cid} c={r} indent
                                    sending={sendingUid === r.user_uid}
                                    onSend={sendOne} />
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
