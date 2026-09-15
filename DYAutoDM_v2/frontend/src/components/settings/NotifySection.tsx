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
} from "../../api/client";
import { Dot, Pill } from "../ui";
import { errMsg, ROLE_LABELS } from "./settings-shared";
import { Card, Field } from "./notify-widgets";
import { AuthorizationCard } from "./AuthorizationCard";



/** 可收缩卡片（原 notify.tsx 同款） */


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
      // v0.38.5：同一平台只保留一个渠道实例 —— 重复添加会让同一 QQ bot
      // 建两条 WS 连接，同一条消息生成两份待授权（实测踩中）。
      if (list.some((x) => x.kind === kind)) {
        push(`已有一个「${CHANNEL_META[kind].label}」渠道，直接在下方卡片里编辑即可`);
        return;
      }
      list.push({
        id: `${kind}_${Date.now().toString(36)}`,
        kind,
        enabled: true,
        default_target: "",
      });
    });
  };

  const removeChannel = (idx: number) => {
    const removed = channels[idx];
    patch((c) => {
      (c.channels || []).splice(idx, 1);
    });
    // v0.38.5：级联清理该渠道的待审条目（渠道删了，挂着它的待审只会造成
    // "明明删了还在"的困惑）。已授权 grants 保留（换渠道重配后原授权仍可复用，
    // 且 gateway 授权按 kind+sender 归并，同平台新实例自动继承）。
    if (removed?.id) {
      api
        .gatewayOverview()
        .then((g) => {
          const stale = (g.pending || []).filter(
            (x) => x.channel_id === removed.id,
          );
          for (const x of stale) void api.gatewayRevoke(x.key);
        })
        .catch(() => {});
    }
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
    return <div style={{ padding: 20, color: "var(--color-text-muted)" }}>加载通知配置…</div>;
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
          color: "var(--color-text-muted)",
          marginBottom: 10,
          lineHeight: 1.6,
        }}
      >
        IM Bot 绑定：任务新建 / 任务监控 / 私信汇报 / 凭证失效提醒，推送到微信、
        企微、钉钉、飞书或 QQ。指令解析用的模型在
        <b>「AI 与 Agent → 模型链路中心」</b>
        绑定（IM 通知指令解析消费方）。
      </div>

      {/* ===== 授权管理（IM 网关，v0.38.5）→ 独立组件 ===== */}
      <AuthorizationCard
        mode={gwMode}
        pending={pendingList}
        grants={grants}
        busy={gwMut.isPending}
        onMode={(m) => gwMut.mutate({ op: "mode", mode: m })}
        onApprove={(key, role) => gwMut.mutate({ op: "approve", key, role })}
        onReject={(key) => gwMut.mutate({ op: "revoke", key })}
      />

      {/* 总开关 */}
      <Card
        title={
          <>
            <strong>启用通知</strong>
            <Dot c={cfg.enabled ? "ok" : "mute"} />
            <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
              {cfg.enabled ? "已启用" : "已关闭"}
            </span>
          </>
        }
        defaultOpen
        footer={
          <button
            className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-10 px-5 rounded-[12px]"
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
        <div style={{ fontSize: 12, color: "var(--color-text-muted)", marginTop: 6 }}>
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
            <button key={k} className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]" onClick={() => addChannel(k)}>
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
            color: "var(--color-text-muted)",
            background: "var(--color-surface-solid)",
            borderRadius: 10,
            border: "1px solid var(--color-border)",
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
                <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
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
                  className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-10 px-5 rounded-[12px]"
                  disabled={testing === String(ch.id || ch.kind)}
                  onClick={() => testChannel(ch)}
                >
                  {testing === String(ch.id || ch.kind) ? "发送中…" : "测试推送"}
                </button>
                <button
                  className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] border border-[color-mix(in_srgb,var(--color-danger)_25%,transparent)] bg-[var(--color-danger-soft)] text-[var(--color-danger)] hover:bg-[var(--color-danger)] hover:text-white h-10 px-5 rounded-[12px]"
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
            className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-10 px-5 rounded-[12px]"
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
