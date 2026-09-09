/**
 * IM 通知配置页
 *
 * 2026-09-09 新增（v0.37.0）。把「后端已有的 5 个 /api/notify/* 接口」暴露到 UI，
 * 让用户不用 curl 也能配置渠道、测试推送。
 *
 * 设计要点（与 settings.tsx 保持一致的视觉范式）：
 *   - 每个渠道一个可收缩卡片（Collapsible），标题栏带就绪状态点
 *   - 敏感字段后端返回时已脱敏为 •••• ，**前端原样回传即保留原值**（不覆写）
 *     故输入框 placeholder 提示「留空/保持 •••• = 不修改」
 *   - 「保存」「测试推送」按钮均有 loading 态，结果用 props.push 弹 toast
 *
 * ⚠️ API 实例是 props.api（不是 import 的裸 api）—— 项目约定，写错编译不过。
 */
import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  PageProps,
  NotifyConfig,
  NotifyChannelCfg,
  NotifyStatus,
  CHANNEL_META,
  NotifyKind,
} from "../api/client";
import { Dot, Pill } from "../components/ui";

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** 可收缩卡片（与 settings.tsx 同款结构，视觉统一） */
function Card(props: {
  title: React.ReactNode;
  subtitle?: string;
  defaultOpen?: boolean;
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(!!props.defaultOpen);
  return (
    <div style={{ background: "var(--panel)", borderRadius: 10, marginBottom: 10, overflow: "hidden" }}>
      <div
        onClick={() => setOpen(!open)}
        style={{
          padding: "12px 14px",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, flex: 1, minWidth: 0 }}>
          {props.title}
        </div>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>{open ? "收起" : "展开"}</span>
      </div>
      {open && (
        <div style={{ padding: "0 14px 12px" }}>
          {props.subtitle && (
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10 }}>{props.subtitle}</div>
          )}
          {props.children}
          {props.footer && <div style={{ marginTop: 12 }}>{props.footer}</div>}
        </div>
      )}
    </div>
  );
}

function Field(props: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  secret?: boolean;
  hint?: string;
  placeholder?: string;
}) {
  return (
    <label style={{ display: "block", marginBottom: 10 }}>
      <div style={{ fontSize: 12, marginBottom: 4, color: "var(--fg)" }}>
        {props.label}
        {props.secret && <span style={{ color: "var(--muted)", marginLeft: 6 }}>（敏感）</span>}
      </div>
      <input
        className="inp"
        type={props.secret ? "password" : "text"}
        value={props.value}
        placeholder={props.placeholder || ""}
        onChange={(e) => props.onChange(e.target.value)}
        style={{ width: "100%" }}
      />
      {props.hint && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 3 }}>{props.hint}</div>}
    </label>
  );
}

export default function NotifyPage({ api, push }: PageProps) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<NotifyConfig | null>(null);
  const [testing, setTesting] = useState<string>("");

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
      list.push({ id: `${kind}_${Date.now().toString(36)}`, kind, enabled: true, default_target: "" });
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

  if (cfgQ.isLoading) return <div className="pane">加载中…</div>;
  if (cfgQ.isError) return <div className="pane">配置加载失败：{errMsg(cfgQ.error)}</div>;

  return (
    <div className="pane" style={{ maxWidth: 860 }}>
      <h2 style={{ marginTop: 0 }}>IM 通知</h2>
      <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 14 }}>
        任务新建 / 任务监控 / 私信汇报 / 凭证失效提醒，推送到微信、企微、钉钉、飞书或 QQ。
      </div>

      {/* 总开关 */}
      <Card
        title={
          <>
            <strong>启用通知</strong>
            <Dot c={cfg.enabled ? "ok" : "mute"} />
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              {cfg.enabled ? "已启用" : "已关闭"}
            </span>
          </>
        }
        defaultOpen
        footer={
          <button className="btn primary" disabled={saveMut.isPending}
                  onClick={() => saveMut.mutate(cfg)}>
            {saveMut.isPending ? "保存中…" : "保存配置"}
          </button>
        }
      >
        <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
          <input type="checkbox" checked={!!cfg.enabled}
                 onChange={(e) => patch((c) => { c.enabled = e.target.checked; })} />
          <span>开启 IM 通知推送</span>
        </label>
        <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>
          关闭后所有事件（含凭证失效告警）都不会推送。
        </div>

        <hr style={{ margin: "14px 0", opacity: 0.2 }} />

        <div style={{ fontSize: 12, marginBottom: 8, color: "var(--fg)" }}>
          LLM 指令解析（可选）—— 未配置时自动使用规则解析
        </div>
        <div
          style={{
            fontSize: 11.5,
            color: "var(--muted)",
            padding: "8px 10px",
            background: "var(--surface-2)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            lineHeight: 1.6,
          }}
        >
          模型配置已统一到
          <b>「设置 → 通知与指令」</b>
          页管理，此处不再重复配置。 留空的字段会自动回落到 AI 全局配置。
        </div>
      </Card>

      {/* 渠道列表 */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", margin: "18px 0 8px" }}>
        <strong>推送渠道（{channels.length}）</strong>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {(Object.keys(CHANNEL_META) as NotifyKind[]).map((k) => (
            <button key={k} className="btn sm" onClick={() => addChannel(k)}>
              + {CHANNEL_META[k].label}
            </button>
          ))}
        </div>
      </div>

      {channels.length === 0 && (
        <div style={{ padding: 20, textAlign: "center", color: "var(--muted)", background: "var(--panel)", borderRadius: 10 }}>
          还没有渠道，点上方按钮添加一个
        </div>
      )}

      {channels.map((ch, i) => {
        const meta = CHANNEL_META[ch.kind as NotifyKind];
        const st = statusOf(String(ch.id || ch.kind));
        const title = meta?.label || ch.kind;
        return (
          <Card
            key={String(ch.id || i)}
            title={
              <>
                <strong>{title}</strong>
                <Dot c={!ch.enabled ? "mute" : st?.ready ? "ok" : "danger"} />
                <span style={{ fontSize: 12, color: "var(--muted)" }}>
                  {!ch.enabled ? "已停用" : st?.ready ? "就绪" : "未就绪"}
                </span>
                {st?.missing?.length ? (
                  <Pill c="warn">缺 {st.missing.join("、")}</Pill>
                ) : null}
              </>
            }
            footer={
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn" disabled={testing === String(ch.id || ch.kind)}
                        onClick={() => testChannel(ch)}>
                  {testing === String(ch.id || ch.kind) ? "发送中…" : "测试推送"}
                </button>
                <button className="btn danger" onClick={() => removeChannel(i)}>删除渠道</button>
              </div>
            }
          >
            <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, cursor: "pointer" }}>
              <input type="checkbox" checked={!!ch.enabled}
                     onChange={(e) => updateChannel(i, "enabled", e.target.checked)} />
              <span>启用该渠道</span>
            </label>

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
              <div style={{ fontSize: 11, color: "var(--warn, #d89614)", marginTop: 4 }}>
                ⚠️ 个人微信（iLink）只能被动回推：对方需先给机器人发一条消息，
                机器人获得 context_token 后才能回复。
              </div>
            )}
          </Card>
        );
      })}

      {channels.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <button className="btn primary" disabled={saveMut.isPending}
                  onClick={() => saveMut.mutate(cfg)}>
            {saveMut.isPending ? "保存中…" : "保存配置"}
          </button>
        </div>
      )}
    </div>
  );
}
