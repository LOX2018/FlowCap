import { User } from "lucide-react";
import { fmtNum, fmtAgo } from "@/lib/utils";
import { AwemeItem, UserItem } from "../../api/platform";
import { Card } from "@/components/ui/card";
import { CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Badge } from "@/components/ui/badge";

export function AwemeCard({ item, onOpen, checked, onToggleCheck, previewCount }: {
  item: AwemeItem;
  onOpen?: (it: AwemeItem) => void;
  checked?: boolean;
  onToggleCheck?: (id: string, checked: boolean) => void;
  /** ★ 2026-10-03：匿名预览读数（零凭证探针结果，>0 才显示角标）。 */
  previewCount?: number;
}) {
  return (
    <Card
      className={`relative overflow-hidden transition-all hover:-translate-y-0.5 ${onOpen ? "cursor-pointer" : ""} ${
        // ★ 2026-10-04（用户指令）：选中卡片加一圈**淡绿色**边框。
        //   用 `ring` 而非 `border`：卡片本身已有 border/布局，改 border
        //   会挤动内容；ring 是**外描边**，不占位、不影响排版。
        //   颜色取既有 `--color-accent-soft`（与账号标签等选中态一致），
        //   不新造颜色 —— 新增色值会破坏「全站只此一套材质」。
        checked
          ? "ring-2 ring-[var(--color-accent)] bg-[var(--color-accent-soft)]"
          : ""
      }`}
      onClick={() => onOpen?.(item)}
    >
      {/* 采集勾选框
          ★ 2026-10-03 修复「勾选却进了播放器」：整张 Card 的 onClick 会被冒泡触发。
            单靠 input 上的 stopPropagation 不够 —— label/合成事件在部分路径下
            仍会把 click 送到父级。故三层兜底：
              ① 外层 span 的 onClick 捕获并 **阻止冒泡**（含默认行为）
              ② input 的 onClick 再挡一次（比 onChange 更早，跨浏览器更稳）
              ③ input 的 onChange 挡 change 冒泡后才回调 onToggleCheck
            判据：点勾选框后 checkedIds 变化，onOpen 不得被调用。 */}
      {onToggleCheck && (
        <span
          className="absolute left-2 top-2 z-10 block"
          onClick={(e) => {
            e.stopPropagation();
            e.preventDefault();
          }}
        >
          <input
            type="checkbox"
            className="h-4 w-4 cursor-pointer accent-[var(--color-accent)]"
            checked={!!checked}
            onClick={(e) => {
              e.stopPropagation();
              e.preventDefault();
            }}
            onChange={(e) => {
              e.stopPropagation();
              e.preventDefault();
              onToggleCheck(item.aweme_id, e.target.checked);
            }}
            title="勾选后可批量采集"
          />
        </span>
      )}
      <div className="relative aspect-[3/4] w-full overflow-hidden bg-[var(--color-surface)]">
        {item.cover ? (
          <img
            src={item.cover}
            alt={item.desc || item.aweme_id}
            loading="lazy"
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-[0.75rem]
                          text-[var(--color-text-muted)]">
            无封面
          </div>
        )}
        {/* ★ 2026-10-03：匿名预览角标 —— 零凭证探针已确认「这个作品有评论」。
            刻意不显示 0（0 与「还没探」不可区分，显示出来会误导）。 */}
        {!!previewCount && previewCount > 0 && (
          <span
            className="absolute bottom-7 left-2 rounded-full bg-black/60 px-2 py-0.5
                       text-[0.62rem] text-white/90 backdrop-blur"
            title={`匿名预览：${previewCount} 条评论（零凭证，未采集全量）`}
          >
            预览 {previewCount}
          </span>
        )}
        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent p-2">
          <div className="flex items-center gap-2 text-[0.68rem] text-white/90">
            <span>♥ {fmtNum(item.digg_count)}</span>
            <span>💬 {fmtNum(item.comment_count)}</span>
          </div>
        </div>
      </div>
      <CardContent className="p-2.5">
        <div className="line-clamp-2 text-[0.75rem] leading-snug text-[var(--color-text)]">
          {item.desc || "（无描述）"}
        </div>
        <div className="mt-1 flex items-center justify-between text-[0.68rem]
                        text-[var(--color-text-muted)]">
          <span className="truncate">{item.author_nickname || "—"}</span>
          <span>{item.create_time ? fmtAgo(item.create_time) : ""}</span>
        </div>
      </CardContent>
    </Card>
  );
}

export function UserCard({ item }: { item: UserItem }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-3">
        {item.avatar ? (
          <img src={item.avatar} alt={item.nickname}
               className="h-11 w-11 shrink-0 rounded-full object-cover" />
        ) : (
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full
                          bg-[var(--color-surface-raised)]">
            <User className="h-5 w-5 text-[var(--color-text-muted)]" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-[0.84rem] font-medium text-[var(--color-text)]">
            {item.nickname || item.uid || "—"}
          </div>
          <div className="truncate text-[0.72rem] text-[var(--color-text-secondary)]">
            {item.signature || `粉丝 ${fmtNum(item.follower_count)} · 作品 ${fmtNum(item.aweme_count)}`}
          </div>
        </div>
        <Badge variant="outline">{fmtNum(item.follower_count)} 粉</Badge>
      </CardContent>
    </Card>
  );
}

export function Grid({ items, kind, onOpenAweme, checkedIds, onToggleCheck, previewCounts }: {
  items: (AwemeItem | UserItem)[]; kind: "video" | "user";
  onOpenAweme?: (it: AwemeItem) => void;
  checkedIds?: Record<string, boolean>;
  onToggleCheck?: (id: string, checked: boolean) => void;
  /** ★ 2026-10-03：匿名预览读数（aweme_id → 评论条数），>0 才在卡片显示角标。 */
  previewCounts?: Record<string, number>;
}) {
  if (!items.length) return <EmptyState title="暂无内容" description="换个条件试试，或稍后重试。" />;
  return (
    <div className="grid grid-cols-5 gap-3">
      {items.map((it, i) =>
        kind === "user"
          ? <UserCard key={(it as UserItem).uid || i} item={it as UserItem} />
          : <AwemeCard key={(it as AwemeItem).aweme_id || i} item={it as AwemeItem}
                       onOpen={onOpenAweme}
                       checked={checkedIds?.[(it as AwemeItem).aweme_id]}
                       onToggleCheck={onToggleCheck}
                       previewCount={previewCounts?.[(it as AwemeItem).aweme_id]} />
      )}
    </div>
  );
}

