/**
 * 目标直播间管理页（2026-09-19 用户定调）
 *
 * 用户原话：「目标直播间可以管理，但别放到配置管理中，单独加一个目标直播间管理，
 * 在该页面中选择是否绑定配置。」
 *
 * ## 职责边界（与「直播配置标签」严格分离，勿混）
 *   - 本页只管**目标直播间**：直播间号 / 备注名 / 是否绑定哪条配置标签 / 启用。
 *     本页**没有任何参数输入控件**（上限/间隔/抖动/词库/连麦/强制重扫）。
 *   - 参数一律在「直播配置标签」（`RoomConfigPage.tsx`）里维护，本页只做**引用**。
 *   - 解绑（选「不绑定配置」）只清 `tag_id` 引用，**绝不删除**配置标签本身。
 *
 * ## 为什么是「新增一层」而不是改造 live_room_configs
 * 既有 `live_room_configs`（按 room_id 唯一）承载两种语义：目标直播间 + 参数标签。
 * 二者本该分离（用户指出的核心问题）。本页用独立 kv `live_target_rooms` 承载
 * 「目标直播间」这一层，展示时与 `live_room_configs` **联合展示**，因此：
 *   - 老数据零迁移、零丢失（存量配置仍以「未绑定」形态出现在列表里）
 *   - 参数表语义不变，`/restart` 热更链路完全不动（回归风险最低）
 */
import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api, RoomConfig } from "../../api/client";
import type { TargetRoom } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import { Blank } from "@/components/page/kit";

interface Props {
  push: (msg: string, holdMs?: number) => void;
  /** 写操作后通知直播页刷新（与配置标签共用同一份 roomCfgs） */
  onChanged?: () => void;
  /** 「去配置参数」：跳到「直播配置标签」子视图 */
  gotoConfigs?: () => void;
}

/** 「不绑定任何配置」哨兵值（Select 不接受空字符串 value） */
const NONE = "__none__";

const EMPTY: TargetRoom = {
  room_id: "",
  name: "",
  tag_id: null,
  enabled: true,
};

interface Item {
  /** 目标直播间号（唯一键） */
  roomId: string;
  target: TargetRoom | null;
  /** 该 room_id 对应的参数标签（可能不存在 → 参数未配置） */
  cfg: RoomConfig | null;
}

export default function TargetRoomPage({ push, onChanged, gotoConfigs }: Props) {
  const [items, setItems] = useState<Item[]>([]);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<TargetRoom>({ ...EMPTY });
  const [editing, setEditing] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  /** 一次拉两份数据：目标直播间（绑定关系）+ 配置标签（参数摘要 / 绑定候选） */
  const load = () => {
    setLoading(true);
    Promise.all([
      api.listTargetRooms().catch((e: unknown) => {
        push("加载目标直播间失败: " + errMsg(e));
        return { ok: false, items: [] as TargetRoom[] };
      }),
      api.listRoomConfigs().catch(() => ({ ok: false, items: [] as RoomConfig[] })),
    ])
      .then(([t, c]) => {
        const cfgs = (c.items || []) as RoomConfig[];
        const byRoom: Record<string, RoomConfig> = {};
        for (const x of cfgs) byRoom[x.room_id] = x;

        const merged: Item[] = [];
        const seen = new Set<string>();
        for (const tr of t.items || []) {
          const rid = tr.room_id;
          if (!rid || seen.has(rid)) continue;
          seen.add(rid);
          merged.push({ roomId: rid, target: tr, cfg: byRoom[rid] || null });
        }
        // 联合展示：只有参数标签、没有目标直播间记录的房间（存量数据 / 已解绑）
        for (const x of cfgs) {
          if (seen.has(x.room_id)) continue;
          seen.add(x.room_id);
          merged.push({ roomId: x.room_id, target: null, cfg: x });
        }
        merged.sort((a, b) => {
          const ta = a.target?.updated_at || 0;
          const tb = b.target?.updated_at || 0;
          if (ta !== tb) return tb - ta;
          return (b.cfg?.updated_at || 0) - (a.cfg?.updated_at || 0);
        });
        setItems(merged);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = () => {
    const rid = normalizeRoomId(draft.room_id);
    if (!rid) {
      push("请填写直播间号或直播间 URL（需能提取出纯数字房间号）");
      return;
    }
    api
      .saveTargetRoom({ ...draft, room_id: rid })
      .then((r) => {
        if (r.ok) {
          push(
            `已保存目标直播间 ${rid}` +
              (r.target?.tag_id ? "（已绑定配置标签）" : "（未绑定配置）"),
          );
          setDraft({ ...EMPTY });
          setEditing(null);
          onChanged?.();
          load();
        } else {
          push("保存失败: " + (r.error || "未知错误"));
        }
      })
      .catch((e: unknown) => push("保存异常: " + errMsg(e)));
  };

  const bindOnly = (roomId: string, tagId: string) => {
    const cur = items.find((x) => x.roomId === roomId)?.target;
    if (!cur) return;
    api
      .saveTargetRoom({ ...cur, tag_id: tagId === NONE ? null : tagId })
      .then((r) => {
        if (r.ok) {
          push(
            tagId === NONE
              ? `已解绑 ${roomId} 的配置（配置标签本身保留）`
              : `已把 ${roomId} 绑定到配置标签「${items.find((x) => x.roomId === roomId)?.cfg?.name || tagId}」`,
          );
          onChanged?.();
          load();
        } else {
          push("绑定失败: " + (r.error || ""));
        }
      })
      .catch((e: unknown) => push("绑定异常: " + errMsg(e)));
  };

  const toggleEnabled = (roomId: string, v: boolean) => {
    const cur = items.find((x) => x.roomId === roomId)?.target;
    if (!cur) return;
    api
      .saveTargetRoom({ ...cur, enabled: v })
      .then((r) => {
        if (r.ok) load();
        else push("保存失败: " + (r.error || ""));
      })
      .catch((e: unknown) => push("保存异常: " + errMsg(e)));
  };

  const remove = (roomId: string) => {
    if (
      !confirm(
        `确认移除目标直播间 ${roomId}？\n仅移除本页的目标记录与绑定关系，` +
          `「直播配置标签」里的参数不会被删除。`,
      )
    ) {
      return;
    }
    api
      .deleteTargetRoom(roomId)
      .then((r) => {
        if (r.ok) {
          push(`已移除目标直播间 ${roomId}（配置标签保留）`);
          onChanged?.();
          load();
        } else push("移除失败: " + (r.error || ""));
      })
      .catch((e: unknown) => push("移除异常: " + errMsg(e)));
  };

  const edit = (it: Item) => {
    setEditing(it.roomId);
    setDraft(
      it.target
        ? { ...it.target }
        : // 只有配置标签、无目标记录的房间：以参数标签为基准建草稿（绑定到自己）
          {
            room_id: it.roomId,
            name: it.cfg?.name || "",
            tag_id: it.roomId,
            enabled: true,
          },
    );
  };

  const tagOptions = items.filter((x) => !!x.cfg) as (Item & { cfg: RoomConfig })[];

  return (
    <div data-od-id="live-target-rooms">
      <div className="mx-auto w-full max-w-[860px] glass-premium rounded-[var(--radius-xl)] px-4 py-3.5">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-[0.95rem] font-semibold text-[var(--color-text)]">
            目标直播间
          </h3>
          <span className="text-[0.72rem] text-[var(--color-text-muted)]">
            本页只管「监听哪个直播间 + 绑定哪条配置」；参数在「直播配置标签」里改
          </span>
        </div>

        {/* ① 列表 */}
        <div className="mb-3.5">
          {loading && (
            <div className="text-[0.74rem] text-[var(--color-text-muted)]">加载中…</div>
          )}
          {!loading && items.length === 0 && (
            <Blank>暂无目标直播间。填写下方表单添加第一个。</Blank>
          )}
          {items.map((it) => {
            const boundTagName = it.target?.tag_id
              ? tagOptions.find((x) => x.roomId === it.target?.tag_id)?.cfg?.name ||
                it.target.tag_id
              : "";
            const open = expanded === it.roomId;
            return (
              <div
                key={it.roomId}
                className="border-b border-[var(--color-border)] px-2.5 py-2"
                data-od-id={`target-room-${it.roomId}`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={open ? "收起参数" : "展开参数"}
                    onClick={() => setExpanded(open ? null : it.roomId)}
                  >
                    {open ? (
                      <ChevronDown className="h-4 w-4" />
                    ) : (
                      <ChevronRight className="h-4 w-4" />
                    )}
                  </Button>
                  {it.target && (
                    <Switch
                      checked={it.target.enabled !== false}
                      onCheckedChange={(v) => toggleEnabled(it.roomId, v)}
                    />
                  )}
                  <div className="min-w-[180px] flex-1">
                    <div style={{ fontSize: 13, fontWeight: 600 }}>
                      {it.target?.name || it.cfg?.name || it.roomId}
                      {!it.target && (
                        <span
                          className="mono"
                          style={{ marginLeft: 8, fontSize: 11, color: "var(--color-text-muted)" }}
                        >
                          存量配置 · 未登记为目标直播间
                        </span>
                      )}
                    </div>
                    <div className="mono" style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
                      {it.roomId}
                      {it.cfg ? "" : " · 参数未配置"}
                    </div>
                  </div>

                  {/* 绑定选择：只选引用，不写参数 */}
                  <Select
                    value={it.target?.tag_id || NONE}
                    onValueChange={(v) => bindOnly(it.roomId, v)}
                    disabled={!it.target}
                  >
                    <SelectTrigger
                      className="h-8 min-w-[170px]"
                      aria-label="绑定配置标签"
                      data-od-id={`target-room-tag-${it.roomId}`}
                    >
                      <SelectValue placeholder="选择配置标签…" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NONE}>不绑定配置</SelectItem>
                      {tagOptions.map((x) => (
                        <SelectItem key={x.roomId} value={x.roomId}>
                          {(x.cfg.name || x.roomId) + ` · ${x.roomId}`}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <span
                    style={{
                      fontSize: 11,
                      color: boundTagName ? "var(--color-accent)" : "var(--color-text-muted)",
                    }}
                  >
                    {boundTagName ? `已绑定：${boundTagName}` : "未绑定"}
                  </span>

                  <Button variant="ghost" size="sm" onClick={() => edit(it)}>
                    编辑
                  </Button>
                  {it.target && (
                    <Button
                      variant="danger-outline"
                      size="sm"
                      onClick={() => remove(it.roomId)}
                    >
                      移除
                    </Button>
                  )}
                </div>

                {/* 展开：只读展示绑定标签的参数（唯一可写入口在配置标签页） */}
                {open && (
                  <div className="mt-1.5 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-2 text-[0.74rem] text-[var(--color-text-secondary)]">
                    {it.cfg ? (
                      <div className="flex flex-col gap-1">
                        <span>
                          发送上限 <b className="mono">{it.cfg.max_target ?? "—"}</b> · 间隔{" "}
                          <b className="mono">{it.cfg.interval ?? "—"}s</b> · 抖动{" "}
                          <b className="mono">{it.cfg.delay || "—"}</b>
                        </span>
                        <span>
                          私信词库 <b className="mono">{(it.cfg.dm_pool || []).length} 条</b> · 自动连麦{" "}
                          <b className="mono">
                            {it.cfg.auto_link_mic
                              ? `开（${it.cfg.link_mic_mode === "video" ? "视频" : "语音"}）`
                              : "关"}
                          </b>
                          {" · "}强制重扫{" "}
                          <b className="mono">{it.cfg.force_rescan ? "开" : "关"}</b>
                        </span>
                        <div>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => gotoConfigs?.()}
                          >
                            去配置参数
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <span>
                        这个直播间还没有配置参数。
                        请到「直播配置标签」新建一条（标签键用本直播间号）后再回来绑定。
                      </span>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* ② 新建 / 编辑表单（只有身份 + 绑定 + 启用，无任何参数） */}
        <div className="grid grid-cols-2 gap-2.5 border-t border-[var(--color-border)] pt-3">
          <div className="col-span-2 flex flex-col gap-1">
            <label>直播间号或 URL {editing && `（编辑中: ${editing}）`}</label>
            <Input
              value={draft.room_id || ""}
              onChange={(e) => setDraft({ ...draft, room_id: e.target.value })}
              placeholder="如 840377749201 或 https://live.douyin.com/..."
              disabled={!!editing}
              data-od-id="target-room-id"
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
            <label>绑定配置标签</label>
            <Select
              value={draft.tag_id || NONE}
              onValueChange={(v) =>
                setDraft({ ...draft, tag_id: v === NONE ? null : v })
              }
            >
              <SelectTrigger aria-label="绑定配置标签" data-od-id="target-room-tag">
                <SelectValue placeholder="选择配置标签…" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>不绑定配置</SelectItem>
                {tagOptions.map((x) => (
                  <SelectItem key={x.roomId} value={x.roomId}>
                    {(x.cfg.name || x.roomId) + ` · ${x.roomId}`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <label className="col-span-2 flex items-center gap-2 text-[0.74rem]">
            <Switch
              checked={draft.enabled !== false}
              onCheckedChange={(v) => setDraft({ ...draft, enabled: v })}
            />
            <span style={{ color: "var(--color-text-muted)" }}>
              启用该目标直播间（关闭后不参与监听）
            </span>
          </label>
        </div>
        <div className="mt-3 flex justify-end gap-2 border-t border-[var(--color-border)] pt-3">
          <Button
            variant="secondary"
            onClick={() => {
              setEditing(null);
              setDraft({ ...EMPTY });
            }}
          >
            清空表单
          </Button>
          <Button onClick={save} data-od-id="target-room-save">
            {editing ? "更新" : "添加目标直播间"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** 从输入提取纯数字直播间号（容忍整条 URL） */
function normalizeRoomId(raw: string): string {
  const s = (raw || "").trim();
  if (/^\d+$/.test(s)) return s;
  const m = s.match(/live\.douyin\.com\/(\d+)/);
  return m ? m[1] : "";
}

function errMsg(e: unknown): string {
  return (e as { message?: string })?.message || String(e);
}
