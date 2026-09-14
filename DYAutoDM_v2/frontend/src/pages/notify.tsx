/**
 * IM 通知配置页（重设计版 · 对标 better-douyin 设计体系）
 *
 * 把后端 5 个 /api/notify/* 接口暴露到 UI：配置渠道、测试推送。
 *
 * ## 设计要点
 * - 每个渠道一个可收缩卡片，标题栏带就绪状态点
 * - 敏感字段后端返回时已脱敏为 ••••，**前端原样回传即保留原值**（不覆写）
 *   故 placeholder 提示「留空/保持 •••• = 不修改」
 * - 颜色/圆角/缓动取自 tokens.css（深浅主题自动生效）
 *
 * ## 本次改动（重设计）
 * - 旧 `<div style={{background:"var(--panel)"}}>` 硬编码 + `.btn`/`.inp` 类 →
 *   新组件（Card / Button / Input / Switch / Badge）+ 设计令牌
 * - 折叠卡片改为受控展开（原生 <button> 头部 + aria-expanded）
 * - **业务逻辑零改动**（脱敏回传语义、字段清单、测试推送调用全部保持）
 */
import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Send, AlertTriangle } from "lucide-react";
import {
  PageProps,
  NotifyConfig,
  NotifyChannelCfg,
  NotifyStatus,
  CHANNEL_META,
  NotifyKind,
} from "../api/client";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { StatusDot } from "@/components/ui/status-dot";
import { Blank, Toolbar, Collapse } from "@/components/page/kit";

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function Field({
  label,
  value,
  onChange,
  secret,
  hint,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  secret?: boolean;
  hint?: string;
  placeholder?: string;
}) {
  return (
    <label className="mb-2.5 block">
      <div className="mb-1 text-[0.72rem] text-[var(--color-text)]">
        {label}
        {secret && (
          <span className="ml-1.5 text-[var(--color-text-muted)]">（敏感）</span>
        )}
      </div>
      <Input
        type={secret ? "password" : "text"}
        value={value}
        placeholder={placeholder || ""}
        onChange={(e) => onChange(e.target.value)}
        className="w-full"
      />
      {hint && (
        <div className="mt-1 text-[0.68rem] text-[var(--color-text-muted)]">{hint}</div>
      )}
    </label>
  );
}

export default function NotifyPage({ api, push }: PageProps) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<NotifyConfig | null>(null);
  const [testing, setTesting] = useState("");

  const cfgQ = useQuery({
    queryKey: ["notify-config"],
    queryFn: () => api.getNotifyConfig(),
  });
  const stQ = useQuery({
    queryKey: ["notify-status"],
    queryFn: () => api.getNotifyStatus(),
  });

  // 后端数据到齐后初始化草稿（仅首次，避免覆盖用户输入）
  useEffect(() => {
    if (cfgQ.data?.config && !draft) {
      setDraft(JSON.parse(JSON.stringify(cfgQ.data.config)));
    }
  }, [cfgQ.data, draft]);

  const saveMut = useMutation({
    mutationFn: (cfg: NotifyConfig) => api.saveNotifyConfig(cfg),
    onSuccess: (r) => {
      push(r.ok ? "通知配置已保存" : `保存失败: ${r.error || "未知错误"}`);
      qc.invalidateQueries({ queryKey: ["notify-config"] });
      qc.invalidateQueries({ queryKey: ["notify-status"] });
    },
    onError: (e) => push(`保存失败: ${errMsg(e)}`),
  });

  const cfg: NotifyConfig = draft || { enabled: false, channels: [], llm: {} };
  const channels: NotifyChannelCfg[] = cfg.channels || [];
  const status: NotifyStatus | undefined = stQ.data;

  const patch = (fn: (c: NotifyConfig) => void) => {
    const next: NotifyConfig = JSON.parse(JSON.stringify(cfg));
    fn(next);
    setDraft(next);
  };

  const statusOf = (id: string) =>
    status?.channels?.find((c) => String(c.id) === String(id));

  const addChannel = (kind: NotifyKind) => {
    patch((c) => {
      const list = c.channels || (c.channels = []);
      list.push({
        id: `${kind}_${Date.now().toString(36)}`,
        kind,
        enabled: true,
        default_target: "",
      });
    });
  };

  const removeChannel = (idx: number) => {
    patch((c) => {
      (c.channels || []).splice(idx, 1);
    });
  };

  const updateChannel = (idx: number, key: string, val: unknown) => {
    patch((c) => {
      const ch = (c.channels || [])[idx];
      if (ch) (ch as Record<string, unknown>)[key] = val;
    });
  };

  const testChannel = async (ch: NotifyChannelCfg) => {
    const id = String(ch.id || ch.kind);
    setTesting(id);
    try {
      const r = await api.testNotify(id, String(ch.default_target || ""));
      const one = r.results?.[id];
      if (r.ok) push(`测试推送成功 → ${id}`);
      else push(`测试推送失败 → ${one?.error || r.error || "未知错误"}`);
    } catch (e) {
      push(`测试推送失败: ${errMsg(e)}`);
    } finally {
      setTesting("");
    }
  };

  if (cfgQ.isLoading) {
    return (
      <PageContainer>
        <PageHeader title="IM 通知" />
        <Blank>加载中…</Blank>
      </PageContainer>
    );
  }
  if (cfgQ.isError) {
    return (
      <PageContainer>
        <PageHeader title="IM 通知" />
        <Blank>配置加载失败：{errMsg(cfgQ.error)}</Blank>
      </PageContainer>
    );
  }

  return (
    <PageContainer maxWidth="860px">
      <PageHeader
        title="IM 通知"
        description="任务新建 / 任务监控 / 私信汇报 / 凭证失效提醒，推送到微信、企微、钉钉、飞书或 QQ"
      />

      {/* 总开关 */}
      <Collapse
        defaultOpen
        title={
          <>
            <strong className="text-[0.86rem] text-[var(--color-text)]">启用通知</strong>
            <StatusDot tone={cfg.enabled ? "ok" : "muted"} />
            <span className="text-[0.72rem] text-[var(--color-text-muted)]">
              {cfg.enabled ? "已启用" : "已关闭"}
            </span>
          </>
        }
        footer={
          <Button disabled={saveMut.isPending} onClick={() => saveMut.mutate(cfg)}>
            {saveMut.isPending ? "保存中…" : "保存配置"}
          </Button>
        }
      >
        <div className="flex items-center gap-2">
          <Switch
            checked={!!cfg.enabled}
            onCheckedChange={(v) => patch((c) => { c.enabled = v; })}
          />
          <span className="text-[0.8rem] text-[var(--color-text-secondary)]">
            开启 IM 通知推送
          </span>
        </div>
        <div className="mt-1.5 text-[0.72rem] text-[var(--color-text-muted)]">
          关闭后所有事件（含凭证失效告警）都不会推送。
        </div>

        <div className="my-3.5 h-px bg-[var(--color-border)]" />

        <div className="mb-2 text-[0.72rem] text-[var(--color-text)]">
          LLM 指令解析（可选）—— 未配置时自动使用规则解析
        </div>
        <div className="rounded-[var(--radius-sm)] border border-[var(--color-border)]
                        bg-[var(--color-surface)] px-2.5 py-2 text-[0.7rem] leading-relaxed
                        text-[var(--color-text-muted)]">
          模型配置已统一到 <b className="text-[var(--color-text-secondary)]">配置中心 → 「通知与指令」</b> 管理，
          此处不再重复配置。留空的字段会自动回落到 AI 全局配置。
        </div>
      </Collapse>

      {/* 渠道列表 */}
      <div className="mb-2 mt-4 flex flex-wrap items-center justify-between gap-2">
        <strong className="text-[0.84rem] text-[var(--color-text)]">
          推送渠道（{channels.length}）
        </strong>
        <Toolbar>
          {(Object.keys(CHANNEL_META) as NotifyKind[]).map((k) => (
            <Button key={k} variant="secondary" size="sm" onClick={() => addChannel(k)}>
              <Plus className="h-3.5 w-3.5" />{CHANNEL_META[k].label}
            </Button>
          ))}
        </Toolbar>
      </div>

      {channels.length === 0 && (
        <Card>
          <CardContent>
            <Blank>还没有渠道，点上方按钮添加一个</Blank>
          </CardContent>
        </Card>
      )}

      {channels.map((ch, i) => {
        const meta = CHANNEL_META[ch.kind as NotifyKind];
        const st = statusOf(String(ch.id || ch.kind));
        const title = meta?.label || ch.kind;
        const tid = String(ch.id || ch.kind);
        return (
          <Collapse
            key={String(ch.id || i)}
            title={
              <>
                <strong className="text-[0.84rem] text-[var(--color-text)]">{title}</strong>
                <StatusDot
                  tone={!ch.enabled ? "muted" : st?.ready ? "ok" : "danger"}
                />
                <span className="text-[0.72rem] text-[var(--color-text-muted)]">
                  {!ch.enabled ? "已停用" : st?.ready ? "就绪" : "未就绪"}
                </span>
                {st?.missing?.length ? (
                  <Badge variant="warning">缺 {st.missing.join("、")}</Badge>
                ) : null}
              </>
            }
            footer={
              <Toolbar>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={testing === tid}
                  onClick={() => testChannel(ch)}
                >
                  <Send className="h-3.5 w-3.5" />
                  {testing === tid ? "发送中…" : "测试推送"}
                </Button>
                <Button
                  variant="danger-outline"
                  size="sm"
                  onClick={() => removeChannel(i)}
                >
                  <Trash2 className="h-3.5 w-3.5" />删除渠道
                </Button>
              </Toolbar>
            }
          >
            <div className="mb-2.5 flex items-center gap-2">
              <Switch
                checked={!!ch.enabled}
                onCheckedChange={(v) => updateChannel(i, "enabled", v)}
              />
              <span className="text-[0.78rem] text-[var(--color-text-secondary)]">
                启用该渠道
              </span>
            </div>

            <Field
              label="默认接收目标"
              value={String(ch.default_target || "")}
              placeholder={meta?.targetHint || "接收方 ID"}
              hint={meta?.targetHint}
              onChange={(v) => updateChannel(i, "default_target", v)}
            />

            {(meta?.fields || []).map((f) => (
              <Field
                key={f.key}
                label={f.label}
                secret={f.secret}
                hint={f.hint}
                placeholder={f.secret ? "留空或 •••• = 不修改" : ""}
                value={String((ch as Record<string, unknown>)[f.key] ?? "")}
                onChange={(v) => updateChannel(i, f.key, v)}
              />
            ))}

            {ch.kind === "weixin_oc" && (
              <div className="mt-1 flex items-start gap-1.5 text-[0.68rem]
                              text-[var(--color-warning)]">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                <span>
                  个人微信（iLink）只能被动回推：对方需先给机器人发一条消息，
                  机器人获得 context_token 后才能回复。
                </span>
              </div>
            )}
          </Collapse>
        );
      })}

      {channels.length > 0 && (
        <div className="mt-1.5">
          <Button disabled={saveMut.isPending} onClick={() => saveMut.mutate(cfg)}>
            {saveMut.isPending ? "保存中…" : "保存配置"}
          </Button>
        </div>
      )}
    </PageContainer>
  );
}
