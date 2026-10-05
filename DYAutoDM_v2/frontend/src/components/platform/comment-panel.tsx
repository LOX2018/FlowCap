/**
 * 评论面板 —— 内容页「播放 + 评论**同时**展示」（ADR-018 F3）
 *
 * ## ★ 2026-09-28 重构：照搬「采集页」的一级评论实现（用户指示）
 *
 * ### 症状
 * 内容中心「点开视频 → 评论区一直刷新 / 加载不出来」，
 * 而独立「采集」页采评论**完全正常**。
 *
 * ### 根因（实测取证，见 cases/…_内容中心评论区恒加载_末级链路分叉…）
 * 两页本应是同一件事，却走了**两条不同的实现**：
 *   · 采集页 `crawl-page.tsx`：`api.crawlComments()` → `POST /api/crawl/comments`
 *     → 基座 `features.work_comments()`（**一级评论**，实测 16.5s / 100 条）
 *   · 内容页 `comment-panel.tsx`：`platformApi.commentsFull(with_inner=true)`
 *     → `POST /api/platform/comments/full` → `get_work_out_comment` 翻页
 *     **再逐条串行 await `get_work_all_inner_comment`**（楼中楼；实测 27.9s）
 * 差异 = 「只取一级」vs「一级 + N 条串行楼中楼」。
 * 后者随评论数线性增长、叠加播放器取址并发，浏览器侧迟迟拿不到渲染数据
 * ⇒ 表现为「一直在加载」。
 *
 * ### 决策（用户 2026-09-28 拍板：「直接照搬一级目录」）
 * 内容页评论区**不再自己造一条链路**，改为与采集页**同一实现**：
 *   `api.crawlComments()` 只取一级评论。
 * 楼中楼**不是本页诉求**（本页要的是「播放时同屏看到评论并可私信截流」），
 * 砍掉它即消除分叉，也消除那条串行放大链。**后端零改动**。
 *
 * ### 与「同时展示」不冲突
 * 仍是组件挂载即取（`useEffect` 依赖 awemeId），与播放器取址并发进行，
 * 不放在任何 TabsContent 内。
 *
 * ## 风控红线（D7）
 * 昵称**只用评论自带**的 `nickname`；本组件不发起任何批量用户补查请求。
 *
 * ## D1（默认不自动发）
 * 「发私信」**只能手动点按钮**；组件内无任何自动/批量/定时发送逻辑。
 *
 * ## 诚实呈现（禁假成功）
 * `crawlDm` 返回 `ok=false` 时如实显示失败原因，不谎称已送达；
 * 采集失败（后端 502）如实报错并给「重试」，**不**把它伪装成「暂无评论」。
 */
import { useCallback, useEffect, useState } from "react";
import { MessageSquare, Send, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/empty-state";
import { fmtAgo, fmtNum } from "@/lib/utils";

/** 采集页 `crawlComments` 返回的评论条目（与 `api/crawl.py::_map_comment` 契约一致）。 */
interface CrawlCommentItem {
  cid?: string;
  text?: string;
  nickname?: string;
  uid?: string;
  secUid?: string;
  digg?: number;
  ip?: string;
  ts?: number;
  replyTotal?: number;
  [k: string]: unknown;
}

/** 采集 API 子集（来自 `PageProps.api`；与采集页同源，不复用 platformApi）。 */
interface CrawlApi {
  crawlComments(body: {
    account: string;
    aweme_id: string;
    limit?: number;
  }): Promise<{ ok?: boolean; items?: CrawlCommentItem[]; total?: number; detail?: string }>;
  crawlDm(body: {
    account: string;
    uid: string;
    text: string;
  }): Promise<{ ok?: boolean; reason?: string; detail?: string }>;
}

interface Props {
  account: string;
  awemeId: string;
  /** 采集 API（来自 PageProps.api）——与「采集」页同一实现入口 */
  api: CrawlApi;
  /** 私信文案（可编辑，默认给一句中性开场白） */
  dmTplDefault?: string;
  push: (msg: string, holdMs?: number) => void;
}

/** 单条评论行。 */
function CommentRow({
  c, sending, state, onSend,
}: {
  c: CrawlCommentItem;
  sending?: boolean;
  state?: string;
  onSend?: (uid: string, nickname: string) => void;
}) {
  const uid = String(c.uid || "");
  const nickname = String(c.nickname || "");
  return (
    <div className="flex items-start justify-between gap-2 rounded px-2 py-1.5
                    hover:bg-[var(--color-surface-raised)]">
      <div className="min-w-0 flex-1">
        <div className="truncate text-[0.75rem] font-medium text-[var(--color-text)]">
          {/* 昵称**仅取评论自带**，取不到就显示 uid（绝不补查、不显示假昵称） */}
          {nickname || (uid ? `uid ${uid}` : "匿名")}
          {c.ip ? (
            <span className="ml-1.5 font-mono text-[0.65rem] text-[var(--color-text-muted)]">
              {String(c.ip)}
            </span>
          ) : null}
        </div>
        <div className="text-[0.72rem] leading-relaxed text-[var(--color-text-secondary)]">
          {String(c.text || "（无内容）")}
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-[0.65rem] text-[var(--color-text-muted)]">
          {c.ts ? <span>{fmtAgo(Number(c.ts))}</span> : null}
          <span>👍 {fmtNum(Number(c.digg || 0))}</span>
          {state ? <span className="text-[var(--color-text-muted)]">{state}</span> : null}
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
  );
}

export function CommentPanel({ account, awemeId, api, dmTplDefault, push }: Props) {
  const [dmTpl, setDmTpl] = useState(
    dmTplDefault || "你好，看到你评论了我的内容，想和你聊聊～");
  const [items, setItems] = useState<CrawlCommentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [sendingUid, setSendingUid] = useState<string>("");
  const [dmState, setDmState] = useState<Record<string, string>>({});

  // ★ 与播放**同时**加载：awemeId 变化即取，不依赖任何 Tab 切换。
  //   实现与采集页 `crawl-page.tsx::openComments` 一致（同一 API、同一字段）。
  const load = useCallback(async () => {
    if (!account || !awemeId) return;
    setLoading(true);
    setErr("");
    try {
      const r = await api.crawlComments({ account, aweme_id: awemeId, limit: 100 });
      if (r?.ok === false) {
        // 后端如实报失败（禁假成功）：不伪装成「暂无评论」
        setItems([]);
        setErr(String(r?.detail || "采集失败"));
      } else {
        setItems((r?.items || []) as CrawlCommentItem[]);
      }
    } catch (e) {
      setItems([]);
      setErr(String((e as Error)?.message || "评论采集失败"));
    } finally {
      setLoading(false);
    }
  }, [account, awemeId, api]);

  useEffect(() => { void load(); }, [load]);

  const sendOne = async (uid: string, nickname: string) => {
    if (!dmTpl.trim()) {
      push("请先填写私信文案", 4000);
      return;
    }
    setSendingUid(uid);
    try {
      const r = await api.crawlDm({ account, uid, text: dmTpl.trim() });
      const ok = r?.ok === true;
      setDmState((s) => ({ ...s, [uid]: ok ? "已发送" : `失败: ${r?.reason || "未知原因"}` }));
      push(ok ? `已向 ${nickname || uid} 发送私信` : `发送失败：${r?.reason || "未知原因"}`, 5000);
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

  return (
    <div className="flex h-full flex-col space-y-2">
      <div className="flex items-center gap-2">
        <MessageSquare className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        <span className="text-[0.78rem] font-medium">评论</span>
        <Badge variant="outline">{items.length}</Badge>
        {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
        <div className="flex-1" />
        <Button size="sm" variant="ghost" disabled={loading} onClick={() => void load()}>
          <RefreshCw className="h-3.5 w-3.5" />刷新
        </Button>
      </div>

      <Input
        value={dmTpl}
        onChange={(e) => setDmTpl(e.target.value)}
        placeholder="私信文案…"
      />

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-10 text-[0.78rem]
                        text-[var(--color-text-secondary)]">
          <Loader2 className="h-4 w-4 animate-spin" />评论采集中…
        </div>
      ) : err ? (
        // 如实呈现失败（风控拦截 ≠ 「本来就没评论」）
        <EmptyState
          title="评论采集失败"
          description={`${err} —— 这不是「没有评论」，请稍后重试或确认账号登录态。`}
          action={{ label: "重试", onClick: () => void load() }}
        />
      ) : items.length === 0 ? (
        <EmptyState title="该作品暂无评论" description="已成功请求，平台返回的评论列表为空。" />
      ) : (
        <div className="flex-1 space-y-1 overflow-y-auto">
          {items.map((c, i) => {
            const uid = String(c.uid || "");
            return (
              <div key={String(c.cid || i)} className="rounded border border-[var(--color-border)]">
                <CommentRow
                  c={c}
                  sending={sendingUid === uid}
                  state={dmState[uid]}
                  onSend={sendOne}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
