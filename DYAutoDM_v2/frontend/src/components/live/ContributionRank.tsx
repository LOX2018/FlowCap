/**
 * 贡献榜（内联面板，2026-09-30）——嵌在「房间热度」卡内、热度曲线**下方**。
 *
 * ## 为什么是内联面板而非独立卡片
 * 用户指定落点：`[data-od-id="heat-chart"] > div:nth-of-type(2) > div.relative > svg > path` 下方，
 * 即热度曲线 SVG 的**正下方**，不另立卡片（原独立 Card 会挤占右列、与热度卡分离）。
 *
 * ## 数据源
 * 来自 `/api/live/stream` 的 `contribution_rank` 字段：后端 `LiveChatHook` 在 WS 生命周期内
 * **后台轮询**上游 `/webcast/ranklist/audience/`（上游 `douyin_api.py:1799`）。前端不新增请求。
 *
 * ## 空态必须如实
 * 拿不到榜单时显示后端下发的 `rankReason` 翻译，**绝不假装「无贡献者」**。
 */
import { Heart } from "lucide-react";

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

export default function ContributionRank({ rank, reason }: Props) {
  return (
    <div className="mt-3 border-t border-[var(--color-border)] pt-2.5" data-od-id="live-rank">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-[0.82rem] font-semibold tracking-tight text-[var(--color-text)]">
          贡献榜
        </span>
        <span className="font-mono text-[0.7rem] text-[var(--color-text-muted)]">
          {rank.length ? `Top ${rank.length} · 每分钟刷新` : ""}
        </span>
      </div>
      {rank.length === 0 ? (
        <div className="py-3 text-center text-[0.72rem] text-[var(--color-text-muted)]">
          {reasonText(reason)}
        </div>
      ) : (
        <div className="flex max-h-[168px] flex-col gap-0.5 overflow-y-auto overscroll-contain">
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
    </div>
  );
}
