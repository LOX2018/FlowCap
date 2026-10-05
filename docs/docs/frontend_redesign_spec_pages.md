# 前端重设计 · 页面迁移规格（子任务共用）

> 本文件是**执行规格**，不是设计讨论。所有迁移必须严格按此执行。

## 一、任务性质（不可偏离）

**只改呈现层，不改业务逻辑。**

| 允许改 | 禁止改 |
|---|---|
| 内联 `style={{...}}` → 设计令牌 Tailwind 类 | 任何 `useQuery` / `useMutation` 的 queryKey、enabled、refetchInterval |
| `.btn` / `.card` / `.pill` 等旧 CSS 类 → 新组件 | 任何 API 调用（`api.xxx()`）的**参数与顺序** |
| 手搓切换按钮 → `SegmentedTabs` | 任何 `push()` 文案语义 |
| 本地 `Section`/`Field` 实现 → 复用 `kit` | 任何状态机（如 `sending`/`sent` 的三态）、轮询间隔、分页大小 |
| 图标补充（lucide-react） | 任何风控相关判断（凭证校验、UID 匹配、限速语义） |

## 二、可用的设计令牌（`@/styles/tokens.css`）

用 Tailwind 任意值语法引用，例如 `text-[var(--color-text)]`、`bg-[var(--color-surface-raised)]`。

```
--color-background        #0e0e14   页面底
--color-background-soft   #0a0a0f   更深（日志/代码区底）
--color-surface           rgba(255,255,255,0.04)
--color-surface-solid     #16161e   卡片实底
--color-surface-raised    rgba(255,255,255,0.07)  悬停/激活
--color-border            rgba(255,255,255,0.08)
--color-border-strong     rgba(255,255,255,0.14)
--color-text              #e8e8ed   主文字
--color-text-secondary    #9b9baa   次要
--color-text-muted        #8b8b9e   弱化
--color-accent            #68cb6e   主色（本项目绿）
--color-accent-hover      #7dd983
--color-accent-soft       rgba(104,203,110,0.1)   主色淡底
--color-accent-ring       rgba(104,203,110,0.22)
--color-success / -soft   #00d68f
--color-info    / -soft   #7c5cfc
--color-warning / -soft   #ffaa00
--color-danger  / -soft   #ff4757

--radius-sm 8px  --radius-md 12px  --radius-lg 18px  --radius-xl 24px
--shadow-sm / -md / -lg / -glow
--ease-spring cubic-bezier(0.2,0,0,1)
--duration-fast 150ms  --duration-base 250ms  --duration-slow 400ms
--font-mono
```

**深浅主题已由令牌自动接管** —— 绝不再写 `#fff`、`rgba(0,0,0,.2)` 之类的固定色（除图片遮罩 `bg-black/55`）。

## 三、可用组件

### 3.1 UI 原子（`@/components/ui/*`）

```tsx
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";       // variant: default|secondary|outline|ghost|danger|danger-outline|success-outline|info-outline|link
                                                      // size: default|sm|lg|icon|icon-sm
import { Badge } from "@/components/ui/badge";          // variant: default|accent|success|info|warning|danger|outline
import { Input, Textarea, Label } from "@/components/ui/input";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { StatusDot } from "@/components/ui/status-dot"; // tone: ok|warn|danger|muted|info
import { EmptyState, LoadingState, ErrorState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import { Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Tooltip } from "@/components/ui/tooltip";
```

`Select` 是 Radix：`<Select value={v} onValueChange={fn}>` + `<SelectTrigger className="h-8 w-[120px]"><SelectValue /></SelectTrigger>` + `<SelectContent>{items.map(i => <SelectItem key={i.v} value={i.v}>{i.label}</SelectItem>)}</SelectContent>`

⚠️ **`SelectItem` 的 value 不能是空字符串**（Radix 限制）。空值场景用哨兵值（如 `"__all"`）再映射回 `""`。

### 3.2 页面组件族（`@/components/page/kit`）

```tsx
import {
  Section,      // { title?, description?, actions?, children, className?, bare?, ...HTMLAttributes }
  Stat,         // { label, value, unit?, delta?, deltaDown?, accent?, icon?, className? }
  StatRow,      // { children, cols?: 2|3|4|5, className? }
  Row,          // { children, className?, onClick?, active? }  —— 列表行
  RowText,      // { primary, secondary?, mono?, className? }
  KeyValue,     // { items: {k, v, mono?}[], cols?: 1|2|3, className? }
  Tone,         // { tone: "ok"|"warn"|"danger"|"accent"|"mute"|"info", children }  —— 替代旧 Pill
  SkeletonRows, // { rows?, className? }
  Blank,        // { children?, className? }  —— 替代旧 .blank / 空态文字
  SegmentedTabs,// { value, onChange, items: {value,label,icon?}[], className? }
  Toolbar,      // { children, className? }  —— flex wrap 工具条
  Collapse,     // { title, subtitle?, defaultOpen?, right?, footer?, children }  —— 替代各页自写折叠
  FormField,    // { label, hint?, secret?, children, className? }
} from "@/components/page/kit";
```

`Section` 已支持透传 HTML 属性（**保留 `data-od-id` 锚点**）。

### 3.3 工具

```tsx
import { cn } from "@/lib/utils";
import { fmtNum, fmtTime, fmtAgo } from "@/lib/utils";
```

## 四、图标

统一 `lucide-react`，size 用 `className="h-3.5 w-3.5"`（小）/ `h-4 w-4`（常规）。

## 五、映射速查（旧 → 新）

| 旧写法 | 新写法 |
|---|---|
| `<div className="card">` | `<Section>` 或 `<Card><CardContent>` |
| `<div className="section-head"><h2>X</h2><div className="desc">Y</div></div>` | `<PageHeader title="X" description="Y" actions={...} />`（页面级） |
| `<span className="pill">` / `<Pill c="ok">` | `<Tone tone="ok">` |
| `<button className="btn primary">` | `<Button>` |
| `<button className="btn ghost">` | `<Button variant="ghost">` |
| `<button className="btn sm">` | `<Button variant="secondary" size="sm">` |
| `<button className="btn text sm">` | `<Button variant="link" size="sm">` |
| `<input className="input">` | `<Input>` |
| `<select className="select">` | `<Select>`（Radix） |
| `<div className="sk" style={{height:28}}/>` | `<Skeleton className="h-7" />` 或 `<SkeletonRows> ` |
| `style={{color:"var(--muted)"}}` | `text-[var(--color-text-muted)]` |
| `style={{background:"var(--panel)"}}` | `bg-[var(--color-surface-solid)]` |
| `style={{border:"1px solid var(--border)"}}` | `border border-[var(--color-border)]` |
| `style={{borderRadius:12}}` | `rounded-[var(--radius-md)]` |
| `.overlay` 抽屉 | 固定定位 + `bg-black/55 backdrop-blur-sm`（见 crawl.tsx 范式） |
| `<input type="checkbox">` | `<Switch checked onCheckedChange>`（若语义为"开关"）；若为**多选列表**保留原生 checkbox 并加 `accent-[var(--color-accent)]` |

## 六、页面骨架范式

```tsx
import { PageContainer, PageHeader } from "@/components/layout/app-shell";

return (
  <PageContainer>
    <PageHeader title="…" description="…" actions={<>…</>} />
    <Section title="…">…</Section>
  </PageContainer>
);
```

`PageContainer` props：`children` / `className?` / `maxWidth?`（默认 `1680px`）。

## 七、参考实现（已完成的同规格页面）

**必读**（照抄其风格与手法）：
- `frontend/src/pages/crawl.tsx` —— 搜索 + 卡片栅格 + 抽屉，含 `Row`/`Tone` 用法
- `frontend/src/pages/notify.tsx` —— `Collapse` + `FormField` + 表单
- `frontend/src/pages/tasks.tsx` —— 表格（具名 `Th`/`Td`）+ 行操作按钮
- `frontend/src/pages/ai.tsx` —— 大表单 + `Collapse` 分区块 + 吸底保存条
- `frontend/src/pages/kb.tsx` —— 可编辑表格 + 子页切换

## 八、验收（必须自己跑）

```bash
cd C:/Users/LOX/Desktop/DYchajian/FlowCap/frontend
npx tsc -b
```
必须 `TSC_EXIT=0`。

⚠️ 若报错**涉及其他页面文件**（非你负责的那个），忽略并在报告中注明（并发写 tsbuildinfo 所致）。

## 九、报告要求（必须逐项列出）

1. 你负责的文件路径 + 迁移前后行数
2. **业务逻辑保全表**：列出你确认**未改动**的关键点（如：轮询间隔、API 参数顺序、三态状态机、风控判断）
3. 你**新增**的组件/辅助函数（如有）
4. `npx tsc -b` 的实际输出
5. 任何你**不确定**的地方（不要猜，列出来）
