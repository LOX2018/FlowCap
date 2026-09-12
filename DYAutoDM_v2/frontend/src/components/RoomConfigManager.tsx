/**
 * 直播间配置管理弹窗（按直播间号管理配置 + 自动申请连麦）
 *
 * 2026-09-10 新增。数据源：/api/live/room-configs（SQLite kv "live_room_configs"）。
 * 配置项 = 现有直播任务全部配置 + auto_link_mic（自动申请连麦）+ link_mic_mode。
 */
import { useEffect, useState } from "react";
import { api, RoomConfig } from "../api/client";

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

export default function RoomConfigManager({ open, onClose, currentRoom, push, onApply }: Props) {
  const [items, setItems] = useState<RoomConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<Partial<RoomConfig>>({ ...EMPTY_DRAFT });
  const [editing, setEditing] = useState<string | null>(null); // 正在编辑的 room_id

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

  if (!open) return null;

  return (
    <div className="modal-mask" onClick={onClose}>
      <div
        className="modal-card"
        onClick={(e) => e.stopPropagation()}
        style={{ width: 720, maxWidth: "92vw", maxHeight: "86vh", overflow: "auto" }}
        data-od-id="room-config-modal"
      >
        <div className="modal-head">
          <h3>直播间配置管理</h3>
          <span className="x" style={{ cursor: "pointer" }} onClick={onClose}>×</span>
        </div>
        <div className="modal-body">
          {/* 配置列表 */}
          <div style={{ marginBottom: 14 }}>
            {loading && <div style={{ color: "var(--muted)", fontSize: 12 }}>加载中…</div>}
            {!loading && items.length === 0 && (
              <div style={{ color: "var(--muted)", fontSize: 12, padding: "12px 0" }}>
                暂无配置。填写下方表单保存第一个直播间配置。
              </div>
            )}
            {items.map((cfg) => (
              <div
                key={cfg.room_id}
                className="head-row"
                style={{
                  padding: "8px 10px",
                  borderBottom: "1px solid var(--border)",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                <div style={{ flex: 1, minWidth: 200 }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>
                    {cfg.name || cfg.room_id}
                    {cfg.auto_link_mic && (
                      <span
                        className="mono"
                        style={{ marginLeft: 8, fontSize: 11, color: "var(--accent)" }}
                      >
                        自动连麦({cfg.link_mic_mode === "video" ? "视频" : "语音"})
                      </span>
                    )}
                  </div>
                  <div className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
                    {cfg.room_id} · 上限{cfg.max_target ?? "—"} · 间隔{cfg.interval ?? "—"}s · 抖动
                    {cfg.delay || "—"}
                    {cfg.acct ? ` · 账号:${cfg.acct}` : ""}
                  </div>
                </div>
                <button className="btn sm ghost" onClick={() => applyToTask(cfg.room_id)}>
                  应用
                </button>
                <button className="btn sm ghost" onClick={() => edit(cfg)}>
                  编辑
                </button>
                <button className="btn sm ghost danger" onClick={() => remove(cfg.room_id)}>
                  删除
                </button>
              </div>
            ))}
          </div>

          {/* 编辑/新建表单 */}
          <div
            style={{
              borderTop: "1px solid var(--border)",
              paddingTop: 12,
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 10,
            }}
          >
            <div className="field" style={{ gridColumn: "1 / -1" }}>
              <label>直播间号或 URL {editing && `(编辑中: ${editing})`}</label>
              <input
                className="input"
                value={draft.room_id || ""}
                onChange={(e) => setDraft({ ...draft, room_id: e.target.value })}
                placeholder="如 840377749201 或 https://live.douyin.com/..."
                disabled={!!editing}
              />
            </div>
            <div className="field">
              <label>备注名</label>
              <input
                className="input"
                value={draft.name || ""}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="如 张老师工伤直播间"
              />
            </div>
            <div className="field">
              <label>监听账号</label>
              <input
                className="input"
                value={draft.acct || ""}
                onChange={(e) => setDraft({ ...draft, acct: e.target.value })}
                placeholder="留空 = 使用页面当前选择"
              />
            </div>
            <div className="field">
              <label>发送上限</label>
              <input
                className="input"
                type="number"
                value={draft.max_target ?? 100}
                onChange={(e) => setDraft({ ...draft, max_target: parseInt(e.target.value, 10) || 0 })}
              />
            </div>
            <div className="field">
              <label>间隔（秒）</label>
              <input
                className="input"
                type="number"
                value={draft.interval ?? 60}
                onChange={(e) => setDraft({ ...draft, interval: parseFloat(e.target.value) || 0 })}
              />
            </div>
            <div className="field">
              <label>延迟抖动（秒）</label>
              <input
                className="input"
                value={draft.delay || ""}
                onChange={(e) => setDraft({ ...draft, delay: e.target.value })}
                placeholder="50,120"
              />
            </div>
            <div className="field">
              <label>连麦方式</label>
              <select
                className="select"
                value={draft.link_mic_mode || "audio"}
                onChange={(e) =>
                  setDraft({ ...draft, link_mic_mode: e.target.value as "audio" | "video" })
                }
                disabled={!draft.auto_link_mic}
              >
                <option value="audio">语音连线</option>
                <option value="video">视频连线</option>
              </select>
            </div>
            <label
              className="head-row"
              style={{ gap: 8, fontSize: 12, alignItems: "center", gridColumn: "1 / -1" }}
            >
              <span className="switch" style={{ flex: "none" }}>
                <input
                  type="checkbox"
                  checked={!!draft.auto_link_mic}
                  onChange={(e) => setDraft({ ...draft, auto_link_mic: e.target.checked })}
                />
                <i />
              </span>
              <span style={{ color: "var(--muted)" }}>
                自动申请连麦（引擎开播监听后自动对本期直播间发起连麦申请）
              </span>
            </label>
            <label
              className="head-row"
              style={{ gap: 8, fontSize: 12, alignItems: "center", gridColumn: "1 / -1" }}
            >
              <span className="switch" style={{ flex: "none" }}>
                <input
                  type="checkbox"
                  checked={!!draft.force_rescan}
                  onChange={(e) => setDraft({ ...draft, force_rescan: e.target.checked })}
                />
                <i />
              </span>
              <span style={{ color: "var(--muted)" }}>强制重扫</span>
            </label>
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn ghost" onClick={onClose}>
            关闭
          </button>
          <button
            className="btn ghost"
            onClick={() => {
              setEditing(null);
              setDraft({ ...EMPTY_DRAFT, room_id: extractRoomId(currentRoom) || "" });
            }}
          >
            清空表单
          </button>
          <button className="btn primary" onClick={save}>
            {editing ? "更新配置" : "保存配置"}
          </button>
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
