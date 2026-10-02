/**
 * 采集策略管理（ADR-018 F1-D3，2026-09-27 新建）。
 *
 * ## 设计意图
 *
 * 采集域此前**没有参数载体** —— 每次采集都在前端现填参数。本卡片把
 * 「一组可复用的采集参数」抽象成**策略对象**，对标直播域的
 * 「策略层（live_room_configs）↔ 实体层（live_rooms）」分离：
 *
 *   - **策略**（本卡片）= 怎么采（num / 排序 / 时段 / 翻页上限）；
 *   - **实体**（采什么）= 关键词 / aweme_id，由采集动作或定时任务给出。
 *
 * ⇒ 本层**零身份字段**，与 `live_config.py` 同一条设计约束。
 *
 * ## 风控提示（用户已定红线，勿删）
 *
 * 本卡片**只存参数**：不自动采集、不后台轮询。参数真正生效须由用户显式
 * 触发采集（或定时任务，其默认态见 ADR-018 D1「自动外发默认休眠」）。
 */
import { useEffect, useState } from "react";
import { api, CrawlPolicy } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Blank } from "@/components/page/kit";

const EMPTY: Partial<CrawlPolicy> = {
  name: "",
  kind: "video",
  num: 20,
  sort_type: "0",
  publish_time: "0",
  filter_duration: "",
  search_range: "",
  content_type: "",
  max_rounds: 20,
};

const KIND_LABEL: Record<string, string> = {
  video: "视频搜索",
  user: "用户搜索",
  comment: "评论采集",
};

const SORT_LABEL: Record<string, string> = {
  "0": "综合",
  "1": "最多点赞",
  "2": "最新",
};

const PUB_LABEL: Record<string, string> = {
  "0": "不限",
  "1": "一天内",
  "7": "一周内",
  "180": "半年内",
};

type Push = (msg: string) => void;

export default function CrawlPolicySection({
  push,
}: {
  push: Push;
}) {
  const [items, setItems] = useState<CrawlPolicy[]>([]);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<Partial<CrawlPolicy>>({ ...EMPTY });
  const [saving, setSaving] = useState(false);

  const load = () => {
    setLoading(true);
    api
      .listCrawlPolicies()
      .then((r) => setItems(r.items || []))
      .catch((e: unknown) => push("加载采集策略失败: " + errMsg(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    if (!draft.name?.trim()) {
      push("请先填写策略名称");
      return;
    }
    setSaving(true);
    try {
      const r = await api.saveCrawlPolicy(draft);
      if (!r.ok) {
        // 后端对非法枚举**明确拒绝**（不静默改写）⇒ 必须如实展示它的错误
        push("保存失败: " + (r.error || "未知原因"));
        return;
      }
      push("采集策略已保存");
      setDraft({ ...EMPTY });
      load();
    } catch (e: unknown) {
      push("保存失败: " + errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  const del = async (pid: string) => {
    try {
      await api.deleteCrawlPolicy(pid);
      push("已删除");
      load();
    } catch (e: unknown) {
      push("删除失败: " + errMsg(e));
    }
  };

  return (
    <div className="space-y-3">
      <div className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-raised)] px-3 py-2 text-[0.75rem] leading-relaxed text-[var(--color-text-secondary)]">
        采集策略 = <b>一组可复用的采集参数</b>（怎么采），不含关键词等身份信息。
        本卡片只存参数，<b>不会自动采集</b> —— 采集动作仍由你显式触发。
      </div>

      {/* 新建 / 编辑 */}
      <Card>
        <CardContent className="space-y-3 pt-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-[0.75rem]">策略名称</label>
              <Input
                value={draft.name || ""}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="如：保守-慢速"
                data-od-id="crawl-policy-name"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[0.75rem]">采集类型</label>
              <Select
                value={draft.kind || "video"}
                onChange={(v) => setDraft({ ...draft, kind: v as CrawlPolicy["kind"] })}
                options={Object.entries(KIND_LABEL).map(([k, v]) => ({ value: k, label: v }))}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[0.75rem]">每次上限（1-50）</label>
              <Input
                type="number"
                value={String(draft.num ?? 20)}
                onChange={(e) => setDraft({ ...draft, num: Number(e.target.value) })}
                data-od-id="crawl-policy-num"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[0.75rem]">排序</label>
              <Select
                value={draft.sort_type || "0"}
                onChange={(v) => setDraft({ ...draft, sort_type: v })}
                options={Object.entries(SORT_LABEL).map(([k, v]) => ({ value: k, label: v }))}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[0.75rem]">发布时段</label>
              <Select
                value={draft.publish_time || "0"}
                onChange={(v) => setDraft({ ...draft, publish_time: v })}
                options={Object.entries(PUB_LABEL).map(([k, v]) => ({ value: k, label: v }))}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[0.75rem]">
                翻页上限（1-100）
                <span className="ml-1 text-[var(--color-text-tertiary)]">
                  防死循环
                </span>
              </label>
              <Input
                type="number"
                value={String(draft.max_rounds ?? 20)}
                onChange={(e) => setDraft({ ...draft, max_rounds: Number(e.target.value) })}
                data-od-id="crawl-policy-rounds"
              />
            </div>
          </div>
          <div className="flex justify-end">
            <Button size="sm" onClick={save} disabled={saving} data-od-id="crawl-policy-save">
              {saving ? "保存中…" : "保存策略"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 列表 */}
      {loading ? (
        <Blank>加载中…</Blank>
      ) : items.length === 0 ? (
        <Blank>尚无采集策略 —— 上方新建一条即可在采集时复用其参数</Blank>
      ) : (
        <div className="space-y-2">
          {items.map((it) => (
            <div
              key={it.id}
              className="flex items-center justify-between rounded-[var(--radius-sm)] border border-[var(--color-border)] px-3 py-2"
            >
              <div className="min-w-0">
                <div className="truncate text-[0.8rem] font-medium">
                  {it.name || it.id}
                </div>
                <div className="truncate text-[0.72rem] text-[var(--color-text-secondary)]">
                  {KIND_LABEL[it.kind] || it.kind} · 每次 {it.num} ·{" "}
                  {SORT_LABEL[it.sort_type] || it.sort_type} ·{" "}
                  {PUB_LABEL[it.publish_time] || it.publish_time} · 翻页≤
                  {it.max_rounds}
                </div>
              </div>
              <Button variant="ghost" size="sm" onClick={() => del(it.id)}>
                删除
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * 极简原生 select 包装。
 *
 * 刻意**不用 shadcn Select**：本卡片的候选项是**静态枚举**（3~4 项），
 * 用原生 select 少一层依赖、少一个受控状态泄漏点（shadcn 的 Select
 * 在受控 + 动态 options 时需额外处理 `value` 为空的情况）。
 */
function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-9 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-[0.78rem] outline-none focus:border-[var(--color-accent)]"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function errMsg(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
