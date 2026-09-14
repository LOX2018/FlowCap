/**
 * 预览入口（开发工具）—— 在无后端的情况下验证新版布局/组件真实渲染。
 *
 * 用途：启动屏（BootSplash）依赖后端就绪与会员登录，无后端时看不到主界面。
 * 本入口直接把 AppShell + 组件族挂起来，供视觉/样式验证。
 * 访问：/preview.html
 */
import React, { useState } from "react";
import ReactDOM from "react-dom/client";
import { AppShell, PageContainer, PageHeader } from "@/components/layout/app-shell";
import type { TabId } from "@/components/layout/sidebar";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input, Textarea, Label } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Progress } from "@/components/ui/progress";
import { StatusDot } from "@/components/ui/status-dot";
import { EmptyState, LoadingState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import {
  Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import "./styles/tokens.css";
import "./styles/global.css";
import "./styles/theme-glass.css";
import "./styles/bridge.css";

function Demo() {
  const [tab, setTab] = useState<TabId>("overview");
  const [sw, setSw] = useState(true);

  return (
    <AppShell
      tab={tab}
      setTab={setTab}
      title="设计系统预览"
      connected
      memberName="预览账号"
      topRight={<Badge variant="accent">示例徽章</Badge>}
      sidebarFooter={
        <div className="flex items-center gap-1.5 px-2.5 text-[0.72rem] text-[var(--color-text-muted)]">
          <StatusDot tone="ok" />
          <span>引擎就绪</span>
        </div>
      }
    >
      <PageContainer>
        <PageHeader
          title="组件族预览"
          description="对标 better-douyin 的设计令牌 + 组件体系"
          actions={
            <>
              <Button variant="outline" size="sm">次要</Button>
              <Button size="sm">主要动作</Button>
            </>
          }
        />

        <div className="grid grid-cols-3 gap-3">
          <Card>
            <CardHeader><CardTitle>按钮变体</CardTitle></CardHeader>
            <CardContent className="flex flex-wrap gap-2">
              <Button size="sm">default</Button>
              <Button size="sm" variant="secondary">secondary</Button>
              <Button size="sm" variant="outline">outline</Button>
              <Button size="sm" variant="ghost">ghost</Button>
              <Button size="sm" variant="danger">danger</Button>
              <Button size="sm" variant="success-outline">success</Button>
              <Button size="sm" variant="info-outline">info</Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>徽章 / 状态</CardTitle></CardHeader>
            <CardContent className="flex flex-wrap items-center gap-2">
              <Badge>默认</Badge>
              <Badge variant="accent">主色</Badge>
              <Badge variant="success">成功</Badge>
              <Badge variant="warning">警告</Badge>
              <Badge variant="danger">危险</Badge>
              <Badge variant="info">信息</Badge>
              <Badge variant="outline">描边</Badge>
              <StatusDot tone="ok" />
              <StatusDot tone="warn" pulse />
              <StatusDot tone="danger" />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>表单控件</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div>
                <Label>账号名称</Label>
                <Input placeholder="请输入…" className="mt-1" />
              </div>
              <div className="flex items-center gap-2">
                <Switch checked={sw} onCheckedChange={setSw} />
                <span className="text-[0.8rem] text-[var(--color-text-secondary)]">
                  自动回复（{sw ? "开" : "关"}）
                </span>
              </div>
              <Select>
                <SelectTrigger><SelectValue placeholder="选择模型" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="a">GPT-4o</SelectItem>
                  <SelectItem value="b">Claude</SelectItem>
                </SelectContent>
              </Select>
              <Textarea placeholder="多行文本…" />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>进度 / 骨架</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <Progress value={62} />
              <Progress value={28} />
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>标签页</CardTitle></CardHeader>
            <CardContent>
              <Tabs defaultValue="t1">
                <TabsList>
                  <TabsTrigger value="t1">概览</TabsTrigger>
                  <TabsTrigger value="t2">明细</TabsTrigger>
                  <TabsTrigger value="t3">设置</TabsTrigger>
                </TabsList>
                <TabsContent value="t1">
                  <div className="text-[0.82rem] text-[var(--color-text-secondary)]">概览内容</div>
                </TabsContent>
                <TabsContent value="t2">
                  <div className="text-[0.82rem] text-[var(--color-text-secondary)]">明细内容</div>
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>对话框</CardTitle></CardHeader>
            <CardContent>
              <Dialog>
                <DialogTrigger asChild>
                  <Button variant="secondary" size="sm">打开对话框</Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>确认操作</DialogTitle>
                    <DialogDescription>这是一个对标 blue 本的对话框。</DialogDescription>
                  </DialogHeader>
                  <DialogFooter>
                    <Button variant="ghost" size="sm">取消</Button>
                    <Button size="sm">确定</Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </CardContent>
          </Card>

          <Card className="col-span-3">
            <CardHeader><CardTitle>状态页</CardTitle></CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-3">
                <LoadingState label="加载中…" />
                <EmptyState title="暂无数据" description="这里还没有内容，去别处看看。" />
                <EmptyState title="带动作" description="点下面按钮开始。" action={{ label: "开始", onClick: () => {} }} />
              </div>
            </CardContent>
          </Card>
        </div>
      </PageContainer>
    </AppShell>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><Demo /></React.StrictMode>
);
