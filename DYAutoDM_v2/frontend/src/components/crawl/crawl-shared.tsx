
/** 数字缩写（爬取页专用：1.2w）。注意与 @/lib/utils 的 fmtNum（万/亿）语义不同，勿混用。 */
export const fmtNumShort = (n: unknown) => {
  const v = Number(n) || 0;
  return v >= 10000 ? (v / 10000).toFixed(1) + "w" : String(v);
};
export const fmtTs = (ts: unknown) => {
  const v = Number(ts);
  if (!v) return "";
  try {
    return new Date(v * 1000).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return "";
  }
};

/** 排序/发布时间/时长 三个筛选器的选项（业务值保持不变）。 */
export const ORDER_OPTS = [
  { v: "0", label: "综合排序" },
  { v: "1", label: "最多点赞" },
  { v: "2", label: "最新发布" },
];
export const DUR_OPTS = [
  { v: "", label: "不限" },
  { v: "0-1", label: "1分钟内" },
  { v: "1-5", label: "1-5分钟" },
  { v: "5-10000", label: "5分钟以上" },
];

export const PT_OPTS = [
  { v: "0", label: "不限" },
  { v: "1", label: "一天内" },
  { v: "7", label: "一周内" },
  { v: "180", label: "半年内" },
];
