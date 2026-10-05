import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Modal, ModalHeader, ModalBody, ModalFooter } from "@/components/ui/modal";

export type DmStatus = "un" | "wait" | "sent" | "fail";
export type PillColor = "ok" | "warn" | "danger" | "accent" | "mute";

export const DM_META: Record<DmStatus, [string, PillColor]> = {
  un: ["未私信", "mute"],
  wait: ["待发送", "warn"],
  // 2026-09-29（用户拍板「拆分已受理 / 已送达」）：本档语义是**受理**
  // （后端 RecordStatus.SENT = 已入池），真实送达由 Row.deliveryState 覆盖显示。
  sent: ["已受理", "ok"],
  fail: ["发送失败", "danger"],
};

/**
 * 2026-09-29：把「调度状态 + 真实投递证据」合成**界面显示档**。
 *
 * 为什么必须分开两者（实测依据）：
 *   后端 `RecordStatus.SENT` 是在 `submit_by_uid` 返回 `accepted=True`（已入池）
 *   时置位的 —— 那是**受理**；实测中平台拒收回执的条数可与受理数相当甚至更多，
 *   界面却把受理行全部显示成绿色「已发送」（用户原话：「投递成功不代表传递送达」）。
 *
 * 显示判据（优先级从高到低）：
 *   1. 有平台拒收证据 ⇒ **被平台拒绝**（红）—— 无论调度状态如何
 *   2. 有送达证据（回声帧/投递标记）⇒ **已送达**（绿）
 *   3. 其余沿用调度状态（已受理 / 发送失败 / 待发送 / 未私信）
 */
export function displayStatus(r: Row): [string, PillColor] {
  // 🔴 2026-09-30（用户实测「发送失败却显示已送达、且无文案」）：
  // 原顺序把 `delivered` 放在 `fail` 之前 ⇒ 只要该 uid 历史上有过回声帧，
  // 本次**失败**的记录也会被渲染成绿色「已送达」，而 dmText 为空 ⇒
  // 「已送达 + 未发送」自相矛盾。**失败是确定的负向事实，不得被历史证据掩盖**。
  // 现口径：拒绝 > 失败 > 送达 > 调度状态。
  if (r.deliveryState === "rejected") return ["被平台拒绝", "danger"];
  if (r.dmStatus === "fail") return DM_META.fail;
  if (r.deliveryState === "delivered") return ["已送达", "ok"];
  return DM_META[r.dmStatus];
}

/**
 * 2026-09-29：「异常」判据 —— **与展示同一真源**（过滤与状态列必须同一口径）。
 *
 * 为什么必须抽成 helper 而不能各写一份：
 *   过滤条件曾写成 `deliveryState==='rejected' || dmStatus==='fail'`，而状态列
 *   用的是 `displayStatus`。两者在 `dmStatus==='fail' && deliveryState==='delivered'`
 *   这一格上**判据相反** —— 该行会**通过「仅看异常」过滤，却被渲染成绿色「已送达」**
 *   （用户看到「异常列表里全是绿色已送达」）。
 *
 * 现约定：**异常 = 展示档 tone 为 danger 的行**（即渲染成「被平台拒绝」或「发送失败」）。
 * 直接读 `displayStatus` 的输出推导，故两者**在结构上不可能漂移**：
 * `deliveryState==='delivered'` 覆盖 `dmStatus==='fail'` 的格子渲染成 ok ⇒ 不算异常。
 */
export function isIssue(r: Row): boolean {
  return displayStatus(r)[1] === "danger";
}

/** 解析延迟抖动字符串：'50,120'/'50-120'/'50~120' -> [50,120]；'60' -> [60,60] */
export function parseDelayRange(raw: string): number[] {
  const s = (raw || "").trim();
  if (!s) return [40, 65];
  const m = s.split(/[,~\-\s]+/);
  if (m.length >= 2) {
    const lo = parseInt(m[0], 10);
    const hi = parseInt(m[1], 10);
    if (!isNaN(lo) && !isNaN(hi)) {
      return lo > hi ? [hi, lo] : [lo, hi];
    }
  }
  const v = parseInt(s, 10);
  if (!isNaN(v)) return [v, v];
  return [40, 65];
}

export interface LiveMsg {
  uid: string;
  nickname: string;
  content: string;
  ts: number;
  status?: string;
}

export interface RankUser {
  /** 名次（1 起） */
  rank: number;
  uid: string;
  nickname: string;
  score: number;
  /** 后端若给出「N万」文案则优先显示 */
  score_text?: string;
  avatar?: string;
}

export interface LiveStream {
  alive: boolean;
  room_id?: string | null;
  online_count: number;
  messages: LiveMsg[];
  heat_curve: number[];
  likes?: number;
  listening?: boolean;
  dmRunning?: boolean;
  dmPaused?: boolean;
  roomTitle?: string;
  liveUrl?: string;
  engineState?: string;
  statusMsg?: string;
  /** 贡献榜（2026-09-30，后端 /api/live/stream 承载；由 LiveChatHook 后台轮询上游） */
  contribution_rank?: RankUser[];
  /** 贡献榜空态说明：ok / empty / 需登录 / 拉取中 / 未启动监听 …（后端下发，前端不推断） */
  rankReason?: string;
}

export interface SendRecord {
  key: string;
  uid: string;
  nickname: string;
  sec_uid?: string | null;
  status?: string;
  reason?: string | null;
  /** 2026-09-08：结构化失败分类（后端 models/task.py 下发） */
  fail_kind?: string | null;
  fail_label?: string | null;
  fail_advice?: string | null;
  captured_at: number;
  send_at?: number | null;
  sent_at?: number | null;
  content?: string | null;
  /** 2026-09-30：后端下发的「尝试发送的文案」（失败行 content 为 null）。 */
  attempted_content?: string | null;
  comment: string;
  /**
   * 2026-09-29：**真实投递结局**（后端 `api/tasks.py::_records_from_adm` 派生）。
   *   · `delivered` —— 有回声帧 / 投递标记 ⇒ 服务端确认已达
   *   · `rejected`  —— 只有平台拒收回执 ⇒ **没送达**
   *   · `""`        —— 无证据
   * 注意它与 `status` 是**两个维度**：`status=sent` 只表示「已受理（入池）」。
   */
  delivery_state?: "delivered" | "rejected" | "";
}

export interface TaskConfig {
  live_url?: string;
  max_target?: number;
  keywords?: string[];
  // 2026-09-20：与后端 TaskConfig 契约对齐 —— 词库两种形态都合法
  //（后端已放宽为 List[Union[str, dict]]；直播间配置里的主形态是 [{text,enabled}]）
  dm_pool?: (string | { text: string; enabled?: boolean })[];
  delay_range?: [number, number];
  interval?: number;
}

export interface TaskListResponse {
  config?: TaskConfig;
  records?: SendRecord[];
  sent?: number;
  captured?: number;
}

export interface RealAcct {
  name: string;
  uid?: string | null;
  status?: string;
  level?: string; // ok=凭证有效；nosign/expired/missing/unknown 为非有效
  role?: string;
}

/** 账号是否有效（凭证齐全且探活通过）。后端 _to_raw_account 的 level==='ok' 即有效。 */
export function acctValid(a: RealAcct): boolean {
  return a.level === "ok";
}

export interface FeedItem {
  id: number;
  t: string;
  k: string;
  n: string;
  l: number;
  x: string;
}

export interface Row {
  id: number;
  time: string;
  name: string;
  lv: number;
  content: string;
  dmStatus: DmStatus;
  dmText: string;
  /** 2026-09-30：**尝试发送的文案**（失败时 dmText 为空，本字段仍有值）。 */
  attemptedContent?: string;
  dmTime: string;
  ts: number;
  reason?: string;
  /** 2026-09-08：结构化失败分类（弹窗展示具体原因） */
  failKind?: string | null;
  failLabel?: string | null;
  failAdvice?: string | null;
  /**
   * 2026-09-29：**真实投递结局**（后端 `services/delivery_verify.delivery_state_of`
   * 按投递证据派生；不是调度状态）。
   *   · `delivered` —— 有回声帧 / 投递标记 ⇒ 服务端确认已达
   *   · `rejected`  —— 只有平台拒收回执 ⇒ **没送达**
   *   · `""`        —— 无证据（保持既有语义，不臆断）
   */
  deliveryState?: "delivered" | "rejected" | "";
  /**
   * 2026-09-30：私信文案的**实际来源**（「AI」/「词库」/「原文」/「」）。
   * 用户实测反馈：界面上「根本不知道这个文本到底是词库，还是 AI 生成，
   * 还是兜底文档」。后端在取值点写入、随 records 下发，前端只呈现不推断。
   */
  contentSource?: string;
}

// ============================================================================
// 私信文案来源标签 + 发送内容预览（2026-09-30，用户实测反馈）
// ----------------------------------------------------------------------------
// 用户原话两条：
//   ① 「发送内容不需要完整的全部展示，显示前10个字就行，不要破坏表格结构」
//   ② 「发送私信的文本目前没有标识，根本不知道这个文本到底是词库，还是 AI
//      生成，还是兜底文档」
// 真源放在本文件（live-shared）而非各视图内联：直播页与查阅模式渲染的是**同一批
// records**，两处各写一份必然漂移（Canonical Contract Law）。
// 契约：来源值只由后端 `content_source` 给定，**前端绝不推断**；
//       取不到 ⇒ 不显示标签（宁缺勿错）。
// ============================================================================

/** 来源标签的呈现映射（键 = 后端 content_source 取值）。 */
export const SOURCE_META: Record<string, { label: string; color: string }> = {
  AI: { label: "AI", color: "var(--color-info)" },
  词库: { label: "词库", color: "var(--color-accent)" },
  原文: { label: "原文", color: "var(--color-warning)" },
};

/** 发送内容**只展示前 10 字**（用户口径：「显示前 10 个字就行」）。 */
export const DM_PREVIEW_CHARS = 10;

/** 取来源标签元数据；无来源值返回 undefined（不猜、不显示）。 */
export function sourceMetaOf(src: string | undefined | null) {
  return SOURCE_META[String(src || "")];
}

/** 2026-09-30：发送失败时的**可读缘由**（空串 = 非失败或无原因）。 */
export function dmFailReason(r: Row): string {
  return r.dmStatus === "fail" ? String(r.reason || "").trim() : "";
}

/** 2026-09-30：单元格正文 —— 优先「尝试发送的文案」（失败行也看得到试发了什么）。 */
export function dmPreviewText(r: Row): string {
  const body = r.attemptedContent || r.dmText || "";
  return body ? body.slice(0, DM_PREVIEW_CHARS) : "未发送";
}

/** `title` 提示用：把来源前缀与正文拼成可悬浮查看的完整文本。 */
export function dmTitle(r: Row): string {
  const m = sourceMetaOf(r.contentSource);
  const tag = m ? `【${m.label}】` : "";
  const body = r.attemptedContent || r.dmText || "未发送";
  const head =
    r.dmStatus === "fail" && r.reason
      ? `${body}\n失败原因: ${r.reason}`
      : body;
  return tag ? tag + head : head;
}

// ============================================================================
// 失败原因结构化说明（2026-09-08 用户要求）
// ----------------------------------------------------------------------------
// 目标：私信发送失败时，弹窗明确告知是「调度堵塞 / 凭证失效 / 账号风控 /
// 频控限流 / 参数错误 / 网络异常」中的哪一类，并给出可操作建议，
// 而不是只显示一行原始报错。
// 后端 models/task.py 已下发 fail_kind / fail_label / fail_advice；
// 老后端未下发时，前端用 localClassifyFail 兜底（保持兼容）。
// ============================================================================
export const FAIL_KIND_META: Record<string, { label: string; color: string; advice: string }> = {
  credential: {
    label: "凭证失效",
    color: "var(--color-danger)",
    advice: "私信签名已失效，请到「账号」页重新登录后重试",
  },
  risk: {
    label: "账号风控",
    color: "var(--color-danger)",
    advice:
      "该账号被判定为风控：降低发送频率，或暂停 30 分钟以上",
  },
  ratelimit: {
    label: "频控限流",
    color: "var(--color-warning)",
    advice:
      "已达发送频率上限，等冷却结束后再发",
  },
  blocked: {
    label: "调度堵塞",
    color: "var(--color-warning)",
    advice:
      "发送队列已满或排队超时，等队列消化后再启动",
  },
  param: {
    label: "参数错误",
    color: "var(--color-text-muted)",
    advice: "目标 uid 或文案不合法，请检查会话数据",
  },
  network: {
    label: "网络异常",
    color: "var(--color-warning)",
    advice: "服务不可达，请稍后重试",
  },
  other: {
    label: "其他原因",
    color: "var(--color-text-muted)",
    advice: "未能归类的失败，可展开原始原因排查",
  },
};

/** 后端未下发分类时的本地兜底（规则与后端 core/sender.classify_fail 保持一致） */
export function localClassifyFail(reason: string): string {
  const r = (reason || "").trim();
  const low = r.toLowerCase();
  if (!r) return "other";
  // ① 账号级风控必须最先判：KICK 文案常同时含 INVALID_REQUEST，
  //    但按后端 sender.py 注释，KICK 多为账号级反 spam 风控，不是签名失效。
  if (r.toUpperCase().includes("KICK") || r.includes("风控") || low.includes("spam") || r.includes("被限制"))
    return "risk";
  if (
    r.includes("签名三件套缺失") ||
    r.includes("需重新扫码") ||
    r.includes("INVALID_REQUEST") ||
    low.includes("unauthorized") ||
    r.includes("登录态") ||
    (low.includes("cookie") && r.includes("失效"))
  )
    return "credential";
  if (
    low.includes("rate_limited") ||
    r.includes("频繁") ||
    r.includes("冷静期") ||
    r.includes("冷却") ||
    r.includes("上限") ||
    r.includes("限流")
  )
    return "ratelimit";
  if (
    r.includes("堵塞") ||
    r.includes("队列") ||
    low.includes("queue") ||
    r.includes("超时") ||
    low.includes("timeout") ||
    low.includes("busy") ||
    r.includes("调度")
  )
    return "blocked";
  if (
    r.includes("为空") ||
    r.includes("非数字") ||
    (r.includes("缺失") && r.includes("账号")) ||
    (low.includes("uid") && r.includes("解析")) ||
    r.includes("会话整理")
  )
    return "param";
  if (
    r.includes("不可达") ||
    low.includes("connection") ||
    r.includes("连接") ||
    low.includes("network") ||
    r.includes("HTTP 5")
  )
    return "network";
  return "other";
}

/** 取最终展示用的失败说明（优先后端下发，缺失时本地兜底） */
export function failInfoOf(row: Row): { kind: string; label: string; color: string; advice: string; raw: string } {
  const raw = String(row.reason || "");
  const kind = row.failKind || localClassifyFail(raw);
  const meta = FAIL_KIND_META[kind] || FAIL_KIND_META.other;
  return {
    kind,
    label: row.failLabel || meta.label,
    color: meta.color,
    advice: row.failAdvice || meta.advice,
    raw,
  };
}

/** 失败原因弹窗 */
export function FailReasonModal({
  row,
  onClose,
}: {
  row: Row | null;
  onClose: () => void;
}) {
  if (!row) return null;
  const info = failInfoOf(row);
  // 2026-10-02：并入统一 Modal（原 z-[9999] + bg-black/45 无模糊 + surface-solid，全站最不一致的一处）
  return (
    <Modal open onClose={onClose} maxWidth="520px" labelledBy="fr-title">
      <ModalHeader
        onClose={onClose}
        title={<b id="fr-title" className="text-[0.95rem]">私信发送失败</b>}
        icon={
          <span
            className="rounded-full px-2.5 py-[3px] text-[0.75rem] font-semibold text-white"
            style={{ background: info.color }}
          >
            {info.label}
          </span>
        }
      />
      <ModalBody>
        <div className="mb-2.5 text-[0.82rem] text-[var(--color-text-muted)]">
          目标：{row.name || "未知"}
        </div>

        <div className="mb-3 rounded-[var(--radius-sm)] bg-[var(--color-accent-soft)] px-3 py-2.5
                        text-[0.82rem] leading-[1.7] text-[var(--color-text)]">
          {info.advice}
        </div>

        {info.raw ? (
          <details className="text-[0.75rem]">
            <summary className="cursor-pointer text-[var(--color-text-muted)]">
              原始原因（点击展开）
            </summary>
            <pre
              className="mt-2 whitespace-pre-wrap break-all rounded-[var(--radius-sm)]
                         bg-[var(--color-background-soft)] p-2.5 text-[0.72rem] leading-relaxed
                         text-[var(--color-text)]"
            >
              {info.raw}
            </pre>
          </details>
        ) : null}
      </ModalBody>
      <ModalFooter>
        <Button onClick={onClose}>我知道了</Button>
      </ModalFooter>
    </Modal>
  );
}

/** 后端英文枚举 status -> 前端 DmStatus（修复旧版中文字符串永不匹配的 bug） */
export function toDmStatus(s: string | null | undefined): DmStatus {
  switch ((s || "").toLowerCase()) {
    case "sent":
      return "sent";
    case "fail":
    case "failed":
    case "error":
      return "fail";
    case "captured":
    case "wait":
    case "waiting":
    case "pending":
    case "queued":
      return "wait";
    default:
      return "un";
  }
}

/** 服务器秒级时间戳 -> HH:MM:SS */
export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = (n: number) => (n < 10 ? "0" : "") + n;
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

/** 任务容器/历史任务 records（dict 快照）-> 查阅模式 Row 列表 */
export function recordsToRows(src: Record<string, unknown>[]): Row[] {
  return src.map((r, i) => ({
    id: 900000 + i,
    // 2026-09-17 修补（OCR 审查 HIGH —— 字段名不存在致时间列恒空）：
    // 原为 `r.start_ts`，但后端 `_records_from_adm`（api/tasks.py:99-130）的
    // 字段映射里**没有 start_ts**（只有 captured_at / send_at / send_ts）
    // → `time` 恒为 ""，"时间" 列永远空白。
    // 现按后端实际字段兜底取值：captured_at（采集时间，与 ts 同源）优先，
    // 退化到 send_at / send_ts。
    // 注意三种值的形态不同：captured_at 是**秒级时间戳**，send_at / send_ts
    // 是**已格式化字符串**，故分开处理。
    time: (() => {
      const ca = Number(r.captured_at) || 0;
      if (ca > 0) {
        const d = new Date(ca * 1000);
        const p = (n: number) => String(n).padStart(2, "0");
        return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
      }
      const s = String(r.send_at || r.send_ts || "");
      return s ? s.slice(11, 19) || s : "";
    })(),
    name: String(r.nickname || r.uid || "未知"),
    lv: 0,
    content: String(r.comment || r.content || ""),
    dmStatus: toDmStatus(String(r.status || "")),
    dmText: String(r.content || ""),
    attemptedContent: String(r.attempted_content || r.content || ""),
    dmTime: r.send_ts ? String(r.send_ts) : "",
    ts: (Number(r.captured_at) || 0) * 1000,
    reason: String(r.reason || ""),
    failKind: (r.fail_kind as string) || null,
    failLabel: (r.fail_label as string) || null,
    failAdvice: (r.fail_advice as string) || null,
    deliveryState: (r.delivery_state as "delivered" | "rejected" | "") || "",
  }));
}

/* ── 表格具名单元（避免每处重复 className，见 tasks.tsx 范式） ── */

export function Th({
  children,
  className,
  width,
}: {
  children?: React.ReactNode;
  className?: string;
  width?: number;
}) {
  return (
    <th
      style={width ? { width } : undefined}
      className={cn(
        "whitespace-nowrap border-b border-[var(--color-border)] px-2.5 py-2 text-left",
        // 2026-09-30：与 table-fixed 配合 —— 长昵称/长文案不再撑宽列。
        width ? "overflow-hidden text-ellipsis" : "",
        "text-[0.7rem] font-semibold tracking-[0.03em] text-[var(--color-text-secondary)]",
        className
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  mono,
  muted,
  className,
  colSpan,
  title,
  style,
}: {
  children?: React.ReactNode;
  mono?: boolean;
  muted?: boolean;
  className?: string;
  colSpan?: number;
  title?: string;
  style?: React.CSSProperties;
}) {
  return (
    <td
      colSpan={colSpan}
      title={title}
      style={style}
      className={cn(
        "border-b border-[var(--color-border)] px-2.5 py-2 align-middle",
        "text-[0.78rem] text-[var(--color-text)]",
        mono && "font-mono tabular-nums",
        muted && "text-[var(--color-text-muted)]",
        // 2026-10-02 修（用户实测「捕获私信列表出现溢出」）：
        // `table-fixed` 已固定列宽，但表格单元格默认 `overflow: visible`
        // ⇒ 超长内容（长昵称 / 长文案）**直接溢出到相邻列**，覆盖右侧单元格文本。
        // 补 overflow-hidden + 省略号，与表头 Th 的处置对齐（Th 早有 overflow-hidden
        // text-ellipsis）；colSpan 行（空态整行提示）不裁 —— 那是占位需完整显示。
        !colSpan && "overflow-hidden text-ellipsis",
        className
      )}
    >
      {children}
    </td>
  );
}
