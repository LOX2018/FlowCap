/**
 * `HighValueKeywordsSection` —— 高价值关键词权重表（2026-09-29 新增）。
 *
 * ## 为什么需要（缺口取证）
 *
 * 数据层 `services/high_value_keywords.py`（134 行 + 27 个种子词）与消费点
 * `services/dm_dispatch.py:843` 一直在用，但 **API 与前端零暴露**
 * （全仓 grep 对 `high_value` 在 `api/`、`main.py`、`frontend/src` 均零命中）
 * ⇒ 用户**无法调整关键词权重**。这与 H-26（MCP 工具族「只交付能力层、
 * 丢呈现层」）是**同型故障**。
 *
 * 该缺口的业务后果直接可见：关键词权重 → `is_high_value` 判定 →
 * 窗口时长（`high_value_window_seconds`）→ 最终是否被发送闸门拦下。
 * 实测「直播捕获目标 100% 发送失败」即由「窗口 > 延迟」造成，
 * 而关键词表不可调使该问题**无从在 UI 侧缓解**。
 *
 * ## 设计约定
 *
 * 1. **不塞进 schema**：`app_config_schema` 只支持 int/bool/float/str/select，
 *    承载不了 `{词: 权重}` 映射，故按黑名单同款范式开独立端点与卡片。
 * 2. **权重语义显式**：权重越高越强指向「有效咨询」；0 分不命中。
 *    阈值（`high_value_score_threshold`）为 0 时该表**完全不参与判定**
 *    （关闭筛查），这一点必须在卡内写清，否则用户会以为改词没生效。
 * 3. **可清空**：清空 = 不按关键词过滤（服务层已修「空表被当成缺失」的
 *    懒初始化缺陷，否则用户永远清不掉）。
 *
 * ## 2026-09-29 修复（OCR[23][24][25][27]）
 *
 * · **[23]/[24] 保存不再静默丢行**：原实现 `if (!w) continue` /
 *   `parseInt` NaN `continue` / 重词 `out[w]=n` 覆盖，
 *   保存后 draft 由服务端回读 ⇒ 被丢的行**无声消失**，而 toast 报的是
 *   服务端条数，与用户屏幕上的行数不符。现改为**保存前校验**：
 *   空关键词 / 非整数权重 / 与前面行重复 ⇒ **阻止保存**，高亮这些行并在
 *   卡内列出「第 N 行 + 原因」，用户修正或删除后再保存。
 *   ⇒ 提交集合 == 用户所见行集合，toast 数量因此必然一致（另附防御性核对）。
 * · **[25] 稳定行 id**：原 `key={i}` 配 `filter` 删除 ⇒ React 按下标复用
 *   DOM 节点，删中间行后输入框焦点/输入态错位。现每行带 `id`（同页
 *   TagSection 的 `key={t.id}` 做法），增删改一律按 id 定位。
 * · **[27] 恢复默认加确认**：与同页 AgentSection / ProviderSection /
 *   TagSection 的破坏性操作一致，reset 前 `confirm(...)`。
 */

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/api/client";
import { confirmDialog } from "@/components/ui/modal";

/** draft 的一行：`id` 为稳定身份（React key 与增删改定位都用它）。 */
export type DraftRow = { id: string; word: string; weight: string };

/** 一行不合格的原因。 */
export type IssueKind = "empty_word" | "bad_weight" | "dup_word";
export type SaveIssue = { id: string; kind: IssueKind };

/** 校验结论：可直接提交的映射 + 不合格行清单（空清单才允许提交）。 */
export type SaveCheck = { out: Record<string, number>; issues: SaveIssue[] };

/** 原因文案（抽出以便被门禁脚本直接引用，保证提示与判据同源）。 */
export function issueLabel(kind: IssueKind): string {
  switch (kind) {
    case "empty_word":
      return "关键词为空";
    case "bad_weight":
      return "权重必须是整数";
    case "dup_word":
      return "关键词与前面的行重复";
  }
}

/**
 * 保存前校验（纯函数，可单测）：**不丢任何行**，只把它们分类。
 *
 * 与旧实现的差别（这正是缺陷点）：
 *   旧：`if (!w) continue` / `if (Number.isNaN(n)) continue` / `out[w]=n`
 *       ⇒ 空行、`parseInt` 解析失败的行、重词行全部**静默消失**。
 *   新：不合格行进 `issues`，由调用方**阻止保存并提示**，绝不静默丢弃。
 *
 * 权重判据用**整串**匹配（`/^[+-]?\d+$/`）而非 `parseInt` ——
 * `parseInt("3个")` 会得到 3，那种「部分解析成功」同样是静默改写用户输入。
 */
export function checkDraft(rows: DraftRow[]): SaveCheck {
  const out: Record<string, number> = {};
  const issues: SaveIssue[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const w = row.word.trim();
    if (!w) {
      issues.push({ id: row.id, kind: "empty_word" });
      continue;
    }
    const raw = row.weight.trim();
    if (!/^[+-]?\d+$/.test(raw)) {
      issues.push({ id: row.id, kind: "bad_weight" });
      continue;
    }
    const n = Number.parseInt(raw, 10);
    if (!Number.isSafeInteger(n)) {
      issues.push({ id: row.id, kind: "bad_weight" });
      continue;
    }
    if (seen.has(w)) {
      issues.push({ id: row.id, kind: "dup_word" });
      continue;
    }
    seen.add(w);
    out[w] = n;
  }
  return { out, issues };
}

/**
 * @param embedded 供弹窗内嵌使用：去掉外层 `mt-3` 外边距与圆角（弹窗自身已有壳）。
 */
export default function HighValueKeywordsSection({
  embedded = false,
}: {
  embedded?: boolean;
} = {}) {
  const [items, setItems] = useState<Record<string, number>>({});
  const [draft, setDraft] = useState<DraftRow[]>([]);
  /** 被判为不合格的行（高亮用）；用户一改该行即刻清除。 */
  const [badIds, setBadIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const seq = useRef(0);

  const nextId = () => `r${(seq.current += 1)}`;

  const toDraft = (o: Record<string, number>): DraftRow[] =>
    Object.entries(o || {}).map(([word, weight]) => ({
      id: nextId(),
      word,
      weight: String(weight),
    }));

  const load = async () => {
    setErr("");
    try {
      const r = await api.aiHighValueKeywords();
      setItems(r.items || {});
      setDraft(toDraft(r.items || {}));
      setBadIds([]);
    } catch (e) {
      setErr("读取失败: " + (e instanceof Error ? e.message : String(e)));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const save = async () => {
    setMsg("");
    setErr("");
    // 保存前校验：有不合格行就**不提交**，只高亮 + 列出原因（不静默丢行）。
    const { out, issues } = checkDraft(draft);
    if (issues.length > 0) {
      setBadIds(issues.map((it) => it.id));
      const lineNo = new Map(draft.map((r, i) => [r.id, i + 1]));
      setErr(
        `未保存：有 ${issues.length} 行需要修正 —— ` +
          issues
            .map((it) => `第 ${lineNo.get(it.id) ?? "?"} 行 ${issueLabel(it.kind)}`)
            .join("；") +
          "。请修正或删除这些行后再保存。",
      );
      return;
    }
    setBadIds([]);
    setBusy(true);
    const sent = Object.keys(out).length;
    try {
      const r = await api.aiHighValueKeywordsSave(out);
      const got = r.items || {};
      setItems(got);
      setDraft(toDraft(got));
      const gotN = Object.keys(got).length;
      // 防御性核对：提交数 == 用户所见行数；若服务端归一化后数量变了，
      // 明说差异，而不是报一个与用户所见不符的数字。
      setMsg(
        gotN === sent
          ? `已保存 ${gotN} 个关键词`
          : `已保存，但服务端返回 ${gotN} 个（本次提交 ${sent} 个），请核对列表`,
      );
    } catch (e) {
      setErr("保存失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (
      !(await confirmDialog({
        message:
          "确认恢复默认关键词表？你自定义的全部关键词与权重会被覆盖为「工伤业务域种子词表」，且无法撤销。",
        danger: true,
      }))
    ) {
      return;
    }
    setBusy(true);
    setMsg("");
    setErr("");
    try {
      const r = await api.aiHighValueKeywordsReset();
      setItems(r.items || {});
      setDraft(toDraft(r.items || {}));
      setBadIds([]);
      setMsg("已恢复默认（工伤业务域种子词表）");
    } catch (e) {
      setErr("重置失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBusy(false);
    }
  };

  const addRow = () =>
    setDraft((d) => [...d, { id: nextId(), word: "", weight: "3" }]);
  const delRow = (id: string) => setDraft((d) => d.filter((r) => r.id !== id));
  const setRow = (
    id: string,
    patch: Partial<{ word: string; weight: string }>,
  ) => {
    setDraft((d) => d.map((r) => (r.id === id ? { ...r, ...patch } : r)));
    // 用户已动手改这一行 ⇒ 撤掉它的高亮（否则提示与画面脱节）
    setBadIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : ids));
  };

  return (
    <Card className={embedded ? "" : "mt-3"}>
      <CardContent className="pt-4">
        <div className="mb-2 flex items-center justify-between">
          <div>
            <div className="text-[0.9rem] font-semibold text-[var(--color-text)]">
              高价值关键词权重
            </div>
            <div className="mt-0.5 text-[0.72rem] text-[var(--color-text-muted)]">
              弹幕/评论命中即累加权重；权重越高越强指向有效咨询。
              阈值（send 节「高价值关键词阈值」）为 <b>0</b> 时本表不参与判定。
            </div>
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" onClick={addRow} disabled={busy}>
              添加
            </Button>
            <Button size="sm" variant="secondary" onClick={reset} disabled={busy}>
              恢复默认
            </Button>
            <Button size="sm" onClick={save} disabled={busy}>
              {busy ? "保存中…" : "保存"}
            </Button>
          </div>
        </div>

        {(msg || err) && (
          <div
            className={
              "mb-2 rounded px-2 py-1 text-[0.74rem] " +
              (err
                ? "bg-[var(--color-danger)]/10 text-[var(--color-danger)]"
                : "bg-[var(--color-ok)]/10 text-[var(--color-ok)]")
            }
          >
            {err || msg}
          </div>
        )}

        <div className="max-h-[320px] overflow-auto rounded border border-[var(--color-border)]">
          <table className="w-full text-[0.76rem]">
            <thead className="sticky top-0 bg-[var(--color-surface)]">
              <tr className="text-left text-[var(--color-text-muted)]">
                <th className="px-2 py-1">关键词</th>
                <th className="w-[110px] px-2 py-1">权重</th>
                <th className="w-[70px] px-2 py-1"></th>
              </tr>
            </thead>
            <tbody>
              {draft.length === 0 && (
                <tr>
                  <td
                    colSpan={3}
                    className="px-2 py-3 text-center text-[var(--color-text-muted)]"
                  >
                    当前为空 —— 保存后表示「不按关键词过滤」
                  </td>
                </tr>
              )}
              {draft.map((row) => {
                const bad = badIds.includes(row.id);
                return (
                  <tr
                    key={row.id}
                    className={
                      "border-t border-[var(--color-border)]" +
                      (bad ? " bg-[var(--color-danger)]/10" : "")
                    }
                  >
                    <td className="px-2 py-1">
                      <input
                        className={
                          "w-full bg-transparent outline-none" +
                          (bad ? " placeholder:text-[var(--color-danger)]" : "")
                        }
                        value={row.word}
                        placeholder={bad ? "（此行需修正）" : "如：工伤认定"}
                        onChange={(e) =>
                          setRow(row.id, { word: e.target.value })
                        }
                      />
                    </td>
                    <td className="px-2 py-1">
                      <input
                        className="w-full bg-transparent outline-none"
                        value={row.weight}
                        inputMode="numeric"
                        onChange={(e) =>
                          setRow(row.id, { weight: e.target.value })
                        }
                      />
                    </td>
                    <td className="px-2 py-1 text-right">
                      <button
                        className="text-[var(--color-danger)] hover:underline"
                        onClick={() => delRow(row.id)}
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="mt-2 text-[0.7rem] text-[var(--color-text-muted)]">
          共 {Object.keys(items).length} 个已生效关键词（保存后即时生效，无需重启）。
        </div>
      </CardContent>
    </Card>
  );
}
