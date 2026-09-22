/**
 * 采集面板 —— 内容浏览的「高级/采集」模式
 *
 * ## 为什么有这个组件（用户 2026-09-15 决策）
 *
 * 「采集页面本质是内容浏览的升级版」→ 已确认：**采集页合并进内容浏览，
 * 作为其高级模式**。
 *
 * 实施原则（避免破坏既有能力）：
 *   · **不重写**采集逻辑（搜索参数 / 发送闸门 / 批量筛选语义全部保持）
 *   · 把"采集动作"做成**可复用面板**，内容浏览页在其搜索结果上直接挂载，
 *     采集页（独立页）亦可引用 —— 两边共用同一份实现，不会分叉。
 *
 * ## 链路
 * 关键词搜视频 → 选中作品 → 采集评论区 → 评论用户一键私信截流
 */
import { useState } from "react";
import { MessageSquare, Send, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
interface CommentItem {
  uid?: string;
  nickname?: string;
  text?: string;
  cid?: string;
  [k: string]: unknown;
}

interface Props {
  account: string;
  /** 采集用的 API 客户端（来自 PageProps.api） */
  api: {
    crawlComments(body: unknown): Promise<{ ok?: boolean; items?: CommentItem[] }>;
    crawlDm(body: unknown): Promise<{ ok?: boolean; msg?: string }>;
    /** ★ 契约（2026-09-21）：入参是 **aweme_id**，不是 uids
     *  —— 后端按作品重新采集评论区并批量私信，返回候选/成功/失败计数。 */
    crawlBatch(body: unknown): Promise<{
      ok?: boolean;
      candidates?: number;
      sent_ok?: number;
      sent_fail?: number;
      rate_limited?: number;
      results?: { uid: string; nickname: string; ok: boolean; reason: string }[];
    }>;
  };
  /** 当前选中的作品 aweme_id（由内容浏览传入） */
  awemeId: string;
  /** toast 提示 */
  push: (msg: string, holdMs?: number) => void;
}

export function CrawlPanel({ account, api, awemeId, push }: Props) {
  const [cmts, setCmts] = useState<CommentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [dmTpl, setDmTpl] = useState("你好，看到你评论了我的内容，想和你聊聊～");
  const [batching, setBatching] = useState(false);
  const [dmState, setDmState] = useState<Record<string, string>>({});

  const load = async () => {
    if (!awemeId) {
      push("请先选择一个作品");
      return;
    }
    setLoading(true);
    try {
      const r = await api.crawlComments({ account, aweme_id: awemeId, limit: 100 });
      setCmts(r?.items || []);
      push(`采集到 ${(r?.items || []).length} 条评论`, 4000);
    } catch (e) {
      push(`采集失败: ${(e as Error)?.message || "未知错误"}`, 6000);
    } finally {
      setLoading(false);
    }
  };

  const sendOne = async (uid: string) => {
    if (!uid) return;
    setDmState((s) => ({ ...s, [uid]: "发送中…" }));
    try {
      const r = await api.crawlDm({ account, uid, text: dmTpl });
      setDmState((s) => ({ ...s, [uid]: r?.ok ? "已发送" : `失败: ${r?.msg || ""}` }));
    } catch (e) {
      setDmState((s) => ({ ...s, [uid]: `异常: ${(e as Error)?.message || ""}` }));
    }
  };

  const batch = async () => {
    // ★ 2026-09-21 修复：原实现传 `{ uids }`，而后端 `CrawlBatchRequest`
    //   的契约是 `{ aweme_id, text, keyword, limit, max_send, interval }`
    //   —— `uids` 是未声明字段，pydantic 直接 **422**，批量私信必然失败。
    //   后端语义是按「作品」重新采集评论区并发私信（含去重/关键词筛选），
    //   故改为传 awemeId；候选人数与成功数由后端返回。
    if (!awemeId) {
      push("请先选择一个作品");
      return;
    }
    setBatching(true);
    try {
      const r = await api.crawlBatch({
        account,
        aweme_id: awemeId,
        text: dmTpl,
        limit: 200,
        max_send: 0,
        interval: 0,
      });
      push(
        `批量完成：候选 ${r.candidates} · 成功 ${r.sent_ok} · 失败 ${r.sent_fail} · 限流 ${r.rate_limited}`,
        8000,
      );
      // 回写每条评论作者的发送状态
      const ns: Record<string, string> = {};
      (r.results || []).forEach((x) => {
        ns[x.uid] = x.ok ? "已发送" : `失败: ${x.reason || ""}`;
      });
      setDmState((s) => ({ ...s, ...ns }));
    } catch (e) {
      push(`批量异常: ${(e as Error)?.message || ""}`, 8000);
    } finally {
      setBatching(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-3 p-3">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={load} disabled={loading}>
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MessageSquare className="h-3.5 w-3.5" />}
            采集评论区
          </Button>
          <Button size="sm" onClick={batch} disabled={batching || !cmts.length}>
            {batching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
            批量私信（{cmts.length}）
          </Button>
          <Badge variant="outline">{awemeId ? `作品 ${awemeId.slice(-8)}` : "未选作品"}</Badge>
        </div>

        <input
          value={dmTpl}
          onChange={(e) => setDmTpl(e.target.value)}
          placeholder="私信模板…"
          className="w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5 text-[0.78rem] outline-none"
        />

        {cmts.length > 0 && (
          <div className="max-h-64 space-y-1.5 overflow-y-auto">
            {cmts.slice(0, 50).map((c, i) => {
              const uid = String(c.uid || "");
              return (
                <div
                  key={c.cid || i}
                  className="flex items-center justify-between gap-2 rounded border border-[var(--border)] px-2 py-1.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[0.75rem] font-medium">
                      {c.nickname || uid || "匿名"}
                    </div>
                    <div className="truncate text-[0.7rem] text-[var(--color-text-muted)]">
                      {c.text || ""}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <span className="text-[0.65rem] text-[var(--color-text-muted)]">
                      {dmState[uid] || ""}
                    </span>
                    <Button size="sm" variant="ghost" onClick={() => sendOne(uid)} disabled={!uid}>
                      私信
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
