/**
 * 通知与指令（设置页 tab，v0.38.4）。
 *
 * 用户指出 v0.38.3 缺陷：「通知与指令中只有模型的内容，核心的 IM bot
 * 绑定内容没有了」——本组件把旧「IM 通知」页（pages/notify.tsx，主导航
 * 已移除）的渠道管理完整迁入设置页：
 *   - 总开关 + 五渠道（个人微信 iLink / 企业微信 / 钉钉 / 飞书 / QQ）增删改
 *   - 渠道测试推送、就绪状态
 *   - 指令解析模型绑定已在「AI 与 Agent → 模型链路中心」（notify_cmd 消费方）
 *
 * 数据面与旧页完全一致：/api/notify/*（enabled+channels 存
 * notify_config.json，敏感字段后端脱敏回传，原样回传 = 不修改）。
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

/** 可收缩卡片（原 notify.tsx 同款） */
function Card(props: {
  title: React.ReactNode;
  subtitle?: string;
  defaultOpen?: boolean;
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(!!props.defaultOpen);
  return (
    <div
      style={{
        background: "var(--panel)",
        borderRadius: 10,
        marginBottom: 10,
        overflow: "hidden",
        border: "1px solid var(--border)",
      }}
    >
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
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            flex: 1,
            minWidth: 0,
          }}
        >
          {props.title}
        </div>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>
          {open ? "收起" : "展开"}
        </span>
      </div>
      {open && (
        <div style={{ padding: "0 14px 12px" }}>
          {props.subtitle && (
            <div
              style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10 }}
            >
              {props.subtitle}
            </div>
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
        {props.secret && (
          <span style={{ color: "var(--muted)", marginLeft: 6 }}>（敏感）</span>
        )}
      </div>
      <input
        className="inp"
        type={props.secret ? "password" : "text"}
        value={props.value}
        placeholder={props.placeholder || ""}
        onChange={(e) => props.onChange(e.target.value)}
        style={{ width: "100%" }}
      />
      {props.hint && (
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 3 }}>
          {props.hint}
        </div>
      )}
    </label>
  );
}

export default function NotifySection({ api, push }: PageProps) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<NotifyConfig | null>(null);
  const [testing, setTesting] = useState<string>("");

  const cfgQ = useQuery({
    queryKey: ["notify-config"],
    queryFn: () => api.getNotifyConfig(),
    enabled: true,
  });
  const stQ = useQuery({
    queryKey: ["notify-status"],
    queryFn: () => api.getNotifyStatus(),
    enabled: true,
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

  if (cfgQ.isLoading)
    return <div style={{ padding: 20, color: "var(--muted)" }}>加载通知配置…</div>;
  if (cfgQ.isError)
    return (
      <div style={{ padding: 20, color: "var(--danger, #c0392b)" }}>
        配置加载失败：{errMsg(cfgQ.error)}
      </div>
    );

  return (
    <div>
      <div
        style={{
          fontSize: 12,
          color: "var(--muted)",
          marginBottom: 10,
          lineHeight: 1.6,
        }}
      >
        IM Bot 绑定：任务新建 / 任务监控 / 私信汇报 / 凭证失效提醒，推送到微信、
        企微、钉钉、飞书或 QQ。指令解析用的模型在
        <b>「AI 与 Agent → 模型链路中心」</b>
        绑定（IM 通知指令解析消费方）。
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
          <button
            className="btn primary"
            disabled={saveMut.isPending}
            onClick={() => saveMut.mutate(cfg)}
          >
            {saveMut.isPending ? "保存中…" : "保存配置"}
          </button>
        }
      >
        <label
          style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
        >
          <input
            type="checkbox"
            checked={!!cfg.enabled}
            onChange={(e) =>
              patch((c) => {
                c.enabled = e.target.checked;
              })
            }
          />
          <span>开启 IM 通知推送</span>
        </label>
        <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>
          关闭后所有事件（含凭证失效告警）都不会推送。
        </div>
      </Card>

      {/* 渠道列表 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          margin: "14px 0 8px",
        }}
      >
        <strong style={{ fontSize: 13.5 }}>
          推送渠道（{channels.length}）
        </strong>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {(Object.keys(CHANNEL_META) as NotifyKind[]).map((k) => (
            <button key={k} className="btn sm" onClick={() => addChannel(k)}>
              + {CHANNEL_META[k].label}
            </button>
          ))}
        </div>
      </div>

      {channels.length === 0 && (
        <div
          style={{
            padding: 20,
            textAlign: "center",
            color: "var(--muted)",
            background: "var(--panel)",
            borderRadius: 10,
            border: "1px solid var(--border)",
          }}
        >
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
                <button
                  className="btn"
                  disabled={testing === String(ch.id || ch.kind)}
                  onClick={() => testChannel(ch)}
                >
                  {testing === String(ch.id || ch.kind) ? "发送中…" : "测试推送"}
                </button>
                <button
                  className="btn danger"
                  onClick={() => removeChannel(i)}
                >
                  删除渠道
                </button>
              </div>
            }
          >
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 10,
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={!!ch.enabled}
                onChange={(e) => updateChannel(i, "enabled", e.target.checked)}
              />
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
              <div
                style={{
                  fontSize: 11,
                  color: "var(--warn, #d89614)",
                  marginTop: 4,
                }}
              >
                ⚠️ 个人微信（iLink）只能被动回推：对方需先给机器人发一条消息，
                机器人获得 context_token 后才能回复。
              </div>
            )}
          </Card>
        );
      })}

      {channels.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <button
            className="btn primary"
            disabled={saveMut.isPending}
            onClick={() => saveMut.mutate(cfg)}
          >
            {saveMut.isPending ? "保存中…" : "保存配置"}
          </button>
        </div>
      )}
    </div>
  );
}
