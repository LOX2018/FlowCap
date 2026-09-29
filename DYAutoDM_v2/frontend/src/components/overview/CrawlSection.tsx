/**
 * 采集与内容（总览页 · L2-① 入口分区）—— ADR-032
 *
 * ## 设计意图（为什么总览必须有这一块）
 *
 * 用户 2026-09-30 反馈：「**目前项目内拥有的采集功能都没有进行体现**」。
 * 采集是获客漏斗的**入口环节**（先找到人 → 才有评论/私信可发），
 * 侧栏有独立「采集」页、后端有 `crawl_history` 完整落库，
 * 但总览页此前**零体现** —— 用户打开首页看不到漏斗的起点。
 *
 * ## 数据来源（只读既有端点，零新采集）
 *
 * - `GET /api/crawl/history?limit=N`（client: `api.getCrawlHistory()`）
 *   → `crawl_history` 表：account / kind / keyword / target / result_count / ts。
 * - 今日聚合数（轮次/结果条数）由 `/api/overview/funnel` 提供（props 传入），
 *   避免本组件重复统计。
 *
 * ## 风控铁律（继承）
 *
 * 纯读库，**零网络零浏览器**，不触发任何采集 / 昵称查询。
 * 本组件只列**历史**，绝不代用户发起采集（那必须回「采集」页显式触发）。
 *
 * ## 铁律：禁止假成功
 *
 * `ok=false`（后端读库失败）必须如实暴露，**不得**渲染成「暂无采集记录」。
 */
import { useQuery } from "@tanstack/react-query";
import { Film, MessageCircle, User as UserIcon, ArrowUpRight } from "lucide-react";
import { PageProps } from "../../api/client";
import { Section, Row, RowText, Tone, SkeletonRows, Blank, KeyValue } from "@/components/page/kit";

/** 采集类型 → 中文 + 图标（`crawl_history.kind`）。 */
const KIND_META: Record<string, { label: string; Icon: typeof Film }> = {
  video: { label: "视频", Icon: Film },
  comment: { label: "评论", Icon: MessageCircle },
  user: { label: "用户", Icon: UserIcon },
};

interface CrawlHistoryItem {
  id: number;
  account?: string;
  kind?: string;
  keyword?: string;
  target?: string;
  result_count?: number;
  ts?: string;
}

/** 今日采集摘要（由 `/api/overview/funnel` 传入，避免本组件重复聚合）。 */
export interface CrawlToday {
  runs: number;
  results: number;
  kinds: Record<string, number>;
}

const MAX_SHOW = 5;

function fmtTs(ts?: string): string {
  const s = String(ts || "");
  return s.length > 16 ? s.slice(5, 16) : s || "—";
}

export default function CrawlSection({
  props,
  today,
  dayLabel,
}: {
  props: PageProps;
  /** 今日（或回落日）采集摘要；为空则只显示历史列表 */
  today?: CrawlToday | null;
  /** 统计日标签，用于说明「今日」还是回落的日期 */
  dayLabel?: string;
}) {
  const { api, ready } = props;

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["crawl-history", MAX_SHOW],
    // 契约见 client.ts::crawlHistory —— 返回 {ok, items}，**无 error 字段**
    queryFn: () =>
      api.crawlHistory(MAX_SHOW) as unknown as Promise<{
        ok: boolean;
        items?: CrawlHistoryItem[];
      }>,
    refetchInterval: 30000,
    enabled: !!ready,
  });

  const ok = data?.ok === true;
  const items = (Array.isArray(data?.items) ? data!.items! : []).slice(0, MAX_SHOW);

  return (
    <Section
      title="采集与内容"
      description="获客漏斗入口 · 只读历史，发起采集在「采集」页"
      data-od-id="overview-crawl"
      actions={
        today && (today.runs > 0 || today.results > 0) ? (
          <Tone tone="info">
            {dayLabel || "今日"} {today.runs} 轮 · {today.results} 条
          </Tone>
        ) : null
      }
    >
      {isLoading ? (
        <SkeletonRows rows={3} />
      ) : isError ? (
        <Blank>
          读取采集记录失败：{(error as Error)?.message || "后端无响应"}
          <br />
          <span className="text-[0.72rem]">数据源 GET /api/crawl/history —— 请确认后端已启动。</span>
        </Blank>
      ) : !ok ? (
        // 后端明确 ok=false：读库失败，必须如实暴露（不得显示成「暂无记录」）
        <Blank>
          后端未能取到采集记录 —— 不代表没有采集过。
          <br />
          <span className="text-[0.72rem]">数据源 GET /api/crawl/history 返回 ok=false。</span>
        </Blank>
      ) : (
        <div className="space-y-2.5">
          {/* 按类型的当日分布（只有真有时才显示，避免一排 0） */}
          {today && Object.keys(today.kinds || {}).length ? (
            <KeyValue
              cols={3}
              items={Object.entries(today.kinds).map(([k, v]) => ({
                k: (KIND_META[k] || { label: k }).label + " 轮次",
                v: String(v),
                mono: true,
              }))}
            />
          ) : null}

          {!items.length ? (
            <Blank>暂无采集记录 —— 可在「采集」页开始关键词搜索或评论采集。</Blank>
          ) : (
            <div className="space-y-1.5">
              {items.map((it) => {
                const meta = KIND_META[String(it.kind || "")] || {
                  label: String(it.kind || "未知"),
                  Icon: Film,
                };
                const { Icon } = meta;
                // 评论类无关键词，展示目标作品；视频/用户类展示关键词
                const subject =
                  String(it.kind) === "comment"
                    ? it.target
                      ? `作品 ${it.target}`
                      : "指定作品"
                    : it.keyword || "无关键词";
                return (
                  <Row key={it.id}>
                    <Icon className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
                    <RowText
                      primary={`${it.account || "未指定账号"} · ${subject}`}
                      secondary={`${fmtTs(it.ts)} · ${meta.label}`}
                      mono
                    />
                    <Tone tone="mute">{it.result_count ?? 0} 条</Tone>
                  </Row>
                );
              })}
              {props.setTab ? (
                <button
                  type="button"
                  onClick={() => props.setTab!("crawl")}
                  className="inline-flex items-center gap-1 text-[0.72rem] text-[var(--color-text-muted)] underline-offset-2 hover:underline"
                >
                  去采集页
                  <ArrowUpRight className="h-3 w-3" />
                </button>
              ) : null}
            </div>
          )}
        </div>
      )}
    </Section>
  );
}
