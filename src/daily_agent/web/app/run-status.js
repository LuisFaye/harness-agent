export function runCardStatusText(status) {
  if (status === "success") return "已完成";
  if (status === "error" || status === "max_iterations") return "执行失败";
  if (status === "needs_input") return "需要补充信息";
  if (status === "needs_confirmation") return "等待确认";
  if (status === "cancel_requested") return "正在取消";
  if (status === "cancelled") return "已取消";
  return "已结束";
}

export function composerTextFromStatus(status) {
  if (status === "success") return "本轮已完成，可以继续追问";
  if (status === "needs_input") return "需要你补充信息后继续";
  if (status === "needs_confirmation") return "等待你的确认后继续";
  if (status === "error") return "执行失败了，可以修改需求后重试";
  if (status === "cancelled") return "任务已取消，可以发起新的任务";
  return "随时可以发起一个任务";
}
