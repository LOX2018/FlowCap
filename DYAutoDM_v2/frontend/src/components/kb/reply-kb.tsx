import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2, Sparkles, Plus, Pencil } from "lucide-react";
import type { PageProps } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  Collapse, Row, Blank,
} from "@/components/page/kit";
import { errMsg } from "@/lib/utils";

export function ReplyKb({
  push,
  api,
  qc,
}: {
  push: PageProps["push"];
  api: PageProps["api"];
  qc: ReturnType<typeof useQueryClient>;
}) {
  const list = useQuery({ queryKey: ["ai-reply-kb"], queryFn: api.aiReplyKbList });
  const [q, setQ] = useState("");
  const [a, setA] = useState("");
  const [editId, setEditId] = useState<number | null>(null);
  const [learning, setLearning] = useState(false);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["ai-reply-kb"] });

  const saveMut = useMutation({
    mutationFn: () =>
      api.aiReplyKbSave(editId ? { id: editId, question: q, answer: a } : { question: q, answer: a }),
    onSuccess: (d) => {
      if (d.ok) {
        push(editId ? "已更新" : "话术已添加");
        setEditId(null);
        setQ("");
        setA("");
        invalidate();
      } else push(`保存失败：${d.error}`);
    },
    onError: (e) => push(`保存失败：${errMsg(e)}`),
  });

  const delMut = useMutation({
    mutationFn: (id: number) => api.aiReplyKbDelete(id),
    onSuccess: () => {
      push("已删除");
      invalidate();
    },
    onError: (e) => push(`删除失败：${errMsg(e)}`),
  });

  const toggleMut = useMutation({
    mutationFn: (it: { id: number; question: string; answer: string; enabled: boolean }) =>
      api.aiReplyKbSave(it),
    onSuccess: () => invalidate(),
    onError: (e) => push(`操作失败：${errMsg(e)}`),
  });

  const learnMut = useMutation({
    mutationFn: () => api.aiReplyKbLearn(),
    onSuccess: (d) => {
      setLearning(false);
      if (d.ok) push(`学习完成：扫描 ${d.scanned} 会话，提取 ${d.extracted} 对，新增 ${d.added} 条`);
      else push(`学习失败：${d.error}`);
      invalidate();
    },
    onError: (e) => {
      setLearning(false);
      push(`学习失败：${errMsg(e)}`);
    },
  });

  const items = list?.data?.items ?? [];

  return (
    <div>
      <div className="mb-3 text-[0.74rem] leading-relaxed text-[var(--color-text-muted)]">
        对方发送的信息符合库内案例时<b className="text-[var(--color-text-secondary)]">直接自动回复，
        不消耗 token</b>。优先级最高，在专业知识库与 AI 生成之前。
      </div>

      {/* 自动学习 */}
      <div className="mb-3.5 flex flex-wrap items-center gap-2.5 rounded-[var(--radius-md)]
                      border border-dashed border-[var(--color-border)] p-2.5">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            setLearning(true);
            learnMut.mutate();
          }}
          disabled={learning}
        >
          <Sparkles className="h-3.5 w-3.5" />
          {learning ? "学习中…" : "从聊天记录自动学习"}
        </Button>
        <span className="min-w-[200px] flex-1 text-[0.7rem] leading-relaxed
                         text-[var(--color-text-muted)]">
          扫描数据库中绑定 Agent 账号的私信会话（WS 长连接落库记录），总结有效问答对自动入库
          （source=auto，可逐条删改关）
        </span>
      </div>

      {/* 添加/编辑 */}
      <Collapse
        title={editId ? "编辑话术" : "添加话术"}
        defaultOpen={editId !== null}
        right={<Badge variant="outline">{items.length} 条</Badge>}
      >
        <div className="flex flex-wrap gap-2">
          <Input
            className="min-w-[220px] flex-1"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="客户问法案例…（例：你们这个啥价格）"
          />
          <Input
            className="min-w-[220px] flex-1"
            value={a}
            onChange={(e) => setA(e.target.value)}
            placeholder="命中的自动回复话术…"
          />
          <Button onClick={() => saveMut.mutate()} disabled={!q && !a}>
            {editId ? <><Pencil className="h-3.5 w-3.5" />更新</> : <><Plus className="h-3.5 w-3.5" />添加</>}
          </Button>
          {editId !== null && (
            <Button
              variant="ghost"
              onClick={() => {
                setEditId(null);
                setQ("");
                setA("");
              }}
            >
              取消
            </Button>
          )}
        </div>
      </Collapse>

      {/* 列表 */}
      <div className="divide-y divide-[var(--color-border)]">
        {items.map((it) => (
          <Row key={it.id} className="!px-0 py-2">
            <Switch
              checked={it.enabled}
              onCheckedChange={() =>
                toggleMut.mutate({
                  id: it.id,
                  question: it.question,
                  answer: it.answer,
                  enabled: !it.enabled,
                })
              }
            />
            <span
              className="min-w-0 flex-1 truncate text-[0.76rem]"
              style={{ opacity: it.enabled ? 1 : 0.45 }}
            >
              <b className="text-[var(--color-text-muted)]">Q:</b>{" "}
              <span className="text-[var(--color-text)]">{it.question}</span>
            </span>
            <span
              className="min-w-0 flex-1 truncate text-[0.76rem] text-[var(--color-text-secondary)]"
              style={{ opacity: it.enabled ? 1 : 0.45 }}
            >
              <b className="text-[var(--color-text-muted)]">A:</b> {it.answer}
            </span>
            <Badge variant={it.source === "auto" ? "accent" : "outline"}>
              {it.source === "auto" ? "自动学" : "手动"} · 命中{it.hits}
            </Badge>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setEditId(it.id);
                setQ(it.question);
                setA(it.answer);
              }}
            >
              <Pencil className="h-3 w-3" />编辑
            </Button>
            <Button
              variant="danger-outline"
              size="sm"
              onClick={() => delMut.mutate(it.id)}
            >
              <Trash2 className="h-3 w-3" />删除
            </Button>
          </Row>
        ))}
      </div>
      {items.length === 0 && (
        <Blank>暂无话术。点「从聊天记录自动学习」或手动添加。</Blank>
      )}
    </div>
  );
}
