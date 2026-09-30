/**
 * 贡献榜卡（2026-09-30 新增）——「房间热度」卡片下方那块预留区域。
 *
 * ## 数据源与为什么不新增请求
 * 数据来自 `/api/live/stream` 的 `contribution_rank` 字段：后端 `LiveChatHook` 在
 * WS 生命周期内**后台轮询**上游 `/webcast/ranklist/audience/`（上游
 * `douyin_api.py:1799 get_live_contribution_rank`）写入。前端**不新增任何请求**——
 * 既复用 `App.tsx` 已有的 3s 常驻轮询（`queryKey:["live-stream"]`），也避免改
 * 正被并发会话占用的 `api/client.ts`。
 *
 * ## 空态必须如实
 * 拿不到榜单时显示后端下发的 `rankReason`（未启动监听 / 拉取中 / 需登录 …），
 * **绝不假装「无贡献者」**（与项目「受理≠送达」同族的可观测纪律）。
 */
import { Heart } from "lucide-react";

import { Section, Blank } from "@/components/page/kit";
import { Avatar, hue } from "../../components/ui";

import type { RankUser } from "./live-shared";

/** 数字缩写（口径与项目其它处一致：≥1万 折成 X.X万） */
function fmtScore(n: number, text?: string): string {
  if (text) return text;
  if (!n) return "0";
  if (n >= 10000) return (n / 10000).toFixed(1) + "万";
  return n.toLocaleString();
}

/** 把后端下发的 rankReason **码**映射为人类可读文案（前端不推断，只翻译）。 */
const REASON_TEXT: Record<string, string> = {
  empty: "暂时取不到贡献榜数据",
  idle: "贡献榜等待中",
  "拉取中": "贡献榜加载中…",
  "未启动监听": "未启动监听 · 开播后自动加载贡献榜",
  "未启动": "未启动监听 · 开播后自动加载贡献榜",
};
function reasonText(reason?: string): string {
  if (!reason) return "暂无贡献榜数据";
  return REASON_TEXT[reason] || reason;
}

interface Props {
  rank: RankUser[];
  reason?: string;
}

export default function ContributionRankCard({ rank, reason }: Props) {
  return (
    <Section
      data-od-id="live-rank"
      title="贡献榜"
      description={rank.length ? `Top ${rank.length} · 每分钟刷新` : "直播间贡献排行"}
    >
      {rank.length === 0 ? (
        <Blank>
          <span className="text-[1.4rem]">🏆</span>
          {reasonText(reason)}
          <br />
          <span className="text-[0.72rem]">
            贡献榜依赖登录态与主播身份，开播后由后台每分钟刷新。
          </span>
        </Blank>
      ) : (
        <div className="flex max-h-[236px] flex-col gap-0.5 overflow-y-auto overscroll-contain">
          {rank.map((u, i) => (
            <div
              key={u.uid || i}
              className="flex items-center gap-2.5 rounded-[var(--radius-sm)] px-2 py-[6px]
                         transition-colors hover:bg-[var(--color-surface-raised)]"
            >
              <span
                className={
                  "w-[22px] shrink-0 text-center font-mono text-[0.78rem] font-semibold tabular-nums " +
                  (u.rank <= 3
                    ? "text-[var(--color-warning)]"
                    : "text-[var(--color-text-muted)]")
                }
              >
                {u.rank}
              </span>
              <Avatar name={u.nickname || "—"} h={hue((u.nickname || "—").length)} sm />
              <span className="min-w-0 flex-1 truncate text-[0.82rem] text-[var(--color-text)]">
                {u.nickname || u.uid || "—"}
              </span>
              <span
                className="shrink-0 font-mono text-[0.76rem] text-[var(--color-text-secondary)]"
                title={`贡献值 ${u.score}`}
              >
                <Heart className="mr-1 inline h-3 w-3 text-[var(--color-danger)]" />
                {fmtScore(u.score, u.score_text)}
              </span>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}
