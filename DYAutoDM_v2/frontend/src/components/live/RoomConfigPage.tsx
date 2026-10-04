/**
 * 直播策略编辑弹窗 —— **只保留直播策略，策略以外全部删除**
 *
 * ## 沿革（2026-09-19 用户定调，勿回退）
 * 用户原话：
 *   「直播策略放到直播监听页面中『直播间』板块的配置标签中，该编辑页面中**只保留
 *    直播策略**，策略以外的全部删除。不需要 tab 切换栏，也不需要子 tab 页面。
 *    『强制重扫』策略早就废弃了，彻底移除。」
 *
 * 因此本组件 = **纯策略编辑器**：
 *   - 保留：策略名称 / 发送上限 / 间隔 / 延迟抖动 / 私信词库 /
 *     自动申请连麦（含连麦方式）/ 监听账号
 *   - **删除**：直播间号、备注名、原直播链接、已废弃的「启动前强制重扫」
 *     （前三个属「身份」，归「直播间」板块输入框；最后一项属已废弃策略，全仓移除）
 *
 * 数据源：/api/live/config-tags（SQLite kv "live_room_configs"）
 *
 * ## ⚠️ 定调更新（2026-09-22，ADR-003，**勿再引用上面的旧断言**）
 *
 * 上面「身份字段全部归『直播间』板块输入框、策略是自足的」是在**没有房间登记层**
 * 的前提下成立的。2026-09-22 用户决定引入**直播间登记表**（房间层），因此：
 *
 *   - 「没有『目标直播间』体系」**不再成立**：现由「**直播间管理**」按钮
 *     （`RoomManagePage.tsx` → kv `live_rooms`）承载**身份 + 策略引用 + 脱敏开关**；
 *   - **本页契约未变**：仍是**纯策略编辑器**（零身份字段）—— 这是「房间与策略分离」
 *     的核心，别把身份字段加回这里；
 *   - **新增联动**：策略可被房间 `strategy_id` 引用 ⇒ 删除策略时后端
 *     **自动解绑**引用它的房间（响应 `unbound: N`），前端必须如实提示解绑数量。
 *
 * 规格：`docs/adr/ADR-003-live-room-registry.md`
 */
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { api, RoomConfig } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import { Blank } from "@/components/page/kit";

interface Props {
  /** 弹窗开关 */
  open: boolean;
  onClose: () => void;
  /** toast */
  push: (msg: string, holdMs?: number) => void;
  /** 写操作完成（保存/删除/重启）后通知父页刷新策略列表 */
  onChanged?: () => void;
  /** 选用某条策略（保存成功后立即回填直播页） */
  onApply?: (cfg: RoomConfig) => void;
}

/** 新建草稿：**只含策略字段**，不含任何身份字段 */
const EMPTY_DRAFT: Partial<RoomConfig> = {
  name: "",
  max_target: 100,
  interval: 60,
  delay: "50,120",
  auto_link_mic: false,
  dm_pool: [],
  link_mic_mode: "audio",
  // 写接口自动化（默认关 ⇒ 不改即零出站）
  danmaku_pool: [],
  danmaku_timer_enabled: false,
  danmaku_timer_min: 3,
  danmaku_timer_max: 6,
  like_batch_enabled: false,
  like_batch_total: 3000,
  like_batch_steps: 4,
  like_batch_step_max: 1000,
  like_batch_cooldown_sec: 150,
};

/** 后端 applied/not_applied 的英文键 → 中文名（汇报口径统一） */
const FIELD_CN: Record<string, string> = {
  max_target: "发送上限",
  interval: "间隔",
  delay_range: "延迟抖动",
  dm_pool: "私信词库",
  danmaku_pool: "弹幕文案库",
  danmaku_timer_enabled: "定时发弹幕",
  like_batch_enabled: "分步批量点赞",
  live_url: "直播间",
  acct: "监听账号",
};

const cnFields = (keys?: string[]): string =>
  (keys || []).map((k) => FIELD_CN[k] || k).join("、");

/** 策略唯一键（以 id 为准，兼容旧数据的 room_id） */
const sidOf = (c: RoomConfig): string => String(c.id || c.room_id || "");

export default function RoomConfigPage({ open, onClose, push, onChanged, onApply }: Props) {
  const [items, setItems] = useState<RoomConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<Partial<RoomConfig>>({ ...EMPTY_DRAFT });
  const [editing, setEditing] = useState<string | null>(null); // 正在编辑的策略 id
  /** 正在「重启」的策略 id（防重复点击） */
  const [restarting, setRestarting] = useState<string | null>(null);
  /** 草稿模式：表单有变化/有写入即 true（取代原「新建策略」按钮） */
  const [dirty, setDirty] = useState(false);
  /**
   * 草稿模式（2026-09-19 用户定调，替代原「新建策略」按钮）。
   *
   * 用户原话：
   *   「既然新建策略按钮的本质也需要通过保存策略按钮才能固化写入，那不如直接取消
   *    新建策略的按钮……只要有变化或者有写入，则默认变成草稿模式，然后通过点击
   *    保存策略就可以新建完成。」
   *
   * 因此**没有显式的"新建"动作**：表单任意字段一改 → dirty=true → 进入草稿模式
   * （列表区出现草稿行，这是用户可见的状态），点「保存策略」落库即完成新建。
   * `[data-od-id="strategy-new"]` 按钮回归其本义：**清空表单**。
   */
  /** 改任意字段 = 进入草稿模式（这是"新建"的唯一入口语义） */
  const touch = (patch: Partial<RoomConfig>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setDirty(true);
  };

  /**
   * 草稿行是否显示：**非编辑态**且有改动（dirty）→ 用户在新建一条。
   * 编辑已有策略时不显示（那条已在列表中，用 ✎ 高亮标记即可）。
   */
  const draftActive = dirty && !editing;

  const load = () => {
    setLoading(true);
    api
      .listRoomConfigs()
      .then((r) => setItems(r.items || []))
      .catch((e: unknown) => push("加载策略失败: " + errMsg(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (open) {
      load();
      setDraft({ ...EMPTY_DRAFT });
      setEditing(null);
      setDirty(false);     // 打开时是干净的浏览态，改动后才进草稿模式
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const save = () => {
    const name = String(draft.name || "").trim();
    if (!name) {
      push("请填写策略名称（如：保守-慢速）");
      return;
    }
    api
      .saveRoomConfig({ ...draft, name, id: editing || undefined } as
        Partial<RoomConfig> & { id?: string })
      .then((r) => {
        if (r.ok) {
          push(
            `已保存直播策略「${r.config?.name || r.config?.id}」` +
              (r.config?.auto_link_mic ? "（含自动申请连麦）" : ""),
          );
          setDraft({ ...EMPTY_DRAFT });
          setEditing(null);
          setDirty(false);
          onChanged?.();
          load();
          if (r.config) onApply?.(r.config);
        } else {
          push("保存失败: " + (r.error || "未知错误"));
        }
      })
      .catch((e: unknown) => push("保存异常: " + errMsg(e)));
  };

  const remove = (sid: string) => {
    api
      .deleteRoomConfig(sid)
      .then((r) => {
        if (r.ok) {
          // ADR-003 §3.4：删除策略会自动解绑引用它的房间，必须**如实**提示解绑数量
          const unbound = (r as { unbound?: number }).unbound || 0;
          push(
            `已删除直播策略 ${sid}` +
              (unbound ? `（自动解绑 ${unbound} 个引用它的直播间）` : ""),
            unbound ? 8000 : undefined,
          );
          onChanged?.();
          load();
        } else push("删除失败: " + (r.error || ""));
      })
      .catch((e: unknown) => push("删除异常: " + errMsg(e)));
  };

  /** 「选用」：把该策略回填到直播页（不写库、不起任务） */
  const useStrategy = (sid: string) => {
    const cfg = items.find((x) => sidOf(x) === sid);
    if (cfg) onApply?.(cfg);
    push(`已选用策略「${cfg?.name || sid}」`);
    onClose();
  };

  const edit = (cfg: RoomConfig) => {
    setEditing(sidOf(cfg));
    setDraft({ ...cfg });
    setDirty(false);     // 刚点开不算草稿；改动后才算
  };

  /**
   * 「重启」：保存该策略 + 热更到正在运行的监听任务（不中断监听）。
   *
   * 后端契约见 `api.restartRoomConfig` —— 三种结果都必须如实告知：
   *   ① 引擎运行中：只列**实际生效**字段（applied）；
   *   ② 引擎未运行：明说「已保存，点开始自动私信后生效」，不谎称已重启；
   *   ③ 换直播间/换账号：明说热更不覆盖（需停止后重新开始）。
   */
  const restart = (sid: string) => {
    if (restarting) return;
    setRestarting(sid);
    api
      .restartRoomConfig(sid)
      .then((r) => {
        if (!r.ok) {
          push("重启失败: " + (r.error || "未知错误"));
          return;
        }
        const rs = r.restart || { ok: false, applied: [], not_applied: [] };
        const applied = cnFields(r.applied_fields);
        const skipped = cnFields(rs.not_applied_fields);
        if (rs.ok) {
          push(
            `已重启「${sid}」· 监听未中断` +
              (applied ? `｜已生效：${applied}` : "") +
              (skipped ? `｜未生效：${skipped}` : ""),
            6000,
          );
        } else {
          push(
            `策略已保存到「${sid}」，但未能热更到运行中的任务：` +
              (rs.reason || "引擎未运行") +
              (skipped ? `（${skipped} 需停止后重新开始）` : ""),
            8000,
          );
        }
        load();
        onChanged?.();
      })
      .catch((e: unknown) => push("重启异常: " + errMsg(e)))
      .finally(() => setRestarting(null));
  };

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[var(--z-modal)] flex items-center justify-center modal-scrim p-5"
      onClick={onClose}
    >
      <div
        className="modal-surface max-h-[86vh] w-[720px] max-w-[92vw] overflow-auto
                   rounded-[var(--radius-xl)]"
              onClick={(e) => e.stopPropagation()}
              data-od-id="live-strategy-modal"
      >
        <div className="flex items-center justify-between border-b border-[var(--color-border)]
                        px-4 py-3">
          <h3 className="text-[0.95rem] font-semibold text-[var(--color-text)]">
            直播策略
          </h3>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="p-4">
          {/* 策略列表 */}
          <div style={{ marginBottom: 14 }}>
            <div className="mb-2 text-[0.78rem] font-semibold text-[var(--color-text)]">
              已保存策略（{items.length}）
            </div>
            {loading && (
              <div className="text-[0.74rem] text-[var(--color-text-muted)]">加载中…</div>
            )}
            {!loading && items.length === 0 && !draftActive && (
              <Blank>暂无策略。在下方「策略详情」里填写后点「保存策略」即新建。</Blank>
            )}
            {/* 草稿行（2026-09-19 用户定调）：**表单一有变化就自动出现**，
                不再需要「新建策略」按钮。这里就是用户指明的列表区位置。 */}
            {draftActive && (
              <div
                data-od-id="strategy-draft-row"
                className="flex flex-wrap items-center gap-2 rounded-[var(--radius-sm)]
                           border border-dashed border-[var(--color-accent)]
                           bg-[var(--color-surface-raised)] px-2.5 py-2"
                style={{ marginBottom: 6 }}
              >
                <div className="min-w-[200px] flex-1">
                  <div style={{ fontSize: 13, fontWeight: 600, color: "var(--color-accent)" }}>
                    ● 草稿（未保存）
                  </div>
                  <div className="mono" style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
                    点「保存策略」即新建完成
                  </div>
                </div>
              </div>
            )}
            {items.map((cfg) => {
              const sid = sidOf(cfg);
              return (
                <div
                  key={sid}
                  data-od-id={"strategy-row-" + sid}
                  className={
                    "flex flex-wrap items-center gap-2 border-b px-2.5 py-2" +
                    (editing === sid
                      ? " bg-[var(--color-surface-raised)] border-l-2 border-l-[var(--color-accent)]"
                      : " border-[var(--color-border)]")
                  }
                >
                  <div className="min-w-[200px] flex-1">
                    <div style={{ fontSize: 13, fontWeight: 600 }}>
                      {editing === sid ? `✎ 编辑中：${cfg.name || sid}` : (cfg.name || sid)}
                      {cfg.auto_link_mic && (
                        <span
                          className="mono"
                          style={{ marginLeft: 8, fontSize: 11, color: "var(--color-accent)" }}
                        >
                          自动连麦({cfg.link_mic_mode === "video" ? "视频" : "语音"})
                        </span>
                      )}
                    </div>
                    <div className="mono" style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
                      上限{cfg.max_target ?? "—"} · 间隔{cfg.interval ?? "—"}s · 抖动
                      {cfg.delay || "—"}
                      {cfg.acct ? ` · 账号:${cfg.acct}` : ""}
                    </div>
                  </div>
                  <Button
                    variant="default"
                    size="sm"
                    disabled={restarting === sid}
                    title="保存该策略并立即热更到正在运行的监听任务（不中断监听）"
                    onClick={() => restart(sid)}
                  >
                    {restarting === sid ? "重启中…" : "重启"}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => useStrategy(sid)}>
                    选用
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => edit(cfg)}>
                    编辑
                  </Button>
                  <Button variant="danger-outline" size="sm" onClick={() => remove(sid)}>
                    删除
                  </Button>
                </div>
              );
            })}
          </div>

          {/* 策略详情表单：**只有策略字段** */}
          <div className="mb-1 text-[0.74rem] font-semibold text-[var(--color-text)]">
            策略详情
            {editing && (
              <span className="ml-2 font-normal text-[var(--color-text-muted)]">
                （编辑中：{items.find((x) => sidOf(x) === editing)?.name || editing}）
              </span>
            )}
            {!editing && dirty && (
              <span className="ml-2 font-normal text-[var(--color-accent)]">（草稿）</span>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2.5 border-t border-[var(--color-border)] pt-3">
            <div className="col-span-2 flex flex-col gap-1">
              <label>策略名称</label>
              <Input
                value={draft.name || ""}
                onChange={(e) => touch({ name: e.target.value })}
                placeholder="如：标准-快速 / 保守-慢速"
                data-od-id="strategy-name"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label>发送上限</label>
              <Input
                type="number"
                value={draft.max_target ?? 100}
                onChange={(e) => touch({ max_target: parseInt(e.target.value, 10) || 0 })}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label>间隔（秒）</label>
              <Input
                type="number"
                value={draft.interval ?? 60}
                onChange={(e) => touch({ interval: parseFloat(e.target.value) || 0 })}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label>延迟抖动（秒）</label>
              <Input
                value={draft.delay || ""}
                onChange={(e) => touch({ delay: e.target.value })}
                placeholder="50,120"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label>监听账号</label>
              <Input
                value={draft.acct || ""}
                onChange={(e) => touch({ acct: e.target.value })}
                placeholder="留空 = 使用页面当前选择"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label>连麦方式</label>
              <Select
                value={draft.link_mic_mode || "audio"}
                onValueChange={(v) =>
                  touch({ link_mic_mode: v as "audio" | "video" })
                }
                disabled={!draft.auto_link_mic}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="audio">语音连线</SelectItem>
                  <SelectItem value="video">视频连线</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <label className="col-span-2 flex items-center gap-2 text-[0.74rem]">
              <Switch
                checked={!!draft.auto_link_mic}
                onCheckedChange={(v) => touch({ auto_link_mic: v })}
              />
              <span style={{ color: "var(--color-text-muted)" }}>
                自动申请连麦（引擎开播监听后自动对本期直播间发起连麦申请）
              </span>
            </label>
            <div className="col-span-2 flex flex-col gap-1.5 border-t
                            border-[var(--color-border)] pt-3">
              <div className="flex items-center gap-2 text-[0.74rem]">
                <span style={{ color: "var(--color-text-muted)" }}>私信词库</span>
                <span style={{ color: "var(--color-text-muted)", opacity: 0.8 }}>
                  发送时随机抽已启用的一条；至少 1 条启用才能发出私信
                </span>
              </div>
              {(draft.dm_pool || []).map((t, idx) => (
                <div key={idx} className="flex items-center gap-2">
                  <Switch
                    checked={t.enabled !== false}
                    onCheckedChange={(v) => {
                      const pool = [...(draft.dm_pool || [])];
                      pool[idx] = { ...pool[idx], enabled: v };
                      touch({ dm_pool: pool });
                    }}
                  />
                  <Input
                    value={t.text || ""}
                    placeholder="私信文案（如：你好，看到您咨询工伤问题）"
                    onChange={(e) => {
                      const pool = [...(draft.dm_pool || [])];
                      pool[idx] = { ...pool[idx], text: e.target.value };
                      touch({ dm_pool: pool });
                    }}
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    title="删除该条文案"
                    onClick={() => {
                      const pool = (draft.dm_pool || []).filter((_, k) => k !== idx);
                      touch({ dm_pool: pool });
                    }}
                  >
                    删除
                  </Button>
                </div>
              ))}
              <div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => touch({
                    dm_pool: [...(draft.dm_pool || []), { text: "", enabled: true }],
                  })}
                >
                  + 新增一条文案
                </Button>
              </div>
            </div>

            {/* ===== 写接口自动化（2026-10-01）=====
                默认全关 ⇒ 不改这里 = 零出站。风控：弹幕/点赞都是**写接口**。 */}
            <div className="col-span-2 flex flex-col gap-1.5 border-t
                            border-[var(--color-border)] pt-3">
              <div className="flex items-center gap-2 text-[0.74rem]">
                <span style={{ color: "var(--color-text-muted)" }}>弹幕文案库</span>
                <span style={{ color: "var(--color-text-muted)", opacity: 0.8 }}>
                  定时弹幕随机抽已启用的一条
                </span>
              </div>
              {(draft.danmaku_pool || []).map((t, idx) => (
                <div key={idx} className="flex items-center gap-2">
                  <Switch
                    checked={t.enabled !== false}
                    onCheckedChange={(v) => {
                      const pool = [...(draft.danmaku_pool || [])];
                      pool[idx] = { ...pool[idx], enabled: v };
                      touch({ danmaku_pool: pool });
                    }}
                  />
                  <Input
                    value={t.text || ""}
                    placeholder="弹幕文案（如：有工伤问题可以打在公屏）"
                    onChange={(e) => {
                      const pool = [...(draft.danmaku_pool || [])];
                      pool[idx] = { ...pool[idx], text: e.target.value };
                      touch({ danmaku_pool: pool });
                    }}
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    title="删除该条文案"
                    onClick={() => touch({
                      danmaku_pool: (draft.danmaku_pool || []).filter((_, k) => k !== idx),
                    })}
                  >
                    删除
                  </Button>
                </div>
              ))}
              <div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => touch({
                    danmaku_pool: [...(draft.danmaku_pool || []), { text: "", enabled: true }],
                  })}
                >
                  + 新增一条弹幕文案
                </Button>
              </div>
            </div>

            <div className="col-span-2 flex flex-col gap-2 border-t
                            border-[var(--color-border)] pt-3">
              <label className="flex items-center gap-2 text-[0.74rem]">
                <Switch
                  checked={!!draft.danmaku_timer_enabled}
                  onCheckedChange={(v) => touch({ danmaku_timer_enabled: v })}
                />
                <span style={{ color: "var(--color-text-muted)" }}>
                  定时发弹幕（监听中按随机间隔自动发一条）
                </span>
              </label>
              <div className="flex items-center gap-2 text-[0.74rem]"
                   style={{ color: "var(--color-text-muted)" }}>
                <span>间隔</span>
                <Input
                  className="w-[80px]" type="number" min="0.5" step="0.5"
                  aria-label="弹幕间隔下限"
                  value={draft.danmaku_timer_min ?? 3}
                  onChange={(e) => touch({ danmaku_timer_min: parseFloat(e.target.value) || 3 })}
                />
                <span>~</span>
                <Input
                  className="w-[80px]" type="number" min="0.5" step="0.5"
                  aria-label="弹幕间隔上限"
                  value={draft.danmaku_timer_max ?? 6}
                  onChange={(e) => touch({ danmaku_timer_max: parseFloat(e.target.value) || 6 })}
                />
                <span>分钟（每轮在此区间随机）</span>
              </div>
            </div>

            <div className="col-span-2 flex flex-col gap-2 border-t
                            border-[var(--color-border)] pt-3">
              <label className="flex items-center gap-2 text-[0.74rem]">
                <Switch
                  checked={!!draft.like_batch_enabled}
                  onCheckedChange={(v) => touch({ like_batch_enabled: v })}
                />
                <span style={{ color: "var(--color-text-muted)" }}>
                  分步批量点赞（分多步完成，步间冷却，模拟真人节奏）
                </span>
              </label>
              <div className="flex flex-wrap items-center gap-2 text-[0.74rem]"
                   style={{ color: "var(--color-text-muted)" }}>
                <span>总点赞</span>
                <Input className="w-[90px]" type="number" min="1"
                       aria-label="批量点赞总数"
                       value={draft.like_batch_total ?? 3000}
                       onChange={(e) => touch({ like_batch_total: parseInt(e.target.value, 10) || 3000 })} />
                <span>分</span>
                <Input className="w-[70px]" type="number" min="1" max="50"
                       aria-label="分几步完成"
                       value={draft.like_batch_steps ?? 4}
                       onChange={(e) => touch({ like_batch_steps: parseInt(e.target.value, 10) || 4 })} />
                <span>步</span>
                <span className="opacity-80">
                  （单步上限 {draft.like_batch_step_max ?? 1000}，超出自动加步）
                </span>
                <span>冷却</span>
                <Input className="w-[80px]" type="number" min="120"
                       aria-label="步间冷却秒数"
                       value={draft.like_batch_cooldown_sec ?? 150}
                       onChange={(e) => touch({ like_batch_cooldown_sec: parseInt(e.target.value, 10) || 150 })} />
                <span>秒</span>
              </div>
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-[var(--color-border)] px-4 py-3">
          <Button variant="secondary" onClick={onClose}>
            关闭
          </Button>
          <Button
            variant="secondary"
            data-od-id="strategy-new"
            title="清空表单，回到干净的浏览态"
            onClick={() => {
              setEditing(null);
              setDraft({ ...EMPTY_DRAFT });
              setDirty(false);
              push("已清空表单");
            }}
          >
            清空表单
          </Button>
          <Button onClick={save}>
            {editing ? "更新策略" : "保存策略"}
          </Button>
        </div>
      </div>
    </div>,
    document.body
  );
}

function errMsg(e: unknown): string {
  return (e as { message?: string })?.message || String(e);
}
