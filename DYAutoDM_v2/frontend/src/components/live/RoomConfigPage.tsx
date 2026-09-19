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
 */
import { useEffect, useState } from "react";
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
};

/** 后端 applied/not_applied 的英文键 → 中文名（汇报口径统一） */
const FIELD_CN: Record<string, string> = {
  max_target: "发送上限",
  interval: "间隔",
  delay_range: "延迟抖动",
  dm_pool: "私信词库",
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
          push(`已删除直播策略 ${sid}`);
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

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-5 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="glass-premium max-h-[86vh] w-[720px] max-w-[92vw] overflow-auto
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
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[0.78rem] font-semibold text-[var(--color-text)]">
                已保存策略（{items.length}）
              </span>
              <Button
                variant="secondary"
                size="sm"
                data-od-id="strategy-new"
                title="清空表单，开始新建一条策略"
                onClick={() => {
                  setEditing(null);
                  setDraft({ ...EMPTY_DRAFT });
                }}
              >
                ＋ 新建策略
              </Button>
            </div>
            {loading && (
              <div className="text-[0.74rem] text-[var(--color-text-muted)]">加载中…</div>
            )}
            {!loading && items.length === 0 && (
              <Blank>暂无策略。点右上「新建策略」或直接在下方表单填写后保存。</Blank>
            )}
            {items.map((cfg) => {
              const sid = sidOf(cfg);
              return (
                <div
                  key={sid}
                  className="flex flex-wrap items-center gap-2 border-b
                             border-[var(--color-border)] px-2.5 py-2"
                >
                  <div className="min-w-[200px] flex-1">
                    <div style={{ fontSize: 13, fontWeight: 600 }}>
                      {cfg.name || sid}
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

          {/* 新建 / 编辑表单：**只有策略字段** */}
          <div className="grid grid-cols-2 gap-2.5 border-t border-[var(--color-border)] pt-3">
            <div className="col-span-2 flex flex-col gap-1">
              <label>
                策略名称
                {editing && `（编辑中: ${items.find((x) => sidOf(x) === editing)?.name || editing}）`}
              </label>
              <Input
                value={draft.name || ""}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="如：标准-快速 / 保守-慢速"
                data-od-id="strategy-name"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label>发送上限</label>
              <Input
                type="number"
                value={draft.max_target ?? 100}
                onChange={(e) => setDraft({ ...draft, max_target: parseInt(e.target.value, 10) || 0 })}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label>间隔（秒）</label>
              <Input
                type="number"
                value={draft.interval ?? 60}
                onChange={(e) => setDraft({ ...draft, interval: parseFloat(e.target.value) || 0 })}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label>延迟抖动（秒）</label>
              <Input
                value={draft.delay || ""}
                onChange={(e) => setDraft({ ...draft, delay: e.target.value })}
                placeholder="50,120"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label>监听账号</label>
              <Input
                value={draft.acct || ""}
                onChange={(e) => setDraft({ ...draft, acct: e.target.value })}
                placeholder="留空 = 使用页面当前选择"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label>连麦方式</label>
              <Select
                value={draft.link_mic_mode || "audio"}
                onValueChange={(v) =>
                  setDraft({ ...draft, link_mic_mode: v as "audio" | "video" })
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
                onCheckedChange={(v) => setDraft({ ...draft, auto_link_mic: v })}
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
                      setDraft({ ...draft, dm_pool: pool });
                    }}
                  />
                  <Input
                    value={t.text || ""}
                    placeholder="私信文案（如：你好，看到您咨询工伤问题）"
                    onChange={(e) => {
                      const pool = [...(draft.dm_pool || [])];
                      pool[idx] = { ...pool[idx], text: e.target.value };
                      setDraft({ ...draft, dm_pool: pool });
                    }}
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    title="删除该条文案"
                    onClick={() => {
                      const pool = (draft.dm_pool || []).filter((_, k) => k !== idx);
                      setDraft({ ...draft, dm_pool: pool });
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
                  onClick={() => setDraft({
                    ...draft,
                    dm_pool: [...(draft.dm_pool || []), { text: "", enabled: true }],
                  })}
                >
                  + 新增一条文案
                </Button>
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
            onClick={() => {
              setEditing(null);
              setDraft({ ...EMPTY_DRAFT });
            }}
          >
            清空表单
          </Button>
          <Button onClick={save}>
            {editing ? "更新策略" : "保存策略"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function errMsg(e: unknown): string {
  return (e as { message?: string })?.message || String(e);
}
