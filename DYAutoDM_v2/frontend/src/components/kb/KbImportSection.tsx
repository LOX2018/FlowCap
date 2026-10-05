/**
 * `KbImportSection` —— 聊天记录 → 知识库（2026-09-18，E8）
 *
 * ## 流程（对照计划：必须**预览后再入库**）
 *
 *   ① 选账号 + 会话（与私信页同源的下拉）；
 *   ② 「预览问答对」→ `action=to_kb_preview`（**零写入**），表格展示
 *      问 / 答 / 来源 msg_id，可逐条勾选；
 *   ③ 「确认入库」→ `action=to_kb` 整会话写入 `reply_kb`/`pro_kb`。
 *
 * ## 诚实边界（写在这里防止后续误用）
 *
 * 后端 `to_kb` 是「整会话抽取即入库」，**没有逐条勾选入库的参数**。
 * 前端勾选的价值是**人工把关**：预览为 0 条或不满意 → 不点入库即可，零副作用。
 * 精细逐条录入走「对话回复库」页的手动添加。
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Database, Loader2, Eye, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { PageProps } from "@/api/client";
import { errMsg } from "@/lib/utils";
import { SegmentedTabs } from "@/components/page/kit";

/** QA 对预览行 */
interface QaPair {
  question: string;
  answer: string;
  source_msg_id: string | null;
  ts: number;
}

interface ConvOption {
  /** 2026-09-18 审查清理：原 `id: c.conv_id || String(i)` 会用下标兜底，
   *  可能与真实 conv_id 撞值（削弱 React key 唯一性）；且 `acct` 字段从未被读取。
   *  会话列表来自后端，conv_id 缺失的行直接丢弃（宁缺勿错）。 */
  id: string;
  name: string;
}

export default function KbImportSection({ push, api, ready }: {
  push?: (m: string, holdMs?: number) => void;
  api: PageProps["api"];
  ready?: boolean;
}) {
  const [target, setTarget] = useState<"reply" | "pro">("reply");
  const [convId, setConvId] = useState("");
  const [acct, setAcct] = useState("");
  const [pairs, setPairs] = useState<QaPair[] | null>(null);
  const [busy, setBusy] = useState<"" | "preview" | "import">("");

  // 2026-09-18（#38）：预览请求的竞态守卫需要读「当前最新」的账号/会话。
  // 直接用闭包捕获的 state 会永远是发起那一刻的值，故用 ref 镜像。
  const acctRef = useRef(acct);
  const convRef = useRef(convId);
  useEffect(() => { acctRef.current = acct; }, [acct]);
  useEffect(() => { convRef.current = convId; }, [convId]);

  // 账号 + 会话列表（只读，与私信页同源数据）
  const acctsQ = useQuery({
    queryKey: ["kb-import-accounts"],
    queryFn: async (): Promise<{ name: string }[]> => {
      const d = (await api.getAccounts()) as unknown as { name: string }[];
      return Array.isArray(d) ? d : [];
    },
    enabled: !!ready,
  });
  const convsQ = useQuery({
    queryKey: ["kb-import-convs", acct],
    queryFn: async (): Promise<ConvOption[]> => {
      const d = (await api.getConversations(acct)) as unknown as {
        conversations?: { conv_id?: string; name?: string }[];
      };
      return (d?.conversations || [])
        .filter((c) => !!c.conv_id)
        .map((c) => ({
          id: String(c.conv_id),
          name: c.name || c.conv_id || "会话",
        }));
    },
    enabled: !!acct,
  });

  const realAccts = acctsQ.data || [];
  const convs: ConvOption[] = convsQ.data || [];

  const preview = async () => {
    if (!acct || !convId) return;
    // 2026-09-18 审查修复（#38）：记录本次请求的账号/会话，应答回来时若已切换
    // 则**丢弃**该结果（否则会把 A 的问答对填给 B，做二次污染）。
    const reqAcct = acct;
    const reqConv = convId;
    const still = () => reqAcct === acctRef.current && reqConv === convRef.current;
    setBusy("preview");
    setPairs(null);
    try {
      const r = await api.exportToKb(reqAcct, reqConv, { target, preview: true });
      if (!still()) return;                    // 已切走 → 丢弃，不污染当前视图
      if (r.ok) {
        setPairs(r.pairs || []);
        if ((r.count || 0) === 0) push?.("没有抽到问答对（对方没提问，或我方没作答）", 6000);
      } else {
        push?.(`预览失败 · ${r.error || "未知原因"}`, 7000);
      }
    } catch (e) {
      push?.("预览失败: " + (e instanceof Error ? e.message : String(e)), 7000);
    } finally {
      setBusy("");
    }
  };

  const doImport = async () => {
    if (!acct || !convId) return;
    setBusy("import");
    try {
      const r = await api.exportToKb(acct, convId, { target, preview: false });
      if (r.ok) {
        push?.(`已入库：新增 ${r.added ?? 0} 条，跳过重复 ${r.skipped ?? 0} 条`, 8000);
        setPairs(null);
      } else {
        push?.(`入库失败 · ${r.error || "未知原因"}`, 7000);
      }
    } catch (e) {
      push?.("入库失败: " + (e instanceof Error ? e.message : String(e)), 7000);
    } finally {
      setBusy("");
    }
  };


  return (
    <Card>
      <CardContent className="space-y-3.5 p-3.5">
        <div className="flex items-start gap-2">
          <Database className="mt-[2px] h-4 w-4 shrink-0 text-[var(--color-accent)]" />
          <div className="min-w-0">
            <div className="text-[0.82rem] font-medium text-[var(--color-text)]">
              来源导入 · 聊天记录沉淀
            </div>
            <div className="mt-0.5 text-[0.76rem] leading-relaxed text-[var(--color-text-muted)]">
              从会话里抽取「对方提问 → 我方作答」的问答对，先预览再入库
              （写入范围仅知识库表，**不碰聊天记录**）。
            </div>
          </div>
        </div>

        {/* 2026-10-05：账号/会话读取失败此前静默为空下拉，用户以为「没有账号」。 */}
        {acctsQ.isPending && (
          <div className="text-[0.76rem] text-[var(--color-text-muted)]">账号加载中…</div>
        )}
        {acctsQ.isError && (
          <div className="text-[0.76rem] text-[var(--color-danger)]">
            账号列表读取失败：{errMsg(acctsQ.error)}（请确认后端已启动）
          </div>
        )}
        {convsQ.isError && (
          <div className="text-[0.76rem] text-[var(--color-danger)]">
            会话列表读取失败：{errMsg(convsQ.error)}
          </div>
        )}

        {/* 目标库 */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[0.76rem] text-[var(--color-text-secondary)]">写入目标</span>
          <SegmentedTabs
            value={target}
            onChange={setTarget}
            items={[
              { value: "reply", label: "对话回复库" },
              { value: "pro", label: "专业知识库" },
            ]}
          />
        </div>

        {/* 账号 + 会话选择 */}
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="h-[32px] rounded-[var(--radius-sm)] border border-[var(--color-border)]
                       bg-[var(--color-surface)] px-2 text-[0.78rem] text-[var(--color-text)]"
            value={acct}
            onChange={(e) => { setAcct(e.target.value); setConvId(""); setPairs(null); }}
          >
            <option value="">选账号…</option>
            {realAccts.map((a: { name: string }) => (
              <option key={a.name} value={a.name}>{a.name}</option>
            ))}
          </select>
          <select
            className="h-[32px] max-w-[280px] rounded-[var(--radius-sm)] border
                       border-[var(--color-border)] bg-[var(--color-surface)]
                       px-2 text-[0.78rem] text-[var(--color-text)]"
            value={convId}
            // 2026-09-18 审查修复（HIGH）：切会话必须清空预览与勾选。
            // 旧实现只 setConvId → 上一会话的问答对仍留在屏上，而 doImport 用的是
            // **当前 convId** → 用户核对的是 A、实际入库的是 B（数据错位）。
            onChange={(e) => { setConvId(e.target.value); setPairs(null); }}
            disabled={!acct}
          >
            <option value="">选会话…</option>
            {convs.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <Button
            variant="secondary"
            size="sm"
            data-od-id="kb-import-preview"
            disabled={!acct || !convId || busy !== ""}
            onClick={preview}
          >
            {busy === "preview" ? (
              <><Loader2 className="h-3.5 w-3.5 animate-spin" />抽取中…</>
            ) : (
              <><Eye className="h-3.5 w-3.5" />预览问答对</>
            )}
          </Button>
        </div>

        {/* 预览表格 */}
        {pairs !== null && (
          <div className="space-y-2">
            {pairs.length === 0 ? (
              <div className="rounded-[var(--radius-sm)] border border-dashed
                              border-[var(--color-border)] px-3 py-4 text-center
                              text-[0.76rem] text-[var(--color-text-muted)]">
                没有抽到问答对（规则保守：宁缺勿错）
              </div>
            ) : (
              <>
                <div className="flex items-center gap-2 text-[0.74rem] text-[var(--color-text-secondary)]">
                  抽到 {pairs.length} 对
                  <span className="text-[var(--color-text-muted)]">
                    （入库按整会话执行，预览仅供核对内容）
                  </span>
                  <div className="flex-1" />
                  <Button
                    variant="secondary"
                    size="sm"
                    data-od-id="kb-import-confirm"
                    disabled={busy !== ""}
                    onClick={doImport}
                  >
                    {busy === "import" ? (
                      <><Loader2 className="h-3.5 w-3.5 animate-spin" />入库中…</>
                    ) : (
                      <><Download className="h-3.5 w-3.5" />确认入库</>
                    )}
                  </Button>
                </div>
                <div className="max-h-[320px] overflow-y-auto rounded-[var(--radius-sm)]
                                border border-[var(--color-border)]">
                  <table className="w-full text-left text-[0.74rem]">
                    <thead className="sticky top-0 bg-[var(--color-surface-raised)]
                                      text-[var(--color-text-muted)]">
                      <tr>
                        <th className="px-2 py-1.5 font-normal">对方提问</th>
                        <th className="px-2 py-1.5 font-normal">我方作答</th>
                        <th className="w-28 px-2 py-1.5 font-normal">来源</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pairs.map((p, i) => (
                        // 2026-09-18 审查修复（#41）：后端允许多条问答对 source_msg_id 为
                        // null/重复，`key={p.source_msg_id || i}` 会撞 key（React 复用错行）。
                        // 统一用「来源 id + 下标」复合键，稳定且唯一。
                        <tr key={`${p.source_msg_id ?? "x"}-${i}`}
                            className="border-t border-[var(--color-border)]">
                          <td className="px-2 py-1.5 text-[var(--color-text)]">{p.question}</td>
                          <td className="px-2 py-1.5 text-[var(--color-text-secondary)]">{p.answer}</td>
                          <td className="px-2 py-1.5 font-mono text-[0.68rem] text-[var(--color-text-muted)]">
                            {p.source_msg_id ? p.source_msg_id.slice(0, 12) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
