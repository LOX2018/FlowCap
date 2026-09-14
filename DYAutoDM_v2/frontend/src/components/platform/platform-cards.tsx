import { User } from "lucide-react";
import { fmtNum, fmtAgo } from "@/lib/utils";
import { AwemeItem, UserItem } from "../../api/platform";
import { Card } from "@/components/ui/card";
import { CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Badge } from "@/components/ui/badge";

export function AwemeCard({ item, onOpen }: { item: AwemeItem; onOpen?: (it: AwemeItem) => void }) {
  return (
    <Card
      className={`overflow-hidden transition-transform hover:-translate-y-0.5 ${onOpen ? "cursor-pointer" : ""}`}
      onClick={() => onOpen?.(item)}
    >
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

export function Grid({ items, kind, onOpenAweme }: {
  items: (AwemeItem | UserItem)[]; kind: "video" | "user";
  onOpenAweme?: (it: AwemeItem) => void;
}) {
  if (!items.length) return <EmptyState title="暂无内容" description="换个条件试试，或稍后重试。" />;
  return (
    <div className="grid grid-cols-5 gap-3">
      {items.map((it, i) =>
        kind === "user"
          ? <UserCard key={(it as UserItem).uid || i} item={it as UserItem} />
          : <AwemeCard key={(it as AwemeItem).aweme_id || i} item={it as AwemeItem}
                       onOpen={onOpenAweme} />
      )}
    </div>
  );
}

