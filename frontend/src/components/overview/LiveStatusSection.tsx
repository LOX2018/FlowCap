/**
 * 直播在线状态（总览页区块）—— ADR-018 F2
 *
 * ## 设计意图（归类依据）
 *
 * 回答「**直播间现在有人在播吗、有多少人**」——这是产品级运行状态，
 * 与「AI 运行状态」「能力健康」同层，故并列放总览页。
 *
 * 区别于总览页已有的「运行中任务」（展示*发送进度*：已发/上限/队列）：
 * 本区块展示的是*直播间本身的实时面*（在线人数 / 点赞 / 房间标题 / WS 监听活性）。
 *
 * ## 数据来源（ADR-018 决策：零新采集，只用既有端点）
 *
 * - `GET /api/live/stream`（client.ts: `api.getStream()`）—— 后端 `api/live.py`
 *   `@router.get("/stream")`，返回 `models/live.py::LiveStreamResponse`：
 *   alive / room_id / online_count / likes / roomTitle / liveUrl /
 *   engineState / statusMsg / dmRunning / dmPaused。
 * - 复用 App 级常驻缓存（queryKey ["live-stream"]，3s 轮询，见 App.tsx）。
 *
 * ## 铁律（禁止假成功）
 *
 * `alive=false` 有三种成因，UI 必须分开表达，不得一律写成「未连接」或补 0：
 *   ① 后端未就绪（ready=false 或请求失败）→ 显示错误信息，不显示任何数字；
 *   ② 引擎未启动（engineState=idle）→ 显式「未启动」空态；
 *   ③ 引擎已启动但 WS 未连上（engineState=starting）→「等待开播」。
 * backend 明确把 heat_curve/online_count 初始化为 0 —— 那是「尚未取到」，
 * 不是「在线 0 人」，UI 因此只在 `alive=true` 时展示数字。
 */
import { useQuery } from "@tanstack/react-query";
import { Radio, Users, Heart, DoorOpen } from "lucide-react";
import { PageProps } from "../../api/client";
import { Section, KeyValue, Tone, SkeletonRows, Blank } from "@/components/page/kit";

/** `GET /api/live/stream` 响应（对齐 backend/models/live.py::LiveStreamResponse）。 */
interface LiveStreamState {
  alive?: boolean;
  room_id?: string | null;
  online_count?: number;
  likes?: number;
  roomTitle?: string;
  liveUrl?: string;
  listening?: boolean;
  engineState?: string;
  statusMsg?: string;
  dmRunning?: boolean;
  dmPaused?: boolean;
}

export default function LiveStatusSection(props: PageProps) {
  const { api, ready } = props;

  // 2026-10-05 T8-2：删除自带 refetchInterval（与 App 常驻 3s 重复），改为读共享缓存。
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["live-stream"],
    queryFn: async () => (await api.getStream()) as unknown as LiveStreamState,
    enabled: !!ready,
  });

  const ls: LiveStreamState = data || {};
  const state = String(ls.engineState || "idle");
  // 与 live-page.tsx 同一判据：engineBusy 表示引擎进程忙（含等待开播），
  // alive 才是 WS 真的挂在直播流上。二者混用会把「等待开播」误报成「未连接」。
  const engineBusy = ["starting", "running", "paused", "stopping"].includes(state);
  const alive = !!ls.alive;

  // 状态胶囊：按三种成因分别措辞（禁止一律「未连接」/ 禁止用 0 冒充未取到）
  const connTone = alive ? "ok" : state === "starting" ? "warn" : "mute";
  const connLabel = alive
    ? "监听中"
    : state === "starting"
      ? "等待开播"
      : engineBusy
        ? "未连上直播流"
        : "未启动";

  return (
    <Section
      title="直播在线状态"
      description="在线人数 / 点赞 / 监听活性"
      actions={<Tone tone={connTone}>{connLabel}</Tone>}
    >
      {isLoading ? (
        <SkeletonRows rows={2} />
      ) : isError ? (
        <Blank>
          读取直播状态失败：{(error as Error)?.message || "后端无响应"}
          <br />
          <span className="text-[0.72rem]">直播状态暂未取到，请确认服务已启动。</span>
        </Blank>
      ) : !alive ? (
        <Blank>
          {state === "starting"
            ? "引擎已启动，正在连接直播流（尚未取到在线人数）—— 数据到位后此处自动刷新。"
            : engineBusy
              ? "引擎忙但未挂上直播流 —— WS 未连上，在线人数暂不可得。"
              : "当前没有直播监听任务 —— 可在「直播」页开始监听。"}
        </Blank>
      ) : (
        <div className="space-y-3">
          <KeyValue
            cols={2}
            items={[
              {
                k: "在线人数",
                v: Number(ls.online_count || 0).toLocaleString(),
                mono: true,
              },
              { k: "累计点赞", v: Number(ls.likes || 0).toLocaleString(), mono: true },
              { k: "房间号", v: ls.room_id || "—", mono: true },
              { k: "房间标题", v: ls.roomTitle || "—" },
            ]}
          />
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.74rem] text-[var(--color-text-muted)]">
            <span className="flex items-center gap-1">
              <Users className="h-3.5 w-3.5" />
              在线数据仅在 WS 挂载期间可得
            </span>
            <span className="flex items-center gap-1">
              <Heart className="h-3.5 w-3.5" />
              点赞为房间累计值
            </span>
            {ls.dmRunning ? (
              <span className="flex items-center gap-1">
                <Radio className="h-3.5 w-3.5" />
                私信引擎{ls.dmPaused ? "已暂停" : "运行中"}
              </span>
            ) : null}
            {ls.statusMsg ? (
              <span className="flex items-center gap-1">
                <DoorOpen className="h-3.5 w-3.5" />
                {ls.statusMsg}
              </span>
            ) : null}
          </div>
        </div>
      )}
    </Section>
  );
}
