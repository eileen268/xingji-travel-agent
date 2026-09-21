type ErrorLike = { provider?: unknown; code?: unknown; message?: unknown; status?: unknown };

const providerNames: Record<string, string> = {
  amap: "高德地图",
  qweather: "和风天气",
  flyai: "交通与酒店查询",
  zhipu: "智能规划模型",
  planner: "行程规划",
};

const codeMessages: Record<string, string> = {
  NETWORK_ERROR: "暂时无法连接，请检查网络后重试",
  TIMEOUT: "响应超时，请稍后重试",
  HTTP_ERROR: "服务响应异常，请稍后重试",
  INVALID_INPUT: "查询条件不完整，请检查后重试",
  NO_RESULT: "没有找到匹配结果，请调整条件后重试",
  PLACE_NOT_FOUND: "没有找到对应地点，请输入更准确的名称",
  PROVIDER_ERROR: "服务暂时无法完成查询，请稍后重试",
  EMPTY_RESPONSE: "服务没有返回可用结果，请稍后重试",
  INVALID_RESPONSE: "服务返回的数据格式异常，请稍后重试",
  INVALID_JSON: "智能规划结果格式异常，请重新生成",
  CLI_NOT_INSTALLED: "交通与酒店查询组件尚未正确安装",
  CLI_ERROR: "交通与酒店查询服务运行失败，请稍后重试",
  INVALID_PRIVATE_KEY: "天气服务密钥格式不正确，请检查环境配置",
  UNSUPPORTED_API_STYLE: "智能规划接口配置不受支持，请检查环境配置",
};

function hasChinese(value: string) {
  return /[\u3400-\u9fff]/.test(value);
}

export function userErrorMessage(error: unknown, fallback = "操作失败，请稍后重试") {
  const value = error && typeof error === "object" ? error as ErrorLike : {};
  const message = typeof value.message === "string" ? value.message.trim() : "";
  if (message && hasChinese(message)) return message;
  if (/Missing required server environment variable/i.test(message)) return "服务配置不完整，请联系维护人员检查环境变量";
  const provider = typeof value.provider === "string" ? value.provider : "";
  const code = typeof value.code === "string" ? value.code : "";
  const prefix = providerNames[provider];
  const translated = codeMessages[code];
  if (prefix && translated) return `${prefix}${translated}`;
  if (translated) return translated;
  if (prefix) return `${prefix}暂时不可用，请稍后重试`;
  if (/network|fetch|connection|socket/i.test(message)) return "网络连接失败，请检查网络后重试";
  if (/timeout|timed out|abort/i.test(message)) return "请求响应超时，请稍后重试";
  if (/not found|no result|empty/i.test(message)) return "没有找到可用结果，请调整条件后重试";
  if (/invalid|unsupported|malformed/i.test(message)) return "提交的数据或服务配置无效，请检查后重试";
  return fallback;
}
