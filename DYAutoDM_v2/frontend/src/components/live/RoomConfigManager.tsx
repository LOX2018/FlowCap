/**
 * 直播间配置管理弹窗（按直播间号管理配置 + 自动申请连麦）
 *
 * 2026-09-10 新增。数据源：/api/live/room-configs（SQLite kv "live_room_configs"）。
 * 配置项 = 现有直播任务全部配置 + auto_link_mic（自动申请连麦）+ link_mic_mode。
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
  open: boolean;
  onClose: () => void;
  /** 当前输入框的直播间（用于「保存当前」预填） */
  currentRoom: string;
  /** toast */
  push: (msg: string, holdMs?: number) => void;
  /** 应用某条配置到页面（回填输入框与各配置项） */
  onApply: (cfg: RoomConfig) => void;
}

const EMPTY_DRAFT: Partial<RoomConfig> = {
  room_id: "",
  name: "",
  max_target: 100,
  interval: 60,
  delay: "50,120",
  force_rescan: false,
  auto_link_mic: false,
  link_mic_mode: "audio",
};

/** 后端 applied/not_applied 的英文键 → 中文名（汇报口径统一） */
const FIELD_CN: Record<string, string> = {
  max_target: "发送上限",
  interval: "间隔",
  delay_range: "延迟抖动",
  dm_pool: "私信词库",
  live_url: "直播间链接",
  acct: "监听账号",
  force_rescan: "强制重扫",
};

const cnFields = (keys?: string[]): string =>
  (keys || []).map((k) => FIELD_CN[k] || k).join("、");

export default function RoomConfigManager({ open, onClose, currentRoom, push, onApply }: Props) {
  const [items, setItems] = useState<RoomConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<Partial<RoomConfig>>({ ...EMPTY_DRAFT });
  const [editing, setEditing] = useState<string | null>(null); // 正在编辑的 room_id
  /** 正在「重启」的 room_id（防重复点击） */
  const [restarting, setRestarting] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    api
      .listRoomConfigs()
      .then((r) => setItems(r.items || []))
      .catch((e: unknown) => push("加载配置失败: " + errMsg(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (open) {
      load();
      // 用当前输入框预填新建草稿
      const rid = extractRoomId(currentRoom);
      setDraft({ ...EMPTY_DRAFT, room_id: rid || "", live_url: currentRoom || "" });
      setEditing(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const save = () => {
    const rid = draft.room_id?.trim();
    if (!rid) {
      push("请填写直播间号或 URL");
      return;
    }
    api
      .saveRoomConfig({ ...draft, room_id: rid } as RoomConfig & { room_id: string })
      .then((r) => {
        if (r.ok) {
          push(`已保存直播间 ${r.config?.room_id} 的配置` + (r.config?.auto_link_mic ? "（含自动申请连麦）" : ""));
          setDraft({ ...EMPTY_DRAFT });
          setEditing(null);
          load();
        } else {
          push("保存失败: " + (r.error || "未知错误"));
        }
      })
      .catch((e: unknown) => push("保存异常: " + errMsg(e)));
  };

  const remove = (roomId: string) => {
    api
      .deleteRoomConfig(roomId)
      .then((r) => {
        if (r.ok) {
          push(`已删除直播间 ${roomId} 的配置`);
          load();
        } else push("删除失败: " + (r.error || ""));
      })
      .catch((e: unknown) => push("删除异常: " + errMsg(e)));
  };

  const applyToTask = (roomId: string) => {
    api
      .applyRoomConfig(roomId)
      .then((r) => {
        if (r.ok) {
          const cfg = items.find((x) => x.room_id === roomId);
          if (cfg) onApply(cfg);
          push(`已应用直播间 ${roomId} 的配置到当前任务`);
        } else push("应用失败: " + (r.error || ""));
      })
      .catch((e: unknown) => push("应用异常: " + errMsg(e)));
  };

  const edit = (cfg: RoomConfig) => {
    setEditing(cfg.room_id);
    setDraft({ ...cfg });
  };

  /**
   * 「重启标签」：保存配置 + 热更到正在运行的监听任务。
   *
   * 后端契约见 `api.restartRoomConfig` —— 三种结果都必须如实告诉用户：
   *   ① 引擎运行中：仅列出**实际生效**的字段（applied）；
   *   ② 引擎未运行：明说「已保存，点开始自动私信后生效」，不谎称已重启；
   *   ③ 换直播间/换账号/强制重扫：明说这类改动热更不覆盖（需停止后重新开始）。
   */
  const restart = (roomId: string) => {
    if (restarting) return;
    setRestarting(roomId);
    api
      .restartRoomConfig(roomId)
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
            `已重启「${roomId}」· 监听未中断` +
              (applied ? `｜已生效：${applied}` : "") +
              (skipped ? `｜未生效：${skipped}` : ""),
            6000,
          );
        } else {
          push(
            `配置已保存到「${roomId}」，但未能热更到运行中的任务：` +
              (rs.reason || "引擎未运行") +
              (skipped ? `（${skipped} 需停止后重新开始）` : ""),
            8000,
          );
        }
        load();
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
        className="glass-premium w-[720px] max-w-[92vw] max-h-[86vh] overflow-auto
                   rounded-[var(--radius-xl)]"
        onClick={(e) => e.stopPropagation()}
        data-od-id="room-config-modal"
      >
        <div className="flex items-center justify-between border-b border-[var(--color-border)]
                        px-4 py-3">
          <h3 className="text-[0.95rem] font-semibold text-[var(--color-text)]">
            直播间配置管理
          </h3>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="p-4">
          {/* 配置列表 */}
          <div style={{ marginBottom: 14 }}>
            {loading && (
              <div className="text-[0.74rem] text-[var(--color-text-muted)]">加载中…</div>
            )}
            {!loading && items.length === 0 && (
              <Blank>暂无配置。填写下方表单保存第一个直播间配置。</Blank>
            )}
            {items.map((cfg) => (
              <div
                key={cfg.room_id}
                className="flex flex-wrap items-center gap-2 border-b
                           border-[var(--color-border)] px-2.5 py-2"
              >
                <div className="min-w-[200px] flex-1">
                  <div style={{ fontSize: 13, fontWeight: 600 }}>
                    {cfg.name || cfg.room_id}
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
                    {cfg.room_id} · 上限{cfg.max_target ?? "—"} · 间隔{cfg.interval ?? "—"}s · 抖动
                    {cfg.delay || "—"}
                    {cfg.acct ? ` · 账号:${cfg.acct}` : ""}
                  </div>
                </div>
                <Button
                  variant="default"
                  size="sm"
                  disabled={restarting === cfg.room_id}
                  title="保存该配置并立即热更到正在运行的监听任务（不中断监听）"
                  onClick={() => restart(cfg.room_id)}
                >
                  {restarting === cfg.room_id ? "重启中…" : "重启"}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => applyToTask(cfg.room_id)}>
                  应用
                </Button>
                <Button variant="ghost" size="sm" onClick={() => edit(cfg)}>
                  编辑
                </Button>
                <Button variant="danger-outline" size="sm" onClick={() => remove(cfg.room_id)}>
                  删除
                </Button>
              </div>
            ))}
          </div>

          {/* 编辑/新建表单 */}
          <div className="grid grid-cols-2 gap-2.5 border-t border-[var(--color-border)] pt-3">
            <div className="col-span-2 flex flex-col gap-1">
              <label>直播间号或 URL {editing && `(编辑中: ${editing})`}</label>
              <Input
                
                value={draft.room_id || ""}
                onChange={(e) => setDraft({ ...draft, room_id: e.target.value })}
                placeholder="如 840377749201 或 https://live.douyin.com/..."
                disabled={!!editing}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label>备注名</label>
              <Input
                
                value={draft.name || ""}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="如 张老师工伤直播间"
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
            <label className="col-span-2 flex items-center gap-2 text-[0.74rem]">
              <Switch
                checked={!!draft.force_rescan}
                onCheckedChange={(v) => setDraft({ ...draft, force_rescan: v })}
              />
              <span style={{ color: "var(--color-text-muted)" }}>强制重扫</span>
            </label>
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
              setDraft({ ...EMPTY_DRAFT, room_id: extractRoomId(currentRoom) || "" });
            }}
          >
            清空表单
          </Button>
          <Button onClick={save}>
            {editing ? "更新配置" : "保存配置"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** 从输入提取直播间号 */
function extractRoomId(raw: string): string {
  const s = (raw || "").trim();
  if (/^\d+$/.test(s)) return s;
  const m = s.match(/live\.douyin\.com\/(\d+)/);
  return m ? m[1] : "";
}

function errMsg(e: unknown): string {
  return (e as { message?: string })?.message || String(e);
}
