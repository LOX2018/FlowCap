/**
 * 直播间管理弹窗 —— **直播间登记表（房间层）**
 *
 * ## 规格（ADR-003，2026-09-22 用户决策；勿重新设计）
 *
 * 用户要求：在「直播间」板块按现有「配置管理（管理策略）」的样式**复刻一个
 * 「直播间管理」按钮**，点开的管理页用于：
 *   填直播间链接 → **自动解析出房间** + 备注 + **绑定直播策略** + **是否支持脱敏**。
 *
 * ## 与「管理策略」的分工（**房间与策略分离**，勿混）
 *
 * | 层 | 载体 | 内容 |
 * |---|---|---|
 * | **策略**（怎么发） | `RoomConfigPage.tsx` → kv `live_room_configs` | 发送参数，零身份 |
 * | **房间**（在哪发） | **本页** → kv `live_rooms` | 身份 + `strategy_id` 引用 + 脱敏开关 |
 *
 * 一份策略可被多个房间**引用**；删除策略时后端**自动解绑**引用它的房间
 * （响应 `unbound: N`），因此本页必须把解绑数量如实呈现，不能谎称「无影响」。
 *
 * ## `allow_desensitized` 的诚实语义（ADR-003 §3.3，用户已知悉）
 *
 * 它 = 「允许盯一个**我没有解密权**的房间做统计」，**不是**「能拿到它的昵称」。
 * 界面文案必须照此写，禁止暗示能取到昵称。
 */
import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { api, LiveRoom, RoomConfig } from "../../api/client";
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
  push: (msg: string, holdMs?: number) => void;
  /** 写操作完成（保存/删除/迁移）后通知父页刷新 */
  onChanged?: () => void;
  /** 选用某房间（把房间号回填直播页输入框） */
  onPick?: (room: LiveRoom) => void;
}

/** 新房间草稿：**不含策略参数**（那是策略层的事） */
const EMPTY_DRAFT: Partial<LiveRoom> = {
  room_id: "",
  live_url: "",
  name: "",
  strategy_id: "",
  allow_desensitized: false,
};

export default function RoomManagePage({ open, onClose, push, onChanged, onPick }: Props) {
  const [items, setItems] = useState<LiveRoom[]>([]);
  const [strategies, setStrategies] = useState<RoomConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<Partial<LiveRoom>>({ ...EMPTY_DRAFT });
  const [editing, setEditing] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [resolving, setResolving] = useState(false);
  /** 无解密权房间的统计开关提示（避免用户误以为能取昵称） */
  const [showDesensHint, setShowDesensHint] = useState(false);

  const touch = (patch: Partial<LiveRoom>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setDirty(true);
  };

  const draftActive = dirty && !editing;

  const load = () => {
    setLoading(true);
    Promise.all([api.listLiveRooms(), api.listRoomConfigs()])
      .then(([rooms, cfgs]) => {
        setItems(rooms.items || []);
        setStrategies(cfgs.items || []);
      })
      .catch((e: unknown) => push("加载直播间失败: " + errMsg(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (open) {
      load();
      setDraft({ ...EMPTY_DRAFT });
      setEditing(null);
      setDirty(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /** 填链接 → 自动解析房间（复用 /api/live/resolve，与直播页同一入口） */
  const resolveFromUrl = (raw: string) => {
    const u = raw.trim();
    if (!u) return;
    if (/^\d+$/.test(u)) {
      touch({ room_id: u, live_url: `https://live.douyin.com/${u}` });
      return;
    }
    setResolving(true);
    api
      .resolveLive(u)
      .then((r) => {
        if (r.ok && (r.liveId || r.liveUrl)) {
          const rid = r.liveId || "";
          touch({ room_id: rid || draft.room_id || "", live_url: r.liveUrl || u });
          push("已解析 · 直播间号 " + (rid || "（未取到房间号）"));
        } else {
          push("解析失败: " + (r.error || "未能从链接中解析出直播间号"));
        }
      })
      .catch((e: unknown) => push("解析异常: " + errMsg(e)))
      .finally(() => setResolving(false));
  };

  const save = () => {
    if (!String(draft.room_id || "").trim() && !String(draft.live_url || "").trim()) {
      push("请填写直播间链接或房间号（失焦自动解析）");
      return;
    }
    api
      .saveLiveRoom({ ...draft, id: editing || undefined })
      .then((r) => {
        if (r.ok) {
          push(`已保存直播间「${r.room?.name || r.room?.room_id || ""}」`);
          setDraft({ ...EMPTY_DRAFT });
          setEditing(null);
          setDirty(false);
          onChanged?.();
          load();
        } else {
          push("保存失败: " + (r.error || "未知错误"));
        }
      })
      .catch((e: unknown) => push("保存异常: " + errMsg(e)));
  };

  const remove = (rid: string, label: string) => {
    api
      .deleteLiveRoom(rid)
      .then((r) => {
        if (r.ok) {
          push(`已删除直播间「${label}」（策略不受影响）`);
          onChanged?.();
          load();
        } else push("删除失败: " + (r.error || ""));
      })
      .catch((e: unknown) => push("删除异常: " + errMsg(e)));
  };

  const edit = (room: LiveRoom) => {
    setEditing(room.id);
    setDraft({ ...room });
    setDirty(false);
  };

  const strategyName = (sid: string): string => {
    if (!sid) return "";
    const c = strategies.find((x) => String(x.id || x.room_id || "") === sid);
    return c?.name || sid;
  };

  /** 一次性迁移：干跑 → 用户确认 → 应用（ADR-003 §3.5，迁移必须可复现且留日志） */
  const migrate = (dryRun: boolean) => {
    api
      .migrateLiveRooms(dryRun)
      .then((r) => {
        if (!r.ok) {
          push("迁移失败: " + (r.error || "未知错误"));
          return;
        }
        const found = r.found || [];
        if (dryRun) {
          push(
            found.length
              ? `干跑：发现 ${found.length} 条「房间形」旧记录（${found.join("、")}）。点「应用迁移」执行。`
              : "干跑：未发现需要迁移的旧记录。",
            found.length ? 8000 : 5000,
          );
        } else {
          push(
            `迁移完成：房间 ${(r.migrated_rooms || []).length} 条、` +
              `承接策略 ${(r.created_strategies || []).length} 条、` +
              `清理旧键 ${(r.removed_config_keys || []).length} 个`,
            8000,
          );
          load();
          onChanged?.();
        }
      })
      .catch((e: unknown) => push("迁移异常: " + errMsg(e)));
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-5 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="glass-premium max-h-[86vh] w-[760px] max-w-[92vw] overflow-auto
                   rounded-[var(--radius-xl)]"
        onClick={(e) => e.stopPropagation()}
        data-od-id="live-room-modal"
      >
        <div className="flex items-center justify-between border-b border-[var(--color-border)]
                        px-4 py-3">
          <h3 className="text-[0.95rem] font-semibold text-[var(--color-text)]">
            直播间管理
          </h3>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="p-4">
          {/* 已登记房间列表 */}
          <div style={{ marginBottom: 14 }}>
            <div className="mb-2 flex items-center gap-2 text-[0.78rem] font-semibold
                            text-[var(--color-text)]">
              <span>已登记直播间（{items.length}）</span>
              <Button
                variant="ghost"
                size="sm"
                data-od-id="room-migrate-dry"
                title="干跑：只报告将迁移哪些旧记录，不写库"
                onClick={() => migrate(true)}
              >
                扫描旧记录
              </Button>
              <Button
                variant="ghost"
                size="sm"
                data-od-id="room-migrate-apply"
                title="把「房间形」旧策略记录迁移为直播间登记（不丢信息）"
                onClick={() => migrate(false)}
              >
                应用迁移
              </Button>
            </div>
            {loading && (
              <div className="text-[0.74rem] text-[var(--color-text-muted)]">加载中…</div>
            )}
            {!loading && items.length === 0 && !draftActive && (
              <Blank>暂无登记。在下方「直播间详情」里填写后点「保存直播间」即新建。</Blank>
            )}
            {draftActive && (
              <div
                data-od-id="room-draft-row"
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
                    点「保存直播间」即新建完成
                  </div>
                </div>
              </div>
            )}
            {items.map((room) => (
              <div
                key={room.id}
                data-od-id={"room-row-" + room.id}
                className={
                  "flex flex-wrap items-center gap-2 border-b px-2.5 py-2" +
                  (editing === room.id
                    ? " bg-[var(--color-surface-raised)] border-l-2 border-l-[var(--color-accent)]"
                    : " border-[var(--color-border)]")
                }
              >
                <div className="min-w-[220px] flex-1">
                  <div style={{ fontSize: 13, fontWeight: 600 }}>
                    {editing === room.id ? `✎ 编辑中：${room.name || room.room_id}` : (room.name || room.room_id)}
                    {room.allow_desensitized && (
                      <span
                        className="mono"
                        style={{ marginLeft: 8, fontSize: 11, color: "var(--color-accent)" }}
                      >
                        脱敏·仅统计
                      </span>
                    )}
                  </div>
                  <div className="mono" style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
                    房间 {room.room_id || "—"}
                    {room.strategy_id
                      ? ` · 策略:${strategyName(room.strategy_id)}`
                      : " · 未绑定策略"}
                  </div>
                </div>
                <Button variant="ghost" size="sm" onClick={() => { onPick?.(room); onClose(); }}>
                  选用
                </Button>
                <Button variant="ghost" size="sm" onClick={() => edit(room)}>
                  编辑
                </Button>
                <Button
                  variant="danger-outline"
                  size="sm"
                  onClick={() => remove(room.id, room.name || room.room_id)}
                >
                  删除
                </Button>
              </div>
            ))}
          </div>

          {/* 直播间详情表单：**身份 + 策略引用 + 脱敏开关** */}
          <div className="mb-1 text-[0.74rem] font-semibold text-[var(--color-text)]">
            直播间详情
            {editing && (
              <span className="ml-2 font-normal text-[var(--color-text-muted)]">
                （编辑中：{items.find((x) => x.id === editing)?.name || editing}）
              </span>
            )}
            {!editing && dirty && (
              <span className="ml-2 font-normal text-[var(--color-accent)]">（草稿）</span>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2.5 border-t border-[var(--color-border)] pt-3">
            <div className="col-span-2 flex flex-col gap-1">
              <label>直播间链接</label>
              <Input
                value={draft.live_url || ""}
                onChange={(e) => touch({ live_url: e.target.value })}
                onBlur={(e) => resolveFromUrl(e.target.value)}
                placeholder="https://live.douyin.com/… 或直接填房间号（失焦自动解析）"
                data-od-id="room-live-url"
              />
              <div className="mono" style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
                {resolving ? "解析中…" : `解析出的房间号：${draft.room_id || "（未解析）"}`}
              </div>
            </div>
            <div className="flex flex-col gap-1">
              <label>备注</label>
              <Input
                value={draft.name || ""}
                onChange={(e) => touch({ name: e.target.value })}
                placeholder="如：自营-工伤咨询"
                data-od-id="room-name"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label>绑定直播策略</label>
              <Select
                value={draft.strategy_id || "__none__"}
                onValueChange={(v) => touch({ strategy_id: v === "__none__" ? "" : v })}
              >
                <SelectTrigger data-od-id="room-strategy">
                  <SelectValue placeholder="未绑定" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">未绑定</SelectItem>
                  {strategies.map((c) => {
                    const sid = String(c.id || c.room_id || "");
                    return (
                      <SelectItem key={sid} value={sid}>
                        {c.name || sid}
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
            </div>
            <label className="col-span-2 flex items-start gap-2 text-[0.74rem]">
              <Switch
                checked={!!draft.allow_desensitized}
                onCheckedChange={(v) => {
                  touch({ allow_desensitized: v });
                  setShowDesensHint(v);
                }}
                data-od-id="room-desensitized"
              />
              <span style={{ color: "var(--color-text-muted)" }}>
                允许盯「无解密权」的房间做**仅统计**（在线数/热度/开播检测）
                <br />
                <span style={{ opacity: 0.8 }}>
                  不影响昵称获取：解密权取决于**房间归属**（自营有 / 他人默认脱敏，与凭证无关）。
                </span>
                {showDesensHint && draft.allow_desensitized && (
                  <>
                    <br />
                    <span style={{ color: "var(--color-accent)" }}>
                      已开启：该房间检测到脱敏时仍继续监听，但**不采昵称/uid、不发私信**。
                    </span>
                  </>
                )}
              </span>
            </label>
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-[var(--color-border)] px-4 py-3">
          <Button variant="secondary" onClick={onClose}>
            关闭
          </Button>
          <Button
            variant="secondary"
            data-od-id="room-new"
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
          <Button onClick={save} data-od-id="room-save">
            {editing ? "更新直播间" : "保存直播间"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function errMsg(e: unknown): string {
  return (e as { message?: string })?.message || String(e);
}
