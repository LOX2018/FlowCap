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
 */

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/api/client";

export default function HighValueKeywordsSection() {
  const [items, setItems] = useState<Record<string, number>>({});
  const [draft, setDraft] = useState<{ word: string; weight: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const toDraft = (o: Record<string, number>) =>
    Object.entries(o || {}).map(([word, weight]) => ({
      word,
      weight: String(weight),
    }));

  const load = async () => {
    setErr("");
    try {
      const r = await api.aiHighValueKeywords();
      setItems(r.items || {});
      setDraft(toDraft(r.items || {}));
    } catch (e) {
      setErr("读取失败: " + (e instanceof Error ? e.message : String(e)));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const save = async () => {
    setBusy(true);
    setMsg("");
    setErr("");
    const out: Record<string, number> = {};
    for (const row of draft) {
      const w = row.word.trim();
      if (!w) continue;
      const n = parseInt(row.weight, 10);
      if (Number.isNaN(n)) continue; // 非法权重丢弃（与服务层同语义）
      out[w] = n;
    }
    try {
      const r = await api.aiHighValueKeywordsSave(out);
      setItems(r.items || {});
      setDraft(toDraft(r.items || {}));
      setMsg(`已保存 ${Object.keys(r.items || {}).length} 个关键词`);
    } catch (e) {
      setErr("保存失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    setMsg("");
    setErr("");
    try {
      const r = await api.aiHighValueKeywordsReset();
      setItems(r.items || {});
      setDraft(toDraft(r.items || {}));
      setMsg("已恢复默认（工伤业务域种子词表）");
    } catch (e) {
      setErr("重置失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBusy(false);
    }
  };

  const addRow = () => setDraft((d) => [...d, { word: "", weight: "3" }]);
  const delRow = (i: number) =>
    setDraft((d) => d.filter((_, idx) => idx !== i));
  const setRow = (i: number, patch: Partial<{ word: string; weight: string }>) =>
    setDraft((d) => d.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));

  return (
    <Card className="mt-3">
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
              {draft.map((row, i) => (
                <tr key={i} className="border-t border-[var(--color-border)]">
                  <td className="px-2 py-1">
                    <input
                      className="w-full bg-transparent outline-none"
                      value={row.word}
                      placeholder="如：工伤认定"
                      onChange={(e) => setRow(i, { word: e.target.value })}
                    />
                  </td>
                  <td className="px-2 py-1">
                    <input
                      className="w-full bg-transparent outline-none"
                      value={row.weight}
                      inputMode="numeric"
                      onChange={(e) => setRow(i, { weight: e.target.value })}
                    />
                  </td>
                  <td className="px-2 py-1 text-right">
                    <button
                      className="text-[var(--color-danger)] hover:underline"
                      onClick={() => delRow(i)}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
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
