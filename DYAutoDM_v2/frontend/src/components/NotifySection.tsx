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
  GatewayGrant,
  GatewayPending,
} from "../api/client";
import { Dot, Pill } from "../components/ui";

const ROLE_LABELS: Record<string, string> = {
  admin: "管理员（全授权）",
  operator: "操作员（可执行任务）",
  viewer: "查看者（仅查看）",
  blocked: "已拉黑",
  pending: "待授权",
};

const CHANNEL_SHORT: Record<string, string> = {
  weixin_oc: "微信",
  wecom: "企微",
  dingtalk: "钉钉",
  lark: "飞书",
  qqofficial: "QQ",
};

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

  // ---- IM 网关（授权/权限组）----
  const gwQ = useQuery({
    queryKey: ["notify-gateway"],
    queryFn: () => api.gatewayOverview(),
    enabled: true,
    refetchInterval: 15_000, // 新待授权及时浮现
  });
  const gwMode = gwQ.data?.mode || "pairing";
  const grants: Record<string, GatewayGrant> = gwQ.data?.grants || {};
  const pendingList: GatewayPending[] = gwQ.data?.pending || [];

  const gwMut = useMutation({
    mutationFn: async (v: { op: string; key?: string; role?: string; mode?: string }) => {
      if (v.op === "approve" && v.key && v.role)
        return api.gatewayApprove(v.key, v.role);
      if (v.op === "revoke" && v.key) return api.gatewayRevoke(v.key);
      if (v.op === "mode" && v.mode) return api.gatewaySetMode(v.mode);
      throw new Error("未知操作");
    },
    onSuccess: (_r, v) => {
      push(
        v.op === "approve"
          ? `已授权为「${ROLE_LABELS[v.role || ""] || v.role}」`
          : v.op === "revoke"
            ? "已撤销授权"
            : `网关模式已切换为 ${v.mode === "open" ? "开放" : "配对"}`,
      );
      qc.invalidateQueries({ queryKey: ["notify-gateway"] });
    },
    onError: (e) => push(`操作失败: ${errMsg(e)}`),
  });

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

      {/* ===== 授权管理（IM 网关，v0.38.5）===== */}
      <Card
        title={
          <>
            <strong>远程操作授权</strong>
            <Dot c={pendingList.length ? "warn" : gwMode === "open" ? "warn" : "ok"} />
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              {gwMode === "open"
                ? "开放模式（未拦截）"
                : pendingList.length
                  ? `${pendingList.length} 条待授权`
                  : "配对模式（拦截未授权）"}
            </span>
            {pendingList.length > 0 && <Pill c="warn">{pendingList.length} 待审</Pill>}
          </>
        }
        defaultOpen={pendingList.length > 0}
      >
        <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.6, marginBottom: 10 }}>
          配对模式（推荐）：所有来源可给 bot 发消息，但未授权者的指令一律拦截并回引导语，
          其首条消息会显示在下方待审区，由你甄别后授予权限组。开放模式不拦截（仅测试用）。
        </div>

        <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "center" }}>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>网关模式</span>
          <button
            className={"btn sm" + (gwMode === "pairing" ? " accent" : "")}
            disabled={gwMut.isPending}
            onClick={() => gwMut.mutate({ op: "mode", mode: "pairing" })}
          >
            配对（拦截未授权）
          </button>
          <button
            className={"btn sm" + (gwMode === "open" ? " accent" : "")}
            disabled={gwMut.isPending}
            onClick={() => {
              if (confirm("开放模式将允许所有来源直接执行指令（受权限组限制的除外），确认切换？"))
                gwMut.mutate({ op: "mode", mode: "open" });
            }}
          >
            开放（不拦截）
          </button>
        </div>

        {/* 待授权 */}
        {pendingList.length > 0 && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6 }}>
              待授权（{pendingList.length}）
            </div>
            {pendingList.map((p) => (
              <div
                key={p.key}
                style={{
                  border: "1px solid var(--warn, #d89614)",
                  borderRadius: 10,
                  padding: "10px 12px",
                  marginBottom: 8,
                  background: "color-mix(in oklch, var(--warn, #d89614) 8%, transparent)",
                }}
              >
                <div style={{ fontSize: 12.5, marginBottom: 4 }}>
                  <b>{CHANNEL_SHORT[p.channel_id] || p.channel_id}</b>
                  <span style={{ color: "var(--muted)", marginLeft: 8, fontFamily: "monospace", fontSize: 11.5 }}>
                    {p.sender_id}
                  </span>
                  <span style={{ color: "var(--muted)", marginLeft: 8, fontSize: 11 }}>
                    共 {p.msg_count} 条 · 最近 {new Date(p.last_at * 1000).toLocaleTimeString()}
                  </span>
                </div>
                {p.last_text && (
                  <div style={{ fontSize: 12, color: "var(--fg)", marginBottom: 8, opacity: 0.85 }}>
                    「{p.last_text}」
                  </div>
                )}
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button
                    className="btn sm accent"
                    disabled={gwMut.isPending}
                    onClick={() => gwMut.mutate({ op: "approve", key: p.key, role: "admin" })}
                    title="全授权（可执行一切指令）"
                  >
                    设为管理员
                  </button>
                  <button
                    className="btn sm"
                    disabled={gwMut.isPending}
                    onClick={() => gwMut.mutate({ op: "approve", key: p.key, role: "operator" })}
                  >
                    操作员
                  </button>
                  <button
                    className="btn sm"
                    disabled={gwMut.isPending}
                    onClick={() => gwMut.mutate({ op: "approve", key: p.key, role: "viewer" })}
                  >
                    仅查看
                  </button>
                  <button
                    className="btn sm danger"
                    disabled={gwMut.isPending}
                    onClick={() => gwMut.mutate({ op: "approve", key: p.key, role: "blocked" })}
                  >
                    拉黑
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 已授权列表 */}
        <div>
          <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6 }}>
            已授权（{Object.keys(grants).length}）
          </div>
          {Object.keys(grants).length === 0 && (
            <div style={{ fontSize: 12, color: "var(--muted)" }}>
              还没有授权任何来源。配对模式下，对方给 bot 发一条消息即可出现在待审区。
            </div>
          )}
          {Object.entries(grants).map(([key, g]) => (
            <div
              key={key}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "8px 10px",
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                marginBottom: 6,
                fontSize: 12,
                flexWrap: "wrap",
              }}
            >
              <span style={{ flex: "0 0 auto", fontWeight: 600 }}>
                {CHANNEL_SHORT[g.channel_id] || g.channel_id}
              </span>
              <span
                style={{
                  flex: 1,
                  minWidth: 140,
                  fontFamily: "monospace",
                  fontSize: 11.5,
                  color: "var(--muted)",
                  wordBreak: "break-all",
                }}
              >
                {g.sender_id}
              </span>
              <span
                style={{
                  flex: "0 0 auto",
                  color: g.role === "admin" ? "var(--accent)" : g.role === "blocked" ? "var(--danger)" : "var(--muted)",
                }}
              >
                {ROLE_LABELS[g.role] || g.role}
              </span>
              {g.role !== "admin" && g.role !== "blocked" && (
                <button
                  className="btn sm"
                  disabled={gwMut.isPending}
                  onClick={() => gwMut.mutate({ op: "approve", key, role: "admin" })}
                  title="提升为管理员"
                >
                  升级管理员
                </button>
              )}
              <button
                className="btn sm"
                disabled={gwMut.isPending}
                onClick={() => gwMut.mutate({ op: "revoke", key })}
              >
                撤销
              </button>
            </div>
          ))}
        </div>
      </Card>

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
              label="默认接收目标（可选）"
              value={String(ch.default_target || "")}
              placeholder="留空 = 自动推送给已授权的会话"
              hint={`${meta?.targetHint || ""} · 已在下方「远程操作授权」中授权的会话会自动接收推送，无需手填`}
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
