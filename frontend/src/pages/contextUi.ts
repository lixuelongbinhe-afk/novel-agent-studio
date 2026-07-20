import type { ContentClassificationValue } from "../api/client";

export const CLASSIFICATION_LABELS: Record<ContentClassificationValue, string> = {
  public: "公开",
  internal: "内部",
  confidential: "机密",
  "personal information": "个人信息",
  "sensitive personal information": "敏感个人信息",
  "unpublished manuscript": "未发布稿件",
  secret: "秘密"
};

export const SECTION_LABELS: Record<string, string> = {
  user_task: "用户任务",
  style: "文风",
  current_scene: "当前场景",
  character_state: "人物状态",
  location_item_relation: "地点 / 物品 / 关系",
  world_rules: "世界规则",
  timeline: "时间线",
  foreshadow: "伏笔",
  neighbor_summaries: "邻章摘要",
  history: "历史片段",
  upstream: "上游输出"
};

export function contextErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "上下文操作失败";
}
