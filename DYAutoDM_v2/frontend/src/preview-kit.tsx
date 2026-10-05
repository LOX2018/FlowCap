/**
 * 页面组件族预览入口（仅开发/验证用）—— 真实渲染 kit.tsx 全部导出，
 * 用于浏览器实测设计令牌是否生效（编译通过 ≠ 渲染正确）。
 */
import { useState } from "react";
import ReactDOM from "react-dom/client";
import "@/styles/tokens.css";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Section, Stat, StatRow, Row, RowText, KeyValue, Tone,
  SkeletonRows, Blank, SegmentedTabs, Toolbar,
} from "@/components/page/kit";

function Demo() {
  const [seg, setSeg] = useState<"a" | "b">("a");
  return (
    <PageContainer>
      <PageHeader
        title="页面组件族验证"
        description="Section / Stat / Row / KeyValue / Tone / Skeleton / Blank / Segmented / Toolbar"
        actions={<Button size="sm">主按钮</Button>}
      />

      <div className="grid gap-4">
        <Section title="统计块 StatRow" description="核心指标高亮主色"
                 actions={<Tone tone="ok">运行中</Tone>}>
          <StatRow cols={4}>
            <Stat label="已发送" value="1,284" unit="条" delta="+12" accent />
            <Stat label="成功率" value="98.6" unit="%" />
            <Stat label="待处理" value="37" />
            <Stat label="失败" value="4" delta="-2" deltaDown />
          </StatRow>
        </Section>

        <Section title="列表行 Row" description="可点击行 + 主副文本">
          <div className="space-y-1">
            <Row active>
              <RowText primary="账号 A · 已登录" secondary="uid 1234… · 凭证就绪" mono />
              <Tone tone="accent">当前</Tone>
            </Row>
            <Row>
              <RowText primary="账号 B · 未登录" secondary="待扫码" />
              <Tone tone="warn">待登录</Tone>
            </Row>
          </div>
        </Section>

        <Section title="键值网格 KeyValue">
          <KeyValue
            cols={3}
            items={[
              { k: "后端版本", v: "0.43.15", mono: true },
              { k: "构建形态", v: "onedir 共享", mono: true },
              { k: "进程状态", v: "正常" },
            ]}
          />
        </Section>

        <Section title="工具栏 + 分段切换 Toolbar / SegmentedTabs"
                 actions={<Input placeholder="搜索…" className="max-w-[200px]" />}>
          <Toolbar>
            <SegmentedTabs
              value={seg}
              onChange={setSeg}
              items={[{ value: "a", label: "全部" }, { value: "b", label: "仅未读" }]}
            />
            <Button variant="secondary" size="sm">次要动作</Button>
            <Button variant="ghost" size="sm">幽灵按钮</Button>
          </Toolbar>
        </Section>

        <div className="grid grid-cols-2 gap-4">
          <Section title="骨架 SkeletonRows"><SkeletonRows rows={3} /></Section>
          <Section title="空态 Blank"><Blank>暂无内容</Blank></Section>
        </div>
      </div>
    </PageContainer>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<Demo />);
