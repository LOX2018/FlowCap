import { useState } from "react";
import { PageProps } from "../../api/client";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { SegmentedTabs } from "@/components/page/kit";
import { useQueryClient } from "@tanstack/react-query";
import { BookOpen, MessageSquare, Database } from "lucide-react";
import { ProKb } from "./pro-kb";
import { ReplyKb } from "./reply-kb";
import KbImportSection from "./KbImportSection";

type SubTab = "pro" | "reply" | "import";

export default function KbPage({ push, api }: PageProps) {
  const [sub, setSub] = useState<SubTab>("pro");
  const qc = useQueryClient();

  return (
    <PageContainer maxWidth="1080px">
      <PageHeader
        title="知识库管理"
        description="专业知识库（向量参考，不直接回复） · 对话回复库（案例命中，零 token 直回）"
      />

      <SegmentedTabs
        className="mb-4"
        value={sub}
        onChange={setSub}
        items={[
          { value: "pro", label: "专业知识库", icon: <BookOpen className="h-3.5 w-3.5" /> },
          { value: "reply", label: "对话回复库", icon: <MessageSquare className="h-3.5 w-3.5" /> },
          { value: "import", label: "来源导入", icon: <Database className="h-3.5 w-3.5" /> },
        ]}
      />

      {sub === "pro"
        ? <ProKb push={push} api={api} qc={qc} />
        : sub === "reply"
          ? <ReplyKb push={push} api={api} qc={qc} />
          : <KbImportSection push={push} />}
    </PageContainer>
  );
}

// ---------------------------------------------------------------------------
// ① 专业知识库（向量参考库）
// ---------------------------------------------------------------------------

