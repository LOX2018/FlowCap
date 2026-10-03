/**
 * 账号凭证健康（总览页区块）—— ADR-018 F2
 *
 * ## 设计意图（归类依据）
 *
 * 与「AI 运行状态」「能力健康」同层：都是**产品级运行状态**，不是某个页面的
 * 局部功能。回答的是「**账号还能不能用**」（凭证/守护双引擎）。
 *
 * ## 数据来源（ADR-018 决策：零新采集，只用既有端点）
 *
 * - `GET /api/accounts`（client.ts: `api.getAccounts()`）—— `_to_raw_account()`
 *   逐字段下发 `wpEngine` / `dmEngine` / `lastRun`，是本区块唯一数据源。
 * - 本区块**不发任何写操作**，不触发 xxx/check：后端 `/{name}/check` 是重型
 *   按需校验（真跑 validate_cookie + 请求 douyin）， Overview 页每 30s
 *   轮询这种端点会打爆后端。此处只读 `list_accounts` 已经带出来的 school 级
 *   结论（`wpEngine.level` / `dmEngine.level`），与账号管理页同源、同口径。
 *
 * ## 铁律（继承 CapabilityHealthSection）
 *
 * - 三态必须如实：后端 level 可能是 `unknown`（该账号**尚未校验**），
 *   UI 必须显示「未校验」，**不得**显示成 `未通过` 或 `异常`。
 * - 空态（未添加账号 / 后端未连接 / 请求失败）要有明确文案，不得显示 0 假数据。
 */
import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, KeyRound, MessageCircleDashed, HelpCircle } from "lucide-react";
import { PageProps } from "../../api/client";
import { Avatar } from "../../components/ui";
import { Section, Row, RowText, Tone, SkeletonRows, Blank } from "@/components/page/kit";

/** 后端 `_to_raw_account` 下发的原始账号结构（本区块只取用到的字段）。 */
interface RawAccountHealth {
  name: string;
  uid?: string | null;
  level?: string;
  label?: string;
  loggedIn?: boolean;
  browserDaemonAlive?: boolean;
  recvDaemonAlive?: boolean;
  wpEngine?: { level?: string; label?: string; detail?: string } | null;
  dmEngine?: { level?: string; label?: string; detail?: string } | null;
  isCurrent?: boolean;
  lastRun?: { room?: string; time?: string; duration?: string; dmSent?: number } | null;
}

type LevelTone = "ok" | "warn" | "danger" | "mute";

/**
 * level → { 文案, 色调 }。
 *
 * `unknown` 单独一档：**未校验 ≠ 未通过**（后端 TTL 缓存里没有结论时下发
 * `unknown`，此时后端从没去校验过，不是账号有问题）。
 */
const LEVEL_META: Record<string, { label: string; tone: LevelTone }> = {
  ok: { label: "可用", tone: "ok" },
  nosign: { label: "缺签名", tone: "warn" },
  expired: { label: "已失效", tone: "danger" },
  missing: { label: "无凭证", tone: "danger" },
  warn: { label: "待处理", tone: "warn" },
  fail: { label: "未通过", tone: "danger" },
  error: { label: "校验异常", tone: "danger" },
  unknown: { label: "未校验", tone: "mute" },
};

function levelMeta(level?: string | null): { label: string; tone: LevelTone } {
  const k = String(level || "unknown");
  return LEVEL_META[k] || { label: k, tone: "mute" };
}

export default function AccountsHealthSection(props: PageProps) {
  const { api, ready } = props;

  // 复用 App 级常驻缓存（queryKey ["accounts"]，30s 轮询）—— 切到总览页直接读
  // 热数据，不再向后端重复拉重型校验。
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.getAccounts()) as unknown as RawAccountHealth[],
    refetchInterval: 30000,
    enabled: !!ready,
  });

  const accounts = Array.isArray(data) ? data : [];

  // 聚合：可用 / 异常 / 未校验。**未校验单独计数**，不并入异常。
  const okN = accounts.filter((a) => (a.wpEngine?.level || "") === "ok").length;
  const badN = accounts.filter((a) =>
    ["expired", "missing", "fail", "error"].includes(String(a.wpEngine?.level || "")),
  ).length;
  const unknownN = accounts.filter((a) => {
    const lv = String(a.wpEngine?.level || "unknown");
    return lv === "unknown";
  }).length;
  const dmReadyN = accounts.filter((a) => (a.dmEngine?.level || "") === "ok").length;
  const daemonAliveN = accounts.filter((a) => a.browserDaemonAlive).length;

  return (
    <Section
      title="账号凭证健康"
      description="凭证守护与私信引擎的双引擎结论"
      actions={
        accounts.length ? (
          <Tone tone={badN ? "danger" : unknownN ? "mute" : okN ? "ok" : "mute"}>
            {badN ? `${badN} 个异常` : okN ? `${okN} 个可用` : `${unknownN} 个未校验`}
          </Tone>
        ) : null
      }
    >
      {isLoading ? (
        <SkeletonRows rows={3} />
      ) : isError ? (
        <Blank>
          读取账号列表失败：{(error as Error)?.message || "后端无响应"}
          <br />
          <span className="text-[0.72rem]">账号数据暂未取到，请确认服务已启动。</span>
        </Blank>
      ) : !accounts.length ? (
        <Blank>尚未添加账号 —— 请先在「账号」页添加并登录后再来看凭证健康。</Blank>
      ) : (
        <div className="space-y-1.5">
          {/* 汇总行：三态计数（纯文本，避免与行内状态胶囊混淆） */}
          <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.78rem] text-[var(--color-text-secondary)]">
            <span>
              凭证可用 <b className="text-[var(--color-text)]">{okN}</b> · 异常{" "}
              <b className="text-[var(--color-text)]">{badN}</b> · 未校验{" "}
              <b className="text-[var(--color-text)]">{unknownN}</b>
            </span>
            <span className="text-[var(--color-text-muted)]">
              私信就绪 {dmReadyN} · 凭证守护在线 {daemonAliveN}
            </span>
          </div>

          {accounts.map((a) => {
            const wp = levelMeta(a.wpEngine?.level);
            const dm = levelMeta(a.dmEngine?.level);
            return (
              <Row key={a.name}>
                <Avatar name={a.name} h="20" />
                <RowText
                  primary={`${a.name}${a.isCurrent ? " · 当前" : ""}`}
                  secondary={`UID: ${a.uid || "—"}${a.browserDaemonAlive ? " · 守护在线" : " · 守护离线"}`}
                  mono
                />
                <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                  <Tone tone={wp.tone}>
                    {a.wpEngine?.level === "ok" ? (
                      <BadgeCheck className="mr-1 h-3.5 w-3.5" />
                    ) : a.wpEngine?.level === "unknown" ? (
                      <HelpCircle className="mr-1 h-3.5 w-3.5" />
                    ) : (
                      <KeyRound className="mr-1 h-3.5 w-3.5" />
                    )}
                    凭证 {a.wpEngine?.label || wp.label}
                  </Tone>
                  <Tone tone={dm.tone}>
                    <MessageCircleDashed className="mr-1 h-3 w-3" />
                    私信 {a.dmEngine?.label || dm.label}
                  </Tone>
                </div>
              </Row>
            );
          })}
        </div>
      )}
    </Section>
  );
}
